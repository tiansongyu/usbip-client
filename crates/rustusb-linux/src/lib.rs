//! Read-only Linux sysfs discovery. Metadata is a snapshot, never proof of USB redirection.
//!
//! This module never opens `/dev/bus/usb`, unbinds drivers, mounts storage, or writes sysfs.
//! Async callers should run [`DeviceDiscovery::scan`] in a blocking task.

#[cfg(target_os = "linux")]
pub mod hotplug;
#[cfg(target_os = "linux")]
pub mod observation;

use rustusb_core::{BusId, Device, DeviceDiscovery, Endpoint, Interface, Result};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{self, Read},
    path::{Path, PathBuf},
};

const MAX_ATTRIBUTE: usize = 4096;
const MAX_DESCRIPTORS: usize = 256 * 1024;
const MAX_ENTRIES: usize = 16384;
const MAX_WALK_DEPTH: usize = 16;
const MAX_WALK_DIRECTORIES: usize = 1024;

/// A bounded, read-only scanner for a Linux USB sysfs device directory.
#[derive(Debug, Clone)]
pub struct SysfsDiscovery {
    root: PathBuf,
}

impl Default for SysfsDiscovery {
    fn default() -> Self {
        Self::new("/sys/bus/usb/devices")
    }
}

impl SysfsDiscovery {
    /// Use a sysfs device root, or an isolated fixture directory for tests.
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    /// Borrow the configured read-only device directory.
    pub fn root(&self) -> &Path {
        &self.root
    }

    fn device(&self, bus_id: BusId, entries: &[String]) -> Result<Option<Device>> {
        let path = self.root.join(bus_id.as_str());
        let canonical = match fs::canonicalize(&path) {
            Ok(path) => path,
            Err(e) if e.kind() == io::ErrorKind::NotFound => return Ok(None),
            Err(e) => return Err(context(&path, e).into()),
        };
        let mut blockers = vec![];
        let vendor_id = number(&path, "idVendor", 16, 65535, &mut blockers) as u16;
        let product_id = number(&path, "idProduct", 16, 65535, &mut blockers) as u16;
        let address = number(&path, "devnum", 10, 127, &mut blockers);
        let bus = number(&path, "busnum", 10, 65535, &mut blockers);
        if address == 0 || bus == 0 {
            blockers.push("invalid_bus_or_device_address".into());
        }
        if bus_id
            .as_str()
            .split_once('-')
            .and_then(|(value, _)| value.parse::<u32>().ok())
            != Some(bus)
        {
            blockers.push("bus_number_does_not_match_address".into());
        }
        let class = number(&path, "bDeviceClass", 16, 255, &mut blockers) as u8;
        let subclass = number(&path, "bDeviceSubClass", 16, 255, &mut blockers) as u8;
        let protocol = number(&path, "bDeviceProtocol", 16, 255, &mut blockers) as u8;
        let count = number(&path, "bNumInterfaces", 10, 255, &mut blockers);
        let usb_version = required_text(&path, "version", &mut blockers);
        if !valid_decimal(&usb_version) {
            blockers.push("invalid_usb_version".into());
        }
        let speed = required_text(&path, "speed", &mut blockers);
        if !matches!(
            speed.as_str(),
            "1.5" | "12" | "480" | "5000" | "10000" | "20000"
        ) {
            blockers.push("unknown_usb_speed".into());
        }
        let manufacturer = optional_text(&path, "manufacturer", &mut blockers);
        let product = optional_text(&path, "product", &mut blockers);
        let serial = optional_text(&path, "serial", &mut blockers);
        let driver = driver(&path, &mut blockers);
        let exported = driver.as_deref() == Some("usbip-host");
        let occupied = match attribute(&path.join("usbip_status")) {
            Ok(status) => match status.as_str() {
                "1" => Some(false),
                "2" => {
                    blockers.push("usbip_device_in_use".into());
                    Some(true)
                }
                _ => {
                    blockers.push("usbip_status_error_or_unknown".into());
                    None
                }
            },
            Err(e) if e.kind() == io::ErrorKind::NotFound && !exported => None,
            Err(_) => {
                blockers.push("usbip_status_unreadable".into());
                None
            }
        };
        let interfaces = self.interfaces(&bus_id, entries, &mut blockers)?;
        if interfaces.is_empty() || interfaces.len() != count as usize {
            blockers.push("interface_metadata_incomplete".into());
        }
        let topology = match physical_topology(&canonical, &bus_id) {
            Some(topology) => topology,
            None => {
                blockers.push("physical_topology_unknown".into());
                // Distinguishes incomplete records without claiming a stable identity.
                format!("unknown/{}", bus_id.as_str())
            }
        };
        if topology.contains("vhci_hcd") || topology.contains("dummy_hcd") {
            blockers.push("virtual_usb_controller".into());
        }
        class_safety(class, true, &mut blockers);
        for interface in &interfaces {
            class_safety(interface.class, false, &mut blockers);
            // usb-storage quirks may match a vendor-specific class. A known
            // storage driver remains a blocker even when class 08 is absent.
            if interface.driver.as_deref().is_some_and(|name| {
                matches!(name, "usb-storage" | "uas") || name.starts_with("ums-")
            }) {
                blockers.push(
                    "storage_driver_requires_mount_open_handle_lun_and_encryption_audit".into(),
                );
            }
        }
        match block_descendant(&canonical) {
            Ok(true) => blockers.push("storage_block_device_descendant_present".into()),
            Ok(false) => {}
            Err(_) => blockers.push("storage_descendant_safety_unproven".into()),
        }
        // Inspect every cached configuration and alternate setting, including inactive
        // storage interfaces that are absent from the current sysfs interface tree.
        inspect_descriptors(&path, &mut blockers);
        if !matches!(attribute(&path.join("authorized")).as_deref(), Ok("1")) {
            blockers.push("device_not_authorized_or_unknown".into());
        }
        // A disconnect/reenumeration after initial enumeration must never turn a
        // mixed metadata snapshot into an eligible device.
        if !path.exists() {
            return Ok(None);
        }
        if fs::canonicalize(&path).ok().as_ref() != Some(&canonical)
            || attribute(&path.join("devnum")).ok().as_deref() != Some(&address.to_string())
            || attribute(&path.join("busnum")).ok().as_deref() != Some(&bus.to_string())
        {
            blockers.push("device_changed_during_scan".into());
        }
        blockers.sort();
        blockers.dedup();
        let id = fingerprint(vendor_id, product_id, serial.as_deref(), &topology);
        Ok(Some(Device {
            id,
            bus_id,
            vendor_id,
            product_id,
            manufacturer,
            product,
            serial,
            device_address: address,
            usb_version,
            speed,
            class,
            subclass,
            protocol,
            topology,
            driver,
            interfaces,
            exported,
            occupied,
            export_blockers: blockers,
        }))
    }

    fn interfaces(
        &self,
        bus_id: &BusId,
        entries: &[String],
        blockers: &mut Vec<String>,
    ) -> Result<Vec<Interface>> {
        let prefix = format!("{}:", bus_id.as_str());
        let mut interfaces = vec![];
        for name in entries.iter().filter(|name| name.starts_with(&prefix)) {
            let path = self.root.join(name);
            let interface_number = number(&path, "bInterfaceNumber", 16, 255, blockers) as u8;
            let alternate_setting = number(&path, "bAlternateSetting", 10, 255, blockers) as u8;
            let class = number(&path, "bInterfaceClass", 16, 255, blockers) as u8;
            let subclass = number(&path, "bInterfaceSubClass", 16, 255, blockers) as u8;
            let protocol = number(&path, "bInterfaceProtocol", 16, 255, blockers) as u8;
            let endpoint_count = number(&path, "bNumEndpoints", 16, 30, blockers);
            let driver = driver(&path, blockers);
            let mut endpoints = vec![];
            let children = match names(&path) {
                Ok(children) => children,
                Err(_) => {
                    blockers.push("interface_endpoint_directory_unreadable".into());
                    vec![]
                }
            };
            for name in children.iter().filter(|name| name.starts_with("ep_")) {
                let ep_path = path.join(name);
                let address = number(&ep_path, "bEndpointAddress", 16, 255, blockers) as u8;
                let attributes = number(&ep_path, "bmAttributes", 16, 255, blockers) as u8;
                let max_packet_size =
                    number(&ep_path, "wMaxPacketSize", 16, 65535, blockers) as u16;
                let interval = number(&ep_path, "bInterval", 16, 255, blockers) as u8;
                if address & 0x0f == 0 || address & 0x70 != 0 || max_packet_size == 0 {
                    blockers.push("endpoint_metadata_invalid".into());
                }
                endpoints.push(Endpoint {
                    address,
                    attributes,
                    max_packet_size,
                    interval,
                });
            }
            endpoints.sort_by_key(|ep| ep.address);
            if endpoints
                .windows(2)
                .any(|pair| pair[0].address == pair[1].address)
            {
                blockers.push("duplicate_endpoint_address".into());
            }
            if endpoints.len() != endpoint_count as usize {
                blockers.push("endpoint_metadata_incomplete".into());
            }
            interfaces.push(Interface {
                number: interface_number,
                alternate_setting,
                class,
                subclass,
                protocol,
                driver,
                endpoints,
            });
        }
        interfaces.sort_by_key(|interface| interface.number);
        if interfaces
            .windows(2)
            .any(|pair| pair[0].number == pair[1].number)
        {
            blockers.push("duplicate_interface_number".into());
        }
        Ok(interfaces)
    }
}

impl DeviceDiscovery for SysfsDiscovery {
    fn scan(&self) -> Result<Vec<Device>> {
        let entries = names(&self.root)?;
        let mut devices = vec![];
        for name in &entries {
            // The grammar excludes usbN root hubs and interface entries.
            if let Ok(bus_id) = BusId::new(name.clone())
                && let Some(device) = self.device(bus_id, &entries)?
            {
                devices.push(device);
            }
        }
        Ok(devices)
    }
}

fn context(path: &Path, error: io::Error) -> io::Error {
    io::Error::new(error.kind(), format!("{}: {error}", path.display()))
}

fn bytes(path: &Path, max: usize) -> io::Result<Vec<u8>> {
    let mut data = vec![];
    File::open(path)
        .map_err(|e| context(path, e))?
        .take((max + 1) as u64)
        .read_to_end(&mut data)
        .map_err(|e| context(path, e))?;
    if data.len() > max {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "sysfs attribute exceeds limit",
        ));
    }
    Ok(data)
}

fn attribute(path: &Path) -> io::Result<String> {
    let value = String::from_utf8(bytes(path, MAX_ATTRIBUTE)?)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "non-UTF8 sysfs attribute"))?;
    let value = value.trim();
    if value.is_empty() || value.chars().any(char::is_control) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "empty or control characters in sysfs attribute",
        ));
    }
    Ok(value.to_string())
}

fn names(path: &Path) -> io::Result<Vec<String>> {
    let mut result = vec![];
    for entry in fs::read_dir(path).map_err(|e| context(path, e))? {
        let entry = entry.map_err(|e| context(path, e))?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "non-UTF8 sysfs name"))?;
        result.push(name);
        if result.len() > MAX_ENTRIES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "sysfs directory exceeds entry limit",
            ));
        }
    }
    result.sort();
    Ok(result)
}

fn required_text(path: &Path, name: &str, blockers: &mut Vec<String>) -> String {
    match attribute(&path.join(name)) {
        Ok(value) => value,
        Err(_) => {
            blockers.push(format!("metadata_unreadable:{name}"));
            String::new()
        }
    }
}

fn optional_text(path: &Path, name: &str, blockers: &mut Vec<String>) -> Option<String> {
    match attribute(&path.join(name)) {
        Ok(value) => Some(value),
        Err(e) if e.kind() == io::ErrorKind::NotFound => None,
        Err(_) => {
            blockers.push(format!("metadata_unreadable:{name}"));
            None
        }
    }
}

fn number(path: &Path, name: &str, radix: u32, max: u32, blockers: &mut Vec<String>) -> u32 {
    let value = required_text(path, name, blockers);
    match u32::from_str_radix(&value, radix) {
        Ok(number) if number <= max => number,
        _ => {
            blockers.push(format!("metadata_invalid:{name}"));
            0
        }
    }
}

fn valid_decimal(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|b| b.is_ascii_digit() || b == b'.')
        && value.bytes().filter(|b| *b == b'.').count() == 1
        && value.parse::<f32>().is_ok_and(|n| n > 0.0)
}

fn driver(path: &Path, blockers: &mut Vec<String>) -> Option<String> {
    match fs::read_link(path.join("driver")) {
        Ok(target) => target
            .file_name()
            .and_then(|name| name.to_str())
            .map(str::to_owned),
        Err(e) if e.kind() == io::ErrorKind::NotFound => None,
        Err(_) => {
            blockers.push("driver_metadata_unreadable".into());
            None
        }
    }
}

fn class_safety(class: u8, device: bool, blockers: &mut Vec<String>) {
    match class {
        0x08 => blockers.push("storage_requires_mount_open_handle_lun_and_encryption_audit".into()),
        0x09 => blockers.push("usb_hub_export_forbidden".into()),
        // Class 0 / EF are common composite device-level markers. Interface 0
        // and reserved class values cannot be assumed safe device functions.
        0x00 | 0xef if device => {}
        0x01..=0x07 | 0x0a..=0x11 | 0xdc | 0xe0 | 0xef | 0xfe | 0xff => {}
        _ => blockers.push("unknown_usb_interface_or_device_class".into()),
    }
}

// Sysfs driver quirks can expose block storage without class 08. Walk only
// actual child directories within this device; never follow subsystem/driver
// symlinks back into the rest of sysfs. All uncertainty denies export.
fn block_descendant(path: &Path) -> io::Result<bool> {
    let mut pending = vec![(path.to_path_buf(), 0)];
    let mut directories = 0;
    let mut entries = 0;
    while let Some((directory, depth)) = pending.pop() {
        directories += 1;
        if directories > MAX_WALK_DIRECTORIES {
            return Err(io::Error::other("USB descendant directory budget exceeded"));
        }
        for child in fs::read_dir(&directory)? {
            let child = child?;
            entries += 1;
            if entries > MAX_ENTRIES {
                return Err(io::Error::other("USB descendant entry budget exceeded"));
            }
            let kind = child.file_type()?;
            if child.file_name() == "block" && (kind.is_dir() || kind.is_symlink()) {
                return Ok(true);
            }
            if kind.is_dir() {
                if depth >= MAX_WALK_DEPTH {
                    return Err(io::Error::other("USB descendant depth budget exceeded"));
                }
                pending.push((child.path(), depth + 1));
            }
        }
    }
    Ok(false)
}

fn inspect_descriptors(path: &Path, blockers: &mut Vec<String>) {
    let data = match bytes(&path.join("descriptors"), MAX_DESCRIPTORS) {
        Ok(data) => data,
        Err(_) => {
            blockers.push("cached_descriptors_unreadable".into());
            return;
        }
    };
    let mut offset = 0;
    let mut configs = 0;
    let mut interface_seen = false;
    while offset < data.len() {
        let rest = &data[offset..];
        if rest.len() < 2 || rest[0] < 2 || usize::from(rest[0]) > rest.len() {
            blockers.push("cached_descriptors_malformed".into());
            return;
        }
        let len = usize::from(rest[0]);
        match rest[1] {
            1 if offset == 0 && len == 18 => {}
            1 => blockers.push("cached_device_descriptor_invalid".into()),
            2 if len >= 9 => {
                configs += 1;
                let total = usize::from(u16::from_le_bytes([rest[2], rest[3]]));
                if total < 9 || total > rest.len() {
                    blockers.push("cached_configuration_descriptor_truncated".into());
                }
            }
            2 => blockers.push("cached_configuration_descriptor_invalid".into()),
            4 if len >= 9 => {
                interface_seen = true;
                class_safety(rest[5], false, blockers);
            }
            4 => blockers.push("cached_interface_descriptor_invalid".into()),
            _ => {}
        }
        offset += len;
    }
    if data.first() != Some(&18)
        || data.get(1) != Some(&1)
        || configs == 0
        || !interface_seen
        || data
            .get(17)
            .is_none_or(|expected| usize::from(*expected) != configs)
    {
        blockers.push("cached_descriptors_incomplete".into());
    }
}

fn physical_topology(canonical: &Path, bus_id: &BusId) -> Option<String> {
    let components: Vec<_> = canonical.iter().filter_map(|part| part.to_str()).collect();
    let root = components.iter().position(|part| {
        part.strip_prefix("usb")
            .is_some_and(|number| !number.is_empty() && number.bytes().all(|b| b.is_ascii_digit()))
    })?;
    if root == 0 {
        return None;
    }
    let start = components[..root]
        .iter()
        .position(|part| *part == "devices")
        .unwrap_or(0);
    let controller = components[start..root].join("/");
    let (_, ports) = bus_id.as_str().split_once('-')?;
    Some(format!("{controller}/ports/{ports}"))
}

fn fingerprint(vendor: u16, product: u16, serial: Option<&str>, topology: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(b"rustusb-device-id-v1");
    hash.update(vendor.to_be_bytes());
    hash.update(product.to_be_bytes());
    for value in [serial.unwrap_or(""), topology] {
        hash.update((value.len() as u64).to_be_bytes());
        hash.update(value.as_bytes());
    }
    format!("usb-{:x}", hash.finalize())
}

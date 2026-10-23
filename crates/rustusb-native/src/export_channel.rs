//! Strong Native export registration and a retained original-attribute stub capability.
//! All synchronous operations require an owning blocking task and caller mutation serialization.
use rustusb_core::{BusId, DeviceKey, Error, Result};
use rustusb_protocol::{RemoteDevice, RemoteInterface};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    net::Shutdown,
    os::{
        fd::AsRawFd,
        unix::{
            fs::{FileTypeExt, MetadataExt},
            net::UnixStream,
        },
    },
    path::{Path, PathBuf},
    sync::atomic::{AtomicBool, Ordering},
    thread,
    time::{Duration, Instant},
};

/// Hardware identity that remains fixed while Native changes active configuration and interfaces.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HardwareGeneration {
    /// Current validated Linux bus ID, never a stable authorization key by itself.
    pub bus_id: BusId,
    /// Canonical original sysfs path.
    pub sysfs_path: PathBuf,
    /// Original sysfs filesystem device.
    pub filesystem_device: u64,
    /// Original sysfs directory inode.
    pub inode: u64,
    /// USB bus number, full 16-bit devid field.
    pub bus_number: u16,
    /// Actual USB device address.
    pub device_number: u32,
    /// Vendor ID.
    pub vendor_id: u16,
    /// Product ID.
    pub product_id: u16,
    /// Device release BCD.
    pub device_version: u16,
    /// Linux USB speed enum.
    pub speed: u32,
    /// Device class.
    pub class: u8,
    /// Device subclass.
    pub subclass: u8,
    /// Device protocol.
    pub protocol: u8,
    /// Actual number of configurations.
    pub num_configurations: u8,
}
/// Actual interface state in one active configuration, including its original driver.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExportInterface {
    /// Actual interface number.
    pub number: u8,
    /// Actual alternate setting.
    pub alternate: u8,
    /// Interface class.
    pub class: u8,
    /// Interface subclass.
    pub subclass: u8,
    /// Interface protocol.
    pub protocol: u8,
    /// Driver basename; None is an explicit absent binding, not a read error.
    pub driver: Option<String>,
}
/// Current configuration state; legal unconfigured sysfs attributes are exactly empty.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExportProfile {
    /// Actual configuration; None requires both sysfs active fields empty and no interfaces.
    pub configuration: Option<u8>,
    /// Complete current interfaces ordered by number.
    pub interfaces: Vec<ExportInterface>,
}
/// Original registration snapshot, captured before binding to usbip-host.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RegistrationSnapshot {
    /// Stable approved Native key.
    pub device_key: DeviceKey,
    /// Original immutable hardware generation.
    pub generation: HardwareGeneration,
    /// Original complete configuration/interfaces/driver baseline.
    pub original_profile: ExportProfile,
    /// Original device-level driver.
    pub original_device_driver: String,
    /// Published registration descriptor snapshot, NOT the current post-export actconfig.
    pub published_device: RemoteDevice,
}
/// Strong receipt for the original, preopened usbip_sockfd attribute.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExportReceipt {
    /// Original hardware directory identity.
    pub generation: HardwareGeneration,
    /// Filesystem device of the original attribute.
    pub attribute_device: u64,
    /// Inode of the original attribute.
    pub attribute_inode: u64,
    /// Actual snapshot at binding, normally None/[] after Native unconfiguration.
    pub bound_profile: ExportProfile,
}
/// Actual stub socket handoff result. USED does not imply the remote VHCI has enumerated.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StubReceipt {
    /// Strong original export identity.
    pub export: ExportReceipt,
    /// Retained kernel-end socket inode.
    pub kernel_socket_inode: u64,
    /// Independently read actual stub status after the one write.
    pub usbip_status: u32,
    /// Actual current profile immediately after handoff; may still be unconfigured.
    pub current_profile: ExportProfile,
}
fn require(ok: bool, message: &str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(Error::State(message.into()))
    }
}
fn bounded(path: &Path) -> Result<String> {
    let mut text = String::new();
    File::open(path)?.take(65537).read_to_string(&mut text)?;
    require(text.len() <= 65536, "Native export attribute exceeds bound")?;
    Ok(text)
}
fn number(path: &Path, radix: u32) -> Result<u32> {
    let raw = bounded(path)?;
    let text = raw.trim();
    require(
        !text.is_empty() && text.chars().all(|c| c.is_digit(radix)),
        "invalid Native export numeric attribute",
    )?;
    u32::from_str_radix(text, radix)
        .map_err(|_| Error::State("Native export numeric overflow".into()))
}
fn byte(path: &Path, name: &str, radix: u32) -> Result<u8> {
    u8::try_from(number(&path.join(name), radix)?)
        .map_err(|_| Error::State("invalid Native descriptor byte".into()))
}
fn word(path: &Path, name: &str, radix: u32) -> Result<u16> {
    u16::try_from(number(&path.join(name), radix)?)
        .map_err(|_| Error::State("invalid Native descriptor word".into()))
}
fn driver(path: &Path) -> Result<Option<String>> {
    match fs::read_link(path.join("driver")) {
        Ok(link) => {
            let name = link
                .file_name()
                .and_then(|n| n.to_str())
                .ok_or_else(|| Error::State("invalid Native driver name".into()))?;
            require(
                !name.is_empty()
                    && name.len() <= 64
                    && name
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b)),
                "invalid Native driver basename",
            )?;
            Ok(Some(name.to_owned()))
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.into()),
    }
}
fn hardware(bus: &BusId) -> Result<HardwareGeneration> {
    let path = Path::new("/sys/bus/usb/devices").join(bus.as_str());
    let real = fs::canonicalize(&path)?;
    require(
        real.starts_with("/sys/devices")
            && !real
                .components()
                .any(|c| c.as_os_str().to_string_lossy().starts_with("vhci_hcd.")),
        "cannot export an imported or non-USB sysfs device",
    )?;
    let before = fs::metadata(&real)?;
    require(
        before.is_dir() && before.uid() == 0 && before.mode() & 0o022 == 0,
        "untrusted Native USB directory",
    )?;
    let speed = match bounded(&real.join("speed"))?.trim() {
        "1.5" => 1,
        "12" => 2,
        "480" => 3,
        "5000" => 5,
        _ => {
            return Err(Error::Unsupported(
                "Native secure export speed is not supported by the audited kernel contract".into(),
            ));
        }
    };
    let value = HardwareGeneration {
        bus_id: bus.clone(),
        sysfs_path: real.clone(),
        filesystem_device: before.dev(),
        inode: before.ino(),
        bus_number: word(&real, "busnum", 10)?,
        device_number: number(&real.join("devnum"), 10)?,
        vendor_id: word(&real, "idVendor", 16)?,
        product_id: word(&real, "idProduct", 16)?,
        device_version: word(&real, "bcdDevice", 16)?,
        speed,
        class: byte(&real, "bDeviceClass", 16)?,
        subclass: byte(&real, "bDeviceSubClass", 16)?,
        protocol: byte(&real, "bDeviceProtocol", 16)?,
        num_configurations: byte(&real, "bNumConfigurations", 10)?,
    };
    let after = fs::metadata(&path)?;
    require(
        before.dev() == after.dev()
            && before.ino() == after.ino()
            && value.bus_number != 0
            && (1..=127).contains(&value.device_number),
        "Native hardware generation changed or addressing is invalid",
    )?;
    Ok(value)
}
fn profile(bus: &BusId) -> Result<ExportProfile> {
    let base = Path::new("/sys/bus/usb/devices").join(bus.as_str());
    let config = bounded(&base.join("bConfigurationValue"))?;
    let count = bounded(&base.join("bNumInterfaces"))?;
    let mut interfaces = Vec::new();
    for (checked, entry) in fs::read_dir("/sys/bus/usb/devices")?.enumerate() {
        require(
            checked < 4096,
            "Native USB interface directory exceeds bound",
        )?;
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !name.starts_with(&format!("{}:", bus.as_str())) {
            continue;
        }
        require(
            interfaces.len() < rustusb_protocol::MAX_INTERFACES,
            "Native interface set exceeds protocol limit",
        )?;
        let path = entry.path();
        interfaces.push(ExportInterface {
            number: byte(&path, "bInterfaceNumber", 16)?,
            alternate: byte(&path, "bAlternateSetting", 10)?,
            class: byte(&path, "bInterfaceClass", 16)?,
            subclass: byte(&path, "bInterfaceSubClass", 16)?,
            protocol: byte(&path, "bInterfaceProtocol", 16)?,
            driver: driver(&path)?,
        });
    }
    interfaces.sort_by_key(|i| i.number);
    let configuration = if config.is_empty() && count.is_empty() {
        require(
            interfaces.is_empty(),
            "unconfigured Native device still has interfaces",
        )?;
        None
    } else {
        let configuration = byte(&base, "bConfigurationValue", 10)?;
        let expected = number(&base.join("bNumInterfaces"), 10)?;
        require(
            configuration != 0 && expected as usize == interfaces.len() && expected != 0,
            "incomplete Native active configuration",
        )?;
        Some(configuration)
    };
    require(
        interfaces.windows(2).all(|w| w[0].number != w[1].number)
            && bounded(&base.join("bConfigurationValue"))? == config
            && bounded(&base.join("bNumInterfaces"))? == count,
        "Native profile changed during observation",
    )?;
    Ok(ExportProfile {
        configuration,
        interfaces,
    })
}
impl RegistrationSnapshot {
    /// Capture actual configured registration descriptors before any Native binding mutation.
    pub fn capture(device_id: &str, bus: BusId) -> Result<Self> {
        let device_key = DeviceKey::native(device_id)?;
        let generation = hardware(&bus)?;
        let original_profile = profile(&bus)?;
        let original_device_driver = driver(&generation.sysfs_path)?.ok_or_else(|| {
            Error::State("Native registration needs an original device driver".into())
        })?;
        require(
            original_device_driver == "usb",
            "Native registration requires the ordinary USB device driver",
        )?;
        let configuration = original_profile.configuration.ok_or_else(|| {
            Error::Unsupported(
                "secure Native publication requires a configured registration snapshot".into(),
            )
        })?;
        require(
            original_profile.interfaces.iter().all(|i| {
                i.class != 8 && !matches!(i.driver.as_deref(), Some("usbfs" | "usbip-host"))
            }),
            "Native registration has unsafe or externally held interfaces",
        )?;
        let path = generation
            .sysfs_path
            .to_str()
            .ok_or_else(|| Error::State("non-UTF8 Native sysfs path".into()))?
            .to_owned();
        let published_device = RemoteDevice {
            bus_id: bus.clone(),
            path,
            bus_number: u32::from(generation.bus_number),
            device_number: generation.device_number,
            speed: generation.speed,
            vendor_id: generation.vendor_id,
            product_id: generation.product_id,
            device_version: generation.device_version,
            class: generation.class,
            subclass: generation.subclass,
            protocol: generation.protocol,
            configuration,
            num_configurations: generation.num_configurations,
            interfaces: original_profile
                .interfaces
                .iter()
                .map(|i| RemoteInterface {
                    class: i.class,
                    subclass: i.subclass,
                    protocol: i.protocol,
                })
                .collect(),
        };
        crate::channel::validate_remote(&published_device)?;
        require(
            hardware(&bus)? == generation
                && profile(&bus)? == original_profile
                && driver(&generation.sysfs_path)?.as_deref()
                    == Some(original_device_driver.as_str()),
            "Native registration changed during capture",
        )?;
        Ok(Self {
            device_key,
            generation,
            original_profile,
            original_device_driver,
            published_device,
        })
    }
    /// Whether the original hardware generation is gone. A new device at the same BusID is not adopted.
    pub fn gone(&self) -> Result<bool> {
        let path = Path::new("/sys/bus/usb/devices").join(self.generation.bus_id.as_str());
        match fs::metadata(&path) {
            Ok(metadata) => Ok(metadata.dev() != self.generation.filesystem_device
                || metadata.ino() != self.generation.inode),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
            Err(error) => Err(error.into()),
        }
    }
    /// Independently verify every original driver/configuration/interface/alternate before retiring.
    pub fn restored(&self) -> Result<bool> {
        if self.gone()? {
            return Ok(false);
        }
        Ok(hardware(&self.generation.bus_id)? == self.generation
            && profile(&self.generation.bus_id)? == self.original_profile
            && driver(&self.generation.sysfs_path)?.as_deref()
                == Some(self.original_device_driver.as_str())
            && hardware(&self.generation.bus_id)? == self.generation)
    }
    /// Await complete original driver/configuration restoration; read errors remain failures.
    pub fn wait_restored(&self) -> Result<()> {
        self.wait_restored_proven().map(|_| ())
    }
    /// Return the actual restoration or original-generation-gone criterion.
    pub fn wait_restored_proven(&self) -> Result<rustusb_core::application::BrokerCleanupProof> {
        use rustusb_core::application::BrokerCleanupProof;
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if self.gone()? {
                return Ok(BrokerCleanupProof::OriginalGenerationGone);
            }
            if self.restored()? {
                return Ok(BrokerCleanupProof::NativeExportRestored);
            }
            require(
                Instant::now() < deadline,
                "original Native baseline was not restored before deadline",
            )?;
            thread::sleep(Duration::from_millis(50));
        }
    }
    /// Check an actual exported-phase profile against the registration, keeping None/[] explicit.
    pub fn validate_exported_profile(&self, actual: &ExportProfile) -> Result<()> {
        if actual.configuration.is_none() {
            return require(
                actual.interfaces.is_empty(),
                "unconfigured Native profile contains interfaces",
            );
        }
        let mut expected = self.original_profile.clone();
        for interface in &mut expected.interfaces {
            interface.driver = None;
        }
        require(
            *actual == expected,
            "Native current configuration/alternate differs from registered baseline",
        )
    }
    /// Validate a reloaded record without opening any caller-supplied path.
    pub fn validate(&self) -> Result<()> {
        require(
            self.device_key.backend() == rustusb_core::DeviceBackend::Native,
            "persisted registration is not Native",
        )?;
        let path = &self.generation.sysfs_path;
        require(
            path.starts_with("/sys/devices")
                && !path
                    .components()
                    .any(|p| matches!(p, std::path::Component::ParentDir))
                && self.generation.filesystem_device != 0
                && self.generation.inode != 0
                && self.original_device_driver == "usb"
                && self.generation.class != 8
                && self.original_profile.configuration == Some(self.published_device.configuration)
                && self.original_profile.interfaces.len() == self.published_device.interfaces.len(),
            "invalid persisted Native registration",
        )?;
        crate::channel::validate_remote(&self.published_device)?;
        require(
            self.published_device.bus_id == self.generation.bus_id
                && self.published_device.path == path.to_string_lossy()
                && self.published_device.bus_number == u32::from(self.generation.bus_number)
                && self.published_device.device_number == self.generation.device_number
                && self.published_device.vendor_id == self.generation.vendor_id
                && self.published_device.product_id == self.generation.product_id
                && self.published_device.speed == self.generation.speed
                && self.published_device.device_version == self.generation.device_version
                && self.published_device.class == self.generation.class
                && self.published_device.subclass == self.generation.subclass
                && self.published_device.protocol == self.generation.protocol
                && self.published_device.num_configurations == self.generation.num_configurations
                && !path
                    .components()
                    .any(|p| p.as_os_str().to_string_lossy().starts_with("vhci_hcd.")),
            "inconsistent Native registration descriptors",
        )?;
        let mut numbers = BTreeSet::new();
        require(
            self.original_profile
                .interfaces
                .iter()
                .zip(&self.published_device.interfaces)
                .all(|(i, published)| {
                    numbers.insert(i.number)
                        && i.class != 8
                        && i.class == published.class
                        && i.subclass == published.subclass
                        && i.protocol == published.protocol
                        && i.driver.as_ref().is_none_or(|d| {
                            !d.is_empty()
                                && d.len() <= 64
                                && !matches!(d.as_str(), "usbfs" | "usbip-host")
                                && d.bytes()
                                    .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
                        })
                }),
            "duplicate persisted Native interface",
        )
    }
}
/// A preopened original attribute. It cannot be reconstructed from an untrusted BusID on restart.
pub struct ExportCapability {
    registration: RegistrationSnapshot,
    receipt: ExportReceipt,
    attribute: File,
}
impl ExportCapability {
    /// Capture the actual unconfigured exported phase and preopen its original kernfs attribute.
    pub fn after_bind(registration: RegistrationSnapshot) -> Result<Self> {
        let generation = hardware(&registration.generation.bus_id)?;
        require(
            generation == registration.generation,
            "Native device changed during binding",
        )?;
        let bound_profile = profile(&generation.bus_id)?;
        require(
            bound_profile.configuration.is_none() && bound_profile.interfaces.is_empty(),
            "fresh Native export is not the expected unconfigured phase",
        )?;
        let path = generation.sysfs_path.join("usbip_sockfd");
        let prior = fs::symlink_metadata(&path)?;
        require(
            prior.is_file() && prior.uid() == 0 && prior.mode() & 0o022 == 0,
            "untrusted original stub attribute",
        )?;
        let attribute = OpenOptions::new().write(true).open(&path)?;
        let opened = attribute.metadata()?;
        require(
            prior.dev() == opened.dev() && prior.ino() == opened.ino(),
            "original stub attribute changed while opening",
        )?;
        let receipt = ExportReceipt {
            generation,
            attribute_device: opened.dev(),
            attribute_inode: opened.ino(),
            bound_profile,
        };
        let result = Self {
            registration,
            receipt,
            attribute,
        };
        result.available()?;
        Ok(result)
    }
    /// Borrow the strong original export receipt, including actual bound-phase metadata.
    pub fn receipt(&self) -> &ExportReceipt {
        &self.receipt
    }
    /// Verify original identity, original attribute and an available export without mutating it.
    pub fn available(&self) -> Result<()> {
        self.current(1).map(|_| ())
    }
    fn current(&self, status: u32) -> Result<ExportProfile> {
        require(
            hardware(&self.receipt.generation.bus_id)? == self.receipt.generation,
            "Native export hardware generation changed",
        )?;
        let path = &self.receipt.generation.sysfs_path;
        require(
            driver(path)?.as_deref() == Some("usbip-host")
                && number(&path.join("usbip_status"), 10)? == status,
            "Native export status or driver changed",
        )?;
        let metadata = fs::symlink_metadata(path.join("usbip_sockfd"))?;
        require(
            metadata.is_file()
                && metadata.dev() == self.receipt.attribute_device
                && metadata.ino() == self.receipt.attribute_inode,
            "original Native attribute changed",
        )?;
        let actual = profile(&self.receipt.generation.bus_id)?;
        self.registration.validate_exported_profile(&actual)?;
        require(
            hardware(&self.receipt.generation.bus_id)? == self.receipt.generation,
            "Native export changed during observation",
        )?;
        Ok(actual)
    }
    /// Prepare a distinct channel with an original-attribute FD, leaving the kernel AVAILABLE.
    pub fn prepare_stub(&self) -> Result<(StubChannel, UnixStream)> {
        self.available()?;
        let attribute = self.attribute.try_clone()?;
        let (kernel, relay) = UnixStream::pair()?;
        let metadata = fs::metadata(format!("/proc/self/fd/{}", kernel.as_raw_fd()))?;
        require(
            metadata.file_type().is_socket(),
            "stub capability is not a socket",
        )?;
        Ok((
            StubChannel {
                capability: Self {
                    registration: self.registration.clone(),
                    receipt: self.receipt.clone(),
                    attribute,
                },
                kernel,
                socket_inode: metadata.ino(),
                started: false,
            },
            relay,
        ))
    }
}
/// One helper-owned kernel endpoint, with a strong original export/attribute receipt.
pub struct StubChannel {
    capability: ExportCapability,
    kernel: UnixStream,
    socket_inode: u64,
    started: bool,
}
impl StubChannel {
    /// Persist commit intent through the callback, then issue exactly one original-attribute write.
    /// Returns after USED, without waiting for client enumeration or configuration selection.
    pub fn commit(
        &mut self,
        cancel: &AtomicBool,
        persist: impl FnOnce() -> Result<()>,
    ) -> Result<StubReceipt> {
        require(
            !self.started && !cancel.load(Ordering::Acquire),
            "stub commit repeated or cancelled",
        )?;
        self.capability.available()?;
        persist()?;
        self.capability.available()?;
        require(
            !cancel.load(Ordering::Acquire),
            "stub commit cancelled before write",
        )?;
        // A cloned attribute shares its file position; reset it explicitly before this single store.
        self.capability.attribute.seek(SeekFrom::Start(0))?;
        let value = format!("{}\n", self.kernel.as_raw_fd());
        self.started = true;
        require(
            self.capability.attribute.write(value.as_bytes())? == value.len(),
            "short stub attribute write; intent retained",
        )?;
        let current_profile = self.capability.current(2)?;
        Ok(StubReceipt {
            export: self.capability.receipt.clone(),
            kernel_socket_inode: self.socket_inode,
            usbip_status: 2,
            current_profile,
        })
    }
    /// Explicitly stop this socket and independently await AVAILABLE or disappearance of the old generation.
    pub fn shutdown(&mut self) -> Result<()> {
        self.shutdown_proven().map(|_| ())
    }
    /// Stop the retained K and return the exact existing cleanup criterion.
    pub fn shutdown_proven(&mut self) -> Result<rustusb_core::application::BrokerCleanupProof> {
        use rustusb_core::application::BrokerCleanupProof;
        self.kernel.shutdown(Shutdown::Both)?;
        if !self.started {
            return Ok(BrokerCleanupProof::NotStarted);
        }
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if self.capability.registration.gone()? {
                return Ok(BrokerCleanupProof::OriginalGenerationGone);
            }
            let path = &self.capability.receipt.generation.sysfs_path;
            let status = number(&path.join("usbip_status"), 10)?;
            if status == 1 {
                self.capability.available()?;
                return Ok(BrokerCleanupProof::NativeStubAvailable);
            }
            require(status == 2, "Native stub cleanup entered an unknown state")?;
            require(
                Instant::now() < deadline,
                "Native stub cleanup is incomplete",
            )?;
            thread::sleep(Duration::from_millis(50));
        }
    }
}
impl Drop for StubChannel {
    fn drop(&mut self) {
        let _ = self.kernel.shutdown(Shutdown::Both);
    }
}

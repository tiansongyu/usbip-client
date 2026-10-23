//! Anonymous Native import channels. The caller supplies authorization and serializes mutations.
//! Synchronous methods belong in an owned blocking task, never an async executor poll.
use crate::{LocalDeviceGeneration, NativeSession, parse_status};
use rustusb_core::{Error, Result, VhciPort};
use rustusb_protocol::RemoteDevice;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
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

/// Exact local controller/attribute identity; meaningful only in this boot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControllerReceipt {
    /// Canonical path selected from the fixed platform directory.
    pub path: PathBuf,
    /// Filesystem device of the controller directory.
    pub filesystem_device: u64,
    /// Inode of that controller directory.
    pub inode: u64,
    /// Inode of the preopened attach attribute.
    pub attribute_inode: u64,
    /// Filesystem device of the attach attribute.
    pub attribute_device: u64,
}
/// Durable intent written before the one sysfs transaction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportIntent {
    /// Exact controller and preopened attribute.
    pub controller: ControllerReceipt,
    /// Initially NULL port selected while the caller owns the mutation guard.
    pub port: VhciPort,
}
/// Complete current imported interface metadata, observed independently of the remote wire tuple.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportedInterface {
    /// Actual interface number.
    pub number: u8,
    /// Actual alternate setting; the standard DEVLIST does not authenticate an alternate baseline.
    pub alternate: u8,
    /// Interface class.
    pub class: u8,
    /// Interface subclass.
    pub subclass: u8,
    /// Interface protocol.
    pub protocol: u8,
}
/// Complete active imported configuration observation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportedProfile {
    /// Actual active configuration.
    pub configuration: u8,
    /// Complete current interfaces ordered by their numbers.
    pub interfaces: Vec<ImportedInterface>,
}
/// Independently observed kernel result, not an official usbip endpoint record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportReceipt {
    /// Persisted original controller/port intent.
    pub intent: ImportIntent,
    /// Fully observed local kernel generation.
    pub session: NativeSession,
    /// Inode of the retained kernel-end socket.
    pub kernel_socket_inode: u64,
    /// Complete profile when enumeration reached that point; absent in partial cleanup observations.
    pub profile: Option<ImportedProfile>,
}

fn require(value: bool, message: &str) -> Result<()> {
    if value {
        Ok(())
    } else {
        Err(Error::State(message.into()))
    }
}
fn bounded(path: &Path) -> Result<String> {
    let mut bytes = Vec::new();
    File::open(path)?.take(65537).read_to_end(&mut bytes)?;
    require(bytes.len() <= 65536, "Native attribute exceeds bound")?;
    String::from_utf8(bytes).map_err(|_| Error::State("invalid Native attribute encoding".into()))
}
fn number(path: &Path, radix: u32) -> Result<u32> {
    let value = bounded(path)?;
    let text = value.trim();
    require(
        !text.is_empty() && text.chars().all(|c| c.is_digit(radix)),
        "invalid Native numeric attribute",
    )?;
    u32::from_str_radix(text, radix).map_err(|_| Error::State("Native number out of bounds".into()))
}
/// Validate untrusted metadata before allocating a kernel capability.
/// Bus numbers use the full 16-bit half of the kernel devid, without the legacy tool's u8 narrowing.
/// Speeds 1..=5 match the audited Linux v6.8 attach whitelist (super-plus is not included).
/// This first secure API requires a configured descriptor snapshot with a complete interface list;
/// an unconfigured wire advertisement is unsupported, never silently filled from guessed defaults.
pub fn validate_remote(remote: &RemoteDevice) -> Result<()> {
    require(
        (1..=u32::from(u16::MAX)).contains(&remote.bus_number)
            && (1..=127).contains(&remote.device_number)
            && matches!(remote.speed, 1..=5)
            && remote.configuration != 0
            && remote.num_configurations != 0
            && !remote.interfaces.is_empty()
            && remote.interfaces.len() <= rustusb_protocol::MAX_INTERFACES
            && remote.path.len() <= 255
            && !remote.path.chars().any(char::is_control),
        "unsupported secure Native descriptor",
    )
}
#[derive(Clone)]
struct Row {
    session: NativeSession,
    hub: String,
    speed: u32,
}
fn controller() -> Result<PathBuf> {
    let mut candidates = Vec::new();
    let mut entries = 0;
    for entry in fs::read_dir("/sys/devices/platform")? {
        entries += 1;
        require(entries <= 4096, "platform directory exceeds bound")?;
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name
            .strip_prefix("vhci_hcd.")
            .is_some_and(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        {
            let path = entry.path();
            let metadata = fs::symlink_metadata(&path)?;
            require(
                metadata.is_dir() && metadata.uid() == 0 && metadata.mode() & 0o022 == 0,
                "untrusted VHCI controller",
            )?;
            if path.join("status").try_exists()? {
                candidates.push(path);
            }
        }
    }
    require(
        candidates.len() == 1,
        "one unambiguous loaded VHCI controller is required",
    )?;
    candidates
        .pop()
        .ok_or_else(|| Error::State("missing VHCI controller".into()))
}
fn rows(controller: &Path) -> Result<Vec<Row>> {
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(rows) = read_rows(controller)? {
            return Ok(rows);
        }
        require(Instant::now() < deadline, "VHCI snapshot did not settle")?;
        thread::sleep(Duration::from_millis(25));
    }
}
fn read_rows(controller: &Path) -> Result<Option<Vec<Row>>> {
    let count = number(&controller.join("nports"), 10)?;
    require((1..=256).contains(&count), "unsupported VHCI port count")?;
    let mut attributes = Vec::new();
    let mut entries = 0;
    for entry in fs::read_dir(controller)? {
        entries += 1;
        require(entries <= 1024, "VHCI directory exceeds bound")?;
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name == "status"
            || name
                .strip_prefix("status.")
                .is_some_and(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        {
            attributes.push(entry.path());
        }
    }
    attributes.sort();
    let mut seen = BTreeSet::new();
    let mut output = Vec::new();
    for attribute in attributes {
        let text = bounded(&attribute)?;
        let parsed = parse_status(&text)?;
        for (session, line) in parsed.into_iter().zip(text.lines().skip(1)) {
            let fields = line.split_whitespace().collect::<Vec<_>>();
            require(
                seen.insert(session.port.0) && u32::from(session.port.0) < count,
                "duplicate or out-of-range VHCI port",
            )?;
            if session.status == 4 {
                require(
                    fields[3].bytes().all(|b| b == b'0')
                        && fields[4].bytes().all(|b| b == b'0')
                        && fields[5].bytes().all(|b| b == b'0'),
                    "NULL port retains unexpected metadata",
                )?;
            }
            let speed = fields[3]
                .parse()
                .map_err(|_| Error::State("invalid kernel speed".into()))?;
            output.push(Row {
                session,
                hub: fields[0].to_owned(),
                speed,
            });
        }
        if bounded(&attribute)? != text {
            return Ok(None);
        }
    }
    require(output.len() == count as usize, "incomplete VHCI snapshot")?;
    Ok(Some(output))
}
fn observe_local(session: &NativeSession, controller: &Path) -> Result<NativeSession> {
    let bus = session
        .local_bus_id
        .as_ref()
        .ok_or_else(|| Error::State("missing local USB identity".into()))?;
    let path = Path::new("/sys/bus/usb/devices").join(bus.as_str());
    let real = fs::canonicalize(&path)?;
    require(
        real.starts_with(controller),
        "imported device is outside the selected controller",
    )?;
    let before = fs::metadata(&real)?;
    require(
        before.is_dir() && before.uid() == 0,
        "invalid local USB directory",
    )?;
    let address = number(&real.join("devnum"), 10)?;
    let vendor = u16::try_from(number(&real.join("idVendor"), 16)?)
        .map_err(|_| Error::State("invalid vendor".into()))?;
    let product = u16::try_from(number(&real.join("idProduct"), 16)?)
        .map_err(|_| Error::State("invalid product".into()))?;
    let after = fs::metadata(&path)?;
    require(
        before.dev() == after.dev()
            && before.ino() == after.ino()
            && (1..=127).contains(&address)
            && number(&real.join("devnum"), 10)? == address
            && number(&real.join("idVendor"), 16)? == u32::from(vendor)
            && number(&real.join("idProduct"), 16)? == u32::from(product),
        "local USB generation changed",
    )?;
    let mut observed = session.clone();
    observed.vendor_id = Some(vendor);
    observed.product_id = Some(product);
    observed.local_device_address = Some(address);
    observed.local_device_generation = Some(LocalDeviceGeneration {
        filesystem_device: before.dev(),
        inode: before.ino(),
    });
    Ok(observed)
}
fn observed_profile(
    session: &NativeSession,
    remote: &RemoteDevice,
) -> Result<Option<ImportedProfile>> {
    let bus = session
        .local_bus_id
        .as_ref()
        .ok_or_else(|| Error::State("profile needs local USB identity".into()))?;
    let path = Path::new("/sys/bus/usb/devices").join(bus.as_str());
    let config = bounded(&path.join("bConfigurationValue"))?;
    if config.is_empty() {
        return Ok(None);
    }
    let configuration = u8::try_from(number(&path.join("bConfigurationValue"), 10)?)
        .map_err(|_| Error::State("invalid imported configuration".into()))?;
    let count = number(&path.join("bNumInterfaces"), 10)?;
    require(
        configuration == remote.configuration && count as usize == remote.interfaces.len(),
        "imported configuration/interface count differs",
    )?;
    let mut interfaces = Vec::new();
    let mut checked = 0;
    for entry in fs::read_dir("/sys/bus/usb/devices")? {
        checked += 1;
        require(checked <= 4096, "USB interface directory exceeds bound")?;
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if !name.starts_with(&format!("{}:", bus.as_str())) {
            continue;
        }
        let path = entry.path();
        let byte = |name: &str, radix| -> Result<u8> {
            u8::try_from(number(&path.join(name), radix)?)
                .map_err(|_| Error::State("invalid imported interface field".into()))
        };
        interfaces.push(ImportedInterface {
            number: byte("bInterfaceNumber", 16)?,
            alternate: byte("bAlternateSetting", 10)?,
            class: byte("bInterfaceClass", 16)?,
            subclass: byte("bInterfaceSubClass", 16)?,
            protocol: byte("bInterfaceProtocol", 16)?,
        });
    }
    interfaces.sort_by_key(|i| i.number);
    if interfaces.len() < count as usize {
        return Ok(None);
    }
    require(
        interfaces.len() == count as usize
            && interfaces.windows(2).all(|w| w[0].number != w[1].number)
            && interfaces.iter().zip(&remote.interfaces).all(|(a, b)| {
                (a.class, a.subclass, a.protocol) == (b.class, b.subclass, b.protocol)
            }),
        "imported full interface set differs from authenticated descriptors",
    )?;
    require(
        bounded(&path.join("bConfigurationValue"))? == config,
        "imported configuration changed during observation",
    )?;
    Ok(Some(ImportedProfile {
        configuration,
        interfaces,
    }))
}
fn old_local_gone(session: &NativeSession) -> Result<bool> {
    let bus = session
        .local_bus_id
        .as_ref()
        .ok_or_else(|| Error::State("cleanup needs local bus receipt".into()))?;
    let generation = session
        .local_device_generation
        .ok_or_else(|| Error::State("cleanup needs local generation receipt".into()))?;
    match fs::metadata(Path::new("/sys/bus/usb/devices").join(bus.as_str())) {
        Ok(metadata) => Ok(
            metadata.dev() != generation.filesystem_device || metadata.ino() != generation.inode
        ),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(error) => Err(error.into()),
    }
}
fn controller_matches(receipt: &ControllerReceipt) -> Result<()> {
    let metadata = fs::symlink_metadata(&receipt.path)?;
    require(
        metadata.is_dir()
            && metadata.dev() == receipt.filesystem_device
            && metadata.ino() == receipt.inode,
        "VHCI controller generation changed",
    )
}
/// Read-only restart reconciliation; never detaches a replacement or writes USB state.
pub fn released(receipt: &ImportReceipt) -> Result<bool> {
    controller_matches(&receipt.intent.controller)?;
    let null = rows(&receipt.intent.controller.path)?
        .iter()
        .any(|r| r.session.port == receipt.intent.port && r.session.status == 4);
    Ok(null
        && old_local_gone(&receipt.session)?
        && rows(&receipt.intent.controller.path)?
            .iter()
            .any(|r| r.session.port == receipt.intent.port && r.session.status == 4)
        && old_local_gone(&receipt.session)?)
}

/// Conservatively reconcile an interrupted intent without a local receipt.
/// Every port and every downstream USB generation must be absent; never writes kernel state.
pub fn unidentified_released(intent: &ImportIntent) -> Result<bool> {
    controller_matches(&intent.controller)?;
    if !rows(&intent.controller.path)?
        .iter()
        .all(|r| r.session.status == 4)
    {
        return Ok(false);
    }
    let mut checked = 0;
    for entry in fs::read_dir("/sys/bus/usb/devices")? {
        checked += 1;
        require(
            checked <= 4096,
            "USB directory exceeds reconciliation bound",
        )?;
        let entry = entry?;
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if name.contains(':') || rustusb_core::BusId::new(name.as_ref()).is_err() {
            continue;
        }
        if fs::canonicalize(entry.path())?.starts_with(&intent.controller.path) {
            return Ok(false);
        }
    }
    Ok(rows(&intent.controller.path)?
        .iter()
        .all(|r| r.session.status == 4))
}

/// Helper-owned endpoint and its kernel transaction. Drop closes a reference only.
/// The supervisor must call `shutdown` and independently verify release.
pub struct ImportChannel {
    kernel: UnixStream,
    remote: RemoteDevice,
    intent: Option<ImportIntent>,
    local: Option<NativeSession>,
    write_started: bool,
    socket_inode: u64,
    profile: Option<ImportedProfile>,
}
impl ImportChannel {
    /// Create an anonymous blocking pair, returning only the relay endpoint to delegate.
    pub fn prepare(remote: RemoteDevice) -> Result<(Self, UnixStream)> {
        validate_remote(&remote)?;
        let (kernel, relay) = UnixStream::pair()?;
        let metadata = fs::metadata(format!("/proc/self/fd/{}", kernel.as_raw_fd()))?;
        require(
            metadata.file_type().is_socket(),
            "kernel capability is not a socket",
        )?;
        Ok((
            Self {
                kernel,
                remote,
                intent: None,
                local: None,
                write_started: false,
                socket_inode: metadata.ino(),
                profile: None,
            },
            relay,
        ))
    }
    /// Perform one actual attach. The callback must durably store intent before the write.
    /// Caller retains its mutation guard throughout this blocking operation.
    pub fn commit(
        &mut self,
        cancel: &AtomicBool,
        mut persist: impl FnMut(&ImportIntent) -> Result<()>,
    ) -> Result<ImportReceipt> {
        require(
            self.intent.is_none() && !self.write_started,
            "Native channel has already been committed",
        )?;
        require(
            !cancel.load(Ordering::Acquire),
            "Native channel cancelled before commit",
        )?;
        let controller = controller()?;
        let hub = if self.remote.speed >= 5 { "ss" } else { "hs" };
        let before = rows(&controller)?;
        let selected = before
            .iter()
            .find(|r| r.hub == hub && r.session.status == 4)
            .ok_or_else(|| Error::State("no free compatible VHCI port".into()))?
            .session
            .port;
        let directory = fs::metadata(&controller)?;
        let path = controller.join("attach");
        let attribute = fs::symlink_metadata(&path)?;
        require(
            attribute.is_file() && attribute.uid() == 0 && attribute.mode() & 0o022 == 0,
            "untrusted VHCI attach attribute",
        )?;
        let mut file = OpenOptions::new().write(true).open(&path)?;
        let opened = file.metadata()?;
        require(
            opened.dev() == attribute.dev() && opened.ino() == attribute.ino(),
            "attach attribute replaced while opening",
        )?;
        let intent = ImportIntent {
            controller: ControllerReceipt {
                path: controller.clone(),
                filesystem_device: directory.dev(),
                inode: directory.ino(),
                attribute_inode: opened.ino(),
                attribute_device: opened.dev(),
            },
            port: selected,
        };
        persist(&intent)?;
        self.intent = Some(intent.clone());
        controller_matches(&intent.controller)?;
        let current = fs::symlink_metadata(&path)?;
        require(
            current.dev() == opened.dev()
                && current.ino() == opened.ino()
                && rows(&controller)?
                    .iter()
                    .any(|r| r.session.port == selected && r.hub == hub && r.session.status == 4),
            "selected attach resource changed",
        )?;
        require(
            !cancel.load(Ordering::Acquire),
            "Native channel cancelled before write",
        )?;
        let devid = (self.remote.bus_number << 16) | self.remote.device_number;
        let value = format!(
            "{} {} {} {}\n",
            selected.0,
            self.kernel.as_raw_fd(),
            devid,
            self.remote.speed
        );
        self.write_started = true;
        let count = file.write(value.as_bytes())?;
        require(
            count == value.len(),
            "short VHCI write; outcome requires reconciliation",
        )?;
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            require(
                !cancel.load(Ordering::Acquire),
                "Native channel cancelled during enumeration",
            )?;
            let snapshot = rows(&controller)?;
            let row = snapshot
                .iter()
                .find(|r| r.session.port == selected)
                .ok_or_else(|| Error::State("selected port disappeared".into()))?;
            if row.session.status == 6 {
                let local = observe_local(&row.session, &controller)?;
                require(
                    row.speed == self.remote.speed
                        && local.remote_device_id == Some(devid)
                        && local.vendor_id == Some(self.remote.vendor_id)
                        && local.product_id == Some(self.remote.product_id),
                    "enumerated Native identity differs from authenticated metadata",
                )?;
                self.local = Some(local.clone());
                self.profile = observed_profile(&local, &self.remote)?;
                if self.profile.is_some() {
                    controller_matches(&intent.controller)?;
                    return Ok(ImportReceipt {
                        intent,
                        session: local,
                        kernel_socket_inode: self.socket_inode,
                        profile: self.profile.clone(),
                    });
                }
            }
            require(
                matches!(row.session.status, 4..=6),
                "Native import entered kernel error state",
            )?;
            require(
                Instant::now() < deadline,
                "Native enumeration did not complete before deadline",
            )?;
            thread::sleep(Duration::from_millis(50));
        }
    }
    /// Explicitly shut down this retained socket, then verify actual asynchronous cleanup.
    /// Unknown pre-enumeration effects remain unresolved instead of guessing a port owner.
    pub fn shutdown(&mut self) -> Result<()> {
        self.kernel.shutdown(Shutdown::Both)?;
        if !self.write_started {
            return Ok(());
        }
        let intent = self
            .intent
            .as_ref()
            .ok_or_else(|| Error::State("missing Native write intent".into()))?;
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            controller_matches(&intent.controller)?;
            let current = rows(&intent.controller.path)?;
            let row = current
                .iter()
                .find(|r| r.session.port == intent.port)
                .ok_or_else(|| Error::State("cleanup port disappeared".into()))?;
            if row.session.status == 6 {
                let observed = observe_local(&row.session, &intent.controller.path)?;
                if let Some(local) = &self.local {
                    require(
                        crate::same_identity(local, &observed),
                        "cleanup sees a replacement Native session",
                    )?;
                } else {
                    require(
                        observed.remote_device_id
                            == Some((self.remote.bus_number << 16) | self.remote.device_number)
                            && observed.vendor_id == Some(self.remote.vendor_id)
                            && observed.product_id == Some(self.remote.product_id)
                            && row.speed == self.remote.speed,
                        "cleanup cannot adopt an unrelated Native port",
                    )?;
                    self.local = Some(observed);
                }
            }
            if row.session.status == 4 {
                let local = self.local.as_ref().ok_or_else(|| Error::State("attach ended without a complete local generation receipt; retain pending intent".into()) )?;
                if old_local_gone(local)? {
                    return Ok(());
                }
            }
            require(
                Instant::now() < deadline,
                "Native channel cleanup remains incomplete",
            )?;
            thread::sleep(Duration::from_millis(50));
        }
    }
    /// Last complete observation, including failed metadata validation, for durable cleanup.
    pub fn observed_receipt(&self) -> Option<ImportReceipt> {
        Some(ImportReceipt {
            intent: self.intent.clone()?,
            session: self.local.clone()?,
            kernel_socket_inode: self.socket_inode,
            profile: self.profile.clone(),
        })
    }
}

impl Drop for ImportChannel {
    fn drop(&mut self) {
        // Last-resort initiation only. The durable intent remains unless explicit shutdown verified cleanup.
        let _ = self.kernel.shutdown(Shutdown::Both);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn remote_bounds_reject_before_any_sysfs_access() -> Result<()> {
        let mut remote = RemoteDevice {
            bus_id: rustusb_core::BusId::new("1-1")?,
            path: "/remote/device".into(),
            bus_number: 1,
            device_number: 2,
            speed: 3,
            vendor_id: 1,
            product_id: 2,
            device_version: 0x100,
            class: 0,
            subclass: 0,
            protocol: 0,
            configuration: 1,
            num_configurations: 1,
            interfaces: vec![rustusb_protocol::RemoteInterface {
                class: 3,
                subclass: 1,
                protocol: 1,
            }],
        };
        validate_remote(&remote)?;
        for bus in [256, u32::from(u16::MAX)] {
            remote.bus_number = bus;
            validate_remote(&remote)?;
        }
        remote.bus_number = u32::from(u16::MAX) + 1;
        assert!(validate_remote(&remote).is_err());
        remote.bus_number = 1;
        for speed in [0, 6, u32::MAX] {
            remote.speed = speed;
            assert!(validate_remote(&remote).is_err());
        }
        remote.speed = 3;
        remote.device_number = 128;
        assert!(validate_remote(&remote).is_err());
        remote.device_number = 2;
        remote.interfaces.clear();
        assert!(validate_remote(&remote).is_err());
        Ok(())
    }
}

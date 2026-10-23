//! Shared domain types and backend contracts. This crate performs no privileged I/O.

mod device_key;
pub use device_key::{DeviceBackend, DeviceKey, VirtualProfile};

use serde::{Deserialize, Serialize};
use std::{fmt, future::Future, net::IpAddr, num::NonZeroU16, str::FromStr};

/// Typed failures shared by adapters and frontends.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A supplied identifier or configuration is invalid.
    #[error("invalid input: {0}")]
    InvalidInput(String),
    /// Underlying OS I/O failure.
    #[error("I/O: {0}")]
    Io(#[from] std::io::Error),
    /// Peer sent an invalid USB/IP message.
    #[error("protocol: {0}")]
    Protocol(String),
    /// A bounded operation exceeded its deadline.
    #[error("operation timed out: {0}")]
    Timeout(String),
    /// OS identity or policy denied an operation.
    #[error("permission denied: {0}")]
    Permission(String),
    /// Export safety could not be established.
    #[error("unsafe device: {0}")]
    UnsafeDevice(String),
    /// A fixed native tool failed, possibly after changing kernel state.
    #[error("native operation: {0}")]
    Command(String),
    /// Kernel or lifecycle state prevents the requested action.
    #[error("state: {0}")]
    State(String),
    /// A feature is intentionally unavailable at this phase/platform.
    #[error("unsupported: {0}")]
    Unsupported(String),
}

/// Common fallible operation result.
pub type Result<T> = std::result::Result<T, Error>;

/// A validated, current Linux USB bus/port address (never a persistent identity).
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct BusId(String);

impl BusId {
    /// Validate the sysfs bus-port grammar and Linux's 32-byte wire field limit.
    pub fn new(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        let valid_number = |s: &str, max: u32| {
            !s.is_empty()
                && !s.starts_with('0')
                && s.bytes().all(|b| b.is_ascii_digit())
                && s.parse::<u32>().is_ok_and(|n| n > 0 && n <= max)
        };
        let valid = value.len() < 32
            && value.split_once('-').is_some_and(|(bus, ports)| {
                valid_number(bus, 65535)
                    && ports.split('.').count() <= 7
                    && ports.split('.').all(|p| valid_number(p, 255))
            });
        if !valid {
            return Err(Error::InvalidInput(
                "bus ID must be BUS-PORT[.PORT], length <32".into(),
            ));
        }
        Ok(Self(value))
    }
    /// Borrow the validated OS address.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl fmt::Display for BusId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}
impl FromStr for BusId {
    type Err = Error;
    fn from_str(value: &str) -> Result<Self> {
        Self::new(value)
    }
}
impl TryFrom<String> for BusId {
    type Error = Error;
    fn try_from(value: String) -> Result<Self> {
        Self::new(value)
    }
}
impl From<BusId> for String {
    fn from(value: BusId) -> Self {
        value.0
    }
}

/// A literal IPv4/IPv6 address or ASCII DNS hostname, without a port or URL.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct Host(String);
impl Host {
    /// Validate before any resolver, subprocess, or policy comparison.
    pub fn new(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        let valid = !value.is_empty()
            && value.len() <= 253
            && (value.parse::<IpAddr>().is_ok()
                || value
                    .strip_suffix('.')
                    .unwrap_or(&value)
                    .split('.')
                    .all(|label| {
                        !label.is_empty()
                            && label.len() <= 63
                            && !label.starts_with('-')
                            && !label.ends_with('-')
                            && label
                                .bytes()
                                .all(|b| b.is_ascii_alphanumeric() || b == b'-')
                    }));
        if !valid {
            return Err(Error::InvalidInput(
                "expected IPv4, IPv6, or ASCII DNS hostname".into(),
            ));
        }
        Ok(Self(value.to_ascii_lowercase()))
    }
    /// Borrow the validated host suitable for a resolver or one argv entry.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl fmt::Display for Host {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(f)
    }
}
impl FromStr for Host {
    type Err = Error;
    fn from_str(value: &str) -> Result<Self> {
        Self::new(value)
    }
}
impl TryFrom<String> for Host {
    type Error = Error;
    fn try_from(value: String) -> Result<Self> {
        Self::new(value)
    }
}
impl From<Host> for String {
    fn from(value: Host) -> Self {
        value.0
    }
}

/// An administrator-approved USB/IP endpoint; authentication is external in Phase 1.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Peer {
    /// IP address or DNS hostname.
    pub host: Host,
    /// Nonzero TCP port.
    pub port: NonZeroU16,
}

/// A Linux usbip utility port, bounded to prevent the utility's u8 truncation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct VhciPort(pub u8);

/// USB endpoint metadata from sysfs.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Endpoint {
    /// Endpoint address, including IN bit.
    pub address: u8,
    /// Raw bmAttributes; low two bits identify transfer type.
    pub attributes: u8,
    /// Maximum packet size descriptor value.
    pub max_packet_size: u16,
    /// Polling interval descriptor value.
    pub interval: u8,
}

/// One currently selected interface alternate setting.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Interface {
    /// Interface number.
    pub number: u8,
    /// Current alternate setting, not a list of all possible alternates.
    pub alternate_setting: u8,
    /// USB class code.
    pub class: u8,
    /// USB subclass code.
    pub subclass: u8,
    /// USB protocol code.
    pub protocol: u8,
    /// Bound local driver, if any.
    pub driver: Option<String>,
    /// Currently visible endpoints.
    pub endpoints: Vec<Endpoint>,
}

/// A discovered device; discovery never promises redirection compatibility.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Device {
    /// Candidate persistent fingerprint; topology is included, not cryptographic identity.
    pub id: String,
    /// Ephemeral current kernel bus ID.
    pub bus_id: BusId,
    /// USB vendor ID.
    pub vendor_id: u16,
    /// USB product ID.
    pub product_id: u16,
    /// Optional descriptor manufacturer string.
    pub manufacturer: Option<String>,
    /// Optional descriptor product string.
    pub product: Option<String>,
    /// Sensitive optional serial; never include it in logs.
    pub serial: Option<String>,
    /// Current device address.
    pub device_address: u32,
    /// bcdUSB as rendered by sysfs, distinct from bcdDevice.
    pub usb_version: String,
    /// Kernel speed in Mbit/s as rendered by sysfs.
    pub speed: String,
    /// USB device class code.
    pub class: u8,
    /// USB device subclass code.
    pub subclass: u8,
    /// USB device protocol code.
    pub protocol: u8,
    /// Controller identity plus physical port chain.
    pub topology: String,
    /// Device-level driver.
    pub driver: Option<String>,
    /// Currently visible interfaces.
    pub interfaces: Vec<Interface>,
    /// Bound to usbip-host.
    pub exported: bool,
    /// Export is currently held by the kernel, if known.
    pub occupied: Option<bool>,
    /// Reasons why automatic export safety cannot be proven. Empty means eligible, not verified.
    pub export_blockers: Vec<String>,
}

/// Linux identity receipt for an explicitly authorized usbfs device descriptor.
/// This ties a capability to one current device generation, not persistent identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PhysicalDeviceReceipt {
    /// Filesystem device number containing the canonical sysfs device directory.
    pub sysfs_device: u64,
    /// Inode of that sysfs directory; replacement must not inherit authorization.
    pub sysfs_inode: u64,
    /// Character-node st_rdev, identifying the opened usbfs device.
    pub node_device: u64,
    /// Inode of the opened usbfs character node.
    pub node_inode: u64,
    /// Current USB address (1..=127), distinct from the port chain.
    pub usb_device_number: u32,
    /// Current USB bus number, used only to construct the fixed usbfs node path.
    pub bus_number: u16,
}

/// All lifecycle states required by the full service design.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum SessionState {
    /// Seen but not yet eligible.
    Discovered,
    /// Eligible and idle.
    Available,
    /// Driver binding in progress.
    Exporting,
    /// Remote import in progress.
    Connecting,
    /// Kernel reports an enumerated connected device.
    Connected,
    /// Release in progress.
    Disconnecting,
    /// Released or transport ended.
    Disconnected,
    /// Operation or transport failure.
    Error,
    /// Reconciling authoritative kernel state, never automatic storage resume.
    Recovering,
}
impl SessionState {
    /// Enforce allowed transitions; same-state cleanup is idempotent.
    pub fn transition(self, next: Self) -> Result<Self> {
        use SessionState::*;
        let allowed = self == next
            || next == Error
            || matches!(
                (self, next),
                (Discovered, Available)
                    | (Available, Exporting | Connecting)
                    | (Exporting, Available | Connecting)
                    | (Connecting, Connected | Disconnecting)
                    | (Connected, Disconnecting)
                    | (Disconnecting, Disconnected)
                    | (Disconnected | Error, Recovering)
                    | (Recovering, Available | Disconnected)
            );
        if allowed {
            Ok(next)
        } else {
            Err(crate::Error::State(format!(
                "invalid transition {self:?} -> {next:?}"
            )))
        }
    }
}

/// Synchronous read-only discovery; async callers must isolate blocking sysfs I/O.
pub trait DeviceDiscovery: Send + Sync {
    /// Scan current kernel state, reporting errors instead of fabricating empty results.
    fn scan(&self) -> Result<Vec<Device>>;
}

/// Whole-device export capability. Dropping the future does not imply rollback.
pub trait ExportBackend: Send + Sync {
    /// Bind a device for export; callers authorize and safety-check first.
    fn export(&self, bus_id: BusId) -> impl Future<Output = Result<()>> + Send;
    /// Release a device and reconcile driver state; idempotent when already released.
    fn unexport(&self, bus_id: BusId) -> impl Future<Output = Result<()>> + Send;
}

/// Native client capability; kernel state is authoritative after timeouts/cancellation.
pub trait ImportBackend: Send + Sync {
    /// Import a single physical device from an approved endpoint.
    fn attach(&self, peer: Peer, bus_id: BusId) -> impl Future<Output = Result<()>> + Send;
    /// Release a checked VHCI port, returning only after verified cleanup or explicit error.
    fn detach(&self, port: VhciPort) -> impl Future<Output = Result<()>> + Send;
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validate_external_identifiers_and_serde() -> Result<()> {
        for s in ["1-1", "65535-255.2.3"] {
            assert_eq!(BusId::new(s)?.as_str(), s);
        }
        for s in [
            "", "../1-2", "-1", "1-0", "1-1;id", "1-1\n", "1-256", "01-1", "usb1",
        ] {
            assert!(BusId::new(s).is_err(), "{s:?}");
            assert!(serde_json::from_value::<BusId>(serde_json::json!(s)).is_err());
        }
        for s in ["localhost", "192.0.2.1", "::1", "example.org"] {
            assert!(Host::new(s).is_ok());
        }
        for s in [
            "",
            "-evil",
            "user@host",
            "host:3240",
            "$(id)",
            "host\n",
            "a..b",
            "a..",
            "a/",
        ] {
            assert!(Host::new(s).is_err());
        }
        Ok(())
    }
    #[test]
    fn lifecycle_rejects_unsafe_shortcuts() -> Result<()> {
        use SessionState::*;
        assert!(Connected.transition(Connecting).is_err());
        assert_eq!(
            Error.transition(Recovering)?.transition(Disconnected)?,
            Disconnected
        );
        assert_eq!(Disconnecting.transition(Disconnecting)?, Disconnecting);
        assert!(Disconnected.transition(Connected).is_err());
        Ok(())
    }
}

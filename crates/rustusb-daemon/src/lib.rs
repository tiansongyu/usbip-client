//! Bounded local IPC and administrator policy for the Linux privileged helper.
//!
//! The helper is the only RustUSB component intended to run as root. It never
//! starts usbipd or configures a firewall. Native USB/IP traffic requires an
//! independently administered trusted VPN or tunnel.

use rustusb_core::application::{BrokerCorrelation, BrokerResourceKind, ResourceId};
use rustusb_core::{BusId, DeviceKey, Error, Peer, Result, VhciPort};
mod broker;
pub use broker::{
    BrokerPendingPage, BrokerPendingRecord, broker_pending, inspect_broker_operation,
};
use serde::{Deserialize, Serialize};
use std::path::Path;

/// Read-only helper readiness diagnostics shared by all frontends.
pub mod diagnostics;
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
mod journal;
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
mod physical;
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
mod physical_lifecycle;
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
mod physical_recovery;
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
pub use physical::PhysicalGrant;

#[cfg(target_os = "linux")]
mod ancillary;
#[cfg(target_os = "linux")]
pub mod controller;
#[cfg(target_os = "linux")]
mod native_channel;
#[cfg(target_os = "linux")]
mod native_export;
#[cfg(target_os = "linux")]
mod native_state;
#[cfg(target_os = "linux")]
mod storage;
#[cfg(target_os = "linux")]
pub use native_channel::{
    NativeImportHandle, NativeImportReceipt, SecureImportTarget, prepare_native_import,
    prepare_native_import_tracked,
};
#[cfg(target_os = "linux")]
pub use native_export::{
    NativeExportHandle, NativeStubHandle, NativeStubReceipt, prepare_native_export,
    prepare_native_export_tracked,
};

/// Maximum line size, including the terminating newline, for local IPC.
pub const MAX_FRAME_BYTES: usize = 64 * 1024;
/// Maximum simultaneous local connections; rejected connections receive no work.
pub const MAX_CONNECTIONS: usize = 32;
/// Status schema providing generation-aware native session observations.
/// This versions the additive diagnostic response, not the Linux USB/IP wire protocol.
pub const STATUS_SCHEMA_VERSION: u32 = 1;

/// One bounded, explicitly enumerated privileged request.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    /// Root-only explicit controller policy management; never a USB target grant.
    #[cfg(target_os = "linux")]
    Controller {
        /// Bounded administrator operation.
        command: controller::Command,
    },
    /// Read bounded storage-use metadata for one current device; root only, never an export permit.
    InspectStorage {
        /// Candidate fingerprint; no arbitrary filesystem paths.
        device_id: String,
    },
    /// Observe one exact broker-owned recorded operation; does not transfer ownership.
    InspectBrokerOperation {
        /// Concrete capability namespace.
        kind: BrokerResourceKind,
        /// Original application attempt.
        correlation: BrokerCorrelation,
    },
    /// Page broker-owned pending records visible under current policy.
    BrokerPending {
        /// Concrete capability namespace.
        kind: BrokerResourceKind,
        /// Exclusive helper resource cursor.
        after: Option<ResourceId>,
    },
    /// Prepare a tracked anonymous import channel.
    #[cfg(target_os = "linux")]
    PrepareNativeImportTracked {
        /// Authorized remote selection.
        target: SecureImportTarget,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },
    /// Register a tracked Native export.
    #[cfg(target_os = "linux")]
    PrepareNativeExportTracked {
        /// Approved fingerprint.
        device_id: String,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },
    /// Reserve a tracked stub on the same process's live export.
    #[cfg(target_os = "linux")]
    OpenNativeStubTracked {
        /// Original helper registration.
        lease_id: String,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },
    /// Delegate a tracked whole-device capability to the secure broker.
    #[cfg(feature = "physical-usb")]
    OpenSecurePhysicalTracked {
        /// Existing lifecycle protocol, still version 1.
        journal_schema_version: u32,
        /// Approved fingerprint.
        device_id: String,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },

    /// Prepare an anonymous kernel channel for the configured TLS broker.
    #[cfg(target_os = "linux")]
    PrepareNativeImport {
        /// Typed logical peer and authenticated remote metadata supplied by the trusted broker.
        target: SecureImportTarget,
    },
    /// Read unresolved secure Native channel records (root only).
    NativePending {
        /// Exclusive UUID cursor returned by the preceding page.
        after: Option<String>,
    },
    /// Register a separately authorized Native device and retain its original attribute.
    #[cfg(target_os = "linux")]
    PrepareNativeExport {
        /// Fingerprint in the administrator's dedicated secure Native pool.
        device_id: String,
    },
    /// Reserve one anonymous stub channel on an existing same-process export lease.
    #[cfg(target_os = "linux")]
    OpenNativeStub {
        /// Helper-issued live export lease; never a bearer credential.
        lease_id: String,
    },
    /// Retire a demonstrably released original channel, without writing USB state (root only).
    ReconcileNative {
        /// Helper-issued channel identifier.
        channel_id: String,
    },
    /// Delegate a device only to the separate secure broker role.
    #[cfg(feature = "physical-usb")]
    OpenSecurePhysical {
        /// Durable lifecycle protocol version, currently 1.
        journal_schema_version: u32,
        /// Exact device fingerprint from the separate secure pool.
        device_id: String,
    },
    /// Complete an original owner's cooperative physical capability lifecycle.
    #[cfg(feature = "physical-usb")]
    FinishPhysical {
        /// Helper-issued UUID; not a bearer authorization token.
        lease_id: String,
    },
    /// Read a bounded page of unresolved physical grants (root only).
    #[cfg(feature = "physical-usb")]
    PhysicalPending {
        /// Continue after the last lease ID of the preceding page.
        after: Option<String>,
    },
    /// Independently verify restored state or acknowledge a gone generation (root only).
    #[cfg(feature = "physical-usb")]
    ReconcilePhysical {
        /// Exact unresolved helper-issued lease ID.
        lease_id: String,
        /// Explicitly permit retiring an old generation that no longer exists.
        acknowledge_gone: bool,
    },
    /// Delegate one approved current usbfs device capability to this local caller.
    #[cfg(feature = "physical-usb")]
    OpenPhysical {
        /// Required durable lifecycle protocol version, currently 1.
        journal_schema_version: u32,
        /// Exact physical-device fingerprint from the separate administrator allowlist.
        device_id: String,
    },
    /// Export the current device matching an approved candidate fingerprint.
    Export {
        /// Candidate persistent device fingerprint, never an arbitrary path.
        device_id: String,
    },
    /// Release an approved, unoccupied exported device.
    Unexport {
        /// Candidate persistent device fingerprint.
        device_id: String,
    },
    /// Import from an approved, externally protected native USB/IP endpoint.
    Attach {
        /// Administrator-approved endpoint.
        peer: Peer,
        /// Current remote Linux USB address, not a persistent identity.
        bus_id: BusId,
    },
    /// Release only a port demonstrably claimed by this helper instance.
    Detach {
        /// Port assigned by the native backend.
        port: VhciPort,
    },
    /// Read actual native sessions; ownership is reported separately.
    Sessions,
    /// Read helper policy boundaries and current kernel sessions.
    Status,
}

// Internally tagged *unit* variants accept extra fields in serde even with
// deny_unknown_fields. Empty struct wire variants enforce the same strict
// schema as mutation requests while retaining a convenient public unit API.
#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum RequestWire {
    #[cfg(target_os = "linux")]
    Controller {
        command: controller::Command,
    },
    InspectStorage {
        device_id: String,
    },
    /// Observe one exact broker-owned recorded operation; does not transfer ownership.
    InspectBrokerOperation {
        /// Concrete capability namespace.
        kind: BrokerResourceKind,
        /// Original application attempt.
        correlation: BrokerCorrelation,
    },
    /// Page broker-owned pending records visible under current policy.
    BrokerPending {
        /// Concrete capability namespace.
        kind: BrokerResourceKind,
        /// Exclusive helper resource cursor.
        after: Option<ResourceId>,
    },
    /// Prepare a tracked anonymous import channel.
    #[cfg(target_os = "linux")]
    PrepareNativeImportTracked {
        /// Authorized remote selection.
        target: SecureImportTarget,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },
    /// Register a tracked Native export.
    #[cfg(target_os = "linux")]
    PrepareNativeExportTracked {
        /// Approved fingerprint.
        device_id: String,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },
    /// Reserve a tracked stub on the same process's live export.
    #[cfg(target_os = "linux")]
    OpenNativeStubTracked {
        /// Original helper registration.
        lease_id: String,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },
    /// Delegate a tracked whole-device capability to the secure broker.
    #[cfg(feature = "physical-usb")]
    OpenSecurePhysicalTracked {
        /// Existing lifecycle protocol, still version 1.
        journal_schema_version: u32,
        /// Approved fingerprint.
        device_id: String,
        /// Independent application attempt.
        correlation: BrokerCorrelation,
    },

    #[cfg(target_os = "linux")]
    PrepareNativeImport {
        target: SecureImportTarget,
    },
    NativePending {
        #[serde(default)]
        after: Option<String>,
    },
    #[cfg(target_os = "linux")]
    PrepareNativeExport {
        device_id: String,
    },
    #[cfg(target_os = "linux")]
    OpenNativeStub {
        lease_id: String,
    },
    ReconcileNative {
        channel_id: String,
    },
    #[cfg(feature = "physical-usb")]
    OpenSecurePhysical {
        journal_schema_version: u32,
        device_id: String,
    },
    #[cfg(feature = "physical-usb")]
    FinishPhysical {
        lease_id: String,
    },
    #[cfg(feature = "physical-usb")]
    PhysicalPending {
        #[serde(default)]
        after: Option<String>,
    },
    #[cfg(feature = "physical-usb")]
    ReconcilePhysical {
        lease_id: String,
        acknowledge_gone: bool,
    },
    #[cfg(feature = "physical-usb")]
    OpenPhysical {
        device_id: String,
        journal_schema_version: u32,
    },
    Export {
        device_id: String,
    },
    Unexport {
        device_id: String,
    },
    Attach {
        peer: Peer,
        bus_id: BusId,
    },
    Detach {
        port: VhciPort,
    },
    Sessions {},
    Status {},
}

impl<'de> Deserialize<'de> for Request {
    fn deserialize<D: serde::Deserializer<'de>>(
        deserializer: D,
    ) -> std::result::Result<Self, D::Error> {
        Ok(match RequestWire::deserialize(deserializer)? {
            #[cfg(target_os = "linux")]
            RequestWire::Controller { command } => Self::Controller { command },
            RequestWire::InspectStorage { device_id } => Self::InspectStorage { device_id },
            RequestWire::InspectBrokerOperation { kind, correlation } => {
                Self::InspectBrokerOperation { kind, correlation }
            }
            RequestWire::BrokerPending { kind, after } => Self::BrokerPending { kind, after },
            #[cfg(target_os = "linux")]
            RequestWire::PrepareNativeImportTracked {
                target,
                correlation,
            } => Self::PrepareNativeImportTracked {
                target,
                correlation,
            },
            #[cfg(target_os = "linux")]
            RequestWire::PrepareNativeExportTracked {
                device_id,
                correlation,
            } => Self::PrepareNativeExportTracked {
                device_id,
                correlation,
            },
            #[cfg(target_os = "linux")]
            RequestWire::OpenNativeStubTracked {
                lease_id,
                correlation,
            } => Self::OpenNativeStubTracked {
                lease_id,
                correlation,
            },
            #[cfg(feature = "physical-usb")]
            RequestWire::OpenSecurePhysicalTracked {
                device_id,
                journal_schema_version,
                correlation,
            } => Self::OpenSecurePhysicalTracked {
                device_id,
                journal_schema_version,
                correlation,
            },

            #[cfg(target_os = "linux")]
            RequestWire::PrepareNativeImport { target } => Self::PrepareNativeImport { target },
            RequestWire::NativePending { after } => Self::NativePending { after },
            #[cfg(target_os = "linux")]
            RequestWire::PrepareNativeExport { device_id } => {
                Self::PrepareNativeExport { device_id }
            }
            #[cfg(target_os = "linux")]
            RequestWire::OpenNativeStub { lease_id } => Self::OpenNativeStub { lease_id },
            RequestWire::ReconcileNative { channel_id } => Self::ReconcileNative { channel_id },
            #[cfg(feature = "physical-usb")]
            RequestWire::OpenSecurePhysical {
                journal_schema_version,
                device_id,
            } => Self::OpenSecurePhysical {
                journal_schema_version,
                device_id,
            },
            #[cfg(feature = "physical-usb")]
            RequestWire::FinishPhysical { lease_id } => Self::FinishPhysical { lease_id },
            #[cfg(feature = "physical-usb")]
            RequestWire::PhysicalPending { after } => Self::PhysicalPending { after },
            #[cfg(feature = "physical-usb")]
            RequestWire::ReconcilePhysical {
                lease_id,
                acknowledge_gone,
            } => Self::ReconcilePhysical {
                lease_id,
                acknowledge_gone,
            },
            #[cfg(feature = "physical-usb")]
            RequestWire::OpenPhysical {
                device_id,
                journal_schema_version,
            } => Self::OpenPhysical {
                device_id,
                journal_schema_version,
            },
            RequestWire::Export { device_id } => Self::Export { device_id },
            RequestWire::Unexport { device_id } => Self::Unexport { device_id },
            RequestWire::Attach { peer, bus_id } => Self::Attach { peer, bus_id },
            RequestWire::Detach { port } => Self::Detach { port },
            RequestWire::Sessions {} => Self::Sessions,
            RequestWire::Status {} => Self::Status,
        })
    }
}

/// A single local response. Native tool output and USB payloads are not included.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Response {
    /// Whether the operation and required postcondition checks succeeded.
    pub ok: bool,
    /// Structured result, null on failure.
    pub data: serde_json::Value,
    /// Sanitized actionable failure, absent on success.
    pub error: Option<String>,
}

impl Response {
    fn success(data: serde_json::Value) -> Self {
        Self {
            ok: true,
            data,
            error: None,
        }
    }

    fn failure(error: Error) -> Self {
        let category = match &error {
            Error::InvalidInput(_) => "invalid_input",
            Error::Io(_) => "io",
            Error::Protocol(_) => "protocol",
            Error::Timeout(_) => "timeout",
            Error::Permission(_) => "permission_denied",
            Error::UnsafeDevice(_) => "unsafe_device",
            Error::Command(_) => "native_operation",
            Error::State(_) => "state_reconciliation",
            Error::Unsupported(_) => "unsupported",
        };
        tracing::warn!(
            error_category = category,
            "local helper operation rejected or failed"
        );
        // Native stderr is untrusted and may contain descriptor strings. Never
        // forward it across the privileged interface or into default logs.
        let message = match error {
            Error::Command(_) => "native operation failed; kernel state may have changed; inspect status and device binding before retrying; unconfirmed resources require administrator reconciliation".into(),
            Error::Io(e) => format!("I/O failure ({:?}); check system permissions and required files", e.kind()),
            other => other.to_string(),
        };
        Self {
            ok: false,
            data: serde_json::Value::Null,
            error: Some(message),
        }
    }
}

/// One root-configured logical server and its exact secure import key set.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecureImportRule {
    /// Nonsecret configured server identity; never an arbitrary filesystem path.
    pub server_id: String,
    /// Explicit stable backend-qualified device keys permitted through this broker.
    pub device_keys: Vec<DeviceKey>,
}

/// Root-managed Phase 1 authorization. All listed UIDs share management of
/// approved devices and ports created by this running helper instance.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    /// Dedicated non-root TLS broker UID, disjoint from interactive allowed UIDs.
    #[serde(default)]
    pub secure_broker_uid: Option<u32>,
    /// Secure client logical server/device pool; empty disables secure imports.
    #[serde(default)]
    pub secure_imports: Vec<SecureImportRule>,
    /// Secure USBFS pool, disjoint from every legacy/native pool.
    #[serde(default)]
    pub secure_physical_device_ids: Vec<String>,
    /// Secure Native server pool, disjoint from every legacy/USBFS pool.
    #[serde(default)]
    pub secure_native_device_ids: Vec<String>,
    /// Explicit Linux UIDs; an empty list denies all requests.
    pub allowed_uids: Vec<u32>,
    /// Exact candidate device IDs approved for both export and unexport.
    pub export_device_ids: Vec<String>,
    /// Exact whole-device USBFS capabilities, disjoint from Native exports.
    /// Empty by default, including in policies written before this feature.
    #[serde(default)]
    pub physical_device_ids: Vec<String>,
    /// Exact subset permitted to restore persisted original drivers after the
    /// original owner ends, without changing configuration or alternates.
    /// Empty by default. Policy changes take effect after helper restart.
    #[serde(default)]
    pub physical_connect_restore_device_ids: Vec<String>,
    /// Exact endpoints approved for import, including the TCP port.
    pub peers: Vec<Peer>,
    /// Administrator confirmation of an external trusted VPN/tunnel and
    /// firewall. This flag does not itself create encryption or authentication.
    pub trusted_network: bool,
}

impl Policy {
    /// Validate bounded administrator configuration before accepting clients.
    pub fn validate(&self) -> Result<()> {
        if self.allowed_uids.len() > 256
            || self.export_device_ids.len() > 1024
            || self.physical_device_ids.len() > 1024
            || self.physical_connect_restore_device_ids.len() > 1024
            || self.peers.len() > 256
        {
            return Err(Error::InvalidInput("policy entry limit exceeded".into()));
        }
        if self.secure_imports.len() > 64
            || self.secure_physical_device_ids.len() > 16
            || self.secure_native_device_ids.len() > 16
        {
            return Err(Error::InvalidInput(
                "secure broker policy limit exceeded".into(),
            ));
        }
        if self
            .secure_broker_uid
            .is_some_and(|uid| uid == 0 || uid == u32::MAX || self.allowed_uids.contains(&uid))
            || (self.secure_broker_uid.is_none()
                && (!self.secure_imports.is_empty()
                    || !self.secure_physical_device_ids.is_empty()
                    || !self.secure_native_device_ids.is_empty()))
        {
            return Err(Error::InvalidInput(
                "secure broker requires a separate non-root UID".into(),
            ));
        }
        let mut secure_ids = std::collections::HashSet::new();
        for id in self
            .secure_physical_device_ids
            .iter()
            .chain(&self.secure_native_device_ids)
        {
            validate_device_id(id)?;
            if !secure_ids.insert(id)
                || self.export_device_ids.contains(id)
                || self.physical_device_ids.contains(id)
            {
                return Err(Error::InvalidInput(
                    "secure and legacy device pools must be disjoint".into(),
                ));
            }
        }
        let mut servers = std::collections::HashSet::new();
        for rule in &self.secure_imports {
            validate_server_id(&rule.server_id)?;
            if !servers.insert(&rule.server_id)
                || rule.device_keys.is_empty()
                || rule.device_keys.len() > 16
                || rule
                    .device_keys
                    .iter()
                    .collect::<std::collections::HashSet<_>>()
                    .len()
                    != rule.device_keys.len()
            {
                return Err(Error::InvalidInput("invalid secure import pool".into()));
            }
        }
        for id in &self.export_device_ids {
            validate_device_id(id)?;
        }
        for id in &self.physical_device_ids {
            validate_device_id(id)?;
            if self.export_device_ids.contains(id) {
                return Err(Error::InvalidInput(
                    "a device cannot be approved for both Native and physical USBFS export".into(),
                ));
            }
        }
        let mut recovery_ids = std::collections::HashSet::new();
        for id in &self.physical_connect_restore_device_ids {
            validate_device_id(id)?;
            if (!self.physical_device_ids.contains(id)
                && !self.secure_physical_device_ids.contains(id))
                || !recovery_ids.insert(id)
            {
                return Err(Error::InvalidInput(
                    "physical recovery IDs must be a unique subset of an authorized USBFS pool"
                        .into(),
                ));
            }
        }
        Ok(())
    }

    fn authorize_broker(&self, uid: u32) -> Result<()> {
        if uid != 0 && self.secure_broker_uid == Some(uid) {
            Ok(())
        } else {
            Err(Error::Permission(
                "caller is not the configured secure broker".into(),
            ))
        }
    }

    #[cfg(target_os = "linux")]
    fn authorize_import(&self, uid: u32, target: &SecureImportTarget) -> Result<()> {
        self.authorize_broker(uid)?;
        target.validate()?;
        if self.secure_imports.iter().any(|rule| {
            rule.server_id == target.server_id && rule.device_keys.contains(&target.device_key)
        }) {
            Ok(())
        } else {
            Err(Error::Permission(
                "secure logical server/device is not approved".into(),
            ))
        }
    }

    #[cfg(feature = "physical-usb")]
    fn authorize_secure_physical(&self, uid: u32, id: &str) -> Result<()> {
        self.authorize_broker(uid)?;
        validate_device_id(id)?;
        if self
            .secure_physical_device_ids
            .iter()
            .any(|value| value == id)
        {
            Ok(())
        } else {
            Err(Error::Permission(
                "secure physical device is not approved".into(),
            ))
        }
    }

    #[cfg(target_os = "linux")]
    fn authorize_secure_native(&self, uid: u32, id: &str) -> Result<()> {
        self.authorize_broker(uid)?;
        validate_device_id(id)?;
        if self
            .secure_native_device_ids
            .iter()
            .any(|value| value == id)
        {
            Ok(())
        } else {
            Err(Error::Permission(
                "secure Native device is not approved".into(),
            ))
        }
    }

    fn authorize_uid(&self, uid: u32) -> Result<()> {
        if self.allowed_uids.contains(&uid) {
            Ok(())
        } else {
            Err(Error::Permission(
                "caller UID is not approved by the administrator".into(),
            ))
        }
    }

    fn authorize_device(&self, id: &str) -> Result<()> {
        validate_device_id(id)?;
        if self.export_device_ids.iter().any(|allowed| allowed == id) {
            Ok(())
        } else {
            Err(Error::Permission(
                "device fingerprint is not approved".into(),
            ))
        }
    }

    #[cfg(feature = "physical-usb")]
    fn authorize_physical(&self, id: &str) -> Result<()> {
        validate_device_id(id)?;
        if !self.trusted_network {
            return Err(Error::Permission(
                "physical USB export requires the administrator's trusted-network declaration"
                    .into(),
            ));
        }
        if self.physical_device_ids.iter().any(|allowed| allowed == id) {
            Ok(())
        } else {
            Err(Error::Permission(
                "physical device fingerprint is not approved".into(),
            ))
        }
    }

    fn authorize_peer(&self, peer: &Peer) -> Result<()> {
        if !self.trusted_network {
            return Err(Error::Permission("attach requires administrator confirmation of an external trusted network; native USB/IP is not encrypted by this helper".into()));
        }
        if self.peers.contains(peer) {
            Ok(())
        } else {
            Err(Error::Permission("remote endpoint is not approved".into()))
        }
    }
}

fn validate_server_id(id: &str) -> Result<()> {
    if id.is_empty()
        || id.len() > 64
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
    {
        return Err(Error::InvalidInput("invalid configured server ID".into()));
    }
    Ok(())
}

fn validate_device_id(id: &str) -> Result<()> {
    let Some(hex) = id.strip_prefix("usb-") else {
        return Err(Error::InvalidInput(
            "expected usb- followed by a 64-digit lowercase fingerprint".into(),
        ));
    };
    if hex.len() != 64
        || !hex
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(Error::InvalidInput(
            "expected usb- followed by a 64-digit lowercase fingerprint".into(),
        ));
    }
    Ok(())
}

#[cfg(target_os = "linux")]
mod linux;

/// Send one bounded request to a root-owned local socket and verify the server's
/// kernel UID. The ordinary CLI never falls back to executing privileged tools.
pub async fn request(socket: &Path, req: Request) -> Result<Response> {
    #[cfg(target_os = "linux")]
    if matches!(
        req,
        Request::PrepareNativeImport { .. }
            | Request::PrepareNativeExport { .. }
            | Request::OpenNativeStub { .. }
    ) {
        return Err(Error::InvalidInput(
            "use the typed Native handle API for persistent descriptor transactions".into(),
        ));
    }
    #[cfg(feature = "physical-usb")]
    if matches!(
        req,
        Request::OpenPhysical { .. } | Request::OpenSecurePhysical { .. }
    ) {
        return Err(Error::InvalidInput(
            "use open_physical to receive the required file descriptor".into(),
        ));
    }
    #[cfg(target_os = "linux")]
    {
        linux::request(socket, req).await
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (socket, req);
        Err(Error::Unsupported(
            "Linux Unix-socket helper required".into(),
        ))
    }
}

/// Receive an explicitly authorized whole-device USBFS capability from the root
/// helper. This is Linux-only and never falls back to opening a caller path.
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
pub async fn open_physical(socket: &Path, device_id: String) -> Result<PhysicalGrant> {
    linux::open_physical(socket, device_id, false, None).await
}

/// Receive a USBFS capability solely under the configured secure broker role.
/// Reuses the physical journal and FinishPhysical; no trusted-network declaration is inferred.
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
pub async fn open_secure_physical(socket: &Path, device_id: String) -> Result<PhysicalGrant> {
    linux::open_physical(socket, device_id, true, None).await
}

/// Receive a broker capability with durable correlation and terminal evidence.
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
pub async fn open_secure_physical_tracked(
    socket: &Path,
    device_id: String,
    correlation: BrokerCorrelation,
) -> Result<PhysicalGrant> {
    correlation.validate()?;
    linux::open_physical(socket, device_id, true, Some(correlation)).await
}

/// Run the root helper using a root-owned policy and protected socket directory.
/// Existing socket paths are never removed automatically. Unknown kernel ports
/// after a restart are reported but cannot be detached by this instance.
pub async fn serve(config: &Path, socket: &Path) -> Result<()> {
    #[cfg(target_os = "linux")]
    {
        linux::serve(
            config,
            socket,
            Path::new("/var/lib/rustusb/physical"),
            Path::new("/var/lib/rustusb/native"),
            None,
        )
        .await
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (config, socket);
        Err(Error::Unsupported("Linux root helper required".into()))
    }
}

/// Run the helper with an administrator-selected physical journal directory.
/// Frontends cannot select this path. An existing missing/corrupt journal fails
/// closed; deleting the entire directory destroys the recovery history.
pub async fn serve_with_physical_state(config: &Path, socket: &Path, state: &Path) -> Result<()> {
    #[cfg(target_os = "linux")]
    {
        linux::serve(
            config,
            socket,
            state,
            Path::new("/var/lib/rustusb/native"),
            None,
        )
        .await
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (config, socket, state);
        Err(Error::Unsupported("Linux root helper required".into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::Host;

    fn policy() -> Result<Policy> {
        Ok(Policy {
            secure_broker_uid: None,
            secure_imports: vec![],
            secure_physical_device_ids: vec![],
            secure_native_device_ids: vec![],
            allowed_uids: vec![1000],
            physical_device_ids: vec![],
            physical_connect_restore_device_ids: vec![],
            export_device_ids: vec![format!("usb-{}", "a".repeat(64))],
            peers: vec![Peer {
                host: Host::new("192.0.2.1")?,
                port: std::num::NonZeroU16::new(3240)
                    .ok_or_else(|| Error::State("test port".into()))?,
            }],
            trusted_network: true,
        })
    }

    #[test]
    fn native_secure_commands_have_strict_bounded_wire_shapes() -> Result<()> {
        let pending: Request = serde_json::from_str(r#"{"op":"native_pending","after":null}"#)
            .map_err(|_| Error::Protocol("test request".into()))?;
        assert!(matches!(pending, Request::NativePending { after: None }));
        for invalid in [
            r#"{"op":"native_pending","after":42}"#,
            r#"{"op":"native_pending","extra":true}"#,
            r#"{"op":"reconcile_native","channel_id":"id","acknowledge":true}"#,
        ] {
            assert!(serde_json::from_str::<Request>(invalid).is_err());
        }
        #[cfg(target_os = "linux")]
        for invalid in [
            r#"{"op":"prepare_native_export","device_id":"id","path":"/sys/other"}"#,
            r#"{"op":"open_native_stub","lease_id":"id","fd":1}"#,
        ] {
            assert!(serde_json::from_str::<Request>(invalid).is_err());
        }
        Ok(())
    }
    #[test]
    fn policy_is_default_deny_and_bounds_external_ids() -> Result<()> {
        let p = policy()?;
        p.validate()?;
        p.authorize_uid(1000)?;
        assert!(p.authorize_uid(1001).is_err());
        p.authorize_device(&p.export_device_ids[0])?;
        for invalid in ["1-2", "../dev", "usb-a", &format!("usb-{}", "A".repeat(64))] {
            assert!(p.authorize_device(invalid).is_err());
        }
        assert!(
            p.authorize_device(&format!("usb-{}", "b".repeat(64)))
                .is_err()
        );
        let peer = p.peers[0].clone();
        p.authorize_peer(&peer)?;
        let mut untrusted = p.clone();
        untrusted.trusted_network = false;
        assert!(untrusted.authorize_peer(&peer).is_err());
        let mut too_large = p;
        too_large.allowed_uids = vec![1000; 257];
        assert!(too_large.validate().is_err());
        Ok(())
    }

    #[test]
    fn physical_policy_is_separate_bounded_and_backward_compatible() -> Result<()> {
        let mut p = policy()?;
        let encoded =
            serde_json::to_value(&p).map_err(|_| Error::State("test policy encoding".into()))?;
        let mut old = encoded
            .as_object()
            .cloned()
            .ok_or_else(|| Error::State("test policy object".into()))?;
        old.remove("physical_device_ids");
        old.remove("physical_connect_restore_device_ids");
        let restored: Policy = serde_json::from_value(serde_json::Value::Object(old))
            .map_err(|_| Error::State("old policy did not decode".into()))?;
        assert!(restored.physical_device_ids.is_empty());
        assert!(restored.physical_connect_restore_device_ids.is_empty());
        p.physical_device_ids = p.export_device_ids.clone();
        assert!(p.validate().is_err());
        p.physical_device_ids = vec![format!("usb-{}", "b".repeat(64))];
        p.validate()?;
        #[cfg(feature = "physical-usb")]
        {
            p.authorize_physical(&p.physical_device_ids[0])?;
            assert!(p.authorize_physical(&p.export_device_ids[0]).is_err());
            assert!(p.authorize_device(&p.physical_device_ids[0]).is_err());
            p.trusted_network = false;
            assert!(p.authorize_physical(&p.physical_device_ids[0]).is_err());
            let extra = serde_json::json!({"op":"open_physical","device_id":p.physical_device_ids[0],"path":"/dev/other"});
            assert!(serde_json::from_value::<Request>(extra).is_err());
        }
        p.physical_device_ids = vec![format!("usb-{}", "b".repeat(64)); 1025];
        assert!(p.validate().is_err());
        Ok(())
    }

    #[test]
    fn recovery_policy_requires_explicit_unique_bounded_physical_subset() -> Result<()> {
        let mut p = policy()?;
        let id = format!("usb-{}", "b".repeat(64));
        p.physical_connect_restore_device_ids = vec![id.clone()];
        assert!(p.validate().is_err());
        p.physical_device_ids = vec![id.clone()];
        p.validate()?;
        p.physical_connect_restore_device_ids.push(id.clone());
        assert!(p.validate().is_err());
        p.physical_connect_restore_device_ids = vec!["../untrusted".into()];
        assert!(p.validate().is_err());
        p.physical_connect_restore_device_ids = vec![id; 1025];
        assert!(p.validate().is_err());
        Ok(())
    }

    #[test]
    fn malformed_or_unknown_request_fields_are_rejected() {
        for input in [
            r#"{"op":"shell","command":"id"}"#,
            r#"{"op":"sessions","command":"id"}"#,
            r#"{"op":"detach","port":256}"#,
            r#"{"op":"attach","peer":{"host":"-evil","port":3240},"bus_id":"1-1"}"#,
        ] {
            assert!(
                serde_json::from_str::<Request>(input).is_err(),
                "accepted {input}"
            );
        }
    }

    #[cfg(feature = "physical-usb")]
    #[test]
    fn physical_lifecycle_wire_requires_version_and_rejects_extra_privilege_fields() {
        let id = format!("usb-{}", "a".repeat(64));
        let lease = "00000000-0000-0000-0000-000000000001";
        for invalid in [
            serde_json::json!({"op":"open_physical","device_id":id}),
            serde_json::json!({"op":"finish_physical","lease_id":lease,"uid":0}),
            serde_json::json!({"op":"physical_pending","path":"/tmp/elsewhere"}),
            serde_json::json!({"op":"reconcile_physical","lease_id":lease,"acknowledge_gone":false,"force":true}),
            serde_json::json!({"op":"reconcile_physical","lease_id":lease}),
        ] {
            assert!(serde_json::from_value::<Request>(invalid).is_err());
        }
        for valid in [
            Request::OpenPhysical {
                device_id: id,
                journal_schema_version: 1,
            },
            Request::FinishPhysical {
                lease_id: lease.into(),
            },
            Request::PhysicalPending { after: None },
            Request::ReconcilePhysical {
                lease_id: lease.into(),
                acknowledge_gone: false,
            },
        ] {
            assert!(
                serde_json::to_value(valid)
                    .is_ok_and(|value| serde_json::from_value::<Request>(value).is_ok())
            );
        }
    }

    #[test]
    fn command_output_is_not_exposed() {
        let response = Response::failure(Error::Command("SERIAL_SECRET and raw USB bytes".into()));
        assert!(!response.ok);
        assert!(
            !response
                .error
                .is_some_and(|message| message.contains("SERIAL_SECRET"))
        );
    }
}

/// Run the helper with independently selected root-controlled physical and Native journals.
/// Existing missing/corrupt state fails closed; client RPCs cannot select these paths.
pub async fn serve_with_state(
    config: &Path,
    socket: &Path,
    physical: &Path,
    native: &Path,
) -> Result<()> {
    #[cfg(target_os = "linux")]
    {
        linux::serve(config, socket, physical, native, None).await
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (config, socket, physical, native);
        Err(Error::Unsupported("Linux root helper required".into()))
    }
}

#[cfg(test)]
mod secure_policy_tests {
    use super::*;
    fn policy() -> Policy {
        Policy {
            allowed_uids: vec![1000],
            export_device_ids: vec![],
            physical_device_ids: vec![],
            physical_connect_restore_device_ids: vec![],
            peers: vec![],
            trusted_network: false,
            secure_broker_uid: Some(2000),
            secure_imports: vec![SecureImportRule {
                server_id: "example".into(),
                device_keys: vec![DeviceKey::virtual_profile(
                    rustusb_core::VirtualProfile::Hid,
                )],
            }],
            secure_physical_device_ids: vec![],
            secure_native_device_ids: vec![],
        }
    }
    #[test]
    fn secure_role_is_separate_and_pools_cannot_bypass_it() -> Result<()> {
        let mut p = policy();
        p.validate()?;
        assert!(p.authorize_uid(2000).is_err());
        p.authorize_broker(2000)?;
        assert!(p.authorize_broker(1000).is_err());
        p.secure_broker_uid = Some(1000);
        assert!(p.validate().is_err());
        p.secure_broker_uid = Some(0);
        assert!(p.validate().is_err());
        p.secure_broker_uid = None;
        assert!(p.validate().is_err());
        p = policy();
        let id = format!("usb-{}", "a".repeat(64));
        p.secure_physical_device_ids.push(id.clone());
        p.export_device_ids.push(id.clone());
        assert!(p.validate().is_err());
        p.export_device_ids.clear();
        p.physical_connect_restore_device_ids.push(id.clone());
        p.validate()?;
        p.secure_native_device_ids.push(id);
        assert!(p.validate().is_err());
        Ok(())
    }
    #[cfg(feature = "physical-usb")]
    #[test]
    fn secure_usbfs_does_not_assert_trusted_network() -> Result<()> {
        let mut p = policy();
        let id = format!("usb-{}", "b".repeat(64));
        p.secure_physical_device_ids.push(id.clone());
        p.validate()?;
        p.authorize_secure_physical(2000, &id)?;
        assert!(!p.trusted_network);
        assert!(p.authorize_physical(&id).is_err());
        assert!(p.authorize_secure_physical(1000, &id).is_err());
        Ok(())
    }
}

/// Run with an optional separate root-only controller policy store.
/// Omission disables controller management and does not retire or restore policy.
/// The administrator, never an RPC caller, chooses the path.
pub async fn serve_with_controller_state(
    config: &Path,
    socket: &Path,
    physical: &Path,
    native: &Path,
    controller: Option<&Path>,
) -> Result<()> {
    #[cfg(target_os = "linux")]
    {
        linux::serve(config, socket, physical, native, controller).await
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (config, socket, physical, native, controller);
        Err(Error::Unsupported("Linux root helper required".into()))
    }
}

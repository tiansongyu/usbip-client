//! Bounded local IPC and administrator policy for the Linux privileged helper.
//!
//! The helper is the only RustUSB component intended to run as root. It never
//! starts usbipd or configures a firewall. Native USB/IP traffic requires an
//! independently administered trusted VPN or tunnel.

use rustusb_core::{BusId, Error, Peer, Result, VhciPort};
use serde::{Deserialize, Serialize};
use std::path::Path;

/// Read-only helper readiness diagnostics shared by all frontends.
pub mod diagnostics;
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
mod physical;
#[cfg(all(feature = "physical-usb", target_os = "linux"))]
pub use physical::PhysicalGrant;

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
    /// Delegate one approved current usbfs device capability to this local caller.
    #[cfg(feature = "physical-usb")]
    OpenPhysical {
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
    #[cfg(feature = "physical-usb")]
    OpenPhysical {
        device_id: String,
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
            #[cfg(feature = "physical-usb")]
            RequestWire::OpenPhysical { device_id } => Self::OpenPhysical { device_id },
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

/// Root-managed Phase 1 authorization. All listed UIDs share management of
/// approved devices and ports created by this running helper instance.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    /// Explicit Linux UIDs; an empty list denies all requests.
    pub allowed_uids: Vec<u32>,
    /// Exact candidate device IDs approved for both export and unexport.
    pub export_device_ids: Vec<String>,
    /// Exact whole-device USBFS capabilities, disjoint from Native exports.
    /// Empty by default, including in policies written before this feature.
    #[serde(default)]
    pub physical_device_ids: Vec<String>,
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
            || self.peers.len() > 256
        {
            return Err(Error::InvalidInput("policy entry limit exceeded".into()));
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
        Ok(())
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
    #[cfg(feature = "physical-usb")]
    if matches!(req, Request::OpenPhysical { .. }) {
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
    linux::open_physical(socket, device_id).await
}

/// Run the root helper using a root-owned policy and protected socket directory.
/// Existing socket paths are never removed automatically. Unknown kernel ports
/// after a restart are reported but cannot be detached by this instance.
pub async fn serve(config: &Path, socket: &Path) -> Result<()> {
    #[cfg(target_os = "linux")]
    {
        linux::serve(config, socket).await
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = (config, socket);
        Err(Error::Unsupported("Linux root helper required".into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::Host;

    fn policy() -> Result<Policy> {
        Ok(Policy {
            allowed_uids: vec![1000],
            physical_device_ids: vec![],
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
        let restored: Policy = serde_json::from_value(serde_json::Value::Object(old))
            .map_err(|_| Error::State("old policy did not decode".into()))?;
        assert!(restored.physical_device_ids.is_empty());
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

//! Read-only diagnosis through the same authenticated local client as mutations.
//! Filesystem presence alone never establishes helper readiness.

use crate::{Request, Response, request};
use rustusb_core::Error;
use serde::Serialize;
use std::{path::Path, time::Duration};

/// Result of verifying and querying the local helper.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum HelperStatus {
    /// A root peer returned a valid read-only status response for this caller.
    Available,
    /// Required socket or directory is absent.
    Missing,
    /// OS identity or protected path checks rejected the local endpoint.
    Untrusted,
    /// A trusted helper denied this caller's UID or a filesystem denied access.
    PermissionDenied,
    /// The endpoint is not accepting connections; a stale socket may remain.
    Unreachable,
    /// The bounded read-only query exceeded its deadline.
    Timeout,
    /// An authenticated server returned an incompatible status schema.
    UnexpectedResponse,
    /// The platform does not support this local Linux helper.
    Unsupported,
    /// Another local service or I/O error prevented diagnosis.
    Error,
}

/// A sanitized status summary, without device identifiers, native stderr, or policy contents.
#[derive(Debug, Clone, Serialize)]
pub struct HelperDiagnostics {
    /// Local helper readiness; this is independent of a module being loaded.
    pub status: HelperStatus,
    /// Human-readable explanation of the observed result.
    pub detail: String,
    /// Suggested next action; no suggested action is executed by this probe.
    pub action: String,
    /// The connected peer's kernel UID was verified as root.
    pub peer_verified: bool,
    /// Whether the helper could read the kernel VHCI snapshot.
    pub kernel_available: Option<bool>,
    /// Count reported by a valid kernel snapshot, not an I/O or device health assertion.
    pub active_sessions: Option<usize>,
    /// An administrator's policy statement, never proof that a VPN or TLS tunnel exists.
    pub external_trusted_network_confirmed: Option<bool>,
}

impl HelperDiagnostics {
    fn failure(status: HelperStatus, detail: &str, action: &str, peer_verified: bool) -> Self {
        Self {
            status,
            detail: detail.into(),
            action: action.into(),
            peer_verified,
            kernel_available: None,
            active_sessions: None,
            external_trusted_network_confirmed: None,
        }
    }
}

/// Verify a root-owned socket and OS peer identity, then send only `Status`.
/// The eight-second outer deadline does not cancel any mutation because this
/// probe never submits one. It does not load modules, start services, or test USB I/O.
pub async fn diagnose_helper(socket: &Path) -> HelperDiagnostics {
    let result =
        tokio::time::timeout(Duration::from_secs(8), request(socket, Request::Status)).await;
    match result {
        Ok(Ok(response)) => classify_response(response),
        Ok(Err(error)) => classify_error(error),
        Err(_) => HelperDiagnostics::failure(
            HelperStatus::Timeout,
            "The local helper did not answer within the diagnostic deadline.",
            "Check the administrator-managed helper and kernel state; this probe made no changes.",
            false,
        ),
    }
}

fn classify_error(error: Error) -> HelperDiagnostics {
    use std::io::ErrorKind;
    let (status, detail, action) = match error {
        Error::Io(e) if e.kind() == ErrorKind::NotFound => (
            HelperStatus::Missing,
            "The helper socket or its protected directory is missing.",
            "Ask an administrator to install/configure and start rustusb-helper; use the configured --socket path.",
        ),
        Error::Io(e) if e.kind() == ErrorKind::PermissionDenied => (
            HelperStatus::PermissionDenied,
            "Filesystem access to the helper endpoint was denied.",
            "Have an administrator check directory traversal, socket permissions, and the caller UID policy.",
        ),
        Error::Io(e)
            if matches!(
                e.kind(),
                ErrorKind::ConnectionRefused | ErrorKind::ConnectionReset | ErrorKind::BrokenPipe
            ) =>
        {
            (
                HelperStatus::Unreachable,
                "The local helper endpoint is not accepting a status query.",
                "Check helper liveness. An administrator must verify an old helper stopped before removing a stale socket.",
            )
        }
        Error::Permission(_) => (
            HelperStatus::Untrusted,
            "The endpoint failed root path, socket type, or OS peer identity validation.",
            "Use a root-owned Unix socket under protected root-owned directories; do not bypass endpoint validation.",
        ),
        Error::Timeout(_) => (
            HelperStatus::Timeout,
            "The local helper status query exceeded a bounded IPC deadline.",
            "Check the administrator-managed helper; a socket file alone is not a live service.",
        ),
        Error::Protocol(_) => (
            HelperStatus::UnexpectedResponse,
            "The local helper returned an invalid or oversized IPC response.",
            "Install matching RustUSB CLI/helper versions and inspect administrator logs.",
        ),
        Error::Unsupported(_) => (
            HelperStatus::Unsupported,
            "The local helper requires Linux Unix-socket credentials.",
            "Use a supported Linux host; this probe does not provide another platform's USB driver.",
        ),
        _ => (
            HelperStatus::Error,
            "A local I/O or service error prevented helper diagnosis.",
            "Check the helper installation and administrator logs; no device operation was attempted.",
        ),
    };
    HelperDiagnostics::failure(status, detail, action, false)
}

fn classify_response(response: Response) -> HelperDiagnostics {
    if !response.ok {
        if response
            .error
            .as_deref()
            .is_some_and(|error| error.starts_with("permission denied:"))
        {
            return HelperDiagnostics::failure(
                HelperStatus::PermissionDenied,
                "A verified root helper denied the caller's UID.",
                "Have an administrator approve the intended UID in the root-managed policy; do not run the whole CLI as root.",
                true,
            );
        }
        return HelperDiagnostics::failure(
            HelperStatus::Error,
            "A verified root helper rejected the read-only status request.",
            "Check matching CLI/helper versions and the administrator-managed policy.",
            true,
        );
    }
    let data = &response.data;
    let kernel = data.get("kernel");
    let kernel_available = kernel
        .and_then(|value| value.get("available"))
        .and_then(|value| value.as_bool());
    let trusted_network = data
        .get("external_trusted_network_confirmed")
        .and_then(|value| value.as_bool());
    let valid_identity = data.get("helper").and_then(|value| value.as_str()) == Some("running")
        && data.get("backend").and_then(|value| value.as_str()) == Some("linux-native")
        && data
            .get("native_data_encryption")
            .and_then(|value| value.as_bool())
            == Some(false);
    let sessions = kernel
        .and_then(|value| value.get("sessions"))
        .and_then(|value| {
            let items = value.as_array()?;
            if items.len() > 256 {
                return None;
            }
            serde_json::from_value::<Vec<rustusb_native::NativeSession>>(value.clone()).ok()
        });
    let mut seen = std::collections::HashSet::new();
    let valid_kernel = match kernel_available {
        Some(true) => sessions.as_ref().is_some_and(|items| {
            items
                .iter()
                .all(|item| (5..=7).contains(&item.status) && seen.insert(item.port))
        }),
        Some(false) => true,
        None => false,
    };
    let valid_schema = data.get("schema_version").and_then(|value| value.as_u64())
        == Some(u64::from(crate::STATUS_SCHEMA_VERSION));
    if !valid_identity
        || !valid_schema
        || !valid_kernel
        || trusted_network.is_none()
        || response.error.is_some()
    {
        return HelperDiagnostics::failure(
            HelperStatus::UnexpectedResponse,
            "A root peer answered, but its status schema is incompatible.",
            "Use matching RustUSB CLI/helper versions; no device operation was attempted.",
            true,
        );
    }
    HelperDiagnostics {
        status: HelperStatus::Available,
        detail: "A verified root helper accepted this caller's read-only status query.".into(),
        action:
            "Device/peer authorization and a trusted data network still need separate verification."
                .into(),
        peer_verified: true,
        kernel_available,
        active_sessions: kernel_available
            .filter(|available| *available)
            .and(sessions.as_ref().map(Vec::len)),
        external_trusted_network_confirmed: trusted_network,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn valid_root_status_is_distinct_from_kernel_or_network_readiness() {
        let response = Response {
            ok: true,
            data: serde_json::json!({"schema_version":crate::STATUS_SCHEMA_VERSION,"helper":"running","backend":"linux-native","native_data_encryption":false,"external_trusted_network_confirmed":false,"kernel":{"available":false,"reason":"module unavailable"}}),
            error: None,
        };
        let result = classify_response(response);
        assert_eq!(result.status, HelperStatus::Available);
        assert!(result.peer_verified);
        assert_eq!(result.kernel_available, Some(false));
        assert_eq!(result.active_sessions, None);
        assert_eq!(result.external_trusted_network_confirmed, Some(false));
    }

    #[test]
    fn denied_unknown_and_oversized_status_do_not_become_ready_or_expose_errors() {
        let denied = classify_response(Response {
            ok: false,
            data: serde_json::Value::Null,
            error: Some("permission denied: UID not allowed; SENSITIVE_SENTINEL".into()),
        });
        assert_eq!(denied.status, HelperStatus::PermissionDenied);
        assert!(denied.peer_verified);
        assert!(!denied.detail.contains("SENSITIVE_SENTINEL"));
        for data in [
            serde_json::json!({"helper":"running"}),
            valid_snapshot(vec![session(); 257]),
        ] {
            assert_eq!(
                classify_response(Response {
                    ok: true,
                    data,
                    error: None
                })
                .status,
                HelperStatus::UnexpectedResponse
            );
        }
        assert_eq!(
            classify_error(Error::Permission("SENSITIVE_SENTINEL".into())).status,
            HelperStatus::Untrusted
        );
        assert_eq!(
            classify_error(Error::Timeout("SENSITIVE_SENTINEL".into())).status,
            HelperStatus::Timeout
        );
    }

    fn session() -> serde_json::Value {
        serde_json::json!({"port":0,"status":6,"local_bus_id":"1-1","remote_device_id":65538,"vendor_id":1575,"product_id":1,"local_device_address":2,"local_device_generation":{"filesystem_device":20,"inode":51}})
    }

    fn valid_snapshot(sessions: Vec<serde_json::Value>) -> serde_json::Value {
        serde_json::json!({"schema_version":crate::STATUS_SCHEMA_VERSION,"helper":"running","backend":"linux-native","native_data_encryption":false,"external_trusted_network_confirmed":true,"kernel":{"available":true,"sessions":sessions}})
    }

    #[test]
    fn active_snapshot_requires_current_schema_and_unique_valid_sessions() {
        let data = valid_snapshot(vec![session()]);
        let result = classify_response(Response {
            ok: true,
            data: data.clone(),
            error: None,
        });
        assert_eq!(result.status, HelperStatus::Available);
        assert_eq!(result.active_sessions, Some(1));
        assert_eq!(result.kernel_available, Some(true));
        let mut old_schema = data.clone();
        old_schema
            .as_object_mut()
            .map(|fields| fields.remove("schema_version"));
        let mut invalid_status = session();
        invalid_status["status"] = serde_json::json!(4);
        let mut invalid_port = session();
        invalid_port["port"] = serde_json::json!(256);
        for data in [
            old_schema,
            valid_snapshot(vec![session(), session()]),
            valid_snapshot(vec![invalid_status]),
            valid_snapshot(vec![invalid_port]),
            valid_snapshot(vec![serde_json::json!(null)]),
        ] {
            assert_eq!(
                classify_response(Response {
                    ok: true,
                    data,
                    error: None
                })
                .status,
                HelperStatus::UnexpectedResponse
            );
        }
    }

    #[tokio::test]
    async fn real_insecure_endpoint_is_rejected_without_sending_work() {
        let result = diagnose_helper(Path::new("/tmp/rustusb-doctor-untrusted.sock")).await;
        assert_eq!(result.status, HelperStatus::Untrusted);
        assert!(!result.peer_verified);
    }
}

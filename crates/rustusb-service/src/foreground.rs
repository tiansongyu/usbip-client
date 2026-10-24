//! Typed, bounded adapter boundary for a directly owned foreground job.
//!
//! Delivery acknowledges frontend output only. It never acknowledges a durable
//! application admission. The caller must retain and join [`run`] after stop.

use crate::jobs::LoadedJob;
use rustusb_core::{DeviceKey, Error, Result};
use rustusb_protocol::RemoteDevice;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{net::SocketAddr, path::PathBuf};
use tokio::sync::{mpsc, oneshot, watch};

/// Validated local control identity; never a device capability or path.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct ControlId(String);
impl ControlId {
    /// Require one to sixty-four safe ASCII characters.
    pub fn new(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        crate::config::valid_label(&value)?;
        Ok(Self(value))
    }
    /// Borrow the validated identity.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl TryFrom<String> for ControlId {
    type Error = Error;
    fn try_from(value: String) -> Result<Self> {
        Self::new(value)
    }
}
impl From<ControlId> for String {
    fn from(value: ControlId) -> Self {
        value.0
    }
}

/// Local test controls, deliberately absent from the production agent RPC.
pub enum Control {
    /// Enqueue one virtual HID key; unsupported by physical and Native serving.
    HidKey {
        /// Validated request identity.
        id: ControlId,
        /// USB usage code, revalidated by the virtual backend.
        key: u8,
    },
    /// Enqueue one virtual CDC byte sequence; never echoed to events or audit.
    CdcEmit {
        /// Validated request identity.
        id: ControlId,
        /// At most 2,048 hex characters, checked before decoding.
        hex: String,
    },
    /// Sample the actual current backend snapshot.
    Snapshot {
        /// Validated request identity.
        id: ControlId,
    },
    /// Sample the loaded secure configuration's volatile authentication counters.
    ServerSecurity {
        /// Validated request identity.
        id: ControlId,
    },
    /// Acknowledge then request normal cooperative shutdown.
    Shutdown {
        /// Validated request identity.
        id: ControlId,
    },
    /// A bounded parser/read failure without reflecting the original input.
    Invalid,
    /// The enabled local input actually ended.
    Eof,
}
#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum WireControl {
    HidKey { id: ControlId, key: u8 },
    CdcEmit { id: ControlId, hex: String },
    Snapshot { id: ControlId },
    ServerSecurity { id: ControlId },
    Shutdown { id: ControlId },
}

/// Parse at most 4,096 bytes without leaking serde's input-bearing diagnostics.
pub fn parse_control(bytes: &[u8]) -> Result<Control> {
    if bytes.len() > 4096 {
        return Err(Error::InvalidInput(
            "local control line exceeds 4096 bytes".into(),
        ));
    }
    let value: WireControl = serde_json::from_slice(bytes)
        .map_err(|_| Error::InvalidInput("invalid local control JSON or fields".into()))?;
    Ok(match value {
        WireControl::HidKey { id, key } => Control::HidKey { id, key },
        WireControl::CdcEmit { id, hex } => Control::CdcEmit { id, hex },
        WireControl::Snapshot { id } => Control::Snapshot { id },
        WireControl::ServerSecurity { id } => Control::ServerSecurity { id },
        WireControl::Shutdown { id } => Control::Shutdown { id },
    })
}

/// Published registration metadata, explicitly not live Native configuration.
#[derive(Debug, Clone, Serialize)]
pub struct NativeDeviceSnapshot {
    /// Exact authorized stable key.
    pub device_key: DeviceKey,
    /// Registered pre-export USB/IP metadata.
    pub published_device: RemoteDevice,
    /// Current dispatch eligibility, distinct from kernel occupancy.
    pub accepting_imports: bool,
}
/// Actual backend-specific diagnostic snapshot.
#[derive(Debug, Clone, Serialize)]
#[serde(untagged)]
pub enum Snapshot {
    /// Pre-export Native registration metadata and dispatch eligibility.
    Native {
        /// Fixed scope label from the actual owner.
        metadata_scope: &'static str,
        /// Authorized published devices.
        devices: Vec<NativeDeviceSnapshot>,
    },
    /// Optional virtual engine counters and device state.
    #[cfg(feature = "rust-backend")]
    Virtual(rustusb_rust_backend::BackendSnapshot),
    /// Optional physical engine counters and device state, without payload.
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    Physical(rustusb_rust_backend::PhysicalSnapshot),
}
/// Fixed frontend error categories; original input and USB bytes are absent.
#[derive(Debug, Clone, Copy)]
pub enum ControlError {
    /// Invalid JSON, unknown operation or unsafe identity.
    InvalidControl,
    /// A valid virtual operation was rejected.
    Rejected,
    /// Physical serving accepts only snapshot and shutdown controls.
    UnsupportedPhysical,
}

/// Real owner observations encoded by the CLI using the existing schema-1 layout.
#[derive(Debug, Clone)]
pub enum Event {
    /// Actual VHCI receipt after commit; never reconstructed from a state enum.
    #[cfg(target_os = "linux")]
    Connected {
        /// Configured server alias.
        server_id: String,
        /// Exact remote key.
        device_key: DeviceKey,
        /// Actual full helper/kernel receipt.
        receipt: Box<rustusb_daemon::NativeImportReceipt>,
    },
    /// Client relay and actual helper cleanup completed successfully.
    Disconnected,
    /// Actual listener address and ready backend snapshot.
    Ready {
        /// Bound socket address.
        address: SocketAddr,
        /// Whether mutual TLS is enabled.
        secure: bool,
        /// Actual backend-specific snapshot.
        snapshot: Snapshot,
    },
    /// Actual backend state sampled for a validated local request.
    Snapshot {
        /// Safe local request identity.
        id: ControlId,
        /// Actual snapshot.
        snapshot: Snapshot,
    },
    /// Successful local control, without payload echo.
    Ack {
        /// Safe local request identity.
        id: ControlId,
    },
    /// Loaded TLS configuration counters, separate from backend or cleanup state.
    ServerSecurity {
        /// Safe local request identity.
        id: ControlId,
        /// Volatile metadata shared with this configuration's clones.
        snapshot: rustusb_transport::SecuritySnapshot,
    },
    /// Fixed local error, optionally associated with a safe request identity.
    Error {
        /// Safe local request identity when available.
        id: Option<ControlId>,
        /// Fixed category.
        code: ControlError,
    },
    /// Verified backend stop, returned only after actual cleanup.
    Stopped {
        /// Final actual backend snapshot.
        snapshot: Snapshot,
    },
    /// Verified Native export cleanup, returned only after actual close.
    NativeStopped {
        /// Number of export handles actually closed.
        exports_closed: usize,
    },
}
impl Event {
    /// Preserve the existing frontend JSON shape without exposing control payload.
    pub fn to_json(&self) -> Result<Value> {
        Ok(match self {
            #[cfg(target_os = "linux")]
            Self::Connected {
                server_id,
                device_key,
                receipt,
            } => {
                json!({"schema_version":1,"event":"connected","transport":"mtls","server_id":server_id,"device_key":device_key,"receipt":receipt})
            }
            Self::Disconnected => {
                json!({"schema_version":1,"event":"disconnected","transport":"mtls","cleanup_verified":true})
            }
            Self::Ready {
                address,
                secure,
                snapshot,
            } => {
                let mut value = match snapshot {
                    #[cfg(feature = "rust-backend")]
                    Snapshot::Virtual(snapshot) => {
                        json!({"schema_version":1,"event":"ready","address":address,"devices":snapshot.devices})
                    }
                    _ => {
                        json!({"schema_version":1,"event":"ready","address":address,"snapshot":snapshot})
                    }
                };
                if *secure {
                    value["transport"] = json!("mutual_tls");
                }
                if matches!(snapshot, Snapshot::Native { .. }) {
                    value["backend"] = json!("native");
                }
                value
            }
            Self::Snapshot { id, snapshot } => {
                json!({"schema_version":1,"event":"snapshot","id":id,"snapshot":snapshot})
            }
            Self::Ack { id } => json!({"schema_version":1,"event":"ack","id":id,"ok":true}),
            Self::ServerSecurity { id, snapshot } => {
                json!({"schema_version":1,"event":"server_security","id":id,"snapshot":snapshot})
            }
            Self::Error { id, code } => {
                let code = match code {
                    ControlError::InvalidControl => "invalid_control",
                    ControlError::Rejected => "control_rejected",
                    ControlError::UnsupportedPhysical => "unsupported_physical_control",
                };
                let mut value = json!({"schema_version":1,"event":"error","code":code});
                if let Some(id) = id {
                    value["id"] = json!(id);
                }
                value
            }
            Self::Stopped { snapshot } => {
                json!({"schema_version":1,"event":"stopped","snapshot":snapshot})
            }
            Self::NativeStopped { exports_closed } => {
                json!({"schema_version":1,"event":"stopped","backend":"native","transport":"mutual_tls","exports_closed":exports_closed,"export_cleanup_verified":true})
            }
        })
    }
}

/// One bounded output delivery. Its acknowledgement never represents persistence.
pub struct Delivery {
    /// Actual typed owner event.
    pub event: Event,
    pub(crate) complete: oneshot::Sender<Result<()>>,
}
impl Delivery {
    /// Report the actual frontend write/flush result exactly once.
    pub fn complete(self, result: Result<()>) {
        let _ = self.complete.send(result);
    }
}
/// Frontend side of the bounded channel; owns no device capability.
pub struct Frontend {
    /// Keep alive when stdin is disabled; send Eof only for actual enabled EOF.
    pub controls: mpsc::Sender<Control>,
    /// Receive and acknowledge one output event at a time.
    pub events: mpsc::Receiver<Delivery>,
}
/// Opaque owner-side connection; it cannot be installed in production RPC.
pub struct Connection {
    pub(crate) controls: mpsc::Receiver<Control>,
    pub(crate) events: mpsc::Sender<Delivery>,
}
/// Create fixed-capacity local controls and output delivery channels.
pub fn channel() -> (Frontend, Connection) {
    let (control_tx, control_rx) = mpsc::channel(2);
    let (event_tx, event_rx) = mpsc::channel(2);
    (
        Frontend {
            controls: control_tx,
            events: event_rx,
        },
        Connection {
            controls: control_rx,
            events: event_tx,
        },
    )
}
/// Foreground adapter inputs. All actual ownership remains in the runner.
pub struct ForegroundOptions {
    /// Existing helper endpoint selected by the CLI/deployment administrator.
    pub helper_socket: PathBuf,
    /// Sticky cooperative stop; caller disappearance also stops the owner.
    pub stop: watch::Receiver<bool>,
    /// Typed bounded local connection.
    pub connection: Connection,
}
/// Independent terminal results, produced after the actual owner cleanup tree.
pub struct JobOutcome {
    /// Original operation result, never erased by a cleanup failure.
    pub operation: Result<()>,
    /// Actual cleanup result; Err never becomes a verified final event.
    pub cleanup: Result<()>,
    /// Durable recording result for managed jobs; foreground has no journal.
    pub recording: Result<()>,
    /// Optional final frontend observation, emitted by CLI after joining the owner.
    pub final_event: Option<Event>,
}
impl JobOutcome {
    /// A failure before acquiring any resource; no cleanup is claimed for an attempt.
    pub fn before_start(error: Error) -> Self {
        Self {
            operation: Err(error),
            cleanup: Ok(()),
            recording: Ok(()),
            final_event: None,
        }
    }
    /// Return every terminal result while keeping distinct error contexts.
    pub fn into_result(self) -> Result<()> {
        let mut errors = vec![];
        if let Err(error) = self.operation {
            errors.push(("operation", error));
        }
        if let Err(error) = self.cleanup {
            errors.push(("cleanup unconfirmed", error));
        }
        if let Err(error) = self.recording {
            errors.push(("recording", error));
        }
        if errors.len() == 1 {
            return Err(errors.remove(0).1);
        }
        if errors.is_empty() {
            return Ok(());
        }
        Err(Error::State(
            errors
                .into_iter()
                .map(|(stage, error)| format!("{stage}: {error}"))
                .collect::<Vec<_>>()
                .join("; "),
        ))
    }
}

/// Run one actual owner. Caller must set stop and await this future on cancellation.
pub async fn run(job: LoadedJob, options: ForegroundOptions) -> JobOutcome {
    crate::jobs::run_owned(job, crate::run_context::RunContext::foreground(options)).await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn control_errors_do_not_reflect_payload_and_unknown_fields_fail() -> Result<()> {
        for raw in [
            br#"{"id":"bad\n","op":"snapshot"}"#.as_slice(),
            br#"{"id":"x","op":"snapshot","secret":"never-log-this"}"#,
            br#"{"op":"eof"}"#,
        ] {
            let error = match parse_control(raw) {
                Ok(_) => return Err(Error::State("invalid test control accepted".into())),
                Err(error) => error,
            };
            assert!(!error.to_string().contains("never-log-this"));
        }
        assert!(parse_control(&vec![b'x'; 4097]).is_err());
        assert!(matches!(
            parse_control(br#"{"op":"snapshot","id":"safe-id"}"#)?,
            Control::Snapshot { .. }
        ));
        Ok(())
    }
    #[test]
    fn independent_terminal_errors_preserve_all_original_context() -> Result<()> {
        let outcome = JobOutcome {
            operation: Err(Error::Io(std::io::Error::other("original network failure"))),
            cleanup: Err(Error::State("original kernel cleanup failure".into())),
            recording: Err(Error::State("original journal failure".into())),
            final_event: None,
        };
        let message = match outcome.into_result() {
            Ok(()) => return Err(Error::State("three failures became success".into())),
            Err(error) => error.to_string(),
        };
        for part in [
            "original network failure",
            "original kernel cleanup failure",
            "original journal failure",
        ] {
            assert!(message.contains(part));
        }
        Ok(())
    }
}

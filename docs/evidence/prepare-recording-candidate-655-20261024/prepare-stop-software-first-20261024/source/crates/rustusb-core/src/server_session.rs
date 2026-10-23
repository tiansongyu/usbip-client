//! Payload-free observations of one server-side IMPORT attempt.
//!
//! These facts are not a durable application session manager. IDs are local to
//! one server instance; callers must qualify them with their own job identity.

use crate::{DeviceKey, Error, Result};
use serde::{Deserialize, Serialize};

/// Nonzero identifier local to a single server handle; never an authorization token.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
#[serde(transparent)]
pub struct ServerSessionId(u64);
impl ServerSessionId {
    /// Validate a nonzero local identifier.
    pub fn new(value: u64) -> Result<Self> {
        if value == 0 {
            return Err(Error::InvalidInput(
                "server session ID must be nonzero".into(),
            ));
        }
        Ok(Self(value))
    }
    /// Return the local numeric identifier.
    pub fn get(self) -> u64 {
        self.0
    }
}
impl<'de> Deserialize<'de> for ServerSessionId {
    fn deserialize<D: serde::Deserializer<'de>>(decoder: D) -> std::result::Result<Self, D::Error> {
        Self::new(u64::deserialize(decoder)?).map_err(serde::de::Error::custom)
    }
}

/// Observed lifecycle, with remote enumeration deliberately not inferred.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServerSessionPhase {
    /// Registered, awaiting the application's durable admission decision.
    Preparing,
    /// Admitted and attempting the actual atomic device claim.
    Claiming,
    /// The connection owns a real lease, before the IMPORT reply is flushed.
    Started,
    /// The complete IMPORT reply was flushed; this is not remote enumeration.
    Connected,
    /// Network use has ended and actual resource cleanup is in progress.
    Closing,
    /// Cleanup was verified, or no resource attempt was started.
    Closed,
    /// Completion could not be verified; this never makes a device available.
    CleanupUnconfirmed,
}

/// Actual cleanup evidence, separate from the operation's success or failure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServerCleanup {
    /// No device claim was initiated.
    NotStarted,
    /// This particular attempt's actual cleanup completed successfully.
    Verified,
    /// This attempt's cleanup is not proven.
    Unconfirmed,
}

/// Bounded error categories; never raw certificates, USB bytes or arbitrary error text.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServerSessionError {
    /// The peer or operation was not authorized.
    Permission,
    /// Invalid protocol framing or input.
    Protocol,
    /// An I/O operation failed.
    Io,
    /// A bounded operation exceeded its deadline.
    Timeout,
    /// State or ownership could not be verified.
    State,
    /// The requested operation is unsupported.
    Unsupported,
    /// Cooperative stop was requested.
    Cancelled,
    /// The observing application ended without completing admission/ownership.
    ObserverGone,
    /// The application explicitly rejected the admission request.
    AdmissionRejected,
}

/// IMPORT-after-flush USB/IP application byte observations, not USB payload or TLS wire bytes.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerSessionMetrics {
    /// Bytes actually delivered by successful reads to the application parser/relay.
    pub app_rx_bytes: u64,
    /// Bytes accepted by successful writes, including partial writes before an error.
    pub app_tx_bytes: u64,
    /// At least one counter saturated; values are lower bounds thereafter.
    pub saturated: bool,
    /// Monotonic milliseconds since this local attempt was registered.
    pub elapsed_millis: u64,
}

/// One actual server attempt. Heartbeat and RTT are unavailable for raw USB/IP v1.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerSessionSnapshot {
    /// Identifier scoped to this server instance.
    pub id: ServerSessionId,
    /// Stable requested key, revalidated by the actual claim before Started.
    pub device_key: DeviceKey,
    /// Verified certificate principal; absent only for explicitly unobserved legacy contexts.
    pub principal: Option<String>,
    /// Actual observed phase.
    pub phase: ServerSessionPhase,
    /// Sticky cooperative stop request, not proof of completion.
    pub stop_requested: bool,
    /// Revision of the last lifecycle observation for this session.
    pub revision: u64,
    /// Live byte counters; no raw data is retained.
    pub metrics: ServerSessionMetrics,
    /// Operation failure, independent of cleanup.
    pub operation_error: Option<ServerSessionError>,
    /// Cleanup failure, independent of the original operation.
    pub cleanup_error: Option<ServerSessionError>,
    /// Terminal cleanup evidence, when available.
    pub cleanup: Option<ServerCleanup>,
}

/// An immutable lifecycle observation retained until its terminal is acknowledged.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerSessionEvent {
    /// Monotonic server-local event sequence.
    pub sequence: u64,
    /// Snapshot captured at this lifecycle event, rather than current mutable metrics.
    pub session: ServerSessionSnapshot,
}

/// Atomic registry view; byte counters are sampled separately from lifecycle mutation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerMonitorSnapshot {
    /// Current lifecycle event sequence.
    pub revision: u64,
    /// True while the server is accepting work and its observer remains present.
    pub accepting: bool,
    /// Active attempts and unacknowledged terminal attempts.
    pub sessions: Vec<ServerSessionSnapshot>,
}

/// A bounded page of retained lifecycle events, explicitly reporting acknowledged history gaps.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ServerEventPage {
    /// Last returned event, or the requested cursor for an empty page.
    pub next_after: u64,
    /// Earliest retained event, or the next sequence when there is no history.
    pub oldest_available: u64,
    /// Some events after the supplied cursor have already been acknowledged and removed.
    pub gap: bool,
    /// At most the requested bounded count of immutable events.
    pub events: Vec<ServerSessionEvent>,
}

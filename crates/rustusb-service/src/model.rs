//! Bounded, payload-free application observations shared by every frontend.

use rustusb_core::{DeviceKey, SessionState, application::*};
use serde::{Deserialize, Serialize};

/// The resource role; server acceptance is distinct from client enumeration.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionRole {
    /// An imported local VHCI device.
    Import,
    /// A published device, available for a remote client.
    Export,
    /// One incoming remote IMPORT of a published device.
    Incoming,
}

/// Actual evidence supporting an observation, never inferred from a UI request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Evidence {
    /// Durable admission precedes any resource attempt.
    Intent,
    /// A helper capability was received, before kernel commit.
    Prepared,
    /// The importing kernel returned a complete local-device receipt.
    LocalEnumeration,
    /// Device registration and the listening service are ready.
    ExportReady,
    /// The complete server-side IMPORT reply was flushed.
    ImportReplyFlushed,
    /// The actual owner started cooperative cleanup.
    Closing,
    /// The actual owner verified cleanup, or no resource attempt was made.
    Released,
    /// Cleanup has not been proven.
    Unconfirmed,
    /// An exact retained root journal proof refers to this old attempt.
    RootJournalProof,
}

/// Fixed failure categories. Arbitrary error strings never enter durable audit.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Failure {
    /// OS identity, certificate, device or action authorization failed.
    Permission,
    /// Bounded protocol/configuration input was invalid.
    InvalidInput,
    /// Actual I/O failed.
    Io,
    /// A bounded operation timed out.
    Timeout,
    /// Ownership or cleanup could not be verified.
    State,
    /// The configured implementation is unavailable.
    Unsupported,
    /// A cooperative stop was requested.
    Cancelled,
    /// Durable recording failed; new work is refused.
    Persistence,
}

impl Failure {
    /// Reduce an internal error to a secret-free audit category.
    pub fn from_error(error: &rustusb_core::Error) -> Self {
        match error {
            rustusb_core::Error::Permission(_) => Self::Permission,
            rustusb_core::Error::InvalidInput(_) => Self::InvalidInput,
            rustusb_core::Error::Io(_) => Self::Io,
            rustusb_core::Error::Timeout(_) => Self::Timeout,
            rustusb_core::Error::Unsupported(_) => Self::Unsupported,
            _ => Self::State,
        }
    }
}

/// Cleanup is independent of the original operation result.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Cleanup {
    /// No resource attempt was started.
    NotStarted,
    /// Every resource owned by this session completed its actual cleanup.
    Verified,
    /// At least one resource remains unproven.
    Unconfirmed,
}

/// Sampled USB/IP application bytes, including protocol headers.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Metrics {
    /// Successfully read application bytes after IMPORT.
    pub app_rx_bytes: u64,
    /// Successfully accepted application writes, including partial writes.
    pub app_tx_bytes: u64,
    /// A counter saturated, making its value a lower bound.
    pub saturated: bool,
    /// Monotonic elapsed milliseconds within this attempt.
    pub elapsed_millis: u64,
}

/// One attempt that can be queried against the helper's exact retained proof.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Attempt {
    /// Capability kind; different kinds cannot substitute for one another.
    pub kind: BrokerResourceKind,
    /// Durable identity assigned before the helper request.
    pub correlation: BrokerCorrelation,
    /// Actual helper ID, recorded after receipt; absence does not imply no mutation.
    pub resource_id: Option<ResourceId>,
    /// Exact root retirement evidence, when retained and verified.
    pub proof: Option<BrokerCleanupProof>,
}

/// A durable session observation. Raw-v1 heartbeat and RTT are unavailable.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Session {
    /// Globally unique session ID.
    pub id: SessionId,
    /// Original service epoch, retained over restarts.
    pub instance_id: InstanceId,
    /// Owning job identity.
    pub job_id: JobId,
    /// Administrator-selected profile ID.
    pub profile: String,
    /// Role determining the meaning of Connected.
    pub role: SessionRole,
    /// Stable configured device key; no serial number is stored.
    pub device_key: DeviceKey,
    /// Verified, bounded certificate principal identifier, if observed.
    pub principal: Option<String>,
    /// Last observed nine-state lifecycle value.
    pub state: SessionState,
    /// Evidence supporting that value.
    pub evidence: Evidence,
    /// An accepted stop request, distinct from actual completion.
    pub stop_requested: bool,
    /// Whether the actual owner returned a terminal observation.
    pub terminal: bool,
    /// Actual operation error category, independent of cleanup.
    pub operation_error: Option<Failure>,
    /// Actual cleanup outcome, when known.
    pub cleanup: Option<Cleanup>,
    /// Real byte observations; never a throughput benchmark.
    pub metrics: Metrics,
    /// Every resource attempt, including incomplete helper prepare.
    pub attempts: Vec<Attempt>,
}

/// Owned job lifecycle, separate from any individual device session.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobState {
    /// Durable intent precedes execution.
    Starting,
    /// The supervisor retains the actual running owner task.
    Running,
    /// Cooperative stop was accepted; completion is not implied.
    Stopping,
    /// The task returned and every session has verified/no-start cleanup.
    Finished,
    /// A previous owner epoch ended without complete release proof.
    Recovering,
}

/// Durable metadata for a directly owned task; never an OS subprocess wrapper.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Job {
    /// Opaque job identity.
    pub id: JobId,
    /// Original owner epoch, retained across restarts.
    pub instance_id: InstanceId,
    /// Fixed administrator profile.
    pub profile: String,
    /// Kernel-reported manual operator, or the explicit durable rule's delegated
    /// UID. Automatic starts retain separate RuleOrigin metadata; they do not
    /// claim that an internal scheduler was a new operator socket peer.
    pub operator_uid: u32,
    /// Actual owner lifecycle.
    pub state: JobState,
    /// Sticky cooperative stop request.
    pub stop_requested: bool,
    /// Payload-free original operation failure category.
    pub operation_error: Option<Failure>,
}

/// Fixed audit action names, with no free-form secret-bearing messages.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditAction {
    /// A durable start intent was committed.
    Start,
    /// A local operator's request was denied.
    Denied,
    /// A cooperative stop was requested.
    Stop,
    /// A device session was admitted.
    Admission,
    /// A helper attempt was reserved.
    Attempt,
    /// A received helper resource ID was recorded.
    Resource,
    /// An actual lifecycle observation was recorded.
    Observe,
    /// The real owner reported cleanup completion.
    Finish,
    /// A prior epoch is being reconciled against exact root proof.
    Recovery,
    /// Authenticated read-only remote discovery was requested.
    Discover,
}

/// Minimal bounded durable audit item. It contains no USB payload or raw error.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuditEvent {
    /// Monotonic sequence within this retained store, never reset on restart.
    pub sequence: u64,
    /// Kernel-reported manual operator, explicit rule delegation for automatic
    /// Start/Discover, or the dedicated broker for owner facts.
    pub actor_uid: u32,
    /// Configured profile, absent for an unrecognized/unauthorized request.
    pub profile: Option<String>,
    /// Exact related job, when known.
    pub job_id: Option<JobId>,
    /// Exact related device session, when known.
    pub session_id: Option<SessionId>,
    /// Fixed action category.
    pub action: AuditAction,
    /// Fixed failure category, when relevant.
    pub failure: Option<Failure>,
    /// Actual state at this observation, when a session is involved.
    pub state: Option<SessionState>,
    /// Role-specific evidence accompanying that state.
    pub evidence: Option<Evidence>,
    /// Cleanup result, separate from the operation failure.
    pub cleanup: Option<Cleanup>,
}

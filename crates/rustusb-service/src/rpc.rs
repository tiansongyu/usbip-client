//! Versioned local application protocol. Requests never carry a path, peer or credential.

use crate::{config::Backend, model::*};
use rustusb_core::application::*;
use rustusb_protocol::secure::CatalogDevice;
use serde::{Deserialize, Serialize};

/// A bounded, identity-checked local operation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    /// List profiles this kernel UID may read.
    Profiles,
    /// Read committed rules plus current finite condition metadata independently of WAL.
    Rules {
        /// Fixed profile requiring Read; only own rules (or root's admin view).
        profile: String,
        /// Exclusive rule cursor; at most sixteen rows.
        after: Option<RuleId>,
    },
    /// Commit a rule edit using this real OS peer and a separate mutation UUID.
    MutateRule {
        /// Complete fixed profile effect; no endpoint/key override.
        profile: String,
        /// Original mutation namespace identity, retained across unknown responses.
        request_id: RequestId,
        /// Closed bounded mutation with exact revision CAS where applicable.
        mutation: crate::automation::RuleMutation,
    },
    /// Query only the original mutation result; an unknown ID never edits a rule.
    RetryRuleMutation {
        /// Same original fixed profile.
        profile: String,
        /// Same original mutation identity.
        request_id: RequestId,
    },
    /// Read independently sequenced committed rule audit, requiring Audit.
    RuleEvents {
        /// Fixed profile; no filesystem path is accepted.
        profile: String,
        /// Exclusive automation audit sequence, not the legacy audit cursor.
        after: u64,
    },
    /// Start one fixed profile; request IDs deduplicate only within retained history.
    Start {
        /// Fixed profile ID.
        profile: String,
        /// Opaque retry identity; never a bearer authorization token.
        request_id: RequestId,
    },
    /// Retry only a retained start; an expired/unknown identity never starts work.
    RetryStart {
        /// Fixed authorized profile.
        profile: String,
        /// Exact original request identity.
        request_id: RequestId,
    },
    /// Request cooperative stop of one owned job.
    StopJob {
        /// Exact job ID.
        job_id: JobId,
    },
    /// Request cooperative stop of one device session, preserving other sessions.
    StopSession {
        /// Exact session ID.
        session_id: SessionId,
    },
    /// List up to sixteen jobs in a fixed authorized profile.
    Jobs {
        /// Fixed profile ID.
        profile: String,
        /// Exclusive opaque cursor; omitted for the first page.
        after: Option<JobId>,
    },
    /// List a bounded byte-limited page of session observations.
    Sessions {
        /// Fixed profile ID.
        profile: String,
        /// Exclusive opaque cursor; omitted for the first page.
        after: Option<SessionId>,
    },
    /// Read live or explicitly historical health of actual data connections.
    SessionHealth {
        /// Exact profile, authorized independently using the Read action.
        profile: String,
        /// Exclusive session cursor; at most sixteen observations are returned.
        after: Option<SessionId>,
    },
    /// Read volatile counters of one fixed loaded server configuration.
    ServerSecurity {
        /// Exact profile requiring the existing Read action.
        profile: String,
    },
    /// Read immutable authentication quota and volatile admission decisions.
    ServerAdmission {
        /// Exact server profile requiring Read; never a quota override.
        profile: String,
    },
    /// Read lifecycle observations from the bounded retained event window.
    Events {
        /// Fixed profile ID.
        profile: String,
        /// Exclusive monotonic cursor.
        after: u64,
    },
    /// Read minimal audit entries using a separately authorized action.
    Audit {
        /// Fixed profile ID.
        profile: String,
        /// Exclusive monotonic cursor.
        after: u64,
    },
    /// Read projected process-log audit records independently of the business WAL.
    ReadLogs {
        /// Exact configured profile, requiring Audit rather than Read permission.
        profile: String,
        /// Complete-line continuation only; never authorization or a filesystem path.
        after: Option<rustusb_logging::Cursor>,
        /// Between one and sixteen records; scanned bytes are independently bounded.
        limit: u8,
    },
    /// Pin a coherent committed view; both Read and Audit are required.
    BeginDiagnostic {
        /// Exact configured profile; no path or credential is accepted.
        profile: String,
    },
    /// Read one finite page from the caller's original pinned snapshot.
    DiagnosticPage {
        /// Exact profile, reauthorized on every page.
        profile: String,
        /// Opaque identity bound to the actual caller UID and profile.
        snapshot: crate::diagnostics::DiagnosticToken,
        /// Fixed typed section.
        kind: crate::diagnostics::DiagnosticKind,
        /// Number of filtered rows already consumed; zero starts a section.
        after: u16,
    },
    /// Idempotently release only this caller's matching metadata snapshot.
    EndDiagnostic {
        /// Exact profile, independently requiring Read and Audit.
        profile: String,
        /// Unknown, foreign or expired tokens have the same no-effect reply.
        snapshot: crate::diagnostics::DiagnosticToken,
    },
    /// Read the authenticated catalog of a fixed client profile.
    Discover {
        /// Fixed client profile ID; endpoint/device credentials cannot be overridden.
        profile: String,
    },
}

/// Version envelope; incompatible versions fail before action dispatch.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RequestEnvelope {
    /// Exactly one.
    pub schema_version: u32,
    /// Typed bounded operation.
    pub request: Request,
}

/// Public profile metadata; credential paths and private buffers are omitted.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ProfileInfo {
    /// Fixed administrator-chosen ID.
    pub id: String,
    /// Fixed implementation selection.
    pub backend: Backend,
}

/// Fixed logging-only failures, independent of business journal health or cleanup.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LogReadFailure {
    /// Invalid bounded cursor or page input.
    InvalidInput,
    /// The private log store failed its filesystem trust checks.
    Permission,
    /// Preserved log history is malformed or ambiguous.
    Corrupt,
    /// Actual log filesystem I/O failed.
    Io,
    /// The finite actual reader and queue capacity is occupied.
    Busy,
    /// No file logging owner is available.
    Disabled,
    /// Checked logging is unavailable on this platform.
    Unsupported,
    /// The actual log worker failed.
    Worker,
    /// Logging lost records; business work may nevertheless have succeeded.
    LostRecords,
}

impl From<&rustusb_logging::Error> for LogReadFailure {
    fn from(error: &rustusb_logging::Error) -> Self {
        match error {
            rustusb_logging::Error::InvalidInput(_) => Self::InvalidInput,
            rustusb_logging::Error::Permission(_) => Self::Permission,
            rustusb_logging::Error::Corrupt(_) => Self::Corrupt,
            rustusb_logging::Error::Io(_) => Self::Io,
            rustusb_logging::Error::Busy => Self::Busy,
            rustusb_logging::Error::Disabled => Self::Disabled,
            rustusb_logging::Error::Unsupported => Self::Unsupported,
            rustusb_logging::Error::Worker(_) => Self::Worker,
            rustusb_logging::Error::LostRecords => Self::LostRecords,
        }
    }
}

/// A payload-free bounded local result.
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "result", rename_all = "snake_case", deny_unknown_fields)]
pub enum Response {
    /// Finite current rule metadata; no USB owner or authorization is transferred.
    Rules {
        /// Whether an explicit startup automation policy was loaded.
        policy_enabled: bool,
        /// This requesting UID's exact grant metadata, if any.
        #[serde(deserialize_with = "crate::automation::required_option")]
        grant: Option<crate::automation::AutomationGrantInfo>,
        /// At most sixteen authorized complete rows.
        rules: Vec<crate::automation::RuleView>,
        /// Exclusive continuation or explicit null after the last row.
        #[serde(deserialize_with = "crate::automation::required_option")]
        next_after: Option<RuleId>,
    },
    /// The original edit committed; later rule state may have advanced.
    RuleMutationApplied {
        /// Exact original mutation namespace identity.
        request_id: RequestId,
        /// Immutable result of that edit, not a current-job release assertion.
        outcome: crate::automation::RuleMutationOutcome,
        /// The original retained result was read without reapplying an edit.
        replayed: bool,
    },
    /// No retained mapping; no new edit performed and no late original excluded.
    RuleMutationExpired {
        /// Exact unknown/expired original mutation identity.
        request_id: RequestId,
    },
    /// Finite independently sequenced rule audit, outside the old audit schema.
    RuleEvents {
        /// At most sixteen complete committed events.
        events: Vec<crate::automation::RuleEvent>,
        /// Next exclusive automation cursor.
        next_after: u64,
        /// Earliest retained automation sequence, or current head plus one.
        oldest_available: u64,
        /// Requested automation history was evicted.
        gap: bool,
    },
    /// Readable fixed profiles.
    Profiles {
        /// At most sixteen entries.
        profiles: Vec<ProfileInfo>,
    },
    /// A durable start intent, independent of the requesting connection.
    Started {
        /// Actual owned job metadata.
        job: Job,
        /// True when the same authorized request identity returned its original job.
        replayed: bool,
    },
    /// The safe retry cannot be resolved within retained history; no work started.
    RequestExpired {
        /// Unknown or expired original request identity.
        request_id: RequestId,
    },
    /// Cooperative stop accepted; this does not prove device release.
    StopAccepted {
        /// False when no live owner exists; historical state is still queryable.
        owner_present: bool,
    },
    /// Bounded job page.
    Jobs {
        /// At most sixteen entries.
        jobs: Vec<Job>,
        /// Last returned ID when additional records remain.
        next_after: Option<JobId>,
    },
    /// Bounded session page.
    Sessions {
        /// At most sixteen entries and at most the protocol byte budget.
        sessions: Vec<Session>,
        /// Last returned ID when additional records remain.
        next_after: Option<SessionId>,
    },
    /// Nonowning data-link observations, separate from USB cleanup proof.
    SessionHealth {
        /// At most sixteen observations within the existing response byte budget.
        sessions: Vec<crate::health::SessionHealth>,
        /// Last session returned when additional records remain.
        next_after: Option<SessionId>,
    },
    /// Current loaded TLS configuration observations, retained across job restarts.
    ServerSecurity {
        /// Actual service epoch; counters are not recovered from previous epochs.
        instance_id: InstanceId,
        /// Volatile metadata; never an admission, durable audit or cleanup proof.
        snapshot: rustusb_transport::SecuritySnapshot,
    },
    /// Separate pre-TLS decisions; existing TLS observation fields are unchanged.
    ServerAdmission {
        /// Actual service epoch; no previous-epoch quota is recovered.
        instance_id: InstanceId,
        /// Loaded-configuration policy and bounded metadata-only decision totals.
        snapshot: rustusb_transport::AdmissionSnapshot,
    },
    /// Bounded observations with explicit history expiry.
    Events {
        /// At most sixteen entries; raw USB and raw errors are absent.
        events: Vec<AuditEvent>,
        /// Cursor for the next request.
        next_after: u64,
        /// Earliest globally retained audit sequence.
        oldest_available: u64,
        /// The requested cursor predates retained history.
        gap: bool,
    },
    /// Authorized typed audit projection; never a business acknowledgement.
    Logs {
        /// Bounded records, scan continuation and independent logger status.
        page: rustusb_logging::LogPage,
    },
    /// An authorized log read failed without changing business journal health.
    LogError {
        /// Fixed logger category, never an internal error string or path.
        code: LogReadFailure,
    },
    /// A bounded snapshot of already committed profile metadata was pinned.
    DiagnosticStarted {
        /// Original committed sequence, counts and finite snapshot identity.
        header: crate::diagnostics::DiagnosticHeader,
    },
    /// One complete typed page of the original pinned snapshot.
    DiagnosticPage {
        /// Finite authorized records and an explicit continuation offset.
        page: crate::diagnostics::DiagnosticPage,
    },
    /// Idempotent metadata-only release; token existence is not revealed.
    DiagnosticEnded {},
    /// Authorized observation failed without changing USB or WAL ownership.
    DiagnosticError {
        /// Fixed category, never an internal path or error string.
        code: crate::diagnostics::DiagnosticFailure,
    },
    /// Authenticated catalog; not proof a listed device successfully redirects.
    Catalog {
        /// Bounded remote metadata.
        devices: Vec<CatalogDevice>,
    },
    /// Fixed secret-free failure category.
    Error {
        /// No arbitrary internal error string is exposed.
        code: Failure,
    },
}

/// Every response states whether durable recording is healthy.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResponseEnvelope {
    /// Exactly one.
    pub schema_version: u32,
    /// False refuses new admission while retaining actual owner cleanup.
    pub journal_healthy: bool,
    /// Operation result.
    pub response: Response,
}

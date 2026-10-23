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

/// A payload-free bounded local result.
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "result", rename_all = "snake_case", deny_unknown_fields)]
pub enum Response {
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

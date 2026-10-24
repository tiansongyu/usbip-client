//! Closed privacy projection. Raw observation values never implement this format.

use crate::{MAX_EXPORT_BYTES, native_probe::NativeObservations};
use rustusb_core::{Error, Result, SessionState, application::*, link_health::*};
use rustusb_daemon::diagnostics::HelperStatus;
use rustusb_logging as logs;
use rustusb_native::diagnostics::DiagnosticStatus;
use serde::Serialize;
use std::{collections::BTreeMap, io::Write};

#[cfg(feature = "secure-transport")]
use std::collections::BTreeSet;
#[cfg(feature = "secure-transport")]
mod agent;
#[cfg(test)]
mod tests;

/// Outcome of the requested capture, independent from every USB operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CaptureOutcome {
    /// Every requested section was collected without a recorded limitation.
    Complete,
    /// Requested information was missing, limited or could not be collected.
    Partial,
    /// The caller stopped collection; accepted actual work was still joined.
    Cancelled,
}
/// Fixed independently sampled section of a diagnostic document.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Stage {
    /// Local fixed Linux Native prerequisites.
    Native,
    /// Authenticated local helper observation.
    Helper,
    /// One pinned, committed application view.
    Agent,
    /// Independently sampled data-link health.
    Health,
    /// Bounded projected process-log history.
    Logs,
}
/// Completion of one requested section, not readiness of the diagnosed system.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SectionOutcome {
    /// This section was not selected for collection.
    NotRequested,
    /// Requested data was collected within its stated limits.
    Complete,
    /// Some requested data is missing, failed or limited.
    Incomplete,
    /// Collection stopped before this section completed.
    Cancelled,
}
/// Fixed collection failures; arbitrary error messages never enter the artifact.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CollectionFailure {
    /// Bounded input failed validation.
    InvalidInput,
    /// Identity or filesystem authorization was denied.
    Permission,
    /// An actual I/O operation failed.
    Io,
    /// A bounded observation timed out.
    Timeout,
    /// Requested observations are unavailable.
    Unavailable,
    /// A pinned view or requested history expired.
    Expired,
    /// Finite actual worker or view capacity was busy.
    Busy,
    /// A row, page, alias or byte budget was exhausted.
    Limit,
    /// Source observations were malformed or inconsistent.
    Corrupt,
    /// The requested facility is disabled.
    Disabled,
    /// An actual observer worker failed.
    Worker,
    /// Completion or retained information could not be confirmed.
    Unconfirmed,
    /// The platform or selected build lacks the facility.
    Unsupported,
    /// A fixed protocol contract was rejected.
    Protocol,
    /// Source state prevents a confirmed observation.
    State,
    /// The source business journal reported persistence failure.
    Persistence,
}
/// Public receipt summary of a fixed section.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct SectionSummary {
    /// Section whose collection was requested.
    pub stage: Stage,
    /// Actual collection outcome.
    pub outcome: SectionOutcome,
    /// Closed collection error, not the USB operation error.
    pub failure: Option<CollectionFailure>,
    /// Local capture-relative monotonic observation time.
    pub observed_at_millis: Option<u64>,
}
/// Bounded receipt summary without original identities, paths or source text.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Summary {
    /// All requested sections complete with no sticky degradation.
    pub complete: bool,
    /// Fixed Native, helper, application, health, logs order.
    pub sections: [SectionSummary; 5],
}

const MAX_ALIASES: usize = 8192;
const MAX_LOGS: usize = 512;
const MAX_PAGES: usize = 32;
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
// The artifact shape is stable when the secure producer is not compiled.
#[cfg_attr(not(feature = "secure-transport"), allow(dead_code))]
enum Namespace {
    ApplicationEpoch,
    ProcessEpoch,
    Job,
    Session,
    Attempt,
    Resource,
    Device,
    Principal,
    Profile,
    Uid,
    Connection,
}
impl Namespace {
    fn index(self) -> usize {
        self as usize
    }
}
// This lookup is deliberately neither Debug nor Serialize. Only generated
// per-namespace integers are placed in projected records.
#[derive(Default)]
struct Aliases {
    values: BTreeMap<(Namespace, String), u16>,
    counts: [u16; 11],
}
impl Aliases {
    fn get(&mut self, kind: Namespace, value: &str) -> Result<u16> {
        if value.is_empty() || value.len() > 256 {
            return Err(invalid());
        }
        let key = (kind, value.to_owned());
        if let Some(id) = self.values.get(&key) {
            return Ok(*id);
        }
        if self.values.len() >= MAX_ALIASES {
            return Err(limit());
        }
        let next = self.counts[kind.index()].checked_add(1).ok_or_else(limit)?;
        self.values.insert(key, next);
        self.counts[kind.index()] = next;
        Ok(next)
    }
    fn uid(&mut self, uid: u32) -> Result<u16> {
        self.get(Namespace::Uid, &uid.to_string())
    }
    fn optional(&mut self, kind: Namespace, value: Option<&str>) -> Result<Option<u16>> {
        value.map(|v| self.get(kind, v)).transpose()
    }
}

#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum Os {
    Linux,
    Windows,
    Macos,
    Other,
}
#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum Architecture {
    X86_64,
    Aarch64,
    X86,
    Arm,
    Other,
}
#[derive(Serialize)]
struct NumericVersion {
    major: u32,
    minor: u32,
    patch: Option<u32>,
}
#[derive(Serialize)]
struct ModuleObservation {
    loaded: DiagnosticStatus,
    sysfs: DiagnosticStatus,
    availability: DiagnosticStatus,
}
#[derive(Serialize)]
struct NativeObservation {
    os: Os,
    architecture: Architecture,
    kernel: DiagnosticStatus,
    kernel_numeric: Option<NumericVersion>,
    usbip_host: ModuleObservation,
    vhci_hcd: ModuleObservation,
    usbip: DiagnosticStatus,
    usbipd: DiagnosticStatus,
}
#[derive(Serialize)]
struct HelperObservation {
    status: HelperStatus,
    peer_verified: bool,
    kernel_available: Option<bool>,
    active_sessions: Option<usize>,
    external_trusted_network_policy_statement: Option<bool>,
}
#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
// The artifact shape is stable when the secure producer is not compiled.
#[cfg_attr(not(feature = "secure-transport"), allow(dead_code))]
enum Backend {
    Client,
    Native,
    Virtual,
    Usbfs,
}
#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
// The artifact shape is stable when the secure producer is not compiled.
#[cfg_attr(not(feature = "secure-transport"), allow(dead_code))]
enum JobPhase {
    Starting,
    Running,
    Stopping,
    Finished,
    Recovering,
}
#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
// The artifact shape is stable when the secure producer is not compiled.
#[cfg_attr(not(feature = "secure-transport"), allow(dead_code))]
enum Role {
    Import,
    Export,
    Incoming,
}
#[derive(Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
// The artifact shape is stable when the secure producer is not compiled.
#[cfg_attr(not(feature = "secure-transport"), allow(dead_code))]
enum Availability {
    Live,
    PreviousEpoch,
    OwnerEnded,
    NotObserved,
    NotDataSession,
}
#[derive(Serialize)]
struct Job {
    job: u16,
    application_epoch: u16,
    profile: u16,
    operator: u16,
    state: JobPhase,
    stop_requested: bool,
    operation_error: Option<logs::FailureCategory>,
}
#[derive(Serialize)]
struct Attempt {
    kind: BrokerResourceKind,
    application_epoch: u16,
    session: u16,
    attempt: u16,
    resource: Option<u16>,
    proof: Option<BrokerCleanupProof>,
}
#[derive(Serialize)]
struct Metrics {
    app_rx_bytes: u64,
    app_tx_bytes: u64,
    saturated: bool,
    elapsed_millis: u64,
}
#[derive(Serialize)]
struct Session {
    session: u16,
    application_epoch: u16,
    job: u16,
    profile: u16,
    role: Role,
    device: u16,
    principal: Option<u16>,
    state: SessionState,
    evidence: logs::Evidence,
    stop_requested: bool,
    terminal: bool,
    operation_error: Option<logs::FailureCategory>,
    cleanup: Option<logs::Cleanup>,
    metrics: Metrics,
    attempts: Vec<Attempt>,
}
#[derive(Serialize)]
struct Audit {
    application_epoch: Option<u16>,
    sequence: u64,
    actor: u16,
    profile: Option<u16>,
    job: Option<u16>,
    session: Option<u16>,
    action: logs::AuditAction,
    failure: Option<logs::FailureCategory>,
    state: Option<SessionState>,
    evidence: Option<logs::Evidence>,
    cleanup: Option<logs::Cleanup>,
}
#[derive(Serialize)]
struct AgentScope {
    profile: u16,
    caller: u16,
}
#[derive(Serialize)]
struct Agent {
    application_epoch: u16,
    profile: u16,
    caller: u16,
    backend: Backend,
    committed_sequence: u64,
    publication_age_millis: u64,
    audit_oldest_retained_sequence: u64,
    expires_after_millis: u64,
    expected_jobs: u16,
    expected_sessions: u16,
    expected_audit: u16,
    jobs: Vec<Job>,
    sessions: Vec<Session>,
    audit: Vec<Audit>,
}
#[derive(Serialize)]
struct HealthSample {
    session: u16,
    application_epoch: u16,
    role: Role,
    availability: Availability,
    not_in_committed_view: bool,
    observed_at_millis: u64,
    link: Option<Link>,
}
#[derive(Serialize)]
struct Link {
    protocol: LinkProtocol,
    connection: Option<u16>,
    sample_sequence: u64,
    elapsed_millis: u64,
    phase: LinkPhase,
    probe: ProbeState,
    last_probe_sequence: Option<u64>,
    last_probe_enqueued_millis: Option<u64>,
    last_reply_sequence: Option<u64>,
    last_reply_millis: Option<u64>,
    last_round_trip_micros: Option<u64>,
    stats: Option<LinkStats>,
    end: Option<LinkEnd>,
}
#[derive(Default, Serialize)]
struct Health {
    pages: u32,
    samples: Vec<HealthSample>,
}
#[derive(Serialize)]
struct LogStatus {
    healthy: bool,
    closing: bool,
    queued: u32,
    accepted: u64,
    written: u64,
    dropped: u64,
    oversized: u64,
    projection_errors: u64,
    overflowed: bool,
    synced_boundary_observed: bool,
    failure: Option<logs::FailureCategory>,
}
impl From<&logs::LogStatus> for LogStatus {
    fn from(s: &logs::LogStatus) -> Self {
        Self {
            healthy: s.healthy,
            closing: s.closing,
            queued: s.queued,
            accepted: s.accepted,
            written: s.written,
            dropped: s.dropped,
            oversized: s.oversized,
            projection_errors: s.projection_errors,
            overflowed: s.overflowed,
            synced_boundary_observed: s.last_written.is_some(),
            failure: s.failure,
        }
    }
}
#[derive(Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
enum LogEvent {
    Started {
        owner: u16,
    },
    Diagnostic {
        code: logs::EventCode,
        line: Option<u32>,
        failure: Option<logs::FailureCategory>,
        errno: Option<i32>,
        fields: BTreeMap<logs::Field, logs::Scalar>,
        omitted_fields: u32,
    },
    Audit(Audit),
    Stopped {
        status: LogStatus,
    },
}
#[derive(Serialize)]
struct LogRecord {
    process_epoch: u16,
    sequence: u64,
    elapsed_millis: u64,
    level: logs::Level,
    event: LogEvent,
}
#[derive(Serialize)]
#[serde(rename_all = "snake_case")]
enum LiveStatusAvailability {
    Observed,
    Unavailable,
}
#[derive(Serialize)]
struct LogPageObservation {
    observed_at_millis: u64,
    source_records: usize,
    exported_records: usize,
    privacy_filtered_records: usize,
    scanned_bytes: u32,
    gap: bool,
    partial_tail: bool,
    more: bool,
    continuation_observed: bool,
    oldest_retained_boundary_observed: bool,
    live_status_availability: LiveStatusAvailability,
    live_status: Option<LogStatus>,
}
#[derive(Default, Serialize)]
struct Logs {
    pages: Vec<LogPageObservation>,
    records: Vec<LogRecord>,
}

pub(crate) struct Builder {
    sections: [SectionSummary; 5],
    sticky: [Option<CollectionFailure>; 5],
    aliases: Aliases,
    native: Option<NativeObservation>,
    helper: Option<HelperObservation>,
    agent: Option<Agent>,
    health: Health,
    logs: Logs,
    agent_scope: Option<(String, u32)>,
    requested_agent_scope: Option<AgentScope>,
    #[cfg(feature = "secure-transport")]
    job_ids: BTreeSet<String>,
    #[cfg(feature = "secure-transport")]
    session_ids: BTreeSet<String>,
    #[cfg(feature = "secure-transport")]
    audit_sequences: BTreeSet<u64>,
    #[cfg(feature = "secure-transport")]
    health_ids: BTreeSet<String>,
    #[cfg(feature = "secure-transport")]
    session_epochs: BTreeMap<String, String>,
    log_sequences: BTreeMap<String, u64>,
    #[cfg(feature = "secure-transport")]
    item_pages: [usize; 3],
    log_source_records: usize,
}
impl Stage {
    fn index(self) -> usize {
        self as usize
    }
}
impl Builder {
    pub(crate) fn new() -> Self {
        let stages = [
            Stage::Native,
            Stage::Helper,
            Stage::Agent,
            Stage::Health,
            Stage::Logs,
        ];
        Self {
            sections: stages.map(|stage| SectionSummary {
                stage,
                outcome: SectionOutcome::NotRequested,
                failure: None,
                observed_at_millis: None,
            }),
            sticky: [None; 5],
            aliases: Aliases::default(),
            native: None,
            helper: None,
            agent: None,
            health: Health::default(),
            logs: Logs::default(),
            agent_scope: None,
            requested_agent_scope: None,
            #[cfg(feature = "secure-transport")]
            job_ids: BTreeSet::new(),
            #[cfg(feature = "secure-transport")]
            session_ids: BTreeSet::new(),
            #[cfg(feature = "secure-transport")]
            audit_sequences: BTreeSet::new(),
            #[cfg(feature = "secure-transport")]
            health_ids: BTreeSet::new(),
            #[cfg(feature = "secure-transport")]
            session_epochs: BTreeMap::new(),
            log_sequences: BTreeMap::new(),
            #[cfg(feature = "secure-transport")]
            item_pages: [0; 3],
            log_source_records: 0,
        }
    }
    pub(crate) fn set_outcome(
        &mut self,
        stage: Stage,
        outcome: SectionOutcome,
        failure: Option<CollectionFailure>,
        at: u64,
    ) {
        let sticky = self.sticky[stage.index()];
        self.sections[stage.index()] = SectionSummary {
            stage,
            outcome: if sticky.is_some() && outcome != SectionOutcome::Cancelled {
                SectionOutcome::Incomplete
            } else {
                outcome
            },
            failure: sticky.or(failure),
            observed_at_millis: Some(at),
        };
    }
    fn degrade(&mut self, stage: Stage, failure: CollectionFailure) {
        let i = stage.index();
        self.sticky[i].get_or_insert(failure);
        self.sections[i].outcome = SectionOutcome::Incomplete;
        self.sections[i].failure = self.sticky[i];
    }
    pub(crate) fn observe_local(&mut self, value: NativeObservations, at: u64) {
        match value.native {
            Ok(n) => {
                let module = |m: rustusb_native::diagnostics::KernelDiagnostic| ModuleObservation {
                    loaded: m.loaded.status,
                    sysfs: m.sysfs.status,
                    availability: m.availability.status,
                };
                self.native = Some(NativeObservation {
                    os: match n.system.os.as_str() {
                        "linux" => Os::Linux,
                        "windows" => Os::Windows,
                        "macos" => Os::Macos,
                        _ => Os::Other,
                    },
                    architecture: match n.system.architecture.as_str() {
                        "x86_64" => Architecture::X86_64,
                        "aarch64" => Architecture::Aarch64,
                        "x86" => Architecture::X86,
                        "arm" => Architecture::Arm,
                        _ => Architecture::Other,
                    },
                    kernel: n.system.kernel.status,
                    kernel_numeric: n.system.kernel_release.as_deref().and_then(numeric_version),
                    usbip_host: module(n.usbip_host),
                    vhci_hcd: module(n.vhci_hcd),
                    usbip: n.usbip.check.status,
                    usbipd: n.usbipd.check.status,
                });
                self.set_outcome(Stage::Native, SectionOutcome::Complete, None, at);
            }
            Err(_) => {
                self.degrade(Stage::Native, CollectionFailure::Unconfirmed);
                self.set_outcome(
                    Stage::Native,
                    SectionOutcome::Incomplete,
                    Some(CollectionFailure::Unconfirmed),
                    at,
                );
            }
        }
        let h = value.helper;
        if h.active_sessions.is_some_and(|n| n > 256) {
            self.degrade(Stage::Helper, CollectionFailure::Corrupt);
            self.set_outcome(
                Stage::Helper,
                SectionOutcome::Incomplete,
                Some(CollectionFailure::Corrupt),
                at,
            );
        } else {
            self.helper = Some(HelperObservation {
                status: h.status,
                peer_verified: h.peer_verified,
                kernel_available: h.kernel_available,
                active_sessions: h.active_sessions,
                external_trusted_network_policy_statement: h.external_trusted_network_confirmed,
            });
            self.set_outcome(Stage::Helper, SectionOutcome::Complete, None, at);
        }
    }
    pub(crate) fn summary(&self) -> Summary {
        let mut sections = self.sections;
        if self.logs.pages.last().is_some_and(|page| page.more)
            && sections[Stage::Logs.index()].outcome == SectionOutcome::Complete
        {
            sections[Stage::Logs.index()].outcome = SectionOutcome::Incomplete;
            sections[Stage::Logs.index()].failure = Some(CollectionFailure::Limit);
        }
        if let Some(a) = &self.agent {
            if (a.jobs.len(), a.sessions.len(), a.audit.len())
                != (
                    usize::from(a.expected_jobs),
                    usize::from(a.expected_sessions),
                    usize::from(a.expected_audit),
                )
                && sections[Stage::Agent.index()].outcome == SectionOutcome::Complete
            {
                sections[Stage::Agent.index()].outcome = SectionOutcome::Incomplete;
                sections[Stage::Agent.index()].failure = Some(CollectionFailure::Unconfirmed);
            }
            if sections[Stage::Agent.index()].outcome == SectionOutcome::Complete
                && a.sessions.iter().any(|session| {
                    !a.jobs.iter().any(|job| {
                        job.job == session.job
                            && job.application_epoch == session.application_epoch
                            && job.profile == session.profile
                    })
                })
            {
                sections[Stage::Agent.index()].outcome = SectionOutcome::Incomplete;
                sections[Stage::Agent.index()].failure = Some(CollectionFailure::Corrupt);
            }
        }
        Summary {
            complete: sections.iter().all(|s| {
                matches!(
                    s.outcome,
                    SectionOutcome::NotRequested | SectionOutcome::Complete
                ) && s.failure.is_none()
            }),
            sections,
        }
    }
    pub(crate) fn observe_logs(&mut self, page: &logs::LogPage, at: u64) -> Result<()> {
        let result = self.logs_page(page, at);
        if let Err(e) = &result {
            self.degrade(Stage::Logs, collection_error(e));
        }
        result
    }
    fn logs_page(&mut self, page: &logs::LogPage, at: u64) -> Result<()> {
        if page.schema_version != 1
            || page.records.len() > 16
            || page.scanned_bytes > 256 * 1024
            || page.more && page.next_after.is_none()
        {
            return Err(invalid());
        }
        if self.logs.pages.len() >= MAX_PAGES
            || self.log_source_records + page.records.len() > MAX_LOGS
        {
            return Err(limit());
        }
        self.log_source_records += page.records.len();
        if page.gap {
            self.degrade(Stage::Logs, CollectionFailure::Limit);
        }
        if page.partial_tail {
            self.degrade(Stage::Logs, CollectionFailure::Corrupt);
        }
        if let Some(s) = &page.status {
            self.inspect_log_status(s)?;
        }
        let before = self.logs.records.len();
        for record in &page.records {
            record.validate().map_err(|_| invalid())?;
            let seq = self
                .log_sequences
                .entry(record.epoch.as_str().to_owned())
                .or_default();
            if record.sequence <= *seq {
                return Err(invalid());
            }
            *seq = record.sequence;
            if !self.log_visible(record) {
                continue;
            }
            let epoch = self
                .aliases
                .get(Namespace::ProcessEpoch, record.epoch.as_str())?;
            let event = match &record.event {
                logs::Event::Started { uid, .. } => LogEvent::Started {
                    owner: self.aliases.uid(*uid)?,
                },
                logs::Event::Diagnostic(d) => LogEvent::Diagnostic {
                    code: d.code,
                    line: d.line,
                    failure: d.failure,
                    errno: d.errno,
                    fields: d.fields.clone(),
                    omitted_fields: d.omitted_fields,
                },
                logs::Event::Audit(a) => LogEvent::Audit(self.project_log_audit(a)?),
                logs::Event::Stopped { status } => {
                    self.inspect_log_status(status)?;
                    LogEvent::Stopped {
                        status: status.into(),
                    }
                }
            };
            self.logs.records.push(LogRecord {
                process_epoch: epoch,
                sequence: record.sequence,
                elapsed_millis: record.elapsed_millis,
                level: record.level,
                event,
            });
        }
        let exported = self.logs.records.len() - before;
        self.logs.pages.push(LogPageObservation {
            observed_at_millis: at,
            source_records: page.records.len(),
            exported_records: exported,
            privacy_filtered_records: page.records.len() - exported,
            scanned_bytes: page.scanned_bytes,
            gap: page.gap,
            partial_tail: page.partial_tail,
            more: page.more,
            continuation_observed: page.next_after.is_some(),
            oldest_retained_boundary_observed: page.oldest_available.is_some(),
            live_status_availability: if page.status.is_some() {
                LiveStatusAvailability::Observed
            } else {
                LiveStatusAvailability::Unavailable
            },
            live_status: page.status.as_ref().map(LogStatus::from),
        });
        if self.logs.pages.len() == MAX_PAGES && page.more {
            self.degrade(Stage::Logs, CollectionFailure::Limit);
        }
        Ok(())
    }
    fn inspect_log_status(&mut self, s: &logs::LogStatus) -> Result<()> {
        if s.queued > 256 || s.written > s.accepted {
            return Err(invalid());
        }
        let loss = s.dropped > 0
            || s.oversized > 0
            || s.projection_errors > 0
            || s.overflowed
            || s.failure.is_some();
        if s.healthy && loss {
            return Err(invalid());
        }
        if loss || !s.healthy {
            self.degrade(Stage::Logs, CollectionFailure::Unconfirmed);
        }
        Ok(())
    }
    fn log_visible(&self, record: &logs::LogRecord) -> bool {
        let Some((profile, uid)) = &self.agent_scope else {
            return true;
        };
        match &record.event {
            logs::Event::Audit(a) => {
                a.profile.as_deref() == Some(profile.as_str())
                    || a.profile.is_none()
                        && a.action == logs::AuditAction::Denied
                        && a.actor_uid == *uid
            }
            _ => false,
        }
    }
    fn project_log_audit(&mut self, a: &logs::AuditProjection) -> Result<Audit> {
        Ok(Audit {
            application_epoch: Some(
                self.aliases
                    .get(Namespace::ApplicationEpoch, a.application_instance.as_str())?,
            ),
            sequence: a.audit_sequence,
            actor: self.aliases.uid(a.actor_uid)?,
            profile: self
                .aliases
                .optional(Namespace::Profile, a.profile.as_deref())?,
            job: self
                .aliases
                .optional(Namespace::Job, a.job_id.as_ref().map(JobId::as_str))?,
            session: self.aliases.optional(
                Namespace::Session,
                a.session_id.as_ref().map(SessionId::as_str),
            )?,
            action: a.action,
            failure: a.failure,
            state: a.state,
            evidence: a.evidence,
            cleanup: a.cleanup,
        })
    }
    pub(crate) fn encode(&self, capture: CaptureOutcome) -> Result<Vec<u8>> {
        let summary = self.summary();
        let capture = if capture == CaptureOutcome::Complete && !summary.complete {
            CaptureOutcome::Partial
        } else {
            capture
        };
        let document = Document {
            schema_version: 1,
            capture,
            summary,
            program_version: numeric_version(env!("CARGO_PKG_VERSION")),
            build: BuildFacts {
                secure_agent_collection: cfg!(feature = "secure-transport"),
            },
            privacy: Privacy {
                policy_version: 1,
                identifiers_aliased: true,
                paths_and_network_addresses_omitted: true,
                credentials_and_payloads_omitted: true,
                raw_messages_omitted: true,
                independent_section_samples: true,
                network_security_verified: false,
                hardware_test_performed: false,
            },
            limits: Limits {
                serialized_bytes: MAX_EXPORT_BYTES,
                aliases: MAX_ALIASES,
                jobs: 512,
                sessions: 512,
                audit: 1024,
                health: 512,
                log_records: MAX_LOGS,
                log_pages: MAX_PAGES,
            },
            alias_counts: AliasCounts::from(self.aliases.counts),
            requested_agent_scope: &self.requested_agent_scope,
            native: &self.native,
            helper: &self.helper,
            agent: &self.agent,
            health: &self.health,
            logs: &self.logs,
        };
        let mut writer = LimitedBytes {
            bytes: Vec::with_capacity(MAX_EXPORT_BYTES),
            limit: MAX_EXPORT_BYTES,
        };
        serde_json::to_writer(&mut writer, &document).map_err(|_| limit())?;
        writer.write_all(b"\n").map_err(|_| limit())?;
        Ok(writer.bytes)
    }
}

#[derive(Serialize)]
struct Document<'a> {
    schema_version: u32,
    capture: CaptureOutcome,
    summary: Summary,
    program_version: Option<NumericVersion>,
    build: BuildFacts,
    privacy: Privacy,
    limits: Limits,
    alias_counts: AliasCounts,
    requested_agent_scope: &'a Option<AgentScope>,
    native: &'a Option<NativeObservation>,
    helper: &'a Option<HelperObservation>,
    agent: &'a Option<Agent>,
    health: &'a Health,
    logs: &'a Logs,
}
#[derive(Serialize)]
struct BuildFacts {
    secure_agent_collection: bool,
}
#[derive(Serialize)]
struct AliasCounts {
    application_epochs: u16,
    process_epochs: u16,
    jobs: u16,
    sessions: u16,
    attempts: u16,
    resources: u16,
    devices: u16,
    principals: u16,
    profiles: u16,
    uids: u16,
    connections: u16,
}
impl From<[u16; 11]> for AliasCounts {
    fn from(c: [u16; 11]) -> Self {
        Self {
            application_epochs: c[0],
            process_epochs: c[1],
            jobs: c[2],
            sessions: c[3],
            attempts: c[4],
            resources: c[5],
            devices: c[6],
            principals: c[7],
            profiles: c[8],
            uids: c[9],
            connections: c[10],
        }
    }
}
#[derive(Serialize)]
struct Privacy {
    policy_version: u32,
    identifiers_aliased: bool,
    paths_and_network_addresses_omitted: bool,
    credentials_and_payloads_omitted: bool,
    raw_messages_omitted: bool,
    independent_section_samples: bool,
    network_security_verified: bool,
    hardware_test_performed: bool,
}
#[derive(Serialize)]
struct Limits {
    serialized_bytes: usize,
    aliases: usize,
    jobs: usize,
    sessions: usize,
    audit: usize,
    health: usize,
    log_records: usize,
    log_pages: usize,
}
struct LimitedBytes {
    bytes: Vec<u8>,
    limit: usize,
}
impl Write for LimitedBytes {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.limit.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("diagnostic byte limit"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn numeric_version(raw: &str) -> Option<NumericVersion> {
    if raw.is_empty()
        || raw.len() > 128
        || !raw
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-+".contains(&b))
    {
        return None;
    }
    let core = raw.split(['-', '+']).next()?;
    let mut parts = core.split('.');
    let number = |s: &str| {
        (!s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
            .then(|| s.parse::<u32>().ok())
            .flatten()
    };
    let major = number(parts.next()?)?;
    let minor = number(parts.next()?)?;
    let patch = match parts.next() {
        Some(value) => Some(number(value)?),
        None => None,
    };
    if parts.next().is_some() {
        return None;
    }
    Some(NumericVersion {
        major,
        minor,
        patch,
    })
}
fn invalid() -> Error {
    Error::Protocol("invalid diagnostic observation".into())
}
fn limit() -> Error {
    Error::State("diagnostic projection limit exceeded".into())
}
fn collection_error(error: &Error) -> CollectionFailure {
    match error {
        Error::InvalidInput(_) => CollectionFailure::InvalidInput,
        Error::Permission(_) => CollectionFailure::Permission,
        Error::Io(_) => CollectionFailure::Io,
        Error::Protocol(_) => CollectionFailure::Corrupt,
        Error::Timeout(_) => CollectionFailure::Timeout,
        Error::Unsupported(_) => CollectionFailure::Unsupported,
        _ => CollectionFailure::Limit,
    }
}

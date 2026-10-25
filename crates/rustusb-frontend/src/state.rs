use crate::Options;
use crate::automation::{RuleDecisionReceipt, RulesPage, UnresolvedRule};
use rustusb_core::application::{JobId, RequestId, SessionId};
use rustusb_discovery::DeviceSnapshot;
use rustusb_service::{
    catalog::CatalogSnapshot,
    health::SessionHealth,
    model::{AuditEvent, Job, Session},
    rpc::ProfileInfo,
};
use std::sync::Arc;

#[derive(Debug, Clone)]
/// Fixed-profile or exact-identity service mutation; service authorization remains authoritative.
pub enum Mutation {
    /// Start: finite frontend operation.
    Start(String),
    /// StopJob: finite frontend operation.
    StopJob(JobId),
    /// StopSession: finite frontend operation.
    StopSession(SessionId),
    /// Edit one fixed profile with a distinct retained mutation UUID.
    Rule {
        /// Complete fixed profile effect.
        profile: String,
        /// Typed bounded definition or exact revision mutation.
        mutation: rustusb_service::automation::RuleMutation,
    },
    /// Query the original rule mutation; never resubmit its payload.
    ResolveRule,
}
#[derive(Debug)]
/// Finite frontend control; validate user-controlled strings/options before queueing.
pub enum Command {
    /// Refresh: finite frontend operation.
    Refresh,
    /// Profile: finite frontend operation.
    Profile(String),
    /// Remote: finite frontend operation.
    Remote(bool),
    /// Request independently qualified rule metadata while Settings is active.
    Rules(bool),
    /// Switch GUI observation surfaces in one admitted operation.
    Observe {
        /// Request the authenticated remote observer.
        remote: bool,
        /// Request bounded rule metadata.
        rules: bool,
    },
    /// Options: finite frontend operation.
    Options(Options),
    /// SaveSettings: finite frontend operation.
    SaveSettings,
    /// ReloadSettings: finite frontend operation.
    ReloadSettings,
    /// Next: finite frontend operation.
    Next(PageKind),
    /// First: finite frontend operation.
    First(PageKind),
    /// Mutate: finite frontend operation.
    Mutate(Mutation),
}
#[derive(Debug)]
/// One admitted command with a caller-owned monotonic sequence.
pub struct Envelope {
    /// Monotonic caller sequence; the worker acknowledges only after command completion.
    pub sequence: u64,
    /// One finite operation, with no new endpoint or credential authority.
    pub command: Command,
}
#[derive(Debug, Clone, Copy)]
/// One bounded service result section.
pub enum PageKind {
    /// Jobs: finite frontend operation.
    Jobs,
    /// Sessions: finite frontend operation.
    Sessions,
    /// Audit: finite frontend operation.
    Audit,
    /// Independently paginated automatic rules.
    Rules,
    /// Independently sequenced automatic-rule audit.
    RuleEvents,
}
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
/// Original issued Start identity retained until a real Started response.
pub struct Unresolved {
    /// Currently selected fixed profile; no device key override.
    pub profile: String,
    /// Original opaque retry identity; never an authorization token.
    pub request_id: RequestId,
}
#[derive(Debug, Clone)]
/// Latest metadata only; never USB authority or cleanup proof.
pub struct Snapshot {
    /// Monotonic publication revision within this frontend worker.
    pub revision: u64,
    /// Last fully processed command sequence; queue removal alone is not completion.
    pub acknowledged: u64,
    /// Effective frontend observation preferences, independent of service policy.
    pub options: Options,
    /// Last successfully checked saved preferences, if present.
    pub saved_options: Option<Options>,
    /// Last checked preference read/write result for operator display.
    pub preferences_status: String,
    /// Whether this worker owns a healthy checked local request writer.
    pub recovery_writable: bool,
    /// Independent local retry-recording status; no service/cleanup authority.
    pub recovery_status: String,
    /// Read-authorized fixed profiles returned by the actual agent.
    pub profiles: Vec<ProfileInfo>,
    /// Currently selected fixed profile; no device key override.
    pub profile: Option<String>,
    /// Latest complete local discovery metadata, including explicit freshness.
    pub local: Option<Arc<DeviceSnapshot>>,
    /// Latest authenticated remote catalog; not proof of successful import.
    pub remote: Option<Arc<CatalogSnapshot>>,
    /// Current bounded job page; qualify it using jobs_status.
    pub jobs: Vec<Job>,
    /// Current bounded session page; release requires actual cleanup evidence.
    pub sessions: Vec<Session>,
    /// Independently sampled data-link health; empty means unavailable.
    pub health: Vec<SessionHealth>,
    /// Current authorized retained audit page, distinct from a business acknowledgement.
    pub audit: Vec<AuditEvent>,
    /// Exclusive continuation cursor only when another job page exists.
    pub next_job: Option<JobId>,
    /// Exclusive continuation cursor only when another session page exists.
    pub next_session: Option<SessionId>,
    /// Exclusive audit continuation, if the returned page advances.
    pub next_audit: Option<u64>,
    /// Health from the last real agent reply; absent when unknown.
    pub journal: Option<bool>,
    /// Operator-facing result of the most recent service action or refresh.
    pub status: String,
    /// Freshness or failure qualifying retained job observations.
    pub jobs_status: String,
    /// Freshness or failure qualifying retained session observations.
    pub sessions_status: String,
    /// Audit availability, retention gap and oldest retained sequence.
    pub audit_status: String,
    /// Local observer state and resynchronization information.
    pub local_status: String,
    /// Remote observer availability and independently sampled freshness.
    pub remote_status: String,
    /// Whether the active frontend requests remote observation.
    pub remote_page: bool,
    /// Original issued Start intent retained until a real Started reply.
    pub unresolved: Option<Unresolved>,
    /// Bounded independently qualified automatic-sharing metadata.
    pub rules: RulesPage,
    /// Original issued rule edit, separate from Start and preferences.
    pub unresolved_rule: Option<UnresolvedRule>,
    /// Last actual rule receipt and original payload, independent of profile selection.
    pub last_rule_decision: Option<RuleDecisionReceipt>,
}
impl Snapshot {
    /// Initialize an unsampled view; no operation has been issued.
    pub fn new(options: Options) -> Self {
        Self {
            revision: 0,
            acknowledged: 0,
            options,
            saved_options: None,
            preferences_status: "Preferences disabled; invocation only".into(),
            profiles: vec![],
            profile: None,
            local: None,
            remote: None,
            jobs: vec![],
            sessions: vec![],
            health: vec![],
            audit: vec![],
            next_job: None,
            next_session: None,
            next_audit: None,
            journal: None,
            status: "Initializing; no operation requested".into(),
            jobs_status: "Not sampled".into(),
            sessions_status: "Not sampled".into(),
            audit_status: "Not sampled".into(),
            local_status: "Initializing".into(),
            remote_status: "Open Remote to discover".into(),
            remote_page: false,
            recovery_writable: false,
            recovery_status: "Initializing checked local request recovery".into(),
            unresolved: None,
            rules: RulesPage::default(),
            unresolved_rule: None,
            last_rule_decision: None,
        }
    }
    /// Retain pages with explicit stale status and discard live-health claims.
    pub fn agent_stale(&mut self) {
        // These pages are retained observations, never a current service receipt.
        self.jobs_status = "Stale: agent unavailable; retained page".into();
        self.sessions_status = "Stale: agent unavailable; retained page".into();
        self.audit_status = "Stale: agent unavailable; retained page".into();
        self.journal = None;
        self.health.clear();
        self.rules.current = false;
        self.rules.decision_support = None;
        self.rules.decision_support_status = "Stale: agent unavailable".into();
        self.rules.status = "Stale: agent unavailable; retained rule page".into();
        self.rules.events_status = "Stale: agent unavailable; retained event page".into();
    }
    /// Discard metadata/cursors from the previous selection.
    pub fn clear_profile(&mut self) {
        let requested = self.rules.requested;
        self.rules = RulesPage {
            requested,
            ..RulesPage::default()
        };
        self.jobs.clear();
        self.sessions.clear();
        self.health.clear();
        self.audit.clear();
        self.next_job = None;
        self.next_session = None;
        self.next_audit = None;
        self.remote = None;
        self.jobs_status = "Not sampled".into();
        self.sessions_status = "Not sampled".into();
        self.audit_status = "Not sampled".into();
        self.remote_status = "Not sampled".into();
        self.journal = None;
    }
    /// Retain the original Start identity before producing a request; retries never mint a new ID.
    pub fn mutation_request(
        &mut self,
        action: Mutation,
        fresh: Option<RequestId>,
    ) -> Option<rustusb_service::rpc::Request> {
        use rustusb_service::rpc::Request;
        match action {
            Mutation::Start(profile) => {
                if let Some(intent) = &self.unresolved {
                    Some(Request::RetryStart {
                        profile: intent.profile.clone(),
                        request_id: intent.request_id.clone(),
                    })
                } else {
                    let request_id = fresh?;
                    self.unresolved = Some(Unresolved {
                        profile: profile.clone(),
                        request_id: request_id.clone(),
                    });
                    Some(Request::Start {
                        profile,
                        request_id,
                    })
                }
            }
            Mutation::StopJob(job_id) => Some(Request::StopJob { job_id }),
            Mutation::StopSession(session_id) => Some(Request::StopSession { session_id }),
            Mutation::Rule { profile, mutation } => self.rule_request_with_protocol(
                profile,
                mutation,
                fresh?,
                self.rules.decision_support == Some(true),
            ),
            Mutation::ResolveRule => self.rule_retry(),
        }
    }
    /// Check the actual issued request before accepting an original Start result.
    pub fn mutation_response_to(
        &mut self,
        request: &rustusb_service::rpc::Request,
        response: rustusb_service::rpc::Response,
    ) {
        use rustusb_service::rpc::{Request, Response};
        if matches!(&response, Response::Started { .. })
            && !matches!(request,
            Request::Start { profile, request_id } | Request::RetryStart { profile, request_id }
                if self.unresolved.as_ref().is_some_and(|i| i.profile == *profile && i.request_id == *request_id))
        {
            self.status = "Unrelated Start receipt; original UUID retained".into();
            return;
        }
        self.mutation_response(response);
    }
    /// Apply the actual service reply, keeping cleanup and admission distinct.
    pub fn mutation_response(&mut self, response: rustusb_service::rpc::Response) {
        use rustusb_service::rpc::Response;
        self.status=match response {
            Response::Started{job,replayed} if self.unresolved.as_ref().is_some_and(|i| i.profile == job.profile && job.operator_uid == rustix::process::geteuid().as_raw())=>{
                self.unresolved=None;
                format!("{} job {}; inspect actual session state",if replayed{"Resolved original"}else{"Admitted"},job.id)
            }
            Response::StopAccepted{owner_present}=>format!("Stop accepted (owner present: {owner_present}); release is not yet proven"),
            Response::RequestExpired{..}=>"Original request is outside retry history; reconcile jobs; new starts remain blocked".into(),
            Response::Error{code}=>format!("Agent refused: {code:?}; any unresolved start remains retained"),
            _=>"Unexpected mutation response; reconcile jobs before retrying".into(),
        };
    }
}

/// Finite printable text, including removal of terminal controls and bidi formatting.
/// text.
pub fn text(value: &str, limit: usize) -> String {
    value.chars().filter(|c| !c.is_control() && !matches!(*c,'\u{061c}'|'\u{200b}'..='\u{200f}'|'\u{202a}'..='\u{202e}'|'\u{2060}'..='\u{206f}'|'\u{feff}')).take(limit).collect()
}

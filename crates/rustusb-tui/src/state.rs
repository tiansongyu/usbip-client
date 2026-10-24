use crate::Options;
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
pub(crate) enum Mutation {
    Start(String),
    StopJob(JobId),
    StopSession(SessionId),
}
#[derive(Debug)]
pub(crate) enum Command {
    Refresh,
    Profile(String),
    Remote(bool),
    Options(Options),
    SaveSettings,
    ReloadSettings,
    Next(PageKind),
    First(PageKind),
    Mutate(Mutation),
}
#[derive(Debug)]
pub(crate) struct Envelope {
    pub sequence: u64,
    pub command: Command,
}
#[derive(Debug, Clone, Copy)]
pub(crate) enum PageKind {
    Jobs,
    Sessions,
    Audit,
}
#[derive(Debug, Clone)]
pub(crate) struct Unresolved {
    pub profile: String,
    pub request_id: RequestId,
}
#[derive(Debug, Clone)]
pub(crate) struct Snapshot {
    pub revision: u64,
    pub acknowledged: u64,
    pub options: Options,
    pub saved_options: Option<Options>,
    pub preferences_status: String,
    pub profiles: Vec<ProfileInfo>,
    pub profile: Option<String>,
    pub local: Option<Arc<DeviceSnapshot>>,
    pub remote: Option<Arc<CatalogSnapshot>>,
    pub jobs: Vec<Job>,
    pub sessions: Vec<Session>,
    pub health: Vec<SessionHealth>,
    pub audit: Vec<AuditEvent>,
    pub next_job: Option<JobId>,
    pub next_session: Option<SessionId>,
    pub next_audit: Option<u64>,
    pub journal: Option<bool>,
    pub status: String,
    pub jobs_status: String,
    pub sessions_status: String,
    pub audit_status: String,
    pub local_status: String,
    pub remote_status: String,
    pub remote_page: bool,
    pub unresolved: Option<Unresolved>,
}
impl Snapshot {
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
            unresolved: None,
        }
    }
    pub fn clear_profile(&mut self) {
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
        }
    }
    pub fn mutation_response(&mut self, response: rustusb_service::rpc::Response) {
        use rustusb_service::rpc::Response;
        self.status=match response {
            Response::Started{job,replayed}=>{
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
pub(crate) fn text(value: &str, limit: usize) -> String {
    value.chars().filter(|c| !c.is_control() && !matches!(*c,'\u{061c}'|'\u{200b}'..='\u{200f}'|'\u{202a}'..='\u{202e}'|'\u{2060}'..='\u{206f}'|'\u{feff}')).take(limit).collect()
}

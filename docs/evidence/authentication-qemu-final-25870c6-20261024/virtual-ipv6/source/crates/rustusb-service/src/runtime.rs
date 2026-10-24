//! Direct job supervision. Cancellation requests never abort an owned USB task.

use crate::{
    config::{Action, Backend, Credentials, LoadedConfig},
    context::{JobContext, JobMessage, SessionContext},
    fresh_id,
    model::*,
    rpc::*,
    store::{RequestRecord, State, Store},
};
use rustusb_core::{Error, Result, SessionState, application::*};
use std::{
    collections::BTreeMap,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    sync::{mpsc, oneshot, watch},
    task::JoinSet,
};

pub(crate) struct Command {
    pub uid: u32,
    pub request: Request,
    pub reply: oneshot::Sender<ResponseEnvelope>,
}
struct Control {
    profile: String,
    job_id: JobId,
    stop: watch::Sender<bool>,
    stoppable: bool,
}
struct HealthEntry {
    profile: String,
    view: crate::health::SessionHealth,
    reporter: Option<crate::health::HealthReporter>,
}
#[derive(Default)]
pub(crate) struct Controls {
    jobs: BTreeMap<JobId, Control>,
    sessions: BTreeMap<SessionId, Control>,
    health: BTreeMap<SessionId, HealthEntry>,
    journal_unhealthy: bool,
    shutdown_requested: bool,
}
impl Controls {
    pub(crate) fn journal_healthy(&self) -> bool {
        !self.journal_unhealthy
    }

    fn register_job(&mut self, id: JobId, control: Control) {
        if self.shutdown_requested {
            control.stop.send_replace(true);
        }
        self.jobs.insert(id, control);
    }

    fn register_session(&mut self, id: SessionId, control: Control) {
        let parent_stopped = self
            .jobs
            .get(&control.job_id)
            .is_some_and(|job| *job.stop.borrow());
        if self.shutdown_requested || parent_stopped {
            control.stop.send_replace(true);
        }
        self.sessions.insert(id, control);
    }

    pub(crate) fn begin_shutdown(&mut self) {
        // The same short mutex protects registration and the sticky latch.
        // A Start/Begin commit finishing later cannot create an unstopped owner.
        self.shutdown_requested = true;
        self.stop_all();
    }

    pub fn health(
        &self,
        config: &LoadedConfig,
        uid: u32,
        profile: &str,
        after: Option<&SessionId>,
    ) -> Result<ResponseEnvelope> {
        config.manifest.authorize(uid, profile, Action::Read)?;
        let all: Vec<_> = self
            .health
            .iter()
            .filter(|(id, entry)| entry.profile == profile && after.is_none_or(|after| *id > after))
            .collect();
        let total = all.len();
        let mut sessions = Vec::new();
        let mut bytes = 0usize;
        for (_, entry) in all.into_iter().take(16) {
            let mut view = entry.view.clone();
            if let Some(reporter) = &entry.reporter {
                view.link = reporter.snapshot()?;
                view.availability = if view.role == SessionRole::Export {
                    crate::health::HealthAvailability::NotDataSession
                } else if view.link.is_some() {
                    crate::health::HealthAvailability::Live
                } else {
                    crate::health::HealthAvailability::NotObserved
                };
            }
            let size = serde_json::to_vec(&view)
                .map_err(|_| Error::State("agent health encoding".into()))?
                .len();
            if bytes + size > 60000 {
                break;
            }
            bytes += size;
            sessions.push(view);
        }
        let next_after = (sessions.len() < total)
            .then(|| sessions.last().map(|s| s.session_id.clone()))
            .flatten();
        Ok(ResponseEnvelope {
            schema_version: 1,
            journal_healthy: !self.journal_unhealthy,
            response: Response::SessionHealth {
                sessions,
                next_after,
            },
        })
    }

    pub fn request(&self, config: &LoadedConfig, uid: u32, request: &Request) -> Result<bool> {
        let control = match request {
            Request::StopJob { job_id } => self.jobs.get(job_id),
            Request::StopSession { session_id } => self.sessions.get(session_id),
            _ => return Ok(false),
        };
        let Some(control) = control else {
            return Ok(false);
        };
        config
            .manifest
            .authorize(uid, &control.profile, Action::Stop)?;
        if !control.stoppable {
            return Err(Error::Unsupported("this backend requires stopping the export job; individual incoming sessions can be stopped".into()));
        }
        control.stop.send_replace(true);
        if matches!(request, Request::StopJob { .. }) {
            for session in self
                .sessions
                .values()
                .filter(|s| s.job_id == control.job_id)
            {
                session.stop.send_replace(true);
            }
        }
        Ok(true)
    }
    fn stop_all(&self) {
        for control in self.jobs.values().chain(self.sessions.values()) {
            control.stop.send_replace(true);
        }
    }
}

pub(crate) struct Runtime {
    config: Arc<LoadedConfig>,
    instance: InstanceId,
    state: State,
    store: Arc<Mutex<Store>>,
    healthy: bool,
    controls: Arc<Mutex<Controls>>,
    facts: mpsc::Receiver<JobMessage>,
    fact_sender: mpsc::Sender<JobMessage>,
    workers: JoinSet<(JobId, Result<()>)>,
    recoveries: JoinSet<(SessionId, Vec<BrokerOperationStatus>)>,
    recovery_cursor: usize,
    logging: Option<rustusb_logging::LogMonitor>,
    diagnostics: Arc<crate::diagnostics::Registry>,
}
impl Runtime {
    pub async fn open(config: Arc<LoadedConfig>, controls: Arc<Mutex<Controls>>) -> Result<Self> {
        Self::open_with_logging(config, controls, None).await
    }

    pub async fn open_with_logging(
        config: Arc<LoadedConfig>,
        controls: Arc<Mutex<Controls>>,
        logging: Option<rustusb_logging::LogMonitor>,
    ) -> Result<Self> {
        let directory = config.manifest.data_directory.clone();
        let uid = config.manifest.broker_uid;
        let store = tokio::task::spawn_blocking(move || Store::open(&directory, uid))
            .await
            .map_err(|_| Error::State("agent store task failed".into()))??;
        let state = store.state.clone();
        let instance = InstanceId::new(fresh_id()?)?;
        let diagnostics = Arc::new(crate::diagnostics::Registry::new(
            instance.clone(),
            state.clone(),
        ));
        let (sender, receiver) = mpsc::channel(64);
        let mut value = Self {
            config,
            instance,
            state,
            store: Arc::new(Mutex::new(store)),
            healthy: true,
            controls,
            facts: receiver,
            fact_sender: sender,
            workers: JoinSet::new(),
            recoveries: JoinSet::new(),
            recovery_cursor: 0,
            logging,
            diagnostics,
        };
        let mut next = value.state.clone();
        let mut changed = false;
        for job in &mut next.jobs {
            if job.state != JobState::Finished {
                // Every runner durably begins a session before requesting any
                // capability. An empty job therefore has no USB attempt; a
                // fully released job retains its already committed receipts.
                let released = next
                    .sessions
                    .iter()
                    .filter(|s| s.job_id == job.id)
                    .all(|s| matches!(s.cleanup, Some(Cleanup::NotStarted | Cleanup::Verified)));
                job.state = if released {
                    JobState::Finished
                } else {
                    JobState::Recovering
                };
                changed = true;
            }
        }
        for session in &mut next.sessions {
            if !matches!(
                session.cleanup,
                Some(Cleanup::NotStarted | Cleanup::Verified)
            ) {
                session.state = SessionState::Recovering;
                session.terminal = false;
                session.evidence = Evidence::Unconfirmed;
                changed = true;
            }
        }
        if changed {
            next.audit(uid, None, None, None, AuditAction::Recovery, None)?;
            value.persist(next).await?;
        }
        value.refresh_health()?;
        Ok(value)
    }

    pub(crate) fn diagnostics(&self) -> Arc<crate::diagnostics::Registry> {
        self.diagnostics.clone()
    }

    fn refresh_health(&self) -> Result<()> {
        use crate::health::{HealthAvailability, SessionHealth};
        let mut controls = self.controls.lock().map_err(|_| lock_error())?;
        controls
            .health
            .retain(|id, _| self.state.sessions.iter().any(|s| &s.id == id));
        controls.journal_unhealthy = !self.healthy;
        for session in &self.state.sessions {
            let reporter = if !session.terminal && controls.sessions.contains_key(&session.id) {
                controls
                    .health
                    .get(&session.id)
                    .and_then(|e| e.reporter.clone())
            } else {
                None
            };
            let link = self
                .state
                .link_history
                .iter()
                .find(|e| e.session_id == session.id)
                .and_then(|e| e.link);
            let availability = if session.role == SessionRole::Export {
                HealthAvailability::NotDataSession
            } else if session.instance_id != self.instance {
                HealthAvailability::PreviousEpoch
            } else {
                HealthAvailability::OwnerEnded
            };
            controls.health.insert(
                session.id.clone(),
                HealthEntry {
                    profile: session.profile.clone(),
                    reporter,
                    view: SessionHealth {
                        session_id: session.id.clone(),
                        instance_id: session.instance_id.clone(),
                        role: session.role,
                        availability,
                        link,
                    },
                },
            );
        }
        Ok(())
    }
    fn checkpoint_links(&self, next: &mut State) -> Result<()> {
        let controls = self.controls.lock().map_err(|_| lock_error())?;
        for entry in &mut next.link_history {
            if let Some(reporter) = controls
                .health
                .get(&entry.session_id)
                .and_then(|e| e.reporter.as_ref())
            {
                let after = reporter.snapshot()?;
                crate::health::check_progress(entry.link, after)?;
                entry.link = after;
            }
        }
        Ok(())
    }

    async fn persist(&mut self, mut next: State) -> Result<()> {
        if !self.healthy {
            return Err(Error::State("agent journal unhealthy".into()));
        }
        self.checkpoint_links(&mut next)?;
        next.preflight(false)?;
        let previous_sequence = self.state.sequence;
        let stored = next.clone();
        let store = self.store.clone();
        let result = tokio::task::spawn_blocking(move || {
            store
                .lock()
                .map_err(|_| Error::State("agent store lock poisoned".into()))?
                .commit(stored)
        })
        .await
        .map_err(|_| Error::State("agent store transaction task failed".into()))
        .and_then(|r| r);
        match result {
            Ok(()) => {
                self.state = next;
                // This is a best-effort mirror of an already committed fact.
                // Admission never awaits logging and log loss never poisons WAL.
                // A later registry failure cannot suppress this actual commit.
                if let Some(logging) = &self.logging {
                    for event in self
                        .state
                        .audit
                        .iter()
                        .filter(|event| event.sequence > previous_sequence)
                    {
                        logging.audit(project_audit(&self.instance, event));
                    }
                }
                // Clone outside the diagnostic registry lock. Publication is
                // observation only: poison cannot turn this committed fact or
                // its already-emitted mirror into a business failure.
                self.diagnostics.publish(self.state.clone());
                self.refresh_health()
            }
            Err(error) => {
                self.healthy = false;
                if let Ok(mut controls) = self.controls.lock() {
                    controls.journal_unhealthy = true;
                }
                Err(error)
            }
        }
    }

    pub async fn run(
        mut self,
        mut commands: mpsc::Receiver<Command>,
        mut stop: watch::Receiver<bool>,
    ) -> Result<()> {
        let mut stopping = *stop.borrow();
        let mut tick = tokio::time::interval(Duration::from_secs(1));
        if stopping && let Ok(mut controls) = self.controls.lock() {
            controls.begin_shutdown();
        }
        loop {
            if stopping && self.workers.is_empty() && self.recoveries.is_empty() {
                break;
            }
            tokio::select! {
                changed=stop.changed(),if !stopping=>{let _=changed;stopping=true;if let Ok(mut controls)=self.controls.lock(){controls.begin_shutdown();}else{self.healthy=false;}},
                fact=self.facts.recv()=>{if let Some(fact)=fact{self.fact(fact).await;}},
                result=self.workers.join_next(),if !self.workers.is_empty()=>{if let Some(result)=result{match result{Ok((id,result))=>self.job_finished(id,result).await,Err(_)=>{self.healthy=false;tracing::error!("application owner task ended abnormally; cleanup remains unproven");}}}},
                result=self.recoveries.join_next(),if !self.recoveries.is_empty()=>{if let Some(Ok((id,proofs)))=result{self.recovered(id,proofs).await;}},
                command=commands.recv(),if !stopping=>{match command {Some(command)=>{
                    let response=self.command(command.uid,command.request).await;
                    let _=command.reply.send(ResponseEnvelope{schema_version:1,journal_healthy:self.healthy,response});
                },None=>{stopping=true;if let Ok(mut controls)=self.controls.lock(){controls.begin_shutdown();}else{self.healthy=false;}}}},
                _=tick.tick(),if !stopping&&self.healthy&&self.recoveries.is_empty()=>self.start_recovery(),
            }
        }
        if self.healthy {
            Ok(())
        } else {
            Err(Error::State(
                "agent stopped with unhealthy durable recording; inspect retained sessions".into(),
            ))
        }
    }

    async fn command(&mut self, uid: u32, request: Request) -> Response {
        match self.dispatch(uid, request).await {
            Ok(response) => response,
            Err(error) => {
                let category = Failure::from_error(&error);
                if category == Failure::Permission && self.healthy {
                    let mut next = self.state.clone();
                    if next
                        .audit(uid, None, None, None, AuditAction::Denied, Some(category))
                        .is_ok()
                    {
                        let _ = self.persist(next).await;
                    }
                }
                Response::Error { code: category }
            }
        }
    }

    async fn dispatch(&mut self, uid: u32, request: Request) -> Result<Response> {
        match request {
            Request::Profiles => Ok(Response::Profiles {
                profiles: self
                    .config
                    .manifest
                    .profiles
                    .iter()
                    .filter(|p| {
                        self.config
                            .manifest
                            .authorize(uid, &p.id, Action::Read)
                            .is_ok()
                    })
                    .map(|p| ProfileInfo {
                        id: p.id.clone(),
                        backend: p.backend,
                    })
                    .collect(),
            }),
            Request::Start {
                profile,
                request_id,
            } => self.start(uid, profile, request_id, false).await,
            Request::RetryStart {
                profile,
                request_id,
            } => self.start(uid, profile, request_id, true).await,
            Request::StopJob { job_id } => {
                let job = self
                    .state
                    .jobs
                    .iter()
                    .find(|j| j.id == job_id)
                    .cloned()
                    .ok_or_else(denied)?;
                self.config
                    .manifest
                    .authorize(uid, &job.profile, Action::Stop)?;
                let owner = self.controls.lock().map_err(|_| lock_error())?.request(
                    &self.config,
                    uid,
                    &Request::StopJob {
                        job_id: job_id.clone(),
                    },
                )?;
                let mut next = self.state.clone();
                if let Some(job) = next.jobs.iter_mut().find(|j| j.id == job_id) {
                    job.stop_requested = true;
                    if owner {
                        job.state = JobState::Stopping;
                    }
                }
                for session in next
                    .sessions
                    .iter_mut()
                    .filter(|s| s.job_id == job_id && !s.terminal)
                {
                    session.stop_requested = true;
                }
                next.audit(
                    uid,
                    Some(job.profile),
                    Some(job_id),
                    None,
                    AuditAction::Stop,
                    None,
                )?;
                self.persist(next).await?;
                Ok(Response::StopAccepted {
                    owner_present: owner,
                })
            }
            Request::StopSession { session_id } => {
                let session = self
                    .state
                    .sessions
                    .iter()
                    .find(|s| s.id == session_id)
                    .cloned()
                    .ok_or_else(denied)?;
                self.config
                    .manifest
                    .authorize(uid, &session.profile, Action::Stop)?;
                let owner = self.controls.lock().map_err(|_| lock_error())?.request(
                    &self.config,
                    uid,
                    &Request::StopSession {
                        session_id: session_id.clone(),
                    },
                )?;
                let mut next = self.state.clone();
                if let Some(session) = next.sessions.iter_mut().find(|s| s.id == session_id) {
                    session.stop_requested = true;
                }
                next.audit(
                    uid,
                    Some(session.profile),
                    Some(session.job_id),
                    Some(session_id),
                    AuditAction::Stop,
                    None,
                )?;
                self.persist(next).await?;
                Ok(Response::StopAccepted {
                    owner_present: owner,
                })
            }
            Request::Jobs { profile, after } => {
                self.config
                    .manifest
                    .authorize(uid, &profile, Action::Read)?;
                let mut jobs: Vec<_> = self
                    .state
                    .jobs
                    .iter()
                    .filter(|j| j.profile == profile && after.as_ref().is_none_or(|id| j.id > *id))
                    .cloned()
                    .collect();
                jobs.sort_by(|a, b| a.id.cmp(&b.id));
                let more = jobs.len() > 16;
                jobs.truncate(16);
                let next_after = if more {
                    jobs.last().map(|j| j.id.clone())
                } else {
                    None
                };
                Ok(Response::Jobs { jobs, next_after })
            }
            Request::Sessions { profile, after } => {
                self.config
                    .manifest
                    .authorize(uid, &profile, Action::Read)?;
                let mut all: Vec<_> = self
                    .state
                    .sessions
                    .iter()
                    .filter(|s| s.profile == profile && after.as_ref().is_none_or(|id| s.id > *id))
                    .cloned()
                    .collect();
                all.sort_by(|a, b| a.id.cmp(&b.id));
                let mut sessions = vec![];
                let mut bytes = 0;
                let total = all.len();
                for session in all.into_iter().take(16) {
                    let size = serde_json::to_vec(&session)
                        .map_err(|_| Error::State("agent session encoding".into()))?
                        .len();
                    if bytes + size > 60000 {
                        break;
                    }
                    bytes += size;
                    sessions.push(session);
                }
                if total > 0 && sessions.is_empty() {
                    return Err(Error::State(
                        "single agent session exceeds response budget".into(),
                    ));
                }
                let next_after = if sessions.len() < total {
                    sessions.last().map(|s| s.id.clone())
                } else {
                    None
                };
                Ok(Response::Sessions {
                    sessions,
                    next_after,
                })
            }
            Request::SessionHealth { profile, after } => {
                let envelope = self.controls.lock().map_err(|_| lock_error())?.health(
                    &self.config,
                    uid,
                    &profile,
                    after.as_ref(),
                )?;
                Ok(envelope.response)
            }
            Request::Events { profile, after } => self.events(uid, profile, after, false),
            Request::Audit { profile, after } => self.events(uid, profile, after, true),
            Request::ReadLogs { profile, .. } => {
                // Only denied reads use this path so they retain the existing
                // durable denial audit. Authorized reads must never await WAL.
                self.config
                    .manifest
                    .authorize(uid, &profile, Action::Audit)?;
                Err(Error::State(
                    "log reads require the independent IPC path".into(),
                ))
            }
            Request::BeginDiagnostic { profile }
            | Request::DiagnosticPage { profile, .. }
            | Request::EndDiagnostic { profile, .. } => {
                // Only denied reads arrive here; their ordinary durable denial
                // path is retained. Authorized observations bypass the actor.
                self.config
                    .manifest
                    .authorize(uid, &profile, Action::Read)?;
                self.config
                    .manifest
                    .authorize(uid, &profile, Action::Audit)?;
                Err(Error::State(
                    "diagnostic reads require the independent IPC path".into(),
                ))
            }
            Request::Discover { profile } => {
                self.config
                    .manifest
                    .authorize(uid, &profile, Action::Discover)?;
                let config = match self.config.credentials.get(&profile) {
                    Some(Credentials::Client(config)) => config.clone(),
                    _ => return Err(denied()),
                };
                // Pure discovery has no kernel ownership. Its request may time out
                // without dropping a prepare, commit or cleanup owner.
                let devices = crate::jobs::discover(&config, Duration::from_secs(4)).await?;
                let mut next = self.state.clone();
                next.audit(uid, Some(profile), None, None, AuditAction::Discover, None)?;
                self.persist(next).await?;
                Ok(Response::Catalog { devices })
            }
        }
    }

    fn events(&self, uid: u32, profile: String, after: u64, audit: bool) -> Result<Response> {
        self.config.manifest.authorize(
            uid,
            &profile,
            if audit { Action::Audit } else { Action::Read },
        )?;
        if after > self.state.sequence {
            return Err(Error::InvalidInput(
                "agent event cursor exceeds current sequence".into(),
            ));
        }
        let oldest_available = self
            .state
            .audit
            .first()
            .map_or(self.state.sequence.saturating_add(1), |e| e.sequence);
        let events: Vec<_> = self
            .state
            .audit
            .iter()
            .filter(|e| {
                e.sequence > after
                    && (e.profile.as_deref() == Some(&profile)
                        || (audit && e.profile.is_none() && (uid == 0 || e.actor_uid == uid)))
                    && (audit
                        || matches!(
                            e.action,
                            AuditAction::Admission
                                | AuditAction::Observe
                                | AuditAction::Finish
                                | AuditAction::Recovery
                        ))
            })
            .take(16)
            .cloned()
            .collect();
        let next_after = events.last().map_or(self.state.sequence, |e| e.sequence);
        Ok(Response::Events {
            events,
            next_after,
            oldest_available,
            gap: after.saturating_add(1) < oldest_available,
        })
    }

    async fn start(
        &mut self,
        uid: u32,
        profile: String,
        request_id: RequestId,
        retry: bool,
    ) -> Result<Response> {
        self.config
            .manifest
            .authorize(uid, &profile, Action::Start)?;
        if !self.healthy {
            return Err(Error::State(
                "agent journal unhealthy; new admission refused".into(),
            ));
        }
        if let Some(old) = self.state.requests.iter().find(|r| r.id == request_id) {
            if old.uid != uid || old.profile != profile {
                return Err(denied());
            }
            let job = self
                .state
                .jobs
                .iter()
                .find(|j| j.id == old.job_id)
                .cloned()
                .ok_or_else(|| Error::State("retained request lost its job".into()))?;
            return Ok(Response::Started {
                job,
                replayed: true,
            });
        }
        if retry {
            return Ok(Response::RequestExpired { request_id });
        }
        if self
            .state
            .jobs
            .iter()
            .any(|j| j.profile == profile && j.state != JobState::Finished)
        {
            return Err(Error::State(
                "profile has a live or unresolved previous job".into(),
            ));
        }
        let definition = self.definition(&profile)?;
        let id = JobId::new(fresh_id()?)?;
        let job = Job {
            id: id.clone(),
            instance_id: self.instance.clone(),
            profile: profile.clone(),
            operator_uid: uid,
            state: JobState::Starting,
            stop_requested: false,
            operation_error: None,
        };
        let mut next = self.state.clone();
        next.jobs.push(job.clone());
        next.requests.push(RequestRecord {
            id: request_id,
            uid,
            profile: profile.clone(),
            job_id: id.clone(),
        });
        next.audit(
            uid,
            Some(profile.clone()),
            Some(id.clone()),
            None,
            AuditAction::Start,
            None,
        )?;
        next.preflight(true)?;
        self.persist(next).await?;
        let (sender, receiver) = watch::channel(false);
        self.controls
            .lock()
            .map_err(|_| lock_error())?
            .register_job(
                id.clone(),
                Control {
                    profile,
                    job_id: id.clone(),
                    stop: sender,
                    stoppable: true,
                },
            );
        let context = JobContext {
            instance_id: self.instance.clone(),
            job_id: id.clone(),
            helper_socket: self.config.manifest.helper_socket.clone(),
            stop: receiver,
            facts: self.fact_sender.clone(),
        };
        self.workers.spawn(async move {
            let result = crate::jobs::run_job(definition, context).await;
            (id, result)
        });
        Ok(Response::Started {
            job,
            replayed: false,
        })
    }

    fn definition(&self, profile: &str) -> Result<crate::jobs::LoadedJob> {
        let selected = self
            .config
            .manifest
            .profiles
            .iter()
            .find(|p| p.id == profile)
            .ok_or_else(denied)?;
        match (selected.backend, self.config.credentials.get(profile)) {
            (Backend::Client, Some(Credentials::Client(config))) => {
                Ok(crate::jobs::LoadedJob::Client {
                    config: config.clone(),
                    timeout: Duration::from_secs(10),
                })
            }
            (Backend::Native, Some(Credentials::Server(config))) => {
                Ok(crate::jobs::LoadedJob::NativeServer(config.clone()))
            }
            #[cfg(feature = "rust-backend")]
            (Backend::Virtual, Some(Credentials::Server(config))) => {
                Ok(crate::jobs::LoadedJob::VirtualServer(config.clone()))
            }
            #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
            (Backend::Usbfs, Some(Credentials::Server(config))) => {
                Ok(crate::jobs::LoadedJob::PhysicalServer(config.clone()))
            }
            _ => Err(Error::Unsupported(
                "agent profile backend unavailable".into(),
            )),
        }
    }

    async fn fact(&mut self, fact: JobMessage) {
        match fact {
            JobMessage::Begin {
                job_id,
                role,
                device_key,
                principal,
                reply,
            } => {
                let result = self.begin(job_id, role, device_key, principal).await;
                let _ = reply.send(result);
            }
            JobMessage::Attempt {
                session_id,
                kind,
                reply,
            } => {
                let result = self.attempt(session_id, kind).await;
                let _ = reply.send(result);
            }
            JobMessage::Resource {
                correlation,
                resource_id,
                reply,
            } => {
                let result = self.resource(correlation, resource_id).await;
                let _ = reply.send(result);
            }
            JobMessage::Observe {
                session_id,
                state,
                evidence,
                metrics,
                reply,
            } => {
                let result = self.observe(session_id, state, evidence, metrics).await;
                let _ = reply.send(result);
            }
            JobMessage::Finish {
                session_id,
                operation_error,
                cleanup,
                metrics,
                reply,
            } => {
                let result = self
                    .finish(session_id, operation_error, cleanup, metrics)
                    .await;
                let _ = reply.send(result);
            }
        }
    }

    async fn begin(
        &mut self,
        job_id: JobId,
        role: SessionRole,
        device_key: rustusb_core::DeviceKey,
        principal: Option<String>,
    ) -> Result<SessionContext> {
        let job = self
            .state
            .jobs
            .iter()
            .find(|j| j.id == job_id && j.instance_id == self.instance)
            .cloned()
            .ok_or_else(|| Error::State("session has no current job".into()))?;
        if job.stop_requested || job.state == JobState::Stopping {
            return Err(Error::State("job is stopping".into()));
        }
        let configured = match self.config.credentials.get(&job.profile) {
            Some(Credentials::Client(c)) => role == SessionRole::Import && c.device == device_key,
            Some(Credentials::Server(s)) => {
                role != SessionRole::Import && s.devices.contains(&device_key)
            }
            None => false,
        };
        if !configured
            || principal
                .as_ref()
                .is_some_and(|p| crate::config::valid_label(p).is_err())
        {
            return Err(denied());
        }
        let id = SessionId::new(fresh_id()?)?;
        let session = Session {
            id: id.clone(),
            instance_id: self.instance.clone(),
            job_id: job_id.clone(),
            profile: job.profile.clone(),
            role,
            device_key,
            principal,
            state: if role == SessionRole::Export {
                SessionState::Exporting
            } else {
                SessionState::Connecting
            },
            evidence: Evidence::Intent,
            stop_requested: false,
            terminal: false,
            operation_error: None,
            cleanup: None,
            metrics: Metrics::default(),
            attempts: vec![],
        };
        let mut next = self.state.clone();
        next.link_history.push(crate::health::LinkHistory {
            session_id: id.clone(),
            instance_id: self.instance.clone(),
            link: None,
        });
        next.sessions.push(session);
        if let Some(job) = next.jobs.iter_mut().find(|j| j.id == job_id) {
            job.state = JobState::Running;
        }
        next.audit(
            self.config.manifest.broker_uid,
            Some(job.profile.clone()),
            Some(job_id.clone()),
            Some(id.clone()),
            AuditAction::Admission,
            None,
        )?;
        next.preflight(true)?;
        self.persist(next).await?;
        let stoppable = role != SessionRole::Export
            || self
                .config
                .manifest
                .profiles
                .iter()
                .any(|p| p.id == job.profile && p.backend == Backend::Native);
        let (sender, receiver) = watch::channel(false);
        self.controls
            .lock()
            .map_err(|_| lock_error())?
            .register_session(
                id.clone(),
                Control {
                    profile: job.profile,
                    job_id,
                    stop: sender,
                    stoppable,
                },
            );
        let health = crate::health::HealthReporter::default();
        if let Some(entry) = self
            .controls
            .lock()
            .map_err(|_| lock_error())?
            .health
            .get_mut(&id)
        {
            entry.reporter = Some(health.clone());
        }
        Ok(SessionContext {
            health,
            id,
            stop: receiver,
            facts: self.fact_sender.clone(),
        })
    }

    async fn attempt(
        &mut self,
        id: SessionId,
        kind: BrokerResourceKind,
    ) -> Result<BrokerCorrelation> {
        let mut next = self.state.clone();
        let session = next
            .sessions
            .iter_mut()
            .find(|s| s.id == id && s.instance_id == self.instance && !s.terminal)
            .ok_or_else(|| Error::State("attempt has no current owner".into()))?;
        if session.stop_requested || session.attempts.len() >= 32 {
            return Err(Error::State(
                "session stopping or attempt capacity exhausted".into(),
            ));
        }
        let allowed = matches!(
            (session.role, kind),
            (SessionRole::Import, BrokerResourceKind::NativeImport)
                | (
                    SessionRole::Export,
                    BrokerResourceKind::NativeExport | BrokerResourceKind::UsbfsGrant
                )
                | (SessionRole::Incoming, BrokerResourceKind::NativeStub)
        );
        if !allowed {
            return Err(Error::State(
                "helper resource kind does not match session role".into(),
            ));
        }
        let correlation = BrokerCorrelation {
            instance_id: self.instance.clone(),
            session_id: id.clone(),
            attempt_id: AttemptId::new(fresh_id()?)?,
        };
        correlation.validate()?;
        session.attempts.push(Attempt {
            kind,
            correlation: correlation.clone(),
            resource_id: None,
            proof: None,
        });
        let profile = session.profile.clone();
        let job_id = session.job_id.clone();
        next.audit(
            self.config.manifest.broker_uid,
            Some(profile),
            Some(job_id),
            Some(id),
            AuditAction::Attempt,
            None,
        )?;
        next.preflight(true)?;
        self.persist(next).await?;
        Ok(correlation)
    }

    async fn resource(
        &mut self,
        correlation: BrokerCorrelation,
        resource_id: ResourceId,
    ) -> Result<()> {
        let mut next = self.state.clone();
        let session = next
            .sessions
            .iter_mut()
            .find(|s| {
                s.id == correlation.session_id && s.instance_id == self.instance && !s.terminal
            })
            .ok_or_else(|| Error::State("resource has no current owner".into()))?;
        let attempt = session
            .attempts
            .iter_mut()
            .find(|a| a.correlation == correlation)
            .ok_or_else(|| Error::State("resource has no durable attempt".into()))?;
        if attempt
            .resource_id
            .as_ref()
            .is_some_and(|id| *id != resource_id)
        {
            return Err(Error::State("resource attempt identity changed".into()));
        }
        attempt.resource_id = Some(resource_id);
        let profile = session.profile.clone();
        let job_id = session.job_id.clone();
        let id = session.id.clone();
        next.audit(
            self.config.manifest.broker_uid,
            Some(profile),
            Some(job_id),
            Some(id),
            AuditAction::Resource,
            None,
        )?;
        self.persist(next).await
    }

    async fn observe(
        &mut self,
        id: SessionId,
        state: SessionState,
        evidence: Evidence,
        metrics: Option<Metrics>,
    ) -> Result<()> {
        let mut next = self.state.clone();
        let session = next
            .sessions
            .iter_mut()
            .find(|s| s.id == id && s.instance_id == self.instance && !s.terminal)
            .ok_or_else(|| Error::State("observation has no current owner".into()))?;
        if state == SessionState::Connected
            && !matches!(
                (session.role, evidence),
                (SessionRole::Import, Evidence::LocalEnumeration)
                    | (SessionRole::Incoming, Evidence::ImportReplyFlushed)
            )
            || state == SessionState::Available
                && (session.role != SessionRole::Export || evidence != Evidence::ExportReady)
            || state == SessionState::Disconnected
        {
            return Err(Error::State(
                "session phase lacks role-specific actual evidence".into(),
            ));
        }
        session.state = session.state.transition(state)?;
        session.evidence = evidence;
        if let Some(metrics) = metrics {
            check_metrics(session.metrics, metrics)?;
            session.metrics = metrics;
        }
        let profile = session.profile.clone();
        let job_id = session.job_id.clone();
        next.audit(
            self.config.manifest.broker_uid,
            Some(profile),
            Some(job_id),
            Some(id),
            AuditAction::Observe,
            None,
        )?;
        self.persist(next).await
    }

    async fn finish(
        &mut self,
        id: SessionId,
        operation_error: Option<Failure>,
        cleanup: Cleanup,
        metrics: Metrics,
    ) -> Result<()> {
        let mut next = self.state.clone();
        let session = next
            .sessions
            .iter_mut()
            .find(|s| s.id == id && s.instance_id == self.instance)
            .ok_or_else(|| Error::State("finish has no current owner".into()))?;
        if session.terminal {
            return if session.cleanup == Some(cleanup) && session.operation_error == operation_error
            {
                Ok(())
            } else {
                Err(Error::State("conflicting terminal observation".into()))
            };
        }
        if cleanup == Cleanup::NotStarted && !session.attempts.is_empty() {
            return Err(Error::State(
                "reserved helper attempt requires actual release evidence".into(),
            ));
        }
        check_metrics(session.metrics, metrics)?;
        let profile = session.profile.clone();
        let job_id = session.job_id.clone();
        if cleanup != Cleanup::Unconfirmed {
            session.state = if session.state == SessionState::Error {
                session.state.transition(SessionState::Recovering)?
            } else {
                session.state.transition(SessionState::Disconnecting)?
            };
            session.evidence = Evidence::Closing;
            next.audit(
                self.config.manifest.broker_uid,
                Some(profile.clone()),
                Some(job_id.clone()),
                Some(id.clone()),
                AuditAction::Observe,
                operation_error,
            )?;
        }
        let session = next
            .sessions
            .iter_mut()
            .find(|s| s.id == id)
            .ok_or_else(|| {
                Error::State("session disappeared during terminal observation".into())
            })?;
        session.metrics = metrics;
        session.operation_error = operation_error;
        session.cleanup = Some(cleanup);
        session.terminal = true;
        session.state = if cleanup == Cleanup::Unconfirmed {
            SessionState::Error
        } else {
            session.state.transition(SessionState::Disconnected)?
        };
        session.evidence = if cleanup == Cleanup::Unconfirmed {
            Evidence::Unconfirmed
        } else {
            Evidence::Released
        };
        next.audit(
            self.config.manifest.broker_uid,
            Some(profile),
            Some(job_id),
            Some(id.clone()),
            AuditAction::Finish,
            operation_error,
        )?;
        self.controls
            .lock()
            .map_err(|_| lock_error())?
            .sessions
            .remove(&id);
        self.persist(next).await
    }

    async fn job_finished(&mut self, id: JobId, result: Result<()>) {
        if let Ok(mut controls) = self.controls.lock() {
            controls.jobs.remove(&id);
            controls.sessions.retain(|_, c| c.job_id != id);
        }
        let mut next = self.state.clone();
        for session in next
            .sessions
            .iter_mut()
            .filter(|s| s.job_id == id && !s.terminal)
        {
            session.state = SessionState::Error;
            session.evidence = Evidence::Unconfirmed;
            session.cleanup = Some(Cleanup::Unconfirmed);
            session.terminal = true;
        }
        let unresolved = next
            .sessions
            .iter()
            .any(|s| s.job_id == id && s.cleanup == Some(Cleanup::Unconfirmed));
        if let Some(job) = next.jobs.iter_mut().find(|j| j.id == id) {
            job.operation_error = result.as_ref().err().map(Failure::from_error);
            job.state = if unresolved {
                JobState::Recovering
            } else {
                JobState::Finished
            };
            let profile = job.profile.clone();
            let failure = job.operation_error;
            if next
                .audit(
                    self.config.manifest.broker_uid,
                    Some(profile),
                    Some(id),
                    None,
                    AuditAction::Finish,
                    failure,
                )
                .is_ok()
            {
                let _ = self.persist(next).await;
            }
        }
    }

    fn start_recovery(&mut self) {
        let Ok(controls) = self.controls.lock() else {
            return;
        };
        let pending: Vec<_> = self
            .state
            .sessions
            .iter()
            .filter(|s| {
                !controls.jobs.contains_key(&s.job_id)
                    && !matches!(s.cleanup, Some(Cleanup::Verified | Cleanup::NotStarted))
                    && !s.attempts.is_empty()
            })
            .cloned()
            .collect();
        drop(controls);
        if pending.is_empty() {
            return;
        }
        let index = self.recovery_cursor % pending.len();
        self.recovery_cursor = self.recovery_cursor.saturating_add(1);
        let session = pending[index].clone();
        let socket = self.config.manifest.helper_socket.clone();
        self.recoveries.spawn(async move {
            let mut statuses = vec![];
            for attempt in &session.attempts {
                if let Ok(Ok(status)) = tokio::time::timeout(
                    Duration::from_secs(1),
                    rustusb_daemon::inspect_broker_operation(
                        &socket,
                        attempt.kind,
                        attempt.correlation.clone(),
                    ),
                )
                .await
                {
                    statuses.push(status);
                }
            }
            (session.id, statuses)
        });
    }

    async fn recovered(&mut self, id: SessionId, statuses: Vec<BrokerOperationStatus>) {
        let Some(old) = self.state.sessions.iter().find(|s| s.id == id) else {
            return;
        };
        if self
            .controls
            .lock()
            .map_or(true, |controls| controls.jobs.contains_key(&old.job_id))
        {
            return;
        }
        let mut next = self.state.clone();
        let Some(session) = next.sessions.iter_mut().find(|s| s.id == id) else {
            return;
        };
        let mut changed = false;
        for attempt in &mut session.attempts {
            let Some(status) = statuses.iter().find(|s| {
                s.schema_version == 1
                    && s.kind == attempt.kind
                    && s.correlation == attempt.correlation
            }) else {
                continue;
            };
            if let BrokerOperationState::Terminal { terminal } = &status.operation
                && terminal.kind == attempt.kind
                && terminal.correlation == attempt.correlation
                && terminal.device_key == session.device_key
                && terminal.owner_uid == self.config.manifest.broker_uid
                && attempt
                    .resource_id
                    .as_ref()
                    .is_none_or(|id| *id == terminal.resource_id)
                && attempt.proof != Some(terminal.proof)
            {
                attempt.proof = Some(terminal.proof);
                attempt.resource_id = Some(terminal.resource_id.clone());
                changed = true;
            }
        }
        if !changed {
            return;
        }
        if session.attempts.iter().all(|a| a.proof.is_some()) {
            session.state = SessionState::Disconnected;
            session.evidence = Evidence::RootJournalProof;
            session.cleanup = Some(Cleanup::Verified);
            session.terminal = true;
        }
        let profile = session.profile.clone();
        let job_id = session.job_id.clone();
        if next
            .sessions
            .iter()
            .filter(|s| s.job_id == job_id)
            .all(|s| matches!(s.cleanup, Some(Cleanup::Verified | Cleanup::NotStarted)))
            && let Some(job) = next.jobs.iter_mut().find(|j| j.id == job_id)
        {
            job.state = JobState::Finished;
        }
        if next
            .audit(
                self.config.manifest.broker_uid,
                Some(profile),
                Some(job_id),
                Some(id),
                AuditAction::Recovery,
                None,
            )
            .is_ok()
        {
            let _ = self.persist(next).await;
        }
    }
}
fn project_audit(instance: &InstanceId, event: &AuditEvent) -> rustusb_logging::AuditProjection {
    use rustusb_logging as logging;
    logging::AuditProjection {
        application_instance: instance.clone(),
        audit_sequence: event.sequence,
        actor_uid: event.actor_uid,
        profile: event.profile.clone(),
        job_id: event.job_id.clone(),
        session_id: event.session_id.clone(),
        action: match event.action {
            AuditAction::Start => logging::AuditAction::Start,
            AuditAction::Denied => logging::AuditAction::Denied,
            AuditAction::Stop => logging::AuditAction::Stop,
            AuditAction::Admission => logging::AuditAction::Admission,
            AuditAction::Attempt => logging::AuditAction::Attempt,
            AuditAction::Resource => logging::AuditAction::Resource,
            AuditAction::Observe => logging::AuditAction::Observe,
            AuditAction::Finish => logging::AuditAction::Finish,
            AuditAction::Recovery => logging::AuditAction::Recovery,
            AuditAction::Discover => logging::AuditAction::Discover,
        },
        failure: event.failure.map(|failure| match failure {
            Failure::Permission => logging::FailureCategory::Permission,
            Failure::InvalidInput => logging::FailureCategory::InvalidInput,
            Failure::Io => logging::FailureCategory::Io,
            Failure::Timeout => logging::FailureCategory::Timeout,
            Failure::State => logging::FailureCategory::State,
            Failure::Unsupported => logging::FailureCategory::Unsupported,
            Failure::Cancelled => logging::FailureCategory::Cancelled,
            Failure::Persistence => logging::FailureCategory::Persistence,
        }),
        state: event.state,
        evidence: event.evidence.map(|evidence| match evidence {
            Evidence::Intent => logging::Evidence::Intent,
            Evidence::Prepared => logging::Evidence::Prepared,
            Evidence::LocalEnumeration => logging::Evidence::LocalEnumeration,
            Evidence::ExportReady => logging::Evidence::ExportReady,
            Evidence::ImportReplyFlushed => logging::Evidence::ImportReplyFlushed,
            Evidence::Closing => logging::Evidence::Closing,
            Evidence::Released => logging::Evidence::Released,
            Evidence::Unconfirmed => logging::Evidence::Unconfirmed,
            Evidence::RootJournalProof => logging::Evidence::RootJournalProof,
        }),
        cleanup: event.cleanup.map(|cleanup| match cleanup {
            Cleanup::NotStarted => logging::Cleanup::NotStarted,
            Cleanup::Verified => logging::Cleanup::Verified,
            Cleanup::Unconfirmed => logging::Cleanup::Unconfirmed,
        }),
    }
}
fn denied() -> Error {
    Error::Permission("agent profile action denied".into())
}
fn lock_error() -> Error {
    Error::State("agent control lock poisoned".into())
}
fn check_metrics(before: Metrics, after: Metrics) -> Result<()> {
    if after.app_rx_bytes < before.app_rx_bytes
        || after.app_tx_bytes < before.app_tx_bytes
        || after.elapsed_millis < before.elapsed_millis
        || before.saturated && !after.saturated
    {
        return Err(Error::State("session metrics moved backwards".into()));
    }
    Ok(())
}

#[cfg(test)]
#[path = "runtime_tests.rs"]
mod tests;

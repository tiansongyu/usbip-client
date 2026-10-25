use crate::{Settings, state::*};
use rustusb_core::{
    Error, Result,
    application::{JobId, RequestId, RuleId, SessionId},
};
use rustusb_discovery::{DeviceMonitor, DeviceSubscription};
use rustusb_service::{
    catalog::{CatalogMonitor, CatalogSource, CatalogSubscription, RefreshInterval},
    config::{Action, Backend, Manifest},
    ipc,
    model::Failure,
    rpc::{Request, Response, ResponseEnvelope},
};
use std::{sync::Arc, time::Duration};
use tokio::sync::{mpsc, watch};

struct Worker {
    manifest: Arc<Manifest>,
    view: Snapshot,
    sender: watch::Sender<Snapshot>,
    stop: watch::Receiver<bool>,
    job_after: Option<JobId>,
    session_after: Option<SessionId>,
    audit_after: u64,
    rule_after: Option<RuleId>,
    rule_event_after: u64,
    remote_page: bool,
    remote: Option<CatalogMonitor>,
    subscription: Option<CatalogSubscription>,
    join_error: Option<Error>,
    settings: Option<Settings>,
}
impl Worker {
    fn publish(&mut self) {
        self.view.revision = self.view.revision.saturating_add(1);
        self.sender.send_replace(self.view.clone());
    }
    fn allowed(&self, action: Action) -> bool {
        self.view.profile.as_ref().is_some_and(|p| {
            self.manifest
                .authorize(rustix::process::geteuid().as_raw(), p, action)
                .is_ok()
        })
    }
    async fn read(&mut self, request: Request) -> Result<Response> {
        let response = tokio::select! {
            biased;
            _=stopped(&mut self.stop)=>return Err(Error::State("frontend stopping".into())),
            result=ipc::call(&self.manifest,request)=>result?,
        };
        self.view.journal = Some(response.journal_healthy);
        match response.response {
            Response::Error { code } => Err(failure(code)),
            other => Ok(other),
        }
    }
    async fn refresh(&mut self) {
        match self.read(Request::Profiles).await {
            Ok(Response::Profiles { profiles }) => {
                if profiles.len() > 16 {
                    self.view.status = "Invalid profile count".into();
                    return;
                }
                self.view.profiles = profiles;
                if !self
                    .view
                    .profiles
                    .iter()
                    .any(|p| Some(&p.id) == self.view.profile.as_ref())
                {
                    self.view.profile = self.view.profiles.first().map(|p| p.id.clone());
                    self.reset().await;
                }
                self.view.status = "Agent reachable; section samples are independent".into();
            }
            Err(error) => {
                self.view.status =
                    format!("Agent stale/unavailable: {:?}", Failure::from_error(&error));
                if matches!(error, Error::Permission(_) | Error::Protocol(_)) {
                    self.view.profiles.clear();
                    self.view.profile = None;
                    self.reset().await;
                } else {
                    self.view.agent_stale();
                }
                return;
            }
            _ => {
                self.view.status = "Unexpected profile response".into();
                return;
            }
        }
        let Some(profile) = self.view.profile.clone() else {
            return;
        };
        match self
            .read(Request::Jobs {
                profile: profile.clone(),
                after: self.job_after.clone(),
            })
            .await
        {
            Ok(Response::Jobs { jobs, next_after }) if jobs.len() <= 16 => {
                self.view.jobs = jobs;
                self.view.next_job = next_after;
                self.view.jobs_status = "Current page (up to 16)".into();
            }
            result => {
                if denied(&result) {
                    self.view.jobs.clear();
                    self.view.next_job = None;
                }
                self.view.jobs_status = read_status(&result);
            }
        }
        match self
            .read(Request::Sessions {
                profile: profile.clone(),
                after: self.session_after.clone(),
            })
            .await
        {
            Ok(Response::Sessions {
                sessions,
                next_after,
            }) if sessions.len() <= 16 => {
                self.view.sessions = sessions;
                self.view.next_session = next_after;
                self.view.sessions_status = "Current page (up to 16)".into();
            }
            result => {
                if denied(&result) {
                    self.view.sessions.clear();
                    self.view.next_session = None;
                }
                self.view.sessions_status = read_status(&result);
            }
        }
        // Health uses the same cursor but is independently sampled and explicitly qualified.
        match self
            .read(Request::SessionHealth {
                profile: profile.clone(),
                after: self.session_after.clone(),
            })
            .await
        {
            Ok(Response::SessionHealth { sessions, .. }) if sessions.len() <= 16 => {
                self.view.health = sessions
            }
            _ => self.view.health.clear(),
        }
        if self.allowed(Action::Audit) {
            match self
                .read(Request::Audit {
                    profile: profile.clone(),
                    after: self.audit_after,
                })
                .await
            {
                Ok(Response::Events {
                    events,
                    next_after,
                    gap,
                    oldest_available,
                }) if events.len() <= 16 => {
                    self.view.next_audit =
                        (!events.is_empty() && next_after > self.audit_after).then_some(next_after);
                    self.view.audit = events;
                    self.view.audit_status =
                        format!("Audit page; gap={gap}; oldest={oldest_available}");
                }
                result => {
                    if denied(&result) {
                        self.view.audit.clear();
                        self.view.next_audit = None;
                    }
                    self.view.audit_status = read_status(&result);
                }
            }
        } else {
            self.view.audit.clear();
            self.view.next_audit = None;
            self.view.audit_status = "Audit permission unavailable".into();
        }
        self.refresh_rules().await;
        self.ensure_remote().await;
    }
    async fn refresh_rules(&mut self) {
        if !self.view.rules.requested {
            return;
        }
        let Some(profile) = self.view.profile.clone() else {
            return;
        };
        self.view.rules.current = false;
        match self
            .read(Request::Rules {
                profile: profile.clone(),
                after: self.rule_after.clone(),
            })
            .await
        {
            Ok(Response::Rules {
                policy_enabled,
                grant,
                rules,
                next_after,
            }) if rules.len() <= 16 => {
                self.view.rules.policy_enabled = Some(policy_enabled);
                self.view.rules.grant = grant;
                self.view.rules.rules = rules;
                self.view.rules.next_rule = next_after;
                self.view.rules.current = true;
                self.view.rules.status = if !policy_enabled {
                    "Policy disabled by administrator"
                } else if self.view.rules.grant.is_none() {
                    "No automation grant for this OS peer/profile"
                } else {
                    "Current rule page (up to 16); whole fixed profile"
                }
                .into();
            }
            result => {
                if denied(&result) {
                    self.view.rules.grant = None;
                    self.view.rules.rules.clear();
                    self.view.rules.next_rule = None;
                    self.view.rules.policy_enabled = None;
                }
                self.view.rules.status = read_status(&result);
            }
        }
        if !self.allowed(Action::Audit) {
            self.view.rules.events.clear();
            self.view.rules.next_event = None;
            self.view.rules.events_status = "Rule audit permission unavailable".into();
            return;
        }
        match self
            .read(Request::RuleEvents {
                profile,
                after: self.rule_event_after,
            })
            .await
        {
            Ok(Response::RuleEvents {
                events,
                next_after,
                oldest_available,
                gap,
            }) if events.len() <= 16 => {
                self.view.rules.next_event = (!events.is_empty()
                    && next_after > self.rule_event_after)
                    .then_some(next_after);
                self.view.rules.events = events;
                self.view.rules.events_status =
                    format!("Rule audit page; gap={gap}; oldest={oldest_available}");
            }
            result => {
                if denied(&result) {
                    self.view.rules.events.clear();
                    self.view.rules.next_event = None;
                }
                self.view.rules.events_status = read_status(&result);
            }
        }
    }
    async fn reset(&mut self) {
        self.close_remote().await;
        self.job_after = None;
        self.session_after = None;
        self.audit_after = 0;
        self.rule_after = None;
        self.rule_event_after = 0;
        self.view.clear_profile();
    }
    async fn close_remote(&mut self) {
        self.subscription = None;
        if let Some(owner) = self.remote.take()
            && let Err(error) = owner.shutdown().await
        {
            self.view.remote_status =
                format!("Observer join failed: {:?}", Failure::from_error(&error));
            if self.join_error.is_none() {
                self.join_error = Some(error);
            }
        }
    }
    async fn ensure_remote(&mut self) {
        let client = self
            .view
            .profiles
            .iter()
            .any(|p| Some(&p.id) == self.view.profile.as_ref() && p.backend == Backend::Client);
        if !self.remote_page
            || !self.view.options.auto_refresh
            || !client
            || !self.allowed(Action::Discover)
            || *self.stop.borrow()
        {
            self.close_remote().await;
            self.view.remote_status =
                "Paused, off-page, non-client profile or Discover unavailable".into();
            return;
        }
        if self.remote.is_none() {
            let result = (|| {
                let source = CatalogSource::agent(
                    self.manifest.clone(),
                    self.view
                        .profile
                        .clone()
                        .ok_or_else(|| Error::State("no profile".into()))?,
                )?;
                CatalogMonitor::start(
                    source,
                    RefreshInterval::new(self.view.options.refresh_seconds)?,
                )
            })();
            match result {
                Ok(owner) => {
                    self.subscription = Some(owner.subscribe());
                    self.remote = Some(owner);
                    self.view.remote_status = "Initializing".into();
                }
                Err(e) => {
                    self.view.remote_status =
                        format!("Remote unavailable: {:?}", Failure::from_error(&e))
                }
            }
        }
    }
    async fn command(&mut self, envelope: Envelope) {
        match envelope.command {
            Command::Profile(profile) => {
                if self.view.profiles.iter().any(|p| p.id == profile) {
                    self.reset().await;
                    self.view.profile = Some(profile);
                    self.refresh().await;
                }
            }
            Command::Refresh => self.refresh().await,
            Command::Remote(active) => {
                self.remote_page = active;
                self.view.remote_page = active;
                self.ensure_remote().await;
            }
            Command::Rules(active) => {
                self.view.rules.requested = active;
                if active {
                    self.refresh_rules().await;
                } else {
                    self.view.rules.current = false;
                    self.view.rules.status =
                        "Paused: Settings is off-page; retained metadata".into();
                }
            }
            Command::Observe { remote, rules } => {
                self.remote_page = remote;
                self.view.remote_page = remote;
                self.view.rules.requested = rules;
                self.ensure_remote().await;
                if rules {
                    self.refresh_rules().await;
                } else {
                    self.view.rules.current = false;
                    self.view.rules.status =
                        "Paused: Settings is off-page; retained metadata".into();
                }
            }
            Command::Options(options) => {
                self.view.options = options;
                self.close_remote().await;
                self.ensure_remote().await;
            }
            Command::SaveSettings | Command::ReloadSettings => {
                let reload = matches!(envelope.command, Command::ReloadSettings);
                if !*self.stop.borrow() {
                    self.settings_work(reload).await;
                }
            }
            Command::Next(kind) => {
                match kind {
                    PageKind::Jobs => {
                        if let Some(cursor) = self.view.next_job.clone() {
                            self.job_after = Some(cursor)
                        }
                    }
                    PageKind::Sessions => {
                        if let Some(cursor) = self.view.next_session.clone() {
                            self.session_after = Some(cursor)
                        }
                    }
                    PageKind::Audit => {
                        if let Some(cursor) = self.view.next_audit {
                            self.audit_after = cursor
                        }
                    }
                    PageKind::Rules => {
                        if let Some(cursor) = self.view.rules.next_rule.clone() {
                            self.rule_after = Some(cursor);
                        }
                    }
                    PageKind::RuleEvents => {
                        if let Some(cursor) = self.view.rules.next_event {
                            self.rule_event_after = cursor;
                        }
                    }
                }
                self.refresh().await;
            }
            Command::First(kind) => {
                match kind {
                    PageKind::Jobs => self.job_after = None,
                    PageKind::Sessions => self.session_after = None,
                    PageKind::Audit => self.audit_after = 0,
                    PageKind::Rules => self.rule_after = None,
                    PageKind::RuleEvents => self.rule_event_after = 0,
                };
                self.refresh().await;
            }
            Command::Mutate(action) => {
                if !*self.stop.borrow() {
                    self.mutate(action).await;
                }
            }
        }
        self.view.acknowledged = envelope.sequence;
        self.publish();
    }
    async fn settings_work(&mut self, reload: bool) {
        let Some(mut settings) = self.settings.clone() else {
            self.view.preferences_status =
                "Preferences disabled; use a user configuration directory".into();
            self.view.status = self.view.preferences_status.clone();
            return;
        };
        let options = self.view.options;
        // Accepted filesystem work is retained through signals/quit, never raced away.
        let result = tokio::task::spawn_blocking(move || {
            if reload {
                settings = Settings::load(settings.directory().to_owned())?;
            } else {
                settings.save(options)?;
            }
            Ok::<_, Error>(settings)
        })
        .await;
        match result {
            Ok(Ok(settings)) => {
                self.view.saved_options = settings.saved();
                if reload {
                    self.view.options = settings.options();
                }
                self.settings = Some(settings);
                self.view.preferences_status = if reload {
                    "Reloaded checked preferences"
                } else {
                    "Saved preferences"
                }
                .into();
                if reload {
                    self.close_remote().await;
                    self.ensure_remote().await;
                }
            }
            Ok(Err(error)) => {
                self.view.preferences_status =
                    format!("{} failed: {error}", if reload { "Reload" } else { "Save" })
            }
            Err(_) => {
                self.view.preferences_status =
                    "Preferences worker failed; inspect file before retry".into()
            }
        }
        self.view.status = self.view.preferences_status.clone();
    }
    async fn mutate(&mut self, action: Mutation) {
        let is_rule = matches!(&action, Mutation::Rule { .. } | Mutation::ResolveRule);
        if matches!(&action, Mutation::Rule { .. }) && self.view.unresolved_rule.is_some() {
            self.view.rules.result =
                "Original rule edit unresolved; query only its original UUID".into();
            self.view.status = self.view.rules.result.clone();
            return;
        }
        let needs_id = (matches!(&action, Mutation::Start(_)) && self.view.unresolved.is_none())
            || matches!(&action, Mutation::Rule { .. });
        let fresh = if needs_id {
            match tokio::task::spawn_blocking(|| {
                RequestId::new(std::fs::read_to_string("/proc/sys/kernel/random/uuid")?.trim_end())
            })
            .await
            {
                Ok(Ok(id)) => Some(id),
                _ => {
                    self.view.status =
                        "Could not generate request identity; no request issued".into();
                    return;
                }
            }
        } else {
            None
        };
        if *self.stop.borrow() {
            return;
        }
        let Some(request) = self.view.mutation_request(action, fresh) else {
            return;
        };
        self.view.status =
            "Request issued; result pending; service jobs outlive this frontend".into();
        if is_rule {
            self.view.rules.result = self.view.status.clone();
            self.view.rules.current = false;
            self.view.rules.status =
                "Rule edit/query issued; refresh current rules before a new edit".into();
        }
        self.publish();
        // Deliberately do not race an issued mutation against frontend shutdown.
        match ipc::call(&self.manifest, request).await {
            Ok(ResponseEnvelope {
                journal_healthy,
                response,
                ..
            }) => {
                self.view.journal = Some(journal_healthy);
                if is_rule {
                    self.view.rule_response(response);
                } else {
                    self.view.mutation_response(response);
                }
            }
            Err(error) => {
                self.view.status = format!(
                    "Response unknown ({:?}); inspect jobs; use only original retry identity",
                    Failure::from_error(&error)
                );
                if is_rule {
                    self.view.rules.result = self.view.status.clone();
                }
            }
        }
    }
}
fn failure(code: Failure) -> Error {
    match code {
        Failure::Permission => Error::Permission("agent denied".into()),
        Failure::Unsupported => Error::Unsupported("agent operation unavailable".into()),
        _ => Error::State(format!("agent {code:?}")),
    }
}
fn denied<T>(result: &Result<T>) -> bool {
    matches!(result, Err(Error::Permission(_) | Error::Protocol(_)))
}
fn read_status<T>(result: &Result<T>) -> String {
    match result {
        Err(e) => format!("Stale/unavailable: {:?}", Failure::from_error(e)),
        Ok(_) => "Invalid response; old page retained as stale".into(),
    }
}
async fn stopped(stop: &mut watch::Receiver<bool>) {
    loop {
        if *stop.borrow() {
            return;
        }
        if stop.changed().await.is_err() {
            return;
        }
    }
}
async fn local_next(
    local: &mut Option<DeviceSubscription>,
) -> Result<rustusb_discovery::DeviceUpdate> {
    match local {
        Some(sub) => sub.next().await,
        None => std::future::pending().await,
    }
}
async fn remote_next(
    remote: &mut Option<CatalogSubscription>,
) -> Result<rustusb_service::catalog::CatalogUpdate> {
    match remote {
        Some(sub) => sub.next().await,
        None => std::future::pending().await,
    }
}
/// Run the single shared worker until sticky stop or command-channel closure.
/// Callers retain and await this future through shutdown; an issued mutation and
/// accepted filesystem work are never discarded. Inputs must be bounded/validated
/// by the frontend. Every actual request is independently authorized by the agent.
pub async fn run(
    manifest: Arc<Manifest>,
    mut requests: mpsc::Receiver<Envelope>,
    sender: watch::Sender<Snapshot>,
    stop: watch::Receiver<bool>,
    settings: Option<Settings>,
) -> Result<()> {
    let mut worker = Worker {
        manifest,
        view: sender.borrow().clone(),
        sender: sender.clone(),
        stop,
        job_after: None,
        session_after: None,
        audit_after: 0,
        rule_after: None,
        rule_event_after: 0,
        remote_page: false,
        remote: None,
        subscription: None,
        join_error: None,
        settings,
    };
    if *worker.stop.borrow() {
        return Ok(());
    }
    let owner = DeviceMonitor::open().await;
    let mut local = owner.as_ref().ok().map(DeviceMonitor::subscribe);
    if let Err(error) = &owner {
        worker.view.local_status = format!("Local unavailable: {:?}", Failure::from_error(error));
    }
    worker.refresh().await;
    worker.publish();
    loop {
        let delay = if worker.view.options.auto_refresh {
            Duration::from_secs(worker.view.options.refresh_seconds)
        } else {
            Duration::from_secs(86400)
        };
        let timer = tokio::time::sleep(delay);
        tokio::pin!(timer);
        // Preserve this deadline across discovery notifications and commands.
        loop {
            tokio::select! {
                biased;
                _=stopped(&mut worker.stop)=>break,
                command=requests.recv()=>match command{Some(envelope)=>{worker.command(envelope).await;break;},None=>break},
                update=local_next(&mut local)=>{match update{Ok(update)=>{worker.view.local_status=format!("{:?}; revision {}; resync={}",update.snapshot.state,update.snapshot.revision,update.resync_required);worker.view.local=Some(update.snapshot);},Err(_)=>{worker.view.local_status="Local observer ended; retained view is stale".into();local=None;}}worker.publish();},
                update=remote_next(&mut worker.subscription)=>{match update{Ok(update)=>{worker.view.remote_status=format!("{:?}; revision {}; resync={}",update.snapshot.state,update.snapshot.revision,update.resync_required);worker.view.remote=Some(update.snapshot);},Err(_)=>{worker.subscription=None;worker.view.remote_status="Remote observer ended; retained view is stale".into();}}worker.publish();},
                _=&mut timer=>{if worker.view.options.auto_refresh{worker.refresh().await;worker.publish();}break;},
            }
            if *worker.stop.borrow() || requests.is_closed() {
                break;
            }
        }
        if *worker.stop.borrow() || requests.is_closed() {
            break;
        }
    }
    worker.close_remote().await;
    let local_result = match owner {
        Ok(owner) => owner.shutdown().await,
        Err(_) => Ok(()),
    };
    let mut unresolved = Vec::new();
    if let Some(intent) = worker.view.unresolved {
        unresolved.push(format!(
            "start outcome unresolved: profile {} request_id {}; reconcile jobs or use agent start --retry --request-id",
            intent.profile, intent.request_id
        ));
    }
    if let Some(intent) = worker.view.unresolved_rule {
        unresolved.push(format!(
            "rule edit outcome unresolved: profile {} request_id {}; reconcile rules/events or use agent rule retry; no edit resubmitted",
            intent.profile, intent.request_id
        ));
    }
    if !unresolved.is_empty() {
        return Err(Error::State(unresolved.join("; ")));
    }
    local_result?;
    worker.join_error.map_or(Ok(()), Err)
}

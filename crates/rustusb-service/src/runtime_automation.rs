//! Agent-owned rule metadata, admission and observations. No detached USB owner.

use super::*;
use crate::{automation::*, rules::RuleRecord};
use rustusb_core::DeviceKey;
use std::collections::BTreeSet;
use tokio::time::Instant;

#[derive(Default)]
pub(super) struct Automation {
    policy: Option<Arc<AutomationPolicy>>,
    boot: Option<InstanceId>,
    local: Option<crate::discovery::DeviceSubscription>,
    local_failed: bool,
    local_error: Option<Failure>,
    candidates: BTreeMap<RuleId, Candidate>,
    remote: BTreeMap<RuleId, RemoteObservation>,
    remote_inflight: Option<(RuleId, u64)>,
    cursor: Option<RuleId>,
    failed_admissions: BTreeMap<RuleId, (u64, Failure)>,
    observer_error: bool,
}

struct Candidate {
    revision: u64,
    token: ObservationToken,
    since: Instant,
}
struct RemoteObservation {
    revision: u64,
    status: RuleStatus,
    error: Option<Failure>,
    next_poll: Instant,
    backoff: u64,
    terminal: bool,
    completed_at: Instant,
}

pub(super) enum CatalogDestination {
    Rpc(oneshot::Sender<ResponseEnvelope>),
    Rule { id: RuleId, revision: u64 },
}
#[derive(Clone)]
pub(super) struct RuleAdmission {
    pub id: RuleId,
    pub revision: u64,
    pub token: ObservationToken,
    pub source_at: Instant,
}

impl Automation {
    pub(super) fn owners_healthy(&self) -> bool {
        !self.observer_error
    }
}

#[derive(Clone)]
pub(super) struct StopTarget {
    pub profile: String,
    pub job_id: JobId,
    pub stoppable: bool,
}

#[derive(Default)]
pub(super) struct RuleRegistry {
    policy: Option<Arc<AutomationPolicy>>,
    settled_results: bool,
    views: Vec<RuleView>,
    events: Vec<RuleEvent>,
    sequence: u64,
    known_jobs: BTreeMap<JobId, StopTarget>,
    known_sessions: BTreeMap<SessionId, StopTarget>,
    automatic_jobs: BTreeSet<JobId>,
    epochs: BTreeMap<String, u64>,
    suppressed: BTreeSet<String>,
}

impl RuleRegistry {
    pub fn stop_target(&self, request: &Request) -> Option<StopTarget> {
        match request {
            Request::StopJob { job_id } => self.known_jobs.get(job_id).cloned(),
            Request::StopSession { session_id } => self.known_sessions.get(session_id).cloned(),
            _ => None,
        }
    }
    pub fn suppress(&mut self, profile: &str) {
        let epoch = self.epochs.entry(profile.into()).or_default();
        *epoch = epoch.saturating_add(1);
        self.suppressed.insert(profile.into());
    }
    pub fn stops_late_job(&self, job: &JobId, profile: &str) -> bool {
        self.automatic_jobs.contains(job) && self.suppressed.contains(profile)
    }
    fn epoch(&self, profile: &str) -> u64 {
        self.epochs.get(profile).copied().unwrap_or(0)
    }
    fn resume(&mut self, profile: &str, epoch: u64) {
        if epoch != u64::MAX && self.epoch(profile) == epoch {
            self.suppressed.remove(profile);
        }
    }
}

impl Controls {
    pub(crate) fn rule_decision_support(
        &self,
        config: &LoadedConfig,
        uid: u32,
        profile: &str,
    ) -> Result<ResponseEnvelope> {
        config.manifest.authorize(uid, profile, Action::Read)?;
        Ok(ResponseEnvelope {
            schema_version: 1,
            journal_healthy: !self.journal_unhealthy,
            response: Response::RuleDecisionSupport {
                enabled: self.automation.settled_results,
            },
        })
    }
    pub(crate) fn rules(
        &self,
        config: &LoadedConfig,
        uid: u32,
        profile: &str,
        after: Option<&RuleId>,
    ) -> Result<ResponseEnvelope> {
        config.manifest.authorize(uid, profile, Action::Read)?;
        let grant = self
            .automation
            .policy
            .as_ref()
            .and_then(|p| {
                p.grants()
                    .iter()
                    .find(|g| g.operator_uid() == uid && g.profile() == profile)
            })
            .map(AutomationGrantInfo::from);
        let all: Vec<_> = self
            .automation
            .views
            .iter()
            .filter(|r| {
                r.profile == profile
                    && (uid == 0 || r.operator_uid == uid)
                    && after.is_none_or(|id| r.id > *id)
            })
            .collect();
        let mut rules = vec![];
        let mut bytes = serde_json::to_vec(&grant)
            .map_err(|_| Error::State("rule grant encoding".into()))?
            .len();
        for row in all.iter().take(16) {
            let size = serde_json::to_vec(row)
                .map_err(|_| Error::State("rule view encoding".into()))?
                .len();
            if bytes + size > 60000 {
                break;
            }
            bytes += size;
            rules.push((*row).clone());
        }
        let next_after = if rules.len() < all.len() {
            rules.last().map(|r| r.id.clone())
        } else {
            None
        };
        Ok(ResponseEnvelope {
            schema_version: 1,
            journal_healthy: !self.journal_unhealthy,
            response: Response::Rules {
                policy_enabled: self.automation.policy.is_some(),
                grant,
                rules,
                next_after,
            },
        })
    }

    pub(crate) fn rule_events(
        &self,
        config: &LoadedConfig,
        uid: u32,
        profile: &str,
        after: u64,
    ) -> Result<ResponseEnvelope> {
        config.manifest.authorize(uid, profile, Action::Audit)?;
        let oldest_available = self
            .automation
            .events
            .first()
            .map_or(self.automation.sequence.saturating_add(1), |e| e.sequence);
        let events: Vec<_> = self
            .automation
            .events
            .iter()
            .filter(|e| e.profile == profile && e.sequence > after)
            .take(16)
            .cloned()
            .collect();
        let next_after = events
            .last()
            .map_or(self.automation.sequence, |e| e.sequence);
        Ok(ResponseEnvelope {
            schema_version: 1,
            journal_healthy: !self.journal_unhealthy,
            response: Response::RuleEvents {
                events,
                next_after,
                oldest_available,
                gap: after.saturating_add(1) < oldest_available,
            },
        })
    }
}

impl Runtime {
    pub(crate) async fn configure_automation(
        &mut self,
        policy: Option<AutomationPolicy>,
    ) -> Result<()> {
        if let Some(policy) = &policy {
            policy.check_binding(&self.config)?;
        }
        if !self.workers.is_empty()
            || !self.observers.is_empty()
            || !self.discoveries.is_empty()
            || !self.recoveries.is_empty()
        {
            return Err(Error::State(
                "automation policy requires startup without actual owners".into(),
            ));
        }
        self.automation = Automation::default();
        self.automation.policy = policy.map(Arc::new);
        let local = self.automation.policy.as_ref().is_some_and(|p| {
            p.grants()
                .iter()
                .any(|g| g.triggers().contains(&TriggerKind::LocalProfileReady))
        });
        if local {
            self.automation.boot = Some(
                tokio::task::spawn_blocking(|| {
                    InstanceId::new(
                        std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?.trim_end(),
                    )
                })
                .await
                .map_err(|_| Error::State("automatic boot metadata task failed".into()))??,
            );
        }
        let decisions = self
            .automation
            .policy
            .as_ref()
            .is_some_and(|p| p.settled_rule_results());
        if (self.automation.policy.is_some() && self.state.schema_version == 2)
            || (decisions && self.state.rule_rejections.is_none())
        {
            let mut next = self.state.clone();
            if decisions {
                next.enable_rule_decisions()?;
            } else {
                next.enable_automation()?;
            }
            self.persist(next).await?;
        }
        self.refresh_rules()
    }

    fn rule_status(&self, r: &RuleRecord, controls: &Controls) -> RuleStatus {
        let journal = self.state.automation.as_ref();
        let old = journal.and_then(|a| a.last_origin(r));
        if let Some(id) = &r.last_job {
            if controls.jobs.contains_key(id) {
                return RuleStatus::Active;
            }
            if let Some(job) = self.state.jobs.iter().find(|j| j.id == *id) {
                if job.state == JobState::Starting && job.instance_id == self.instance {
                    return RuleStatus::Admitting;
                }
                if !crate::rules::released(job, &self.state.sessions) {
                    return RuleStatus::NeedsReconciliation;
                }
            } else {
                return RuleStatus::NeedsReconciliation;
            }
        }
        if r.deleted || !r.enabled {
            return RuleStatus::Disabled;
        }
        if r.paused_by_user || controls.automation.suppressed.contains(&r.profile) {
            return RuleStatus::PausedByUser;
        }
        if !self.healthy || controls.shutdown_requested {
            return RuleStatus::Unavailable;
        }
        if self.rule_grant(r).is_err() {
            return RuleStatus::BlockedPermission;
        }
        if self.state.sessions.iter().any(|s| {
            s.profile == r.profile
                && !controls.jobs.contains_key(&s.job_id)
                && !matches!(s.cleanup, Some(Cleanup::Verified | Cleanup::NotStarted))
        }) {
            return RuleStatus::NeedsReconciliation;
        }
        if self.local_scope_busy(r) {
            return RuleStatus::ProfileBusy;
        }
        if self
            .automation
            .failed_admissions
            .get(&r.id)
            .is_some_and(|(rev, _)| *rev == r.revision)
        {
            return RuleStatus::Unavailable;
        }
        if self
            .state
            .jobs
            .iter()
            .any(|j| j.profile == r.profile && j.state != JobState::Finished)
            || controls.jobs.values().any(|c| c.profile == r.profile)
        {
            return RuleStatus::ProfileBusy;
        }
        if old.is_some_and(|o| o.revision == r.revision) && r.spec.rearm() == RearmKind::Once {
            return RuleStatus::OnceConsumed;
        }
        match r.spec.trigger() {
            TriggerKind::LocalProfileReady => {
                if self.automation.local_failed {
                    RuleStatus::Unavailable
                } else {
                    self.local_condition(r)
                        .map_or_else(|s| s, |(token, _)| self.candidate_status(r, &token))
                }
            }
            TriggerKind::RemoteDeviceAvailable => self
                .automation
                .remote
                .get(&r.id)
                .filter(|o| o.revision == r.revision)
                .map_or(RuleStatus::Waiting, |o| {
                    if !o.terminal
                        && o.error.is_none()
                        && o.completed_at.elapsed() > Duration::from_secs(6)
                    {
                        RuleStatus::Stale
                    } else {
                        o.status
                    }
                }),
        }
    }

    fn candidate_status(&self, r: &RuleRecord, token: &ObservationToken) -> RuleStatus {
        if self
            .state
            .automation
            .as_ref()
            .is_none_or(|a| !a.allows_occurrence(r, token))
        {
            RuleStatus::AwaitingNewObservation
        } else {
            RuleStatus::Candidate
        }
    }

    fn rule_grant(&self, r: &RuleRecord) -> Result<&AutomationGrant> {
        let grant = self
            .automation
            .policy
            .as_ref()
            .ok_or_else(denied)?
            .authorize(&self.config, r.operator_uid, &r.profile, &r.spec)?;
        let count = self.state.automation.as_ref().map_or(0, |a| {
            a.rules
                .iter()
                .filter(|rule| rule.operator_uid == r.operator_uid && rule.profile == r.profile)
                .count()
        });
        if count > usize::from(grant.max_rules()) {
            return Err(denied());
        }
        Ok(grant)
    }

    fn local_scope_busy(&self, rule: &RuleRecord) -> bool {
        if rule.spec.trigger() != TriggerKind::LocalProfileReady {
            return false;
        }
        let Ok(grant) = self.rule_grant(rule) else {
            return true;
        };
        let overlaps = |profile: &str| {
            if profile == rule.profile
                || !self.config.manifest.profiles.iter().any(|p| {
                    p.id == profile && matches!(p.backend, Backend::Native | Backend::Usbfs)
                })
            {
                return false;
            }
            match self.config.credentials.get(profile) {
                Some(Credentials::Server(c)) => c.devices.iter().any(|other| {
                    grant
                        .devices()
                        .iter()
                        .any(|key| key.fingerprint() == other.fingerprint())
                }),
                _ => false,
            }
        };
        self.state
            .jobs
            .iter()
            .any(|j| j.state != JobState::Finished && overlaps(&j.profile))
            || self.state.sessions.iter().any(|s| {
                !matches!(s.cleanup, Some(Cleanup::Verified | Cleanup::NotStarted))
                    && overlaps(&s.profile)
            })
    }

    pub(super) fn refresh_rules(&self) -> Result<()> {
        let mut controls = self.controls.lock().map_err(|_| lock_error())?;
        let registry = &mut controls.automation;
        registry.policy = self.automation.policy.clone();
        registry.settled_results = self.state.rule_rejections.is_some();
        registry.known_jobs = self
            .state
            .jobs
            .iter()
            .map(|j| {
                (
                    j.id.clone(),
                    StopTarget {
                        profile: j.profile.clone(),
                        job_id: j.id.clone(),
                        stoppable: true,
                    },
                )
            })
            .collect();
        registry.known_sessions = self
            .state
            .sessions
            .iter()
            .map(|s| {
                (
                    s.id.clone(),
                    StopTarget {
                        profile: s.profile.clone(),
                        job_id: s.job_id.clone(),
                        stoppable: s.role != SessionRole::Export
                            || self
                                .config
                                .manifest
                                .profiles
                                .iter()
                                .any(|p| p.id == s.profile && p.backend == Backend::Native),
                    },
                )
            })
            .collect();
        registry.automatic_jobs = self
            .state
            .automation
            .as_ref()
            .map_or_else(BTreeSet::new, |a| {
                a.origins.iter().map(|o| o.job_id.clone()).collect()
            });
        registry.events = self
            .state
            .automation
            .as_ref()
            .map_or_else(Vec::new, |a| a.events.clone());
        registry.sequence = self.state.automation.as_ref().map_or(0, |a| a.sequence);
        let mut views = vec![];
        if let Some(journal) = &self.state.automation {
            for r in &journal.rules {
                let last_job = r
                    .last_job
                    .as_ref()
                    .and_then(|id| self.state.jobs.iter().find(|j| j.id == *id))
                    .cloned();
                views.push(RuleView {
                    id: r.id.clone(),
                    revision: r.revision,
                    operator_uid: r.operator_uid,
                    profile: r.profile.clone(),
                    spec: r.spec.clone(),
                    enabled: r.enabled,
                    paused_by_user: r.paused_by_user,
                    deleted: r.deleted,
                    status: self.rule_status(r, &controls),
                    last_origin: journal.last_origin(r).cloned(),
                    last_job,
                    observation_error: self
                        .automation
                        .remote
                        .get(&r.id)
                        .filter(|o| o.revision == r.revision)
                        .and_then(|o| o.error)
                        .or_else(|| {
                            (r.spec.trigger() == TriggerKind::LocalProfileReady
                                && self.automation.local_failed)
                                .then_some(self.automation.local_error.unwrap_or(Failure::State))
                        }),
                    admission_error: self
                        .automation
                        .failed_admissions
                        .get(&r.id)
                        .filter(|(rev, _)| *rev == r.revision)
                        .map(|(_, error)| *error),
                });
            }
        }
        views.sort_by(|a, b| a.id.cmp(&b.id));
        controls.automation.views = views;
        Ok(())
    }

    pub(super) async fn mutate_rule(
        &mut self,
        uid: u32,
        profile: String,
        request_id: RequestId,
        mutation: Option<RuleMutation>,
    ) -> Result<Response> {
        self.config
            .manifest
            .authorize(uid, &profile, Action::Read)?;
        if let Some(old) = self
            .state
            .rule_rejections
            .as_ref()
            .map(|r| r.find(uid, &profile, &request_id))
            .transpose()?
            .flatten()
        {
            if mutation.as_ref().is_some_and(|m| *m != old.mutation) {
                return Err(Error::InvalidInput(
                    "mutation identity payload mismatch".into(),
                ));
            }
            // The original denial was already durably audited. Legacy readback
            // keeps its original shape without manufacturing another denial.
            return Ok(Response::Error { code: old.failure });
        }
        if let Some(old) = self
            .state
            .automation
            .as_ref()
            .map(|a| a.mutation(uid, &profile, &request_id))
            .transpose()?
            .flatten()
        {
            if mutation.as_ref().is_some_and(|edit| *edit != old.mutation) {
                return Err(Error::InvalidInput(
                    "mutation identity payload mismatch".into(),
                ));
            }
            return Ok(Response::RuleMutationApplied {
                request_id,
                outcome: old.outcome.clone(),
                replayed: true,
            });
        }
        let Some(mutation) = mutation else {
            return Ok(Response::RuleMutationExpired { request_id });
        };
        if !self.healthy {
            return Err(Error::State(
                "automatic mutation journal unavailable".into(),
            ));
        }
        let journal = self.state.automation.as_ref();
        let (spec, resume) = match &mutation {
            RuleMutation::Create { spec, enabled } => (Some(spec), *enabled),
            RuleMutation::Replace { rule_id, spec, .. } => {
                journal.ok_or_else(denied)?.rule(uid, &profile, rule_id)?;
                (Some(spec), false)
            }
            RuleMutation::SetEnabled {
                rule_id, enabled, ..
            } => {
                let old = journal.ok_or_else(denied)?.rule(uid, &profile, rule_id)?;
                (if *enabled { Some(&old.spec) } else { None }, *enabled)
            }
            RuleMutation::Delete { rule_id, .. } => {
                journal.ok_or_else(denied)?.rule(uid, &profile, rule_id)?;
                (None, false)
            }
        };
        let max_rules = if let Some(spec) = spec {
            self.automation
                .policy
                .as_ref()
                .ok_or_else(denied)?
                .authorize(&self.config, uid, &profile, spec)?
                .max_rules()
        } else {
            MAX_RULES_PER_GRANT
        };
        if !matches!(mutation, RuleMutation::Create { .. })
            && spec.is_some()
            && journal.is_some_and(|a| {
                a.rules
                    .iter()
                    .filter(|r| r.operator_uid == uid && r.profile == profile)
                    .count()
                    > usize::from(max_rules)
            })
        {
            return Err(denied());
        }
        let epoch = self
            .controls
            .lock()
            .map_err(|_| lock_error())?
            .automation
            .epoch(&profile);
        let create_id = if matches!(mutation, RuleMutation::Create { .. }) {
            Some(RuleId::new(fresh_id()?)?)
        } else {
            None
        };
        let mut next = self.state.clone();
        next.enable_automation()?;
        let outcome = next.automation.as_mut().ok_or_else(denied)?.mutate(
            uid,
            &profile,
            request_id.clone(),
            mutation,
            create_id,
            max_rules,
        )?;
        next.preflight(true)?;
        self.persist(next).await?;
        if resume {
            self.controls
                .lock()
                .map_err(|_| lock_error())?
                .automation
                .resume(&profile, epoch);
        }
        self.automation.candidates.remove(&outcome.rule_id);
        self.automation.remote.remove(&outcome.rule_id);
        self.automation.failed_admissions.remove(&outcome.rule_id);
        self.refresh_rules()?;
        Ok(Response::RuleMutationApplied {
            request_id,
            outcome,
            replayed: false,
        })
    }

    fn original_rule_decision(
        &self,
        uid: u32,
        profile: &str,
        request_id: &RequestId,
        mutation: Option<&RuleMutation>,
    ) -> Result<Option<RuleDecision>> {
        if let Some(old) = self
            .state
            .automation
            .as_ref()
            .map(|a| a.mutation(uid, profile, request_id))
            .transpose()?
            .flatten()
        {
            if mutation.is_some_and(|m| *m != old.mutation) {
                return Err(Error::InvalidInput(
                    "decision identity payload mismatch".into(),
                ));
            }
            return Ok(Some(RuleDecision::Applied {
                outcome: old.outcome.clone(),
            }));
        }
        if let Some(old) = self
            .state
            .rule_rejections
            .as_ref()
            .map(|r| r.find(uid, profile, request_id))
            .transpose()?
            .flatten()
        {
            if mutation.is_some_and(|m| *m != old.mutation) {
                return Err(Error::InvalidInput(
                    "decision identity payload mismatch".into(),
                ));
            }
            return Ok(Some(RuleDecision::Rejected {
                sequence: old.sequence,
                failure: old.failure,
            }));
        }
        Ok(None)
    }

    pub(super) async fn decide_rule(
        &mut self,
        uid: u32,
        profile: String,
        request_id: RequestId,
        mutation: Option<RuleMutation>,
    ) -> Result<Response> {
        self.config
            .manifest
            .authorize(uid, &profile, Action::Read)?;
        if self.state.rule_rejections.is_none() {
            return Err(Error::Unsupported(
                "settled rule results require explicit disk4 policy".into(),
            ));
        }
        if let Some(decision) =
            self.original_rule_decision(uid, &profile, &request_id, mutation.as_ref())?
        {
            return Ok(Response::RuleDecided {
                request_id,
                decision,
                replayed: true,
            });
        }
        let Some(mutation) = mutation else {
            return Ok(Response::RuleDecisionExpired { request_id });
        };
        match self
            .mutate_rule(
                uid,
                profile.clone(),
                request_id.clone(),
                Some(mutation.clone()),
            )
            .await
        {
            Ok(Response::RuleMutationApplied {
                outcome, replayed, ..
            }) => {
                return Ok(Response::RuleDecided {
                    request_id,
                    decision: RuleDecision::Applied { outcome },
                    replayed,
                });
            }
            Ok(_) => {
                return Err(Error::State(
                    "unexpected legacy mutation outcome; original identity retained".into(),
                ));
            }
            Err(error) => {
                // Actual persist may have succeeded before registry publication
                // failed. Never replace that committed positive with a refusal.
                if let Some(decision) =
                    self.original_rule_decision(uid, &profile, &request_id, Some(&mutation))?
                {
                    return Ok(Response::RuleDecided {
                        request_id,
                        decision,
                        replayed: false,
                    });
                }
                if !self.healthy {
                    return Err(error);
                }
                let failure = Failure::from_error(&error);
                let mut next = self.state.clone();
                next.rule_rejections.as_mut().ok_or_else(denied)?.record(
                    uid,
                    profile.clone(),
                    request_id.clone(),
                    mutation.clone(),
                    failure,
                )?;
                if failure == Failure::Permission {
                    next.audit(uid, None, None, None, AuditAction::Denied, Some(failure))?;
                }
                next.preflight(true)?;
                let committed = self.persist(next).await;
                // A postcommit registry error cannot erase an actual durable
                // rejection either. Writer uncertainty still has no receipt.
                if let Some(decision) =
                    self.original_rule_decision(uid, &profile, &request_id, Some(&mutation))?
                {
                    return Ok(Response::RuleDecided {
                        request_id,
                        decision,
                        replayed: false,
                    });
                }
                committed?;
            }
        }
        Err(Error::State(
            "rule decision missing after persistence; retain original identity".into(),
        ))
    }

    pub(super) async fn open_rule_observer(
        &mut self,
        mut stop: watch::Receiver<bool>,
        mut closing: watch::Receiver<bool>,
    ) {
        if self.automation.boot.is_none() || *stop.borrow() || *closing.borrow() {
            return;
        }
        match crate::discovery::DeviceMonitor::open().await {
            Ok(monitor) => {
                self.automation.local = Some(monitor.subscribe());
                let mut lifecycle = monitor.subscribe();
                self.observers.spawn(async move {
                    loop {
                        tokio::select! {
                            _=crate::context::stopped(&mut stop)=>break,
                            _=crate::context::stopped(&mut closing)=>break,
                            update=lifecycle.next()=>if update.is_err() { break; },
                        }
                    }
                    monitor.shutdown().await
                });
            }
            Err(error) => {
                self.automation.local_failed = true;
                self.automation.local_error = Some(Failure::from_error(&error));
                self.automation.observer_error = true;
                tracing::warn!("automatic local observation could not be prepared");
            }
        }
        if self.refresh_rules().is_err() {
            self.healthy = false;
        }
    }

    pub(super) fn automation_observer_finished(
        &mut self,
        result: Option<std::result::Result<Result<()>, tokio::task::JoinError>>,
    ) {
        self.automation.local = None;
        if !matches!(result, Some(Ok(Ok(())))) {
            self.automation.local_failed = true;
            self.automation.local_error = Some(match result {
                Some(Ok(Err(error))) => Failure::from_error(&error),
                _ => Failure::State,
            });
            self.automation.observer_error = true;
            tracing::error!("automatic observation owner ended abnormally");
        }
    }

    fn local_condition(
        &self,
        r: &RuleRecord,
    ) -> std::result::Result<(ObservationToken, Instant), RuleStatus> {
        let snapshot = self
            .automation
            .local
            .as_ref()
            .and_then(|s| s.latest())
            .ok_or(RuleStatus::Stale)?;
        let grant = self
            .rule_grant(r)
            .map_err(|_| RuleStatus::BlockedPermission)?;
        let boot = self
            .automation
            .boot
            .as_ref()
            .ok_or(RuleStatus::Unavailable)?;
        local_observation(&snapshot, grant.devices(), boot)
    }

    fn remote_condition(
        &self,
        r: &RuleRecord,
        devices: &[rustusb_protocol::secure::CatalogDevice],
    ) -> std::result::Result<ObservationToken, RuleStatus> {
        use rustusb_core::VirtualProfile;
        let grant = self
            .rule_grant(r)
            .map_err(|_| RuleStatus::BlockedPermission)?;
        let key = grant.devices().first().ok_or(RuleStatus::BlockedUnsafe)?;
        if key.backend() != rustusb_core::DeviceBackend::Virtual {
            return Err(RuleStatus::SafetyEvidenceUnavailable);
        }
        rustusb_protocol::secure::validate_catalog(devices)
            .map_err(|_| RuleStatus::BlockedUnsafe)?;
        let Some(entry) = devices.iter().find(|d| d.device_key == *key) else {
            return Err(RuleStatus::Waiting);
        };
        let d = &entry.device;
        let hid = *key == DeviceKey::virtual_profile(VirtualProfile::Hid);
        let expected: Vec<_> = if hid {
            vec![(3, 1, 1)]
        } else {
            vec![(2, 2, 0), (10, 0, 0)]
        };
        if d.num_configurations != 1
            || d.configuration != 1
            || d.vendor_id != 0x1234
            || d.product_id != if hid { 1 } else { 2 }
            || d.device_version != 0x0100
            || d.speed != 2
            || (d.class, d.subclass, d.protocol) != if hid { (0, 0, 0) } else { (0xef, 2, 1) }
            || d.interfaces
                .iter()
                .map(|i| (i.class, i.subclass, i.protocol))
                .collect::<Vec<_>>()
                != expected
        {
            return Err(RuleStatus::BlockedUnsafe);
        }
        let address = (&d.bus_id, &d.path, d.bus_number, d.device_number);
        let descriptor = (
            d.speed,
            d.vendor_id,
            d.product_id,
            d.device_version,
            d.class,
            d.subclass,
            d.protocol,
            d.configuration,
            d.num_configurations,
        );
        let interfaces: Vec<_> = d
            .interfaces
            .iter()
            .map(|i| (i.class, i.subclass, i.protocol))
            .collect();
        digest(&(
            "rustusb-remote-advertisement-v1",
            entry.device_key.as_str(),
            address,
            descriptor,
            interfaces,
        ))
        .map_err(|_| RuleStatus::Unavailable)
    }

    fn candidate_ready(&mut self, r: &RuleRecord, token: &ObservationToken) -> bool {
        let now = Instant::now();
        let entry = self
            .automation
            .candidates
            .entry(r.id.clone())
            .or_insert_with(|| Candidate {
                revision: r.revision,
                token: token.clone(),
                since: now,
            });
        if entry.revision != r.revision || entry.token != *token {
            *entry = Candidate {
                revision: r.revision,
                token: token.clone(),
                since: now,
            };
        }
        now.duration_since(entry.since) >= Duration::from_secs(u64::from(r.spec.settle_seconds()))
    }

    pub(super) fn check_rule_admission(
        &self,
        uid: u32,
        profile: &str,
        input: &RuleAdmission,
    ) -> Result<()> {
        let journal = self.state.automation.as_ref().ok_or_else(denied)?;
        let r = journal.rule(uid, profile, &input.id)?;
        self.rule_grant(r)?;
        if self.local_scope_busy(r) {
            return Err(Error::State(
                "automatic physical scope is already owned".into(),
            ));
        }
        if r.revision != input.revision
            || !journal.allows_occurrence(r, &input.token)
            || input.source_at.elapsed() > Duration::from_secs(6)
            || self.state.sessions.iter().any(|s| {
                s.profile == profile
                    && !matches!(s.cleanup, Some(Cleanup::Verified | Cleanup::NotStarted))
            })
        {
            return Err(Error::State(
                "automatic condition or previous release unavailable".into(),
            ));
        }
        let controls = self.controls.lock().map_err(|_| lock_error())?;
        if controls.shutdown_requested
            || controls.automation.suppressed.contains(profile)
            || controls.jobs.values().any(|c| c.profile == profile)
        {
            return Err(Error::State(
                "automatic admission suppressed or owned".into(),
            ));
        }
        Ok(())
    }

    pub(super) fn rule_source_current(
        &self,
        uid: u32,
        profile: &str,
        input: &RuleAdmission,
    ) -> bool {
        if input.source_at.elapsed() > Duration::from_secs(6) {
            return false;
        }
        let Some(r) = self
            .state
            .automation
            .as_ref()
            .and_then(|a| a.rule(uid, profile, &input.id).ok())
        else {
            return false;
        };
        match r.spec.trigger() {
            TriggerKind::LocalProfileReady => self
                .local_condition(r)
                .is_ok_and(|(token, _)| token == input.token),
            TriggerKind::RemoteDeviceAvailable => true, // The captured authenticated completion time above is authoritative.
        }
    }

    async fn fire_rule(&mut self, r: &RuleRecord, token: ObservationToken, source_at: Instant) {
        let request = match fresh_id().and_then(RequestId::new) {
            Ok(id) => id,
            Err(error) => {
                self.automation
                    .failed_admissions
                    .insert(r.id.clone(), (r.revision, Failure::from_error(&error)));
                return;
            }
        };
        let result = self
            .start_with_origin(
                r.operator_uid,
                r.profile.clone(),
                request,
                false,
                Some(RuleAdmission {
                    id: r.id.clone(),
                    revision: r.revision,
                    token,
                    source_at,
                }),
            )
            .await;
        if let Err(error) = result {
            self.automation
                .failed_admissions
                .insert(r.id.clone(), (r.revision, Failure::from_error(&error)));
        }
        self.automation.candidates.remove(&r.id);
    }

    pub(super) async fn automatic_tick(
        &mut self,
        stop: watch::Receiver<bool>,
        closing: watch::Receiver<bool>,
    ) {
        if *stop.borrow() || *closing.borrow() || !self.healthy || self.automation.policy.is_none()
        {
            return;
        }
        let rules = self
            .state
            .automation
            .as_ref()
            .map_or_else(Vec::new, |a| a.rules.clone());
        self.automation.candidates.retain(|id, c| {
            rules
                .iter()
                .any(|r| r.id == *id && r.revision == c.revision && !r.deleted)
        });
        self.automation.remote.retain(|id, o| {
            rules
                .iter()
                .any(|r| r.id == *id && r.revision == o.revision && !r.deleted)
        });
        self.automation.failed_admissions.retain(|id, (rev, _)| {
            rules
                .iter()
                .any(|r| r.id == *id && r.revision == *rev && !r.deleted)
        });
        for r in rules
            .iter()
            .filter(|r| r.spec.trigger() == TriggerKind::LocalProfileReady)
        {
            let eligible = self
                .controls
                .lock()
                .map(|c| {
                    matches!(
                        self.rule_status(r, &c),
                        RuleStatus::Candidate
                            | RuleStatus::Waiting
                            | RuleStatus::AwaitingNewObservation
                            | RuleStatus::Stale
                    )
                })
                .unwrap_or(false);
            if !eligible {
                self.automation.candidates.remove(&r.id);
                continue;
            }
            match self.local_condition(r) {
                Ok((token, at))
                    if self
                        .state
                        .automation
                        .as_ref()
                        .is_some_and(|a| a.allows_occurrence(r, &token)) =>
                {
                    if self.candidate_ready(r, &token) {
                        self.fire_rule(r, token, at).await;
                    }
                }
                _ => {
                    self.automation.candidates.remove(&r.id);
                }
            }
        }
        if self.refresh_rules().is_err() {
            self.healthy = false;
            return;
        }
        self.schedule_rule_catalog(&rules, stop, closing);
    }

    fn schedule_rule_catalog(
        &mut self,
        rules: &[RuleRecord],
        mut stop: watch::Receiver<bool>,
        mut closing: watch::Receiver<bool>,
    ) {
        if self.automation.remote_inflight.is_some()
            || self.discoveries.len() >= MAX_DISCOVERIES
            || *stop.borrow()
            || *closing.borrow()
        {
            return;
        }
        let mut ordered: Vec<_> = rules
            .iter()
            .filter(|r| r.spec.trigger() == TriggerKind::RemoteDeviceAvailable)
            .collect();
        ordered.sort_by(|a, b| a.id.cmp(&b.id));
        let split = self
            .automation
            .cursor
            .as_ref()
            .map_or(0, |id| ordered.partition_point(|r| r.id <= *id));
        ordered.rotate_left(split);
        for r in ordered {
            let eligible = self
                .controls
                .lock()
                .map(|c| {
                    matches!(
                        self.rule_status(r, &c),
                        RuleStatus::Candidate
                            | RuleStatus::Waiting
                            | RuleStatus::AwaitingNewObservation
                            | RuleStatus::Stale
                    )
                })
                .unwrap_or(false);
            if !eligible
                || self.automation.remote.get(&r.id).is_some_and(|o| {
                    o.revision == r.revision && (o.terminal || o.next_poll > Instant::now())
                })
            {
                continue;
            }
            let Some(Credentials::Client(config)) = self.config.credentials.get(&r.profile) else {
                continue;
            };
            if config.device.backend() != rustusb_core::DeviceBackend::Virtual {
                self.automation.remote.insert(
                    r.id.clone(),
                    RemoteObservation {
                        revision: r.revision,
                        status: RuleStatus::SafetyEvidenceUnavailable,
                        error: None,
                        next_poll: Instant::now(),
                        backoff: 2,
                        terminal: true,
                        completed_at: Instant::now(),
                    },
                );
                continue;
            }
            let Ok(config) = self.catalog_config(r.operator_uid, &r.profile) else {
                continue;
            };
            let id = r.id.clone();
            let revision = r.revision;
            let uid = r.operator_uid;
            let profile = r.profile.clone();
            self.automation.cursor = Some(id.clone());
            self.automation.remote_inflight = Some((id.clone(), revision));
            self.discoveries.spawn(async move {
                let result = crate::jobs::discover_until(&config,Instant::now()+Duration::from_secs(4),async {
                    tokio::select! { _=crate::context::stopped(&mut stop)=>{}, _=crate::context::stopped(&mut closing)=>{} }
                }).await;
                CatalogCompletion { uid,profile,destination:CatalogDestination::Rule {id,revision},completed_at:Instant::now(),result }
            });
            break;
        }
    }

    pub(super) async fn remote_rule_finished(
        &mut self,
        uid: u32,
        profile: &str,
        id: RuleId,
        revision: u64,
        completed_at: Instant,
        response: Response,
    ) {
        if self.automation.remote_inflight.as_ref() == Some(&(id.clone(), revision)) {
            self.automation.remote_inflight = None;
        }
        let r = self
            .state
            .automation
            .as_ref()
            .and_then(|a| a.rule(uid, profile, &id).ok())
            .cloned();
        let Some(r) = r.filter(|r| r.revision == revision && r.enabled && !r.paused_by_user) else {
            return;
        };
        if self.rule_grant(&r).is_err() {
            return;
        }
        let mut next = RemoteObservation {
            revision,
            status: RuleStatus::Waiting,
            error: None,
            next_poll: Instant::now() + Duration::from_secs(2),
            backoff: 2,
            terminal: false,
            completed_at,
        };
        match response {
            Response::Catalog { devices } if completed_at.elapsed() <= Duration::from_secs(6) => {
                match self.remote_condition(&r, &devices) {
                    Ok(token) => {
                        if self
                            .state
                            .automation
                            .as_ref()
                            .is_some_and(|a| a.allows_occurrence(&r, &token))
                        {
                            next.status = RuleStatus::Candidate;
                            if self.candidate_ready(&r, &token) {
                                self.fire_rule(&r, token, completed_at).await;
                            }
                        } else {
                            next.status = RuleStatus::AwaitingNewObservation;
                        }
                    }
                    Err(status) => {
                        next.status = status;
                        next.terminal = matches!(
                            status,
                            RuleStatus::BlockedUnsafe
                                | RuleStatus::SafetyEvidenceUnavailable
                                | RuleStatus::BlockedPermission
                                | RuleStatus::Unavailable
                        );
                        self.automation.candidates.remove(&id);
                    }
                }
            }
            Response::Catalog { .. } => {
                next.status = RuleStatus::Stale;
                self.automation.candidates.remove(&id);
            }
            Response::Error { code } => {
                next.error = Some(code);
                next.status = if code == Failure::Permission {
                    RuleStatus::BlockedPermission
                } else {
                    RuleStatus::Stale
                };
                next.terminal =
                    !matches!(code, Failure::Io | Failure::Timeout | Failure::Cancelled);
                next.backoff = self
                    .automation
                    .remote
                    .get(&id)
                    .map_or(2, |o| o.backoff.saturating_mul(2).min(120));
                next.next_poll = Instant::now() + Duration::from_secs(next.backoff);
                self.automation.candidates.remove(&id);
            }
            _ => {
                next.status = RuleStatus::Unavailable;
                next.terminal = true;
                self.automation.candidates.remove(&id);
            }
        }
        self.automation.remote.insert(id, next);
        if self.refresh_rules().is_err() {
            self.healthy = false;
        }
    }
}

fn digest(value: &impl serde::Serialize) -> Result<ObservationToken> {
    use sha2::{Digest, Sha256};
    let bytes = serde_json::to_vec(value)
        .map_err(|_| Error::State("automatic observation encoding".into()))?;
    ObservationToken::new(format!("{:x}", Sha256::digest(bytes)))
}

fn local_observation(
    snapshot: &crate::discovery::DeviceSnapshot,
    keys: &BTreeSet<DeviceKey>,
    boot: &InstanceId,
) -> std::result::Result<(ObservationToken, Instant), RuleStatus> {
    if snapshot.state != crate::discovery::DiscoveryState::Current
        || std::time::Instant::now()
            .checked_duration_since(snapshot.sampled_at)
            .is_none_or(|age| age > Duration::from_secs(6))
    {
        return Err(RuleStatus::Stale);
    }
    let devices = snapshot.devices.as_ref().ok_or(RuleStatus::Stale)?;
    if keys.is_empty() || keys.len() > MAX_PROFILE_DEVICES {
        return Err(RuleStatus::BlockedUnsafe);
    }
    let mut observed = vec![];
    for key in keys {
        let fingerprint = key.fingerprint().ok_or(RuleStatus::BlockedUnsafe)?;
        let matches: Vec<_> = devices
            .iter()
            .filter(|d| d.device.id == fingerprint)
            .collect();
        if matches.is_empty() {
            return Err(RuleStatus::Waiting);
        }
        if matches.len() != 1 {
            return Err(RuleStatus::BlockedUnsafe);
        }
        let d = matches[0];
        if !d.device.export_blockers.is_empty()
            || d.device.exported
            || d.device.occupied.is_some()
            || d.device.class == 8
            || d.device.interfaces.is_empty()
            || d.device.interfaces.iter().any(|i| i.class == 8)
            || d.stamp.sysfs_inode == 0
            || d.stamp.bus_number == 0
            || !(1..=127).contains(&d.stamp.device_number)
            || d.device.device_address != u32::from(d.stamp.device_number)
        {
            return Err(RuleStatus::BlockedUnsafe);
        }
        observed.push((
            key.as_str(),
            d.stamp.sysfs_device,
            d.stamp.sysfs_inode,
            d.stamp.bus_number,
            d.stamp.device_number,
        ));
    }
    let token = digest(&("rustusb-local-observation-v1", boot.as_str(), observed))
        .map_err(|_| RuleStatus::Unavailable)?;
    Ok((token, Instant::from_std(snapshot.sampled_at)))
}

#[cfg(test)]
#[path = "runtime_automation_tests.rs"]
mod tests;

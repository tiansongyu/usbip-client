//! Application DTOs are consumed here, never serialized as part of the export.
use super::*;
use rustusb_service::{
    diagnostics::{DiagnosticHeader, DiagnosticItems},
    health::{HealthAvailability, SessionHealth},
    model,
};

impl Builder {
    pub(crate) fn set_agent_scope(&mut self, profile: &str, caller_uid: u32) -> Result<()> {
        if profile.is_empty()
            || profile.len() > 64
            || !profile
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        {
            return Err(invalid());
        }
        if let Some((current, uid)) = &self.agent_scope {
            return if current == profile && *uid == caller_uid {
                Ok(())
            } else {
                Err(Error::Permission("diagnostic scope cannot change".into()))
            };
        }
        if !self.logs.pages.is_empty() {
            return Err(invalid());
        }
        self.requested_agent_scope = Some(AgentScope {
            profile: self.aliases.get(Namespace::Profile, profile)?,
            caller: self.aliases.uid(caller_uid)?,
        });
        self.agent_scope = Some((profile.to_owned(), caller_uid));
        Ok(())
    }

    pub(crate) fn begin_agent(
        &mut self,
        h: &DiagnosticHeader,
        profile: &str,
        caller_uid: u32,
        at: u64,
    ) -> Result<()> {
        let result = self.begin(h, profile, caller_uid, at);
        if let Err(e) = &result {
            self.degrade(Stage::Agent, collection_error(e));
        }
        result
    }
    fn begin(&mut self, h: &DiagnosticHeader, profile: &str, uid: u32, at: u64) -> Result<()> {
        self.set_agent_scope(profile, uid)?;
        if self.agent.is_some()
            || !self.logs.pages.is_empty()
            || profile.is_empty()
            || profile.len() > 64
            || !profile
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
            || h.counts.jobs > 512
            || h.counts.sessions > 512
            || h.counts.audit > 1024
            || h.expires_after_millis != 30_000
            || h.audit_oldest_retained_sequence > h.committed_sequence.saturating_add(1)
        {
            return Err(invalid());
        }
        let backend = match h.backend {
            rustusb_service::config::Backend::Client => Backend::Client,
            rustusb_service::config::Backend::Native => Backend::Native,
            rustusb_service::config::Backend::Virtual => Backend::Virtual,
            rustusb_service::config::Backend::Usbfs => Backend::Usbfs,
        };
        self.agent = Some(Agent {
            application_epoch: self
                .aliases
                .get(Namespace::ApplicationEpoch, h.snapshot.instance_id.as_str())?,
            profile: self.aliases.get(Namespace::Profile, profile)?,
            caller: self.aliases.uid(uid)?,
            backend,
            committed_sequence: h.committed_sequence,
            publication_age_millis: h.publication_age_millis,
            audit_oldest_retained_sequence: h.audit_oldest_retained_sequence,
            expires_after_millis: h.expires_after_millis,
            expected_jobs: h.counts.jobs,
            expected_sessions: h.counts.sessions,
            expected_audit: h.counts.audit,
            jobs: Vec::new(),
            sessions: Vec::new(),
            audit: Vec::new(),
        });
        self.set_outcome(
            Stage::Agent,
            SectionOutcome::Incomplete,
            Some(CollectionFailure::Unavailable),
            at,
        );
        Ok(())
    }
    pub(crate) fn push_items(&mut self, items: DiagnosticItems) -> Result<()> {
        let result = self.items(items);
        if let Err(e) = &result {
            self.degrade(Stage::Agent, collection_error(e));
        }
        result
    }
    fn items(&mut self, items: DiagnosticItems) -> Result<()> {
        let (profile, uid) = self.agent_scope.as_ref().ok_or_else(invalid)?.clone();
        match items {
            DiagnosticItems::Jobs { jobs } => {
                let a = self.agent.as_ref().ok_or_else(invalid)?;
                if jobs.len() > 16
                    || self.item_pages[0] >= 32
                    || a.jobs.len() + jobs.len() > usize::from(a.expected_jobs)
                {
                    return Err(limit());
                }
                self.item_pages[0] += 1;
                for j in jobs {
                    if j.profile != profile || !self.job_ids.insert(j.id.as_str().to_owned()) {
                        return Err(invalid());
                    }
                    let row = Job {
                        job: self.aliases.get(Namespace::Job, j.id.as_str())?,
                        application_epoch: self
                            .aliases
                            .get(Namespace::ApplicationEpoch, j.instance_id.as_str())?,
                        profile: self.aliases.get(Namespace::Profile, &j.profile)?,
                        operator: self.aliases.uid(j.operator_uid)?,
                        state: match j.state {
                            model::JobState::Starting => JobPhase::Starting,
                            model::JobState::Running => JobPhase::Running,
                            model::JobState::Stopping => JobPhase::Stopping,
                            model::JobState::Finished => JobPhase::Finished,
                            model::JobState::Recovering => JobPhase::Recovering,
                        },
                        stop_requested: j.stop_requested,
                        operation_error: j.operation_error.map(failure),
                    };
                    self.agent.as_mut().ok_or_else(invalid)?.jobs.push(row);
                }
            }
            DiagnosticItems::Sessions { sessions } => {
                let a = self.agent.as_ref().ok_or_else(invalid)?;
                if sessions.len() > 16
                    || self.item_pages[1] >= 32
                    || a.sessions.len() + sessions.len() > usize::from(a.expected_sessions)
                {
                    return Err(limit());
                }
                self.item_pages[1] += 1;
                for s in sessions {
                    if s.profile != profile
                        || s.attempts.len() > 32
                        || !self.session_ids.insert(s.id.as_str().to_owned())
                    {
                        return Err(invalid());
                    }
                    let mut attempts = Vec::with_capacity(s.attempts.len());
                    let mut seen = BTreeSet::new();
                    for a in s.attempts {
                        a.correlation.validate()?;
                        if a.correlation.instance_id != s.instance_id
                            || a.correlation.session_id != s.id
                            || !seen.insert(a.correlation.attempt_id.as_str().to_owned())
                            || a.proof.is_some() && a.resource_id.is_none()
                        {
                            return Err(invalid());
                        }
                        let resource = a
                            .resource_id
                            .as_ref()
                            .map(|r| {
                                let kind = match a.kind {
                                    BrokerResourceKind::NativeImport => "import",
                                    BrokerResourceKind::NativeExport => "export",
                                    BrokerResourceKind::NativeStub => "stub",
                                    BrokerResourceKind::UsbfsGrant => "usbfs",
                                };
                                self.aliases
                                    .get(Namespace::Resource, &format!("{kind}:{}", r.as_str()))
                            })
                            .transpose()?;
                        attempts.push(Attempt {
                            kind: a.kind,
                            application_epoch: self.aliases.get(
                                Namespace::ApplicationEpoch,
                                a.correlation.instance_id.as_str(),
                            )?,
                            session: self
                                .aliases
                                .get(Namespace::Session, a.correlation.session_id.as_str())?,
                            attempt: self
                                .aliases
                                .get(Namespace::Attempt, a.correlation.attempt_id.as_str())?,
                            resource,
                            proof: a.proof,
                        });
                    }
                    self.session_epochs
                        .insert(s.id.as_str().to_owned(), s.instance_id.as_str().to_owned());
                    let row = Session {
                        session: self.aliases.get(Namespace::Session, s.id.as_str())?,
                        application_epoch: self
                            .aliases
                            .get(Namespace::ApplicationEpoch, s.instance_id.as_str())?,
                        job: self.aliases.get(Namespace::Job, s.job_id.as_str())?,
                        profile: self.aliases.get(Namespace::Profile, &s.profile)?,
                        role: role(s.role),
                        device: self.aliases.get(Namespace::Device, s.device_key.as_str())?,
                        principal: self
                            .aliases
                            .optional(Namespace::Principal, s.principal.as_deref())?,
                        state: s.state,
                        evidence: evidence(s.evidence),
                        stop_requested: s.stop_requested,
                        terminal: s.terminal,
                        operation_error: s.operation_error.map(failure),
                        cleanup: s.cleanup.map(cleanup),
                        metrics: Metrics {
                            app_rx_bytes: s.metrics.app_rx_bytes,
                            app_tx_bytes: s.metrics.app_tx_bytes,
                            saturated: s.metrics.saturated,
                            elapsed_millis: s.metrics.elapsed_millis,
                        },
                        attempts,
                    };
                    self.agent.as_mut().ok_or_else(invalid)?.sessions.push(row);
                }
            }
            DiagnosticItems::Audit { events } => {
                let a = self.agent.as_ref().ok_or_else(invalid)?;
                if events.len() > 16
                    || self.item_pages[2] >= 64
                    || a.audit.len() + events.len() > usize::from(a.expected_audit)
                {
                    return Err(limit());
                }
                let commit = a.committed_sequence;
                self.item_pages[2] += 1;
                for e in events {
                    if !(e.profile.as_deref() == Some(profile.as_str())
                        || e.profile.is_none()
                            && e.action == model::AuditAction::Denied
                            && e.actor_uid == uid)
                        || e.sequence == 0
                        || e.sequence > commit
                        || self
                            .audit_sequences
                            .last()
                            .is_some_and(|s| e.sequence <= *s)
                    {
                        return Err(invalid());
                    }
                    self.audit_sequences.insert(e.sequence);
                    let row = Audit {
                        application_epoch: None,
                        sequence: e.sequence,
                        actor: self.aliases.uid(e.actor_uid)?,
                        profile: self
                            .aliases
                            .optional(Namespace::Profile, e.profile.as_deref())?,
                        job: self
                            .aliases
                            .optional(Namespace::Job, e.job_id.as_ref().map(JobId::as_str))?,
                        session: self.aliases.optional(
                            Namespace::Session,
                            e.session_id.as_ref().map(SessionId::as_str),
                        )?,
                        action: action(e.action),
                        failure: e.failure.map(failure),
                        state: e.state,
                        evidence: e.evidence.map(evidence),
                        cleanup: e.cleanup.map(cleanup),
                    };
                    self.agent.as_mut().ok_or_else(invalid)?.audit.push(row);
                }
            }
        }
        Ok(())
    }
    pub(crate) fn observe_health(&mut self, samples: &[SessionHealth], at: u64) -> Result<()> {
        let result = self.health_page(samples, at);
        if let Err(e) = &result {
            self.degrade(Stage::Health, collection_error(e));
        }
        result
    }
    fn health_page(&mut self, samples: &[SessionHealth], at: u64) -> Result<()> {
        if self.agent_scope.is_none() || samples.len() > 16 {
            return Err(invalid());
        }
        if self.health.pages as usize >= MAX_PAGES
            || self.health.samples.len() + samples.len() > 512
        {
            return Err(limit());
        }
        self.health.pages += 1;
        for s in samples {
            if !self.health_ids.insert(s.session_id.as_str().to_owned()) {
                return Err(invalid());
            }
            let link = s
                .link
                .map(|l| {
                    l.validate()?;
                    let connection = l
                        .identity
                        .map(|id| {
                            self.aliases.get(
                                Namespace::Connection,
                                &format!("{}:{}", s.instance_id.as_str(), id.connection),
                            )
                        })
                        .transpose()?;
                    Ok::<Link, Error>(Link {
                        protocol: l.protocol,
                        connection,
                        sample_sequence: l.sample_sequence,
                        elapsed_millis: l.elapsed_millis,
                        phase: l.phase,
                        probe: l.probe,
                        last_probe_sequence: l.last_probe_sequence,
                        last_probe_enqueued_millis: l.last_probe_enqueued_millis,
                        last_reply_sequence: l.last_reply_sequence,
                        last_reply_millis: l.last_reply_millis,
                        last_round_trip_micros: l.last_round_trip_micros,
                        stats: l.stats,
                        end: l.end,
                    })
                })
                .transpose()?;
            self.health.samples.push(HealthSample {
                session: self
                    .aliases
                    .get(Namespace::Session, s.session_id.as_str())?,
                application_epoch: self
                    .aliases
                    .get(Namespace::ApplicationEpoch, s.instance_id.as_str())?,
                role: role(s.role),
                availability: match s.availability {
                    HealthAvailability::Live => Availability::Live,
                    HealthAvailability::PreviousEpoch => Availability::PreviousEpoch,
                    HealthAvailability::OwnerEnded => Availability::OwnerEnded,
                    HealthAvailability::NotObserved => Availability::NotObserved,
                    HealthAvailability::NotDataSession => Availability::NotDataSession,
                },
                not_in_committed_view: self
                    .session_epochs
                    .get(s.session_id.as_str())
                    .is_none_or(|e| e != s.instance_id.as_str()),
                observed_at_millis: at,
                link,
            });
        }
        Ok(())
    }
}
fn role(v: model::SessionRole) -> Role {
    match v {
        model::SessionRole::Import => Role::Import,
        model::SessionRole::Export => Role::Export,
        model::SessionRole::Incoming => Role::Incoming,
    }
}
fn failure(v: model::Failure) -> logs::FailureCategory {
    match v {
        model::Failure::Permission => logs::FailureCategory::Permission,
        model::Failure::InvalidInput => logs::FailureCategory::InvalidInput,
        model::Failure::Io => logs::FailureCategory::Io,
        model::Failure::Timeout => logs::FailureCategory::Timeout,
        model::Failure::State => logs::FailureCategory::State,
        model::Failure::Unsupported => logs::FailureCategory::Unsupported,
        model::Failure::Cancelled => logs::FailureCategory::Cancelled,
        model::Failure::Persistence => logs::FailureCategory::Persistence,
    }
}
fn evidence(v: model::Evidence) -> logs::Evidence {
    match v {
        model::Evidence::Intent => logs::Evidence::Intent,
        model::Evidence::Prepared => logs::Evidence::Prepared,
        model::Evidence::LocalEnumeration => logs::Evidence::LocalEnumeration,
        model::Evidence::ExportReady => logs::Evidence::ExportReady,
        model::Evidence::ImportReplyFlushed => logs::Evidence::ImportReplyFlushed,
        model::Evidence::Closing => logs::Evidence::Closing,
        model::Evidence::Released => logs::Evidence::Released,
        model::Evidence::Unconfirmed => logs::Evidence::Unconfirmed,
        model::Evidence::RootJournalProof => logs::Evidence::RootJournalProof,
    }
}
fn cleanup(v: model::Cleanup) -> logs::Cleanup {
    match v {
        model::Cleanup::NotStarted => logs::Cleanup::NotStarted,
        model::Cleanup::Verified => logs::Cleanup::Verified,
        model::Cleanup::Unconfirmed => logs::Cleanup::Unconfirmed,
    }
}
fn action(v: model::AuditAction) -> logs::AuditAction {
    match v {
        model::AuditAction::Start => logs::AuditAction::Start,
        model::AuditAction::Denied => logs::AuditAction::Denied,
        model::AuditAction::Stop => logs::AuditAction::Stop,
        model::AuditAction::Admission => logs::AuditAction::Admission,
        model::AuditAction::Attempt => logs::AuditAction::Attempt,
        model::AuditAction::Resource => logs::AuditAction::Resource,
        model::AuditAction::Observe => logs::AuditAction::Observe,
        model::AuditAction::Finish => logs::AuditAction::Finish,
        model::AuditAction::Recovery => logs::AuditAction::Recovery,
        model::AuditAction::Discover => logs::AuditAction::Discover,
    }
}

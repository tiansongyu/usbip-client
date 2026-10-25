//! Finite rule journal. Callers mutate a transaction clone, never the live store.

use crate::{
    automation::{required_option, *},
    config::valid_label,
    model::{Cleanup, Job, JobState, Session},
};
use rustusb_core::{Error, Result, application::*};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub(crate) const MAX_RULES: usize = 64;
const MAX_MUTATIONS: usize = 256;
const MAX_ORIGINS: usize = 512;
const MAX_EVENTS: usize = 256;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RuleRecord {
    pub id: RuleId,
    pub revision: u64,
    pub operator_uid: u32,
    pub profile: String,
    pub spec: RuleSpec,
    pub enabled: bool,
    pub paused_by_user: bool,
    pub deleted: bool,
    #[serde(deserialize_with = "required_option")]
    pub last_job: Option<JobId>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MutationRecord {
    pub sequence: u64,
    pub request_id: RequestId,
    pub uid: u32,
    pub profile: String,
    pub mutation: RuleMutation,
    pub outcome: RuleMutationOutcome,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RuleJournal {
    pub sequence: u64,
    pub rules: Vec<RuleRecord>,
    pub mutations: Vec<MutationRecord>,
    pub origins: Vec<RuleOrigin>,
    pub events: Vec<RuleEvent>,
}

fn invalid() -> Error {
    Error::InvalidInput("automatic rule journal identity or bounds".into())
}
fn denied() -> Error {
    Error::Permission("automatic rule identity denied".into())
}

pub(crate) fn released(job: &Job, sessions: &[Session]) -> bool {
    job.state == JobState::Finished
        && sessions
            .iter()
            .filter(|s| s.job_id == job.id)
            .all(|s| matches!(s.cleanup, Some(Cleanup::Verified | Cleanup::NotStarted)))
}

impl RuleJournal {
    pub fn mutation(
        &self,
        uid: u32,
        profile: &str,
        request: &RequestId,
    ) -> Result<Option<&MutationRecord>> {
        let Some(record) = self.mutations.iter().find(|m| m.request_id == *request) else {
            return Ok(None);
        };
        if record.uid != uid || record.profile != profile {
            return Err(denied());
        }
        Ok(Some(record))
    }

    pub fn rule(&self, uid: u32, profile: &str, id: &RuleId) -> Result<&RuleRecord> {
        self.rules
            .iter()
            .find(|r| r.id == *id && r.operator_uid == uid && r.profile == profile && !r.deleted)
            .ok_or_else(denied)
    }

    pub fn last_origin(&self, rule: &RuleRecord) -> Option<&RuleOrigin> {
        rule.last_job
            .as_ref()
            .and_then(|id| self.origins.iter().find(|o| o.job_id == *id))
    }

    pub fn allows_occurrence(&self, rule: &RuleRecord, observation: &ObservationToken) -> bool {
        if !rule.enabled || rule.paused_by_user || rule.deleted {
            return false;
        }
        match self.last_origin(rule) {
            None => rule.last_job.is_none(),
            Some(old) if old.revision < rule.revision => true,
            Some(old) => {
                rule.spec.rearm() == RearmKind::NewObservation && old.observation != *observation
            }
        }
    }

    // Authorization belongs to Runtime's actual peer / immutable root policy.
    // This edits only a transaction clone and cannot run or stop a USB owner.
    pub fn mutate(
        &mut self,
        uid: u32,
        profile: &str,
        request_id: RequestId,
        mutation: RuleMutation,
        create_id: Option<RuleId>,
        max_rules: u8,
    ) -> Result<RuleMutationOutcome> {
        valid_label(profile)?;
        if self.mutations.iter().any(|r| r.request_id == request_id) {
            return Err(invalid());
        }
        let (record, index, kind) = match &mutation {
            RuleMutation::Create { spec, enabled } => {
                if max_rules == 0
                    || max_rules > MAX_RULES_PER_GRANT
                    || self.rules.len() >= MAX_RULES
                    || self
                        .rules
                        .iter()
                        .filter(|r| r.operator_uid == uid && r.profile == profile)
                        .count()
                        >= usize::from(max_rules)
                {
                    return Err(Error::State("automatic rule capacity exhausted".into()));
                }
                let id = create_id.ok_or_else(invalid)?;
                if self.rules.iter().any(|r| r.id == id)
                    || self.origins.iter().any(|o| o.rule_id == id)
                    || self.mutations.iter().any(|m| m.outcome.rule_id == id)
                {
                    return Err(invalid());
                }
                (
                    RuleRecord {
                        id,
                        revision: 1,
                        operator_uid: uid,
                        profile: profile.into(),
                        spec: spec.clone(),
                        enabled: *enabled,
                        paused_by_user: false,
                        deleted: false,
                        last_job: None,
                    },
                    None,
                    RuleEventKind::Created,
                )
            }
            RuleMutation::Replace {
                rule_id,
                expected_revision,
                ..
            }
            | RuleMutation::SetEnabled {
                rule_id,
                expected_revision,
                ..
            }
            | RuleMutation::Delete {
                rule_id,
                expected_revision,
            } => {
                let old = self.rule(uid, profile, rule_id)?;
                if old.revision != expected_revision.get() {
                    return Err(Error::State("automatic rule revision conflict".into()));
                }
                let index = self
                    .rules
                    .iter()
                    .position(|r| r.id == *rule_id)
                    .ok_or_else(invalid)?;
                let mut record = old.clone();
                record.revision = record.revision.checked_add(1).ok_or_else(invalid)?;
                let kind = match &mutation {
                    RuleMutation::Replace { spec, .. } => {
                        record.spec = spec.clone();
                        RuleEventKind::Replaced
                    }
                    RuleMutation::SetEnabled { enabled, .. } => {
                        record.enabled = *enabled;
                        if *enabled {
                            record.paused_by_user = false;
                        }
                        if *enabled {
                            RuleEventKind::Enabled
                        } else {
                            RuleEventKind::Disabled
                        }
                    }
                    RuleMutation::Delete { .. } => {
                        record.deleted = true;
                        record.enabled = false;
                        RuleEventKind::Deleted
                    }
                    RuleMutation::Create { .. } => return Err(invalid()),
                };
                (record, Some(index), kind)
            }
        };
        let outcome = RuleMutationOutcome {
            rule_id: record.id.clone(),
            revision: record.revision,
            deleted: record.deleted,
        };
        let sequence = self.event(RuleEvent {
            sequence: 0,
            actor_uid: uid,
            operator_uid: uid,
            profile: profile.into(),
            rule_id: record.id.clone(),
            revision: record.revision,
            kind,
            request_id: Some(request_id.clone()),
            job_id: None,
        })?;
        match index {
            Some(index) => self.rules[index] = record,
            None => self.rules.push(record),
        }
        self.mutations.push(MutationRecord {
            sequence,
            request_id,
            uid,
            profile: profile.into(),
            mutation,
            outcome: outcome.clone(),
        });
        self.trim();
        Ok(outcome)
    }

    pub fn fire(&mut self, mut origin: RuleOrigin) -> Result<()> {
        let index = self
            .rules
            .iter()
            .position(|r| r.id == origin.rule_id)
            .ok_or_else(invalid)?;
        let rule = &self.rules[index];
        if rule.revision != origin.revision
            || rule.operator_uid != origin.operator_uid
            || rule.profile != origin.profile
            || !self.allows_occurrence(rule, &origin.observation)
            || self.origins.len() >= MAX_ORIGINS
            || self
                .origins
                .iter()
                .any(|o| o.job_id == origin.job_id || o.request_id == origin.request_id)
        {
            return Err(Error::State(
                "automatic occurrence already consumed or unavailable".into(),
            ));
        }
        origin.sequence = self.event(RuleEvent {
            sequence: 0,
            actor_uid: origin.operator_uid,
            operator_uid: origin.operator_uid,
            profile: origin.profile.clone(),
            rule_id: origin.rule_id.clone(),
            revision: origin.revision,
            kind: RuleEventKind::Fired,
            request_id: Some(origin.request_id.clone()),
            job_id: Some(origin.job_id.clone()),
        })?;
        self.rules[index].last_job = Some(origin.job_id.clone());
        self.origins.push(origin);
        Ok(())
    }

    pub fn pause_profile(&mut self, uid: u32, profile: &str, job_id: &JobId) -> Result<()> {
        let mut events = vec![];
        for rule in self
            .rules
            .iter_mut()
            .filter(|r| r.profile == profile && !r.deleted && r.enabled && !r.paused_by_user)
        {
            rule.revision = rule.revision.checked_add(1).ok_or_else(invalid)?;
            rule.paused_by_user = true;
            events.push(RuleEvent {
                sequence: 0,
                actor_uid: uid,
                operator_uid: rule.operator_uid,
                profile: profile.into(),
                rule_id: rule.id.clone(),
                revision: rule.revision,
                kind: RuleEventKind::PausedByStop,
                request_id: None,
                job_id: Some(job_id.clone()),
            });
        }
        for event in events {
            self.event(event)?;
        }
        Ok(())
    }

    fn event(&mut self, mut event: RuleEvent) -> Result<u64> {
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| Error::State("automatic rule audit sequence exhausted".into()))?;
        event.sequence = self.sequence;
        self.events.push(event);
        self.trim();
        Ok(self.sequence)
    }

    fn trim(&mut self) {
        if self.mutations.len() > MAX_MUTATIONS {
            self.mutations.drain(..self.mutations.len() - MAX_MUTATIONS);
        }
        if self.events.len() > MAX_EVENTS {
            self.events.drain(..self.events.len() - MAX_EVENTS);
        }
    }

    pub fn prune(&mut self, jobs: &[Job], sessions: &[Session]) {
        self.origins
            .retain(|o| jobs.iter().any(|j| j.id == o.job_id));
        self.rules.retain(|r| {
            !r.deleted
                || self.origins.iter().any(|o| {
                    o.rule_id == r.id
                        && jobs
                            .iter()
                            .any(|j| j.id == o.job_id && !released(j, sessions))
                })
        });
        self.trim();
    }

    pub fn validate(&self, jobs: &[Job], sessions: &[Session]) -> Result<()> {
        if self.rules.len() > MAX_RULES
            || self.mutations.len() > MAX_MUTATIONS
            || self.origins.len() > MAX_ORIGINS
            || self.events.len() > MAX_EVENTS
        {
            return Err(invalid());
        }
        let mut ids = BTreeSet::new();
        let mut counts = BTreeMap::new();
        for r in &self.rules {
            valid_label(&r.profile)?;
            let count = counts.entry((r.operator_uid, &r.profile)).or_insert(0usize);
            *count += 1;
            if !ids.insert(&r.id)
                || r.revision == 0
                || r.deleted && r.enabled
                || *count > usize::from(MAX_RULES_PER_GRANT)
            {
                return Err(invalid());
            }
            let last = self.origins.iter().rev().find(|o| o.rule_id == r.id);
            if r.last_job.as_ref() != last.map(|o| &o.job_id) {
                return Err(invalid());
            }
        }
        let mut job_ids = BTreeSet::new();
        let mut requests = BTreeSet::new();
        let mut sequence = 0;
        let mut previous: BTreeMap<&RuleId, &RuleOrigin> = BTreeMap::new();
        for o in &self.origins {
            valid_label(&o.profile)?;
            let job = jobs
                .iter()
                .find(|j| {
                    j.id == o.job_id && j.profile == o.profile && j.operator_uid == o.operator_uid
                })
                .ok_or_else(invalid)?;
            if o.sequence <= sequence
                || o.sequence > self.sequence
                || o.revision == 0
                || !job_ids.insert(&o.job_id)
                || !requests.insert(&o.request_id)
            {
                return Err(invalid());
            }
            sequence = o.sequence;
            if let Some(old) = previous.insert(&o.rule_id, o)
                && (o.revision < old.revision
                    || o.revision == old.revision && o.observation == old.observation)
            {
                return Err(invalid());
            }
            match self.rules.iter().find(|r| r.id == o.rule_id) {
                Some(r)
                    if r.operator_uid == o.operator_uid
                        && r.profile == o.profile
                        && o.revision <= r.revision => {}
                None if released(job, sessions) => {}
                _ => return Err(invalid()),
            }
        }
        let mut mutation_ids = BTreeSet::new();
        let mut mutation_sequences = BTreeSet::new();
        for m in &self.mutations {
            valid_label(&m.profile)?;
            if m.sequence == 0
                || m.sequence > self.sequence
                || !mutation_ids.insert(&m.request_id)
                || !mutation_sequences.insert(m.sequence)
                || self.origins.iter().any(|o| o.sequence == m.sequence)
            {
                return Err(invalid());
            }
            let valid = match &m.mutation {
                RuleMutation::Create { .. } => m.outcome.revision == 1 && !m.outcome.deleted,
                RuleMutation::Replace {
                    rule_id,
                    expected_revision,
                    ..
                }
                | RuleMutation::SetEnabled {
                    rule_id,
                    expected_revision,
                    ..
                } => {
                    *rule_id == m.outcome.rule_id
                        && expected_revision.get().checked_add(1) == Some(m.outcome.revision)
                        && !m.outcome.deleted
                }
                RuleMutation::Delete {
                    rule_id,
                    expected_revision,
                } => {
                    *rule_id == m.outcome.rule_id
                        && expected_revision.get().checked_add(1) == Some(m.outcome.revision)
                        && m.outcome.deleted
                }
            };
            if !valid {
                return Err(invalid());
            }
            if let Some(r) = self.rules.iter().find(|r| r.id == m.outcome.rule_id)
                && (r.operator_uid != m.uid
                    || r.profile != m.profile
                    || r.revision < m.outcome.revision
                    || m.outcome.deleted && (!r.deleted || r.revision != m.outcome.revision))
            {
                return Err(invalid());
            }
        }
        sequence = 0;
        for event in &self.events {
            valid_label(&event.profile)?;
            if event.sequence <= sequence || event.sequence > self.sequence || event.revision == 0 {
                return Err(invalid());
            }
            sequence = event.sequence;
            let fields = match event.kind {
                RuleEventKind::Fired => event.request_id.is_some() && event.job_id.is_some(),
                RuleEventKind::PausedByStop => event.request_id.is_none() && event.job_id.is_some(),
                _ => event.request_id.is_some() && event.job_id.is_none(),
            };
            if !fields {
                return Err(invalid());
            }
            if let Some(r) = self.rules.iter().find(|r| r.id == event.rule_id)
                && (r.operator_uid != event.operator_uid
                    || r.profile != event.profile
                    || r.revision < event.revision)
            {
                return Err(invalid());
            }
            if event.kind == RuleEventKind::Fired {
                if event
                    .job_id
                    .as_ref()
                    .is_some_and(|id| jobs.iter().any(|j| j.id == *id))
                    && !self.origins.iter().any(|o| o.sequence == event.sequence)
                {
                    return Err(invalid());
                }
            } else if event.kind != RuleEventKind::PausedByStop
                && !self.mutations.iter().any(|m| m.sequence == event.sequence)
            {
                return Err(invalid());
            }
            if let Some(o) = self.origins.iter().find(|o| o.sequence == event.sequence)
                && (event.kind != RuleEventKind::Fired
                    || event.rule_id != o.rule_id
                    || event.revision != o.revision
                    || event.actor_uid != o.operator_uid
                    || event.operator_uid != o.operator_uid
                    || event.profile != o.profile
                    || event.request_id.as_ref() != Some(&o.request_id)
                    || event.job_id.as_ref() != Some(&o.job_id))
            {
                return Err(invalid());
            }
            if let Some(m) = self.mutations.iter().find(|m| m.sequence == event.sequence) {
                let kind = match &m.mutation {
                    RuleMutation::Create { .. } => RuleEventKind::Created,
                    RuleMutation::Replace { .. } => RuleEventKind::Replaced,
                    RuleMutation::SetEnabled { enabled: true, .. } => RuleEventKind::Enabled,
                    RuleMutation::SetEnabled { enabled: false, .. } => RuleEventKind::Disabled,
                    RuleMutation::Delete { .. } => RuleEventKind::Deleted,
                };
                if event.kind != kind
                    || event.rule_id != m.outcome.rule_id
                    || event.revision != m.outcome.revision
                    || event.profile != m.profile
                    || event.actor_uid != m.uid
                    || event.operator_uid != m.uid
                    || event.request_id.as_ref() != Some(&m.request_id)
                {
                    return Err(invalid());
                }
            }
        }
        Ok(())
    }
}

#[cfg(test)]
#[path = "rule_journal_tests.rs"]
mod tests;

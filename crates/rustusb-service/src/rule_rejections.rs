//! Strict disk4 original rejection history, separate from positive rule events.
use crate::{automation::RuleMutation, config::valid_label, model::Failure, rules::RuleJournal};
use rustusb_core::{Error, Result, application::RequestId};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const MAX_REJECTIONS: usize = 256;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RejectionRecord {
    pub sequence: u64,
    pub request_id: RequestId,
    pub uid: u32,
    pub profile: String,
    pub mutation: RuleMutation,
    pub failure: Failure,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RejectionJournal {
    pub sequence: u64,
    pub entries: Vec<RejectionRecord>,
}
impl RejectionJournal {
    pub fn find(
        &self,
        uid: u32,
        profile: &str,
        request_id: &RequestId,
    ) -> Result<Option<&RejectionRecord>> {
        let Some(old) = self.entries.iter().find(|r| r.request_id == *request_id) else {
            return Ok(None);
        };
        if old.uid != uid || old.profile != profile {
            return Err(Error::Permission("rule decision identity denied".into()));
        }
        Ok(Some(old))
    }
    pub fn record(
        &mut self,
        uid: u32,
        profile: String,
        request_id: RequestId,
        mutation: RuleMutation,
        failure: Failure,
    ) -> Result<u64> {
        valid_label(&profile)?;
        if failure == Failure::Persistence
            || self.entries.iter().any(|r| r.request_id == request_id)
        {
            return Err(Error::InvalidInput(
                "rule rejection identity/category".into(),
            ));
        }
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| Error::State("rule rejection sequence exhausted".into()))?;
        self.entries.push(RejectionRecord {
            sequence: self.sequence,
            request_id,
            uid,
            profile,
            mutation,
            failure,
        });
        if self.entries.len() > MAX_REJECTIONS {
            self.entries.remove(0);
        }
        Ok(self.sequence)
    }
    pub fn validate(&self, positive: &RuleJournal) -> Result<()> {
        if self.entries.len() > MAX_REJECTIONS {
            return Err(Error::InvalidInput("rule rejection bounds".into()));
        }
        let mut ids = BTreeSet::new();
        let mut previous = self
            .sequence
            .checked_sub(self.entries.len() as u64)
            .ok_or_else(|| Error::InvalidInput("rule rejection sequence range".into()))?;
        for record in &self.entries {
            valid_label(&record.profile)?;
            if record.sequence != previous.saturating_add(1)
                || record.sequence > self.sequence
                || record.failure == Failure::Persistence
                || !ids.insert(record.request_id.as_str())
                || positive
                    .mutations
                    .iter()
                    .any(|m| m.request_id == record.request_id)
            {
                return Err(Error::InvalidInput(
                    "rule rejection ordering/binding".into(),
                ));
            }
            previous = record.sequence;
        }
        if self
            .entries
            .last()
            .is_some_and(|r| r.sequence != self.sequence)
            || self.entries.is_empty() && self.sequence != 0
        {
            return Err(Error::InvalidInput("rule rejection sequence head".into()));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;

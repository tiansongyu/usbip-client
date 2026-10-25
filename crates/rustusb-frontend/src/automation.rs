//! Bounded rule metadata and original mutation identities; never service authority.

use crate::state::Snapshot;
use rustusb_core::application::{RequestId, RuleId};
use rustusb_service::{
    automation::{AutomationGrantInfo, RuleEvent, RuleMutation, RuleView},
    rpc::{Request, Response},
};

#[derive(Debug, Clone)]
/// Independently sampled, bounded automatic-sharing metadata for one profile.
pub struct RulesPage {
    /// Whether this frontend currently requests automation metadata.
    pub requested: bool,
    /// True only after the latest successful Rules read for the selected profile.
    pub current: bool,
    /// Explicit startup policy presence, absent until sampled.
    pub policy_enabled: Option<bool>,
    /// This OS peer's complete fixed-profile grant; never renderer authority.
    pub grant: Option<AutomationGrantInfo>,
    /// At most sixteen complete rule observations.
    pub rules: Vec<RuleView>,
    /// Exclusive rule continuation, independent from jobs and sessions.
    pub next_rule: Option<RuleId>,
    /// Qualification of the retained rule page.
    pub status: String,
    /// At most sixteen independently sequenced rule events.
    pub events: Vec<RuleEvent>,
    /// Exclusive rule-event continuation.
    pub next_event: Option<u64>,
    /// Event read qualification, including retention gaps.
    pub events_status: String,
    /// Last actual edit/query result, not overwritten by metadata polling.
    pub result: String,
}
impl Default for RulesPage {
    fn default() -> Self {
        Self {
            requested: false,
            current: false,
            policy_enabled: None,
            grant: None,
            rules: vec![],
            next_rule: None,
            status: "Not sampled; open Settings".into(),
            events: vec![],
            next_event: None,
            events_status: "Not sampled".into(),
            result: "No rule edit requested".into(),
        }
    }
}

#[derive(Debug, Clone)]
/// Original issued rule edit, retained independently of renderer and Start state.
pub struct UnresolvedRule {
    /// Original fixed profile; selection changes never redirect a query.
    pub profile: String,
    /// Original mutation namespace UUID, never a caller-supplied capability.
    pub request_id: RequestId,
    /// Exact original bounded payload, not automatically resubmitted.
    pub mutation: RuleMutation,
}
impl Snapshot {
    /// Retain identity and payload before issuing a new rule edit; refuse overlaps.
    pub fn rule_request(
        &mut self,
        profile: String,
        mutation: RuleMutation,
        fresh: RequestId,
    ) -> Option<Request> {
        if self.unresolved_rule.is_some() {
            self.rules.result =
                "Original rule edit unresolved; query only its original UUID".into();
            return None;
        }
        self.unresolved_rule = Some(UnresolvedRule {
            profile: profile.clone(),
            request_id: fresh.clone(),
            mutation: mutation.clone(),
        });
        Some(Request::MutateRule {
            profile,
            request_id: fresh,
            mutation,
        })
    }

    /// Query only the original rule edit, including after Expired or a profile change.
    pub fn rule_retry(&self) -> Option<Request> {
        self.unresolved_rule
            .as_ref()
            .map(|intent| Request::RetryRuleMutation {
                profile: intent.profile.clone(),
                request_id: intent.request_id.clone(),
            })
    }

    /// Apply only an exact original edit receipt; no negative reply permits replay.
    pub fn rule_response(&mut self, response: Response) {
        self.rules.result = match response {
            Response::RuleMutationApplied { request_id, outcome, replayed }
                if self.unresolved_rule.as_ref().is_some_and(|i| i.request_id == request_id) => {
                    self.unresolved_rule = None;
                    format!("{} rule {} revision {}; deleted={}; current jobs are independent", if replayed { "Resolved original" } else { "Committed" }, outcome.rule_id, outcome.revision, outcome.deleted)
                }
            Response::RuleMutationExpired { .. } => "Original rule edit is outside retry history; inspect latest rules/events and reconcile with the administrator. New edits remain blocked; no edit was resubmitted".into(),
            Response::Error { code } => format!("Agent refused rule edit: {code:?}; draft and original UUID retained. Inspect latest rules/events; query only the original UUID"),
            _ => "Unexpected rule edit receipt; original UUID retained; no new edit permitted".into(),
        };
        self.status = self.rules.result.clone();
    }
}

#[cfg(test)]
mod tests;

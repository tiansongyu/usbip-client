use super::*;
use crate::{
    Options,
    state::{Mutation, Unresolved},
};
use rustusb_core::{Error, Result, application::JobId};
use rustusb_service::{
    automation::{RearmKind, RuleMutationOutcome, RuleSpec, TriggerKind},
    model::Failure,
};

fn request(n: u8) -> Result<RequestId> {
    RequestId::new(format!("00000000-0000-4000-8000-{n:012}"))
}
fn edit() -> Result<RuleMutation> {
    Ok(RuleMutation::Create {
        spec: RuleSpec::new(
            "Keyboard 界".into(),
            TriggerKind::LocalProfileReady,
            2,
            RearmKind::Once,
        )?,
        enabled: false,
    })
}

#[test]
fn original_payload_and_uuid_precede_issue_and_new_edits_are_blocked() -> Result<()> {
    let mut view = Snapshot::new(Options::default());
    let payload = edit()?;
    assert!(matches!(
        view.rule_request("original".into(), payload.clone(), request(1)?),
        Some(Request::MutateRule { .. })
    ));
    assert!(
        view.rule_request("other".into(), payload.clone(), request(2)?)
            .is_none()
    );
    let intent = view
        .unresolved_rule
        .as_ref()
        .ok_or_else(|| Error::State("lost rule intent".into()))?;
    assert_eq!(intent.profile, "original");
    assert_eq!(intent.request_id, request(1)?);
    assert_eq!(intent.mutation, payload);
    Ok(())
}

#[test]
fn expired_refused_and_profile_reset_only_query_the_original() -> Result<()> {
    let mut view = Snapshot::new(Options::default());
    view.rule_request("first".into(), edit()?, request(1)?);
    view.rule_response(Response::Error {
        code: Failure::State,
    });
    view.rule_response(Response::RuleMutationExpired {
        request_id: request(1)?,
    });
    view.profile = Some("second".into());
    view.clear_profile();
    view.agent_stale();
    match view.rule_retry() {
        Some(Request::RetryRuleMutation {
            profile,
            request_id,
        }) => {
            assert_eq!(profile, "first");
            assert_eq!(request_id, request(1)?);
        }
        other => return Err(Error::State(format!("not an original query: {other:?}"))),
    }
    assert!(
        view.rule_request("second".into(), edit()?, request(2)?)
            .is_none()
    );
    Ok(())
}

#[test]
fn only_an_exact_positive_rule_receipt_clears_rule_identity() -> Result<()> {
    let mut view = Snapshot::new(Options::default());
    view.rule_request("fixed".into(), edit()?, request(1)?);
    let outcome = RuleMutationOutcome {
        rule_id: RuleId::new("00000000-0000-4000-8000-000000000003")?,
        revision: 1,
        deleted: false,
    };
    view.rule_response(Response::RuleMutationApplied {
        request_id: request(2)?,
        outcome: outcome.clone(),
        replayed: true,
    });
    assert!(view.unresolved_rule.is_some());
    view.rule_response(Response::RuleMutationApplied {
        request_id: request(1)?,
        outcome,
        replayed: true,
    });
    assert!(view.unresolved_rule.is_none());
    assert!(view.rules.result.starts_with("Resolved original rule "));
    Ok(())
}

#[test]
fn start_stop_and_rule_queries_keep_separate_namespaces() -> Result<()> {
    let mut view = Snapshot::new(Options::default());
    view.unresolved = Some(Unresolved {
        profile: "start-profile".into(),
        request_id: request(4)?,
    });
    view.rule_request("rule-profile".into(), edit()?, request(1)?);
    let job = JobId::new("00000000-0000-4000-8000-000000000006")?;
    assert!(matches!(
        view.mutation_request(Mutation::StopJob(job), None),
        Some(Request::StopJob { .. })
    ));
    view.mutation_response(Response::StopAccepted {
        owner_present: true,
    });
    assert!(view.unresolved_rule.is_some());
    assert!(
        matches!(view.mutation_request(Mutation::Start("different".into()), None), Some(Request::RetryStart { profile, request_id }) if profile == "start-profile" && request_id == request(4)?)
    );
    assert!(
        matches!(view.mutation_request(Mutation::ResolveRule, None), Some(Request::RetryRuleMutation { profile, request_id }) if profile == "rule-profile" && request_id == request(1)?)
    );
    Ok(())
}

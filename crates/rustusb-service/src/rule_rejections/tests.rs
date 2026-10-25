use super::*;
use crate::{
    automation::{ExpectedRevision, RearmKind, RuleSpec, TriggerKind},
    store::State,
};

type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;
fn id(n: u64) -> Result<RequestId> {
    RequestId::new(format!("00000000-0000-4000-8000-{n:012x}"))
}
fn edit() -> Result<RuleMutation> {
    Ok(RuleMutation::Create {
        spec: RuleSpec::new(
            "bounded".into(),
            TriggerKind::LocalProfileReady,
            2,
            RearmKind::Once,
        )?,
        enabled: false,
    })
}

#[test]
fn rejection_eviction_is_bounded_and_an_expired_identity_is_not_a_negative_receipt() -> TestResult {
    let mut journal = RejectionJournal::default();
    for n in 1..=300 {
        journal.record(1000, "fixed".into(), id(n)?, edit()?, Failure::State)?;
    }
    journal.validate(&RuleJournal::default())?;
    assert_eq!(journal.entries.len(), 256);
    assert_eq!(journal.entries[0].sequence, 45);
    assert_eq!(journal.sequence, 300);
    assert!(journal.find(1000, "fixed", &id(1)?)?.is_none());
    assert_eq!(
        journal
            .find(1000, "fixed", &id(300)?)?
            .ok_or("latest")?
            .sequence,
        300
    );
    assert!(journal.find(1001, "fixed", &id(300)?).is_err());
    assert!(journal.find(1000, "other", &id(300)?).is_err());
    let mut gap = journal.clone();
    gap.entries[10].sequence += 1;
    assert!(gap.validate(&RuleJournal::default()).is_err());
    let mut duplicate = journal.clone();
    duplicate.entries[1].request_id = duplicate.entries[0].request_id.clone();
    assert!(duplicate.validate(&RuleJournal::default()).is_err());
    let mut empty = RejectionJournal {
        sequence: 1,
        entries: vec![],
    };
    assert!(empty.validate(&RuleJournal::default()).is_err());
    empty.sequence = u64::MAX;
    assert!(
        empty
            .record(1000, "fixed".into(), id(301)?, edit()?, Failure::State)
            .is_err()
    );
    Ok(())
}
#[test]
fn strict_rejection_records_cannot_overlap_positive_history_or_claim_persistence_failure()
-> TestResult {
    let mut state = State::default();
    state.enable_rule_decisions()?;
    let positive = state.automation.as_mut().ok_or("positive")?;
    positive.mutate(
        1000,
        "fixed",
        id(1)?,
        edit()?,
        Some(rustusb_core::application::RuleId::new(id(10)?.as_str())?),
        4,
    )?;
    state.rule_rejections.as_mut().ok_or("negative")?.record(
        1000,
        "fixed".into(),
        id(1)?,
        edit()?,
        Failure::State,
    )?;
    assert!(state.validate().is_err());
    let mut journal = RejectionJournal::default();
    assert!(
        journal
            .record(1000, "fixed".into(), id(2)?, edit()?, Failure::Persistence)
            .is_err()
    );
    journal.record(1000, "fixed".into(), id(2)?, edit()?, Failure::Permission)?;
    let value = serde_json::to_value(&journal)?;
    for field in [
        "request_id",
        "uid",
        "profile",
        "mutation",
        "failure",
        "sequence",
    ] {
        let mut bad = value.clone();
        bad["entries"][0]
            .as_object_mut()
            .ok_or("record")?
            .remove(field);
        assert!(serde_json::from_value::<RejectionJournal>(bad).is_err());
    }
    let mut extra = value.clone();
    extra["entries"][0]["path"] = serde_json::json!("/tmp/forged");
    assert!(serde_json::from_value::<RejectionJournal>(extra).is_err());
    let mut bad = value;
    bad["entries"][0]["mutation"] =
        serde_json::json!({"change":"delete","rule_id":id(10)?,"expected_revision":0});
    assert!(serde_json::from_value::<RejectionJournal>(bad).is_err());
    assert!(ExpectedRevision::new(u64::MAX).is_err());
    Ok(())
}

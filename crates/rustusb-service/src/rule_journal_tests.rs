//! Synthetic journal relationships and strict codecs, without USB operations.
use super::*;
use crate::{
    model::*,
    store::{RequestRecord, State},
};
use rustusb_core::{DeviceKey, SessionState, VirtualProfile};
use serde_json::{Value, json};

type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;
fn uuid(n: u64) -> String {
    format!("00000000-0000-0000-0000-{n:012x}")
}
fn token(c: char) -> Result<ObservationToken> {
    ObservationToken::new(c.to_string().repeat(64))
}
fn spec() -> Result<RuleSpec> {
    RuleSpec::new(
        "Desk keyboard".into(),
        TriggerKind::RemoteDeviceAvailable,
        2,
        RearmKind::NewObservation,
    )
}
fn create(j: &mut RuleJournal, n: u64, uid: u32) -> Result<RuleId> {
    let id = RuleId::new(uuid(n))?;
    j.mutate(
        uid,
        "profile",
        RequestId::new(uuid(1000 + n))?,
        RuleMutation::Create {
            spec: spec()?,
            enabled: true,
        },
        Some(id.clone()),
        4,
    )?;
    Ok(id)
}
fn base() -> Result<(State, RuleId, JobId)> {
    let mut state = State::default();
    state.enable_automation()?;
    let journal = state.automation.as_mut().ok_or_else(invalid)?;
    let id = create(journal, 1, 1002)?;
    let job = Job {
        id: JobId::new(uuid(100))?,
        instance_id: InstanceId::new(uuid(99))?,
        profile: "profile".into(),
        operator_uid: 1002,
        state: JobState::Starting,
        stop_requested: false,
        operation_error: None,
    };
    let request_id = RequestId::new(uuid(101))?;
    state.jobs.push(job.clone());
    state.requests.push(RequestRecord {
        id: request_id.clone(),
        uid: 1002,
        profile: "profile".into(),
        job_id: job.id.clone(),
    });
    state.audit(
        1002,
        Some("profile".into()),
        Some(job.id.clone()),
        None,
        AuditAction::Start,
        None,
    )?;
    journal_mut(&mut state)?.fire(RuleOrigin {
        sequence: 0,
        rule_id: id.clone(),
        revision: 1,
        operator_uid: 1002,
        profile: "profile".into(),
        observation: token('a')?,
        request_id,
        job_id: job.id.clone(),
    })?;
    state.preflight(true)?;
    Ok((state, id, job.id))
}
fn journal_mut(s: &mut State) -> Result<&mut RuleJournal> {
    s.automation.as_mut().ok_or_else(invalid)
}

#[test]
fn disk_versions_have_closed_distinct_required_shapes() -> TestResult {
    let old = serde_json::to_value(State::default())?;
    assert_eq!(old.as_object().ok_or("state object")?.len(), 7);
    assert!(old.get("automation").is_none());
    for payload in [Value::Null, json!({})] {
        let mut bad = old.clone();
        bad["automation"] = payload;
        assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    }
    let mut s = State::default();
    s.enable_automation()?;
    let current = serde_json::to_value(&s)?;
    assert_eq!(
        State::decode(&serde_json::to_vec(&current)?)?.schema_version,
        3
    );
    for missing in ["automation", "link_history", "requests", "schema_version"] {
        let mut bad = current.clone();
        bad.as_object_mut().ok_or("state object")?.remove(missing);
        assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    }
    for missing in ["rules", "mutations", "origins", "events", "sequence"] {
        let mut bad = current.clone();
        bad["automation"]
            .as_object_mut()
            .ok_or("journal object")?
            .remove(missing);
        assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    }
    for bad in [json!(null), json!([]), json!(true)] {
        let mut value = current.clone();
        value["automation"] = bad;
        assert!(State::decode(&serde_json::to_vec(&value)?).is_err());
    }
    let mut bad = current.clone();
    bad["schema_version"] = json!(2);
    assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    let mut bad = old.clone();
    bad["schema_version"] = json!(3);
    assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    let legacy =
        br#"{"schema_version":1,"sequence":0,"jobs":[],"sessions":[],"audit":[],"requests":[]}"#;
    let migrated = State::decode(legacy)?;
    assert_eq!(migrated.schema_version, 2);
    assert!(migrated.automation.is_none());
    Ok(())
}

#[test]
fn crud_cas_and_separate_mutation_history_keep_original_results() -> TestResult {
    let mut j = RuleJournal::default();
    let id = create(&mut j, 1, 1002)?;
    for (n, revision, mutation) in [
        (
            2,
            1,
            RuleMutation::Replace {
                rule_id: id.clone(),
                expected_revision: ExpectedRevision::new(1)?,
                spec: RuleSpec::new(
                    "Updated name".into(),
                    TriggerKind::RemoteDeviceAvailable,
                    3,
                    RearmKind::Once,
                )?,
            },
        ),
        (
            3,
            2,
            RuleMutation::SetEnabled {
                rule_id: id.clone(),
                expected_revision: ExpectedRevision::new(2)?,
                enabled: false,
            },
        ),
        (
            4,
            3,
            RuleMutation::SetEnabled {
                rule_id: id.clone(),
                expected_revision: ExpectedRevision::new(3)?,
                enabled: true,
            },
        ),
        (
            5,
            4,
            RuleMutation::Delete {
                rule_id: id.clone(),
                expected_revision: ExpectedRevision::new(4)?,
            },
        ),
    ] {
        let original = serde_json::to_vec(&j)?;
        let stale = RuleMutation::SetEnabled {
            rule_id: id.clone(),
            expected_revision: ExpectedRevision::new(revision + 1)?,
            enabled: true,
        };
        assert!(
            j.mutate(
                1002,
                "profile",
                RequestId::new(uuid(900 + n))?,
                stale,
                None,
                4
            )
            .is_err()
        );
        assert_eq!(serde_json::to_vec(&j)?, original);
        let out = j.mutate(
            1002,
            "profile",
            RequestId::new(uuid(1000 + n))?,
            mutation,
            None,
            4,
        )?;
        assert_eq!(out.revision, revision + 1);
    }
    assert!(j.rules[0].deleted && !j.rules[0].enabled);
    j.prune(&[], &[]);
    assert!(j.rules.is_empty());
    j.validate(&[], &[])?;
    let original = j
        .mutation(1002, "profile", &RequestId::new(uuid(1001))?)?
        .ok_or("original mutation")?;
    assert_eq!(original.outcome.revision, 1);
    assert!(!original.outcome.deleted);
    assert!(j.mutation(1003, "profile", &original.request_id).is_err());
    assert!(j.mutation(1002, "other", &original.request_id).is_err());
    assert!(
        j.mutation(1002, "profile", &RequestId::new(uuid(9999))?)?
            .is_none()
    );
    assert!(
        j.mutate(
            1002,
            "profile",
            original.request_id.clone(),
            RuleMutation::Create {
                spec: spec()?,
                enabled: true
            },
            Some(RuleId::new(uuid(7))?),
            4
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn finite_quotas_and_history_eviction_do_not_reapply_a_mutation() -> TestResult {
    let mut j = RuleJournal::default();
    for uid in 0..16 {
        for n in 0..4 {
            create(&mut j, 1 + u64::from(uid) * 4 + n, uid)?;
        }
    }
    j.validate(&[], &[])?;
    assert_eq!(j.rules.len(), 64);
    assert!(create(&mut j, 65, 16).is_err());
    let mut j = RuleJournal::default();
    let id = create(&mut j, 1, 1002)?;
    for n in 0..300_u64 {
        j.mutate(
            1002,
            "profile",
            RequestId::new(uuid(2000 + n))?,
            RuleMutation::SetEnabled {
                rule_id: id.clone(),
                expected_revision: ExpectedRevision::new(n + 1)?,
                enabled: n % 2 == 0,
            },
            None,
            4,
        )?;
    }
    assert_eq!(j.mutations.len(), 256);
    assert_eq!(j.events.len(), 256);
    assert_eq!(j.sequence, 301);
    assert!(
        j.mutation(1002, "profile", &RequestId::new(uuid(1001))?)?
            .is_none()
    );
    let before = serde_json::to_vec(&j)?;
    assert!(
        j.mutation(1002, "profile", &RequestId::new(uuid(1001))?)?
            .is_none()
    );
    assert_eq!(serde_json::to_vec(&j)?, before);
    j.validate(&[], &[])?;
    Ok(())
}

#[test]
fn occurrence_suppression_survives_start_fifo_and_terminal_job_pruning() -> TestResult {
    let (mut s, id, job_id) = base()?;
    s.jobs[0].state = JobState::Finished;
    s.requests.clear();
    for n in 200..400 {
        s.jobs.push(Job {
            id: JobId::new(uuid(n))?,
            instance_id: InstanceId::new(uuid(99))?,
            profile: "other".into(),
            operator_uid: 1003,
            state: JobState::Finished,
            stop_requested: false,
            operation_error: None,
        });
    }
    s.prune();
    s.validate()?;
    assert!(s.jobs.iter().any(|j| j.id == job_id));
    let j = s.automation.as_ref().ok_or("journal")?;
    let r = j.rule(1002, "profile", &id)?;
    assert!(!j.allows_occurrence(r, &token('a')?));
    assert!(j.allows_occurrence(r, &token('b')?));
    assert_eq!(j.origins.len(), 1);
    assert!(s.requests.is_empty());
    let bytes = serde_json::to_vec(&s)?;
    let restored = State::decode(&bytes)?;
    assert_eq!(
        restored
            .automation
            .as_ref()
            .ok_or("restored journal")?
            .origins[0]
            .job_id,
        job_id
    );
    Ok(())
}

#[test]
fn unresolved_tombstones_keep_the_original_obligation_and_capacity() -> TestResult {
    let (mut s, id, job_id) = base()?;
    journal_mut(&mut s)?.mutate(
        1002,
        "profile",
        RequestId::new(uuid(5000))?,
        RuleMutation::Delete {
            rule_id: id.clone(),
            expected_revision: ExpectedRevision::new(1)?,
        },
        None,
        4,
    )?;
    s.prune();
    assert!(s.automation.as_ref().ok_or("journal")?.rules[0].deleted);
    for n in 2..5 {
        create(journal_mut(&mut s)?, n, 1002)?;
    }
    assert!(create(journal_mut(&mut s)?, 5, 1002).is_err());
    let session_id = SessionId::new(uuid(500))?;
    s.sessions.push(Session {
        id: session_id.clone(),
        instance_id: InstanceId::new(uuid(99))?,
        job_id: job_id.clone(),
        profile: "profile".into(),
        role: SessionRole::Import,
        device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
        principal: None,
        state: SessionState::Error,
        evidence: Evidence::Unconfirmed,
        stop_requested: true,
        terminal: true,
        operation_error: Some(Failure::State),
        cleanup: Some(Cleanup::Unconfirmed),
        metrics: Metrics::default(),
        attempts: vec![],
    });
    s.initialize_legacy_links();
    s.jobs[0].state = JobState::Finished;
    s.prune();
    s.validate()?;
    assert!(
        s.automation
            .as_ref()
            .ok_or("journal")?
            .rules
            .iter()
            .any(|r| r.id == id)
    );
    s.sessions[0].cleanup = Some(Cleanup::Verified);
    s.sessions[0].state = SessionState::Disconnected;
    s.sessions[0].evidence = Evidence::Released;
    s.prune();
    s.validate()?;
    assert!(
        !s.automation
            .as_ref()
            .ok_or("journal")?
            .rules
            .iter()
            .any(|r| r.id == id)
    );
    create(journal_mut(&mut s)?, 5, 1002)?;
    s.preflight(true)?;
    assert!(
        s.automation
            .as_ref()
            .ok_or("journal")?
            .origins
            .iter()
            .any(|o| o.rule_id == id)
    );
    Ok(())
}

#[test]
fn origin_request_and_required_nested_fields_cannot_be_forged_or_forgotten() -> TestResult {
    let (s, _, _) = base()?;
    let good = serde_json::to_value(&s)?;
    for target in ["rules", "mutations", "origins", "events"] {
        let mut bad = good.clone();
        bad["automation"][target][0]["unknown"] = json!(true);
        assert!(
            State::decode(&serde_json::to_vec(&bad)?).is_err(),
            "accepted unknown {target} field"
        );
    }
    for (target, field) in [
        ("rules", "last_job"),
        ("events", "request_id"),
        ("events", "job_id"),
    ] {
        let mut bad = good.clone();
        bad["automation"][target][0]
            .as_object_mut()
            .ok_or("row object")?
            .remove(field);
        assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    }
    for (path, value) in [
        (vec!["automation", "origins", "operator_uid"], json!(1003)),
        (vec!["automation", "origins", "revision"], json!(2)),
        (vec!["automation", "origins", "sequence"], json!(0)),
        (
            vec!["automation", "origins", "observation"],
            json!("A".repeat(64)),
        ),
        (vec!["automation", "rules", "last_job"], Value::Null),
        (vec!["automation", "mutations", "sequence"], json!(0)),
    ] {
        let mut bad = good.clone();
        bad[path[0]][path[1]][0][path[2]] = value;
        assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    }
    let mut bad = good.clone();
    bad["automation"]["rules"] = json!([]);
    assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    let mut bad = good.clone();
    bad["automation"]["origins"] = json!([]);
    bad["automation"]["rules"][0]["last_job"] = Value::Null;
    assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    let mut bad = good.clone();
    bad["automation"]["mutations"] = json!([]);
    assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    let mut bad = good.clone();
    bad["requests"][0]["uid"] = json!(1003);
    assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    let mut bad = good.clone();
    bad["automation"]["mutations"][0]["outcome"]["revision"] = json!(2);
    assert!(State::decode(&serde_json::to_vec(&bad)?).is_err());
    Ok(())
}

#[cfg(target_os = "linux")]
#[test]
fn real_atomic_disk3_reopen_preserves_intent_and_rejects_corruption_without_reset() -> TestResult {
    use crate::store::Store;
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let root = tempfile::tempdir()?;
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700))?;
    let path = root.path().join("state");
    std::fs::create_dir(&path)?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))?;
    let mut store = Store::fixture(&path, root.path())?;
    store.commit(State::default())?;
    let legacy = std::fs::read(path.join("state.json"))?;
    assert!(
        serde_json::from_slice::<Value>(&legacy)?
            .get("automation")
            .is_none()
    );
    let (state, rule, job) = base()?;
    store.commit(state)?;
    let bytes = std::fs::read(path.join("state.json"))?;
    let inode = std::fs::metadata(path.join("state.json"))?.ino();
    assert_eq!(
        std::fs::metadata(path.join("state.json"))?.mode() & 0o7777,
        0o600
    );
    assert!(!path.join("state.next").exists());
    drop(store);
    let store = Store::fixture(&path, root.path())?;
    assert_eq!(store.state.schema_version, 3);
    let j = store.state.automation.as_ref().ok_or("reopened journal")?;
    assert_eq!(j.rules[0].id, rule);
    assert_eq!(j.origins[0].job_id, job);
    assert_eq!(store.state.requests[0].job_id, job);
    assert_eq!(std::fs::metadata(path.join("state.json"))?.ino(), inode);
    assert_eq!(std::fs::read(path.join("state.json"))?, bytes);
    drop(store);
    let mut corrupt: Value = serde_json::from_slice(&bytes)?;
    corrupt["automation"] = Value::Null;
    let corrupt = serde_json::to_vec(&corrupt)?;
    std::fs::write(path.join("state.json"), &corrupt)?;
    assert!(Store::fixture(&path, root.path()).is_err());
    assert_eq!(std::fs::read(path.join("state.json"))?, corrupt);
    Ok(())
}

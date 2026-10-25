use super::*;
use crate::Options;
use rustusb_core::application::{JobId, RequestId};
use rustusb_service::{
    automation::{ExpectedRevision, RearmKind, RuleDecision, RuleMutation, RuleSpec, TriggerKind},
    config::{Backend, Profile},
    model::{Failure, Job, JobState},
    rpc::{Request, Response},
};
use std::{
    io::{BufRead, BufReader, Read, Write},
    os::unix::{
        fs::{PermissionsExt, symlink},
        process::ExitStatusExt,
    },
    process::{Command, Stdio},
};

fn manifest() -> Manifest {
    Manifest {
        schema_version: 1,
        broker_uid: 2000,
        helper_socket: "/run/rustusb/helper.sock".into(),
        control_directory: "/run/rustusb/agent".into(),
        data_directory: "/var/lib/rustusb/agent".into(),
        profiles: vec![Profile {
            id: "original".into(),
            backend: Backend::Client,
            config: "/etc/rustusb/client.json".into(),
        }],
        operators: vec![],
    }
}
fn request(n: u8) -> Result<RequestId> {
    RequestId::new(format!("00000000-0000-4000-8000-{n:012}"))
}
fn intent(n: u8) -> Result<UnresolvedRule> {
    Ok(UnresolvedRule {
        profile: "original".into(),
        request_id: request(n)?,
        settled_protocol: true,
        mutation: RuleMutation::Replace {
            rule_id: rustusb_core::application::RuleId::new(
                "00000000-0000-4000-8000-000000000099",
            )?,
            expected_revision: ExpectedRevision::new(9_007_199_254_740_993)?,
            spec: RuleSpec::new(
                "Preserved 界 draft".into(),
                TriggerKind::RemoteDeviceAvailable,
                2,
                RearmKind::Once,
            )?,
        },
    })
}
fn retained() -> Result<Retained> {
    Ok(Retained {
        start: Some(Unresolved {
            profile: "original".into(),
            request_id: request(1)?,
        }),
        rule: Some(intent(2)?),
        last: Some(RuleDecisionReceipt {
            original: intent(3)?,
            decision: RuleDecision::Rejected {
                sequence: 9_007_199_254_740_993,
                failure: Failure::State,
            },
            replayed: true,
        }),
    })
}
fn fixture() -> Result<(tempfile::TempDir, Recovery)> {
    let root = tempfile::tempdir()?;
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700))?;
    let store = Recovery::open(root.path().join("state"), &manifest())?;
    Ok((root, store))
}
fn bytes(state: Retained) -> Result<Vec<u8>> {
    serde_json::to_vec(&Document::new(state, 2000, &endpoint_key(&manifest())))
        .map_err(|e| Error::State(e.to_string()))
}
#[test]
fn both_original_namespaces_and_last_draft_survive_checked_reopen() -> Result<()> {
    let (root, mut store) = fixture()?;
    store.save(retained()?)?;
    let path = root.path().join("state").join(&store.name);
    assert_eq!(
        std::fs::metadata(&path)?.permissions().mode() & 0o7777,
        0o600
    );
    assert_eq!(std::fs::read(&path)?, bytes(retained()?)?);
    let mut view = Snapshot::new(Options::default());
    drop(store);
    let store = Recovery::open(root.path().join("state"), &manifest())?;
    store.retained.clone().restore(&mut view);
    assert!(store.writable());
    assert!(
        matches!(view.mutation_request(crate::state::Mutation::Start("different".into()), None), Some(Request::RetryStart { profile, request_id }) if profile == "original" && request_id == request(1)?)
    );
    assert!(
        matches!(view.rule_retry(), Some(Request::RetryRuleDecision { profile, request_id }) if profile == "original" && request_id == request(2)?)
    );
    assert_eq!(
        view.unresolved_rule.as_ref().map(|i| &i.mutation),
        Some(&intent(2)?.mutation)
    );
    assert_eq!(
        view.last_rule_decision
            .as_ref()
            .map(|r| r.original.request_id.clone()),
        Some(request(3)?)
    );
    Ok(())
}
#[test]
fn independent_open_descriptions_exclude_same_endpoint_but_not_another() -> Result<()> {
    let (root, mut store) = fixture()?;
    store.save(retained()?)?;
    let mut second = Recovery::open(root.path().join("state"), &manifest())?;
    assert!(!second.writable());
    assert_eq!(bytes(second.retained.clone())?, bytes(retained()?)?);
    assert!(second.save(Retained::default()).is_err());
    let mut other_manifest = manifest();
    other_manifest.control_directory = "/run/rustusb/other".into();
    let other = Recovery::open(root.path().join("state"), &other_manifest)?;
    assert!(other.writable());
    drop(store);
    drop(second);
    assert!(Recovery::open(root.path().join("state"), &manifest())?.writable());
    Ok(())
}
#[test]
fn strict_required_slots_unknown_null_bounds_and_broker_changes_preserve_file() -> Result<()> {
    let (root, mut store) = fixture()?;
    store.save(retained()?)?;
    let path = root.path().join("state").join(&store.name);
    let valid: serde_json::Value =
        serde_json::from_slice(&std::fs::read(&path)?).map_err(|e| Error::State(e.to_string()))?;
    drop(store);
    let mut invalid = vec![vec![], vec![b' '; LIMIT as usize + 1], b"null".to_vec()];
    for name in [
        "schema_version",
        "operator_uid",
        "broker_uid",
        "endpoint",
        "start",
        "rule",
        "last",
    ] {
        let mut missing = valid.clone();
        missing
            .as_object_mut()
            .ok_or_else(|| Error::State("object".into()))?
            .remove(name);
        invalid.push(serde_json::to_vec(&missing).map_err(|e| Error::State(e.to_string()))?);
        let mut null = valid.clone();
        null[name] = serde_json::Value::Null;
        invalid.push(serde_json::to_vec(&null).map_err(|e| Error::State(e.to_string()))?);
    }
    for (pointer, bad) in [
        ("/start/value/profile", serde_json::json!("../other")),
        ("/start/value/request_id", serde_json::json!("not-an-id")),
        ("/rule/value/settled_protocol", serde_json::Value::Null),
        (
            "/last/value/decision/failure",
            serde_json::json!("persistence"),
        ),
        ("/last/value/decision/sequence", serde_json::json!(0)),
    ] {
        let mut value = valid.clone();
        *value
            .pointer_mut(pointer)
            .ok_or_else(|| Error::State("fixture pointer".into()))? = bad;
        invalid.push(serde_json::to_vec(&value).map_err(|e| Error::State(e.to_string()))?);
    }
    let mut unknown = valid.clone();
    unknown["command"] = serde_json::json!("forbidden");
    invalid.push(serde_json::to_vec(&unknown).map_err(|e| Error::State(e.to_string()))?);
    for data in invalid {
        std::fs::write(&path, &data)?;
        assert!(Recovery::open(root.path().join("state"), &manifest()).is_err());
        assert_eq!(std::fs::read(&path)?, data);
    }
    let original = bytes(retained()?)?;
    std::fs::write(&path, &original)?;
    let mut changed = manifest();
    changed.broker_uid = 2001;
    assert!(Recovery::open(root.path().join("state"), &changed).is_err());
    assert_eq!(std::fs::read(&path)?, original);
    Ok(())
}
#[test]
fn foreign_previous_lock_identity_and_links_are_not_overwritten() -> Result<()> {
    let (root, mut store) = fixture()?;
    store.save(retained()?)?;
    let directory = root.path().join("state");
    let path = directory.join(&store.name);
    let original = std::fs::read(&path)?;
    std::fs::write(&path, bytes(Retained::default())?)?;
    assert!(store.save(Retained::default()).is_err());
    assert!(!store.writable());
    assert_eq!(std::fs::read(&path)?, bytes(Retained::default())?);
    drop(store);
    std::fs::write(&path, &original)?;
    let mut store = Recovery::open(directory.clone(), &manifest())?;
    std::fs::remove_file(directory.join(&store.lock_name))?;
    let lock = std::fs::File::create(directory.join(&store.lock_name))?;
    lock.set_permissions(std::fs::Permissions::from_mode(0o600))?;
    assert!(store.save(Retained::default()).is_err());
    assert_eq!(std::fs::read(&path)?, original);
    drop(store);
    drop(lock);
    let other = root.path().join("other");
    std::fs::rename(&path, &other)?;
    symlink(&other, &path)?;
    assert!(Recovery::open(directory.clone(), &manifest()).is_err());
    assert_eq!(std::fs::read(&other)?, original);
    std::fs::remove_file(&path)?;
    std::fs::hard_link(&other, &path)?;
    assert!(Recovery::open(directory, &manifest()).is_err());
    Ok(())
}
#[test]
fn interrupted_prepublication_preserves_original_and_only_removes_own_temporary() -> Result<()> {
    let (root, mut store) = fixture()?;
    store.save(retained()?)?;
    let path = root.path().join("state").join(&store.name);
    let original = std::fs::read(&path)?;
    assert!(
        store
            .save_with(Retained::default(), || Err(Error::State(
                "fixture before publication".into()
            )))
            .is_err()
    );
    assert_eq!(std::fs::read(path)?, original);
    assert_eq!(std::fs::read_dir(root.path().join("state"))?.count(), 2);
    Ok(())
}
#[test]
fn receipt_validation_and_xdg_selection_do_not_create_authority() -> Result<()> {
    let mut state = retained()?;
    if let Some(last) = &mut state.last {
        last.original.settled_protocol = false;
    }
    assert!(state.validate().is_err());
    let mut state = retained()?;
    state.rule = Some(intent(3)?);
    assert!(state.validate().is_err());
    let mut state = retained()?;
    if let Some(start) = &mut state.start {
        start.profile = "../foreign".into();
    }
    assert!(state.validate().is_err());
    assert_eq!(
        directory_from(Some("relative".into()), Some("/home/operator".into())),
        Some("/home/operator/.local/state/rustusb".into())
    );
    assert_eq!(
        directory_from(Some("/state".into()), None),
        Some("/state/rustusb".into())
    );
    assert_eq!(directory_from(None, None), None);
    Ok(())
}
#[test]
fn unrelated_started_reply_cannot_retire_an_original_local_intent() -> Result<()> {
    let mut view = Snapshot::new(Options::default());
    view.unresolved = retained()?.start;
    let request = Request::RetryStart {
        profile: "original".into(),
        request_id: request(1)?,
    };
    let job = Job {
        id: JobId::new("00000000-0000-4000-8000-000000000044")?,
        instance_id: rustusb_core::application::InstanceId::new(
            "00000000-0000-4000-8000-000000000045",
        )?,
        profile: "original".into(),
        operator_uid: rustix::process::geteuid().as_raw(),
        state: JobState::Running,
        stop_requested: false,
        operation_error: None,
    };
    view.mutation_response_to(
        &Request::StopJob {
            job_id: job.id.clone(),
        },
        Response::Started {
            job: job.clone(),
            replayed: true,
        },
    );
    assert!(view.unresolved.is_some());
    let mut wrong = job.clone();
    wrong.profile = "other".into();
    view.mutation_response_to(
        &request,
        Response::Started {
            job: wrong,
            replayed: true,
        },
    );
    assert!(view.unresolved.is_some());
    let wrong_id = Request::RetryStart {
        profile: "original".into(),
        request_id: request_id_for_test()?,
    };
    view.mutation_response_to(
        &wrong_id,
        Response::Started {
            job: job.clone(),
            replayed: true,
        },
    );
    assert!(view.unresolved.is_some());
    view.mutation_response_to(
        &request,
        Response::Started {
            job,
            replayed: true,
        },
    );
    assert!(view.unresolved.is_none());
    Ok(())
}
fn request_id_for_test() -> Result<RequestId> {
    request(9)
}
#[test]
fn child_writer_process() -> Result<()> {
    let Some(directory) = std::env::var_os("RUSTUSB_RECOVERY_TEST_CHILD") else {
        return Ok(());
    };
    let mut store = Recovery::open(directory.into(), &manifest())?;
    store.save(retained()?)?;
    println!("RECOVERY_READY");
    std::io::stdout().flush()?;
    let mut byte = [0u8; 1];
    std::io::stdin().read_exact(&mut byte)?;
    Ok(())
}
#[test]
fn actual_killed_writer_is_reaped_and_original_intents_reopen() -> Result<()> {
    let (root, store) = fixture()?;
    drop(store);
    let directory = root.path().join("state");
    let mut child = Command::new(std::env::current_exe()?)
        .args([
            "--exact",
            "recovery::tests::child_writer_process",
            "--nocapture",
        ])
        .env("RUSTUSB_RECOVERY_TEST_CHILD", &directory)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let inspection = (|| -> Result<()> {
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| Error::State("child stdout".into()))?;
        let mut lines = BufReader::new(stdout);
        let mut ready = false;
        for _ in 0..6 {
            let mut line = String::new();
            if lines.read_line(&mut line)? == 0 {
                break;
            }
            if line.trim() == "RECOVERY_READY" {
                ready = true;
                break;
            }
        }
        if !ready {
            return Err(Error::State(
                "child did not save/hold recovery writer".into(),
            ));
        }
        let second = Recovery::open(directory.clone(), &manifest())?;
        if second.writable() || bytes(second.retained.clone())? != bytes(retained()?)? {
            return Err(Error::State("actual child lock/record mismatch".into()));
        }
        Ok(())
    })();
    // Reap the real child on every inspection result before assertions/errors.
    let kill = child.kill();
    let waited = child.wait();
    inspection?;
    kill?;
    let status = waited?;
    assert_eq!(status.signal(), Some(9));
    let store = Recovery::open(directory, &manifest())?;
    assert!(store.writable());
    assert_eq!(bytes(store.retained.clone())?, bytes(retained()?)?);
    Ok(())
}

#[test]
fn copied_endpoint_document_is_refused_and_manifest_profile_changes_do_not_erase_intents()
-> Result<()> {
    let (root, mut store) = fixture()?;
    store.save(retained()?)?;
    let source = root.path().join("state").join(&store.name);
    let original = std::fs::read(&source)?;
    let mut other = manifest();
    other.control_directory = "/run/rustusb/another".into();
    let target = root
        .path()
        .join("state")
        .join(format!("intent-{}.json", endpoint_key(&other)));
    std::fs::write(&target, &original)?;
    std::fs::set_permissions(&target, std::fs::Permissions::from_mode(0o600))?;
    assert!(Recovery::open(root.path().join("state"), &other).is_err());
    assert_eq!(std::fs::read(target)?, original);
    drop(store);
    let mut changed = manifest();
    changed.profiles[0].id = "renamed".into();
    let restored = Recovery::open(root.path().join("state"), &changed)?;
    assert_eq!(bytes(restored.retained.clone())?, bytes(retained()?)?);
    Ok(())
}

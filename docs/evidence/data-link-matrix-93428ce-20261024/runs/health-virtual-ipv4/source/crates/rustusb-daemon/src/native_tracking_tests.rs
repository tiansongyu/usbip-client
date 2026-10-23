//! Actual temporary-file journal tests; no USB or production sysfs access.
use super::*;
use rustusb_core::application::{
    AttemptId, BrokerCleanupProof as Proof, BrokerCorrelation, BrokerOperationState as State,
    BrokerResourceKind as Kind, InstanceId, SessionId,
};
fn id(n: u32) -> String {
    format!("00000000-0000-0000-0000-{n:012x}")
}
fn correlation(n: u32) -> Result<BrokerCorrelation> {
    Ok(BrokerCorrelation {
        instance_id: InstanceId::new(id(5000))?,
        session_id: SessionId::new(id(5001))?,
        attempt_id: AttemptId::new(id(n + 10000))?,
    })
}
fn record(n: u32, tracked: bool) -> Result<Record> {
    let e = super::tests::export_fixture(&id(n))?;
    Ok(Record {
        channel_id: id(n),
        owner: e.owner,
        target: SecureImportTarget {
            server_id: "test-server".into(),
            device_key: e.registration.device_key,
            remote: e.registration.published_device,
        },
        state: Phase::Prepared {},
        correlation: if tracked { Some(correlation(n)?) } else { None },
    })
}
fn path(root: &tempfile::TempDir) -> std::path::PathBuf {
    root.path().join("state")
}
fn open(root: &tempfile::TempDir) -> Result<Store> {
    // tempfile honors the caller's umask; secure fixture ancestry is explicit.
    std::fs::set_permissions(
        root.path(),
        std::os::unix::fs::PermissionsExt::from_mode(0o700),
    )?;
    Store::open_anchored(
        &path(root),
        root.path(),
        rustix::process::geteuid().as_raw(),
    )
}
fn json(root: &tempfile::TempDir) -> Result<serde_json::Value> {
    serde_json::from_slice(&std::fs::read(path(root).join(DATA))?)
        .map_err(|e| Error::State(e.to_string()))
}
#[test]
fn legacy_bytes_stay_v1_then_tracked_migration_keeps_original_owner_and_phase() -> Result<()> {
    let root = tempfile::tempdir()?;
    let mut store = open(&root)?;
    let legacy = record(1, false)?;
    store.reserve(legacy.clone())?;
    let before = json(&root)?;
    assert_eq!(before["schema_version"], 1);
    assert!(before["records"][0].get("correlation").is_none());
    let mut second = record(2, true)?;
    second.target.server_id = "second-server".into();
    store.reserve(second)?;
    let after = json(&root)?;
    assert_eq!(after["schema_version"], 2);
    assert_eq!(after["records"][0]["correlation"], serde_json::Value::Null);
    let mut without = after["records"][0].clone();
    without
        .as_object_mut()
        .ok_or_else(|| Error::State("missing fixture object".into()))?
        .remove("correlation");
    assert_eq!(without, before["records"][0]);
    drop(store);
    let mut reopened = open(&root)?;
    assert!(reopened.unresolved.contains(&id(1)));
    assert!(matches!(
        reopened.tracked_status(1000, Kind::NativeImport, &correlation(2)?)?,
        Some((
            State::Active {
                phase: rustusb_core::application::BrokerOperationPhase::Unresolved,
                ..
            },
            _
        ))
    ));
    Ok(())
}
#[test]
fn terminal_is_atomic_retained_after_reopen_and_scoped_by_kind_uid() -> Result<()> {
    let root = tempfile::tempdir()?;
    let mut store = open(&root)?;
    store.reserve(record(1, true)?)?;
    store.retire_verified(&id(1), Proof::NotStarted)?;
    let data = json(&root)?;
    assert_eq!(data["records"], serde_json::json!([]));
    assert_eq!(data["terminals"].as_array().map(Vec::len), Some(1));
    drop(store);
    let mut reopened = open(&root)?;
    assert!(reopened.unresolved.is_empty());
    assert!(
        matches!(reopened.tracked_status(1000,Kind::NativeImport,&correlation(1)?)?,Some((State::Terminal{terminal},Some(server))) if terminal.proof==Proof::NotStarted&&server=="test-server")
    );
    assert!(
        reopened
            .tracked_status(1001, Kind::NativeImport, &correlation(1)?)?
            .is_none()
    );
    assert!(
        reopened
            .tracked_status(1000, Kind::NativeStub, &correlation(1)?)?
            .is_none()
    );
    assert!(reopened.reserve(record(1, true)?).is_err());
    assert_eq!(json(&root)?, data);
    Ok(())
}
#[test]
fn capacity_reserves_active_slots_and_only_admission_evicts_oldest_terminal() -> Result<()> {
    let root = tempfile::tempdir()?;
    let mut store = open(&root)?;
    for n in 1..=128 {
        store.reserve(record(n, true)?)?;
        store.retire_verified(&id(n), Proof::NotStarted)?;
    }
    assert_eq!(store.terminals.len(), 128);
    assert!(store.reserve(record(1, true)?).is_err());
    store.reserve(record(129, true)?)?;
    assert_eq!(store.terminals.len(), 127);
    assert!(
        store
            .tracked_status(1000, Kind::NativeImport, &correlation(1)?)?
            .is_none()
    );
    assert!(matches!(
        store.tracked_status(1000, Kind::NativeImport, &correlation(129)?)?,
        Some((State::Active { .. }, _))
    ));
    store.retire_verified(&id(129), Proof::NotStarted)?;
    assert_eq!(store.terminals.len(), 128);
    assert_eq!(json(&root)?["schema_version"], 2);
    Ok(())
}
#[test]
fn new_format_missing_correlation_and_legacy_tracking_field_are_rejected() -> Result<()> {
    let root = tempfile::tempdir()?;
    let mut store = open(&root)?;
    store.reserve(record(1, true)?)?;
    let mut encoded = json(&root)?;
    drop(store);
    encoded["records"][0]
        .as_object_mut()
        .ok_or_else(|| Error::State("fixture object missing".into()))?
        .remove("correlation");
    std::fs::write(
        path(&root).join(DATA),
        serde_json::to_vec(&encoded).map_err(|e| Error::State(e.to_string()))?,
    )?;
    assert!(open(&root).is_err());
    let other = tempfile::tempdir()?;
    let mut store = open(&other)?;
    store.reserve(record(1, false)?)?;
    let mut legacy = json(&other)?;
    drop(store);
    legacy["records"][0]["correlation"] = serde_json::Value::Null;
    std::fs::write(
        path(&other).join(DATA),
        serde_json::to_vec(&legacy).map_err(|e| Error::State(e.to_string()))?,
    )?;
    assert!(open(&other).is_err());
    Ok(())
}
#[test]
fn failure_before_and_after_rename_never_acknowledges_lost_proof() -> Result<()> {
    for after in [false, true] {
        let root = tempfile::tempdir()?;
        let mut store = open(&root)?;
        store.reserve(record(1, true)?)?;
        store.fail_before_rename = !after;
        store.fail_after_rename = after;
        assert!(store.retire_verified(&id(1), Proof::NotStarted).is_err());
        assert!(
            store
                .tracked_status(1000, Kind::NativeImport, &correlation(1)?)
                .is_err()
        );
        drop(store);
        let mut reopened = open(&root)?;
        let observed = reopened.tracked_status(1000, Kind::NativeImport, &correlation(1)?)?;
        assert!(if after {
            matches!(observed, Some((State::Terminal { .. }, _)))
        } else {
            matches!(observed, Some((State::Active { .. }, _)))
        });
    }
    Ok(())
}
#[test]
fn stub_proof_and_parent_occupancy_change_in_one_commit() -> Result<()> {
    let root = tempfile::tempdir()?;
    let mut store = open(&root)?;
    let mut e = super::tests::export_fixture(&id(1))?;
    e.correlation = Some(correlation(1)?);
    e.bound = Some(ExportReceipt {
        generation: e.registration.generation.clone(),
        attribute_device: 1,
        attribute_inode: 3,
        bound_profile: rustusb_native::export_channel::ExportProfile {
            configuration: None,
            interfaces: vec![],
        },
    });
    store.reserve_export(e.clone())?;
    store.reserve_stub(
        &id(1),
        StubRecord {
            channel_id: id(2),
            state: StubPhase::Prepared {},
            correlation: Some(correlation(2)?),
        },
    )?;
    store.retire_stub_verified(&id(1), &id(2), Proof::NotStarted)?;
    assert!(store.exports[&id(1)].stub.is_none());
    assert_eq!(store.terminals.len(), 1);
    assert!(store.exports.contains_key(&id(1)));
    store.retire_export_verified(&id(1), Proof::NativeExportRestored)?;
    assert!(store.exports.is_empty());
    assert_eq!(store.terminals.len(), 2);
    drop(store);
    let mut reopened = open(&root)?;
    assert!(
        matches!(reopened.tracked_status(1000,Kind::NativeStub,&correlation(2)?)?,Some((State::Terminal{terminal},_)) if terminal.proof==Proof::NotStarted)
    );
    Ok(())
}
#[test]
fn verified_cleanup_proof_survives_original_operation_eof_error() -> Result<()> {
    let root = tempfile::tempdir()?;
    let mut store = open(&root)?;
    store.reserve(record(1, true)?)?;
    let operation: Result<()> = Err(Error::State("control EOF fixture".into()));
    let cleanup = store.retire_verified(&id(1), Proof::NotStarted);
    assert!(cleanup.and(operation).is_err());
    assert!(matches!(
        store.tracked_status(1000, Kind::NativeImport, &correlation(1)?)?,
        Some((State::Terminal { .. }, _))
    ));
    Ok(())
}
#[test]
fn wrong_proof_or_duplicate_attempt_never_removes_active_record() -> Result<()> {
    let root = tempfile::tempdir()?;
    let mut store = open(&root)?;
    store.reserve(record(1, true)?)?;
    let before = json(&root)?;
    assert!(
        store
            .retire_verified(&id(1), Proof::PhysicalBaselineRestored)
            .is_err()
    );
    assert_eq!(json(&root)?, before);
    let mut r = record(2, true)?;
    r.target.server_id = "second-server".into();
    r.correlation = Some(correlation(1)?);
    assert!(store.reserve(r).is_err());
    assert_eq!(json(&root)?, before);
    Ok(())
}

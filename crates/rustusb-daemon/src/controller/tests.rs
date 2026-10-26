use super::*;
#[tokio::test]
async fn dropping_waiter_retains_actual_mutation_guard_and_quota() -> Result<()> {
    let state = Arc::new(tokio::sync::Mutex::new(
        crate::linux::ManagedState::default(),
    ));
    let slots = Arc::new(tokio::sync::Semaphore::new(1));
    let guard = state.clone().lock_owned().await;
    let permit = slots
        .clone()
        .acquire_owned()
        .await
        .map_err(|_| Error::State("test quota closed".into()))?;
    let (entered_tx, entered_rx) = tokio::sync::oneshot::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel();
    let (done_tx, done_rx) = tokio::sync::oneshot::channel();
    let waiter = tokio::spawn(run_owned(guard, permit, move |_| {
        let _ = entered_tx.send(());
        release_rx
            .recv()
            .map_err(|_| Error::State("test gate closed".into()))?;
        let _ = done_tx.send(());
        Ok(())
    }));
    tokio::time::timeout(Duration::from_secs(3), entered_rx)
        .await
        .map_err(|_| Error::Timeout("test worker did not start".into()))?
        .map_err(|_| Error::State("test worker exited".into()))?;
    waiter.abort();
    let result = waiter.await;
    assert!(result.is_err());
    assert!(state.try_lock().is_err());
    assert_eq!(slots.available_permits(), 0);
    release_tx
        .send(())
        .map_err(|_| Error::State("test worker lost gate".into()))?;
    done_rx
        .await
        .map_err(|_| Error::State("test worker did not finish".into()))?;
    let _guard = tokio::time::timeout(Duration::from_secs(3), state.lock())
        .await
        .map_err(|_| Error::Timeout("actual guard not released".into()))?;
    let _permit = tokio::time::timeout(Duration::from_secs(3), slots.acquire())
        .await
        .map_err(|_| Error::Timeout("actual quota not released".into()))?
        .map_err(|_| Error::State("test quota closed".into()))?;
    Ok(())
}
#[test]
fn strict_controller_wire_rejects_unknown_fields_and_arbitrary_paths() {
    for input in [
        r#"{"op":"controller","command":{"action":"list","extra":true}}"#,
        r#"{"op":"controller","command":{"action":"inspect","address":"/sys/devices"}}"#,
        r#"{"op":"controller","command":{"action":"list"},"path":"/tmp/state"}"#,
    ] {
        assert!(serde_json::from_str::<crate::Request>(input).is_err());
    }
    assert!(
        serde_json::from_str::<crate::Request>(
            r#"{"op":"controller","command":{"action":"list"}}"#
        )
        .is_ok()
    );
}

#[test]
fn restarted_service_has_new_epoch_and_no_current_pins() -> Result<()> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let temp = tempfile::tempdir()?;
    std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700))?;
    let path = temp.path().join("controllers");
    let uid = std::fs::metadata(temp.path())?.uid();
    let mut store = store::Store::open_anchored(&path, temp.path(), uid)?;
    let change = Change {
        policy_id: PolicyId::new("10000000-0000-0000-0000-000000000001")?,
        request_id: RequestId::new("20000000-0000-0000-0000-000000000001")?,
        expected_revision: 0,
        mutation: Mutation::Enroll {
            selector: Selector {
                address: "0000:00:06.0".parse()?,
                vendor_id: 1,
                device_id: 2,
                subsystem_vendor_id: 3,
                subsystem_device_id: 4,
            },
            expected_boot: "30000000-0000-0000-0000-000000000001".into(),
        },
    };
    let selector = store.selection(&change)?;
    store.begin(&change, selector)?;
    let first = Service::from_store(
        store,
        InstanceId::new("40000000-0000-0000-0000-000000000001")?,
    )?;
    let before = first.snapshot()?;
    drop(first);
    let store = store::Store::open_anchored(&path, temp.path(), uid)?;
    let second = Service::from_store(
        store,
        InstanceId::new("40000000-0000-0000-0000-000000000002")?,
    )?;
    let after = second.snapshot()?;
    assert_ne!(before.helper_epoch, after.helper_epoch);
    assert_eq!(before.historical, after.historical);
    assert!(after.verified_in_this_helper.is_empty());
    // Exact pending replay returns history before any production sysfs open.
    let replay = second.execute(change, |_| {
        Err(Error::State(
            "must not reach kernel conflict callback".into(),
        ))
    })?;
    assert_eq!(replay["historical"], true);
    assert_eq!(replay["record"]["outcome"]["state"], "pending");
    Ok(())
}

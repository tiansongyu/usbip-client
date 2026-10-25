use super::*;
use rustusb_core::BusId;
use rustusb_linux::observation::ObservationStamp;
use std::sync::{Mutex, mpsc};

fn record(bus: &str, inode: u64, address: u8) -> Result<ObservedDevice> {
    Ok(ObservedDevice {
        device: Device {
            id: format!("usb-{}", "a".repeat(64)),
            bus_id: BusId::new(bus)?,
            vendor_id: 0x1234,
            product_id: 0x5678,
            manufacturer: Some("Fixture".into()),
            product: Some("Fixture".into()),
            serial: Some("sensitive-test-value".into()),
            device_address: address.into(),
            usb_version: "2.00".into(),
            speed: "480".into(),
            class: 3,
            subclass: 0,
            protocol: 0,
            topology: "pci/test/port2".into(),
            driver: None,
            interfaces: Vec::new(),
            exported: false,
            occupied: None,
            export_blockers: Vec::new(),
        },
        stamp: ObservationStamp {
            sysfs_device: 1,
            sysfs_inode: inode,
            bus_number: 1,
            device_number: address,
        },
    })
}
fn views() -> Views {
    Views {
        instance: "fixture-epoch".into(),
        started: Instant::now(),
        revision: 0,
        last: None,
        counters: DiscoveryCounters::default(),
        receive_buffer: 131072,
    }
}

#[tokio::test]
async fn identical_periodic_scan_refreshes_source_time_without_a_wire_revision() -> Result<()> {
    let (sender, receiver) = watch::channel(None);
    let mut source = views();
    let mut subscription = DeviceSubscription {
        receiver,
        revision: None,
    };
    source.publish(
        &sender,
        Ok(vec![record("1-2", 1, 4)?]),
        DiscoveryReason::Initial,
    )?;
    let first = subscription.next().await?.snapshot;
    let mut old = (*first).clone();
    old.sampled_at = Instant::now() - Duration::from_secs(20);
    let old = Arc::new(old);
    source.last = Some(old.clone());
    sender.send_replace(Some(old.clone()));
    source.publish(
        &sender,
        Ok(vec![record("1-2", 1, 4)?]),
        DiscoveryReason::Periodic,
    )?;
    let current = subscription
        .latest()
        .ok_or_else(|| Error::State("latest source metadata missing".into()))?;
    assert_eq!(current.revision, first.revision);
    assert_eq!(current.devices, first.devices);
    assert_eq!(current.counters.scans, 2);
    assert!(current.sampled_at.duration_since(old.sampled_at) >= Duration::from_secs(20));
    assert!(
        tokio::time::timeout(Duration::from_millis(10), subscription.next())
            .await
            .is_err()
    );
    Ok(())
}

#[test]
fn failed_scans_preserve_successful_inventory_and_never_fabricate_removals() -> Result<()> {
    let (sender, receiver) = watch::channel(None);
    let mut views = views();
    views.publish(
        &sender,
        Err(io::Error::from(io::ErrorKind::PermissionDenied).into()),
        DiscoveryReason::Initial,
    )?;
    let first = receiver
        .borrow()
        .clone()
        .ok_or_else(|| Error::State("missing first view".into()))?;
    assert_eq!(first.failure, Some(DiscoveryFailure::Permission));
    assert!(first.devices.is_none() && first.changes.is_empty());
    views.publish(
        &sender,
        Ok(vec![record("1-2", 1, 4)?]),
        DiscoveryReason::Periodic,
    )?;
    let before = receiver
        .borrow()
        .clone()
        .ok_or_else(|| Error::State("missing good view".into()))?;
    assert_eq!(before.state, DiscoveryState::Current);
    assert_eq!(before.reason, DiscoveryReason::Resync);
    views.publish(
        &sender,
        Err(io::Error::from(io::ErrorKind::NotFound).into()),
        DiscoveryReason::Periodic,
    )?;
    let failed = receiver
        .borrow()
        .clone()
        .ok_or_else(|| Error::State("missing stale view".into()))?;
    assert_eq!(failed.state, DiscoveryState::Stale);
    assert!(failed.changes.is_empty());
    assert_eq!(failed.devices.as_deref(), before.devices.as_deref());
    views.publish(&sender, Ok(Vec::new()), DiscoveryReason::Periodic)?;
    let empty = receiver
        .borrow()
        .clone()
        .ok_or_else(|| Error::State("missing empty view".into()))?;
    assert_eq!(empty.state, DiscoveryState::Current);
    assert_eq!(empty.devices.as_ref().map(|records| records.len()), Some(0));
    assert_eq!(empty.changes[0].kind, DeviceChangeKind::Removed);
    Ok(())
}

#[test]
fn generation_metadata_and_candidate_address_changes_have_distinct_observations() -> Result<()> {
    let original = record("1-2", 1, 4)?;
    let mut changed = original.clone();
    changed.device.driver = Some("usbip-host".into());
    changed.device.exported = true;
    assert_eq!(
        changes(
            Some(std::slice::from_ref(&original)),
            std::slice::from_ref(&changed)
        )[0]
        .kind,
        DeviceChangeKind::MetadataChanged
    );
    changed.stamp.sysfs_inode = 2;
    assert_eq!(
        changes(
            Some(std::slice::from_ref(&original)),
            std::slice::from_ref(&changed)
        )[0]
        .kind,
        DeviceChangeKind::GenerationChanged
    );
    let moved = record("9-2", 3, 42)?;
    assert_eq!(original.device.id, moved.device.id);
    let moved_changes = changes(Some(&[original]), &[moved]);
    assert_eq!(
        moved_changes
            .iter()
            .map(|change| change.kind)
            .collect::<Vec<_>>(),
        vec![DeviceChangeKind::Removed, DeviceChangeKind::Added]
    );
    Ok(())
}

#[test]
fn capacity_rejection_preserves_prior_devices_instead_of_truncating() -> Result<()> {
    let mut views = views();
    let (sender, receiver) = watch::channel(None);
    let original = record("1-2", 1, 4)?;
    views.publish(
        &sender,
        Ok(vec![original.clone()]),
        DiscoveryReason::Initial,
    )?;
    views.publish(
        &sender,
        Ok(vec![original.clone(); MAX_DEVICES + 1]),
        DiscoveryReason::UsbHint,
    )?;
    let failed = receiver
        .borrow()
        .clone()
        .ok_or_else(|| Error::State("missing capacity view".into()))?;
    assert_eq!(failed.failure, Some(DiscoveryFailure::Capacity));
    assert_eq!(
        failed.devices.as_ref().map(|records| records.len()),
        Some(1)
    );
    assert!(failed.changes.is_empty());
    let mut large = original.clone();
    large.device.product = Some("x".repeat(MAX_DEVICE_BYTES));
    assert_eq!(bounded(vec![large]).err(), Some(DiscoveryFailure::Capacity));
    let mut aggregate = original;
    aggregate.device.product = Some("x".repeat(32 * 1024));
    assert_eq!(
        bounded(vec![aggregate; 130]).err(),
        Some(DiscoveryFailure::Capacity)
    );
    Ok(())
}

#[tokio::test]
async fn latest_view_skips_are_explicit_and_metadata_subscriptions_do_not_own_workers() -> Result<()>
{
    let mut views = views();
    let (sender, receiver) = watch::channel(None);
    let mut subscriber = DeviceSubscription {
        receiver,
        revision: None,
    };
    views.publish(
        &sender,
        Ok(vec![record("1-2", 1, 4)?]),
        DiscoveryReason::Initial,
    )?;
    assert!(subscriber.next().await?.resync_required);
    views.publish(
        &sender,
        Ok(vec![record("1-2", 2, 5)?]),
        DiscoveryReason::UsbHint,
    )?;
    views.publish(
        &sender,
        Ok(vec![record("1-2", 3, 6)?]),
        DiscoveryReason::UsbHint,
    )?;
    let skipped = subscriber.next().await?;
    assert_eq!(skipped.snapshot.revision, 3);
    assert!(skipped.resync_required);
    views.publish(
        &sender,
        Ok(vec![record("1-2", 4, 7)?]),
        DiscoveryReason::UsbHint,
    )?;
    assert!(!subscriber.next().await?.resync_required);
    drop(sender);
    assert!(subscriber.next().await.is_err());
    Ok(())
}

#[tokio::test]
async fn actual_blocking_scan_is_joined_and_holds_worker_capacity_after_stop() -> Result<()> {
    let pool = Arc::new(Semaphore::new(1));
    let (entered, started) = oneshot::channel();
    let mut entered = Some(entered);
    let (release, held) = mpsc::sync_channel(1);
    let owner = launch(pool.clone(), move || {
        if let Some(entered) = entered.take() {
            let _ = entered.send(());
            held.recv_timeout(Duration::from_secs(5))
                .map_err(|error| Error::State(format!("test scan gate: {error}")))?;
        }
        Ok(Vec::new())
    })
    .await?;
    tokio::time::timeout(Duration::from_secs(2), started)
        .await
        .map_err(|_| Error::Timeout("test scan entry".into()))?
        .map_err(|_| Error::State("test scan disappeared".into()))?;
    let mut subscription = owner.subscribe();
    let initial = subscription.next().await?;
    assert_eq!(initial.snapshot.state, DiscoveryState::Initializing);
    assert!(initial.snapshot.devices.is_none());
    assert!(initial.snapshot.changes.is_empty());
    let mut joining = tokio::spawn(owner.shutdown());
    assert!(
        tokio::time::timeout(Duration::from_millis(50), &mut joining)
            .await
            .is_err()
    );
    assert_eq!(pool.available_permits(), 0);
    assert!(permit(pool.clone()).is_err());
    release
        .send(())
        .map_err(|_| Error::State("test scan gate ended".into()))?;
    tokio::time::timeout(Duration::from_secs(2), joining)
        .await
        .map_err(|_| Error::Timeout("test monitor join".into()))?
        .map_err(|error| Error::State(error.to_string()))??;
    assert_eq!(pool.available_permits(), 1);
    Ok(())
}

#[tokio::test]
async fn dropping_owner_requests_stop_without_returning_a_blocked_workers_permit() -> Result<()> {
    let pool = Arc::new(Semaphore::new(1));
    let (entered, started) = oneshot::channel();
    let mut entered = Some(entered);
    let (release, held) = mpsc::sync_channel(1);
    let mut owner = launch(pool.clone(), move || {
        if let Some(entered) = entered.take() {
            let _ = entered.send(());
            held.recv_timeout(Duration::from_secs(5))
                .map_err(|error| Error::State(error.to_string()))?;
        }
        Ok(Vec::new())
    })
    .await?;
    tokio::time::timeout(Duration::from_secs(2), started)
        .await
        .map_err(|_| Error::Timeout("test entry".into()))?
        .map_err(|_| Error::State("test worker ended".into()))?;
    let original = owner
        .worker
        .take()
        .ok_or_else(|| Error::State("missing worker".into()))?;
    let subscription = owner.subscribe();
    drop(owner);
    assert_eq!(pool.available_permits(), 0);
    release
        .send(())
        .map_err(|_| Error::State("test gate ended".into()))?;
    tokio::time::timeout(Duration::from_secs(2), original)
        .await
        .map_err(|_| Error::Timeout("test actual join".into()))?
        .map_err(|error| Error::State(error.to_string()))??;
    assert_eq!(pool.available_permits(), 1);
    // Retaining metadata has not retained worker quota or its source socket.
    drop(subscription);
    Ok(())
}

#[tokio::test]
async fn periodic_reconciliation_recovers_without_a_fabricated_kernel_event() -> Result<()> {
    let pool = Arc::new(Semaphore::new(1));
    let records = Arc::new(Mutex::new(Some(vec![record("1-2", 1, 4)?])));
    let source = records.clone();
    let owner = launch(pool, move || {
        source
            .lock()
            .map_err(|_| Error::State("test source lock".into()))?
            .clone()
            .ok_or_else(|| io::Error::from(io::ErrorKind::PermissionDenied).into())
    })
    .await?;
    let mut subscription = owner.subscribe();
    let first = tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let update = subscription.next().await?;
            if update.snapshot.state == DiscoveryState::Current {
                return Ok::<_, Error>(update);
            }
        }
    })
    .await
    .map_err(|_| Error::Timeout("test first inventory".into()))??;
    assert_eq!(first.snapshot.reason, DiscoveryReason::Initial);
    *records
        .lock()
        .map_err(|_| Error::State("test lock".into()))? = None;
    let failed = tokio::time::timeout(Duration::from_secs(3), subscription.next())
        .await
        .map_err(|_| Error::Timeout("test failed scan".into()))??;
    assert_eq!(failed.snapshot.state, DiscoveryState::Stale);
    assert_eq!(
        failed.snapshot.devices.as_deref(),
        first.snapshot.devices.as_deref()
    );
    *records
        .lock()
        .map_err(|_| Error::State("test lock".into()))? = Some(vec![record("1-2", 2, 5)?]);
    let recovered = tokio::time::timeout(Duration::from_secs(3), subscription.next())
        .await
        .map_err(|_| Error::Timeout("test recovered scan".into()))??;
    assert_eq!(recovered.snapshot.reason, DiscoveryReason::Resync);
    assert!(recovered.resync_required);
    assert_eq!(
        recovered.snapshot.changes[0].kind,
        DeviceChangeKind::GenerationChanged
    );
    owner.shutdown().await
}

#[tokio::test]
async fn cancelling_one_shot_waiter_does_not_cancel_started_read_or_return_capacity() -> Result<()>
{
    let pool = Arc::new(Semaphore::new(1));
    let (entered, started) = oneshot::channel();
    let (release, held) = mpsc::sync_channel(1);
    let finished = Arc::new(AtomicBool::new(false));
    let actual_finished = finished.clone();
    let work_pool = pool.clone();
    let waiter = tokio::spawn(async move {
        scan_work(work_pool, move || {
            let _ = entered.send(());
            held.recv_timeout(Duration::from_secs(5))
                .map_err(|error| Error::State(error.to_string()))?;
            actual_finished.store(true, Ordering::Release);
            Ok(Vec::new())
        })
        .await
    });
    tokio::time::timeout(Duration::from_secs(2), started)
        .await
        .map_err(|_| Error::Timeout("test one-shot entry".into()))?
        .map_err(|_| Error::State("test one-shot ended".into()))?;
    waiter.abort();
    assert!(waiter.await.is_err());
    assert_eq!(pool.available_permits(), 0);
    assert!(!finished.load(Ordering::Acquire));
    release
        .send(())
        .map_err(|_| Error::State("test gate ended".into()))?;
    tokio::time::timeout(Duration::from_secs(2), async {
        while pool.available_permits() == 0 {
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    })
    .await
    .map_err(|_| Error::Timeout("test actual read return".into()))?;
    assert!(finished.load(Ordering::Acquire));
    Ok(())
}

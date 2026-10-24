//! State boundaries and real loopback TLS ownership; no USB/helper operations.
use super::*;
use rustusb_core::{BusId, VirtualProfile};
use rustusb_core::{Host, Peer};
use rustusb_protocol::{RemoteDevice, RemoteInterface};
use std::{net::SocketAddr, num::NonZeroU16};
use tokio::{
    io::AsyncReadExt,
    net::{TcpListener, TcpStream},
    sync::oneshot,
};

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn configured(
    tls: rustusb_transport::TlsClient,
    address: SocketAddr,
) -> TestResult<Arc<LoadedClientConfig>> {
    Ok(Arc::new(LoadedClientConfig {
        broker_uid: 2000,
        server_id: "test-server".into(),
        endpoint: Peer {
            host: Host::new(address.ip().to_string())?,
            port: NonZeroU16::new(address.port()).ok_or("zero test port")?,
        },
        server_name: "server.test".into(),
        device: DeviceKey::virtual_profile(VirtualProfile::Hid),
        tls,
    }))
}

fn start(config: Arc<LoadedClientConfig>, owners: Arc<Semaphore>) -> Result<CatalogMonitor> {
    CatalogMonitor::launch(
        CatalogSource::secure(config, Duration::from_secs(5))?,
        RefreshInterval::default(),
        owners,
    )
}

async fn state(
    subscription: &mut CatalogSubscription,
    expected: CatalogState,
) -> TestResult<CatalogUpdate> {
    tokio::time::timeout(Duration::from_secs(5), async {
        loop {
            let next = subscription.next().await?;
            if next.snapshot.state == expected {
                return Ok::<_, Error>(next);
            }
        }
    })
    .await?
    .map_err(Into::into)
}

#[tokio::test]
async fn actual_raw_and_framed_refresh_address_then_empty_without_usb() -> TestResult {
    for framed in [false, true] {
        let (server, client) = crate::jobs::management_tests::credentials(framed)?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let config = configured(client, listener.local_addr()?)?;
        let first = device(VirtualProfile::Hid, "1-1")?;
        let mut moved = first.clone();
        moved.device.bus_id = BusId::new("1-4")?;
        moved.device.device_number = 4;
        let peer = tokio::spawn(crate::jobs::management_tests::serve_catalog_sequence(
            server,
            listener,
            vec![vec![first], vec![moved], Vec::new()],
        ));
        let owner = start(config, Arc::new(Semaphore::new(2)))?;
        let mut subscription = owner.subscribe();
        let initial = state(&mut subscription, CatalogState::Current).await?;
        assert_eq!(initial.snapshot.attempts, 1);
        assert_eq!(initial.snapshot.devices.as_deref().map(<[_]>::len), Some(1));
        let changed = state(&mut subscription, CatalogState::Current).await?;
        assert_eq!(changed.snapshot.attempts, 2);
        assert_eq!(changed.snapshot.changes.len(), 1);
        assert_eq!(
            changed.snapshot.changes[0].kind,
            CatalogChangeKind::AddressChanged
        );
        let empty = state(&mut subscription, CatalogState::Current).await?;
        assert_eq!(empty.snapshot.attempts, 3);
        assert_eq!(empty.snapshot.devices.as_deref(), Some([].as_slice()));
        assert_eq!(empty.snapshot.changes[0].kind, CatalogChangeKind::Removed);
        owner.shutdown().await?;
        peer.await??;
    }
    Ok(())
}

#[tokio::test]
async fn actual_refused_connection_retains_old_catalog_and_recovery_resynchronizes() -> TestResult {
    let (server, client) = crate::jobs::management_tests::credentials(false)?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let config = configured(client, address)?;
    let peer = tokio::spawn(crate::jobs::management_tests::serve_catalog_sequence(
        server.clone(),
        listener,
        vec![vec![device(VirtualProfile::Hid, "1-1")?]],
    ));
    let owner = start(config, Arc::new(Semaphore::new(2)))?;
    let mut subscription = owner.subscribe();
    let first = state(&mut subscription, CatalogState::Current).await?;
    peer.await??;
    let stale = state(&mut subscription, CatalogState::Stale).await?;
    assert_eq!(stale.snapshot.failure, Some(CatalogFailure::Io));
    assert_eq!(stale.snapshot.devices, first.snapshot.devices);
    assert!(stale.snapshot.changes.is_empty());
    let listener = TcpListener::bind(address).await?;
    let peer = tokio::spawn(crate::jobs::management_tests::serve_catalog_sequence(
        server,
        listener,
        vec![Vec::new()],
    ));
    let recovered = state(&mut subscription, CatalogState::Current).await?;
    assert!(recovered.resync_required);
    assert_eq!(recovered.snapshot.devices.as_deref(), Some([].as_slice()));
    assert_eq!(
        recovered.snapshot.changes[0].kind,
        CatalogChangeKind::Removed
    );
    owner.shutdown().await?;
    peer.await??;
    Ok(())
}

#[tokio::test]
async fn framed_stop_joins_before_releasing_the_actual_reverse_fin_gate() -> TestResult {
    let (server, client) = crate::jobs::management_tests::credentials(true)?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let config = configured(client, listener.local_addr()?)?;
    let (fin, observed) = oneshot::channel();
    let (release, released) = oneshot::channel();
    let peer = tokio::spawn(crate::jobs::management_tests::serve_held_catalog(
        server, listener, fin, released,
    ));
    let owner = start(config, Arc::new(Semaphore::new(2)))?;
    let mut subscription = owner.subscribe();
    tokio::time::timeout(Duration::from_secs(3), observed).await??;
    let mut closing = tokio::spawn(owner.shutdown());
    let completed = tokio::time::timeout(Duration::from_secs(2), &mut closing).await;
    let before_release = completed.is_ok();
    release.send(()).map_err(|_| "peer release lost")?;
    peer.await??;
    match completed {
        Ok(result) => result??,
        Err(_) => closing.await??,
    }
    assert!(
        before_release,
        "observer join required the later fixture release"
    );
    let stopped = state(&mut subscription, CatalogState::Stopped).await?;
    assert!(
        stopped.snapshot.devices.is_none(),
        "parsed but unclosed response became Current"
    );
    Ok(())
}

async fn held_peer(listener: &TcpListener) -> TestResult<TcpStream> {
    let (mut peer, _) = tokio::time::timeout(Duration::from_secs(2), listener.accept()).await??;
    let mut header = [0; 5];
    tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut header)).await??;
    assert_eq!(header[0], 22);
    let mut hello = vec![0; usize::from(u16::from_be_bytes([header[3], header[4]]))];
    tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut hello)).await??;
    Ok(peer)
}

#[tokio::test]
async fn actual_owner_quota_drop_and_metadata_subscription_do_not_retain_the_socket() -> TestResult
{
    let (_server, client) = crate::jobs::management_tests::credentials(false)?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let config = configured(client, listener.local_addr()?)?;
    let pool = Arc::new(Semaphore::new(2));
    let a = start(config.clone(), pool.clone())?;
    let mut a_events = a.subscribe();
    let mut peer_a = held_peer(&listener).await?;
    let b = start(config.clone(), pool.clone())?;
    let mut peer_b = held_peer(&listener).await?;
    assert!(start(config.clone(), pool.clone()).is_err());
    drop(a);
    assert_eq!(
        state(&mut a_events, CatalogState::Stopped)
            .await?
            .snapshot
            .state,
        CatalogState::Stopped
    );
    assert!(
        tokio::time::timeout(Duration::from_secs(2), a_events.next())
            .await?
            .is_err()
    );
    let mut tail = Vec::new();
    tokio::time::timeout(Duration::from_secs(2), peer_a.read_to_end(&mut tail)).await??;
    assert!(tail.is_empty());
    assert_eq!(pool.available_permits(), 1);
    let c = start(config, pool.clone())?;
    c.shutdown().await?;
    b.shutdown().await?;
    tokio::time::timeout(Duration::from_secs(2), peer_b.read_to_end(&mut tail)).await??;
    assert!(tail.is_empty());
    assert_eq!(pool.available_permits(), 2);
    Ok(())
}

fn device(profile: VirtualProfile, bus: &str) -> Result<CatalogDevice> {
    Ok(CatalogDevice {
        device_key: DeviceKey::virtual_profile(profile),
        device: RemoteDevice {
            bus_id: BusId::new(bus)?,
            path: "/test/catalog".into(),
            bus_number: 1,
            device_number: 2,
            speed: 2,
            vendor_id: 0x1234,
            product_id: 1,
            device_version: 0x100,
            class: 0,
            subclass: 0,
            protocol: 0,
            configuration: 1,
            num_configurations: 1,
            interfaces: vec![RemoteInterface {
                class: 3,
                subclass: 1,
                protocol: 1,
            }],
        },
    })
}

fn view() -> CatalogSnapshot {
    CatalogSnapshot {
        instance: "test-observer".into(),
        revision: 0,
        origin: CatalogOrigin::LoadedTlsClient,
        state: CatalogState::Initializing,
        failure: None,
        attempts: 0,
        sampled_ms: 0,
        last_success_ms: None,
        journal_healthy: None,
        devices: None,
        changes: Vec::new(),
        next_refresh_seconds: 0,
        resync_required: true,
    }
}

fn success(devices: Vec<CatalogDevice>) -> Attempt {
    Attempt {
        result: Ok(Some(devices)),
        journal_healthy: Some(true),
    }
}

#[test]
fn stale_retains_success_and_denial_clears_without_fabricated_removals() -> Result<()> {
    let mut current = view();
    let mut failures = 0;
    apply(
        &mut current,
        Attempt::failed(CatalogFailure::Io),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(current.state, CatalogState::Stale);
    assert!(current.devices.is_none());
    apply(
        &mut current,
        success(vec![device(VirtualProfile::Hid, "1-1")?]),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(current.devices.as_deref().map(<[_]>::len), Some(1));
    let successful = current.devices.clone();
    apply(
        &mut current,
        Attempt::failed(CatalogFailure::Timeout),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(current.state, CatalogState::Stale);
    assert_eq!(current.devices, successful);
    assert!(current.changes.is_empty());
    assert!(current.journal_healthy.is_none());
    apply(
        &mut current,
        success(Vec::new()),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(current.state, CatalogState::Current);
    assert_eq!(current.devices.as_deref(), Some([].as_slice()));
    assert!(current.resync_required);
    assert_eq!(current.changes.len(), 1);
    assert_eq!(current.changes[0].kind, CatalogChangeKind::Removed);
    apply(
        &mut current,
        success(vec![device(VirtualProfile::Hid, "1-1")?]),
        RefreshInterval::default(),
        &mut failures,
    )?;
    apply(
        &mut current,
        Attempt::failed(CatalogFailure::Permission),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(current.state, CatalogState::Failed);
    assert!(current.devices.is_none());
    assert!(current.changes.is_empty());
    assert_eq!(current.next_refresh_seconds, 0);
    Ok(())
}

#[test]
fn key_order_is_stable_and_address_is_not_a_physical_generation() -> Result<()> {
    let a = device(VirtualProfile::Hid, "1-1")?;
    let b = device(VirtualProfile::CdcAcm, "1-2")?;
    let mut current = view();
    let mut failures = 0;
    apply(
        &mut current,
        success(vec![a.clone(), b.clone()]),
        RefreshInterval::default(),
        &mut failures,
    )?;
    apply(
        &mut current,
        success(vec![b.clone(), a.clone()]),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert!(current.changes.is_empty());
    assert!(!current.resync_required);
    let mut address = a;
    address.device.bus_id = BusId::new("1-4")?;
    address.device.device_number = 4;
    apply(
        &mut current,
        success(vec![b.clone(), address.clone()]),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(current.changes.len(), 1);
    assert_eq!(current.changes[0].kind, CatalogChangeKind::AddressChanged);
    address.device.vendor_id = 0x4321;
    apply(
        &mut current,
        success(vec![address]),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(current.changes.len(), 2);
    assert!(
        current
            .changes
            .iter()
            .any(|c| c.kind == CatalogChangeKind::MetadataChanged)
    );
    assert!(
        current
            .changes
            .iter()
            .any(|c| c.kind == CatalogChangeKind::Removed)
    );
    Ok(())
}

#[test]
fn malformed_duplicate_and_oversized_catalogs_fail_closed() -> Result<()> {
    let item = device(VirtualProfile::Hid, "1-1")?;
    let mut bad = vec![vec![item.clone(), item.clone()]];
    let mut same_bus = device(VirtualProfile::CdcAcm, "1-1")?;
    bad.push(vec![item.clone(), same_bus.clone()]);
    same_bus.device.path = "bad\npath".into();
    bad.push(vec![same_bus]);
    let mut many_interfaces = item.clone();
    many_interfaces.device.interfaces = vec![
        RemoteInterface {
            class: 3,
            subclass: 0,
            protocol: 0
        };
        33
    ];
    bad.push(vec![many_interfaces]);
    let mut long = item.clone();
    long.device.path = "a".repeat(256);
    bad.push(vec![long]);
    bad.push(vec![item.clone(); 17]);
    for devices in bad {
        let mut current = view();
        let mut failures = 0;
        apply(
            &mut current,
            success(vec![item.clone()]),
            RefreshInterval::default(),
            &mut failures,
        )?;
        apply(
            &mut current,
            success(devices),
            RefreshInterval::default(),
            &mut failures,
        )?;
        assert_eq!(current.state, CatalogState::Failed);
        assert_eq!(current.failure, Some(CatalogFailure::Protocol));
        assert!(current.devices.is_none() && current.changes.is_empty());
    }
    Ok(())
}

#[test]
fn interval_backoff_and_revision_bounds_do_not_wrap_or_burst() -> Result<()> {
    for seconds in [0, 1, 121, u64::MAX] {
        assert!(RefreshInterval::new(seconds).is_err());
    }
    let mut current = view();
    let mut failures = 0;
    for expected in [2, 4, 8, 16, 32, 64, 120, 120] {
        apply(
            &mut current,
            Attempt::failed(CatalogFailure::Io),
            RefreshInterval::default(),
            &mut failures,
        )?;
        assert_eq!(current.next_refresh_seconds, expected);
        assert!(current.devices.is_none());
    }
    apply(
        &mut current,
        success(Vec::new()),
        RefreshInterval::default(),
        &mut failures,
    )?;
    assert_eq!(failures, 0);
    assert_eq!(current.next_refresh_seconds, 2);
    current.revision = u64::MAX;
    assert!(
        apply(
            &mut current,
            success(Vec::new()),
            RefreshInterval::default(),
            &mut failures
        )
        .is_err()
    );
    assert_eq!(current.revision, u64::MAX);
    Ok(())
}

#[tokio::test]
async fn slow_subscriber_gets_full_latest_view_and_terminal_publication_before_close() -> Result<()>
{
    let (sender, receiver) = watch::channel(Arc::new(view()));
    let mut sub = CatalogSubscription {
        receiver,
        revision: None,
    };
    assert!(sub.next().await?.resync_required);
    let mut current = view();
    let mut failures = 0;
    for _ in 0..3 {
        apply(
            &mut current,
            success(vec![device(VirtualProfile::Hid, "1-1")?]),
            RefreshInterval::default(),
            &mut failures,
        )?;
        sender.send_replace(Arc::new(current.clone()));
    }
    let skipped = sub.next().await?;
    assert_eq!(skipped.snapshot.revision, 3);
    assert!(skipped.resync_required);
    assert_eq!(skipped.snapshot.devices.as_deref().map(<[_]>::len), Some(1));
    apply(
        &mut current,
        Attempt::stopped(),
        RefreshInterval::default(),
        &mut failures,
    )?;
    sender.send_replace(Arc::new(current));
    drop(sender);
    assert_eq!(sub.next().await?.snapshot.state, CatalogState::Stopped);
    assert!(sub.next().await.is_err());
    Ok(())
}

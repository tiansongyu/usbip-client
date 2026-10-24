//! Real authenticated IMPORTs exercise application admission and targeted teardown.
use super::*;
use crate::ServerMonitor;
use rustusb_core::server_session::{
    ServerCleanup, ServerSessionError, ServerSessionPhase, ServerSessionSnapshot,
};

async fn managed_server(
    credentials: &Credentials,
) -> TestResult<(VirtualBackend, ServerHandle, ServerMonitor)> {
    let backend = VirtualBackend::new()?;
    let monitor = ServerMonitor::new();
    let handle = ServerHandle::bind_managed_tls(
        SecureServerConfig {
            listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        },
        backend.clone(),
        credentials.server.clone(),
        monitor.clone(),
    )
    .await?;
    Ok((backend, handle, monitor))
}
async fn observed(
    monitor: &ServerMonitor,
    predicate: impl Fn(&ServerSessionSnapshot) -> bool,
) -> TestResult<ServerSessionSnapshot> {
    let mut changes = monitor.changes();
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Some(session) = monitor.snapshot()?.sessions.into_iter().find(&predicate) {
                return Ok::<_, Box<dyn std::error::Error + Send + Sync>>(session);
            }
            changes.changed().await?;
        }
    })
    .await?
}
async fn start_import(
    client: &TlsClient,
    address: SocketAddr,
    key: DeviceKey,
    bus: &'static str,
    monitor: &ServerMonitor,
) -> TestResult<(PeerStream, ServerSessionSnapshot, u32)> {
    let client = client.clone();
    let expected = key.clone();
    let task = tokio::spawn(async move { import(&client, address, key, bus).await });
    let pending = observed(monitor, |s| {
        s.device_key == expected && s.phase == ServerSessionPhase::Preparing
    })
    .await?;
    assert!(monitor.admit(pending.id)?);
    assert!(!monitor.admit(pending.id)?);
    let (stream, status) = task.await??;
    Ok((stream, pending, status))
}

#[tokio::test]
async fn admission_precedes_claim_and_targeted_stop_preserves_other_tls_session() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid(), cdc()]])?;
    let (backend, handle, monitor) = managed_server(&credentials).await?;
    let address = handle.local_addr();
    let (mapping, _) = catalog(&credentials.clients[0], address).await?;
    assert_eq!(mapping.len(), 2);
    assert!(monitor.snapshot()?.sessions.is_empty());
    let client = credentials.clients[0].clone();
    let importing = tokio::spawn(async move { import(&client, address, hid(), "1-1").await });
    let preparing = observed(&monitor, |s| s.phase == ServerSessionPhase::Preparing).await?;
    assert_eq!(preparing.principal.as_deref(), Some("client-0"));
    assert!(backend.snapshot()?.devices.iter().all(|d| !d.leased));
    assert!(!importing.is_finished());
    assert!(monitor.admit(preparing.id)?);
    let (mut keyboard, status) = importing.await??;
    assert_eq!(status, 0);
    configure(&mut keyboard, 0x10001).await?;
    let (mut serial, serial_session, status) =
        start_import(&credentials.clients[0], address, cdc(), "1-2", &monitor).await?;
    assert_eq!(status, 0);
    configure(&mut serial, 0x10002).await?;
    let snapshot = monitor.snapshot()?;
    let keyboard_session = snapshot
        .sessions
        .iter()
        .find(|s| s.id == preparing.id)
        .ok_or("missing HID session")?;
    assert_eq!(keyboard_session.phase, ServerSessionPhase::Connected);
    assert_eq!(
        (
            keyboard_session.metrics.app_rx_bytes,
            keyboard_session.metrics.app_tx_bytes
        ),
        (48, 48)
    );
    // A pending IN belongs to this one actor and must be cancelled by targeted stop.
    send(&mut keyboard, &submit(2, 0x10001, 1, 1, 8, [0; 8])).await?;
    assert!(monitor.control().request_disconnect(preparing.id)?);
    assert!(!monitor.request_disconnect(preparing.id)?);
    closed(&mut keyboard).await?;
    let terminal = observed(&monitor, |s| s.id == preparing.id && s.cleanup.is_some()).await?;
    assert_eq!(terminal.cleanup, Some(ServerCleanup::Verified));
    assert_eq!(
        terminal.operation_error,
        Some(ServerSessionError::Cancelled)
    );
    assert_eq!(terminal.cleanup_error, None);
    // The CDC session remains live and transfers actual bytes after HID closes.
    send(&mut serial, &submit(3, 0x10002, 1, 2, 4, [0; 8])).await?;
    let mut output = submit(4, 0x10002, 0, 2, 4, [0; 8]);
    output.extend_from_slice(b"stay");
    send(&mut serial, &output).await?;
    reply(&mut serial, false).await?;
    assert_eq!(reply(&mut serial, true).await?.1, b"stay");
    assert!(
        monitor
            .snapshot()?
            .sessions
            .iter()
            .any(|s| s.id == serial_session.id && s.phase == ServerSessionPhase::Connected)
    );
    let phases: Vec<_> = monitor
        .events_after(0, 64)?
        .events
        .into_iter()
        .filter(|e| e.session.id == preparing.id)
        .map(|e| e.session.phase)
        .collect();
    assert_eq!(
        phases,
        vec![
            ServerSessionPhase::Preparing,
            ServerSessionPhase::Claiming,
            ServerSessionPhase::Started,
            ServerSessionPhase::Connected,
            ServerSessionPhase::Closing,
            ServerSessionPhase::Closed
        ]
    );
    assert!(monitor.acknowledge(preparing.id, terminal.revision)?);
    assert!(monitor.events_after(0, 64)?.gap);
    let (mut replacement, replacement_session, status) =
        start_import(&credentials.clients[0], address, hid(), "1-1", &monitor).await?;
    assert_eq!(status, 0);
    assert_ne!(replacement_session.id, preparing.id);
    configure(&mut replacement, 0x10001).await?;
    handle.shutdown().await?;
    closed(&mut replacement).await?;
    closed(&mut serial).await?;
    assert!(
        monitor
            .snapshot()?
            .sessions
            .iter()
            .all(|s| s.cleanup == Some(ServerCleanup::Verified))
    );
    released(&backend).await?;
    Ok(())
}

#[tokio::test]
async fn rejected_admission_and_busy_claim_keep_distinct_cleanup_facts() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid()]])?;
    let (backend, handle, monitor) = managed_server(&credentials).await?;
    let address = handle.local_addr();
    let client = credentials.clients[0].clone();
    let importing = tokio::spawn(async move { import(&client, address, hid(), "1-1").await });
    let preparing = observed(&monitor, |s| s.phase == ServerSessionPhase::Preparing).await?;
    assert!(monitor.reject(preparing.id)?);
    assert_eq!(importing.await??.1, 4);
    let rejected = observed(&monitor, |s| s.id == preparing.id && s.cleanup.is_some()).await?;
    assert_eq!(rejected.cleanup, Some(ServerCleanup::NotStarted));
    assert_eq!(
        rejected.operation_error,
        Some(ServerSessionError::AdmissionRejected)
    );
    assert!(
        backend
            .snapshot()?
            .devices
            .iter()
            .all(|d| d.counters.sessions_started == 0)
    );
    let (mut owner, _, status) =
        start_import(&credentials.clients[0], address, hid(), "1-1", &monitor).await?;
    assert_eq!(status, 0);
    let (_, busy, status) =
        start_import(&credentials.clients[0], address, hid(), "1-1", &monitor).await?;
    assert_eq!(status, 2);
    let busy = observed(&monitor, |s| s.id == busy.id && s.cleanup.is_some()).await?;
    assert_eq!(busy.cleanup, Some(ServerCleanup::NotStarted));
    configure(&mut owner, 0x10001).await?;
    handle.shutdown().await?;
    released(&backend).await?;
    Ok(())
}

#[tokio::test]
async fn last_observer_drop_stops_live_lease_without_handle_drop() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid()]])?;
    let (backend, handle, monitor) = managed_server(&credentials).await?;
    let (mut peer, _, status) = start_import(
        &credentials.clients[0],
        handle.local_addr(),
        hid(),
        "1-1",
        &monitor,
    )
    .await?;
    assert_eq!(status, 0);
    let control = monitor.control();
    assert!(handle.monitor().is_some());
    drop(monitor);
    closed(&mut peer).await?;
    released(&backend).await?;
    assert!(handle.monitor().is_none());
    handle.shutdown().await?;
    // A non-owning control cannot keep the observer or listener alive.
    assert!(
        control
            .request_disconnect(rustusb_core::server_session::ServerSessionId::new(1)?)
            .is_err()
    );
    Ok(())
}

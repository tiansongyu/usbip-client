//! Shared Rust engine owner, with explicit foreground and durable managed modes.

use super::recording;
use crate::{
    context::stopped,
    foreground::{Control, ControlError, Event, JobOutcome, Snapshot},
    model::{Cleanup, Evidence, Failure, Metrics, SessionRole},
    run_context::{RunContext, RunSession},
};
use rustusb_core::{DeviceKey, Error, Result, SessionState, VirtualProfile, server_session::*};
use rustusb_rust_backend::{
    SecureServerConfig, ServerConfig, ServerHandle, ServerMonitor, VirtualBackend,
};
use rustusb_transport::config::LoadedServerConfig;
use std::{collections::BTreeSet, sync::Arc, time::Duration};
use tokio::{
    sync::watch,
    task::{JoinHandle, JoinSet},
};

enum StageError {
    Operation(Error),
    Recording(Error),
}

impl From<Error> for StageError {
    fn from(error: Error) -> Self {
        Self::Operation(error)
    }
}

fn stage_results(result: std::result::Result<(), StageError>) -> (Result<()>, Result<()>) {
    match result {
        Ok(()) => (Ok(()), Ok(())),
        Err(StageError::Operation(error)) => (Err(error), Ok(())),
        Err(StageError::Recording(error)) => (Ok(()), Err(error)),
    }
}

fn metrics(value: ServerSessionMetrics) -> Metrics {
    Metrics {
        app_rx_bytes: value.app_rx_bytes,
        app_tx_bytes: value.app_tx_bytes,
        saturated: value.saturated,
        elapsed_millis: value.elapsed_millis,
    }
}
fn failure(value: ServerSessionError) -> Failure {
    match value {
        ServerSessionError::Permission => Failure::Permission,
        ServerSessionError::Protocol => Failure::InvalidInput,
        ServerSessionError::Io => Failure::Io,
        ServerSessionError::Timeout => Failure::Timeout,
        ServerSessionError::Unsupported => Failure::Unsupported,
        ServerSessionError::Cancelled => Failure::Cancelled,
        _ => Failure::State,
    }
}

async fn record(session: &RunSession, value: &ServerSessionSnapshot) -> Result<bool> {
    let sampled = Some(metrics(value.metrics));
    match value.phase {
        ServerSessionPhase::Preparing | ServerSessionPhase::Claiming => {
            session
                .observe(SessionState::Connecting, Evidence::Intent, sampled)
                .await?
        }
        ServerSessionPhase::Started => {
            session
                .observe(SessionState::Connecting, Evidence::Prepared, sampled)
                .await?
        }
        ServerSessionPhase::Connected => {
            session
                .observe(
                    SessionState::Connected,
                    Evidence::ImportReplyFlushed,
                    sampled,
                )
                .await?
        }
        ServerSessionPhase::Closing => {
            session
                .observe(SessionState::Disconnecting, Evidence::Closing, sampled)
                .await?
        }
        ServerSessionPhase::Closed | ServerSessionPhase::CleanupUnconfirmed => {
            if value.cleanup.is_none() {
                // A soft cleanup deadline is an observation while the real
                // blocking owner remains alive. It is not an acknowledgeable
                // terminal and cannot retire this application session.
                session
                    .observe(SessionState::Error, Evidence::Unconfirmed, sampled)
                    .await?;
                return Ok(false);
            }
            let cleanup = match value.cleanup {
                Some(ServerCleanup::NotStarted) => Cleanup::NotStarted,
                Some(ServerCleanup::Verified) => Cleanup::Verified,
                Some(ServerCleanup::Unconfirmed) | None => Cleanup::Unconfirmed,
            };
            session
                .finish(
                    value.operation_error.map(failure),
                    cleanup,
                    metrics(value.metrics),
                )
                .await?;
            return Ok(true);
        }
    }
    Ok(false)
}

async fn incoming(monitor: ServerMonitor, id: ServerSessionId, context: RunContext) -> Result<()> {
    let initial = monitor
        .snapshot()?
        .sessions
        .into_iter()
        .find(|entry| entry.id == id)
        .ok_or_else(|| Error::State("managed attempt disappeared before admission".into()))?;
    let session = match context
        .begin_session(SessionRole::Incoming, initial.device_key, initial.principal)
        .await
    {
        Ok(session) => session,
        Err(error) => {
            let _ = monitor.reject(id);
            let _ = monitor.request_disconnect(id);
            return Err(error);
        }
    };
    session.server_link(monitor.control(), id)?;
    let mut job_stop = context.stop.clone();
    let mut session_stop = session.stop.clone();
    let mut first_error = None;
    let mut changed = monitor.changes();
    let mut after = initial.revision.saturating_sub(1);
    let result = {
        let observing = async {
            if *context.stop.borrow() || *session.stop.borrow() {
                monitor.reject(id)?;
                monitor.request_disconnect(id)?;
            } else if let Err(error) = monitor.admit(id) {
                first_error = Some(error);
                let _ = monitor.request_disconnect(id);
            }
            loop {
                let page = monitor.events_after(after, 64)?;
                let full_page = page.events.len() == 64;
                // Other sessions acknowledge their own events concurrently. A
                // global gap does not discard this unacknowledged session's events.
                for event in page.events {
                    after = event.sequence;
                    if event.session.id != id {
                        continue;
                    }
                    if first_error.is_none() {
                        match record(&session, &event.session).await {
                            Ok(true) => {
                                monitor.acknowledge(id, event.session.revision)?;
                                return Ok(());
                            }
                            Ok(false) => {}
                            Err(error) => {
                                first_error = Some(error);
                                let _ = monitor.request_disconnect(id);
                            }
                        }
                    }
                    // A recording failure still waits for this attempt's real
                    // terminal owner. Never acknowledge a terminal not persisted.
                    if event.session.cleanup.is_some() {
                        return Ok(());
                    }
                }
                if full_page {
                    continue;
                }
                let current = monitor
                    .snapshot()?
                    .sessions
                    .into_iter()
                    .find(|entry| entry.id == id);
                if let Some(current) = current {
                    // Never jump to a newer phase with a metric sample and then
                    // replay an older lifecycle event on the next page.
                    if current.revision > after {
                        continue;
                    }
                    if current.cleanup.is_none() && first_error.is_none() {
                        // Periodic byte samples preserve the actual phase/evidence.
                        if let Err(error) = record(&session, &current).await {
                            first_error = Some(error);
                            let _ = monitor.request_disconnect(id);
                        }
                    }
                } else {
                    return Err(Error::State(
                        "managed owner vanished without a terminal observation".into(),
                    ));
                }
                tokio::select! {
                    result = changed.changed() => if result.is_err() { return Err(Error::State("managed monitor ended before cleanup".into())); },
                    _ = tokio::time::sleep(Duration::from_secs(1)) => {},
                }
            }
        };
        tokio::pin!(observing);
        // This select covers even an fsync acknowledgement. After a stop, retain
        // and join the same observer until this attempt's real terminal arrives.
        tokio::select! {
            biased;
            _ = stopped(&mut job_stop) => { let _ = monitor.request_disconnect(id); (&mut observing).await },
            _ = stopped(&mut session_stop) => { let _ = monitor.request_disconnect(id); (&mut observing).await },
            result = &mut observing => result,
        }
    };
    result?;
    first_error.map_or(Ok(()), Err)
}

async fn supervise(
    monitor: ServerMonitor,
    context: RunContext,
    mut finished: watch::Receiver<bool>,
    failed: watch::Sender<bool>,
) -> Result<()> {
    let mut changed = monitor.changes();
    let mut seen = BTreeSet::new();
    let mut tasks = JoinSet::new();
    let mut first = None;
    loop {
        let snapshot = monitor.snapshot()?;
        seen.retain(|id| snapshot.sessions.iter().any(|entry| entry.id == *id));
        for entry in &snapshot.sessions {
            if seen.insert(entry.id) {
                let monitor = monitor.clone();
                let context = context.clone();
                let id = entry.id;
                tasks.spawn(incoming(monitor, id, context));
            }
        }
        if *finished.borrow() && tasks.is_empty() {
            break;
        }
        tokio::select! {
            result = tasks.join_next(), if !tasks.is_empty() => {
                let result = match result { Some(Ok(result)) => result, Some(Err(_)) => Err(Error::State("managed session reporter failed".into())), None => Ok(()) };
                if let Err(error) = result { failed.send_replace(true); if first.is_none() { first = Some(error); } }
            },
            result = changed.changed() => if result.is_err() { failed.send_replace(true); if first.is_none() { first = Some(Error::State("managed monitor ended".into())); } },
            _ = finished.changed(), if !*finished.borrow() => {},
        }
    }
    first.map_or(Ok(()), Err)
}

struct Served {
    operation: Result<()>,
    cleanup: Result<()>,
    recording: Result<()>,
    observing: Option<JoinHandle<Result<()>>>,
}

/// Only the admission/helper selection varies; every mode uses the same owner tree.
enum Binding {
    Legacy(ServerConfig),
    Secure(Arc<LoadedServerConfig>),
}

impl Binding {
    fn validate(&self) -> Result<()> {
        match self {
            Self::Legacy(config) => config.validate(),
            Self::Secure(_) => Ok(()),
        }
    }

    fn is_secure(&self) -> bool {
        matches!(self, Self::Secure(_))
    }

    async fn bind(&self, owner: &Owner, monitor: Option<&ServerMonitor>) -> Result<ServerHandle> {
        match (self, owner, monitor) {
            (Self::Legacy(config), Owner::Virtual(backend), None) => {
                ServerHandle::bind(config.clone(), backend.clone()).await
            }
            (Self::Secure(config), Owner::Virtual(backend), None) => {
                ServerHandle::bind_tls(
                    SecureServerConfig {
                        listen: config.listen,
                    },
                    backend.clone(),
                    config.tls.clone(),
                )
                .await
            }
            (Self::Secure(config), Owner::Virtual(backend), Some(monitor)) => {
                ServerHandle::bind_managed_tls(
                    SecureServerConfig {
                        listen: config.listen,
                    },
                    backend.clone(),
                    config.tls.clone(),
                    monitor.clone(),
                )
                .await
            }
            #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
            (Self::Legacy(config), Owner::Physical(backend), None) => {
                ServerHandle::bind_physical(config.clone(), backend.clone()).await
            }
            #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
            (Self::Secure(config), Owner::Physical(backend), None) => {
                ServerHandle::bind_physical_tls(
                    SecureServerConfig {
                        listen: config.listen,
                    },
                    backend.clone(),
                    config.tls.clone(),
                )
                .await
            }
            #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
            (Self::Secure(config), Owner::Physical(backend), Some(monitor)) => {
                ServerHandle::bind_physical_managed_tls(
                    SecureServerConfig {
                        listen: config.listen,
                    },
                    backend.clone(),
                    config.tls.clone(),
                    monitor.clone(),
                )
                .await
            }
            (Self::Legacy(_), _, Some(_)) => Err(Error::Permission(
                "managed jobs require an authenticated fixed profile".into(),
            )),
        }
    }
}

enum Selection {
    Virtual(Vec<DeviceKey>),
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    Physical(Vec<DeviceKey>),
}

impl Selection {
    fn keys(&self) -> &[DeviceKey] {
        match self {
            Self::Virtual(keys) => keys,
            #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
            Self::Physical(keys) => keys,
        }
    }
}

enum Owner {
    Virtual(VirtualBackend),
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    Physical(rustusb_rust_backend::PhysicalBackend),
}

impl Owner {
    fn snapshot(&self) -> Result<Snapshot> {
        match self {
            Self::Virtual(backend) => backend.snapshot().map(Snapshot::Virtual),
            #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
            Self::Physical(backend) => backend.snapshot().map(Snapshot::Physical),
        }
    }

    async fn close(self) -> Result<Snapshot> {
        match self {
            Self::Virtual(backend) => backend.snapshot().map(Snapshot::Virtual),
            #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
            Self::Physical(backend) => backend.close().await.map(Snapshot::Physical),
        }
    }

    fn input(&self, control: Control) -> std::result::Result<Event, Error> {
        match control {
            Control::Snapshot { id } => Ok(Event::Snapshot {
                id,
                snapshot: self.snapshot()?,
            }),
            Control::HidKey { id, key } => Ok(match self {
                Self::Virtual(backend) => match backend.hid_key(key) {
                    Ok(()) => Event::Ack { id },
                    Err(_) => Event::Error {
                        id: Some(id),
                        code: ControlError::Rejected,
                    },
                },
                #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
                Self::Physical(_) => Event::Error {
                    id: Some(id),
                    code: ControlError::UnsupportedPhysical,
                },
            }),
            Control::CdcEmit { id, hex } => Ok(match self {
                Self::Virtual(backend) => {
                    match hex_bytes(&hex).and_then(|bytes| backend.cdc_emit(&bytes)) {
                        Ok(()) => Event::Ack { id },
                        Err(_) => Event::Error {
                            id: Some(id),
                            code: ControlError::Rejected,
                        },
                    }
                }
                #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
                Self::Physical(_) => Event::Error {
                    id: Some(id),
                    code: ControlError::UnsupportedPhysical,
                },
            }),
            Control::Invalid => Ok(Event::Error {
                id: None,
                code: ControlError::InvalidControl,
            }),
            Control::Shutdown { .. } | Control::Eof => Err(Error::State(
                "stop control was not dispatched by owner".into(),
            )),
        }
    }
}

fn hex_bytes(hex: &str) -> Result<Vec<u8>> {
    if hex.is_empty() || hex.len() > 2048 || !hex.len().is_multiple_of(2) {
        return Err(Error::InvalidInput("hex must encode 1..1024 bytes".into()));
    }
    fn nibble(value: u8) -> Result<u8> {
        match value {
            b'0'..=b'9' => Ok(value - b'0'),
            b'a'..=b'f' => Ok(value - b'a' + 10),
            b'A'..=b'F' => Ok(value - b'A' + 10),
            _ => Err(Error::InvalidInput("invalid hex digit".into())),
        }
    }
    hex.as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok(nibble(pair[0])? * 16 + nibble(pair[1])?))
        .collect()
}

fn stopping(stop: &watch::Receiver<bool>) -> bool {
    *stop.borrow() || stop.has_changed().is_err()
}

async fn serving(
    server: ServerHandle,
    owner: &Owner,
    secure: bool,
    monitor: Option<ServerMonitor>,
    context: RunContext,
    exports: &[Export],
    pending: &mut Option<recording::Pending>,
) -> Served {
    let (done, finished) = watch::channel(false);
    let (failed, mut failure) = watch::channel(false);
    let observing = monitor.map(|monitor| {
        tokio::spawn(supervise(
            monitor,
            context.clone(),
            finished,
            failed.clone(),
        ))
    });
    let result: std::result::Result<(), StageError> = async {
        for export in exports {
            let reporter = export.session.clone();
            let mut stop = context.stop.clone();
            let mut local = export.session.stop.clone();
            match recording::wait(
                async move {
                    reporter
                        .observe(SessionState::Available, Evidence::ExportReady, None)
                        .await
                },
                async {
                    tokio::select! {
                        biased;
                        _=stopped(&mut stop)=>Ok(()),
                        _=stopped(&mut local)=>Ok(()),
                        _=stopped(&mut failure)=>Ok(()),
                    }
                },
            )
            .await
            {
                recording::Wait::Ready(result) => result.map_err(StageError::Recording)?,
                recording::Wait::Interrupted {
                    pending: original,
                    reason,
                } => {
                    *pending = Some(original);
                    return reason.map_err(StageError::Operation);
                }
            }
        }
        context
            .emit(Event::Ready {
                address: server.local_addr(),
                secure,
                snapshot: owner.snapshot()?,
            })
            .await?;
        let mut stop = context.stop.clone();
        loop {
            tokio::select! {
                biased;
                _ = stopped(&mut stop) => return Ok(()),
                // The observer join supplies the original recording error.
                _ = stopped(&mut failure) => return Ok(()),
                control = context.next_control() => match control {
                    Control::Shutdown { id } => {
                        context.emit(Event::Ack { id }).await?;
                        return Ok(());
                    }
                    Control::Eof => return Ok(()),
                    control => context.emit(owner.input(control)?).await?,
                },
            }
        }
    }
    .await;
    let (operation, recording) = stage_results(result);
    // Output and stop waiters never own this handle. Keep the managed consumer
    // alive through shutdown so actual terminal events still receive durable ACKs.
    let cleanup = server.shutdown().await;
    done.send_replace(true);
    // The consumer keeps running, but its durable receipts must not delay the
    // physical registry close or the remaining helper Finish requests.
    Served {
        operation,
        cleanup,
        recording,
        observing,
    }
}

struct Export {
    session: RunSession,
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    grant: Option<Grant>,
}

#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
#[derive(Default)]
struct Grant {
    attempted: bool,
    lease_id: Option<String>,
}

async fn exports(
    keys: &[DeviceKey],
    context: &RunContext,
) -> std::result::Result<Vec<Export>, StageError> {
    let mut exports: Vec<Export> = Vec::new();
    for key in keys {
        match context
            .begin_session(SessionRole::Export, key.clone(), None)
            .await
        {
            Ok(session) => exports.push(Export {
                session,
                #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
                grant: None,
            }),
            Err(mut error) => {
                for export in exports {
                    if let Err(next) = export
                        .session
                        .finish(
                            Some(Failure::Persistence),
                            Cleanup::NotStarted,
                            Metrics::default(),
                        )
                        .await
                    {
                        error = Error::State(format!("{error}; {next}"));
                    }
                }
                return Err(StageError::Recording(error));
            }
        }
    }
    Ok(exports)
}

async fn prepare(
    selection: Selection,
    binding: &Binding,
    context: &RunContext,
    exports: &mut [Export],
    pending: &mut Option<recording::Pending>,
) -> std::result::Result<Option<Owner>, StageError> {
    #[cfg(not(all(feature = "physical-nusb", target_os = "linux")))]
    let _ = (binding, &exports, &pending);
    if stopping(&context.stop) {
        return Ok(None);
    }
    match selection {
        Selection::Virtual(_) => VirtualBackend::new()
            .map(Owner::Virtual)
            .map(Some)
            .map_err(StageError::Operation),
        #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
        Selection::Physical(keys) => {
            use rustusb_core::application::BrokerResourceKind;
            use rustusb_daemon::{
                Request, open_physical, open_secure_physical, open_secure_physical_tracked, request,
            };
            use rustusb_rust_backend::{PhysicalBackend, PhysicalSource};
            if !binding.is_secure() {
                // Status is read-only and has not delegated a capability. Keep
                // the old startup cancellation boundary; actual grant requests
                // and initialization below must always be retained and awaited.
                let mut stop = context.stop.clone();
                let status = tokio::select! {
                    biased;
                    _ = stopped(&mut stop) => return Ok(None),
                    result = request(&context.helper_socket, Request::Status) => result?,
                };
                if !status.ok {
                    return Err(Error::State(
                        status
                            .error
                            .unwrap_or_else(|| "helper rejected physical service caller".into()),
                    )
                    .into());
                }
                if status
                    .data
                    .get("physical_usb_capability_enabled")
                    .and_then(|value| value.as_bool())
                    != Some(true)
                {
                    return Err(Error::Unsupported("install a matching helper built with physical-usb and configure its physical_device_ids allowlist".into()).into());
                }
                if status
                    .data
                    .get("physical_journal_schema_version")
                    .and_then(|value| value.as_u64())
                    != Some(1)
                {
                    return Err(Error::Unsupported(
                        "install a matching helper with physical delegation journal schema 1"
                            .into(),
                    )
                    .into());
                }
            }
            let mut sources = Vec::with_capacity(keys.len());
            for (key, export) in keys.iter().zip(exports) {
                if stopping(&context.stop) || stopping(&export.session.stop) {
                    return Ok(None);
                }
                let reporter = export.session.clone();
                let mut stop = context.stop.clone();
                let mut local = export.session.stop.clone();
                let correlation = match recording::wait(
                    async move { reporter.attempt(BrokerResourceKind::UsbfsGrant).await },
                    async {
                        tokio::select! {
                            biased;
                            _=stopped(&mut stop)=>Ok(()),
                            _=stopped(&mut local)=>Ok(()),
                        }
                    },
                )
                .await
                {
                    recording::Wait::Ready(result) => result.map_err(StageError::Recording)?,
                    recording::Wait::Interrupted {
                        pending: original,
                        reason,
                    } => {
                        export.grant = Some(Grant {
                            attempted: context.is_managed(),
                            lease_id: None,
                        });
                        *pending = Some(original);
                        reason.map_err(StageError::Operation)?;
                        return Ok(None);
                    }
                };
                if stopping(&context.stop) || stopping(&export.session.stop) {
                    export.grant = Some(Grant {
                        attempted: correlation.is_some(),
                        lease_id: None,
                    });
                    return Ok(None);
                }
                export.grant = Some(Grant {
                    attempted: true,
                    lease_id: None,
                });
                let id = key
                    .fingerprint()
                    .ok_or_else(|| Error::InvalidInput("physical fingerprint required".into()))?
                    .to_owned();
                // Accepted helper I/O is retained even when stop arrives. Only
                // the mode selects tracked/untracked, never a failed WAL fallback.
                let grant = match correlation.clone() {
                    Some(correlation) => {
                        open_secure_physical_tracked(&context.helper_socket, id, correlation)
                            .await?
                    }
                    None if binding.is_secure() => {
                        open_secure_physical(&context.helper_socket, id).await?
                    }
                    None => open_physical(&context.helper_socket, id).await?,
                };
                export.grant = Some(Grant {
                    attempted: true,
                    lease_id: Some(grant.lease_id.clone()),
                });
                sources.push(PhysicalSource {
                    fd: grant.fd,
                    device: grant.device,
                    receipt: grant.receipt,
                });
                let reporter = export.session.clone();
                let lease_id = grant.lease_id;
                match recording::wait(
                    async move { reporter.resource(correlation, &lease_id).await },
                    async {
                        tokio::select! {
                            biased;
                            _=stopped(&mut stop)=>Ok(()),
                            _=stopped(&mut local)=>Ok(()),
                        }
                    },
                )
                .await
                {
                    recording::Wait::Ready(result) => result.map_err(StageError::Recording)?,
                    recording::Wait::Interrupted {
                        pending: original,
                        reason,
                    } => {
                        *pending = Some(original);
                        // Returning drops the entire source vector. The outer
                        // owner issues every Finish before draining this ACK.
                        reason.map_err(StageError::Operation)?;
                        return Ok(None);
                    }
                }
            }
            if stopping(&context.stop) {
                return Ok(None);
            }
            // Never select/drop this initializer. Once it really returns the
            // shared pipeline checks stop before bind and closes all owners.
            PhysicalBackend::from_sources(sources)
                .await
                .map(Owner::Physical)
                .map(Some)
                .map_err(StageError::Operation)
        }
    }
}

fn retain_error(target: &mut Result<()>, next: Result<()>) {
    if let Err(next) = next {
        let previous = std::mem::replace(target, Ok(()));
        *target = match previous {
            Ok(()) => Err(next),
            Err(previous) => Err(Error::State(format!("{previous}; {next}"))),
        };
    }
}

async fn finish_exports(
    exports: &[Export],
    context: &RunContext,
    operation: &Result<()>,
    owners_closed: bool,
    started: bool,
    pending: Option<recording::Pending>,
) -> (Result<()>, Result<()>) {
    let base = if owners_closed {
        if started {
            Cleanup::Verified
        } else {
            Cleanup::NotStarted
        }
    } else {
        Cleanup::Unconfirmed
    };
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    let (cleanup, evidence) = finish_grants(exports, owners_closed, base, |lease_id| {
        finish_lease(&context.helper_socket, lease_id)
    })
    .await;
    #[cfg(not(all(feature = "physical-nusb", target_os = "linux")))]
    let (cleanup, evidence) = (Ok(()), vec![base; exports.len()]);
    #[cfg(not(all(feature = "physical-nusb", target_os = "linux")))]
    let _ = context;
    let mut recording = Ok(());
    // The original stage precedes this session's Closing/Finish, but not actual
    // FD/backend cleanup or any received delegation's root Finish above.
    if let Some(original) = pending {
        retain_error(&mut recording, original.await);
    }
    // All received capabilities have been attempted above, before any
    // application persistence acknowledgement can wait or fail.
    for (export, evidence) in exports.iter().zip(evidence) {
        let observed = export
            .session
            .observe(SessionState::Disconnecting, Evidence::Closing, None)
            .await;
        let recorded = export
            .session
            .finish(
                operation
                    .as_ref()
                    .err()
                    .or(recording.as_ref().err())
                    .or(observed.as_ref().err())
                    .map(Failure::from_error),
                evidence,
                Metrics::default(),
            )
            .await;
        retain_error(&mut recording, observed);
        retain_error(&mut recording, recorded);
    }
    (cleanup, recording)
}

#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
async fn finish_grants<F, T>(
    exports: &[Export],
    owners_closed: bool,
    base: Cleanup,
    mut finish: F,
) -> (Result<()>, Vec<Cleanup>)
where
    F: FnMut(String) -> T,
    T: std::future::Future<Output = Result<()>>,
{
    let mut cleanup = Ok(());
    let mut evidence = Vec::with_capacity(exports.len());
    for export in exports {
        evidence.push(match &export.grant {
            Some(grant) => {
                let finished = match &grant.lease_id {
                    Some(lease_id) if owners_closed => finish(lease_id.clone()).await,
                    Some(_) => Err(Error::State("physical worker close unconfirmed".into())),
                    None if grant.attempted => {
                        Err(Error::State("physical grant outcome unconfirmed".into()))
                    }
                    None => Ok(()),
                };
                let evidence = if finished.is_err() {
                    Cleanup::Unconfirmed
                } else if grant.attempted {
                    Cleanup::Verified
                } else {
                    Cleanup::NotStarted
                };
                retain_error(&mut cleanup, finished);
                evidence
            }
            None => base,
        });
    }
    (cleanup, evidence)
}

#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
async fn finish_lease(socket: &std::path::Path, lease_id: String) -> Result<()> {
    use rustusb_daemon::{Request, request};
    let response = request(socket, Request::FinishPhysical { lease_id }).await?;
    if response.ok {
        Ok(())
    } else {
        Err(Error::State(response.error.unwrap_or_else(|| {
            "helper could not verify physical delegation completion".into()
        })))
    }
}

async fn run(binding: Binding, selection: Selection, context: RunContext) -> JobOutcome {
    if let Err(error) = binding.validate() {
        return JobOutcome::before_start(error);
    }
    if context.is_managed() && !binding.is_secure() {
        return JobOutcome::before_start(Error::Permission(
            "managed jobs require an authenticated fixed profile".into(),
        ));
    }
    let mut exports = match exports(selection.keys(), &context).await {
        Ok(exports) => exports,
        Err(error) => {
            let (operation, recording) = stage_results(Err(error));
            return JobOutcome {
                operation,
                cleanup: Ok(()),
                recording,
                final_event: None,
            };
        }
    };
    let mut pending = None;
    let prepared = prepare(selection, &binding, &context, &mut exports, &mut pending).await;
    let mut owner = None;
    let mut cleanup = Ok(());
    let mut recording = Ok(());
    let mut observing = None;
    let operation = match prepared {
        Err(error) => {
            let (operation, recorded) = stage_results(Err(error));
            recording = recorded;
            operation
        }
        Ok(None) => Ok(()),
        Ok(Some(prepared)) => {
            owner = Some(prepared);
            if stopping(&context.stop)
                || exports.iter().any(|export| stopping(&export.session.stop))
            {
                Ok(())
            } else {
                let monitor = context.is_managed().then(ServerMonitor::new);
                // No cancellation branch can discard a newly bound ServerHandle.
                match owner.as_ref() {
                    Some(owner) => match binding.bind(owner, monitor.as_ref()).await {
                        Ok(server) => {
                            let served = serving(
                                server,
                                owner,
                                binding.is_secure(),
                                monitor,
                                context.clone(),
                                &exports,
                                &mut pending,
                            )
                            .await;
                            cleanup = served.cleanup;
                            recording = served.recording;
                            observing = served.observing;
                            served.operation
                        }
                        Err(error) => Err(error),
                    },
                    None => Err(Error::State("prepared Rust owner disappeared".into())),
                }
            }
        }
    };
    let started = owner.is_some();
    let (closed, snapshot) = match owner {
        Some(owner) => match owner.close().await {
            Ok(snapshot) => (Ok(()), Some(snapshot)),
            Err(error) => (Err(error), None),
        },
        None => (Ok(()), None),
    };
    let owners_closed = closed.is_ok() && cleanup.is_ok();
    retain_error(&mut cleanup, closed);
    // Finish every received delegation after all actual owner FDs have ended.
    // An error for one export never skips the remaining helper Finish requests.
    let (finished, recorded) = finish_exports(
        &exports,
        &context,
        &operation,
        owners_closed,
        started,
        pending,
    )
    .await;
    retain_error(&mut cleanup, finished);
    retain_error(&mut recording, recorded);
    if let Some(task) = observing {
        let observed = match task.await {
            Ok(result) => result,
            Err(_) => Err(Error::State("managed monitor supervisor failed".into())),
        };
        retain_error(&mut recording, observed);
    }
    let final_event =
        if context.is_foreground() && operation.is_ok() && cleanup.is_ok() && recording.is_ok() {
            snapshot.map(|snapshot| Event::Stopped { snapshot })
        } else {
            None
        };
    JobOutcome {
        operation,
        cleanup,
        recording,
        final_event,
    }
}

pub(super) async fn virtual_server(
    config: Arc<LoadedServerConfig>,
    context: RunContext,
) -> JobOutcome {
    let selection = Selection::Virtual(config.devices.clone());
    run(Binding::Secure(config), selection, context).await
}

pub(super) async fn virtual_legacy(config: ServerConfig, context: RunContext) -> JobOutcome {
    run(
        Binding::Legacy(config),
        Selection::Virtual(vec![
            DeviceKey::virtual_profile(VirtualProfile::Hid),
            DeviceKey::virtual_profile(VirtualProfile::CdcAcm),
        ]),
        context,
    )
    .await
}

#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
pub(super) async fn physical_server(
    config: Arc<LoadedServerConfig>,
    context: RunContext,
) -> JobOutcome {
    let selection = Selection::Physical(config.devices.clone());
    run(Binding::Secure(config), selection, context).await
}

#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
pub(super) async fn physical_legacy(
    config: ServerConfig,
    ids: Vec<String>,
    context: RunContext,
) -> JobOutcome {
    if ids.is_empty() || ids.len() > 16 || ids.iter().collect::<BTreeSet<_>>().len() != ids.len() {
        return JobOutcome::before_start(Error::InvalidInput(
            "select 1..16 distinct approved physical device fingerprints".into(),
        ));
    }
    let keys = match ids
        .iter()
        .map(|id| DeviceKey::physical(id))
        .collect::<Result<Vec<_>>>()
    {
        Ok(keys) => keys,
        Err(error) => return JobOutcome::before_start(error),
    };
    run(Binding::Legacy(config), Selection::Physical(keys), context).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context::{JobContext, JobMessage, SessionContext};
    use crate::foreground::{self, ControlId, Delivery, ForegroundOptions, Frontend};
    use rustusb_core::{
        DeviceKey, VirtualProfile,
        application::{InstanceId, JobId, SessionId},
    };
    use tokio::sync::mpsc;
    use tokio::{
        io::{AsyncReadExt, AsyncWriteExt},
        net::TcpStream,
    };

    fn foreground_fixture() -> (RunContext, Frontend, watch::Sender<bool>) {
        let (frontend, connection) = foreground::channel();
        let (stop, stopping) = watch::channel(false);
        let context = RunContext::foreground(ForegroundOptions {
            helper_socket: "/this-helper-must-never-be-contacted/rustusb.sock".into(),
            stop: stopping,
            connection,
        });
        (context, frontend, stop)
    }

    #[tokio::test]
    async fn held_available_ack_releases_actual_listener_and_lease_before_original_record()
    -> Result<()> {
        tokio::time::timeout(Duration::from_secs(5), async {
            let backend = VirtualBackend::new()?;
            let server = ServerHandle::bind(
                ServerConfig {
                    listen: ([127, 0, 0, 1], 0).into(),
                    ..ServerConfig::default()
                },
                backend.clone(),
            )
            .await?;
            let address = server.local_addr();
            let mut network = import(address).await?;
            assert!(
                backend
                    .snapshot()?
                    .devices
                    .iter()
                    .any(|device| device.leased)
            );
            let (facts, mut receiver) = mpsc::channel(2);
            let (stop, stopping) = watch::channel(false);
            let context = RunContext::managed(JobContext {
                instance_id: InstanceId::new("00000000-0000-0000-0000-000000000001")?,
                job_id: JobId::new("00000000-0000-0000-0000-000000000002")?,
                helper_socket: "/no-helper-needed-for-real-virtual-owner".into(),
                stop: stopping.clone(),
                facts: facts.clone(),
            });
            let session: RunSession = SessionContext {
                health: crate::health::HealthReporter::default(),
                id: SessionId::new("00000000-0000-0000-0000-000000000003")?,
                stop: stopping,
                facts,
            }
            .into();
            let exports = vec![Export {
                session,
                #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
                grant: None,
            }];
            let (closed_tx, closed_rx) = tokio::sync::oneshot::channel();
            let task = tokio::spawn(async move {
                let owner = Owner::Virtual(backend);
                let mut pending = None;
                // Production ready/shutdown pipeline with a real listener and
                // IMPORT lease; the held actor reply is not a filesystem test.
                let served = serving(
                    server,
                    &owner,
                    false,
                    None,
                    context.clone(),
                    &exports,
                    &mut pending,
                )
                .await;
                let snapshot = owner.close().await?;
                let Snapshot::Virtual(snapshot) = snapshot else {
                    return Err(Error::State("wrong owner snapshot".into()));
                };
                assert!(
                    snapshot
                        .devices
                        .iter()
                        .all(|device| !device.leased && device.pending_urbs == 0)
                );
                closed_tx
                    .send(())
                    .map_err(|_| Error::State("cleanup observer ended".into()))?;
                let (finished, recorded) = finish_exports(
                    &exports,
                    &context,
                    &served.operation,
                    served.cleanup.is_ok(),
                    true,
                    pending,
                )
                .await;
                served.operation?;
                served.cleanup?;
                finished?;
                assert!(served.recording.is_ok());
                Ok::<_, Error>(recorded)
            });
            let Some(JobMessage::Observe {
                evidence: Evidence::ExportReady,
                state: SessionState::Available,
                reply,
                ..
            }) = receiver.recv().await
            else {
                return Err(Error::State("expected held Available record".into()));
            };
            stop.send_replace(true);
            closed_rx
                .await
                .map_err(|_| Error::State("real owner did not close before ACK".into()))?;
            closed(&mut network).await?;
            assert!(TcpStream::connect(address).await.is_err());
            assert!(!reply.is_closed() && !task.is_finished());
            assert!(receiver.try_recv().is_err());
            reply
                .send(Err(Error::Permission(
                    "late Available recording rejection".into(),
                )))
                .map_err(|_| Error::State("original Available ACK was dropped".into()))?;
            let Some(JobMessage::Observe {
                evidence: Evidence::Closing,
                reply,
                ..
            }) = receiver.recv().await
            else {
                return Err(Error::State(
                    "Closing did not follow original Available".into(),
                ));
            };
            let _ = reply.send(Ok(()));
            let Some(JobMessage::Finish {
                cleanup: Cleanup::Verified,
                reply,
                ..
            }) = receiver.recv().await
            else {
                return Err(Error::State("actual cleanup did not reach Finish".into()));
            };
            let _ = reply.send(Ok(()));
            let result = task
                .await
                .map_err(|_| Error::State("real Rust owner task failed".into()))??;
            assert!(matches!(result, Err(Error::Permission(_))));
            Ok(())
        })
        .await
        .map_err(|_| Error::Timeout("held Available owner fixture".into()))?
    }

    #[tokio::test]
    async fn rejected_begin_receipt_is_recording_failure_before_any_owner() -> Result<()> {
        let (facts, mut receiver) = mpsc::channel(2);
        let (_stop, stop) = watch::channel(false);
        let context = RunContext::managed(JobContext {
            instance_id: InstanceId::new("00000000-0000-0000-0000-000000000001")?,
            job_id: JobId::new("00000000-0000-0000-0000-000000000002")?,
            helper_socket: "/this-helper-must-never-be-contacted/rustusb.sock".into(),
            stop,
            facts,
        });
        let task = tokio::spawn(async move {
            exports(&[DeviceKey::virtual_profile(VirtualProfile::Hid)], &context).await
        });
        // A deliberately rejected receipt exercises the real message boundary;
        // it is not a fake successful durable admission or a filesystem test.
        match tokio::time::timeout(Duration::from_secs(2), receiver.recv())
            .await
            .map_err(|_| Error::Timeout("begin rejection fixture".into()))?
        {
            Some(JobMessage::Begin { reply, .. }) => {
                let _ = reply.send(Err(Error::Permission("fixture store refused begin".into())));
            }
            _ => return Err(Error::State("expected begin before any owner".into())),
        }
        let result = task
            .await
            .map_err(|_| Error::State("begin rejection fixture task".into()))?;
        let error = result
            .err()
            .ok_or_else(|| Error::State("begin rejection was lost".into()))?;
        let (operation, recording) = stage_results(Err(error));
        assert!(operation.is_ok());
        assert!(matches!(recording, Err(Error::Permission(_))));
        Ok(())
    }

    async fn next_event(frontend: &mut Frontend) -> Result<Delivery> {
        tokio::time::timeout(Duration::from_secs(2), frontend.events.recv())
            .await
            .map_err(|_| Error::Timeout("foreground fixture event".into()))?
            .ok_or_else(|| Error::State("foreground fixture event stream ended".into()))
    }

    async fn import(address: std::net::SocketAddr) -> Result<TcpStream> {
        tokio::time::timeout(Duration::from_secs(2), async {
            let mut stream = TcpStream::connect(address).await?;
            let mut request = [0_u8; 40];
            request[..8].copy_from_slice(&[1, 0x11, 0x80, 3, 0, 0, 0, 0]);
            request[8..11].copy_from_slice(b"1-1");
            stream.write_all(&request).await?;
            let mut reply = [0; 320];
            stream.read_exact(&mut reply).await?;
            assert_eq!(&reply[..8], &[1, 0x11, 0, 3, 0, 0, 0, 0]);
            Ok(stream)
        })
        .await
        .map_err(|_| Error::Timeout("foreground fixture IMPORT".into()))?
    }

    async fn joined(task: JoinHandle<JobOutcome>) -> Result<JobOutcome> {
        tokio::time::timeout(Duration::from_secs(2), task)
            .await
            .map_err(|_| Error::Timeout("foreground fixture cleanup".into()))?
            .map_err(|_| Error::State("foreground fixture owner failed".into()))
    }

    async fn closed(stream: &mut TcpStream) -> Result<()> {
        let mut byte = [0];
        let read = tokio::time::timeout(Duration::from_secs(2), stream.read(&mut byte))
            .await
            .map_err(|_| Error::Timeout("foreground fixture TCP close".into()))?;
        assert!(
            matches!(read, Ok(0))
                || matches!(read, Err(ref error) if error.kind() == std::io::ErrorKind::ConnectionReset)
        );
        Ok(())
    }

    fn stopped_snapshot(outcome: &JobOutcome) -> Result<()> {
        match &outcome.final_event {
            Some(Event::Stopped {
                snapshot: Snapshot::Virtual(snapshot),
            }) => {
                assert!(
                    snapshot
                        .devices
                        .iter()
                        .all(|device| !device.leased && device.pending_urbs == 0)
                );
                Ok(())
            }
            _ => Err(Error::State(
                "foreground fixture lacked actual stopped snapshot".into(),
            )),
        }
    }

    #[tokio::test]
    async fn blocked_output_receipt_does_not_block_real_foreground_cleanup() -> Result<()> {
        let (context, mut frontend, stop) = foreground_fixture();
        let task = tokio::spawn(virtual_legacy(
            ServerConfig {
                listen: ([127, 0, 0, 1], 0).into(),
                ..ServerConfig::default()
            },
            context,
        ));
        let ready = next_event(&mut frontend).await?;
        let address = match &ready.event {
            Event::Ready {
                address,
                secure: false,
                ..
            } => *address,
            _ => return Err(Error::State("foreground fixture ready mismatch".into())),
        };
        ready.complete(Ok(()));
        let mut stream = import(address).await?;
        frontend
            .controls
            .send(Control::Snapshot {
                id: ControlId::new("held")?,
            })
            .await
            .map_err(|_| Error::State("foreground controls ended".into()))?;
        let held = next_event(&mut frontend).await?;
        match &held.event {
            Event::Snapshot {
                snapshot: Snapshot::Virtual(snapshot),
                ..
            } => assert!(snapshot.devices.iter().any(|device| device.leased)),
            _ => return Err(Error::State("foreground fixture snapshot mismatch".into())),
        }
        // The real frontend has not acknowledged this event. Stop must still
        // reach the owner and its actual IMPORT stream must be reclaimed.
        stop.send_replace(true);
        let outcome = joined(task).await?;
        stopped_snapshot(&outcome)?;
        closed(&mut stream).await?;
        outcome.into_result()?;
        drop(held);
        Ok(())
    }

    #[tokio::test]
    async fn failed_ready_delivery_keeps_actual_cleanup_and_no_success_event() -> Result<()> {
        let (context, mut frontend, _stop) = foreground_fixture();
        let task = tokio::spawn(virtual_legacy(
            ServerConfig {
                listen: ([127, 0, 0, 1], 0).into(),
                ..ServerConfig::default()
            },
            context,
        ));
        let ready = next_event(&mut frontend).await?;
        let address = match &ready.event {
            Event::Ready { address, .. } => *address,
            _ => return Err(Error::State("foreground fixture ready mismatch".into())),
        };
        let mut stream = import(address).await?;
        ready.complete(Err(Error::Io(std::io::Error::new(
            std::io::ErrorKind::BrokenPipe,
            "fixture stdout ended",
        ))));
        let outcome = joined(task).await?;
        assert!(matches!(outcome.operation, Err(Error::Io(_))));
        assert!(outcome.cleanup.is_ok());
        assert!(outcome.recording.is_ok());
        assert!(outcome.final_event.is_none());
        closed(&mut stream).await
    }

    #[tokio::test]
    async fn typed_controls_preserve_ack_error_and_ordered_shutdown() -> Result<()> {
        let (context, mut frontend, _stop) = foreground_fixture();
        let task = tokio::spawn(virtual_legacy(
            ServerConfig {
                listen: ([127, 0, 0, 1], 0).into(),
                ..ServerConfig::default()
            },
            context,
        ));
        next_event(&mut frontend).await?.complete(Ok(()));
        frontend
            .controls
            .send(Control::CdcEmit {
                id: ControlId::new("bad")?,
                hex: "secret-non-hex".into(),
            })
            .await
            .map_err(|_| Error::State("foreground controls ended".into()))?;
        let error = next_event(&mut frontend).await?;
        assert!(matches!(
            error.event,
            Event::Error {
                code: ControlError::Rejected,
                ..
            }
        ));
        assert!(!error.event.to_json()?.to_string().contains("secret"));
        error.complete(Ok(()));
        frontend
            .controls
            .send(Control::Shutdown {
                id: ControlId::new("end")?,
            })
            .await
            .map_err(|_| Error::State("foreground controls ended".into()))?;
        let ack = next_event(&mut frontend).await?;
        assert!(matches!(&ack.event, Event::Ack { id } if id.as_str() == "end"));
        assert!(!task.is_finished());
        ack.complete(Ok(()));
        let outcome = joined(task).await?;
        stopped_snapshot(&outcome)?;
        outcome.into_result()
    }

    #[test]
    fn cdc_payload_bounds_and_control_errors_remain_redacted() -> Result<()> {
        assert!(hex_bytes(&"aa".repeat(1025)).is_err());
        assert!(hex_bytes("0g").is_err());
        assert!(hex_bytes("").is_err());
        assert_eq!(hex_bytes("0034aF")?, [0, 0x34, 0xaf]);
        for bad in [
            br#"{"id":"bad\n","op":"snapshot"}"#.as_slice(),
            br#"{"id":"x","op":"snapshot","secret":"payload"}"#,
            br#"{"id":"x","op":"other"}"#,
        ] {
            let error = foreground::parse_control(bad)
                .err()
                .ok_or_else(|| Error::State("accepted invalid control".into()))?;
            assert!(!error.to_string().contains("payload"));
        }
        Ok(())
    }

    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    #[tokio::test]
    async fn finish_attempts_all_received_grants_before_any_recording_and_retains_errors()
    -> Result<()> {
        let (context, _frontend, _stop) = foreground_fixture();
        let key = DeviceKey::physical(&format!("usb-{}", "a".repeat(64)))?;
        let mut exports = exports(&[key.clone(), key.clone(), key], &context)
            .await
            .map_err(|error| match error {
                StageError::Operation(error) | StageError::Recording(error) => error,
            })?;
        for (export, id) in exports.iter_mut().zip(["first", "second", "third"]) {
            export.grant = Some(Grant {
                attempted: true,
                lease_id: Some(id.into()),
            });
        }
        let mut attempted = Vec::new();
        let (result, evidence) = finish_grants(&exports, true, Cleanup::Verified, |id| {
            attempted.push(id.clone());
            std::future::ready(match id.as_str() {
                "first" => Err(Error::Permission("first rejection".into())),
                "second" => Err(Error::State("second rejection".into())),
                _ => Ok(()),
            })
        })
        .await;
        assert_eq!(attempted, ["first", "second", "third"]);
        assert_eq!(
            evidence,
            [
                Cleanup::Unconfirmed,
                Cleanup::Unconfirmed,
                Cleanup::Verified
            ]
        );
        let error = result
            .err()
            .ok_or_else(|| Error::State("finish errors were lost".into()))?
            .to_string();
        assert!(error.contains("first rejection") && error.contains("second rejection"));
        Ok(())
    }

    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    #[tokio::test]
    async fn invalid_physical_selection_and_network_policy_precede_helper_io() -> Result<()> {
        let id = format!("usb-{}", "a".repeat(64));
        for ids in [vec![], vec![id.clone(); 2], vec![id.clone(); 17]] {
            let (context, _frontend, _stop) = foreground_fixture();
            let outcome = physical_legacy(ServerConfig::default(), ids, context).await;
            assert!(matches!(outcome.operation, Err(Error::InvalidInput(_))));
        }
        let (context, _frontend, _stop) = foreground_fixture();
        let outcome = physical_legacy(
            ServerConfig {
                listen: ([0, 0, 0, 0], 3240).into(),
                ..ServerConfig::default()
            },
            vec![id],
            context,
        )
        .await;
        assert!(matches!(outcome.operation, Err(Error::Permission(_))));
        Ok(())
    }

    // These exercise the service's fact conversion and durable acknowledgement
    // boundary, not a real USB claim or a blocking kernel completion.
    #[tokio::test]
    async fn soft_cleanup_deadline_is_not_finished_until_real_terminal() -> Result<()> {
        let (facts, mut receiver) = mpsc::channel(2);
        let (_stop, stop) = watch::channel(false);
        let session: RunSession = SessionContext {
            health: crate::health::HealthReporter::default(),
            id: SessionId::new("00000000-0000-0000-0000-000000000001")?,
            stop,
            facts,
        }
        .into();
        let mut value = ServerSessionSnapshot {
            id: ServerSessionId::new(1)?,
            device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
            principal: Some("client".into()),
            phase: ServerSessionPhase::CleanupUnconfirmed,
            stop_requested: true,
            revision: 5,
            metrics: ServerSessionMetrics::default(),
            operation_error: Some(ServerSessionError::Cancelled),
            cleanup_error: Some(ServerSessionError::Timeout),
            cleanup: None,
        };
        let owner = session.clone();
        let observed = value.clone();
        let task = tokio::spawn(async move { record(&owner, &observed).await });
        match tokio::time::timeout(Duration::from_secs(2), receiver.recv())
            .await
            .map_err(|_| Error::Timeout("soft deadline fixture fact".into()))?
        {
            Some(JobMessage::Observe {
                state: SessionState::Error,
                evidence: Evidence::Unconfirmed,
                reply,
                ..
            }) => {
                let _ = reply.send(Ok(()));
            }
            _ => {
                return Err(Error::State(
                    "soft deadline incorrectly emitted a terminal fact".into(),
                ));
            }
        }
        assert!(
            !task
                .await
                .map_err(|_| Error::State("soft deadline fixture task".into()))??
        );
        value.phase = ServerSessionPhase::Closed;
        value.cleanup = Some(ServerCleanup::Verified);
        value.cleanup_error = None;
        value.revision = 6;
        let task = tokio::spawn(async move { record(&session, &value).await });
        match tokio::time::timeout(Duration::from_secs(2), receiver.recv())
            .await
            .map_err(|_| Error::Timeout("terminal fixture fact".into()))?
        {
            Some(JobMessage::Finish {
                cleanup: Cleanup::Verified,
                operation_error: Some(Failure::Cancelled),
                reply,
                ..
            }) => {
                let _ = reply.send(Ok(()));
            }
            _ => {
                return Err(Error::State(
                    "real terminal did not emit its separate cleanup result".into(),
                ));
            }
        }
        assert!(
            task.await
                .map_err(|_| Error::State("terminal fixture task".into()))??
        );
        Ok(())
    }
}

//! Native TLS owners use only helper-held kernel endpoints; no plain listener.

use super::{
    io::{self, Counters},
    link,
};
use crate::{
    context::stopped,
    foreground::{Control, ControlError, Event, JobOutcome, NativeDeviceSnapshot, Snapshot},
    model::{Cleanup, Evidence, Failure, Metrics, SessionRole},
    run_context::{RunContext, RunSession},
};
use rustusb_core::{DeviceKey, Error, Result, SessionState, application::BrokerResourceKind};
use rustusb_daemon::{NativeExportHandle, prepare_native_export, prepare_native_export_tracked};
use rustusb_protocol::{
    RemoteDevice,
    secure::{self, DeviceMapping, SecureOperation},
    server::{self, Management},
};
use rustusb_transport::{TlsServer, VerifiedPeer, config::LoadedServerConfig};
use std::{
    collections::HashSet,
    future::Future,
    pin::Pin,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::{
    io::{AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::{Mutex, MutexGuard, Semaphore, watch},
    task::JoinSet,
};

const FRAME_DEADLINE: Duration = Duration::from_secs(5);

#[derive(Debug)]
enum StageError {
    Operation(Error),
    Recording(Error),
}
impl From<Error> for StageError {
    fn from(error: Error) -> Self {
        Self::Operation(error)
    }
}
fn stage_outcome(result: std::result::Result<(), StageError>) -> JobOutcome {
    match result {
        Ok(()) => success(),
        Err(StageError::Operation(error)) => JobOutcome::before_start(error),
        Err(StageError::Recording(error)) => JobOutcome {
            operation: Ok(()),
            cleanup: Ok(()),
            recording: Err(error),
            final_event: None,
        },
    }
}

fn stop_requested(receiver: &watch::Receiver<bool>) -> bool {
    *receiver.borrow() || receiver.has_changed().is_err()
}

type Recording = Pin<Box<dyn Future<Output = Result<()>> + Send>>;

struct RelayStops {
    job: watch::Receiver<bool>,
    local: watch::Receiver<bool>,
    export: watch::Receiver<bool>,
    stub: watch::Receiver<bool>,
    source: watch::Receiver<bool>,
    link: Option<rustusb_transport::LinkMonitor>,
    link_ended: Option<watch::Receiver<bool>>,
}
impl RelayStops {
    async fn wait<T>(
        &mut self,
        future: impl Future<Output = T>,
    ) -> std::result::Result<Option<T>, StageError> {
        tokio::select! {
            biased;
            _ = stopped(&mut self.job) => Ok(None),
            _ = stopped(&mut self.local) => Ok(None),
            _ = stopped(&mut self.export) => Ok(None),
            _ = stopped(&mut self.stub) => Ok(None),
            _ = stopped(&mut self.source) => Err(Error::State("Native export invalidated during relay".into()).into()),
            error = link::ended(&mut self.link_ended, self.link.as_ref()) => Err(StageError::Operation(error)),
            result = future => Ok(Some(result)),
        }
    }
}

async fn record_or_stop(
    mut recording: Recording,
    postponed: &mut Option<Recording>,
    stops: &mut RelayStops,
) -> std::result::Result<bool, StageError> {
    match stops.wait(&mut recording).await {
        Ok(Some(result)) => result.map(|()| true).map_err(StageError::Recording),
        stopped => {
            // Keep this exact future, including a request already accepted by
            // the persistence actor. It owns no helper handle or device lock.
            *postponed = Some(recording);
            stopped.map(|_| false)
        }
    }
}

async fn cleanup_then_unlock(
    cleanup: impl Future<Output = Result<()>>,
    guard: Option<MutexGuard<'_, ()>>,
    failed: &AtomicBool,
) -> Result<()> {
    let result = cleanup.await;
    if result.is_err() {
        failed.store(true, Ordering::Release);
    }
    // The actual helper result, including failure, precedes unlock. No WAL
    // acknowledgement is needed to let the export perform its own real close.
    drop(guard);
    result
}

struct Export {
    key: DeviceKey,
    published: RemoteDevice,
    handle: Mutex<Option<NativeExportHandle>>,
    session: RunSession,
    invalidated: watch::Receiver<bool>,
    stop: watch::Sender<bool>,
    use_lock: Mutex<()>,
    reserved: AtomicBool,
    failed: AtomicBool,
}
impl Export {
    fn accepting(&self) -> bool {
        !stop_requested(&self.stop.subscribe())
            && !stop_requested(&self.invalidated)
            && !self.reserved.load(Ordering::Acquire)
            && !self.failed.load(Ordering::Acquire)
    }
    async fn close(&self) -> JobOutcome {
        // Disable catalog/IMPORT before stopping this export's sole stub owner.
        self.stop.send_replace(true);
        let using = self.use_lock.lock().await;
        let handle = self.handle.lock().await.take();
        let cleanup = cleanup_then_unlock(
            async {
                match handle {
                    Some(handle) => handle.close().await,
                    None => Err(Error::State("Native export close repeated".into())),
                }
            },
            Some(using),
            &self.failed,
        )
        .await;
        let observed = self
            .session
            .observe(SessionState::Disconnecting, Evidence::Closing, None)
            .await;
        let failure = observed.as_ref().err().map(Failure::from_error);
        let recorded = self
            .session
            .finish(
                failure,
                if cleanup.is_ok() {
                    Cleanup::Verified
                } else {
                    Cleanup::Unconfirmed
                },
                Metrics::default(),
            )
            .await;
        let mut recording = observed;
        merge_result(&mut recording, recorded);
        JobOutcome {
            operation: Ok(()),
            cleanup,
            recording,
            final_event: None,
        }
    }
}

struct Reservation(Arc<Export>);
impl Drop for Reservation {
    fn drop(&mut self) {
        self.0.reserved.store(false, Ordering::Release);
    }
}

async fn prepare(
    key: &DeviceKey,
    context: &RunContext,
) -> std::result::Result<Option<Arc<Export>>, JobOutcome> {
    let session = context
        .begin_session(SessionRole::Export, key.clone(), None)
        .await
        .map_err(|error| stage_outcome(Err(StageError::Recording(error))))?;
    let mut attempted = false;
    let mut skipped = false;
    let mut owner = None;
    let operation = async {
        session
            .observe(SessionState::Exporting, Evidence::Intent, None)
            .await
            .map_err(StageError::Recording)?;
        let correlation = session
            .attempt(BrokerResourceKind::NativeExport)
            .await
            .map_err(StageError::Recording)?;
        attempted = correlation.is_some();
        if stop_requested(&context.stop) || stop_requested(&session.stop) {
            if !attempted {
                skipped = true;
                return Ok(());
            }
            return Err(Error::State("Native export stopped before prepare".into()).into());
        }
        let id = key
            .fingerprint()
            .ok_or_else(|| Error::InvalidInput("Native device fingerprint required".into()))?;
        attempted = true;
        owner = Some(match correlation.clone() {
            Some(correlation) => {
                prepare_native_export_tracked(&context.helper_socket, id.to_owned(), correlation)
                    .await?
            }
            None => prepare_native_export(&context.helper_socket, id.to_owned()).await?,
        });
        let handle = owner
            .as_ref()
            .ok_or_else(|| Error::State("Native export owner absent".into()))?;
        session
            .resource(correlation, handle.lease_id())
            .await
            .map_err(StageError::Recording)?;
        if handle.device_key() != key {
            return Err(Error::Protocol("Native export identity differs".into()).into());
        }
        Ok(())
    }
    .await;
    if skipped {
        return Ok(None);
    }
    if operation.is_err() {
        let mut outcome = stage_outcome(operation);
        let cleanup = match owner {
            Some(handle) => handle.close().await,
            None if attempted => Err(Error::State("Native export prepare unconfirmed".into())),
            None => Ok(()),
        };
        let recorded = session
            .finish(
                outcome
                    .operation
                    .as_ref()
                    .err()
                    .or(outcome.recording.as_ref().err())
                    .map(Failure::from_error),
                if cleanup.is_err() {
                    Cleanup::Unconfirmed
                } else if attempted {
                    Cleanup::Verified
                } else {
                    Cleanup::NotStarted
                },
                Metrics::default(),
            )
            .await;
        outcome.cleanup = cleanup;
        merge_result(&mut outcome.recording, recorded);
        return Err(outcome);
    }
    let handle = owner.ok_or_else(|| {
        JobOutcome::before_start(Error::State("Native export owner absent".into()))
    })?;
    let published = handle.published_device().clone();
    let invalidated = handle.invalidated();
    let (stop, _) = watch::channel(false);
    Ok(Some(Arc::new(Export {
        key: key.clone(),
        published,
        handle: Mutex::new(Some(handle)),
        session,
        invalidated,
        stop,
        use_lock: Mutex::new(()),
        reserved: AtomicBool::new(false),
        failed: AtomicBool::new(false),
    })))
}

enum Task {
    Export(JobOutcome),
    Connection(JobOutcome),
}

fn success() -> JobOutcome {
    JobOutcome {
        operation: Ok(()),
        cleanup: Ok(()),
        recording: Ok(()),
        final_event: None,
    }
}

fn merge_result(target: &mut Result<()>, result: Result<()>) {
    let previous = std::mem::replace(target, Ok(()));
    *target = match (previous, result) {
        (Err(first), Err(second)) => Err(Error::State(format!("{first}; {second}"))),
        (Err(error), _) | (_, Err(error)) => Err(error),
        _ => Ok(()),
    };
}

fn merge(target: &mut JobOutcome, result: JobOutcome, include_operation: bool) {
    if include_operation {
        merge_result(&mut target.operation, result.operation);
    }
    merge_result(&mut target.cleanup, result.cleanup);
    merge_result(&mut target.recording, result.recording);
}

fn snapshot(exports: &[Arc<Export>]) -> Snapshot {
    Snapshot::Native {
        metadata_scope: "published_registration_not_live_configuration",
        devices: exports
            .iter()
            .map(|export| NativeDeviceSnapshot {
                device_key: export.key.clone(),
                published_device: export.published.clone(),
                accepting_imports: export.accepting(),
            })
            .collect(),
    }
}

pub(super) async fn run(config: Arc<LoadedServerConfig>, context: RunContext) -> JobOutcome {
    let mut exports = Vec::new();
    let mut outcome = success();
    let mut buses = HashSet::new();
    for key in &config.devices {
        if stop_requested(&context.stop) {
            break;
        }
        match prepare(key, &context).await {
            Ok(Some(export)) => {
                let distinct = buses.insert(export.published.bus_id.clone());
                exports.push(export);
                if !distinct {
                    outcome.operation =
                        Err(Error::Protocol("duplicate Native publication bus".into()));
                    break;
                }
            }
            Ok(None) => break,
            Err(result) => {
                merge(&mut outcome, result, true);
                break;
            }
        }
    }
    if outcome.operation.is_ok() && outcome.cleanup.is_ok() && outcome.recording.is_ok() {
        outcome.operation = server::devlist_reply(
            &exports
                .iter()
                .map(|export| export.published.clone())
                .collect::<Vec<_>>(),
        )
        .map(|_| ());
    }
    if outcome.operation.is_err()
        || outcome.cleanup.is_err()
        || outcome.recording.is_err()
        || stop_requested(&context.stop)
    {
        merge(&mut outcome, close_all(&exports).await, true);
        return outcome;
    }
    // A stop never drops a helper prepare or listener acquisition future.
    let listener = match TcpListener::bind(config.listen).await {
        Ok(listener) => listener,
        Err(error) => {
            outcome.operation = Err(error.into());
            merge(&mut outcome, close_all(&exports).await, true);
            return outcome;
        }
    };
    for export in &exports {
        if let Err(error) = export
            .session
            .observe(SessionState::Available, Evidence::ExportReady, None)
            .await
        {
            merge_result(&mut outcome.recording, Err(error));
            break;
        }
    }
    if outcome.recording.is_ok() {
        outcome.operation = async {
            context
                .emit(Event::Ready {
                    address: listener.local_addr()?,
                    secure: true,
                    snapshot: snapshot(&exports),
                })
                .await
        }
        .await;
    }
    if outcome.operation.is_err() || outcome.recording.is_err() || stop_requested(&context.stop) {
        drop(listener);
        merge(&mut outcome, close_all(&exports).await, true);
        return outcome;
    }
    let mut tasks = JoinSet::new();
    let (stop_all, stop) = watch::channel(false);
    for export in &exports {
        let export = export.clone();
        let mut job_stop = stop.clone();
        tasks.spawn(async move {
            let mut local_stop = export.session.stop.clone();
            let mut loss = export.invalidated.clone();
            let lost = tokio::select! {
                biased;
                _ = stopped(&mut job_stop) => false,
                _ = stopped(&mut local_stop) => false,
                _ = stopped(&mut loss) => true,
            };
            let mut result = export.close().await;
            if lost {
                result.operation = Err(Error::State("Native export invalidated".into()));
            }
            Task::Export(result)
        });
    }
    let budget = Arc::new(Semaphore::new(16));
    let mut job_stop = context.stop.clone();
    let operation = loop {
        tokio::select! {
            biased;
            _ = stopped(&mut job_stop) => break Ok(()),
            result = tasks.join_next(), if !tasks.is_empty() => match result {
                Some(Ok(Task::Export(result))) => {
                    let failed = result.operation.is_err() || result.cleanup.is_err() || result.recording.is_err();
                    merge(&mut outcome, result, true);
                    if failed { break Ok(()); }
                },
                Some(Ok(Task::Connection(result))) => {
                    if result.operation.is_err() { tracing::debug!("Native connection ended with an operation error"); }
                    let failed = result.cleanup.is_err() || result.recording.is_err();
                    // A malformed/denied peer does not terminate the listener;
                    // owned cleanup or recording failures always do.
                    merge(&mut outcome, result, false);
                    if failed { break Ok(()); }
                },
                Some(Err(_)) => break Err(Error::State("Native owned task failed".into())),
                None => {},
            },
            control = context.next_control() => {
                let event = match control {
                    Control::Eof => break Ok(()),
                    Control::Shutdown { id } => {
                        let result = context.emit(Event::Ack { id }).await;
                        break result;
                    },
                    Control::Snapshot { id } => Event::Snapshot { id, snapshot: snapshot(&exports) },
                    _ => Event::Error { id: None, code: ControlError::InvalidControl },
                };
                if let Err(error) = context.emit(event).await { break Err(error); }
            },
            accepted = listener.accept() => {
                let (network, _) = match accepted { Ok(value) => value, Err(error) => break Err(error.into()) };
                let Ok(permit) = budget.clone().try_acquire_owned() else { continue };
                let config = config.clone();
                let registry = exports.clone();
                let context = context.clone();
                let stop = stop.clone();
                tasks.spawn(async move {
                    let _permit = permit;
                    Task::Connection(connection(network, config.tls.clone(), registry, context, stop).await)
                });
            }
        }
    };
    merge_result(&mut outcome.operation, operation);
    drop(listener);
    stop_all.send_replace(true);
    // Join every real stub owner before its export's final baseline close.
    while let Some(task) = tasks.join_next().await {
        match task {
            Ok(Task::Export(result)) => merge(&mut outcome, result, true),
            Ok(Task::Connection(result)) => merge(&mut outcome, result, false),
            Err(_) => merge_result(
                &mut outcome.cleanup,
                Err(Error::State("Native task cleanup unconfirmed".into())),
            ),
        }
    }
    if exports
        .iter()
        .any(|export| export.failed.load(Ordering::Acquire))
    {
        merge_result(
            &mut outcome.cleanup,
            Err(Error::State("Native owned cleanup unconfirmed".into())),
        );
    }
    if outcome.operation.is_ok() && outcome.cleanup.is_ok() && outcome.recording.is_ok() {
        outcome.final_event = Some(Event::NativeStopped {
            exports_closed: exports.len(),
        });
    }
    outcome
}

async fn close_all(exports: &[Arc<Export>]) -> JobOutcome {
    let mut tasks = JoinSet::new();
    for export in exports {
        let export = export.clone();
        tasks.spawn(async move { export.close().await });
    }
    join_closes(tasks).await
}

async fn join_closes(mut tasks: JoinSet<JobOutcome>) -> JobOutcome {
    let mut outcome = success();
    // Every close was started before waiting for a recording acknowledgement.
    // Never abort a sibling that still owns an actual helper close operation.
    while let Some(result) = tasks.join_next().await {
        match result {
            Ok(result) => merge(&mut outcome, result, true),
            Err(_) => merge_result(
                &mut outcome.cleanup,
                Err(Error::State("Native export close task failed".into())),
            ),
        }
    }
    outcome
}

async fn write_frame(writer: &mut (impl AsyncWrite + Unpin), bytes: &[u8]) -> Result<()> {
    tokio::time::timeout(FRAME_DEADLINE, async {
        writer.write_all(bytes).await?;
        writer.flush().await
    })
    .await
    .map_err(|_| Error::Timeout("Native TLS management response".into()))??;
    Ok(())
}

async fn management(
    stream: &mut link::Io,
    exports: &[Arc<Export>],
    peer: &VerifiedPeer,
) -> Result<Option<Reservation>> {
    match (
        secure::read_prelude(stream).await?,
        server::read_management(stream).await?,
    ) {
        (SecureOperation::Catalog, Management::Devlist) => {
            let catalog: Vec<_> = exports
                .iter()
                .filter(|export| export.accepting() && peer.allows(&export.key))
                .collect();
            let mapping: Vec<_> = catalog
                .iter()
                .map(|export| DeviceMapping {
                    device_key: export.key.clone(),
                    bus_id: export.published.bus_id.clone(),
                })
                .collect();
            let devices: Vec<_> = catalog
                .iter()
                .map(|export| export.published.clone())
                .collect();
            secure::write_catalog_mapping(stream, &mapping).await?;
            write_frame(stream, &server::devlist_reply(&devices)?).await?;
            Ok(None)
        }
        (SecureOperation::Import { device_key }, Management::Import(bus_id)) => {
            let Some(export) = exports.iter().find(|export| {
                export.key == device_key
                    && export.published.bus_id == bus_id
                    && peer.allows(&export.key)
                    && !stop_requested(&export.stop.subscribe())
                    && !stop_requested(&export.invalidated)
                    && !export.failed.load(Ordering::Acquire)
            }) else {
                write_frame(stream, &server::import_reply(4, None)?).await?;
                return Ok(None);
            };
            if export
                .reserved
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_err()
            {
                write_frame(stream, &server::import_reply(2, None)?).await?;
                return Ok(None);
            }
            Ok(Some(Reservation(export.clone())))
        }
        _ => Err(Error::Protocol(
            "secure prelude and operation differ".into(),
        )),
    }
}

async fn connection(
    network: TcpStream,
    tls: TlsServer,
    exports: Vec<Arc<Export>>,
    context: RunContext,
    job_stop: watch::Receiver<bool>,
) -> JobOutcome {
    let mut stop = job_stop.clone();
    let accepted = tokio::select! {
        biased;
        _ = stopped(&mut stop) => return success(),
        result = link::accept(&tls, network) => result,
    };
    let (connection, peer) = match accepted {
        Ok(value) => value,
        Err(error) => return JobOutcome::before_start(error),
    };
    let mut owner = connection.owner;
    let mut outcome = match connection_inner(
        connection.io,
        peer,
        connection.monitor,
        &mut owner,
        exports,
        context,
        job_stop,
    )
    .await
    {
        Ok(outcome) => outcome,
        Err(error) => JobOutcome::before_start(error),
    };
    let (closed, _) = link::finish(&mut owner).await;
    if let Err(error) = closed {
        outcome.operation = Err(error);
    }
    outcome
}

async fn connection_inner(
    mut stream: link::Io,
    peer: VerifiedPeer,
    link_monitor: Option<rustusb_transport::LinkMonitor>,
    link_owner: &mut Option<rustusb_transport::LinkOwner>,
    exports: Vec<Arc<Export>>,
    context: RunContext,
    mut job_stop: watch::Receiver<bool>,
) -> Result<JobOutcome> {
    let mut link_ended = link_monitor
        .as_ref()
        .map(rustusb_transport::LinkMonitor::ended);
    let reservation = tokio::select! {
        biased;
        _ = stopped(&mut job_stop) => return Ok(success()),
        error = link::ended(&mut link_ended, link_monitor.as_ref()) => return Err(error),
        result = management(&mut stream, &exports, &peer) => result?,
    };
    let Some(reservation) = reservation else {
        return Ok(success());
    };
    let export = &reservation.0;
    if stop_requested(&job_stop) || stop_requested(&export.stop.subscribe()) {
        return Ok(success());
    }
    // The actual TLS principal and exact authorized stable key are recorded
    // before a helper stub operation; no UI-supplied principal is accepted.
    let session = match context
        .begin_session(
            SessionRole::Incoming,
            export.key.clone(),
            Some(peer.principal().to_owned()),
        )
        .await
    {
        Ok(session) => session,
        Err(error) => return Ok(stage_outcome(Err(StageError::Recording(error)))),
    };
    session.link(link_monitor.clone())?;
    let local_stop = session.stop.clone();
    let export_stop = export.stop.subscribe();
    let counters = Counters::new();
    let mut attempted = false;
    let mut stub = None;
    let mut using = None;
    let mut postponed_recording = None;
    let mut network = Some(stream);
    let mut copying = None;
    let operation = async {
        session.observe(SessionState::Connecting, Evidence::Intent, None).await.map_err(StageError::Recording)?;
        let correlation = session.attempt(BrokerResourceKind::NativeStub).await.map_err(StageError::Recording)?;
        attempted = correlation.is_some();
        if stop_requested(&job_stop) || stop_requested(&local_stop) || stop_requested(&export_stop) { return Ok(()); }
        link::check(link_monitor.as_ref())?;
        using = Some(export.use_lock.lock().await);
        // Admission may have waited for disk without holding the resource lock.
        // Recheck the original export after acquiring it; never substitute one.
        if stop_requested(&job_stop) || stop_requested(&local_stop) || stop_requested(&export_stop) { return Ok(()); }
        link::check(link_monitor.as_ref())?;
        let owner_guard = export.handle.lock().await;
        let owner = owner_guard.as_ref().ok_or_else(|| Error::State("Native export closed before stub".into()))?;
        attempted = true;
        let opened = match correlation.clone() {
            Some(correlation) => owner.open_stub_tracked(correlation).await,
            None => owner.open_stub().await,
        };
        drop(owner_guard);
        stub = Some(match opened {
            Ok(stub) => stub,
            Err(error) => {
                // Keep legacy foreground IMPORT error replies. Managed jobs
                // retain their established EOF-on-helper-error behavior.
                if context.is_foreground() && !stop_requested(&job_stop) && !stop_requested(&export_stop) {
                    let status = if matches!(&error, Error::State(_)) { 2 } else { 4 };
                    if let Some(stream) = network.as_mut()
                        && let Err(reply) = write_frame(stream, &server::import_reply(status, None)?).await {
                        return Err(Error::State(format!("Native stub prepare failed ({error}); IMPORT failure response failed ({reply})")).into());
                    }
                }
                return Err(error.into());
            }
        });
        let stub = stub.as_mut().ok_or_else(|| Error::State("Native stub owner absent".into()))?;
        let mut stops = RelayStops {
            job: job_stop.clone(), local: local_stop.clone(), export: export_stop.clone(),
            stub: stub.invalidated(), source: export.invalidated.clone(),
            link: link_monitor.clone(), link_ended: link_monitor.as_ref().map(rustusb_transport::LinkMonitor::ended),
        };
        let reporter = session.clone();
        let channel_id = stub.channel_id().to_owned();
        if !record_or_stop(Box::pin(async move { reporter.resource(correlation, &channel_id).await }), &mut postponed_recording, &mut stops).await? { return Ok(()); }
        let reporter = session.clone();
        if !record_or_stop(Box::pin(async move { reporter.observe(SessionState::Connecting, Evidence::Prepared, None).await }), &mut postponed_recording, &mut stops).await? { return Ok(()); }
        if stop_requested(&job_stop) || stop_requested(&local_stop) || stop_requested(&export_stop) { return Ok(()); }
        link::check(link_monitor.as_ref())?;
        let relay = stub.take_relay()?;
        // commit is received before a stop is processed; the helper actor
        // retains any real blocking operation even if its caller disappears.
        stub.commit().await?;
        if stop_requested(&job_stop) || stop_requested(&local_stop) || stop_requested(&export_stop) { return Ok(()); }
        let stream = network.as_mut().ok_or_else(|| Error::State("Native network owner absent".into()))?;
        write_frame(stream, &server::import_reply(0, Some(&export.published))?).await?;
        let reporter = session.clone();
        let sampled = counters.snapshot();
        if !record_or_stop(Box::pin(async move { reporter.observe(SessionState::Connected, Evidence::ImportReplyFlushed, Some(sampled)).await }), &mut postponed_recording, &mut stops).await? { return Ok(()); }
        let stream = network.take().ok_or_else(|| Error::State("Native network owner absent".into()))?;
        let relay_monitor = link_monitor.clone();
        let relay_counters = counters.clone();
        copying = Some(tokio::spawn(async move {
            let mut ended = relay_monitor.as_ref().map(rustusb_transport::LinkMonitor::ended);
            tokio::select! {
                biased;
                error = link::ended(&mut ended, relay_monitor.as_ref()) => Err(error),
                result = io::relay(stream, relay, relay_counters) => result,
            }
        }));
        let mut sample = tokio::time::interval(Duration::from_secs(1));
        sample.tick().await;
        loop {
            let task = copying.as_mut().ok_or_else(|| Error::State("Native relay task absent".into()))?;
            let event = stops.wait(async {
                tokio::select! {
                    result = &mut *task => Some(result),
                    _ = sample.tick() => None,
                }
            }).await?;
            match event {
                None => return Ok(()),
                Some(Some(result)) => {
                    copying.take();
                    return result.map_err(|_| Error::State("Native relay task failed".into()))?.map_err(StageError::Operation);
                },
                Some(None) => {
                    let reporter = session.clone();
                    let sampled = counters.snapshot();
                    if !record_or_stop(Box::pin(async move { reporter.observe(SessionState::Connected, Evidence::ImportReplyFlushed, Some(sampled)).await }), &mut postponed_recording, &mut stops).await? { return Ok(()); }
                }
            }
        }
    }.await;
    drop(network);
    if let Some(task) = copying {
        if !task.is_finished() {
            task.abort();
        }
        let _ = task.await;
    }
    let (link_result, _) = link::finish(link_owner).await;
    let mut outcome = stage_outcome(operation);
    if let Err(error) = link_result {
        outcome.operation = Err(error);
    }
    let cleanup = cleanup_then_unlock(
        async {
            match stub {
                Some(stub) => stub.close().await,
                None if attempted => Err(Error::State("Native stub prepare unconfirmed".into())),
                None => Ok(()),
            }
        },
        using.take(),
        &export.failed,
    )
    .await;
    // Only after real helper close/unlock do we await the retained ACK. Its
    // original phase must complete before Closing/Finish can be submitted.
    if let Some(recording) = postponed_recording {
        merge_result(&mut outcome.recording, recording.await);
    }
    let closing = session
        .observe(
            SessionState::Disconnecting,
            Evidence::Closing,
            Some(counters.snapshot()),
        )
        .await;
    let failure = outcome
        .operation
        .as_ref()
        .err()
        .or(outcome.recording.as_ref().err())
        .or(closing.as_ref().err())
        .map(Failure::from_error);
    let recorded = session
        .finish(
            failure,
            if cleanup.is_err() {
                Cleanup::Unconfirmed
            } else if attempted {
                Cleanup::Verified
            } else {
                Cleanup::NotStarted
            },
            counters.snapshot(),
        )
        .await;
    outcome.cleanup = cleanup;
    merge_result(&mut outcome.recording, closing);
    merge_result(&mut outcome.recording, recorded);
    Ok(outcome)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncReadExt;

    #[tokio::test]
    async fn data_link_failure_releases_real_streams_and_lock_before_retained_ack() -> Result<()> {
        // This drives the production stop/record/cleanup helpers with actual
        // in-memory stream owners. It does not claim a kernel USB close.
        let (watch_owner, receiver) = watch::channel(false);
        let (link_end, ended) = watch::channel(false);
        let (network, mut remote) = tokio::io::duplex(1);
        let (relay, mut kernel) = tokio::io::duplex(1);
        let lock = Arc::new(Mutex::new(()));
        let owner_lock = lock.clone();
        let (started, recording_started) = tokio::sync::oneshot::channel();
        let (ack, acknowledged) = tokio::sync::oneshot::channel::<Result<()>>();
        let (cleaned, cleanup_seen) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(async move {
            let using = owner_lock.lock().await;
            let mut stops = RelayStops {
                job: receiver.clone(),
                local: receiver.clone(),
                export: receiver.clone(),
                stub: receiver.clone(),
                source: receiver,
                link: None,
                link_ended: Some(ended),
            };
            let mut pending = None;
            let result = record_or_stop(
                Box::pin(async move {
                    started
                        .send(())
                        .map_err(|_| Error::State("fixture record observer".into()))?;
                    acknowledged
                        .await
                        .map_err(|_| Error::State("fixture record abandoned".into()))?
                }),
                &mut pending,
                &mut stops,
            )
            .await;
            assert!(matches!(result, Err(StageError::Operation(_))));
            let failed = AtomicBool::new(false);
            cleanup_then_unlock(
                async move {
                    drop(network);
                    drop(relay);
                    Ok(())
                },
                Some(using),
                &failed,
            )
            .await?;
            cleaned
                .send(())
                .map_err(|_| Error::State("fixture cleanup observer".into()))?;
            pending
                .ok_or_else(|| Error::State("fixture ACK dropped".into()))?
                .await
        });
        tokio::time::timeout(Duration::from_secs(2), async {
            recording_started
                .await
                .map_err(|_| Error::State("fixture ACK never started".into()))?;
            link_end.send_replace(true);
            cleanup_seen
                .await
                .map_err(|_| Error::State("link failure did not enter cleanup".into()))?;
            assert!(!*watch_owner.borrow());
            assert!(lock.try_lock().is_ok());
            assert!(!task.is_finished());
            let mut byte = [0];
            assert_eq!(remote.read(&mut byte).await?, 0);
            assert_eq!(kernel.read(&mut byte).await?, 0);
            ack.send(Err(Error::State("original delayed WAL rejection".into())))
                .map_err(|_| Error::State("original ACK was cancelled".into()))?;
            assert!(matches!(
                task.await
                    .map_err(|_| Error::State("fixture owner task".into()))?,
                Err(Error::State(_))
            ));
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("link-ended WAL fixture".into()))?
    }

    #[tokio::test]
    async fn stopped_recording_keeps_its_ack_but_does_not_hold_cleanup_lock() -> Result<()> {
        // Software ownership fixture only: the cleanup future below models a
        // helper result. It makes no kernel, driver or real-helper claim.
        let lock = Arc::new(Mutex::new(()));
        let failed = Arc::new(AtomicBool::new(false));
        let (stop, receiver) = watch::channel(false);
        let (started, recording_started) = tokio::sync::oneshot::channel();
        let (ack, acknowledgement) = tokio::sync::oneshot::channel::<Result<()>>();
        let (closing, cleanup_started) = tokio::sync::oneshot::channel();
        let (release, cleanup_allowed) = tokio::sync::oneshot::channel();
        let owner_lock = lock.clone();
        let owner_failed = failed.clone();
        let task = tokio::spawn(async move {
            let using = owner_lock.lock().await;
            let mut stops = RelayStops {
                job: receiver.clone(),
                local: receiver.clone(),
                export: receiver.clone(),
                stub: receiver.clone(),
                source: receiver,
                link: None,
                link_ended: None,
            };
            let mut postponed = None;
            let complete = record_or_stop(
                Box::pin(async move {
                    started
                        .send(())
                        .map_err(|_| Error::State("record start observer ended".into()))?;
                    acknowledgement
                        .await
                        .map_err(|_| Error::State("record acknowledgement abandoned".into()))?
                }),
                &mut postponed,
                &mut stops,
            )
            .await;
            assert!(matches!(complete, Ok(false)));
            let cleanup = cleanup_then_unlock(
                async move {
                    closing
                        .send(())
                        .map_err(|_| Error::State("cleanup observer ended".into()))?;
                    cleanup_allowed
                        .await
                        .map_err(|_| Error::State("cleanup release abandoned".into()))?;
                    Err(Error::State("fixture actual cleanup failure".into()))
                },
                Some(using),
                &owner_failed,
            )
            .await;
            let recording = postponed
                .ok_or_else(|| Error::State("pending record future lost".into()))?
                .await;
            Ok::<_, Error>(JobOutcome {
                operation: Ok(()),
                cleanup,
                recording,
                final_event: None,
            })
        });
        tokio::time::timeout(Duration::from_secs(2), async {
            recording_started
                .await
                .map_err(|_| Error::State("record never polled".into()))?;
            assert!(lock.try_lock().is_err());
            stop.send_replace(true);
            cleanup_started
                .await
                .map_err(|_| Error::State("actual cleanup not started".into()))?;
            assert!(lock.try_lock().is_err());
            release
                .send(())
                .map_err(|_| Error::State("cleanup future abandoned".into()))?;
            let unlocked = lock.lock().await;
            assert!(failed.load(Ordering::Acquire));
            assert!(!task.is_finished());
            drop(unlocked);
            // A successful send proves that the original already-polled future
            // was retained, rather than dropped and replaced by a fake ACK.
            ack.send(Err(Error::State(
                "fixture original recording rejection".into(),
            )))
            .map_err(|_| Error::State("original recording future was dropped".into()))?;
            let outcome = task
                .await
                .map_err(|_| Error::State("ownership fixture task failed".into()))??;
            assert!(outcome.operation.is_ok());
            assert!(matches!(outcome.cleanup, Err(Error::State(_))));
            assert!(matches!(outcome.recording, Err(Error::State(_))));
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("retained recording/cleanup ordering fixture".into()))?
    }

    #[tokio::test]
    async fn all_close_tasks_start_while_a_sibling_record_ack_is_delayed() -> Result<()> {
        let (first_started, first) = tokio::sync::oneshot::channel();
        let (second_started, second) = tokio::sync::oneshot::channel();
        let (ack, receipt) = tokio::sync::oneshot::channel();
        let mut closes = JoinSet::new();
        closes.spawn(async move {
            let _ = first_started.send(());
            let recording = receipt
                .await
                .map_err(|_| Error::State("first recording abandoned".into()));
            JobOutcome {
                operation: Ok(()),
                cleanup: Ok(()),
                recording,
                final_event: None,
            }
        });
        closes.spawn(async move {
            let _ = second_started.send(());
            JobOutcome {
                operation: Ok(()),
                cleanup: Err(Error::State("second actual close failed".into())),
                recording: Ok(()),
                final_event: None,
            }
        });
        let joining = tokio::spawn(join_closes(closes));
        tokio::time::timeout(Duration::from_secs(2), async {
            first
                .await
                .map_err(|_| Error::State("first close never started".into()))?;
            second
                .await
                .map_err(|_| Error::State("second close waited on first recording".into()))?;
            assert!(!joining.is_finished());
            ack.send(())
                .map_err(|_| Error::State("first close was aborted".into()))?;
            let outcome = joining
                .await
                .map_err(|_| Error::State("close join fixture failed".into()))?;
            assert!(outcome.operation.is_ok());
            assert!(outcome.cleanup.is_err());
            assert!(outcome.recording.is_ok());
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("concurrent close fixture".into()))?
    }

    #[tokio::test]
    async fn vanished_foreground_stop_sender_skips_the_real_helper() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let (_frontend, connection) = crate::foreground::channel();
        let (stop, receiver) = watch::channel(false);
        drop(stop);
        let context = RunContext::foreground(crate::foreground::ForegroundOptions {
            helper_socket: directory.path().join("must-not-be-opened.sock"),
            stop: receiver,
            connection,
        });
        let key = DeviceKey::native(&format!("usb-{}", "a".repeat(64)))?;
        match prepare(&key, &context).await {
            Ok(None) => Ok(()),
            Ok(Some(export)) => {
                export.close().await.into_result()?;
                Err(Error::State("vanished caller unexpectedly exported".into()))
            }
            Err(outcome) => {
                outcome.into_result()?;
                Err(Error::State(
                    "vanished caller was not a successful skip".into(),
                ))
            }
        }
    }

    #[tokio::test]
    async fn stopped_foreground_prepare_skips_the_real_helper_and_creates_no_export() -> Result<()>
    {
        let directory = tempfile::tempdir()?;
        let helper_socket = directory.path().join("must-not-be-opened.sock");
        let (_frontend, connection) = crate::foreground::channel();
        let (_stop, receiver) = watch::channel(true);
        let context = RunContext::foreground(crate::foreground::ForegroundOptions {
            helper_socket,
            stop: receiver,
            connection,
        });
        let key = DeviceKey::native(&format!("usb-{}", "a".repeat(64)))?;
        // The missing helper path makes an accidental real prepare fail. This
        // drives the actual shared preparation owner, not a mirrored state branch.
        match prepare(&key, &context).await {
            Ok(None) => Ok(()),
            Ok(Some(export)) => {
                export.close().await.into_result()?;
                Err(Error::State(
                    "stopped foreground unexpectedly exported a device".into(),
                ))
            }
            Err(outcome) => {
                outcome.into_result()?;
                Err(Error::State(
                    "stopped foreground was not a successful skip".into(),
                ))
            }
        }
    }

    async fn pump<A, B>(
        network: A,
        kernel: B,
        stop: watch::Receiver<bool>,
        stub: watch::Receiver<bool>,
        export: watch::Receiver<bool>,
    ) -> Result<()>
    where
        A: tokio::io::AsyncRead + AsyncWrite + Unpin,
        B: tokio::io::AsyncRead + AsyncWrite + Unpin,
    {
        let (_local, local) = watch::channel(false);
        let (_export_stop, export_stop) = watch::channel(false);
        let forwarding = async {
            io::relay(network, kernel, Counters::new())
                .await
                .map_err(StageError::Operation)
        };
        let mut stops = RelayStops {
            job: stop,
            local,
            export: export_stop,
            stub,
            source: export,
            link: None,
            link_ended: None,
        };
        let result = match stops.wait(forwarding).await {
            Ok(Some(result)) => result,
            Ok(None) => Ok(()),
            Err(error) => Err(error),
        };
        stage_outcome(result).into_result()
    }

    // These in-memory stream tests cover the broker's relay ownership only;
    // they do not claim helper, sysfs, kernel or physical-device validation.
    #[tokio::test]
    async fn relay_copies_both_directions_and_first_eof_releases_other_half() -> Result<()> {
        let (network, mut remote) = tokio::io::duplex(64);
        let (relay, mut kernel) = tokio::io::duplex(64);
        let (_stop, stop) = watch::channel(false);
        let (_stub, stub) = watch::channel(false);
        let (_export, export) = watch::channel(false);
        let task = tokio::spawn(pump(network, relay, stop, stub, export));
        tokio::time::timeout(Duration::from_secs(2), async {
            remote.write_all(b"request").await?;
            let mut request = [0u8; 7];
            kernel.read_exact(&mut request).await?;
            assert_eq!(&request, b"request");
            kernel.write_all(b"reply").await?;
            let mut reply = [0u8; 5];
            remote.read_exact(&mut reply).await?;
            assert_eq!(&reply, b"reply");
            drop(remote);
            task.await
                .map_err(|_| Error::State("relay test task failed".into()))??;
            let mut tail = [0u8; 1];
            assert_eq!(kernel.read(&mut tail).await?, 0);
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("relay test".into()))?
    }

    #[tokio::test]
    async fn shutdown_releases_both_streams_even_without_peer_eof() -> Result<()> {
        let (network, mut remote) = tokio::io::duplex(64);
        let (relay, mut kernel) = tokio::io::duplex(1);
        let (shutdown, stop) = watch::channel(false);
        let (_stub, stub) = watch::channel(false);
        let (_export, export) = watch::channel(false);
        let task = tokio::spawn(pump(network, relay, stop, stub, export));
        remote
            .write_all(b"larger than the unread kernel buffer")
            .await?;
        shutdown.send_replace(true);
        tokio::time::timeout(Duration::from_secs(2), async {
            task.await
                .map_err(|_| Error::State("relay test task failed".into()))??;
            let mut bytes = Vec::new();
            // A prefix may have reached the bounded relay before shutdown.
            kernel.read_to_end(&mut bytes).await?;
            assert!(b"larger than the unread kernel buffer".starts_with(&bytes));
            let mut tail = [0u8; 1];
            assert_eq!(remote.read(&mut tail).await?, 0);
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("relay shutdown test".into()))?
    }

    #[tokio::test]
    async fn export_invalidation_ends_idle_relay_with_error() -> Result<()> {
        let (network, mut remote) = tokio::io::duplex(64);
        let (relay, mut kernel) = tokio::io::duplex(64);
        let (_stop, stop) = watch::channel(false);
        let (_stub, stub) = watch::channel(false);
        let (invalidate, export) = watch::channel(false);
        let task = tokio::spawn(pump(network, relay, stop, stub, export));
        invalidate.send_replace(true);
        tokio::time::timeout(Duration::from_secs(2), async {
            let result = task
                .await
                .map_err(|_| Error::State("relay test task failed".into()))?;
            assert!(matches!(result, Err(Error::State(_))));
            let mut tail = [0u8; 1];
            assert_eq!(remote.read(&mut tail).await?, 0);
            assert_eq!(kernel.read(&mut tail).await?, 0);
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("relay invalidation test".into()))?
    }
}

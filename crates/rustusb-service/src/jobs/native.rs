//! Native TLS owners use only helper-held kernel endpoints; no plain listener.

use super::{
    io::{self, Counters},
    link, recording,
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
        atomic::{AtomicBool, AtomicU8, Ordering},
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

#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(u8)]
enum CloseCause {
    Open,
    Job,
    Local,
    Requested,
    Invalidated,
    Failure,
}

// This private result cannot be confused with a cleanup proof or public state.
enum PrepareOutcome {
    Ready(Arc<Export>),
    LocalCancelled { unresolved: Option<Error> },
    JobStopped,
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
    close_cause: AtomicU8,
    pending_record: Mutex<Option<recording::Pending>>,
    prior_failure: Mutex<Option<Failure>>,
    // Only the received-owner software fixture supplies a Unix control receipt.
    // Production always owns the root-validated NativeExportHandle above.
    #[cfg(test)]
    fixture_close: Mutex<Option<Recording>>,
}
impl Export {
    fn accepting(&self) -> bool {
        !stop_requested(&self.session.stop)
            && self.close_cause.load(Ordering::Acquire) == CloseCause::Open as u8
            && !stop_requested(&self.stop.subscribe())
            && !stop_requested(&self.invalidated)
            && !self.reserved.load(Ordering::Acquire)
            && !self.failed.load(Ordering::Acquire)
    }
    fn unexpected_invalidation(&self) -> bool {
        let cause = self.close_cause.load(Ordering::Acquire);
        cause == CloseCause::Invalidated as u8
            || cause == CloseCause::Failure as u8
            || (cause == CloseCause::Open as u8
                && stop_requested(&self.invalidated)
                && !stop_requested(&self.session.stop)
                && !stop_requested(&self.stop.subscribe()))
    }
    async fn actual_close(&self) -> Result<()> {
        // Only the collection's one watcher may take the real capability.
        self.stop.send_replace(true);
        let using = self.use_lock.lock().await;
        let handle = self.handle.lock().await.take();
        cleanup_then_unlock(
            async {
                #[cfg(test)]
                if let Some(close) = self.fixture_close.lock().await.take() {
                    return close.await;
                }
                match handle {
                    Some(handle) => handle.close().await,
                    None => Err(Error::State("Native export close repeated".into())),
                }
            },
            Some(using),
            &self.failed,
        )
        .await
    }

    async fn retain_record(&self, pending: recording::Pending) -> Result<()> {
        let mut slot = self.pending_record.lock().await;
        if let Some(previous) = slot.take() {
            // Even an invariant failure cannot discard an accepted transaction.
            *slot = Some(Box::pin(async move {
                let mut result = previous.await;
                merge_result(&mut result, pending.await);
                result
            }));
            return Err(Error::State(
                "Native preparation already has a retained record".into(),
            ));
        }
        *slot = Some(pending);
        Ok(())
    }

    async fn remember_failure(&self, error: &Error) {
        let mut failure = self.prior_failure.lock().await;
        if failure.is_none() {
            *failure = Some(Failure::from_error(error));
        }
    }

    async fn finalize(&self, cleanup: Result<()>, lost: bool) -> JobOutcome {
        // Every real close has already been started/joined before batch
        // finalization. The exact original transaction precedes Closing.
        let pending = self.pending_record.lock().await.take();
        let mut recording = match pending {
            Some(pending) => pending.await,
            None => Ok(()),
        };
        let operation = if lost {
            Err(Error::State("Native export invalidated".into()))
        } else {
            Ok(())
        };
        let observed = self
            .session
            .observe(SessionState::Disconnecting, Evidence::Closing, None)
            .await;
        let recorded_failure = *self.prior_failure.lock().await;
        let failure = recorded_failure
            .or_else(|| operation.as_ref().err().map(Failure::from_error))
            .or_else(|| recording.as_ref().err().map(Failure::from_error))
            .or_else(|| observed.as_ref().err().map(Failure::from_error));
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
        merge_result(&mut recording, observed);
        merge_result(&mut recording, recorded);
        JobOutcome {
            operation,
            cleanup,
            recording,
            final_event: None,
        }
    }
}

struct ClosedExport {
    export: Arc<Export>,
    cleanup: Result<()>,
    lost: bool,
}
impl ClosedExport {
    async fn finalize(self) -> JobOutcome {
        self.export.finalize(self.cleanup, self.lost).await
    }
}

struct Preparation {
    exports: Vec<Arc<Export>>,
    closes: JoinSet<ClosedExport>,
    fatal: watch::Sender<bool>,
    local_unresolved: Vec<Error>,
}
impl Default for Preparation {
    fn default() -> Self {
        let (fatal, _) = watch::channel(false);
        Self {
            exports: Vec::new(),
            closes: JoinSet::new(),
            fatal,
            local_unresolved: Vec::new(),
        }
    }
}
impl Preparation {
    fn register(&mut self, export: Arc<Export>, mut job_stop: watch::Receiver<bool>) {
        // Install before any Resource wait. This remains polled independently
        // while another export is waiting for Begin/Attempt/Available storage.
        self.exports.push(export.clone());
        let fatal = self.fatal.clone();
        self.closes.spawn(async move {
            let mut local_stop = export.session.stop.clone();
            let mut requested = export.stop.subscribe();
            let mut invalidated = export.invalidated.clone();
            let mut failure = fatal.subscribe();
            let cause = tokio::select! {
                biased;
                _ = stopped(&mut job_stop) => CloseCause::Job,
                _ = stopped(&mut local_stop) => CloseCause::Local,
                _ = stopped(&mut failure) => CloseCause::Failure,
                _ = stopped(&mut requested) => CloseCause::Requested,
                _ = stopped(&mut invalidated) => CloseCause::Invalidated,
            };
            export.close_cause.store(cause as u8, Ordering::Release);
            let cleanup = export.actual_close().await;
            if cleanup.is_err() || cause == CloseCause::Invalidated {
                // Real resource failure closes siblings even while the parent
                // is waiting for a different metadata transaction.
                fatal.send_replace(true);
            }
            ClosedExport {
                export,
                cleanup,
                lost: cause == CloseCause::Invalidated,
            }
        });
    }
    fn stopping(&self) -> bool {
        *self.fatal.borrow()
            || self.exports.iter().any(|export| {
                export.failed.load(Ordering::Acquire) || export.unexpected_invalidation()
            })
    }
    fn retain_local(&mut self, unresolved: Option<Error>) -> Result<()> {
        if let Some(error) = unresolved {
            if self.local_unresolved.len() >= 16 {
                return Err(Error::State(
                    "Native local cancellation budget exceeded".into(),
                ));
            }
            self.local_unresolved.push(error);
        }
        Ok(())
    }
    fn finish_local(&mut self, outcome: &mut JobOutcome) {
        // Metadata-only uncertainty is reported after genuine whole-job stop
        // and all actual cleanup. It is never authority to stop a safe sibling.
        for error in self.local_unresolved.drain(..) {
            merge_result(&mut outcome.cleanup, Err(error));
        }
    }

    fn request_stop(&self) {
        for export in &self.exports {
            export.stop.send_replace(true);
        }
    }
    async fn collect_actual(&mut self) -> (Vec<ClosedExport>, Result<()>) {
        let mut completed = Vec::new();
        let mut result = Ok(());
        while let Some(closed) = self.closes.join_next().await {
            match closed {
                Ok(closed) => completed.push(closed),
                Err(_) => merge_result(
                    &mut result,
                    Err(Error::State(
                        "Native actual-close task failed; cleanup unconfirmed".into(),
                    )),
                ),
            }
        }
        (completed, result)
    }
}

async fn preparation_stop(export: &Export, mut job: watch::Receiver<bool>) -> Result<()> {
    let mut local = export.session.stop.clone();
    let mut requested = export.stop.subscribe();
    let mut source = export.invalidated.clone();
    tokio::select! {
        biased;
        _ = stopped(&mut job) => Ok(()),
        _ = stopped(&mut local) => Ok(()),
        _ = stopped(&mut requested) => Ok(()),
        _ = stopped(&mut source) => Err(Error::State("Native export invalidated during preparation".into())),
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
    preparation: &mut Preparation,
) -> std::result::Result<PrepareOutcome, JobOutcome> {
    let session = context
        .begin_session(SessionRole::Export, key.clone(), None)
        .await
        .map_err(|error| stage_outcome(Err(StageError::Recording(error))))?;
    if stop_requested(&context.stop) || stop_requested(&session.stop) {
        return cancel_unrequested(&session, &context.stop, false).await;
    }
    if let Err(error) = session
        .observe(SessionState::Exporting, Evidence::Intent, None)
        .await
    {
        preparation.request_stop();
        let mut outcome = stage_outcome(Err(StageError::Recording(error)));
        let recorded = session
            .finish(
                outcome.recording.as_ref().err().map(Failure::from_error),
                Cleanup::NotStarted,
                Metrics::default(),
            )
            .await;
        merge_result(&mut outcome.recording, recorded);
        return Err(outcome);
    }
    if stop_requested(&context.stop) || stop_requested(&session.stop) {
        return cancel_unrequested(&session, &context.stop, false).await;
    }
    let correlation = match session.attempt(BrokerResourceKind::NativeExport).await {
        Ok(correlation) => correlation,
        Err(error) => {
            preparation.request_stop();
            let mut outcome = stage_outcome(Err(StageError::Recording(error)));
            let recorded = session
                .finish(
                    outcome.recording.as_ref().err().map(Failure::from_error),
                    Cleanup::NotStarted,
                    Metrics::default(),
                )
                .await;
            merge_result(&mut outcome.recording, recorded);
            return Err(outcome);
        }
    };
    if stop_requested(&context.stop) || stop_requested(&session.stop) {
        return cancel_unrequested(&session, &context.stop, correlation.is_some()).await;
    }
    let id = key.fingerprint().ok_or_else(|| {
        JobOutcome::before_start(Error::InvalidInput(
            "Native device fingerprint required".into(),
        ))
    })?;
    // This possibly accepted helper exchange is retained to its actual result.
    let acquired = match correlation.clone() {
        Some(correlation) => {
            prepare_native_export_tracked(&context.helper_socket, id.to_owned(), correlation).await
        }
        None => prepare_native_export(&context.helper_socket, id.to_owned()).await,
    };
    let handle = match acquired {
        Ok(handle) => handle,
        Err(error) => {
            preparation.request_stop();
            let mut outcome = JobOutcome::before_start(error);
            outcome.cleanup = Err(Error::State("Native export prepare unconfirmed".into()));
            let recorded = session
                .finish(
                    outcome.operation.as_ref().err().map(Failure::from_error),
                    Cleanup::Unconfirmed,
                    Metrics::default(),
                )
                .await;
            merge_result(&mut outcome.recording, recorded);
            return Err(outcome);
        }
    };
    let received_key = handle.device_key().clone();
    let lease_id = handle.lease_id().to_owned();
    let published = handle.published_device().clone();
    let invalidated = handle.invalidated();
    let (stop, _) = watch::channel(false);
    let export = Arc::new(Export {
        key: key.clone(),
        published,
        handle: Mutex::new(Some(handle)),
        session,
        invalidated,
        stop,
        use_lock: Mutex::new(()),
        reserved: AtomicBool::new(false),
        failed: AtomicBool::new(false),
        close_cause: AtomicU8::new(CloseCause::Open as u8),
        pending_record: Mutex::new(None),
        prior_failure: Mutex::new(None),
        #[cfg(test)]
        fixture_close: Mutex::new(None),
    });
    preparation.register(export.clone(), context.stop.clone());
    finish_received_export(export, received_key, lease_id, correlation, &context.stop).await
}

async fn cancel_unrequested(
    session: &RunSession,
    job: &watch::Receiver<bool>,
    reserved: bool,
) -> std::result::Result<PrepareOutcome, JobOutcome> {
    let cleanup = if reserved {
        Cleanup::Unconfirmed
    } else {
        Cleanup::NotStarted
    };
    let recorded = session
        .finish(Some(Failure::Cancelled), cleanup, Metrics::default())
        .await;
    if let Err(error) = recorded {
        return Err(stage_outcome(Err(StageError::Recording(error))));
    }
    let unresolved = reserved.then(|| Error::State("Native reserved export attempt was cancelled before helper request; cleanup unconfirmed".into()));
    if stop_requested(job) {
        if let Some(error) = unresolved {
            let mut outcome = success();
            outcome.cleanup = Err(error);
            return Err(outcome);
        }
        Ok(PrepareOutcome::JobStopped)
    } else {
        Ok(PrepareOutcome::LocalCancelled { unresolved })
    }
}

// The actual owner enters here immediately after the helper has returned it.
// Kept separate so ordinary-user fixtures can exercise this production phase
// without bypassing the helper client's root credential/path checks.
async fn finish_received_export(
    export: Arc<Export>,
    received_key: DeviceKey,
    lease_id: String,
    correlation: Option<rustusb_core::application::BrokerCorrelation>,
    stop: &watch::Receiver<bool>,
) -> std::result::Result<PrepareOutcome, JobOutcome> {
    let mut outcome = success();
    // Known invalid identity never depends on which select branch wins.
    if received_key != export.key {
        let error = Error::Protocol("Native export identity differs".into());
        export.remember_failure(&error).await;
        export.stop.send_replace(true);
        outcome.operation = Err(error);
    }
    let reporter = export.session.clone();
    let recorded = recording::wait(
        async move { reporter.resource(correlation, &lease_id).await },
        preparation_stop(&export, stop.clone()),
    )
    .await;
    let mut interrupted = false;
    match recorded {
        recording::Wait::Ready(Err(error)) => {
            export.remember_failure(&error).await;
            outcome.recording = Err(error);
        }
        recording::Wait::Ready(Ok(())) => {}
        recording::Wait::Interrupted { pending, reason } => {
            interrupted = true;
            merge_result(&mut outcome.recording, export.retain_record(pending).await);
            merge_result(&mut outcome.operation, reason);
        }
    }
    if outcome.operation.is_err() || outcome.recording.is_err() {
        return Err(outcome);
    }
    if stop_requested(stop) {
        return Ok(PrepareOutcome::JobStopped);
    }
    if export.unexpected_invalidation() || export.failed.load(Ordering::Acquire) {
        return Err(JobOutcome::before_start(Error::State(
            "Native export failed during preparation".into(),
        )));
    }
    if stop_requested(&export.session.stop) {
        return Ok(PrepareOutcome::LocalCancelled { unresolved: None });
    }
    if interrupted || stop_requested(&export.stop.subscribe()) {
        return Ok(PrepareOutcome::JobStopped);
    }
    Ok(PrepareOutcome::Ready(export))
}

async fn record_available(
    export: &Arc<Export>,
    stop: watch::Receiver<bool>,
) -> std::result::Result<(), JobOutcome> {
    if stop_requested(&stop) {
        return Err(success());
    }
    if export.unexpected_invalidation() || export.failed.load(Ordering::Acquire) {
        return Err(JobOutcome::before_start(Error::State(
            "Native export failed before ready".into(),
        )));
    }
    if stop_requested(&export.session.stop) {
        return Ok(());
    }
    if stop_requested(&export.stop.subscribe()) {
        return Err(success());
    }
    let reporter = export.session.clone();
    match recording::wait(
        async move {
            reporter
                .observe(SessionState::Available, Evidence::ExportReady, None)
                .await
        },
        preparation_stop(export, stop.clone()),
    )
    .await
    {
        recording::Wait::Ready(Ok(())) => Ok(()),
        recording::Wait::Ready(Err(error)) => {
            export.remember_failure(&error).await;
            Err(stage_outcome(Err(StageError::Recording(error))))
        }
        recording::Wait::Interrupted { pending, reason } => {
            let mut outcome = stage_outcome(reason.map_err(StageError::Operation));
            merge_result(&mut outcome.recording, export.retain_record(pending).await);
            if outcome.operation.is_err() || outcome.recording.is_err() {
                return Err(outcome);
            }
            if !stop_requested(&stop)
                && stop_requested(&export.session.stop)
                && !export.unexpected_invalidation()
                && !export.failed.load(Ordering::Acquire)
            {
                Ok(())
            } else {
                Err(outcome)
            }
        }
    }
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
    let mut preparation = Preparation::default();
    let mut preparation_interrupted = false;
    let mut outcome = success();
    let mut buses = HashSet::new();
    for key in &config.devices {
        if stop_requested(&context.stop) || preparation.stopping() {
            preparation_interrupted = true;
            break;
        }
        match prepare(key, &context, &mut preparation).await {
            Ok(PrepareOutcome::Ready(export)) => {
                let distinct = buses.insert(export.published.bus_id.clone());
                if !distinct {
                    outcome.operation =
                        Err(Error::Protocol("duplicate Native publication bus".into()));
                    break;
                }
            }
            Ok(PrepareOutcome::LocalCancelled { unresolved }) => {
                if let Err(error) = preparation.retain_local(unresolved) {
                    outcome.cleanup = Err(error);
                    preparation_interrupted = true;
                    break;
                }
            }
            Ok(PrepareOutcome::JobStopped) => {
                preparation_interrupted = true;
                break;
            }
            Err(result) => {
                preparation_interrupted = true;
                merge(&mut outcome, result, true);
                break;
            }
        }
    }
    if outcome.operation.is_ok() && outcome.cleanup.is_ok() && outcome.recording.is_ok() {
        outcome.operation = server::devlist_reply(
            &preparation
                .exports
                .iter()
                .map(|export| export.published.clone())
                .collect::<Vec<_>>(),
        )
        .map(|_| ());
    }
    if outcome.operation.is_err()
        || outcome.cleanup.is_err()
        || outcome.recording.is_err()
        || preparation_interrupted
        || preparation.stopping()
        || stop_requested(&context.stop)
    {
        merge(&mut outcome, close_all(&mut preparation).await, true);
        return outcome;
    }
    // A stop never drops a helper prepare or listener acquisition future.
    let listener = match TcpListener::bind(config.listen).await {
        Ok(listener) => listener,
        Err(error) => {
            outcome.operation = Err(error.into());
            merge(&mut outcome, close_all(&mut preparation).await, true);
            return outcome;
        }
    };
    for export in &preparation.exports {
        if let Err(result) = record_available(export, context.stop.clone()).await {
            preparation_interrupted = true;
            merge(&mut outcome, result, true);
            break;
        }
    }
    if outcome.recording.is_ok()
        && !preparation_interrupted
        && !preparation.stopping()
        && !stop_requested(&context.stop)
    {
        outcome.operation = async {
            context
                .emit(Event::Ready {
                    address: listener.local_addr()?,
                    secure: true,
                    snapshot: snapshot(&preparation.exports),
                })
                .await
        }
        .await;
    }
    if outcome.operation.is_err()
        || outcome.recording.is_err()
        || preparation_interrupted
        || preparation.stopping()
        || stop_requested(&context.stop)
    {
        drop(listener);
        merge(&mut outcome, close_all(&mut preparation).await, true);
        return outcome;
    }
    let mut tasks = JoinSet::new();
    let (stop_all, stop) = watch::channel(false);
    let budget = Arc::new(Semaphore::new(16));
    let mut job_stop = context.stop.clone();
    let operation = loop {
        tokio::select! {
            biased;
            _ = stopped(&mut job_stop) => break Ok(()),
            closed = preparation.closes.join_next(), if !preparation.closes.is_empty() => {
                match closed {
                    Some(Ok(closed)) => {
                        let failed = closed.cleanup.is_err() || closed.lost;
                        tasks.spawn(async move { Task::Export(closed.finalize().await) });
                        if failed { break Ok(()); }
                    },
                    Some(Err(_)) => {
                        merge_result(&mut outcome.cleanup, Err(Error::State("Native actual-close task failed; cleanup unconfirmed".into())));
                        break Err(Error::State("Native actual-close task failed".into()));
                    },
                    None => {},
                }
            },
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
                    Control::Snapshot { id } => Event::Snapshot { id, snapshot: snapshot(&preparation.exports) },
                    Control::ServerSecurity { id } => Event::ServerSecurity {
                        id, snapshot: config.tls.security_monitor().snapshot(),
                    },
                    Control::ServerAdmission { id } => Event::ServerAdmission {
                        id, snapshot: config.tls.admission_monitor().snapshot(),
                    },
                    _ => Event::Error { id: None, code: ControlError::InvalidControl },
                };
                if let Err(error) = context.emit(event).await { break Err(error); }
            },
            accepted = listener.accept() => {
                let (network, _) = match accepted { Ok(value) => value, Err(error) => break Err(error.into()) };
                let Ok(permit) = budget.clone().try_acquire_owned() else {
                    config.tls.security_monitor().listener_connection_limit();
                    continue;
                };
                let config = config.clone();
                let registry = preparation.exports.clone();
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
    preparation.request_stop();
    // Stub close releases its use lock before WAL. Join every actual export
    // close before awaiting any remaining connection/final-record receipts.
    let (closed, actual_result) = preparation.collect_actual().await;
    merge_result(&mut outcome.cleanup, actual_result);
    for export in closed {
        tasks.spawn(async move { Task::Export(export.finalize().await) });
    }
    // Every remaining task now owns only real cleanup already in progress or
    // metadata finalization. Never abort a retained helper/record transaction.
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
    if preparation
        .exports
        .iter()
        .any(|export| export.failed.load(Ordering::Acquire))
    {
        merge_result(
            &mut outcome.cleanup,
            Err(Error::State("Native owned cleanup unconfirmed".into())),
        );
    }
    preparation.finish_local(&mut outcome);
    if outcome.operation.is_ok() && outcome.cleanup.is_ok() && outcome.recording.is_ok() {
        outcome.final_event = Some(Event::NativeStopped {
            exports_closed: preparation.exports.len(),
        });
    }
    outcome
}

async fn close_all(preparation: &mut Preparation) -> JobOutcome {
    preparation.request_stop();
    let (completed, actual_result) = preparation.collect_actual().await;
    let mut tasks = JoinSet::new();
    for closed in completed {
        tasks.spawn(closed.finalize());
    }
    let mut outcome = join_closes(tasks).await;
    merge_result(&mut outcome.cleanup, actual_result);
    preparation.finish_local(&mut outcome);
    outcome
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
                    && !stop_requested(&export.session.stop)
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
    if let Err(error) = closed
        && outcome.operation.is_ok()
    {
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
        link::complete_management(&mut stream, link_owner, async {
            tokio::select! {
                biased;
                _ = stopped(&mut job_stop) => Ok(()),
                error = link::ended(&mut link_ended, link_monitor.as_ref()) => Err(error),
            }
        })
        .await?;
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
                    if let Some(stream) = network.as_mut() {
                        let mut local = local_stop.clone();
                        let mut exported = export_stop.clone();
                        let closed = link::complete_management(stream, link_owner, async {
                            tokio::select! {
                                biased;
                                _ = stopped(&mut job_stop) => Ok(()),
                                _ = stopped(&mut local) => Ok(()),
                                _ = stopped(&mut exported) => Ok(()),
                                failure = link::ended(&mut link_ended, link_monitor.as_ref()) => Err(failure),
                            }
                        }).await;
                        if let Err(closed) = closed {
                            return Err(Error::State(format!("Native stub prepare failed ({error}); framed IMPORT failure response close failed ({closed})")).into());
                        }
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
#[path = "native_prepare_tests.rs"]
mod prepare_tests;

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncReadExt;

    #[tokio::test]
    async fn received_export_stop_closes_before_original_resource_ack() -> Result<()> {
        // Production received-owner phase plus real application messages. The
        // Unix peer below is only a software cleanup receipt, not a trusted
        // helper, root-credential test, kernel export or driver restoration.
        use crate::{
            context::{JobMessage, SessionContext},
            health::HealthReporter,
        };
        use rustusb_core::application::{AttemptId, BrokerCorrelation, InstanceId, SessionId};
        let correlation = BrokerCorrelation {
            instance_id: InstanceId::new("00000000-0000-0000-0000-000000000001")?,
            session_id: SessionId::new("00000000-0000-0000-0000-000000000002")?,
            attempt_id: AttemptId::new("00000000-0000-0000-0000-000000000003")?,
        };
        let key = DeviceKey::native(&format!("usb-{}", "a".repeat(64)))?;
        let (facts, mut messages) = tokio::sync::mpsc::channel(8);
        let (stop, receiver) = watch::channel(false);
        let (invalidated, invalidation) = watch::channel(false);
        let (export_stop, _) = watch::channel(false);
        let (mut owned_control, mut peer) = tokio::net::UnixStream::pair()?;
        let export = Arc::new(Export {
            key: key.clone(),
            published: super::super::management_tests::device()?,
            handle: Mutex::new(None),
            session: RunSession::from(SessionContext {
                health: HealthReporter::default(),
                id: correlation.session_id.clone(),
                stop: receiver.clone(),
                facts,
            }),
            invalidated: invalidation,
            stop: export_stop,
            use_lock: Mutex::new(()),
            reserved: AtomicBool::new(false),
            failed: AtomicBool::new(false),
            close_cause: AtomicU8::new(CloseCause::Open as u8),
            pending_record: Mutex::new(None),
            prior_failure: Mutex::new(None),
            fixture_close: Mutex::new(Some(Box::pin(async move {
                owned_control.write_all(b"close").await?;
                let mut response = [0; 2];
                owned_control.read_exact(&mut response).await?;
                if response != *b"ok" {
                    return Err(Error::Protocol("fixture cleanup receipt".into()));
                }
                Ok(())
            }))),
        });
        let lease = "00000000-0000-0000-0000-000000000004".to_owned();
        let task = tokio::spawn(async move {
            let mut preparation = Preparation::default();
            preparation.register(export.clone(), receiver.clone());
            match finish_received_export(export, key, lease, Some(correlation), &receiver).await {
                Err(mut outcome) => {
                    merge(&mut outcome, close_all(&mut preparation).await, true);
                    Err(outcome)
                }
                Ok(PrepareOutcome::JobStopped) => {
                    let mut outcome = success();
                    merge(&mut outcome, close_all(&mut preparation).await, true);
                    Err(outcome)
                }
                Ok(value) => {
                    let _ = close_all(&mut preparation).await;
                    Ok(value)
                }
            }
        });
        let resource = messages
            .recv()
            .await
            .ok_or_else(|| Error::State("Resource request absent".into()))?;
        let JobMessage::Resource { reply, .. } = resource else {
            return Err(Error::State(
                "first received-owner record was not Resource".into(),
            ));
        };
        stop.send_replace(true);
        let mut close = [0; 5];
        let released_before_ack =
            tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut close))
                .await
                .is_ok();
        // Always release the original gate and collect the owner, including on
        // the old failing path. A successful send also proves its ACK survives.
        reply
            .send(Err(Error::State(
                "fixture original Resource rejection".into(),
            )))
            .map_err(|_| Error::State("original Resource ACK was abandoned".into()))?;
        if !released_before_ack {
            tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut close))
                .await
                .map_err(|_| Error::Timeout("fixture cleanup after gate release".into()))??;
        }
        assert_eq!(&close, b"close");
        peer.write_all(b"ok").await?;
        let closing = messages
            .recv()
            .await
            .ok_or_else(|| Error::State("Closing record absent".into()))?;
        let JobMessage::Observe {
            state,
            evidence,
            reply,
            ..
        } = closing
        else {
            return Err(Error::State("Closing record order".into()));
        };
        assert_eq!(state, SessionState::Disconnecting);
        assert_eq!(evidence, Evidence::Closing);
        let _ = reply.send(Ok(()));
        let finish = messages
            .recv()
            .await
            .ok_or_else(|| Error::State("Finish record absent".into()))?;
        let JobMessage::Finish { cleanup, reply, .. } = finish else {
            return Err(Error::State("Finish record order".into()));
        };
        assert!(matches!(cleanup, Cleanup::Verified));
        let _ = reply.send(Ok(()));
        let result = task
            .await
            .map_err(|_| Error::State("received-owner task failed".into()))?;
        let outcome = match result {
            Err(outcome) => outcome,
            Ok(_) => {
                return Err(Error::State(
                    "stopped preparation remained available".into(),
                ));
            }
        };
        assert!(outcome.operation.is_ok());
        assert!(outcome.cleanup.is_ok());
        assert!(
            matches!(outcome.recording, Err(Error::State(ref message)) if message.contains("fixture original Resource rejection"))
        );
        drop(invalidated);
        assert!(
            released_before_ack,
            "actual received-owner cleanup was blocked by the original Resource ACK"
        );
        Ok(())
    }

    #[tokio::test]
    async fn actual_native_catalog_and_short_refusal_finish_on_the_same_tls_owner()
    -> super::super::management_tests::TestResult {
        use super::super::management_tests::{credentials, device, foreground_context, loaded};
        tokio::time::timeout(Duration::from_secs(15), async {
            for framed in [false, true] {
                let (tls, client) = credentials(framed)?;
                let listener = TcpListener::bind("127.0.0.1:0").await?;
                let config = loaded(client, listener.local_addr()?)?;
                let directory = tempfile::tempdir()?;
                let helper = directory.path().join("missing-helper.sock");
                let observed_helper = helper.clone();
                let serving = tokio::spawn(async move {
                    for _ in 0..3 {
                        let (network, _) = listener.accept().await?;
                        let (context, _stop, _frontend) = foreground_context(helper.clone());
                        let stop = context.stop.clone();
                        connection(network, tls.clone(), Vec::new(), context, stop)
                            .await
                            .into_result()?;
                    }
                    Ok::<_, Error>(())
                });
                for _ in 0..2 {
                    assert!(
                        super::super::discover(&config, Duration::from_secs(5))
                            .await?
                            .is_empty()
                    );
                }
                let mut connection = link::connect(&config).await?;
                let selected = secure::CatalogDevice {
                    device_key: config.device.clone(),
                    device: device()?,
                };
                assert!(matches!(
                    secure::import_device(&mut connection.io, &selected).await,
                    Err(Error::Protocol(_))
                ));
                assert!(
                    link::complete_management(
                        &mut connection.io,
                        &mut connection.owner,
                        std::future::pending()
                    )
                    .await?
                );
                serving.await??;
                assert!(!observed_helper.exists());
            }
            Ok::<_, Box<dyn std::error::Error + Send + Sync>>(())
        })
        .await??;
        Ok(())
    }

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
        let mut preparation = Preparation::default();
        match prepare(&key, &context, &mut preparation).await {
            Ok(PrepareOutcome::JobStopped) => Ok(()),
            Ok(_) => {
                close_all(&mut preparation).await.into_result()?;
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
        let mut preparation = Preparation::default();
        match prepare(&key, &context, &mut preparation).await {
            Ok(PrepareOutcome::JobStopped) => Ok(()),
            Ok(_) => {
                close_all(&mut preparation).await.into_result()?;
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

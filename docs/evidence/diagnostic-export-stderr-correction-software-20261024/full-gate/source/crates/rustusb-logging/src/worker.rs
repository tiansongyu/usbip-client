//! Bounded record admission and explicitly owned blocking I/O workers.

use crate::{
    AuditProjection, Cursor, Diagnostic, Error, Event, EventCode, FailureCategory, Field, Level,
    LogId, LogPage, LogRecord, LogStatus, Options, QUEUE_CAPACITY, ReadFilter, Result, Scalar,
    ShutdownReport, record, storage,
};
use std::{
    collections::BTreeMap,
    fmt,
    sync::{
        Arc, Mutex, Weak,
        atomic::{AtomicBool, AtomicU8, AtomicU64, Ordering},
        mpsc::{self, Receiver, SyncSender, TrySendError},
    },
    thread::{self, JoinHandle},
    time::{Instant, SystemTime, UNIX_EPOCH},
};
use tokio::sync::oneshot;
use tracing::{Event as TracingEvent, Subscriber, field::Visit};
use tracing_subscriber::{EnvFilter, Layer, layer::Context, prelude::*};

enum WriteRequest {
    Record(LogRecord),
    Shutdown,
}

struct ReadRequest {
    filter: ReadFilter,
    after: Option<Cursor>,
    limit: u8,
    reply: oneshot::Sender<Result<LogPage>>,
}

struct Admission {
    sender: Option<SyncSender<WriteRequest>>,
    sequence: u64,
}

struct Shared {
    epoch: LogId,
    started: Instant,
    admission: Mutex<Admission>,
    readers: Mutex<Option<SyncSender<ReadRequest>>>,
    closing: AtomicBool,
    accepted: AtomicU64,
    queued: AtomicU64,
    written: AtomicU64,
    dropped: AtomicU64,
    oversized: AtomicU64,
    projection_errors: AtomicU64,
    overflowed: AtomicBool,
    failed: AtomicBool,
    failure: AtomicU8,
    last_written: Mutex<Option<Cursor>>,
}

impl Shared {
    fn count(&self, counter: &AtomicU64) -> bool {
        if counter
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |v| v.checked_add(1))
            .is_err()
        {
            self.overflowed.store(true, Ordering::Release);
            self.latch(FailureCategory::State);
            return false;
        }
        true
    }

    fn latch(&self, category: FailureCategory) {
        self.failed.store(true, Ordering::Release);
        let _ = self.failure.compare_exchange(
            0,
            failure_number(category),
            Ordering::AcqRel,
            Ordering::Acquire,
        );
    }

    fn storage_error(&self, error: &Error) {
        self.latch(error_category(error));
        self.closing.store(true, Ordering::Release);
    }

    fn snapshot(&self) -> LogStatus {
        let last_written = match self.last_written.lock() {
            Ok(v) => v.clone(),
            Err(_) => {
                self.latch(FailureCategory::State);
                None
            }
        };
        let failure = failure_category(self.failure.load(Ordering::Acquire));
        let dropped = self.dropped.load(Ordering::Acquire);
        let oversized = self.oversized.load(Ordering::Acquire);
        let projection_errors = self.projection_errors.load(Ordering::Acquire);
        let overflowed = self.overflowed.load(Ordering::Acquire);
        // An actual write always follows admission. Sample written first so a
        // concurrent successful producer cannot make the snapshot invert them.
        let written = self.written.load(Ordering::Acquire);
        let accepted = self.accepted.load(Ordering::Acquire);
        LogStatus {
            healthy: !self.failed.load(Ordering::Acquire)
                && failure.is_none()
                && dropped == 0
                && oversized == 0
                && projection_errors == 0
                && !overflowed,
            closing: self.closing.load(Ordering::Acquire),
            queued: self
                .queued
                .load(Ordering::Acquire)
                .min(QUEUE_CAPACITY as u64) as u32,
            accepted,
            written,
            dropped,
            oversized,
            projection_errors,
            overflowed,
            last_written,
            failure,
        }
    }

    fn record(&self, sequence: u64, level: Level, event: Event) -> Result<LogRecord> {
        let elapsed_millis = u64::try_from(self.started.elapsed().as_millis())
            .map_err(|_| Error::Worker("log elapsed counter exhausted"))?;
        let wall_unix_millis = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .ok()
            .and_then(|v| u64::try_from(v.as_millis()).ok());
        Ok(LogRecord {
            schema_version: 1,
            epoch: self.epoch.clone(),
            sequence,
            elapsed_millis,
            wall_unix_millis,
            level,
            event,
        })
    }

    fn submit(&self, level: Level, event: Event) {
        if self.closing.load(Ordering::Acquire) {
            self.count(&self.dropped);
            return;
        }
        // Validate and bound the complete line before acquiring the metadata-only
        // publication lock. The largest possible sequence reserves enough bytes
        // for the actual sequence assigned inside that lock.
        let mut record = match self.record(u64::MAX, level, event) {
            Ok(record) => record,
            Err(_) => {
                self.overflowed.store(true, Ordering::Release);
                self.latch(FailureCategory::State);
                self.count(&self.projection_errors);
                return;
            }
        };
        if record.validate().is_err() {
            self.count(&self.projection_errors);
            return;
        }
        if record::encode_line(&record).is_err() {
            self.count(&self.oversized);
            return;
        }
        // No filesystem, serialization or await occurs in this short region.
        // Ordinary concurrent producers serialize metadata instead of losing a
        // record solely because another producer holds this lock.
        let mut admission = match self.admission.lock() {
            Ok(v) => v,
            Err(_) => {
                self.latch(FailureCategory::State);
                self.count(&self.projection_errors);
                return;
            }
        };
        if self.closing.load(Ordering::Acquire) || admission.sender.is_none() {
            self.count(&self.dropped);
            return;
        }
        let Some(sequence) = admission.sequence.checked_add(1) else {
            self.overflowed.store(true, Ordering::Release);
            self.storage_error(&Error::Worker("log sequence exhausted"));
            return;
        };
        admission.sequence = sequence;
        record.sequence = sequence;
        if !self.count(&self.accepted) {
            return;
        }
        if !self.count(&self.queued) {
            self.accepted.fetch_sub(1, Ordering::AcqRel);
            return;
        }
        let result = match &admission.sender {
            Some(sender) => sender.try_send(WriteRequest::Record(record)),
            None => {
                self.accepted.fetch_sub(1, Ordering::AcqRel);
                self.queued.fetch_sub(1, Ordering::AcqRel);
                self.count(&self.dropped);
                return;
            }
        };
        if let Err(error) = result {
            self.accepted.fetch_sub(1, Ordering::AcqRel);
            self.queued.fetch_sub(1, Ordering::AcqRel);
            self.count(&self.dropped);
            if matches!(error, TrySendError::Disconnected(_)) {
                self.storage_error(&Error::Worker("log writer disconnected"));
            }
        }
    }

    fn synced(&self, cursor: Cursor) {
        match self.last_written.lock() {
            Ok(mut value) => *value = Some(cursor),
            Err(_) => self.latch(FailureCategory::State),
        }
    }
}

/// Metadata-only weak observer; it does not keep the writer or files alive.
#[derive(Clone)]
pub struct LogMonitor {
    shared: Weak<Shared>,
}

impl LogMonitor {
    /// Observe actual bounded counters. A closed owner is explicitly unavailable.
    pub fn status(&self) -> LogStatus {
        self.shared
            .upgrade()
            .map_or_else(unavailable_status, |s| s.snapshot())
    }

    /// Mirror one already committed application audit, without acknowledging work.
    ///
    /// This never waits for filesystem I/O or queue capacity. Loss is observable
    /// through status and never changes the business WAL outcome.
    pub fn audit(&self, projection: AuditProjection) {
        if let Some(shared) = self.shared.upgrade() {
            let level = if projection.failure.is_some() {
                Level::Warn
            } else {
                Level::Info
            };
            shared.submit(level, Event::Audit(projection));
        }
    }

    /// Read a finite already-authorized page using two actually owned workers.
    ///
    /// At most two requests wait in addition to the two active operations. Dropping
    /// this future does not free a running worker or cancel its filesystem calls.
    pub async fn read(
        &self,
        filter: ReadFilter,
        after: Option<Cursor>,
        limit: u8,
    ) -> Result<LogPage> {
        validate_read(&filter, limit)?;
        let shared = self.shared.upgrade().ok_or(Error::Disabled)?;
        let (reply, response) = oneshot::channel();
        {
            let admission = match shared.readers.try_lock() {
                Ok(v) => v,
                Err(std::sync::TryLockError::WouldBlock) => return Err(Error::Busy),
                Err(std::sync::TryLockError::Poisoned(_)) => {
                    shared.storage_error(&Error::Worker("reader admission failed"));
                    return Err(Error::Worker("reader admission failed"));
                }
            };
            let sender = admission.as_ref().ok_or(Error::Disabled)?;
            match sender.try_send(ReadRequest {
                filter,
                after,
                limit,
                reply,
            }) {
                Ok(()) => {}
                Err(TrySendError::Full(_)) => return Err(Error::Busy),
                Err(TrySendError::Disconnected(_)) => {
                    shared.storage_error(&Error::Worker("log readers disconnected"));
                    return Err(Error::Worker("log readers disconnected"));
                }
            }
        }
        // Do not hold Shared/admission while a client waits. The actual owner and
        // reader thread, not the observer future, retain the I/O lifetime.
        drop(shared);
        response
            .await
            .map_err(|_| Error::Worker("log reader ended abnormally"))?
    }
}

/// Sole owner of the actual writer and two reader threads.
///
/// Call synchronous shutdown only after device/network owners have completed their
/// cleanup. A hard-blocked filesystem can also block this real join. Drop performs
/// the same join rather than detaching threads.
pub struct LogOwner {
    console: Option<Arc<crate::console::ConsoleState>>,
    shared: Option<Arc<Shared>>,
    writer: Option<JoinHandle<Result<()>>>,
    readers: Vec<JoinHandle<()>>,
}

impl LogOwner {
    /// Open checked storage and own all workers without installing a subscriber.
    ///
    /// This is useful to applications which already manage their own tracing
    /// subscriber. It performs exactly the same storage checks as initialize.
    pub fn open(options: Options) -> Result<Self> {
        options.validate()?;
        let epoch = LogId::fresh()?;
        let store = storage::Storage::open(&options, &epoch)?;
        Self::start(store, epoch, ReaderHooks::default(), WriterHooks::default())
    }

    fn start(
        mut store: storage::Storage,
        epoch: LogId,
        reader_hooks: ReaderHooks,
        writer_hooks: WriterHooks,
    ) -> Result<Self> {
        let source = store.source();
        let (writer_tx, writer_rx) = mpsc::sync_channel(QUEUE_CAPACITY);
        let (reader_tx, reader_rx) = mpsc::sync_channel(2);
        let shared = Arc::new(Shared {
            epoch,
            started: Instant::now(),
            admission: Mutex::new(Admission {
                sender: Some(writer_tx),
                sequence: 1,
            }),
            readers: Mutex::new(Some(reader_tx)),
            closing: AtomicBool::new(false),
            accepted: AtomicU64::new(0),
            queued: AtomicU64::new(0),
            written: AtomicU64::new(0),
            dropped: AtomicU64::new(0),
            oversized: AtomicU64::new(0),
            projection_errors: AtomicU64::new(0),
            overflowed: AtomicBool::new(false),
            failed: AtomicBool::new(false),
            failure: AtomicU8::new(0),
            last_written: Mutex::new(None),
        });
        let started = shared.record(
            1,
            Level::Info,
            Event::Started {
                pid: std::process::id(),
                uid: actual_uid(),
            },
        )?;
        shared.synced(store.append(&started)?);
        let mut owner = Self {
            console: None,
            shared: Some(shared.clone()),
            writer: None,
            readers: Vec::new(),
        };
        let receiver = Arc::new(Mutex::new(reader_rx));
        for number in 0..2 {
            let receiver = receiver.clone();
            let shared = shared.clone();
            let source = source.clone();
            let hooks = reader_hooks.clone();
            let reader = thread::Builder::new()
                .name(format!("rustusb-log-read-{number}"))
                .spawn(move || reader_loop(receiver, source, shared, hooks))?;
            owner.readers.push(reader);
        }
        let worker_shared = shared.clone();
        let writer = thread::Builder::new()
            .name("rustusb-log-write".into())
            .spawn(move || writer_loop(store, writer_rx, worker_shared, writer_hooks))?;
        owner.writer = Some(writer);
        Ok(owner)
    }

    /// Obtain a weak metadata observer, or None in default stderr-only mode.
    pub fn monitor(&self) -> Option<LogMonitor> {
        self.shared.as_ref().map(|shared| LogMonitor {
            shared: Arc::downgrade(shared),
        })
    }

    /// Close admissions, drain and actually join all writer/reader operations.
    ///
    /// A distinct logging failure never means a USB operation did not occur.
    pub fn shutdown(mut self) -> Result<ShutdownReport> {
        self.finish()
    }

    fn finish(&mut self) -> Result<ShutdownReport> {
        let Some(shared) = self.shared.take() else {
            if let Some(console) = self.console.take() {
                console.check()?;
            }
            return Ok(ShutdownReport { status: None });
        };
        shared.closing.store(true, Ordering::Release);
        let mut error = None;
        let sender = match shared.admission.lock() {
            Ok(mut admission) => admission.sender.take(),
            Err(poisoned) => {
                shared.storage_error(&Error::Worker("writer admission failed"));
                error = Some(Error::Worker("writer admission failed"));
                poisoned.into_inner().sender.take()
            }
        };
        match shared.readers.lock() {
            Ok(mut readers) => {
                readers.take();
            }
            Err(poisoned) => {
                shared.storage_error(&Error::Worker("reader admission failed"));
                error.get_or_insert(Error::Worker("reader admission failed"));
                poisoned.into_inner().take();
            }
        }
        // Reader failures must be reflected in the final writer observation.
        // The writer continues draining its finite queue while these actual
        // reads finish; no reader can publish a new write request.
        for reader in self.readers.drain(..) {
            if reader.join().is_err() {
                shared.storage_error(&Error::Worker("reader panicked"));
                error.get_or_insert(Error::Worker("reader panicked"));
            }
        }
        if let Some(sender) = sender
            && self.writer.is_some()
            && sender.send(WriteRequest::Shutdown).is_err()
        {
            // No producer can publish after this fence. Blocking here is permitted
            // only in the outer owner, after actual USB cleanup.
            shared.storage_error(&Error::Worker("writer ended before shutdown"));
            error.get_or_insert(Error::Worker("writer ended before shutdown"));
        }
        if let Some(writer) = self.writer.take() {
            match writer.join() {
                Ok(Ok(())) => {}
                Ok(Err(failure)) => {
                    error.get_or_insert(failure);
                }
                Err(_) => {
                    shared.storage_error(&Error::Worker("writer panicked"));
                    error.get_or_insert(Error::Worker("writer panicked"));
                }
            }
        }
        let status = shared.snapshot();
        if error.is_none() && shared.failed.load(Ordering::Acquire) {
            error = Some(Error::Worker("log worker failed"));
        }
        if error.is_none() && !status.healthy {
            error = Some(Error::LostRecords);
        }
        match error {
            Some(error) => Err(error),
            None => Ok(ShutdownReport {
                status: Some(status),
            }),
        }
    }
}

impl Drop for LogOwner {
    fn drop(&mut self) {
        // Never recursively log a failed sink or print to machine stdout. All real
        // owners are joined even when an explicit caller ignores its result.
        let _ = self.finish();
    }
}

/// Install default legacy stderr logging or the checked file-only typed layer.
///
/// File mode deliberately conflicts with console JSON; it never tees file logs
/// synchronously to stderr. Configure once before creating device owners/runtime.
pub fn initialize(options: Option<Options>, json_console: bool) -> Result<LogOwner> {
    if options.is_some() && json_console {
        return Err(Error::InvalidInput(
            "file logging conflicts with console JSON",
        ));
    }
    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    if let Some(options) = options {
        let owner = LogOwner::open(options)?;
        let monitor = owner.monitor().ok_or(Error::Worker("file owner missing"))?;
        let layer = TypedLayer {
            shared: monitor.shared,
        };
        tracing_subscriber::registry()
            .with(filter)
            .with(layer)
            .try_init()
            .map_err(|_| Error::Worker("subscriber could not be installed"))?;
        Ok(owner)
    } else {
        let console = Arc::new(crate::console::ConsoleState::default());
        let writer = crate::console::ConsoleWriter(console.clone());
        let initialized = if json_console {
            tracing_subscriber::fmt()
                .with_env_filter(filter)
                .with_writer(writer)
                .log_internal_errors(false)
                .json()
                .with_ansi(false)
                .try_init()
        } else {
            tracing_subscriber::fmt()
                .with_env_filter(filter)
                .with_writer(writer)
                .log_internal_errors(false)
                .with_ansi(false)
                .try_init()
        };
        initialized.map_err(|_| Error::Worker("subscriber could not be installed"))?;
        Ok(LogOwner {
            console: Some(console),
            shared: None,
            writer: None,
            readers: Vec::new(),
        })
    }
}

fn writer_loop(
    mut store: storage::Storage,
    receiver: Receiver<WriteRequest>,
    shared: Arc<Shared>,
    hooks: WriterHooks,
) -> Result<()> {
    let mut error = None;
    while let Ok(request) = receiver.recv() {
        match request {
            WriteRequest::Record(record) => {
                shared.queued.fetch_sub(1, Ordering::AcqRel);
                if shared.failed.load(Ordering::Acquire) {
                    continue;
                }
                hooks.before_write();
                match store.append(&record) {
                    Ok(cursor) => {
                        shared.synced(cursor);
                        shared.count(&shared.written);
                    }
                    Err(failure) => {
                        shared.storage_error(&failure);
                        error.get_or_insert(failure);
                    }
                }
            }
            WriteRequest::Shutdown => break,
        }
    }
    if !shared.failed.load(Ordering::Acquire) {
        let sequence = match shared.admission.lock() {
            Ok(mut admission) => {
                let sequence = admission.sequence.checked_add(1);
                if let Some(sequence) = sequence {
                    admission.sequence = sequence;
                }
                sequence
            }
            Err(_) => None,
        };
        let stopped = sequence
            .ok_or(Error::Worker("log sequence exhausted"))
            .and_then(|sequence| {
                shared.record(
                    sequence,
                    Level::Info,
                    Event::Stopped {
                        status: shared.snapshot(),
                    },
                )
            });
        match stopped.and_then(|record| store.append(&record)) {
            Ok(cursor) => shared.synced(cursor),
            Err(failure) => {
                shared.storage_error(&failure);
                error.get_or_insert(failure);
            }
        }
    }
    if let Err(failure) = store.sync() {
        shared.storage_error(&failure);
        error.get_or_insert(failure);
    }
    match error {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

fn reader_loop(
    receiver: Arc<Mutex<Receiver<ReadRequest>>>,
    source: storage::Source,
    shared: Arc<Shared>,
    hooks: ReaderHooks,
) {
    loop {
        let request = match receiver.lock() {
            Ok(receiver) => receiver.recv(),
            Err(_) => {
                shared.storage_error(&Error::Worker("reader queue failed"));
                return;
            }
        };
        let Ok(request) = request else {
            return;
        };
        hooks.before_read();
        let mut result = storage::read_source(
            &source,
            &request.filter,
            request.after.as_ref(),
            request.limit,
        );
        match &mut result {
            Ok(page) => page.status = Some(shared.snapshot()),
            Err(error)
                if matches!(
                    error,
                    Error::Io(_) | Error::Permission(_) | Error::Corrupt(_)
                ) =>
            {
                shared.storage_error(error);
            }
            _ => {}
        }
        // Even a disconnected observer does not cancel or detach the operation.
        let _ = request.reply.send(result);
    }
}

fn validate_read(filter: &ReadFilter, limit: u8) -> Result<()> {
    if !(1..=crate::MAX_PAGE_RECORDS).contains(&limit) {
        return Err(Error::InvalidInput("log page limit"));
    }
    if let ReadFilter::Audit { profile, .. } = filter
        && (profile.is_empty()
            || profile.len() > 64
            || !profile
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.')))
    {
        return Err(Error::InvalidInput("log profile"));
    }
    Ok(())
}

fn actual_uid() -> u32 {
    #[cfg(unix)]
    {
        rustix::process::geteuid().as_raw()
    }
    #[cfg(not(unix))]
    {
        0
    }
}

fn unavailable_status() -> LogStatus {
    LogStatus {
        healthy: false,
        closing: true,
        failure: Some(FailureCategory::State),
        ..LogStatus::default()
    }
}

fn error_category(error: &Error) -> FailureCategory {
    match error {
        Error::InvalidInput(_) => FailureCategory::InvalidInput,
        Error::Permission(_) => FailureCategory::Permission,
        Error::Io(_) => FailureCategory::Io,
        Error::Unsupported => FailureCategory::Unsupported,
        _ => FailureCategory::State,
    }
}

fn failure_number(category: FailureCategory) -> u8 {
    match category {
        FailureCategory::InvalidInput => 1,
        FailureCategory::Permission => 2,
        FailureCategory::Protocol => 3,
        FailureCategory::Io => 4,
        FailureCategory::Timeout => 5,
        FailureCategory::State => 6,
        FailureCategory::Unsupported => 7,
        FailureCategory::Command => 8,
        FailureCategory::UnsafeDevice => 9,
        FailureCategory::Cancelled => 10,
        FailureCategory::Persistence => 11,
    }
}

fn failure_category(value: u8) -> Option<FailureCategory> {
    match value {
        0 => None,
        1 => Some(FailureCategory::InvalidInput),
        2 => Some(FailureCategory::Permission),
        3 => Some(FailureCategory::Protocol),
        4 => Some(FailureCategory::Io),
        5 => Some(FailureCategory::Timeout),
        6 => Some(FailureCategory::State),
        7 => Some(FailureCategory::Unsupported),
        8 => Some(FailureCategory::Command),
        9 => Some(FailureCategory::UnsafeDevice),
        10 => Some(FailureCategory::Cancelled),
        11 => Some(FailureCategory::Persistence),
        _ => Some(FailureCategory::State),
    }
}

struct TypedLayer {
    shared: Weak<Shared>,
}
impl<S: Subscriber> Layer<S> for TypedLayer {
    fn on_event(&self, event: &TracingEvent<'_>, _context: Context<'_, S>) {
        let Some(shared) = self.shared.upgrade() else {
            return;
        };
        let metadata = event.metadata();
        if !record::approved_target(metadata.target()) {
            return;
        }
        let mut projection = Projection::default();
        event.record(&mut projection);
        let level = match *metadata.level() {
            tracing::Level::TRACE => Level::Trace,
            tracing::Level::DEBUG => Level::Debug,
            tracing::Level::INFO => Level::Info,
            tracing::Level::WARN => Level::Warn,
            tracing::Level::ERROR => Level::Error,
        };
        shared.submit(
            level,
            Event::Diagnostic(Diagnostic {
                code: projection.code,
                target: metadata.target().to_owned(),
                line: metadata.line(),
                failure: projection.failure,
                errno: projection.errno,
                fields: projection.fields,
                omitted_fields: projection.omitted,
            }),
        );
    }
}

struct Projection {
    code: EventCode,
    failure: Option<FailureCategory>,
    errno: Option<i32>,
    fields: BTreeMap<Field, Scalar>,
    omitted: u32,
}

impl Default for Projection {
    fn default() -> Self {
        Self {
            code: EventCode::Diagnostic,
            failure: None,
            errno: None,
            fields: BTreeMap::new(),
            omitted: 0,
        }
    }
}

impl Projection {
    fn omit(&mut self) {
        self.omitted = self.omitted.saturating_add(1);
    }
    fn scalar(&mut self, field: &tracing::field::Field, value: Scalar) {
        let key = match field.name() {
            "count" => Field::Count,
            "device_count" => Field::DeviceCount,
            "session_count" => Field::SessionCount,
            "interface" => Field::Interface,
            "success" => Field::Success,
            "network_closed" => Field::NetworkClosed,
            "bytes_to_peer" => Field::BytesToPeer,
            "bytes_from_peer" => Field::BytesFromPeer,
            "elapsed_millis" => Field::ElapsedMillis,
            "errno" => {
                let errno = match value {
                    Scalar::Signed(v) => i32::try_from(v).ok(),
                    Scalar::Unsigned(v) => i32::try_from(v).ok(),
                    Scalar::Bool(_) => None,
                };
                if let Some(errno) = errno.filter(|v| (1..=4095).contains(v)) {
                    self.errno = Some(errno);
                } else {
                    self.omit();
                }
                return;
            }
            _ => {
                self.omit();
                return;
            }
        };
        // Reuse the actual record validator rather than accepting an unbounded
        // string or formatting a Debug value for a numeric field.
        self.fields.insert(key, value);
    }
}

impl Visit for Projection {
    fn record_debug(&mut self, _field: &tracing::field::Field, _value: &dyn fmt::Debug) {
        // Deliberately do not call format!/Display/Debug, even for approved names.
        self.omit();
    }
    fn record_error(
        &mut self,
        _field: &tracing::field::Field,
        _value: &(dyn std::error::Error + 'static),
    ) {
        self.omit();
    }
    fn record_str(&mut self, field: &tracing::field::Field, value: &str) {
        match field.name() {
            "operation" => {
                self.code = match value {
                    "attach" => EventCode::NativeAttach,
                    "detach" => EventCode::NativeDetach,
                    "export" | "native_secure_export" => EventCode::NativeExport,
                    "unexport" => EventCode::NativeUnexport,
                    _ => {
                        self.omit();
                        return;
                    }
                };
            }
            "failure" | "failure_category" => {
                self.failure = match value {
                    "invalid_input" => Some(FailureCategory::InvalidInput),
                    "permission" => Some(FailureCategory::Permission),
                    "protocol" => Some(FailureCategory::Protocol),
                    "io" => Some(FailureCategory::Io),
                    "timeout" => Some(FailureCategory::Timeout),
                    "state" => Some(FailureCategory::State),
                    "unsupported" => Some(FailureCategory::Unsupported),
                    "command" => Some(FailureCategory::Command),
                    "unsafe_device" => Some(FailureCategory::UnsafeDevice),
                    "cancelled" => Some(FailureCategory::Cancelled),
                    "persistence" => Some(FailureCategory::Persistence),
                    _ => {
                        self.omit();
                        None
                    }
                };
            }
            "event_code" => {
                self.code = match value {
                    "discovery_completed" => EventCode::DiscoveryCompleted,
                    "operation_started" => EventCode::OperationStarted,
                    "operation_completed" => EventCode::OperationCompleted,
                    "operation_failed" => EventCode::OperationFailed,
                    "native_attach" => EventCode::NativeAttach,
                    "native_detach" => EventCode::NativeDetach,
                    "native_export" => EventCode::NativeExport,
                    "native_unexport" => EventCode::NativeUnexport,
                    "network_ended" => EventCode::NetworkEnded,
                    "resource_released" => EventCode::ResourceReleased,
                    "diagnostic" => EventCode::Diagnostic,
                    _ => {
                        self.omit();
                        EventCode::Diagnostic
                    }
                };
            }
            _ => self.omit(),
        }
    }
    fn record_u64(&mut self, field: &tracing::field::Field, value: u64) {
        self.scalar(field, Scalar::Unsigned(value));
    }
    fn record_i64(&mut self, field: &tracing::field::Field, value: i64) {
        self.scalar(field, Scalar::Signed(value));
    }
    fn record_bool(&mut self, field: &tracing::field::Field, value: bool) {
        self.scalar(field, Scalar::Bool(value));
    }
}

// These hooks are absent from non-test builds. They gate the same actual worker
// before its real storage call; they do not replace results or create USB proof.
#[derive(Clone, Default)]
struct ReaderHooks {
    #[cfg(test)]
    before: Option<Arc<dyn Fn() + Send + Sync>>,
}
impl ReaderHooks {
    fn before_read(&self) {
        #[cfg(test)]
        if let Some(before) = &self.before {
            before();
        }
    }
}
#[derive(Default)]
struct WriterHooks {
    #[cfg(test)]
    before: Option<Arc<dyn Fn() + Send + Sync>>,
}
impl WriterHooks {
    fn before_write(&self) {
        #[cfg(test)]
        if let Some(before) = &self.before {
            before();
        }
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use crate::{AuditAction, Options};
    use rustusb_core::application::InstanceId;
    use std::{
        future::Future,
        os::unix::fs::PermissionsExt,
        sync::{Condvar, atomic::AtomicUsize},
        task::{Context as TaskContext, Poll, Waker},
        time::Duration,
    };

    #[derive(Default)]
    struct Gate {
        state: Mutex<(usize, bool)>,
        changed: Condvar,
        failed: AtomicBool,
    }
    impl Gate {
        fn enter(&self) {
            let Ok(mut state) = self.state.lock() else {
                self.failed.store(true, Ordering::Release);
                return;
            };
            state.0 += 1;
            self.changed.notify_all();
            let deadline = Instant::now() + Duration::from_secs(10);
            while !state.1 {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    self.failed.store(true, Ordering::Release);
                    return;
                }
                match self.changed.wait_timeout(state, remaining) {
                    Ok((next, _)) => state = next,
                    Err(_) => {
                        self.failed.store(true, Ordering::Release);
                        return;
                    }
                }
            }
        }
        fn release(&self) {
            match self.state.lock() {
                Ok(mut state) => state.1 = true,
                Err(poisoned) => {
                    poisoned.into_inner().1 = true;
                    self.failed.store(true, Ordering::Release);
                }
            }
            self.changed.notify_all();
        }
        fn entered(&self) -> usize {
            self.state.lock().map_or(0, |s| s.0)
        }
    }
    struct Release(Arc<Gate>);
    impl Drop for Release {
        fn drop(&mut self) {
            self.0.release();
        }
    }

    fn fixture(
        reader_hooks: ReaderHooks,
        writer_hooks: WriterHooks,
    ) -> Result<(tempfile::TempDir, LogOwner, LogMonitor)> {
        let temp = tempfile::tempdir()?;
        std::fs::set_permissions(temp.path(), std::fs::Permissions::from_mode(0o700))?;
        let directory = temp.path().join("logs");
        std::fs::create_dir(&directory)?;
        std::fs::set_permissions(&directory, std::fs::Permissions::from_mode(0o700))?;
        let options = Options::new(directory);
        let epoch = LogId::fresh()?;
        let storage = storage::Storage::fixture(&options, &epoch, temp.path())?;
        let owner = LogOwner::start(storage, epoch, reader_hooks, writer_hooks)?;
        let monitor = owner
            .monitor()
            .ok_or(Error::Worker("test monitor missing"))?;
        Ok((temp, owner, monitor))
    }

    fn audit(sequence: u64) -> Result<AuditProjection> {
        Ok(AuditProjection {
            application_instance: InstanceId::new("00000000-0000-0000-0000-000000000001")
                .map_err(|_| Error::InvalidInput("test instance"))?,
            audit_sequence: sequence,
            actor_uid: actual_uid(),
            profile: Some("lab-hid".into()),
            job_id: None,
            session_id: None,
            action: AuditAction::Observe,
            failure: None,
            state: None,
            evidence: None,
            cleanup: None,
        })
    }

    async fn until(mut condition: impl FnMut() -> bool) -> Result<()> {
        tokio::time::timeout(Duration::from_secs(5), async {
            while !condition() {
                tokio::time::sleep(Duration::from_millis(1)).await;
            }
        })
        .await
        .map_err(|_| Error::Worker("test observation deadline"))
    }

    struct Secret(Arc<AtomicUsize>);
    impl fmt::Debug for Secret {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            self.0.fetch_add(1, Ordering::AcqRel);
            f.write_str("SECRET_SERIAL_KEY_BUFFER_SENTINEL")
        }
    }

    #[tokio::test]
    async fn actual_typed_layer_never_formats_unapproved_debug_or_stores_secrets() -> Result<()> {
        let (_temp, owner, monitor) = fixture(ReaderHooks::default(), WriterHooks::default())?;
        let formats = Arc::new(AtomicUsize::new(0));
        let secret = Secret(formats.clone());
        let subscriber = tracing_subscriber::registry().with(TypedLayer {
            shared: monitor.shared.clone(),
        });
        tracing::subscriber::with_default(subscriber, || {
            tracing::error!(target: "rustusb_cli::logging_test", event_code="operation_failed", failure="io", errno=5_i64, count=2_u64,
                error=?secret, serial="SERIAL_SENTINEL", key="KEY_SENTINEL", buffer=?secret,
                "RAW_MESSAGE_SENTINEL");
            tracing::error!(target:"unapproved_library", error=?secret, "UNAPPROVED_MESSAGE_SENTINEL");
        });
        until(|| monitor.status().written == 1).await?;
        assert_eq!(formats.load(Ordering::Acquire), 0);
        let page = monitor.read(ReadFilter::All, None, 16).await?;
        let data = serde_json::to_string(&page).map_err(|_| Error::Worker("test page encoding"))?;
        for secret in [
            "SECRET_SERIAL_KEY_BUFFER_SENTINEL",
            "SERIAL_SENTINEL",
            "KEY_SENTINEL",
            "RAW_MESSAGE_SENTINEL",
            "UNAPPROVED_MESSAGE_SENTINEL",
        ] {
            assert!(!data.contains(secret));
        }
        let diagnostic = page
            .records
            .iter()
            .find_map(|r| match &r.event {
                Event::Diagnostic(v) => Some(v),
                _ => None,
            })
            .ok_or(Error::Worker("safe diagnostic missing"))?;
        assert_eq!(diagnostic.failure, Some(FailureCategory::Io));
        assert_eq!(diagnostic.errno, Some(5));
        assert_eq!(
            diagnostic.fields.get(&Field::Count),
            Some(&Scalar::Unsigned(2))
        );
        assert!(diagnostic.omitted_fields >= 5);
        assert!(
            owner
                .shutdown()?
                .status
                .is_some_and(|s| s.healthy && s.accepted == 1 && s.written == 1)
        );
        assert!(matches!(
            monitor.read(ReadFilter::All, None, 16).await,
            Err(Error::Disabled)
        ));
        assert!(!monitor.status().healthy);
        Ok(())
    }

    #[tokio::test]
    async fn cancelled_readers_keep_actual_two_slots_and_only_two_waiting_requests() -> Result<()> {
        let gate = Arc::new(Gate::default());
        let hooks = ReaderHooks {
            before: Some({
                let gate = gate.clone();
                Arc::new(move || gate.enter())
            }),
        };
        let (_temp, owner, monitor) = fixture(hooks, WriterHooks::default())?;
        let _release = Release(gate.clone());
        let first = tokio::spawn({
            let monitor = monitor.clone();
            async move { monitor.read(ReadFilter::All, None, 16).await }
        });
        let second = tokio::spawn({
            let monitor = monitor.clone();
            async move { monitor.read(ReadFilter::All, None, 16).await }
        });
        until(|| gate.entered() == 2).await?;
        first.abort();
        second.abort();
        assert!(first.await.is_err());
        assert!(second.await.is_err());
        let mut third = Box::pin(monitor.read(ReadFilter::All, None, 16));
        let mut fourth = Box::pin(monitor.read(ReadFilter::All, None, 16));
        let mut context = TaskContext::from_waker(Waker::noop());
        assert!(matches!(third.as_mut().poll(&mut context), Poll::Pending));
        assert!(matches!(fourth.as_mut().poll(&mut context), Poll::Pending));
        assert!(matches!(
            monitor.read(ReadFilter::All, None, 16).await,
            Err(Error::Busy)
        ));
        drop(third);
        drop(fourth);
        assert!(matches!(
            monitor.read(ReadFilter::All, None, 16).await,
            Err(Error::Busy)
        ));
        gate.release();
        let report = owner.shutdown()?;
        assert!(report.status.is_some_and(|s| s.healthy));
        assert_eq!(gate.entered(), 4);
        assert!(!gate.failed.load(Ordering::Acquire));
        Ok(())
    }

    #[tokio::test]
    async fn full_actual_writer_queue_is_loss_visible_and_shutdown_joins_after_release()
    -> Result<()> {
        let gate = Arc::new(Gate::default());
        let hooks = WriterHooks {
            before: Some({
                let gate = gate.clone();
                Arc::new(move || gate.enter())
            }),
        };
        let (_temp, owner, monitor) = fixture(ReaderHooks::default(), hooks)?;
        let _release = Release(gate.clone());
        monitor.audit(audit(1)?);
        until(|| gate.entered() == 1).await?;
        for sequence in 2..=(QUEUE_CAPACITY as u64 + 2) {
            monitor.audit(audit(sequence)?);
        }
        let status = monitor.status();
        assert_eq!(status.queued as usize, QUEUE_CAPACITY);
        assert_eq!(status.accepted, QUEUE_CAPACITY as u64 + 1);
        assert_eq!(status.written, 0);
        assert_eq!(status.dropped, 1);
        assert!(!status.healthy);
        let (finished, receive) = mpsc::channel();
        let joined = thread::spawn(move || {
            let result = owner.shutdown();
            let _ = finished.send(result);
        });
        assert!(matches!(receive.try_recv(), Err(mpsc::TryRecvError::Empty)));
        gate.release();
        let result = receive
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| Error::Worker("test shutdown deadline"))?;
        assert!(matches!(result, Err(Error::LostRecords)));
        joined
            .join()
            .map_err(|_| Error::Worker("test owner panicked"))?;
        assert!(!gate.failed.load(Ordering::Acquire));
        assert_eq!(gate.entered(), QUEUE_CAPACITY + 1);
        Ok(())
    }

    #[tokio::test]
    async fn actual_directory_replacement_latches_error_and_never_fakes_successful_shutdown()
    -> Result<()> {
        let gate = Arc::new(Gate::default());
        let hooks = WriterHooks {
            before: Some({
                let gate = gate.clone();
                Arc::new(move || gate.enter())
            }),
        };
        let (temp, owner, monitor) = fixture(ReaderHooks::default(), hooks)?;
        let _release = Release(gate.clone());
        monitor.audit(audit(1)?);
        until(|| gate.entered() == 1).await?;
        std::fs::rename(temp.path().join("logs"), temp.path().join("old-logs"))?;
        std::fs::create_dir(temp.path().join("logs"))?;
        std::fs::set_permissions(
            temp.path().join("logs"),
            std::fs::Permissions::from_mode(0o700),
        )?;
        gate.release();
        until(|| monitor.status().failure.is_some()).await?;
        let status = monitor.status();
        assert!(!status.healthy);
        assert_eq!(status.written, 0);
        assert!(matches!(
            owner.shutdown(),
            Err(Error::Permission(_)) | Err(Error::Io(_))
        ));
        assert!(!gate.failed.load(Ordering::Acquire));
        Ok(())
    }
}

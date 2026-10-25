//! The owned single-stream h2 data link. Only authenticated TLS code constructs it.

use crate::{
    Error, Result,
    flush::{FlushMonitor, Side, TrackingIo},
    quiet::{self, Quiet, QuietFuture},
};
use bytes::Bytes;
use rustusb_core::link_health::{
    LinkEnd, LinkHealth, LinkIdentity, LinkPhase, LinkProtocol, LinkStats, ProbeState, TcpTuple,
};
use std::{
    future::{Future, poll_fn},
    io,
    pin::Pin,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, AtomicU8, AtomicU64, Ordering},
    },
    task::{Context, Poll},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncWrite, DuplexStream, ReadBuf},
    sync::{Notify, watch},
    task::JoinHandle,
    time::Instant,
};

const WINDOW: u32 = 65_535;
const FRAME: usize = 16_384;
const AUTHORITY: &str = "rustusb.invalid:443";
static NEXT_CONNECTION: AtomicU64 = AtomicU64::new(1);

/// Validated policy for probes on the actual data connection.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ProbePolicy {
    interval: Duration,
    response_timeout: Duration,
}

impl ProbePolicy {
    /// Require interval1..=60 seconds and timeout2*interval..=300 seconds.
    pub fn new(interval: Duration, response_timeout: Duration) -> Result<Self> {
        if interval < Duration::from_secs(1)
            || interval > Duration::from_secs(60)
            || response_timeout > Duration::from_secs(300)
            || response_timeout < interval.saturating_mul(2)
        {
            return Err(Error::InvalidInput("data-link probe policy"));
        }
        Ok(Self {
            interval,
            response_timeout,
        })
    }
    /// Fixed production interval between completed probes.
    pub fn interval(&self) -> Duration {
        self.interval
    }
    /// Absolute pending-response and completed-connection drain grace.
    pub fn response_timeout(&self) -> Duration {
        self.response_timeout
    }
}

impl Default for ProbePolicy {
    fn default() -> Self {
        Self {
            interval: Duration::from_secs(5),
            response_timeout: Duration::from_secs(15),
        }
    }
}

/// Actual stream, cooperative task owner, and nonowning observations.
pub struct Link<I = LinkIo> {
    /// Authenticated application byte stream, without h2 control frames.
    pub io: I,
    /// Must be cooperatively stopped and joined before device cleanup.
    pub owner: LinkOwner,
    /// Latest facts only; contains no stream, permit, or device lease.
    pub monitor: LinkMonitor,
}

/// Real driver terminal result, never USB cleanup proof.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct LinkOutcome {
    /// Redacted actual driver terminal cause.
    pub reason: LinkEnd,
    /// Frozen final transport sample.
    pub final_health: LinkHealth,
}

struct Observation {
    health: Mutex<LinkHealth>,
    revision: watch::Sender<u64>,
    ended: watch::Sender<bool>,
    alive: AtomicBool,
    failed: AtomicBool,
}

/// Cloneable latest-fact observer, without an owning or stop capability.
#[derive(Clone)]
pub struct LinkMonitor {
    observation: Arc<Observation>,
}

impl LinkMonitor {
    /// Read a validated sample; poison or an unexpectedly lost driver is an error.
    pub fn snapshot(&self) -> Result<LinkHealth> {
        if self.observation.failed.load(Ordering::Acquire) {
            return Err(Error::LinkDriverFailed);
        }
        let sample = *self
            .observation
            .health
            .lock()
            .map_err(|_| Error::LinkDriverFailed)?;
        if self.observation.failed.load(Ordering::Acquire) {
            return Err(Error::LinkDriverFailed);
        }
        if !self.observation.alive.load(Ordering::Acquire) && sample.phase != LinkPhase::Ended {
            return Err(Error::LinkDriverFailed);
        }
        sample.validate().map_err(|_| Error::LinkDriverFailed)?;
        Ok(sample)
    }
    /// Latest publication sequence; changes coalesce and never queue payload.
    pub fn changes(&self) -> watch::Receiver<u64> {
        self.observation.revision.subscribe()
    }
    /// Sticky driver-ended notification, delayed until clean receive data drains.
    pub fn ended(&self) -> watch::Receiver<bool> {
        self.observation.ended.subscribe()
    }
}

struct Shared {
    observation: Arc<Observation>,
    start: Instant,
    received: AtomicU64,
    consumed: AtomicU64,
    fatal: AtomicU8,
    wake: Notify,
    stop: watch::Sender<bool>,
    local_fin_flushed: AtomicBool,
}

fn code(reason: LinkEnd) -> u8 {
    match reason {
        LinkEnd::LocalStop => 1,
        LinkEnd::PeerEof => 2,
        LinkEnd::Io => 3,
        LinkEnd::Protocol => 4,
        LinkEnd::ProbeTimeout => 5,
        LinkEnd::CounterOverflow => 6,
        LinkEnd::LocalDataStall => 7,
    }
}
fn reason(code: u8) -> Option<LinkEnd> {
    match code {
        1 => Some(LinkEnd::LocalStop),
        2 => Some(LinkEnd::PeerEof),
        3 => Some(LinkEnd::Io),
        4 => Some(LinkEnd::Protocol),
        5 => Some(LinkEnd::ProbeTimeout),
        6 => Some(LinkEnd::CounterOverflow),
        7 => Some(LinkEnd::LocalDataStall),
        _ => None,
    }
}
fn failed() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidData,
        "framed data-link protocol failed",
    )
}
fn h2_error(error: h2::Error) -> io::Error {
    // Called only within quiet h2 boundaries; neither Display nor Debug escapes.
    let kind = error
        .get_io()
        .map_or(io::ErrorKind::InvalidData, |e| e.kind());
    io::Error::new(kind, "framed connection failed")
}
fn observation_error() -> io::Error {
    io::Error::other("data-link observation failed")
}
fn ended_error(end: LinkEnd) -> io::Error {
    io::Error::new(
        match end {
            LinkEnd::ProbeTimeout => io::ErrorKind::TimedOut,
            LinkEnd::LocalStop => io::ErrorKind::Interrupted,
            LinkEnd::PeerEof => io::ErrorKind::BrokenPipe,
            LinkEnd::Io => io::ErrorKind::ConnectionAborted,
            LinkEnd::LocalDataStall => io::ErrorKind::TimedOut,
            _ => io::ErrorKind::InvalidData,
        },
        "framed data link ended",
    )
}
fn elapsed(start: Instant) -> io::Result<u64> {
    u64::try_from(start.elapsed().as_millis()).map_err(|_| failed())
}

// The driver poll future borrows this Send-only monitor exclusively: its
// cancellation-safe waiter state need not be Sync merely for this observation.
fn observed_pending(flush: &mut FlushMonitor) -> bool {
    flush.write_pending()
}

impl Shared {
    fn check(&self) -> io::Result<()> {
        if self.observation.failed.load(Ordering::Acquire) {
            return Err(observation_error());
        }
        if let Some(end) = reason(self.fatal.load(Ordering::Acquire)) {
            return Err(ended_error(end));
        }
        if !self.observation.alive.load(Ordering::Acquire) {
            let clean = self
                .observation
                .health
                .lock()
                .map_err(|_| self.fail_observation())?
                .end
                == Some(LinkEnd::PeerEof);
            if !clean {
                return Err(ended_error(LinkEnd::Io));
            }
        }
        Ok(())
    }
    fn fail(&self, end: LinkEnd) {
        let _ = self
            .fatal
            .compare_exchange(0, code(end), Ordering::AcqRel, Ordering::Acquire);
        self.wake.notify_waiters();
    }
    fn fail_observation(&self) -> io::Error {
        self.observation.failed.store(true, Ordering::Release);
        self.wake.notify_waiters();
        observation_error()
    }
    fn update(&self, change: impl FnOnce(&mut LinkHealth)) -> io::Result<()> {
        if self.observation.failed.load(Ordering::Acquire) {
            return Err(observation_error());
        }
        let mut health = self
            .observation
            .health
            .lock()
            .map_err(|_| self.fail_observation())?;
        if health.phase == LinkPhase::Ended {
            return Ok(());
        }
        health.elapsed_millis =
            elapsed(self.start).inspect_err(|_| self.fail(LinkEnd::CounterOverflow))?;
        health.sample_sequence = health.sample_sequence.checked_add(1).ok_or_else(|| {
            self.fail(LinkEnd::CounterOverflow);
            failed()
        })?;
        // Load consumption first: received was published before exposing bytes.
        let consumed = self.consumed.load(Ordering::Acquire);
        let received = self.received.load(Ordering::Acquire);
        let pending = received
            .checked_sub(consumed)
            .and_then(|n| u32::try_from(n).ok())
            .filter(|n| *n <= WINDOW)
            .ok_or_else(|| self.fail_observation())?;
        if let Some(stats) = health.stats.as_mut() {
            stats.app_read_bytes = consumed;
            stats.receive_pending_bytes = pending;
        }
        change(&mut health);
        self.observation
            .revision
            .send_replace(health.sample_sequence);
        Ok(())
    }
    fn finish(&self, end: LinkEnd) -> Result<LinkOutcome> {
        self.update(|health| {
            health.phase = LinkPhase::Ended;
            health.end = Some(end);
            health.probe = if end == LinkEnd::ProbeTimeout {
                ProbeState::TimedOut
            } else {
                ProbeState::Stopped
            };
        })
        .map_err(|_| Error::LinkDriverFailed)?;
        let sample = *self
            .observation
            .health
            .lock()
            .map_err(|_| Error::LinkDriverFailed)?;
        sample.validate().map_err(|_| Error::LinkDriverFailed)?;
        self.observation.ended.send_replace(true);
        Ok(LinkOutcome {
            reason: end,
            final_health: sample,
        })
    }
    fn write_pending(&self, pending: bool) -> io::Result<()> {
        let changed = self
            .observation
            .health
            .lock()
            .map_err(|_| self.fail_observation())?
            .stats
            .is_some_and(|s| s.transport_write_blocked != pending);
        if changed {
            self.update(|h| {
                if let Some(s) = h.stats.as_mut() {
                    s.transport_write_blocked = pending;
                }
            })?;
        }
        Ok(())
    }
}

/// The unique driver join owner. Drop initiates stop but cannot prove completion.
pub struct LinkOwner {
    stop: watch::Sender<bool>,
    task: Option<JoinHandle<Result<LinkOutcome>>>,
}

impl LinkOwner {
    /// Request cooperative stop; the receiver remains independently driven.
    pub fn request_stop(&self) {
        self.stop.send_replace(true);
    }
    /// Join the actual task; panic/poison never turns into a successful snapshot.
    pub async fn join(mut self) -> Result<LinkOutcome> {
        let task = self.task.take().ok_or(Error::LinkDriverFailed)?;
        task.await.map_err(|_| Error::LinkDriverFailed)?
    }
}
impl Drop for LinkOwner {
    fn drop(&mut self) {
        self.request_stop();
    }
}

/// Bounded application bytes over stream1, without a public h2/TLS extraction API.
pub struct LinkIo {
    receive: DuplexStream,
    send: Quiet<h2::SendStream<Bytes>>,
    flush: FlushMonitor,
    shared: Arc<Shared>,
    data_enqueued: u64,
    fin_enqueued: bool,
}

impl AsyncRead for LinkIo {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if let Err(error) = this.shared.check() {
            return Poll::Ready(Err(error));
        }
        let before = buffer.filled().len();
        match Pin::new(&mut this.receive).poll_read(cx, buffer) {
            Poll::Ready(Ok(())) => {
                let count = buffer.filled().len() - before;
                if count != 0 {
                    if this
                        .shared
                        .consumed
                        .fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| {
                            n.checked_add(count as u64)
                        })
                        .is_err()
                    {
                        this.shared.fail(LinkEnd::CounterOverflow);
                        return Poll::Ready(Err(failed()));
                    }
                    this.shared.wake.notify_one();
                    if let Err(error) = this.shared.update(|h| {
                        if let Some(s) = h.stats.as_mut() {
                            s.last_app_read_millis = Some(h.elapsed_millis);
                        }
                    }) {
                        return Poll::Ready(Err(error));
                    }
                }
                Poll::Ready(Ok(()))
            }
            other => other,
        }
    }
}

impl AsyncWrite for LinkIo {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        if let Err(error) = this.shared.check() {
            return Poll::Ready(Err(error));
        }
        if this.fin_enqueued {
            return Poll::Ready(Err(ended_error(LinkEnd::PeerEof)));
        }
        if bytes.is_empty() {
            return Poll::Ready(Ok(0));
        }
        let wanted = bytes.len().min(FRAME);
        let capacity = this
            .send
            .with_mut(|send| {
                send.reserve_capacity(wanted);
                if send.capacity() > 0 {
                    return Poll::Ready(Ok(send.capacity()));
                }
                match send.poll_capacity(cx) {
                    Poll::Pending => Poll::Pending,
                    Poll::Ready(Some(Ok(capacity))) => Poll::Ready(Ok(capacity)),
                    Poll::Ready(_) => Poll::Ready(Err(failed())),
                }
            })
            .unwrap_or_else(|| Poll::Ready(Err(failed())));
        if let Err(error) = this.shared.update(|h| {
            if let Some(s) = h.stats.as_mut() {
                s.send_capacity_blocked = capacity.is_pending();
            }
        }) {
            return Poll::Ready(Err(error));
        }
        let capacity = match capacity {
            Poll::Pending => return Poll::Pending,
            Poll::Ready(Err(e)) => {
                this.shared.fail(LinkEnd::Protocol);
                return Poll::Ready(Err(e));
            }
            Poll::Ready(Ok(n)) => n,
        };
        let count = capacity.min(wanted);
        let Some(total) = this.data_enqueued.checked_add(count as u64) else {
            this.shared.fail(LinkEnd::CounterOverflow);
            return Poll::Ready(Err(failed()));
        };
        let sent = this
            .send
            .with_mut(|send| {
                send.send_data(Bytes::copy_from_slice(&bytes[..count]), false)
                    .map_err(|_| failed())
            })
            .unwrap_or_else(|| Err(failed()));
        if let Err(e) = sent {
            this.shared.fail(LinkEnd::Protocol);
            return Poll::Ready(Err(e));
        }
        this.data_enqueued = total;
        if let Err(error) = this.shared.update(|h| {
            if let Some(s) = h.stats.as_mut() {
                s.app_write_bytes = total;
                s.last_app_write_millis = Some(h.elapsed_millis);
            }
        }) {
            return Poll::Ready(Err(error));
        }
        Poll::Ready(Ok(count))
    }
    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if let Err(error) = this.shared.check() {
            return Poll::Ready(Err(error));
        }
        this.flush.poll_flushed(cx, this.data_enqueued, false)
    }
    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        if let Err(error) = this.shared.check() {
            return Poll::Ready(Err(error));
        }
        if !this.fin_enqueued {
            let result = this
                .send
                .with_mut(|send| send.send_data(Bytes::new(), true).map_err(|_| failed()))
                .unwrap_or_else(|| Err(failed()));
            if let Err(e) = result {
                this.shared.fail(LinkEnd::Protocol);
                return Poll::Ready(Err(e));
            }
            this.fin_enqueued = true;
            if let Err(error) = this
                .shared
                .update(|health| health.phase = LinkPhase::Draining)
            {
                return Poll::Ready(Err(error));
            }
        }
        let result = this.flush.poll_flushed(cx, this.data_enqueued, true);
        if matches!(result, Poll::Ready(Ok(()))) {
            this.shared.local_fin_flushed.store(true, Ordering::Release);
            this.shared.wake.notify_one();
        }
        result
    }
}
impl Drop for LinkIo {
    fn drop(&mut self) {
        self.shared.stop.send_replace(true);
    }
}

type Connection = Pin<Box<dyn Future<Output = io::Result<()>> + Send>>;

struct DriverGuard(Arc<Shared>);
impl Drop for DriverGuard {
    fn drop(&mut self) {
        self.0.observation.alive.store(false, Ordering::Release);
        self.0.observation.ended.send_replace(true);
        self.0.wake.notify_waiters();
    }
}

fn make_shared(tuple: TcpTuple) -> Result<Arc<Shared>> {
    let id = NEXT_CONNECTION
        .fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| n.checked_add(1))
        .map_err(|_| Error::LinkDriverFailed)?;
    let (revision, _) = watch::channel(0);
    let (ended, _) = watch::channel(false);
    let (stop, _) = watch::channel(false);
    let observation = Arc::new(Observation {
        health: Mutex::new(LinkHealth {
            protocol: LinkProtocol::FramedV2,
            identity: Some(LinkIdentity {
                connection: id,
                tcp: tuple,
            }),
            sample_sequence: 0,
            elapsed_millis: 0,
            phase: LinkPhase::Open,
            probe: ProbeState::AwaitingFirstReply,
            last_probe_sequence: None,
            last_probe_enqueued_millis: None,
            last_reply_sequence: None,
            last_reply_millis: None,
            last_round_trip_micros: None,
            stats: Some(LinkStats {
                app_read_bytes: 0,
                app_write_bytes: 0,
                last_app_read_millis: None,
                last_app_write_millis: None,
                receive_pending_bytes: 0,
                send_capacity_blocked: false,
                transport_write_blocked: false,
            }),
            end: None,
        }),
        revision,
        ended,
        alive: AtomicBool::new(true),
        failed: AtomicBool::new(false),
    });
    Ok(Arc::new(Shared {
        observation,
        start: Instant::now(),
        received: AtomicU64::new(0),
        consumed: AtomicU64::new(0),
        fatal: AtomicU8::new(0),
        wake: Notify::new(),
        stop,
        local_fin_flushed: AtomicBool::new(false),
    }))
}

fn start(
    connection: Connection,
    receive: Quiet<h2::RecvStream>,
    send: Quiet<h2::SendStream<Bytes>>,
    ping: Quiet<h2::PingPong>,
    flush: FlushMonitor,
    tuple: TcpTuple,
    policy: ProbePolicy,
) -> Result<Link> {
    let shared = make_shared(tuple)?;
    let (reader, writer) = tokio::io::duplex(WINDOW as usize);
    let monitor = LinkMonitor {
        observation: shared.observation.clone(),
    };
    let task_shared = shared.clone();
    let driver_flush = flush.clone();
    let task = tokio::spawn(async move {
        let guard = DriverGuard(task_shared.clone());
        let result = driver(
            connection,
            receive,
            writer,
            ping,
            task_shared,
            policy,
            driver_flush,
        )
        .await;
        drop(guard);
        result
    });
    Ok(Link {
        io: LinkIo {
            receive: reader,
            send,
            flush,
            shared: shared.clone(),
            data_enqueued: 0,
            fin_enqueued: false,
        },
        owner: LinkOwner {
            stop: shared.stop.clone(),
            task: Some(task),
        },
        monitor,
    })
}

struct Pump {
    receive: Quiet<h2::RecvStream>,
    writer: Option<DuplexStream>,
    frame: Option<Bytes>,
    offset: usize,
    credited: u64,
    eof: bool,
    done: bool,
}

impl Pump {
    fn credit(&mut self, shared: &Shared) -> io::Result<()> {
        let consumed = shared.consumed.load(Ordering::Acquire);
        let received = shared.received.load(Ordering::Acquire);
        if consumed > received {
            return Err(shared.fail_observation());
        }
        let delta = consumed
            .checked_sub(self.credited)
            .ok_or_else(|| shared.fail_observation())?;
        if delta != 0 {
            let count = usize::try_from(delta).map_err(|_| shared.fail_observation())?;
            self.receive
                .with_mut(|receive| {
                    receive
                        .flow_control()
                        .release_capacity(count)
                        .map_err(|_| failed())
                })
                .unwrap_or_else(|| Err(failed()))?;
            self.credited = consumed;
        }
        Ok(())
    }
    // Retained frame/offset makes interruption by the driver's other branches
    // safe. No discarded write_all future can duplicate or lose partial bytes.
    fn poll(&mut self, cx: &mut Context<'_>, shared: &Shared) -> Poll<io::Result<()>> {
        if let Err(error) = self.credit(shared) {
            return Poll::Ready(Err(error));
        }
        if self.done {
            return Poll::Ready(Ok(()));
        }
        for _ in 0..32 {
            if let Some(frame) = self.frame.as_ref() {
                let Some(writer) = self.writer.as_mut() else {
                    return Poll::Ready(Err(failed()));
                };
                match Pin::new(writer).poll_write(cx, &frame[self.offset..]) {
                    Poll::Pending => return Poll::Pending,
                    Poll::Ready(Err(e)) => return Poll::Ready(Err(e)),
                    Poll::Ready(Ok(0)) => return Poll::Ready(Err(failed())),
                    Poll::Ready(Ok(n)) => {
                        self.offset += n;
                        if self.offset != frame.len() {
                            continue;
                        }
                        self.frame = None;
                        self.offset = 0;
                    }
                }
            }
            if self.eof {
                let trailers = self
                    .receive
                    .with_mut(|receive| {
                        receive.poll_trailers(cx).map(|result| match result {
                            Ok(None) => Ok(()),
                            Ok(Some(_)) => Err(failed()),
                            Err(error) => Err(h2_error(error)),
                        })
                    })
                    .unwrap_or_else(|| Poll::Ready(Err(failed())));
                match trailers {
                    Poll::Pending => return Poll::Pending,
                    Poll::Ready(Ok(())) => {}
                    Poll::Ready(_) => return Poll::Ready(Err(failed())),
                }
                // Dropping the writer preserves buffered bytes and provides EOF
                // only once the application's reader has consumed them.
                drop(self.writer.take());
                self.done = true;
                if let Err(e) = shared.update(|h| h.phase = LinkPhase::Draining) {
                    return Poll::Ready(Err(e));
                }
                return Poll::Ready(Ok(()));
            }
            let next = self
                .receive
                .with_mut(|receive| {
                    receive
                        .poll_data(cx)
                        .map(|result| result.map(|r| r.map_err(h2_error)))
                })
                .unwrap_or_else(|| Poll::Ready(Some(Err(failed()))));
            match next {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(Some(Err(e))) => return Poll::Ready(Err(e)),
                Poll::Ready(None) => self.eof = true,
                Poll::Ready(Some(Ok(frame))) => {
                    if frame.len() > FRAME {
                        return Poll::Ready(Err(failed()));
                    }
                    if frame.is_empty() {
                        continue;
                    }
                    if shared
                        .received
                        .fetch_update(Ordering::AcqRel, Ordering::Acquire, |n| {
                            n.checked_add(frame.len() as u64)
                        })
                        .is_err()
                    {
                        shared.fail(LinkEnd::CounterOverflow);
                        return Poll::Ready(Err(failed()));
                    }
                    // Published before any bytes can become observable to reader.
                    self.frame = Some(frame);
                    if let Err(e) = shared.update(|_| {}) {
                        return Poll::Ready(Err(e));
                    }
                }
            }
        }
        cx.waker().wake_by_ref();
        Poll::Pending
    }
}

async fn driver(
    mut connection: Connection,
    receive: Quiet<h2::RecvStream>,
    writer: DuplexStream,
    mut ping: Quiet<h2::PingPong>,
    shared: Arc<Shared>,
    policy: ProbePolicy,
    mut flush: FlushMonitor,
) -> Result<LinkOutcome> {
    let mut pump = Pump {
        receive,
        writer: Some(writer),
        frame: None,
        offset: 0,
        credited: 0,
        eof: false,
        done: false,
    };
    let mut stop = shared.stop.subscribe();
    let mut connection_done = false;
    let mut drain_deadline = None;
    let mut pending: Option<(u64, Instant)> = None;
    let mut sequence = 0_u64;
    let mut next_probe = Instant::now() + policy.interval;
    let mut end = loop {
        let notified = shared.wake.notified();
        tokio::pin!(notified);
        notified.as_mut().enable();
        if shared.observation.failed.load(Ordering::Acquire) {
            break LinkEnd::Io;
        }
        if let Some(end) = reason(shared.fatal.load(Ordering::Acquire)) {
            break end;
        }
        if *stop.borrow() {
            break LinkEnd::LocalStop;
        }
        if pump.credit(&shared).is_err() {
            break LinkEnd::Protocol;
        }
        let consumed = shared.consumed.load(Ordering::Acquire);
        let received = shared.received.load(Ordering::Acquire);
        if pump.done
            && consumed == received
            && (connection_done || shared.local_fin_flushed.load(Ordering::Acquire))
        {
            break LinkEnd::PeerEof;
        }
        let now = Instant::now();
        if drain_deadline.is_some_and(|deadline| now >= deadline) {
            break LinkEnd::LocalDataStall;
        }
        let probe_deadline = pending
            .map(|(_, started)| started + policy.response_timeout)
            .unwrap_or(next_probe);
        let timer_deadline = if connection_done {
            drain_deadline.unwrap_or(probe_deadline)
        } else {
            drain_deadline.map_or(probe_deadline, |d| d.min(probe_deadline))
        };
        tokio::select! {
            _ = stop.changed() => {},
            _ = &mut notified => {},
            result = poll_fn(|cx| {
                let result = connection.as_mut().poll(cx);
                if shared.write_pending(observed_pending(&mut flush)).is_err() { return Poll::Ready(Err(failed())); }
                result
            }), if !connection_done => {
                if let Err(error) = result { break if error.kind() == io::ErrorKind::InvalidData { LinkEnd::Protocol } else { LinkEnd::Io }; }
                connection_done = true;
                drain_deadline = Some(Instant::now() + policy.response_timeout);
                if shared.update(|h| h.phase = LinkPhase::Draining).is_err() { break reason(shared.fatal.load(Ordering::Acquire)).unwrap_or(LinkEnd::Io); }
            },
            result = poll_fn(|cx| pump.poll(cx, &shared)), if !pump.done => {
                if let Err(error) = result {
                    break reason(shared.fatal.load(Ordering::Acquire)).unwrap_or_else(|| {
                        if error.kind() == io::ErrorKind::InvalidData { LinkEnd::Protocol } else { LinkEnd::Io }
                    });
                }
            },
            result = poll_fn(|cx| ping.with_mut(|p| p.poll_pong(cx).map(|r| r.map(|_| ()).map_err(|_| failed()))).unwrap_or_else(|| Poll::Ready(Err(failed())))), if pending.is_some() && !connection_done => {
                if result.is_err() { break LinkEnd::Protocol; }
                let Some((seq, started)) = pending.take() else { break LinkEnd::Protocol; };
                let Ok(rtt) = u64::try_from(started.elapsed().as_micros()) else { break LinkEnd::CounterOverflow; };
                if shared.update(|h| { h.probe = ProbeState::ReplyObserved; h.last_reply_sequence = Some(seq); h.last_reply_millis = Some(h.elapsed_millis); h.last_round_trip_micros = Some(rtt); }).is_err() { break reason(shared.fatal.load(Ordering::Acquire)).unwrap_or(LinkEnd::Io); }
                next_probe = Instant::now() + policy.interval;
            },
            _ = tokio::time::sleep_until(timer_deadline) => {
                if drain_deadline.is_some_and(|d| Instant::now() >= d) { break LinkEnd::LocalDataStall; }
                if connection_done { continue; }
                if pending.is_some() { break LinkEnd::ProbeTimeout; }
                let Some(seq) = sequence.checked_add(1) else { break LinkEnd::CounterOverflow; };
                let started = Instant::now();
                let Ok(enqueued_millis) = elapsed(shared.start) else { break LinkEnd::CounterOverflow; };
                let sent = ping.with_mut(|p| p.send_ping(h2::Ping::opaque()).map_err(|_| failed())).unwrap_or_else(|| Err(failed()));
                if sent.is_err() { break LinkEnd::Protocol; }
                sequence = seq;
                pending = Some((seq, started));
                if shared.update(|h| { h.probe = ProbeState::Pending; h.last_probe_sequence = Some(seq); h.last_probe_enqueued_millis = Some(enqueued_millis); }).is_err() { break reason(shared.fatal.load(Ordering::Acquire)).unwrap_or(LinkEnd::Io); }
            },
        }
    };
    // Keep completed stream/ping references alive while the actual connection
    // performs its codec/TLS shutdown; publish only after every owner drops.
    if end == LinkEnd::PeerEof && !connection_done {
        end = tokio::select! {
            biased;
            _ = async {
                if !*stop.borrow_and_update() { let _ = stop.changed().await; }
            } => LinkEnd::LocalStop,
            result = tokio::time::timeout(policy.response_timeout, poll_fn(|cx| {
                let result = connection.as_mut().poll(cx);
                if shared.write_pending(observed_pending(&mut flush)).is_err() {
                    return Poll::Ready(Err(failed()));
                }
                result
            })) => match result {
                Ok(Ok(())) => LinkEnd::PeerEof,
                Ok(Err(error)) if error.kind() == io::ErrorKind::InvalidData => LinkEnd::Protocol,
                Ok(Err(_)) => LinkEnd::Io,
                Err(_) => LinkEnd::LocalDataStall,
            },
        };
    }
    if end != LinkEnd::PeerEof && !shared.observation.failed.load(Ordering::Acquire) {
        shared.fail(end);
    }
    // Close every actual network/h2 reference before publishing terminal facts.
    drop(connection);
    drop(pump);
    drop(ping);
    if shared.observation.failed.load(Ordering::Acquire) {
        return Err(Error::LinkDriverFailed);
    }
    shared.finish(end)
}

fn client_builder() -> h2::client::Builder {
    quiet::call(|| {
        let mut builder = h2::client::Builder::new();
        builder
            .initial_window_size(WINDOW)
            .initial_connection_window_size(WINDOW)
            .max_frame_size(FRAME as u32)
            .max_header_list_size(2048)
            .header_table_size(0)
            .max_concurrent_streams(1)
            .initial_max_send_streams(1)
            .enable_push(false)
            .max_concurrent_reset_streams(1)
            .max_local_error_reset_streams(Some(1))
            .max_pending_accept_reset_streams(1)
            .max_send_buffer_size(65_536);
        builder
    })
}
fn server_builder() -> h2::server::Builder {
    quiet::call(|| {
        let mut builder = h2::server::Builder::new();
        builder
            .initial_window_size(WINDOW)
            .initial_connection_window_size(WINDOW)
            .max_frame_size(FRAME as u32)
            .max_header_list_size(2048)
            .header_table_size(0)
            .max_concurrent_streams(1)
            .max_concurrent_reset_streams(1)
            .max_local_error_reset_streams(Some(1))
            .max_pending_accept_reset_streams(1)
            .max_send_buffer_size(65_536);
        builder
    })
}

/// Crate-private: the caller must supply its real authenticated stream and tuple.
pub(crate) async fn client<S>(verified: S, tuple: TcpTuple, policy: ProbePolicy) -> Result<Link>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let (io, flush) = TrackingIo::new(verified, Side::Client);
    let builder = Quiet::new(client_builder());
    let handshake = builder
        .with(|b| b.handshake::<_, Bytes>(io))
        .ok_or(Error::LinkDriverFailed)?;
    let (mut sender, mut connection) =
        QuietFuture::new(handshake, |r: std::result::Result<_, h2::Error>| {
            r.map(|(s, c)| (Quiet::new(s), Quiet::new(c)))
                .map_err(|_| Error::ProtocolMismatch)
        })
        .await?;
    let uri = http::Uri::builder()
        .authority(AUTHORITY)
        .build()
        .map_err(|_| Error::ProtocolMismatch)?;
    let request = http::Request::builder()
        .method(http::Method::CONNECT)
        .uri(uri)
        .body(())
        .map_err(|_| Error::ProtocolMismatch)?;
    let (response, send) = sender
        .with_mut(|s| {
            s.send_request(request, false)
                .map(|(r, s)| {
                    (
                        QuietFuture::new(
                            r,
                            |r: std::result::Result<http::Response<h2::RecvStream>, h2::Error>| {
                                r.map(|r| {
                                    let valid = r.status() == http::StatusCode::OK
                                        && r.headers().is_empty()
                                        && r.body().stream_id().as_u32() == 1
                                        && !r.body().is_end_stream();
                                    if valid {
                                        Ok(Quiet::new(r.into_body()))
                                    } else {
                                        drop(r);
                                        Err(Error::ProtocolMismatch)
                                    }
                                })
                                .map_err(|_| Error::ProtocolMismatch)
                                .and_then(|r| r)
                            },
                        ),
                        Quiet::new(s),
                    )
                })
                .map_err(|_| Error::ProtocolMismatch)
        })
        .ok_or(Error::LinkDriverFailed)??;
    tokio::pin!(response);
    let receive = tokio::select! {
        result = &mut response => result?,
        _ = poll_fn(|cx| connection.with_mut(|c| Pin::new(c).poll(cx).map(|r| r.map_err(h2_error))).unwrap_or_else(|| Poll::Ready(Err(failed())))) => return Err(Error::ProtocolMismatch),
    };
    drop(sender);
    let ping = connection
        .with_mut(|c| c.ping_pong().map(Quiet::new))
        .flatten()
        .ok_or(Error::LinkDriverFailed)?;
    let connection: Connection = Box::pin(poll_fn(move |cx| {
        connection
            .with_mut(|c| Pin::new(c).poll(cx).map(|r| r.map_err(h2_error)))
            .unwrap_or_else(|| Poll::Ready(Err(failed())))
    }));
    start(connection, receive, send, ping, flush, tuple, policy)
}

/// Crate-private: TLS authentication and principal mapping precede this factory.
pub(crate) async fn server<S>(verified: S, tuple: TcpTuple, policy: ProbePolicy) -> Result<Link>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let (io, flush) = TrackingIo::new(verified, Side::Server);
    let builder = Quiet::new(server_builder());
    let handshake = builder
        .with(|b| b.handshake::<_, Bytes>(io))
        .ok_or(Error::LinkDriverFailed)?;
    let mut connection = QuietFuture::new(handshake, |r: std::result::Result<_, h2::Error>| {
        r.map(Quiet::new).map_err(|_| Error::ProtocolMismatch)
    })
    .await?;
    let (receive, send) = poll_fn(|cx| {
        connection
            .with_mut(|c| {
                c.poll_accept(cx).map(|r| match r {
                    Some(Ok((request, mut respond))) => {
                        let valid = request.method() == http::Method::CONNECT
                            && request
                                .uri()
                                .authority()
                                .is_some_and(|a| a.as_str() == AUTHORITY)
                            && request.uri().scheme().is_none()
                            && request.uri().path_and_query().is_none()
                            && request.headers().is_empty()
                            && request.body().stream_id().as_u32() == 1
                            && !request.body().is_end_stream();
                        if !valid {
                            return Err(Error::ProtocolMismatch);
                        }
                        let response = http::Response::builder()
                            .status(200)
                            .body(())
                            .map_err(|_| Error::ProtocolMismatch)?;
                        let send = respond
                            .send_response(response, false)
                            .map(Quiet::new)
                            .map_err(|_| Error::ProtocolMismatch)?;
                        Ok((Quiet::new(request.into_body()), send))
                    }
                    _ => Err(Error::ProtocolMismatch),
                })
            })
            .unwrap_or_else(|| Poll::Ready(Err(Error::LinkDriverFailed)))
    })
    .await?;
    let ping = connection
        .with_mut(|c| c.ping_pong().map(Quiet::new))
        .flatten()
        .ok_or(Error::LinkDriverFailed)?;
    // This fixed protocol admits exactly the already-open CONNECT stream1.
    // Announce no future streams while both halves are still active, so h2
    // finishes its grace exchange before idle EOF rather than racing it.
    connection
        .with_mut(|c| c.graceful_shutdown())
        .ok_or(Error::LinkDriverFailed)?;
    let connection: Connection = Box::pin(poll_fn(move |cx| {
        connection
            .with_mut(|c| match c.poll_accept(cx) {
                Poll::Pending => Poll::Pending,
                Poll::Ready(None) => Poll::Ready(Ok(())),
                Poll::Ready(Some(Err(error))) => Poll::Ready(Err(h2_error(error))),
                Poll::Ready(Some(Ok(_))) => Poll::Ready(Err(failed())),
            })
            .unwrap_or_else(|| Poll::Ready(Err(failed())))
    }));
    start(connection, receive, send, ping, flush, tuple, policy)
}

#[cfg(test)]
mod tests;

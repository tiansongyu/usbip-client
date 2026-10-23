//! Managed IMPORT admission and observations. No monitor owns USB capabilities.

use rustusb_core::{DeviceKey, Error, Result, server_session::*};
use std::{
    collections::BTreeMap,
    sync::{
        Arc, Mutex, OnceLock, Weak,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::Instant,
};
use tokio::sync::watch;

const MAX_RECORDS: usize = 64;
const MAX_ACTIVE: usize = 16;
const MAX_SESSION_EVENTS: usize = 8;

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Admission {
    Pending,
    Admitted,
    Rejected,
}
#[derive(Clone, Copy)]
pub(crate) struct Signal {
    pub admission: Admission,
    pub stop: bool,
}

pub(crate) struct Counters {
    rx: AtomicU64,
    tx: AtomicU64,
    saturated: AtomicBool,
    enabled: AtomicBool,
    start: OnceLock<Instant>,
}
impl Counters {
    pub(crate) fn new() -> Arc<Self> {
        Arc::new(Self {
            rx: AtomicU64::new(0),
            tx: AtomicU64::new(0),
            saturated: AtomicBool::new(false),
            enabled: AtomicBool::new(false),
            start: OnceLock::new(),
        })
    }
    pub(crate) fn enable(&self) {
        self.enabled.store(true, Ordering::Release);
    }
    fn add(&self, counter: &AtomicU64, count: usize) {
        if !self.enabled.load(Ordering::Acquire) {
            return;
        }
        let amount = u64::try_from(count).unwrap_or(u64::MAX);
        let mut old = counter.load(Ordering::Relaxed);
        loop {
            let new = old.saturating_add(amount);
            match counter.compare_exchange_weak(old, new, Ordering::Relaxed, Ordering::Relaxed) {
                Ok(_) => {
                    if old.checked_add(amount).is_none() {
                        self.saturated.store(true, Ordering::Relaxed);
                    }
                    break;
                }
                Err(value) => old = value,
            }
        }
    }
    pub(crate) fn received(&self, count: usize) {
        self.add(&self.rx, count);
    }
    pub(crate) fn sent(&self, count: usize) {
        self.add(&self.tx, count);
    }
    pub(crate) fn snapshot(&self) -> ServerSessionMetrics {
        ServerSessionMetrics {
            app_rx_bytes: self.rx.load(Ordering::Relaxed),
            app_tx_bytes: self.tx.load(Ordering::Relaxed),
            saturated: self.saturated.load(Ordering::Relaxed),
            elapsed_millis: self.start.get().map_or(0, |start| {
                u64::try_from(start.elapsed().as_millis()).unwrap_or(u64::MAX)
            }),
        }
    }
}
struct Entry {
    snapshot: ServerSessionSnapshot,
    metrics: Arc<Counters>,
    signal: watch::Sender<Signal>,
    events: Vec<ServerSessionEvent>,
}
struct State {
    entries: BTreeMap<ServerSessionId, Entry>,
    next_id: u64,
    revision: u64,
    pruned_through: u64,
    bound: bool,
    accepting: bool,
}
struct Shared {
    state: Mutex<State>,
    changed: watch::Sender<u64>,
    alive: watch::Sender<bool>,
    stopping: watch::Sender<bool>,
}
impl Shared {
    fn lock(&self) -> Result<std::sync::MutexGuard<'_, State>> {
        self.state.lock().map_err(|_| {
            Error::State("server monitor lock poisoned; cleanup remains supervised".into())
        })
    }
    fn stop_all(&self) {
        self.stopping.send_replace(true);
        if let Ok(mut state) = self.state.lock() {
            state.accepting = false;
            for entry in state.entries.values_mut() {
                if entry.snapshot.cleanup.is_none() {
                    entry.snapshot.stop_requested = true;
                    entry.signal.send_modify(|signal| signal.stop = true);
                }
            }
            self.changed.send_replace(state.revision);
        }
    }
}
struct Observer {
    shared: Weak<Shared>,
}
impl Drop for Observer {
    fn drop(&mut self) {
        if let Some(shared) = self.shared.upgrade() {
            shared.alive.send_replace(false);
            shared.stop_all();
        }
    }
}

/// Application-side bounded observation/admission handle, created before managed bind.
/// Keep a clone while the service runs. Dropping the last observer requests cooperative stop.
#[derive(Clone)]
pub struct ServerMonitor {
    shared: Arc<Shared>,
    observer: Arc<Observer>,
}
impl Default for ServerMonitor {
    fn default() -> Self {
        Self::new()
    }
}
impl ServerMonitor {
    /// Create a single-bind monitor with 16 active and 64 total retained attempt slots.
    /// Terminal slots are not reused until acknowledged; admission fails when full.
    pub fn new() -> Self {
        let (changed, _) = watch::channel(0);
        let (alive, _) = watch::channel(true);
        let (stopping, _) = watch::channel(false);
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                entries: BTreeMap::new(),
                next_id: 1,
                revision: 0,
                pruned_through: 0,
                bound: false,
                accepting: false,
            }),
            changed,
            alive,
            stopping,
        });
        Self {
            observer: Arc::new(Observer {
                shared: Arc::downgrade(&shared),
            }),
            shared,
        }
    }
    /// Return a control handle that owns neither tasks, observations nor device capabilities.
    pub fn control(&self) -> ServerControl {
        ServerControl {
            shared: Arc::downgrade(&self.shared),
        }
    }
    /// Read all active and unacknowledged terminal attempts with current counters.
    pub fn snapshot(&self) -> Result<ServerMonitorSnapshot> {
        let state = self.shared.lock()?;
        let sessions = state
            .entries
            .values()
            .map(|entry| {
                let mut snapshot = entry.snapshot.clone();
                if snapshot.cleanup.is_none() {
                    snapshot.metrics = entry.metrics.snapshot();
                }
                snapshot
            })
            .collect();
        Ok(ServerMonitorSnapshot {
            revision: state.revision,
            accepting: state.accepting,
            sessions,
        })
    }
    /// Read at most 64 retained events. Acknowledged history loss is always explicit.
    pub fn events_after(&self, after: u64, limit: usize) -> Result<ServerEventPage> {
        if !(1..=64).contains(&limit) {
            return Err(Error::InvalidInput(
                "monitor page limit must be 1..64".into(),
            ));
        }
        let state = self.shared.lock()?;
        if after > state.revision {
            return Err(Error::InvalidInput(
                "monitor cursor is beyond the current revision".into(),
            ));
        }
        let mut events: Vec<_> = state
            .entries
            .values()
            .flat_map(|entry| entry.events.iter())
            .cloned()
            .collect();
        events.sort_by_key(|event| event.sequence);
        let oldest_available = events
            .first()
            .map_or(state.revision.saturating_add(1), |event| event.sequence);
        let events: Vec<_> = events
            .into_iter()
            .filter(|event| event.sequence > after)
            .take(limit)
            .collect();
        Ok(ServerEventPage {
            next_after: events.last().map_or(after, |event| event.sequence),
            oldest_available,
            gap: after < state.pruned_through,
            events,
        })
    }
    /// Subscribe to wakeups; always reread events/snapshot because watch coalesces updates.
    pub fn changes(&self) -> watch::Receiver<u64> {
        self.shared.changed.subscribe()
    }
    /// Cooperatively stop exactly one local attempt; true means newly requested, not cleaned.
    pub fn request_disconnect(&self, id: ServerSessionId) -> Result<bool> {
        self.control().request_disconnect(id)
    }
    /// Confirm that the application has durably admitted this Preparing attempt.
    /// Device ACLs are still atomically rechecked by the actual backend claim.
    pub fn admit(&self, id: ServerSessionId) -> Result<bool> {
        self.control().decision(id, Admission::Admitted)
    }
    /// Reject a pending admission without acquiring a device.
    pub fn reject(&self, id: ServerSessionId) -> Result<bool> {
        self.control().decision(id, Admission::Rejected)
    }
    /// Release a retained terminal slot only after persisting its exact terminal revision.
    pub fn acknowledge(&self, id: ServerSessionId, terminal_revision: u64) -> Result<bool> {
        let mut state = self.shared.lock()?;
        let Some(entry) = state.entries.get(&id) else {
            return Ok(false);
        };
        if entry.snapshot.cleanup.is_none() || entry.snapshot.revision != terminal_revision {
            return Err(Error::State(
                "only the exact terminal revision can be acknowledged".into(),
            ));
        }
        state.pruned_through = state.pruned_through.max(terminal_revision);
        state.entries.remove(&id);
        Ok(true)
    }
    pub(crate) fn writer(&self) -> MonitorWriter {
        MonitorWriter {
            shared: self.shared.clone(),
            observer: Arc::downgrade(&self.observer),
        }
    }
}

/// Weak, non-owning per-session control capability. The service must authorize its callers.
#[derive(Clone)]
pub struct ServerControl {
    shared: Weak<Shared>,
}
impl ServerControl {
    /// Set a sticky stop flag without waiting for the manager, network or cleanup.
    pub fn request_disconnect(&self, id: ServerSessionId) -> Result<bool> {
        let shared = self
            .shared
            .upgrade()
            .ok_or_else(|| Error::State("server monitor ended".into()))?;
        let mut state = shared.lock()?;
        let Some(entry) = state.entries.get_mut(&id) else {
            return Ok(false);
        };
        if entry.snapshot.cleanup.is_some() || entry.snapshot.stop_requested {
            return Ok(false);
        }
        entry.snapshot.stop_requested = true;
        entry.signal.send_modify(|signal| signal.stop = true);
        shared.changed.send_replace(state.revision);
        Ok(true)
    }
    fn decision(&self, id: ServerSessionId, decision: Admission) -> Result<bool> {
        let shared = self
            .shared
            .upgrade()
            .ok_or_else(|| Error::State("server monitor ended".into()))?;
        let state = shared.lock()?;
        let Some(entry) = state.entries.get(&id) else {
            return Ok(false);
        };
        if entry.snapshot.phase != ServerSessionPhase::Preparing || entry.snapshot.stop_requested {
            return Ok(false);
        }
        if entry.signal.borrow().admission != Admission::Pending {
            return Ok(false);
        }
        entry
            .signal
            .send_modify(|signal| signal.admission = decision);
        Ok(true)
    }
}

#[derive(Clone)]
pub(crate) struct MonitorWriter {
    shared: Arc<Shared>,
    observer: Weak<Observer>,
}
impl MonitorWriter {
    pub(crate) fn attach(&self) -> Result<()> {
        let mut state = self.shared.lock()?;
        if state.bound || !*self.shared.alive.borrow() {
            return Err(Error::State(
                "monitor is already bound or its observer ended".into(),
            ));
        }
        state.bound = true;
        state.accepting = true;
        Ok(())
    }
    pub(crate) fn observer(&self) -> Option<ServerMonitor> {
        self.observer.upgrade().map(|observer| ServerMonitor {
            shared: self.shared.clone(),
            observer,
        })
    }
    pub(crate) fn stopping(&self) -> watch::Receiver<bool> {
        self.shared.stopping.subscribe()
    }
    pub(crate) fn stop_all(&self) {
        self.shared.stop_all();
    }
    pub(crate) fn begin(
        &self,
        device_key: DeviceKey,
        principal: String,
        metrics: Arc<Counters>,
    ) -> Result<Tracked> {
        let mut state = self.shared.lock()?;
        if !state.accepting
            || !*self.shared.alive.borrow()
            || state.entries.len() >= MAX_RECORDS
            || state
                .entries
                .values()
                .filter(|e| e.snapshot.cleanup.is_none())
                .count()
                >= MAX_ACTIVE
        {
            return Err(Error::State(
                "managed IMPORT admission capacity is unavailable".into(),
            ));
        }
        let id = ServerSessionId::new(state.next_id)?;
        state.next_id = state
            .next_id
            .checked_add(1)
            .ok_or_else(|| Error::State("server session ID exhausted".into()))?;
        state.revision = state
            .revision
            .checked_add(1)
            .ok_or_else(|| Error::State("server monitor revision exhausted".into()))?;
        let _ = metrics.start.set(Instant::now());
        let snapshot = ServerSessionSnapshot {
            id,
            device_key,
            principal: Some(principal),
            phase: ServerSessionPhase::Preparing,
            stop_requested: false,
            revision: state.revision,
            metrics: metrics.snapshot(),
            operation_error: None,
            cleanup_error: None,
            cleanup: None,
        };
        let (signal, receiver) = watch::channel(Signal {
            admission: Admission::Pending,
            stop: false,
        });
        let event = ServerSessionEvent {
            sequence: state.revision,
            session: snapshot.clone(),
        };
        state.entries.insert(
            id,
            Entry {
                snapshot,
                metrics,
                signal,
                events: vec![event],
            },
        );
        self.shared.changed.send_replace(state.revision);
        Ok(Tracked {
            writer: self.clone(),
            id,
            signal: receiver,
            finished: false,
            touched: false,
        })
    }
}

pub(crate) struct Tracked {
    writer: MonitorWriter,
    pub(crate) id: ServerSessionId,
    pub(crate) signal: watch::Receiver<Signal>,
    finished: bool,
    touched: bool,
}
impl Tracked {
    pub(crate) async fn admission(&mut self) -> Result<()> {
        tokio::time::timeout(std::time::Duration::from_secs(5), async {
            loop {
                let signal = *self.signal.borrow_and_update();
                if signal.stop {
                    return Err(Error::State(
                        "managed IMPORT was stopped before admission".into(),
                    ));
                }
                match signal.admission {
                    Admission::Admitted => return Ok(()),
                    Admission::Rejected => {
                        return Err(Error::Permission(
                            "managed IMPORT admission rejected".into(),
                        ));
                    }
                    Admission::Pending => {}
                }
                self.signal
                    .changed()
                    .await
                    .map_err(|_| Error::State("managed IMPORT observer ended".into()))?;
            }
        })
        .await
        .map_err(|_| Error::Timeout("managed IMPORT durable admission".into()))?
    }
    pub(crate) fn phase(&mut self, phase: ServerSessionPhase) -> Result<()> {
        if phase == ServerSessionPhase::Claiming {
            self.touched = true;
        }
        self.update(phase, None, None, None)
    }
    fn update(
        &self,
        phase: ServerSessionPhase,
        operation_error: Option<ServerSessionError>,
        cleanup_error: Option<ServerSessionError>,
        cleanup: Option<ServerCleanup>,
    ) -> Result<()> {
        let mut state = self.writer.shared.lock()?;
        let revision = state
            .revision
            .checked_add(1)
            .ok_or_else(|| Error::State("server monitor revision exhausted".into()))?;
        let entry = state
            .entries
            .get_mut(&self.id)
            .ok_or_else(|| Error::State("live monitor session disappeared".into()))?;
        // Reserve the final observation independently of the application's speed.
        // Internal phase calls cannot consume the terminal slot or grow history.
        let limit = if cleanup.is_some() {
            MAX_SESSION_EVENTS
        } else {
            MAX_SESSION_EVENTS - 1
        };
        if entry.events.len() >= limit {
            return Err(Error::State(
                "bounded session event history exhausted".into(),
            ));
        }
        entry.snapshot.phase = phase;
        entry.snapshot.revision = revision;
        entry.snapshot.metrics = entry.metrics.snapshot();
        entry.snapshot.operation_error = operation_error;
        entry.snapshot.cleanup_error = cleanup_error;
        entry.snapshot.cleanup = cleanup;
        entry.events.push(ServerSessionEvent {
            sequence: revision,
            session: entry.snapshot.clone(),
        });
        state.revision = revision;
        self.writer.shared.changed.send_replace(revision);
        Ok(())
    }
    pub(crate) fn finish(
        &mut self,
        cleanup: ServerCleanup,
        operation: Option<ServerSessionError>,
        error: Option<ServerSessionError>,
    ) -> Result<()> {
        self.update(
            if cleanup == ServerCleanup::Unconfirmed {
                ServerSessionPhase::CleanupUnconfirmed
            } else {
                ServerSessionPhase::Closed
            },
            operation,
            error,
            Some(cleanup),
        )?;
        self.finished = true;
        Ok(())
    }
}
impl Drop for Tracked {
    fn drop(&mut self) {
        if !self.finished {
            let _ = self.finish(
                if self.touched {
                    ServerCleanup::Unconfirmed
                } else {
                    ServerCleanup::NotStarted
                },
                Some(ServerSessionError::State),
                self.touched.then_some(ServerSessionError::State),
            );
        }
    }
}

pub(crate) async fn stopped(receiver: &mut watch::Receiver<Signal>) {
    loop {
        if receiver.borrow_and_update().stop {
            return;
        }
        if receiver.changed().await.is_err() {
            return;
        }
    }
}
pub(crate) fn category(error: &Error) -> ServerSessionError {
    match error {
        Error::Io(_) => ServerSessionError::Io,
        Error::Timeout(_) => ServerSessionError::Timeout,
        Error::Permission(_) => ServerSessionError::Permission,
        Error::InvalidInput(_) | Error::Protocol(_) => ServerSessionError::Protocol,
        Error::Unsupported(_) => ServerSessionError::Unsupported,
        Error::State(_) | Error::Command(_) => ServerSessionError::State,
        Error::UnsafeDevice(_) => ServerSessionError::Permission,
    }
}

impl std::fmt::Debug for MonitorWriter {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("MonitorWriter([bounded registry])")
    }
}
pub(crate) async fn server_stopped(receiver: &mut watch::Receiver<bool>) {
    loop {
        if *receiver.borrow_and_update() {
            return;
        }
        if receiver.changed().await.is_err() {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn key() -> DeviceKey {
        DeviceKey::virtual_profile(rustusb_core::VirtualProfile::Hid)
    }
    #[tokio::test]
    async fn active_and_unacknowledged_terminal_capacity_cannot_overwrite_history() -> Result<()> {
        let monitor = ServerMonitor::new();
        let writer = monitor.writer();
        writer.attach()?;
        let mut active = Vec::new();
        for _ in 0..MAX_ACTIVE {
            active.push(writer.begin(key(), "verified-test-principal".into(), Counters::new())?);
        }
        assert!(writer.begin(key(), "x".into(), Counters::new()).is_err());
        let first = active[0].id;
        assert!(monitor.acknowledge(first, 1).is_err());
        for tracked in &mut active {
            tracked.finish(
                ServerCleanup::NotStarted,
                Some(ServerSessionError::AdmissionRejected),
                None,
            )?;
        }
        for _ in MAX_ACTIVE..MAX_RECORDS {
            let mut tracked = writer.begin(key(), "x".into(), Counters::new())?;
            tracked.finish(ServerCleanup::NotStarted, None, None)?;
        }
        assert_eq!(monitor.snapshot()?.sessions.len(), MAX_RECORDS);
        assert!(writer.begin(key(), "x".into(), Counters::new()).is_err());
        let first = monitor
            .snapshot()?
            .sessions
            .into_iter()
            .find(|s| s.id == first)
            .ok_or_else(|| Error::State("first terminal overwritten".into()))?;
        assert!(monitor.acknowledge(first.id, first.revision + 1).is_err());
        assert!(monitor.acknowledge(first.id, first.revision)?);
        let replacement = writer.begin(key(), "x".into(), Counters::new())?;
        assert!(replacement.id > first.id);
        assert!(monitor.events_after(0, 64)?.gap);
        assert!(monitor.events_after(0, 0).is_err());
        assert!(monitor.events_after(0, 65).is_err());
        assert!(monitor.events_after(u64::MAX, 1).is_err());
        Ok(())
    }
    #[tokio::test(start_paused = true)]
    async fn no_admission_times_out_and_observer_loss_cannot_admit() -> Result<()> {
        let monitor = ServerMonitor::new();
        let writer = monitor.writer();
        writer.attach()?;
        let mut attempt = writer.begin(key(), "x".into(), Counters::new())?;
        assert!(matches!(attempt.admission().await, Err(Error::Timeout(_))));
        attempt.finish(
            ServerCleanup::NotStarted,
            Some(ServerSessionError::Timeout),
            None,
        )?;
        let mut next = writer.begin(key(), "x".into(), Counters::new())?;
        let id = next.id;
        let control = monitor.control();
        drop(monitor);
        assert!(next.admission().await.is_err());
        assert!(!control.decision(id, Admission::Admitted)?);
        assert!(writer.begin(key(), "x".into(), Counters::new()).is_err());
        Ok(())
    }
    #[tokio::test]
    async fn phase_history_reserves_terminal_and_dropped_attempt_never_fakes_cleanup() -> Result<()>
    {
        let monitor = ServerMonitor::new();
        let writer = monitor.writer();
        writer.attach()?;
        let mut attempt = writer.begin(key(), "x".into(), Counters::new())?;
        let id = attempt.id;
        for _ in 1..MAX_SESSION_EVENTS - 1 {
            attempt.phase(ServerSessionPhase::Claiming)?;
        }
        assert!(attempt.phase(ServerSessionPhase::Connected).is_err());
        drop(attempt);
        let snapshot = monitor.snapshot()?;
        assert_eq!(snapshot.sessions[0].id, id);
        assert_eq!(
            snapshot.sessions[0].cleanup,
            Some(ServerCleanup::Unconfirmed)
        );
        assert_eq!(
            monitor.events_after(0, 64)?.events.len(),
            MAX_SESSION_EVENTS
        );
        Ok(())
    }
}

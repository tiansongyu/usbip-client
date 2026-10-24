use super::*;
use rustix::{
    event::{PollFd, PollFlags, Timespec, poll},
    io::Errno,
};
use rustusb_linux::{
    SysfsDiscovery,
    hotplug::{KernelUsbEvents, Notice, NotificationLoss},
    observation::ObservedDevice,
};
use serde::Serialize;
use std::{
    collections::BTreeMap,
    io,
    os::unix::net::UnixStream,
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};
use tokio::{
    sync::{oneshot, watch},
    task::JoinHandle,
};

const MAX_DEVICES: usize = 256;
const MAX_DEVICE_BYTES: usize = 48 * 1024;
const MAX_SNAPSHOT_BYTES: usize = 4 * 1024 * 1024;
const RECONCILE: Duration = Duration::from_secs(2);
const DEBOUNCE: Duration = Duration::from_millis(50);
const MIN_SCAN_INTERVAL: Duration = Duration::from_millis(100);

/// Quality of metadata, independent of device eligibility or connection health.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DiscoveryState {
    /// The source is bound, but no complete scan has succeeded or failed yet.
    Initializing,
    /// This complete scan succeeded; devices can still change after observation.
    Current,
    /// The latest scan failed. Any retained devices belong to the last success.
    Stale,
}

/// Fixed, payload-free scan failure; no path, serial or arbitrary error is logged.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DiscoveryFailure {
    /// An OS read failed; this is never converted into an empty inventory.
    Io,
    /// Current-user read permissions prevented enumeration.
    Permission,
    /// Device generation/address changed during the reads.
    Changing,
    /// A record, count or aggregate exceeds the finite publication budget.
    Capacity,
    /// Metadata could not be represented by the supported schema.
    Metadata,
}

/// Why a scan was requested; notification hints are not device facts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DiscoveryReason {
    /// First scan after the kernel socket was bound.
    Initial,
    /// At least one actual kernel USB notification was consumed.
    UsbHint,
    /// Periodic authoritative reconciliation, independent of notifications.
    Periodic,
    /// Loss, truncation or malformed trusted notification requires a fresh view.
    Resync,
}

/// Difference between two successful observations, without inferring physical cause.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DeviceChangeKind {
    /// Newly observed current address.
    Added,
    /// The successful scan no longer observed this address.
    Removed,
    /// Candidate identity or observational generation changed at this address.
    GenerationChanged,
    /// Metadata changed while the compared identity/stamp remained equal.
    MetadataChanged,
}

/// Small change index. Complete current records are in the accompanying snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct DeviceChange {
    /// Observed transition, not a hotplug cause or an authorization decision.
    pub kind: DeviceChangeKind,
    /// Candidate fingerprint from the relevant successful observation.
    pub device_id: String,
    /// Current address, never a persistent identity.
    pub bus_id: rustusb_core::BusId,
}

/// Counters as of this publication; these do not form durable event history.
#[derive(Debug, Clone, Default, Serialize)]
pub struct DiscoveryCounters {
    /// Complete accepted USB notification datagrams, not unique device actions.
    pub usb_hints: u64,
    /// Explicit notification loss/invalid trusted datagrams.
    pub resyncs: u64,
    /// Actual ENOBUFS receive results.
    pub queue_overflows: u64,
    /// Truncated datagrams from the accepted kernel source.
    pub truncated_datagrams: u64,
    /// Malformed complete datagrams from the accepted kernel source.
    pub malformed_datagrams: u64,
    /// Ignored non-USB or untrusted datagrams.
    pub ignored: u64,
    /// Actual completed scan attempts.
    pub scans: u64,
    /// Failed scan attempts, including publication capacity rejection.
    pub scan_errors: u64,
    /// A counter reached its representable limit.
    pub saturated: bool,
}

fn increment(value: &mut u64, saturated: &mut bool) {
    if let Some(next) = value.checked_add(1) {
        *value = next;
    } else {
        *saturated = true;
    }
}

/// Immutable bounded metadata. Absence of `devices` means no successful scan yet.
#[derive(Debug)]
pub struct DeviceSnapshot {
    /// Fresh observer epoch; revisions from different instances cannot be compared.
    pub instance: String,
    /// Monotonic publication revision within this instance.
    pub revision: u64,
    /// Current or explicitly retained stale metadata.
    pub state: DiscoveryState,
    /// Fixed scan failure; never arbitrary kernel environment or USB contents.
    pub failure: Option<DiscoveryFailure>,
    /// Trigger of this published scan.
    pub reason: DiscoveryReason,
    /// Milliseconds since this observer started, at the published scan's completion.
    pub sampled_ms: u64,
    /// Last complete successful observation; stale state never silently clears it.
    pub devices: Option<Arc<[ObservedDevice]>>,
    /// Changes only from successful scans; none are invented on failure.
    pub changes: Vec<DeviceChange>,
    /// Counter values as of this publication.
    pub counters: DiscoveryCounters,
    /// Actual kernel receive buffer, distinct from the requested size.
    pub receive_buffer_bytes: usize,
}

/// Latest view plus an explicit instruction to discard any assumed delta continuity.
#[derive(Debug)]
pub struct DeviceUpdate {
    /// Complete immutable metadata view; not a capability or cleanup proof.
    pub snapshot: Arc<DeviceSnapshot>,
    /// True on first delivery, revision gaps, notification loss or stale recovery.
    pub resync_required: bool,
}

/// A bounded latest-view subscription, without a socket, worker permit or USB owner.
pub struct DeviceSubscription {
    receiver: watch::Receiver<Option<Arc<DeviceSnapshot>>>,
    revision: Option<u64>,
}

impl DeviceSubscription {
    /// Wait for the next published view. Slow readers may skip revisions.
    ///
    /// Consumers must replace their view from `snapshot` when resynchronization is
    /// requested. Dropping a subscription does not stop the real monitor owner.
    pub async fn next(&mut self) -> Result<DeviceUpdate> {
        loop {
            let snapshot = self.receiver.borrow_and_update().clone();
            if let Some(snapshot) = snapshot
                && self.revision != Some(snapshot.revision)
            {
                let resync_required = self.revision.and_then(|value| value.checked_add(1))
                    != Some(snapshot.revision)
                    || snapshot.state == DiscoveryState::Stale
                    || matches!(
                        snapshot.reason,
                        DiscoveryReason::Initial | DiscoveryReason::Resync
                    );
                self.revision = Some(snapshot.revision);
                return Ok(DeviceUpdate {
                    snapshot,
                    resync_required,
                });
            }
            self.receiver
                .changed()
                .await
                .map_err(|_| Error::State("local device monitor ended".into()))?;
        }
    }
}

struct Stop {
    requested: AtomicBool,
    wake: UnixStream,
}

impl Stop {
    fn signal(&self) {
        if !self.requested.swap(true, Ordering::AcqRel) {
            // One nonblocking byte only. The sticky flag plus finite poll fallback
            // still stops the worker if the wake write itself fails.
            let _ = rustix::io::write(&self.wake, &[1]);
        }
    }
}

/// Actual read-only notification/scan owner. Call `shutdown` to observe its join.
///
/// Drop requests cooperative stop. It never cancels an in-flight filesystem read
/// or returns the actual worker permit before that worker finishes.
pub struct DeviceMonitor {
    stop: Arc<Stop>,
    worker: Option<JoinHandle<Result<()>>>,
    receiver: watch::Receiver<Option<Arc<DeviceSnapshot>>>,
}

impl DeviceMonitor {
    /// Bind an ordinary-user kernel source before initial enumeration.
    ///
    /// At most two actual local-discovery workers are admitted per process.
    /// This does not open USB device nodes, contact a helper or write sysfs.
    pub async fn open() -> Result<Self> {
        launch(WORKERS.clone(), || SysfsDiscovery::default().observe()).await
    }

    /// Subscribe to bounded latest metadata; consumers can share one real owner.
    pub fn subscribe(&self) -> DeviceSubscription {
        DeviceSubscription {
            receiver: self.receiver.clone(),
            revision: None,
        }
    }

    /// Request cooperation without claiming that any scan or worker has ended.
    pub fn request_stop(&self) {
        self.stop.signal();
    }

    /// Request stop and await the original accepted blocking owner.
    ///
    /// A permanently blocked OS read may delay this; no timeout fabricates cleanup.
    pub async fn shutdown(mut self) -> Result<()> {
        self.request_stop();
        if let Some(worker) = self.worker.take() {
            worker
                .await
                .map_err(|error| Error::State(format!("local device monitor worker: {error}")))??;
        }
        Ok(())
    }
}

impl Drop for DeviceMonitor {
    fn drop(&mut self) {
        self.request_stop();
    }
}

async fn launch(
    pool: Arc<Semaphore>,
    scanner: impl FnMut() -> Result<Vec<ObservedDevice>> + Send + 'static,
) -> Result<DeviceMonitor> {
    let permit = permit(pool)?;
    let (read, write) = UnixStream::pair()?;
    read.set_nonblocking(true)?;
    write.set_nonblocking(true)?;
    let stop = Arc::new(Stop {
        requested: AtomicBool::new(false),
        wake: write,
    });
    let owned_stop = stop.clone();
    let (sender, receiver) = watch::channel(None);
    let (ready, prepared) = oneshot::channel();
    let worker = tokio::task::spawn_blocking(move || {
        let _permit = permit;
        run(owned_stop, read, sender, ready, scanner)
    });
    let owner = DeviceMonitor {
        stop,
        worker: Some(worker),
        receiver,
    };
    if prepared.await.is_err() {
        owner.shutdown().await?;
        return Err(Error::State(
            "local device monitor ended during preparation".into(),
        ));
    }
    Ok(owner)
}

struct Views {
    instance: String,
    started: Instant,
    revision: u64,
    last: Option<Arc<DeviceSnapshot>>,
    counters: DiscoveryCounters,
    receive_buffer: usize,
}

fn bounded(
    devices: Vec<ObservedDevice>,
) -> std::result::Result<Vec<ObservedDevice>, DiscoveryFailure> {
    if devices.len() > MAX_DEVICES {
        return Err(DiscoveryFailure::Capacity);
    }
    let mut bytes = 0_usize;
    for device in &devices {
        let encoded = serde_json::to_vec(device).map_err(|_| DiscoveryFailure::Metadata)?;
        if encoded.len() > MAX_DEVICE_BYTES {
            return Err(DiscoveryFailure::Capacity);
        }
        bytes = bytes.saturating_add(encoded.len() + 1);
        if bytes > MAX_SNAPSHOT_BYTES {
            return Err(DiscoveryFailure::Capacity);
        }
    }
    Ok(devices)
}

fn failure(error: Error) -> DiscoveryFailure {
    match error {
        Error::Io(error) if error.kind() == io::ErrorKind::PermissionDenied => {
            DiscoveryFailure::Permission
        }
        Error::Io(_) => DiscoveryFailure::Io,
        Error::State(_) => DiscoveryFailure::Changing,
        _ => DiscoveryFailure::Metadata,
    }
}

fn changes(old: Option<&[ObservedDevice]>, new: &[ObservedDevice]) -> Vec<DeviceChange> {
    let old: BTreeMap<_, _> = old
        .unwrap_or_default()
        .iter()
        .map(|record| (record.device.bus_id.as_str(), record))
        .collect();
    let new: BTreeMap<_, _> = new
        .iter()
        .map(|record| (record.device.bus_id.as_str(), record))
        .collect();
    let mut result = Vec::new();
    for (bus, before) in &old {
        if !new.contains_key(bus) {
            result.push(DeviceChange {
                kind: DeviceChangeKind::Removed,
                device_id: before.device.id.clone(),
                bus_id: before.device.bus_id.clone(),
            });
        }
    }
    for (bus, after) in &new {
        let kind = match old.get(bus) {
            None => Some(DeviceChangeKind::Added),
            Some(before) if before.stamp != after.stamp || before.device.id != after.device.id => {
                Some(DeviceChangeKind::GenerationChanged)
            }
            Some(before) if before.device != after.device => {
                Some(DeviceChangeKind::MetadataChanged)
            }
            _ => None,
        };
        if let Some(kind) = kind {
            result.push(DeviceChange {
                kind,
                device_id: after.device.id.clone(),
                bus_id: after.device.bus_id.clone(),
            });
        }
    }
    result
}

impl Views {
    fn initializing(&mut self, sender: &watch::Sender<Option<Arc<DeviceSnapshot>>>) {
        let snapshot = Arc::new(DeviceSnapshot {
            instance: self.instance.clone(),
            revision: 0,
            state: DiscoveryState::Initializing,
            failure: None,
            reason: DiscoveryReason::Initial,
            sampled_ms: 0,
            devices: None,
            changes: Vec::new(),
            counters: self.counters.clone(),
            receive_buffer_bytes: self.receive_buffer,
        });
        self.last = Some(snapshot.clone());
        sender.send_replace(Some(snapshot));
    }

    fn publish(
        &mut self,
        sender: &watch::Sender<Option<Arc<DeviceSnapshot>>>,
        result: Result<Vec<ObservedDevice>>,
        mut reason: DiscoveryReason,
    ) -> Result<()> {
        increment(&mut self.counters.scans, &mut self.counters.saturated);
        let result = result.map_err(failure).and_then(bounded);
        let (state, failed, devices, changes) = match result {
            Ok(devices) => {
                let previous = self.last.as_ref().and_then(|view| view.devices.as_deref());
                let changed = changes(previous, &devices);
                if self
                    .last
                    .as_ref()
                    .is_some_and(|view| view.state == DiscoveryState::Stale)
                {
                    reason = DiscoveryReason::Resync;
                }
                (
                    DiscoveryState::Current,
                    None,
                    Some(Arc::<[ObservedDevice]>::from(devices)),
                    changed,
                )
            }
            Err(error) => {
                increment(&mut self.counters.scan_errors, &mut self.counters.saturated);
                (
                    DiscoveryState::Stale,
                    Some(error),
                    self.last.as_ref().and_then(|view| view.devices.clone()),
                    Vec::new(),
                )
            }
        };
        let same = self.last.as_ref().is_some_and(|view| {
            view.state == state && view.failure == failed && changes.is_empty()
        });
        if same && reason == DiscoveryReason::Periodic {
            return Ok(());
        }
        self.revision = self
            .revision
            .checked_add(1)
            .ok_or_else(|| Error::State("local discovery revision exhausted".into()))?;
        let snapshot = Arc::new(DeviceSnapshot {
            instance: self.instance.clone(),
            revision: self.revision,
            state,
            failure: failed,
            reason,
            sampled_ms: u64::try_from(self.started.elapsed().as_millis()).unwrap_or(u64::MAX),
            devices,
            changes,
            counters: self.counters.clone(),
            receive_buffer_bytes: self.receive_buffer,
        });
        self.last = Some(snapshot.clone());
        sender.send_replace(Some(snapshot));
        Ok(())
    }
}

fn run(
    stop: Arc<Stop>,
    wake: UnixStream,
    sender: watch::Sender<Option<Arc<DeviceSnapshot>>>,
    ready: oneshot::Sender<()>,
    mut scanner: impl FnMut() -> Result<Vec<ObservedDevice>>,
) -> Result<()> {
    let events = KernelUsbEvents::open()?;
    let mut views = Views {
        instance: std::fs::read_to_string("/proc/sys/kernel/random/uuid")?
            .trim_end()
            .to_owned(),
        started: Instant::now(),
        revision: 0,
        last: None,
        counters: DiscoveryCounters::default(),
        receive_buffer: events.receive_buffer_bytes(),
    };
    views.initializing(&sender);
    let _ = ready.send(());
    if stop.requested.load(Ordering::Acquire) {
        return Ok(());
    }
    let mut last_scan = Instant::now();
    views.publish(&sender, scanner(), DiscoveryReason::Initial)?;
    let mut periodic = Instant::now() + RECONCILE;
    let mut dirty: Option<(Instant, DiscoveryReason)> = None;
    loop {
        if stop.requested.load(Ordering::Acquire) {
            return Ok(());
        }
        let now = Instant::now();
        if dirty.is_some_and(|(at, _)| at <= now) || periodic <= now {
            let reason = dirty
                .take()
                .map(|(_, reason)| reason)
                .unwrap_or(DiscoveryReason::Periodic);
            last_scan = now;
            views.publish(&sender, scanner(), reason)?;
            periodic = Instant::now() + RECONCILE;
            continue;
        }
        let at = dirty.map(|(at, _)| at.min(periodic)).unwrap_or(periodic);
        let wait = at
            .saturating_duration_since(now)
            .min(Duration::from_secs(1));
        let wait = Timespec {
            tv_sec: wait.as_secs() as i64,
            tv_nsec: i64::from(wait.subsec_nanos()),
        };
        let mut polls = [
            PollFd::new(&events, PollFlags::IN),
            PollFd::new(&wake, PollFlags::IN),
        ];
        match poll(&mut polls, Some(&wait)) {
            Ok(_) => {}
            Err(Errno::INTR) => continue,
            Err(error) => return Err(io::Error::from(error).into()),
        }
        if stop.requested.load(Ordering::Acquire) {
            return Ok(());
        }
        if polls[1]
            .revents()
            .intersects(PollFlags::HUP | PollFlags::ERR | PollFlags::NVAL)
        {
            return Err(Error::State("local discovery wake channel failed".into()));
        }
        if polls[0]
            .revents()
            .intersects(PollFlags::HUP | PollFlags::NVAL)
        {
            return Err(Error::State("local USB notification source closed".into()));
        }
        if polls[0]
            .revents()
            .intersects(PollFlags::IN | PollFlags::ERR)
        {
            for _ in 0..64 {
                if stop.requested.load(Ordering::Acquire) {
                    return Ok(());
                }
                let Some(notice) = events.receive()? else {
                    break;
                };
                let reason = match notice {
                    Notice::Ignored => {
                        increment(&mut views.counters.ignored, &mut views.counters.saturated);
                        continue;
                    }
                    Notice::Usb => {
                        increment(&mut views.counters.usb_hints, &mut views.counters.saturated);
                        DiscoveryReason::UsbHint
                    }
                    Notice::Resync(cause) => {
                        increment(&mut views.counters.resyncs, &mut views.counters.saturated);
                        let counter = match cause {
                            NotificationLoss::QueueOverflow => &mut views.counters.queue_overflows,
                            NotificationLoss::Truncated => &mut views.counters.truncated_datagrams,
                            NotificationLoss::Malformed => &mut views.counters.malformed_datagrams,
                        };
                        increment(counter, &mut views.counters.saturated);
                        DiscoveryReason::Resync
                    }
                };
                let at = (Instant::now() + DEBOUNCE).max(last_scan + MIN_SCAN_INTERVAL);
                dirty = Some(match dirty {
                    Some((old, old_reason)) => (
                        old.min(at),
                        if old_reason == DiscoveryReason::Resync {
                            old_reason
                        } else {
                            reason
                        },
                    ),
                    None => (at, reason),
                });
            }
        }
    }
}

#[cfg(test)]
mod tests;

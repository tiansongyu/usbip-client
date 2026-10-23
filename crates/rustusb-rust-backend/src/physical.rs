//! Linux usbfs capabilities, supervised until physical requests are reaped.

mod observe;
mod safety;
mod worker;

use crate::{
    session::{
        ConnectionAccess, RegisteredDevice, ServingBackend, SessionLease, Started, TransferEvent,
        TransferId,
    },
    wire::{Completion, Submit},
};
use rustusb_core::{Device, DeviceKey, Error, PhysicalDeviceReceipt, Result};
use rustusb_protocol::RemoteDevice;
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    os::fd::OwnedFd,
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicBool, Ordering},
    },
    task::{Context, Poll},
    time::Duration,
};
use tokio::sync::{Notify, OwnedSemaphorePermit, Semaphore, mpsc, oneshot};
use tokio::task::{JoinHandle, JoinSet};

const BUFFER_BUDGET: usize = 16 * 1024 * 1024;
const MAX_REQUESTS: usize = 64;
const CLEANUP_DEADLINE: Duration = Duration::from_secs(10);

/// A helper-authorized capability for one particular Linux device generation.
/// The adapter never reopens a path derived from this metadata.
pub struct PhysicalSource {
    /// Already-open usbfs descriptor, transferred by the trusted helper.
    pub fd: OwnedFd,
    /// Safety-checked metadata from the same helper observation.
    pub device: Device,
    /// Node and sysfs generation that must still match the open capability.
    pub receipt: PhysicalDeviceReceipt,
}

/// Driver observation for one physical interface; no device strings or payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PhysicalDriver {
    /// USB interface number.
    pub interface: u8,
    /// Current kernel driver name, or an explicitly unbound interface.
    pub driver: Option<String>,
}

/// Cumulative physical completions; failed and partial transfers are counted.
#[derive(Debug, Clone, Default, Serialize)]
pub struct PhysicalCounters {
    /// Completed control requests, including host-state operations.
    pub controls: u64,
    /// Completed bulk IN requests.
    pub bulk_in: u64,
    /// Completed bulk OUT requests.
    pub bulk_out: u64,
    /// Completed interrupt IN requests.
    pub interrupt_in: u64,
    /// Completed interrupt OUT requests.
    pub interrupt_out: u64,
    /// Completions with a nonzero status.
    pub errors: u64,
    /// Actual bytes physically received, including partial errors/cancellation.
    pub bytes_in: u64,
    /// Actual bytes physically sent, including partial errors/cancellation.
    pub bytes_out: u64,
    /// RET_UNLINK results observed by the wire actor.
    pub unlinks: u64,
    /// Cancellations that won against physical completion.
    pub unlink_cancelled: u64,
}

/// Cached observed state; reading it never performs blocking USB or sysfs I/O.
#[derive(Debug, Clone, Serialize)]
pub struct PhysicalDeviceSnapshot {
    /// Ephemeral bus/port address of the explicitly authorized generation.
    pub bus_id: String,
    /// Helper-checked fingerprint; raw serial strings are omitted.
    pub device_id: String,
    /// available, claiming, leased, closing, quarantined, or gone.
    pub state: String,
    /// Ownership remains held while claiming, closing, or quarantined.
    pub leased: bool,
    /// Requests still holding memory until terminal and network retirement.
    pub pending_urbs: usize,
    /// Logical requests waiting for a physical endpoint.
    pub queued_urbs: usize,
    /// Submitted physical URBs not yet returned by REAPURB.
    pub usbfs_inflight: usize,
    /// Cumulative actual raw usbfs completions.
    pub usbfs_reaped: u64,
    /// Cumulative requests whose cancellation won.
    pub cancelled: u64,
    /// Last confirmed physical configuration value.
    pub configuration: u8,
    /// Original device descriptor EP0 size; never assumes full-speed 64B.
    pub ep0_max_packet_size: u16,
    /// Linux USB speed enumeration from the actual opened descriptor.
    pub speed: u32,
    /// Driver bindings observed before this session.
    pub original_drivers: Vec<PhysicalDriver>,
    /// Last independently observed driver bindings.
    pub current_drivers: Vec<PhysicalDriver>,
    /// All physical requests ended and the original bindings were verified.
    pub cleanup_verified: bool,
    /// Cumulative transfer observations without raw data.
    pub counters: PhysicalCounters,
}

/// Versioned physical-adapter snapshot without payload or serial strings.
#[derive(Debug, Clone, Serialize)]
pub struct PhysicalSnapshot {
    /// Schema version, currently 1.
    pub schema_version: u32,
    /// Global queued, in-flight, and response-held memory reservation.
    pub buffer_bytes_reserved: usize,
    /// Only explicitly authorized device capabilities.
    pub devices: Vec<PhysicalDeviceSnapshot>,
}

struct Slot {
    key: DeviceKey,
    usb: nusb::Device,
    metadata: RemoteDevice,
    source: Device,
    receipt: PhysicalDeviceReceipt,
    paths: observe::Paths,
    original_configuration: u8,
    state: Mutex<PhysicalDeviceSnapshot>,
    changed: Notify,
}

impl Slot {
    fn lock(&self) -> Result<MutexGuard<'_, PhysicalDeviceSnapshot>> {
        self.state.lock().map_err(|_| {
            Error::State("physical state lock is poisoned; device is quarantined".into())
        })
    }
    fn update(&self, update: impl FnOnce(&mut PhysicalDeviceSnapshot)) -> Result<()> {
        update(&mut *self.lock()?);
        self.changed.notify_waiters();
        Ok(())
    }
}

#[derive(Default)]
struct Supervisors(JoinSet<()>);

impl Drop for Supervisors {
    fn drop(&mut self) {
        // Closing or dropping the registry must never abort a supervisor that
        // still owns real USB requests, buffers, or an uncertain operation.
        self.0.detach_all();
    }
}

#[derive(Default)]
struct BackgroundTasks {
    closing: bool,
    monitors: Vec<JoinHandle<()>>,
    supervisors: Supervisors,
    failed: bool,
}

impl BackgroundTasks {
    fn check_supervisors(&mut self) -> Result<()> {
        while let Some(result) = self.supervisors.0.try_join_next() {
            self.failed |= result.is_err();
        }
        if self.closing || self.failed {
            return Err(Error::State(
                "physical registry is closing or a supervisor failed; delegation remains unresolved"
                    .into(),
            ));
        }
        Ok(())
    }
}

/// A registry of explicitly granted Linux usbfs capabilities.
/// No method enumerates, opens, or automatically authorizes another device.
#[derive(Clone)]
pub struct PhysicalBackend {
    slots: Arc<Vec<Arc<Slot>>>,
    budget: Arc<Semaphore>,
    tasks: Arc<Mutex<BackgroundTasks>>,
}

impl PhysicalBackend {
    /// Validate helper-provided descriptors and receipts without claiming USB.
    /// Claims and kernel-driver detach happen only after a successful IMPORT.
    pub async fn from_sources(sources: Vec<PhysicalSource>) -> Result<Self> {
        if sources.is_empty() || sources.len() > 16 {
            return Err(Error::InvalidInput(
                "physical sources must contain 1..16 explicitly authorized devices".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        let mut keys = BTreeSet::new();
        for source in &sources {
            let key = DeviceKey::physical(&source.device.id)?;
            if !ids.insert(source.device.bus_id.to_string()) || !keys.insert(key) {
                return Err(Error::InvalidInput(
                    "duplicate physical source identity".into(),
                ));
            }
        }
        let mut slots = Vec::new();
        for source in sources {
            slots.push(Arc::new(observe::initialize(source).await?));
        }
        // Do not leave monitors behind when a later source fails initialization.
        let mut tasks = BackgroundTasks::default();
        for slot in &slots {
            let weak = Arc::downgrade(slot);
            // Sleep without retaining a slot; dropping the registry terminates
            // idle monitors, while a leased supervisor keeps only its own slot.
            tasks.monitors.push(tokio::spawn(async move {
                loop {
                    tokio::time::sleep(Duration::from_millis(250)).await;
                    let Some(slot) = weak.upgrade() else {
                        break;
                    };
                    if !slot.lock().is_ok_and(|state| state.state == "available") {
                        continue;
                    }
                    let observation = observe::verify(&slot).await;
                    let _ = slot.update(|state| {
                        if state.state != "available" {
                            return;
                        }
                        match observation {
                            Ok(true) => {}
                            Ok(false) => {
                                state.state = "gone".into();
                                state.leased = false;
                            }
                            Err(_) => {
                                state.state = "quarantined".into();
                                state.leased = true;
                                state.cleanup_verified = false;
                            }
                        }
                    });
                }
            }));
        }
        Ok(Self {
            slots: Arc::new(slots),
            budget: Arc::new(Semaphore::new(BUFFER_BUDGET)),
            tasks: Arc::new(Mutex::new(tasks)),
        })
    }

    /// Return cached observations; never read physical payload or device strings.
    pub fn snapshot(&self) -> Result<PhysicalSnapshot> {
        Ok(PhysicalSnapshot {
            schema_version: 1,
            buffer_bytes_reserved: BUFFER_BUDGET - self.budget.available_permits(),
            devices: self
                .slots
                .iter()
                .map(|slot| slot.lock().map(|state| state.clone()))
                .collect::<Result<_>>()?,
        })
    }

    /// Close this registry after its server has shut down and return owned state.
    ///
    /// Other registry clones, active requests, or uncertain cleanup prevent a
    /// successful close. Known monitors and supervisors are joined before the
    /// registry's device descriptors are dropped. This does not revoke or prove
    /// the absence of capabilities copied to other owners. Cancellation or a
    /// timeout never aborts a supervisor that may still own physical work.
    pub async fn close(self) -> Result<PhysicalSnapshot> {
        if Arc::strong_count(&self.slots) != 1 {
            return Err(Error::State(
                "other physical registry clones remain; delegation cannot be finished".into(),
            ));
        }
        closeable(&self.snapshot()?)?;
        let (monitors, mut supervisors, failed) = {
            let mut tasks = self.tasks.lock().map_err(|_| {
                Error::State("physical task lock is poisoned; delegation remains unresolved".into())
            })?;
            tasks.closing = true;
            (
                std::mem::take(&mut tasks.monitors),
                std::mem::take(&mut tasks.supervisors),
                tasks.failed,
            )
        };
        let joined = async {
            let mut failed = failed;
            // These observers only read sysfs. Their temporary slot references
            // must end before the final registry descriptor is released.
            for monitor in &monitors {
                monitor.abort();
            }
            for monitor in monitors {
                if let Err(error) = monitor.await {
                    failed |= !error.is_cancelled();
                }
            }
            while let Some(result) = supervisors.0.join_next().await {
                failed |= result.is_err();
            }
            if failed {
                return Err(Error::State(
                    "physical background task failed; delegation remains unresolved".into(),
                ));
            }
            Ok(())
        };
        tokio::time::timeout(CLEANUP_DEADLINE, joined)
            .await
            .map_err(|_| {
                Error::Timeout(
                    "physical background tasks have not ended; delegation remains unresolved"
                        .into(),
                )
            })??;
        let snapshot = self.snapshot()?;
        closeable(&snapshot)?;
        drop(self);
        Ok(snapshot)
    }

    /// Return registered metadata, always excluding gone generations.
    /// With `available_only`, also exclude claiming, leased, closing and quarantined devices.
    pub fn remote_devices(&self, available_only: bool) -> Result<Vec<RemoteDevice>> {
        Ok(self
            .catalog_with_access(available_only, &ConnectionAccess::Legacy)?
            .into_iter()
            .map(|entry| entry.metadata)
            .collect())
    }

    fn catalog_with_access(
        &self,
        available_only: bool,
        access: &ConnectionAccess,
    ) -> Result<Vec<RegisteredDevice>> {
        let mut devices = Vec::new();
        for slot in self.slots.iter() {
            let state = slot.lock()?;
            if access.allows(&slot.key)
                && state.state != "gone"
                && (!available_only || state.state == "available")
            {
                let mut metadata = slot.metadata.clone();
                metadata.configuration = state.configuration;
                devices.push(RegisteredDevice {
                    key: slot.key.clone(),
                    metadata,
                });
            }
        }
        Ok(devices)
    }
}

struct Signals {
    closing: AtomicBool,
    network_closed: AtomicBool,
    cancel: Mutex<BTreeSet<TransferId>>,
    permits: Mutex<BTreeMap<TransferId, OwnedSemaphorePermit>>,
    notify: Notify,
    disconnect: Mutex<Option<oneshot::Sender<()>>>,
    close_after_retire: Mutex<Option<TransferId>>,
}

impl Signals {
    fn close(&self) {
        self.closing.store(true, Ordering::Release);
        self.notify.notify_one();
        if let Ok(mut sender) = self.disconnect.lock()
            && let Some(sender) = sender.take()
        {
            let _ = sender.send(());
        }
    }
    fn permits(&self) -> Result<MutexGuard<'_, BTreeMap<TransferId, OwnedSemaphorePermit>>> {
        self.permits
            .lock()
            .map_err(|_| Error::State("physical budget lock is poisoned".into()))
    }
}

struct Queued {
    id: TransferId,
    request: Submit,
}

pub(crate) struct PhysicalLease {
    slot: Arc<Slot>,
    signals: Arc<Signals>,
    sender: mpsc::Sender<Queued>,
    events: mpsc::Receiver<TransferEvent>,
    cleanup: oneshot::Receiver<Result<()>>,
    disconnect: oneshot::Receiver<()>,
    budget: Arc<Semaphore>,
    cleanup_complete: Option<bool>,
}

impl ServingBackend for PhysicalBackend {
    type Lease = PhysicalLease;
    fn catalog(
        &self,
        available_only: bool,
        access: &ConnectionAccess,
    ) -> Result<Vec<RegisteredDevice>> {
        self.catalog_with_access(available_only, access)
    }
    async fn claim(
        &self,
        bus_id: &str,
        expected: Option<&DeviceKey>,
        access: &ConnectionAccess,
    ) -> Result<Self::Lease> {
        self.claim_attempt(bus_id, expected, access, None).await
    }
    async fn claim_managed(
        &self,
        bus_id: &str,
        expected: Option<&DeviceKey>,
        access: &ConnectionAccess,
        terminal: tokio::sync::watch::Sender<Option<rustusb_core::server_session::ServerCleanup>>,
    ) -> Result<Self::Lease> {
        self.claim_attempt(bus_id, expected, access, Some(terminal))
            .await
    }
    async fn settle_closed(&self) -> Result<()> {
        for slot in self.slots.iter() {
            loop {
                let notification = slot.changed.notified();
                tokio::pin!(notification);
                notification.as_mut().enable();
                let state = slot.lock()?.state.clone();
                match state.as_str() {
                    "available" | "gone" => break,
                    "quarantined" => {
                        return Err(Error::State(
                            "physical cleanup is quarantined; do not re-import this generation"
                                .into(),
                        ));
                    }
                    _ => notification.await,
                }
            }
        }
        Ok(())
    }
}

fn closeable(snapshot: &PhysicalSnapshot) -> Result<()> {
    if snapshot.buffer_bytes_reserved != 0
        || snapshot.devices.iter().any(|device| {
            !matches!(device.state.as_str(), "available" | "gone")
                || device.leased
                || !device.cleanup_verified
                || device.pending_urbs != 0
                || device.queued_urbs != 0
                || device.usbfs_inflight != 0
        })
    {
        return Err(Error::State(
            "physical requests or cleanup remain unresolved; delegation cannot be finished".into(),
        ));
    }
    Ok(())
}

impl SessionLease for PhysicalLease {
    fn device(&self) -> RemoteDevice {
        self.slot.metadata.clone()
    }
    fn start(&mut self, id: TransferId, request: Submit) -> Result<Started> {
        if self.signals.closing.load(Ordering::Acquire) {
            return Err(Error::State("physical session is closing".into()));
        }
        let reserved = request_charge(request.length)?;
        let permit = self
            .budget
            .clone()
            .try_acquire_many_owned(reserved)
            .map_err(|_| Error::State("physical service memory budget exhausted".into()))?;
        {
            let mut permits = self.signals.permits()?;
            if permits.len() >= MAX_REQUESTS || permits.contains_key(&id) {
                return Err(Error::State(
                    "physical session request limit or duplicate ID".into(),
                ));
            }
            permits.insert(id, permit);
        }
        self.slot.update(|state| {
            state.pending_urbs += 1;
            state.queued_urbs += 1;
        })?;
        if self.sender.try_send(Queued { id, request }).is_err() {
            self.signals.permits()?.remove(&id);
            self.slot.update(|state| {
                state.pending_urbs = state.pending_urbs.saturating_sub(1);
                state.queued_urbs = state.queued_urbs.saturating_sub(1);
            })?;
            return Err(Error::State(
                "physical request queue is full or closed".into(),
            ));
        }
        self.signals.notify.notify_one();
        Ok(Started::Queued)
    }
    fn poll_event(&mut self, cx: &mut Context<'_>) -> Poll<Result<TransferEvent>> {
        self.events.poll_recv(cx).map(|event| {
            event.ok_or_else(|| Error::State("physical completion stream closed".into()))
        })
    }
    fn poll_disconnect(&mut self, cx: &mut Context<'_>) -> Poll<Result<()>> {
        std::pin::Pin::new(&mut self.disconnect).poll(cx).map(|_| {
            Err(Error::State(
                "physical generation ended or its supervisor began closing".into(),
            ))
        })
    }
    fn request_cancel(&mut self, id: TransferId) -> Result<()> {
        self.signals
            .cancel
            .lock()
            .map_err(|_| Error::State("physical cancellation lock poisoned".into()))?
            .insert(id);
        self.signals.notify.notify_one();
        Ok(())
    }
    fn retire(&mut self, id: TransferId) -> Result<()> {
        if self.signals.permits()?.remove(&id).is_some() {
            self.slot.update(|state| {
                state.pending_urbs = state.pending_urbs.saturating_sub(1);
            })?;
        }
        let close = {
            let mut terminal = self
                .signals
                .close_after_retire
                .lock()
                .map_err(|_| Error::State("physical terminal lock poisoned".into()))?;
            if *terminal == Some(id) {
                terminal.take();
                true
            } else {
                false
            }
        };
        if close {
            self.signals.close();
        }
        Ok(())
    }
    fn record_unlink(&mut self, cancelled: bool) -> Result<()> {
        self.slot.update(|state| {
            state.counters.unlinks += 1;
            state.counters.unlink_cancelled += u64::from(cancelled);
        })
    }
    fn begin_close(&mut self) {
        self.events.close();
        while self.events.try_recv().is_ok() {}
        self.signals.network_closed.store(true, Ordering::Release);
        self.signals.close();
    }
    fn poll_cleanup(&mut self, cx: &mut Context<'_>) -> Poll<Result<()>> {
        if let Some(success) = self.cleanup_complete {
            return Poll::Ready(if success {
                Ok(())
            } else {
                Err(Error::State(
                    "physical cleanup failed; device remains quarantined".into(),
                ))
            });
        }
        std::pin::Pin::new(&mut self.cleanup)
            .poll(cx)
            .map(|result| {
                let result = result.unwrap_or_else(|_| {
                    Err(Error::State(
                        "physical supervisor ended without cleanup receipt".into(),
                    ))
                });
                self.cleanup_complete = Some(result.is_ok());
                result
            })
    }
}

impl Drop for PhysicalLease {
    fn drop(&mut self) {
        self.begin_close();
    }
}

fn request_charge(length: u32) -> Result<u32> {
    if length > crate::wire::MAX_TRANSFER as u32 {
        return Err(Error::InvalidInput(
            "physical transfer exceeds 64KiB".into(),
        ));
    }
    // Covers the owned request, physical buffer, control setup and reply copy.
    Ok(256 + length * 2)
}

fn error_completion(status: i32) -> Completion {
    Completion {
        status,
        actual_length: 0,
        data: Vec::new(),
    }
}

// A unique per-attempt witness: early validation failure touched no USB. Once
// moved to the supervisor, only that worker may publish its actual outcome.
struct ClaimCompletion(
    Option<tokio::sync::watch::Sender<Option<rustusb_core::server_session::ServerCleanup>>>,
);
impl ClaimCompletion {
    fn take(
        &mut self,
    ) -> Option<tokio::sync::watch::Sender<Option<rustusb_core::server_session::ServerCleanup>>>
    {
        self.0.take()
    }
}
impl Drop for ClaimCompletion {
    fn drop(&mut self) {
        if let Some(sender) = self.0.take() {
            sender.send_replace(Some(
                rustusb_core::server_session::ServerCleanup::NotStarted,
            ));
        }
    }
}
impl PhysicalBackend {
    async fn claim_attempt(
        &self,
        bus_id: &str,
        expected: Option<&DeviceKey>,
        access: &ConnectionAccess,
        terminal: Option<
            tokio::sync::watch::Sender<Option<rustusb_core::server_session::ServerCleanup>>,
        >,
    ) -> Result<PhysicalLease> {
        let mut terminal = ClaimCompletion(terminal);
        let slot = self
            .slots
            .iter()
            .find(|slot| slot.metadata.bus_id.as_str() == bus_id)
            .ok_or_else(|| Error::InvalidInput("unknown physical device".into()))?
            .clone();
        let (ready, receipt) = oneshot::channel();
        {
            let mut tasks = self.tasks.lock().map_err(|_| {
                Error::State("physical task lock is poisoned; device is quarantined".into())
            })?;
            let mut state = slot.lock()?;
            access.require(&slot.key, expected)?;
            tasks.check_supervisors()?;
            if state.state == "gone" {
                return Err(Error::InvalidInput(
                    "physical generation is gone; request a new explicit grant".into(),
                ));
            }
            if state.state != "available" {
                return Err(Error::State(
                    "physical device is owned, stale, or awaiting cleanup".into(),
                ));
            }
            state.state = "claiming".into();
            state.leased = true;
            state.cleanup_verified = false;
            drop(state);
            let budget = self.budget.clone();
            // The supervisor owns USB state even if IMPORT is cancelled. Its
            // task is tracked so close can await its actual final return.
            let terminal = terminal.take();
            tasks.supervisors.0.spawn(async move {
                worker::supervise(slot, budget, ready, terminal).await;
            });
        }
        receipt
            .await
            .map_err(|_| Error::State("physical supervisor ended before claim receipt".into()))?
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs::File, os::unix::fs::MetadataExt};

    fn empty_registry(tasks: BackgroundTasks) -> PhysicalBackend {
        PhysicalBackend {
            slots: Arc::new(Vec::new()),
            budget: Arc::new(Semaphore::new(BUFFER_BUDGET)),
            tasks: Arc::new(Mutex::new(tasks)),
        }
    }

    #[tokio::test]
    async fn close_rejects_another_registry_owner_without_disabling_it() -> Result<()> {
        let backend = empty_registry(BackgroundTasks::default());
        assert!(matches!(
            backend.clone().close().await,
            Err(Error::State(_))
        ));
        assert_eq!(backend.snapshot()?.buffer_bytes_reserved, 0);
        assert!(backend.close().await?.devices.is_empty());
        Ok(())
    }

    #[tokio::test]
    async fn close_joins_read_only_monitor_then_waits_for_supervisor_return() -> Result<()> {
        struct MonitorEnded(Option<oneshot::Sender<()>>);
        impl Drop for MonitorEnded {
            fn drop(&mut self) {
                if let Some(ended) = self.0.take() {
                    let _ = ended.send(());
                }
            }
        }

        let (monitor_started, started) = oneshot::channel();
        let (monitor_ended, ended) = oneshot::channel();
        let (release_supervisor, release) = oneshot::channel();
        let (supervisor_ended, completed) = oneshot::channel();
        let mut tasks = BackgroundTasks::default();
        tasks.monitors.push(tokio::spawn(async move {
            let _owner = MonitorEnded(Some(monitor_ended));
            let _ = monitor_started.send(());
            std::future::pending::<()>().await;
        }));
        tasks.supervisors.0.spawn(async move {
            let _ = release.await;
            let _ = supervisor_ended.send(());
        });
        tokio::time::timeout(Duration::from_secs(5), started)
            .await
            .map_err(|_| Error::Timeout("monitor fixture did not start".into()))?
            .map_err(|_| Error::State("monitor fixture did not start".into()))?;
        let closing = tokio::spawn(empty_registry(tasks).close());
        tokio::time::timeout(Duration::from_secs(5), ended)
            .await
            .map_err(|_| Error::Timeout("close did not release the monitor".into()))?
            .map_err(|_| Error::State("close did not release the monitor".into()))?;
        // The monitor witness establishes that close is already in progress;
        // the supervisor remains gated, without a sleep-based timing guess.
        assert!(!closing.is_finished());
        release_supervisor
            .send(())
            .map_err(|_| Error::State("supervisor was aborted before release".into()))?;
        tokio::time::timeout(Duration::from_secs(5), completed)
            .await
            .map_err(|_| Error::Timeout("supervisor did not actually return".into()))?
            .map_err(|_| Error::State("supervisor did not actually return".into()))?;
        assert!(
            closing
                .await
                .map_err(|_| Error::State("close fixture task failed".into()))??
                .devices
                .is_empty()
        );
        Ok(())
    }

    #[tokio::test]
    async fn dropping_supervisor_tracking_does_not_abort_owned_work() -> Result<()> {
        let (release, gated) = oneshot::channel();
        let (finished, observed) = oneshot::channel();
        let mut supervisors = Supervisors::default();
        supervisors.0.spawn(async move {
            let _ = gated.await;
            let _ = finished.send(());
        });
        drop(supervisors);
        release
            .send(())
            .map_err(|_| Error::State("tracked work was aborted on drop".into()))?;
        tokio::time::timeout(Duration::from_secs(5), observed)
            .await
            .map_err(|_| Error::Timeout("detached work did not finish".into()))?
            .map_err(|_| Error::State("detached work did not finish".into()))?;
        Ok(())
    }

    #[tokio::test]
    async fn failed_supervisor_join_does_not_report_closed() {
        let mut tasks = BackgroundTasks::default();
        let handle = tasks.supervisors.0.spawn(std::future::pending::<()>());
        handle.abort();
        assert!(matches!(
            empty_registry(tasks).close().await,
            Err(Error::State(_))
        ));
    }

    #[test]
    fn reservations_cover_two_buffers_and_remain_bounded_until_retirement() -> Result<()> {
        assert!(request_charge(65_537).is_err());
        let charge = request_charge(65_536)?;
        assert!(charge > 2 * 65_536);
        let budget = Arc::new(Semaphore::new(BUFFER_BUDGET));
        let mut outstanding = Vec::new();
        while let Ok(permit) = budget.clone().try_acquire_many_owned(charge) {
            outstanding.push(permit);
        }
        assert_eq!(outstanding.len(), 127);
        assert!(budget.available_permits() < charge as usize);
        drop(outstanding);
        assert_eq!(budget.available_permits(), BUFFER_BUDGET);
        Ok(())
    }

    #[tokio::test]
    async fn regular_file_capability_is_rejected_before_any_usb_or_sysfs_access() -> Result<()> {
        let fixture = super::observe::test_fixture::Fixture::new()?;
        let file = File::create(fixture.root.join("not-usb"))?;
        let metadata = file.metadata()?;
        let source = PhysicalSource {
            fd: file.into(),
            device: Device {
                id: format!("usb-{}", "0".repeat(64)),
                bus_id: rustusb_core::BusId::new("1-1")?,
                vendor_id: 0x1234,
                product_id: 1,
                manufacturer: None,
                product: None,
                serial: None,
                device_address: 2,
                usb_version: "2.00".into(),
                speed: "12".into(),
                class: 3,
                subclass: 1,
                protocol: 1,
                topology: "fixture".into(),
                driver: None,
                interfaces: Vec::new(),
                exported: false,
                occupied: None,
                export_blockers: Vec::new(),
            },
            receipt: PhysicalDeviceReceipt {
                sysfs_device: 0,
                sysfs_inode: 0,
                node_device: metadata.rdev(),
                node_inode: metadata.ino(),
                usb_device_number: 2,
                bus_number: 1,
            },
        };
        // The invalid identity is rejected by registration before an FD or
        // sysfs operation; a valid identity must still hit the regular-FD guard.
        let invalid_identity = PhysicalSource {
            fd: source.fd.try_clone()?,
            device: Device {
                id: "fixture".into(),
                ..source.device.clone()
            },
            receipt: source.receipt.clone(),
        };
        assert!(matches!(
            PhysicalBackend::from_sources(vec![invalid_identity]).await,
            Err(Error::InvalidInput(_))
        ));
        assert!(matches!(
            PhysicalBackend::from_sources(vec![source]).await,
            Err(Error::UnsafeDevice(_))
        ));
        assert!(matches!(
            PhysicalBackend::from_sources(Vec::new()).await,
            Err(Error::InvalidInput(_))
        ));
        Ok(())
    }
}

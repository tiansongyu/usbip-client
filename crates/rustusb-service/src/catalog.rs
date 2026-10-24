//! Shared latest-view observation of authenticated remote catalogs.
//!
//! Observing never imports USB or acquires helper capabilities. Subscriptions
//! contain metadata only. The owner cooperatively stops and joins its real task;
//! an agent may still finish an accepted remote read after local IPC is closed.

use crate::{
    config::Manifest,
    model::Failure,
    rpc::{Request, Response},
};
use rustusb_core::{DeviceKey, Error, Result};
use rustusb_protocol::secure::CatalogDevice;
use rustusb_transport::config::LoadedClientConfig;
use serde::Serialize;
use std::{
    collections::BTreeMap,
    sync::{Arc, OnceLock},
    time::Duration,
};
use tokio::{
    sync::{OwnedSemaphorePermit, Semaphore, watch},
    task::JoinHandle,
    time::Instant,
};

const MAX_CATALOG_BYTES: usize = 60 * 1024;
static OWNERS: OnceLock<Arc<Semaphore>> = OnceLock::new();

/// Validated delay after a completed refresh; not a network or USB deadline.
#[derive(Debug, Clone, Copy)]
pub struct RefreshInterval(u64);

impl RefreshInterval {
    /// Require between two and 120 seconds; retries never run catch-up bursts.
    pub fn new(seconds: u64) -> Result<Self> {
        if !(2..=120).contains(&seconds) {
            return Err(Error::InvalidInput(
                "catalog refresh interval must be 2..=120 seconds".into(),
            ));
        }
        Ok(Self(seconds))
    }

    /// Return the configured normal delay in whole seconds.
    pub fn seconds(self) -> u64 {
        self.0
    }
}

impl Default for RefreshInterval {
    fn default() -> Self {
        Self(2)
    }
}

/// Provenance of an observed catalog, without credentials or endpoint details.
#[derive(Debug, Clone, Copy, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum CatalogOrigin {
    /// A fixed local agent profile, authorized by the actual Unix peer UID.
    AgentProfile,
    /// An already-loaded mutually authenticated client configuration.
    LoadedTlsClient,
}

enum Source {
    Agent {
        manifest: Arc<Manifest>,
        profile: String,
    },
    Secure {
        config: Arc<LoadedClientConfig>,
        timeout: Duration,
    },
}

/// Fixed trusted source owned only by the observer, never its subscribers.
pub struct CatalogSource(Source);

impl CatalogSource {
    /// Observe one configured client profile without loading a private TLS key.
    ///
    /// This checks manifest shape, not local authorization. Every actual request
    /// still verifies the protected endpoint and the server authorizes its peer UID.
    pub fn agent(manifest: Arc<Manifest>, profile: String) -> Result<Self> {
        manifest.validate()?;
        if !manifest.profiles.iter().any(|entry| entry.id == profile) {
            return Err(Error::InvalidInput("unknown catalog profile".into()));
        }
        Ok(Self(Source::Agent { manifest, profile }))
    }

    /// Observe the exact loaded TLS identity/endpoint; no fallback or import occurs.
    pub fn secure(config: Arc<LoadedClientConfig>, timeout: Duration) -> Result<Self> {
        if timeout < Duration::from_secs(1) || timeout > Duration::from_secs(120) {
            return Err(Error::InvalidInput(
                "catalog timeout must be 1..=120 seconds".into(),
            ));
        }
        Ok(Self(Source::Secure { config, timeout }))
    }

    fn origin(&self) -> CatalogOrigin {
        match &self.0 {
            Source::Agent { .. } => CatalogOrigin::AgentProfile,
            Source::Secure { .. } => CatalogOrigin::LoadedTlsClient,
        }
    }

    async fn poll(&self, mut stop: watch::Receiver<bool>) -> Attempt {
        match &self.0 {
            Source::Agent { manifest, profile } => {
                let response = tokio::select! {
                    biased;
                    _ = crate::context::stopped(&mut stop) => return Attempt::stopped(),
                    response = crate::ipc::call(manifest, Request::Discover { profile: profile.clone() }) => response,
                };
                match response {
                    Ok(envelope) => Attempt {
                        journal_healthy: Some(envelope.journal_healthy),
                        result: match envelope.response {
                            Response::Catalog { devices } => Ok(Some(devices)),
                            Response::Error { code } => Err(CatalogFailure::from_agent(code)),
                            _ => Err(CatalogFailure::Protocol),
                        },
                    },
                    Err(error) => Attempt::failed(CatalogFailure::from_error(&error)),
                }
            }
            Source::Secure { config, timeout } => Attempt {
                journal_healthy: None,
                result: crate::jobs::discover_until(
                    config,
                    Instant::now() + *timeout,
                    crate::context::stopped(&mut stop),
                )
                .await
                .map_err(|error| CatalogFailure::from_error(&error)),
            },
        }
    }
}

/// Quality of a latest catalog; none of these states is a USB lease or proof.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CatalogState {
    /// No successful response has been received yet; inventory is absent.
    Initializing,
    /// The last completed refresh returned a valid authorized catalog.
    Current,
    /// A transient refresh failed; any old inventory is retained and marked stale.
    Stale,
    /// A terminal authorization/input/protocol failure cleared the current view.
    Failed,
    /// This local observer ended; retained inventory is no longer refreshed.
    Stopped,
}

/// Secret-free failure categories; raw TLS/OS errors never enter a snapshot.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CatalogFailure {
    /// Identity/profile or existing TLS-handshake rejection class; refresh stops.
    Permission,
    /// A fixed source or request was invalid; automatic refresh stops.
    InvalidInput,
    /// Returned metadata was malformed or exceeded bounds; refresh stops.
    Protocol,
    /// An actual network/local-IPC I/O failed; the next read backs off.
    Io,
    /// The existing operation deadline elapsed; the next read backs off.
    Timeout,
    /// The service's capacity or state is unavailable; the next read backs off.
    State,
    /// Durable recording is unavailable; no successful catalog is acknowledged.
    Persistence,
    /// The configured operation is not supported; automatic refresh stops.
    Unsupported,
    /// The agent cancelled its read independently; a later refresh can retry.
    Cancelled,
}

impl CatalogFailure {
    fn from_error(error: &Error) -> Self {
        match error {
            Error::Permission(_) => Self::Permission,
            Error::InvalidInput(_) => Self::InvalidInput,
            Error::Protocol(_) => Self::Protocol,
            Error::Io(_) => Self::Io,
            Error::Timeout(_) => Self::Timeout,
            Error::Unsupported(_) => Self::Unsupported,
            _ => Self::State,
        }
    }
    fn from_agent(failure: Failure) -> Self {
        match failure {
            Failure::Permission => Self::Permission,
            Failure::InvalidInput => Self::InvalidInput,
            Failure::Io => Self::Io,
            Failure::Timeout => Self::Timeout,
            Failure::State => Self::State,
            Failure::Unsupported => Self::Unsupported,
            Failure::Cancelled => Self::Cancelled,
            Failure::Persistence => Self::Persistence,
        }
    }
    fn terminal(self) -> bool {
        matches!(
            self,
            Self::Permission | Self::InvalidInput | Self::Protocol | Self::Unsupported
        )
    }
}

/// A change between successful visible catalogs, not an assertion of physical hotplug.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CatalogChangeKind {
    /// The authorization key became visible.
    Added,
    /// The authorization key is no longer visible.
    Removed,
    /// Bus ID, bus number or device address changed for the same key.
    AddressChanged,
    /// Other advertised descriptor metadata changed for the same key.
    MetadataChanged,
}

/// Minimal key-qualified change; the complete new inventory accompanies it.
#[derive(Debug, Clone, Serialize)]
pub struct CatalogChange {
    /// Change classification, with no physical-generation or cleanup implication.
    pub kind: CatalogChangeKind,
    /// Exact backend-qualified key, not a temporary Bus ID.
    pub device_key: DeviceKey,
}

/// Complete bounded latest metadata view. Consumers can retain their own copies.
#[derive(Debug, Clone)]
pub struct CatalogSnapshot {
    /// Fresh local observer identity; not a server epoch or authorization token.
    pub instance: String,
    /// Monotonic local publication revision, never wrapped.
    pub revision: u64,
    /// Fixed non-secret source class.
    pub origin: CatalogOrigin,
    /// Current freshness or terminal condition.
    pub state: CatalogState,
    /// Typed failure of the latest attempt, without raw error text.
    pub failure: Option<CatalogFailure>,
    /// Initiated refresh operations, including a read stopped before its reply.
    pub attempts: u64,
    /// Local elapsed milliseconds when this view was published, not a remote clock.
    pub sampled_ms: u64,
    /// Local observation time of the last successful catalog, if any.
    pub last_success_ms: Option<u64>,
    /// Actually observed agent journal health; absent for TLS/direct IPC failure.
    pub journal_healthy: Option<bool>,
    /// None before success or after terminal failure; Some(empty) is real empty.
    pub devices: Option<Arc<[CatalogDevice]>>,
    /// Delta against the previous successful view; absent for errors/stopping.
    pub changes: Vec<CatalogChange>,
    /// Whole-second delay planned after this completed refresh; zero at the end.
    pub next_refresh_seconds: u64,
    /// This publication requires full replacement independently of reader gaps.
    pub resync_required: bool,
}

/// A full view and a reader-specific signal that intermediate updates were skipped.
#[derive(Debug)]
pub struct CatalogUpdate {
    /// Complete immutable view, without TLS configuration, sockets or owner permits.
    pub snapshot: Arc<CatalogSnapshot>,
    /// Replace from the full view on first delivery, gaps, stale recovery or failure.
    pub resync_required: bool,
}

/// Latest-view receiver. Dropping it neither owns nor stops a network operation.
pub struct CatalogSubscription {
    receiver: watch::Receiver<Arc<CatalogSnapshot>>,
    revision: Option<u64>,
}

impl CatalogSubscription {
    /// Observe a complete publication; slow consumers can skip intermediate revisions.
    pub async fn next(&mut self) -> Result<CatalogUpdate> {
        loop {
            let snapshot = self.receiver.borrow_and_update().clone();
            if self.revision != Some(snapshot.revision) {
                let resync_required = snapshot.resync_required
                    || self.revision.and_then(|v| v.checked_add(1)) != Some(snapshot.revision);
                self.revision = Some(snapshot.revision);
                return Ok(CatalogUpdate {
                    snapshot,
                    resync_required,
                });
            }
            self.receiver
                .changed()
                .await
                .map_err(|_| Error::State("remote catalog observer ended".into()))?;
        }
    }
}

/// Real observation task owner. Drop only requests stop; shutdown joins it.
pub struct CatalogMonitor {
    stop: watch::Sender<bool>,
    receiver: watch::Receiver<Arc<CatalogSnapshot>>,
    task: Option<JoinHandle<Result<()>>>,
}

impl CatalogMonitor {
    /// Start inside a live Tokio runtime; at most two actual owners per process.
    ///
    /// UUID allocation uses the existing small Linux proc read. Permanent OS or
    /// resolver blocking is not claimed bounded/cancellable. No USB is imported.
    pub fn start(source: CatalogSource, interval: RefreshInterval) -> Result<Self> {
        Self::launch(
            source,
            interval,
            OWNERS.get_or_init(|| Arc::new(Semaphore::new(2))).clone(),
        )
    }

    fn launch(
        source: CatalogSource,
        interval: RefreshInterval,
        owners: Arc<Semaphore>,
    ) -> Result<Self> {
        let handle = tokio::runtime::Handle::try_current().map_err(|_| {
            Error::State("catalog observation requires a running Tokio runtime".into())
        })?;
        let permit = owners
            .try_acquire_owned()
            .map_err(|_| Error::State("catalog observer capacity unavailable".into()))?;
        let instance = rustusb_core::application::InstanceId::new(crate::fresh_id()?)?;
        let initial = CatalogSnapshot {
            instance: instance.to_string(),
            revision: 0,
            origin: source.origin(),
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
        };
        let (sender, receiver) = watch::channel(Arc::new(initial.clone()));
        let (stop, stopping) = watch::channel(false);
        let task = handle.spawn(observe(source, interval, initial, sender, stopping, permit));
        Ok(Self {
            stop,
            receiver,
            task: Some(task),
        })
    }

    /// Subscribe without acquiring a TLS/source configuration, socket or quota.
    pub fn subscribe(&self) -> CatalogSubscription {
        CatalogSubscription {
            receiver: self.receiver.clone(),
            revision: None,
        }
    }

    /// Request sticky cooperative termination, without asserting that it finished.
    pub fn request_stop(&self) {
        self.stop.send_replace(true);
    }

    /// Stop and join the local observer and any owned TLS driver.
    ///
    /// Accepted agent requests and libc resolver workers retain their existing
    /// independent owners/quotas. This is not proof of remote or USB cleanup.
    pub async fn shutdown(mut self) -> Result<()> {
        self.request_stop();
        if let Some(task) = self.task.take() {
            task.await
                .map_err(|_| Error::State("catalog observer task failed".into()))??;
        }
        Ok(())
    }
}

impl Drop for CatalogMonitor {
    fn drop(&mut self) {
        self.request_stop();
    }
}

struct Attempt {
    result: std::result::Result<Option<Vec<CatalogDevice>>, CatalogFailure>,
    journal_healthy: Option<bool>,
}
impl Attempt {
    fn stopped() -> Self {
        Self {
            result: Ok(None),
            journal_healthy: None,
        }
    }
    fn failed(failure: CatalogFailure) -> Self {
        Self {
            result: Err(failure),
            journal_healthy: None,
        }
    }
}

fn changes(old: &[CatalogDevice], new: &[CatalogDevice]) -> Vec<CatalogChange> {
    let before: BTreeMap<_, _> = old
        .iter()
        .map(|entry| (&entry.device_key, &entry.device))
        .collect();
    let after: BTreeMap<_, _> = new
        .iter()
        .map(|entry| (&entry.device_key, &entry.device))
        .collect();
    let mut result = Vec::new();
    for (key, device) in &before {
        let kind = match after.get(key) {
            None => Some(CatalogChangeKind::Removed),
            Some(next)
                if (
                    device.bus_id.clone(),
                    device.bus_number,
                    device.device_number,
                ) != (next.bus_id.clone(), next.bus_number, next.device_number) =>
            {
                Some(CatalogChangeKind::AddressChanged)
            }
            Some(next) if device != next => Some(CatalogChangeKind::MetadataChanged),
            _ => None,
        };
        if let Some(kind) = kind {
            result.push(CatalogChange {
                kind,
                device_key: (*key).clone(),
            });
        }
    }
    for key in after.keys().filter(|key| !before.contains_key(*key)) {
        result.push(CatalogChange {
            kind: CatalogChangeKind::Added,
            device_key: (*key).clone(),
        });
    }
    result
}

fn apply(
    view: &mut CatalogSnapshot,
    attempt: Attempt,
    interval: RefreshInterval,
    failures: &mut u32,
) -> Result<()> {
    let previous = view.state;
    view.revision = view
        .revision
        .checked_add(1)
        .ok_or_else(|| Error::State("catalog revision exhausted".into()))?;
    view.journal_healthy = attempt.journal_healthy;
    view.changes.clear();
    let result = attempt.result.and_then(|devices| {
        if let Some(devices) = &devices {
            rustusb_protocol::secure::validate_catalog(devices)
                .map_err(|_| CatalogFailure::Protocol)?;
            let bytes = serde_json::to_vec(devices).map_err(|_| CatalogFailure::Protocol)?;
            if bytes.len() > MAX_CATALOG_BYTES {
                return Err(CatalogFailure::Protocol);
            }
        }
        Ok(devices)
    });
    match result {
        Ok(Some(mut devices)) => {
            devices.sort_by(|a, b| a.device_key.cmp(&b.device_key));
            view.changes = changes(view.devices.as_deref().unwrap_or_default(), &devices);
            view.devices = Some(devices.into());
            view.state = CatalogState::Current;
            view.failure = None;
            view.last_success_ms = Some(view.sampled_ms);
            view.next_refresh_seconds = interval.0;
            view.resync_required = previous != CatalogState::Current;
            *failures = 0;
        }
        Ok(None) => {
            view.state = CatalogState::Stopped;
            view.failure = None;
            view.next_refresh_seconds = 0;
            view.resync_required = true;
        }
        Err(failure) => {
            view.failure = Some(failure);
            view.resync_required = true;
            if failure.terminal() {
                view.state = CatalogState::Failed;
                view.devices = None;
                view.next_refresh_seconds = 0;
            } else {
                view.state = CatalogState::Stale;
                view.next_refresh_seconds = interval
                    .0
                    .saturating_mul(1_u64 << (*failures).min(6))
                    .min(120);
                *failures = failures.saturating_add(1);
            }
        }
    }
    Ok(())
}

async fn observe(
    source: CatalogSource,
    interval: RefreshInterval,
    mut view: CatalogSnapshot,
    sender: watch::Sender<Arc<CatalogSnapshot>>,
    mut stop: watch::Receiver<bool>,
    _permit: OwnedSemaphorePermit,
) -> Result<()> {
    let started = Instant::now();
    let mut failures = 0;
    loop {
        let attempt = if *stop.borrow() {
            Attempt::stopped()
        } else {
            view.attempts = view
                .attempts
                .checked_add(1)
                .ok_or_else(|| Error::State("catalog attempt counter exhausted".into()))?;
            source.poll(stop.clone()).await
        };
        view.sampled_ms = u64::try_from(started.elapsed().as_millis())
            .map_err(|_| Error::State("catalog observation clock exhausted".into()))?;
        apply(&mut view, attempt, interval, &mut failures)?;
        sender.send_replace(Arc::new(view.clone()));
        if matches!(view.state, CatalogState::Failed | CatalogState::Stopped) {
            return Ok(());
        }
        tokio::select! {
            biased;
            _ = crate::context::stopped(&mut stop) => {},
            _ = tokio::time::sleep(Duration::from_secs(view.next_refresh_seconds)) => {},
        }
    }
}

#[cfg(test)]
mod tests;

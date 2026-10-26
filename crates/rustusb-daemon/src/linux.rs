use super::{MAX_CONNECTIONS, MAX_FRAME_BYTES, Policy, Request, Response};
#[cfg(feature = "physical-usb")]
use rustusb_core::application::{BrokerCleanupProof, BrokerCorrelation};
use rustusb_core::{Device, DeviceDiscovery, Error, ExportBackend, Result, VhciPort};
use rustusb_linux::SysfsDiscovery;
use rustusb_native::{NativeBackend, NativeSession};
use serde::Serialize;
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::Read,
    os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt},
    path::{Component, Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    net::{UnixListener, UnixStream},
    sync::{Mutex, Semaphore},
    task::JoinSet,
    time::timeout,
};

const READ_TIMEOUT: Duration = Duration::from_secs(5);
const WRITE_TIMEOUT: Duration = Duration::from_secs(5);
const QUEUE_TIMEOUT: Duration = Duration::from_secs(30);
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(120);
const CLEANUP_TIMEOUT: Duration = Duration::from_secs(10);

#[derive(Default)]
pub(crate) struct ManagedState {
    exports: HashMap<String, Device>,
    ports: HashMap<VhciPort, NativeSession>,
    released_ports: HashSet<VhciPort>,
    // At most one failed new receipt can collide with a different old owner:
    // no further import starts before its independent release is verified.
    conflicted_import: Option<NativeSession>,
}

impl ManagedState {
    fn retain_failed_import(&mut self, session: NativeSession) -> Result<()> {
        if let Some(old) = self.ports.get(&session.port)
            && !same_session(old, &session)
        {
            if self
                .conflicted_import
                .as_ref()
                .is_some_and(|prior| !same_session(prior, &session))
            {
                return Err(Error::State(
                    "another import conflict is unresolved; no ownership was overwritten".into(),
                ));
            }
            self.conflicted_import = Some(session);
            return Err(Error::State("failed import conflicts with an old owner; both receipts retained; administrator reconciliation required".into()));
        }
        self.released_ports.remove(&session.port);
        self.ports.entry(session.port).or_insert(session);
        Ok(())
    }

    fn authorize_current_binding(&self, device: &Device) -> Result<()> {
        match self.exports.get(&device.id) {
            Some(owned) if same_device_generation(owned, device) => Ok(()),
            Some(_) => Err(Error::State("exported device generation changed; administrator reconciliation required".into())),
            None if device.exported => Err(Error::Permission("existing export is not owned by this helper instance; administrator reconciliation required".into())),
            None => Ok(()),
        }
    }

    fn record_new_export(&mut self, before: &Device, after: Device) -> Result<()> {
        if before.exported || !after.exported || !same_device_generation(before, &after) {
            return Err(Error::State(
                "new export ownership cannot be proven; administrator reconciliation required"
                    .into(),
            ));
        }
        self.exports.insert(after.id.clone(), after);
        Ok(())
    }

    fn released_port_candidates(&self, current: &[NativeSession]) -> Result<Vec<NativeSession>> {
        if self.conflicted_import.is_some() {
            return Err(Error::State(
                "import conflict remains unresolved; no new import may start".into(),
            ));
        }
        let mut candidates = Vec::new();
        for owned in self.ports.values() {
            let observed = current.iter().find(|session| session.port == owned.port)
                .ok_or_else(|| Error::State("a managed port is absent from the kernel snapshot; reconciliation required".into()))?;
            if observed.status == 4 {
                candidates.push(owned.clone());
            } else if !same_session(owned, observed) {
                return Err(Error::State("a managed port has changed or has unknown identity; reconcile it before importing another device".into()));
            }
        }
        Ok(candidates)
    }
}

pub(crate) struct App {
    pub(crate) policy: Policy,
    pub(crate) backend: NativeBackend,
    // Serializes all mutations from this helper. It does not lock out an
    // administrator running usbip independently; identity checks still matter.
    pub(crate) state: Arc<Mutex<ManagedState>>,
    pub(crate) native_state: Option<Arc<std::sync::Mutex<super::native_state::Store>>>,
    pub(crate) native_exports: super::native_export::Registry,
    pub(crate) controllers: Option<Arc<super::controller::Service>>,
    pub(crate) storage_slots: Arc<Semaphore>,
    pub(crate) native_stop: tokio::sync::watch::Sender<bool>,
    #[cfg(feature = "physical-usb")]
    pub(crate) journal: Option<Arc<std::sync::Mutex<super::journal::Store>>>,
    #[cfg(feature = "physical-usb")]
    handoffs: Arc<std::sync::Mutex<HashSet<String>>>,
}

#[cfg(feature = "physical-usb")]
struct Handoff {
    // Fields drop in declaration order: release our FD before permitting finish.
    grant: super::PhysicalGrant,
    _tracking: HandoffTracking,
}
#[cfg(feature = "physical-usb")]
struct HandoffTracking {
    lease_id: String,
    active: Arc<std::sync::Mutex<HashSet<String>>>,
}
#[cfg(feature = "physical-usb")]
impl Drop for HandoffTracking {
    fn drop(&mut self) {
        if let Ok(mut active) = self.active.lock() {
            active.remove(&self.lease_id);
        }
    }
}

impl App {
    #[cfg(feature = "physical-usb")]
    async fn journal<T: Send + 'static>(
        &self,
        action: impl FnOnce(&mut super::journal::Store) -> Result<T> + Send + 'static,
    ) -> Result<T> {
        let journal = self
            .journal
            .clone()
            .ok_or_else(|| Error::State("physical persistent journal is not enabled".into()))?;
        tokio::task::spawn_blocking(move || {
            let mut store = journal.lock().map_err(|_| {
                Error::State("physical journal lock poisoned; authorization stopped".into())
            })?;
            action(&mut store)
        })
        .await
        .map_err(|_| Error::State("physical journal task failed".into()))?
    }

    #[cfg(feature = "physical-usb")]
    async fn open_physical(
        &self,
        uid: u32,
        pid: Option<i32>,
        id: String,
        secure: bool,
        correlation: Option<BrokerCorrelation>,
    ) -> Result<Handoff> {
        if let Some(c) = &correlation {
            c.validate()?;
        }
        if secure {
            self.policy.authorize_secure_physical(uid, &id)?;
        } else {
            self.policy.authorize_uid(uid)?;
            self.policy.authorize_physical(&id)?;
        }
        let _state = timeout(QUEUE_TIMEOUT, self.state.lock())
            .await
            .map_err(|_| Error::Timeout("physical capability authorization queue".into()))?;
        let pending_id = id.clone();
        if self
            .journal(move |store| store.has_device(&pending_id))
            .await?
        {
            return Err(Error::State("unresolved physical grant blocks new authorization; inspect physical-pending and reconcile the original baseline".into()));
        }
        let before = current_device(&id).await?;
        super::physical::ensure_eligible(&before)?;
        let recovery = if self
            .policy
            .physical_connect_restore_device_ids
            .contains(&id)
        {
            super::journal::RecoveryAuthorization::ConnectOriginalDrivers
        } else {
            super::journal::RecoveryAuthorization::ManualOnly
        };
        let (mut grant, record) = tokio::task::spawn_blocking(move || {
            let grant = super::physical::open_current(before)?;
            let baseline = super::physical_lifecycle::capture(&grant.device, &grant.receipt)?;
            let owner = super::physical_lifecycle::owner(uid, pid)?;
            let lease_id =
                super::physical_lifecycle::uuid(Path::new("/proc/sys/kernel/random/uuid"))?;
            Ok::<_, Error>((
                grant,
                super::journal::Record {
                    correlation,
                    lease_id,
                    owner,
                    baseline,
                    recovery,
                },
            ))
        })
        .await
        .map_err(|_| Error::State("physical capability observation task failed".into()))??;
        grant.lease_id = record.lease_id.clone();
        self.journal(move |store| store.reserve(record)).await?;
        // Any uncertainty from this point leaves the durable record unresolved.
        self.handoffs
            .lock()
            .map_err(|_| Error::State("physical handoff lock poisoned".into()))?
            .insert(grant.lease_id.clone());
        let tracking = HandoffTracking {
            lease_id: grant.lease_id.clone(),
            active: self.handoffs.clone(),
        };
        Ok(Handoff {
            grant,
            _tracking: tracking,
        })
    }

    #[cfg(feature = "physical-usb")]
    async fn finish_physical(
        &self,
        uid: u32,
        pid: Option<i32>,
        lease_id: String,
        admin_gone: Option<bool>,
    ) -> Result<serde_json::Value> {
        super::journal::validate_lease_id(&lease_id)?;
        if admin_gone.is_some() && uid != 0 {
            return Err(Error::Permission(
                "physical reconciliation requires root".into(),
            ));
        }
        if admin_gone.is_none() && self.policy.authorize_uid(uid).is_err() {
            self.policy.authorize_broker(uid)?;
        }
        let _state = timeout(QUEUE_TIMEOUT, self.state.lock())
            .await
            .map_err(|_| Error::Timeout("physical finish queue".into()))?;
        let lookup = lease_id.clone();
        let Some(record) = self.journal(move |store| store.get(&lookup)).await? else {
            return Ok(serde_json::json!({"status":"not_pending","lease_id":lease_id}));
        };
        let owner_record = record.clone();
        tokio::task::spawn_blocking(move || {
            if admin_gone.is_some() {
                super::physical_lifecycle::require_ended_owner(&owner_record)
            } else {
                super::physical_lifecycle::require_owner(
                    &owner_record,
                    &super::physical_lifecycle::owner(uid, pid)?,
                )
            }
        })
        .await
        .map_err(|_| Error::State("physical owner observation task failed".into()))??;
        timeout(READ_TIMEOUT, async {
            loop {
                let active = self
                    .handoffs
                    .lock()
                    .map_err(|_| Error::State("physical handoff lock poisoned".into()))?
                    .contains(&lease_id);
                if !active {
                    return Ok::<_, Error>(());
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .map_err(|_| {
            Error::Timeout("physical descriptor handoff still active; record retained".into())
        })??;
        let (observation, proof) = tokio::task::spawn_blocking(move || {
            let observation =
                super::physical_lifecycle::verify(&record, admin_gone.unwrap_or(true))?;
            let proof = if observation == "restored" {
                BrokerCleanupProof::PhysicalBaselineRestored
            } else if record.owner.boot_id
                != super::native_state::uuid("/proc/sys/kernel/random/boot_id")?
            {
                BrokerCleanupProof::PreviousBoot
            } else {
                BrokerCleanupProof::OriginalGenerationGone
            };
            Ok::<_, Error>((observation, proof))
        })
        .await
        .map_err(|_| Error::State("physical restoration observation task failed".into()))??;
        let retiring = lease_id.clone();
        self.journal(move |store| store.retire_verified(&retiring, proof))
            .await?;
        Ok(serde_json::json!({"status":"retired","lease_id":lease_id,"observation":observation}))
    }

    #[cfg(feature = "physical-usb")]
    async fn physical_pending(&self, uid: u32, after: Option<String>) -> Result<serde_json::Value> {
        if uid != 0 {
            return Err(Error::Permission(
                "physical pending records require root".into(),
            ));
        }
        if let Some(after) = &after {
            super::journal::validate_lease_id(after)?;
        }
        let snapshot = if self.journal.is_some() {
            self.journal(|store| store.snapshot()).await?
        } else {
            super::journal::Snapshot {
                schema_version: 1,
                records: Vec::new(),
            }
        };
        pending_page(snapshot, after)
    }

    async fn handle(&self, uid: u32, req: Request) -> Result<serde_json::Value> {
        if !matches!(req, Request::Status | Request::Sessions)
            || self.policy.authorize_broker(uid).is_err()
        {
            self.policy.authorize_uid(uid)?;
        }
        if let Request::Status = req {
            let kernel = match self.backend.sessions().await {
                Ok(sessions) => serde_json::json!({ "available": true, "sessions": sessions }),
                Err(error) => {
                    serde_json::json!({ "available": false, "reason": Response::failure(error).error })
                }
            };
            return Ok(serde_json::json!({
                "schema_version": super::STATUS_SCHEMA_VERSION,
                "helper": "running",
                "backend": "linux-native",
                "physical_usb_capability_enabled": cfg!(feature = "physical-usb"),
                "physical_journal_schema_version": if cfg!(feature = "physical-usb") { Some(1_u32) } else { None },
                "physical_device_allowlist_count": self.policy.physical_device_ids.len(),
                "physical_connect_restore_supported": cfg!(feature = "physical-usb"),
                "physical_connect_restore_allowlist_count": self.policy.physical_connect_restore_device_ids.len(),
                "physical_journal_storage_versions": if cfg!(feature = "physical-usb") { vec![1_u32, 2, 3] } else { vec![] },
                "secure_broker_configured": self.policy.secure_broker_uid.is_some(),
                "secure_native_import_supported": true,
                "broker_tracking_schema_version": 1,
                "broker_terminal_capacity": 128,
                "native_journal_storage_versions": [1,2],
                "secure_physical_device_allowlist_count": self.policy.secure_physical_device_ids.len(),
                "native_data_encryption": false,
                "external_trusted_network_confirmed": self.policy.trusted_network,
                "kernel": kernel,
                "port_ownership": "only ports created by this helper instance; unknown ports after restart require administrator reconciliation",
                "export_ownership": "only bindings successfully created and verified by this helper instance; unknown exports after restart require administrator reconciliation",
                "policy_reload": "restart required"
            }));
        }
        let mut state = timeout(QUEUE_TIMEOUT, self.state.lock())
            .await
            .map_err(|_| {
                Error::Timeout("helper operation queue; no new mutation started".into())
            })?;
        let export = matches!(&req, Request::Export { .. });
        match req {
            Request::Controller { .. }
            | Request::InspectStorage { .. }
            | Request::InspectBrokerOperation { .. }
            | Request::BrokerPending { .. }
            | Request::PrepareNativeImportTracked { .. }
            | Request::PrepareNativeExportTracked { .. }
            | Request::OpenNativeStubTracked { .. }
            | Request::PrepareNativeImport { .. }
            | Request::PrepareNativeExport { .. }
            | Request::OpenNativeStub { .. }
            | Request::NativePending { .. }
            | Request::ReconcileNative { .. } => Err(Error::InvalidInput(
                "secure native requests require dedicated transaction dispatch".into(),
            )),
            #[cfg(feature = "physical-usb")]
            Request::OpenSecurePhysical { .. }
            | Request::OpenSecurePhysicalTracked { .. }
            | Request::FinishPhysical { .. }
            | Request::PhysicalPending { .. }
            | Request::ReconcilePhysical { .. }
            | Request::OpenPhysical { .. } => Err(Error::InvalidInput(
                "physical capability requires descriptor handoff".into(),
            )),
            Request::Export { device_id } | Request::Unexport { device_id } => {
                self.policy.authorize_device(&device_id)?;
                let before = current_device(&device_id).await?;
                state.authorize_current_binding(&before)?;
                if before.occupied != Some(false) && before.exported {
                    return Err(Error::UnsafeDevice("exported device is occupied or its occupancy is unknown; release the client first".into()));
                }
                // Both paths are idempotent without changing existing kernel
                // resources. An already-exported device passed ownership above.
                if before.exported == export {
                    if !export {
                        state.exports.remove(&device_id);
                    }
                    return Ok(
                        serde_json::json!({ "device_id": device_id, "bus_id": before.bus_id, "exported": before.exported }),
                    );
                }
                if export {
                    // An earlier owned export may already have been released
                    // externally. A failed fresh attempt must not retain that
                    // stale ownership record and adopt a later binding.
                    state.exports.remove(&device_id);
                }
                if export && !before.export_blockers.is_empty() {
                    return Err(Error::UnsafeDevice("current device is not eligible for safe export; inspect devices and the safety policy".into()));
                }
                // A second fresh scan rejects obvious removal/replacement. This
                // is not an atomic storage-exclusion mechanism; storage remains
                // blocked by discovery for Phase 1.
                let immediate = current_device(&device_id).await?;
                state.authorize_current_binding(&immediate)?;
                if !same_device_generation(&before, &immediate) {
                    return Err(Error::State(
                        "device changed during authorization; rescan and retry explicitly".into(),
                    ));
                }
                if export && !immediate.export_blockers.is_empty() {
                    return Err(Error::UnsafeDevice(
                        "device safety changed before export".into(),
                    ));
                }
                if immediate.exported && immediate.occupied != Some(false) {
                    return Err(Error::UnsafeDevice(
                        "device became occupied or its occupancy is unknown before the operation"
                            .into(),
                    ));
                }
                let operation = if export {
                    self.backend.export(immediate.bus_id.clone()).await
                } else {
                    self.backend.unexport(immediate.bus_id.clone()).await
                };
                // Reconcile even when the native command failed. Never report
                // a successful rollback or retry an uncertain operation.
                let after = current_device(&device_id).await;
                if let Err(error) = operation {
                    // Even a matching new binding after failure is insufficient
                    // proof of its origin when external root tools may run. Do
                    // not claim or undo it on a before/after difference alone.
                    if export && after.as_ref().is_ok_and(|device| device.exported) {
                        tracing::warn!(
                            "export failed with a binding now present; ownership was not claimed; administrator reconciliation required"
                        );
                    }
                    return Err(error);
                }
                let after = after?;
                if !same_device_generation(&immediate, &after) || after.exported != export {
                    return Err(Error::State("native operation postcondition is uncertain; inspect kernel state before another change".into()));
                }
                if export {
                    state.record_new_export(&immediate, after.clone())?;
                } else {
                    state.exports.remove(&device_id);
                }
                Ok(
                    serde_json::json!({ "device_id": device_id, "bus_id": after.bus_id, "exported": after.exported }),
                )
            }
            Request::Attach { peer, bus_id } => {
                self.policy.authorize_peer(&peer)?;
                // The old local generation must be gone before a new attach
                // can reuse its port. Reconcile before the tool creates any
                // kernel connection, not after discovering a map collision.
                timeout(CLEANUP_TIMEOUT, self.reconcile_released_ports(&mut state))
                    .await
                    .map_err(|_| Error::Timeout("prior managed session cleanup is incomplete; no new import was started; inspect kernel state".into()))??;
                // The backend must verify a unique before/after port, remote
                // device ID, current USB identity and native endpoint record.
                let session = match self.backend.attach_session_detailed(peer, bus_id).await {
                    Ok(session) => session,
                    Err(failure) => {
                        if failure
                            .cleanup
                            .as_ref()
                            .is_none_or(|result| result.is_err())
                            && let Some(session) = &failure.observed
                            && let Err(retention) = state.retain_failed_import(session.clone())
                        {
                            return Err(Error::State(format!("{failure}; {retention}")));
                        }
                        return Err(failure.into_error());
                    }
                };
                let port = session.port;
                if state.ports.contains_key(&port) {
                    // An external administrator can still change kernel state
                    // between snapshots. Only this operation's freshly verified
                    // receipt may authorize compensation; never use the old map.
                    let cleanup = self.backend.detach_session(&session).await;
                    if let Err(error) = &cleanup {
                        // Keep the old owner and this distinct failed-new
                        // receipt; never use one generation's proof for both.
                        let retention = state.retain_failed_import(session);
                        if let Err(retention) = retention {
                            return Err(Error::State(format!(
                                "new import conflicted with a managed record; cleanup could not be confirmed: {error}; {retention}"
                            )));
                        }
                    }
                    return Err(Error::State(match cleanup {
                        Ok(()) => "new import conflicted with a managed record; the newly verified connection was released; prior ownership requires reconciliation".into(),
                        Err(error) => format!("new import conflicted with a managed record; cleanup of the newly verified connection could not be confirmed: {error}; administrator reconciliation required"),
                    }));
                }
                state.released_ports.remove(&port);
                let data = serde_json::to_value(&session).map_err(json_error)?;
                state.ports.insert(port, session);
                Ok(data)
            }
            Request::Detach { port } => {
                if !state.ports.contains_key(&port) && !state.released_ports.contains(&port) {
                    return Err(Error::Permission("port is not owned by this helper instance; unknown or restart-surviving ports require administrator reconciliation".into()));
                }
                let current = self.backend.sessions().await?;
                let at_port = current.iter().find(|session| session.port == port);
                let Some(owned) = state.ports.get(&port) else {
                    if state.released_ports.contains(&port) && at_port.is_none() {
                        return Ok(serde_json::json!({ "port": port, "detached": true }));
                    }
                    return Err(Error::Permission("port is not owned by this helper instance; unknown or restart-surviving ports require administrator reconciliation".into()));
                };
                if let Some(actual) = at_port
                    && !same_session(owned, actual)
                {
                    return Err(Error::State("port identity changed since import; refusing to detach a possibly unrelated session".into()));
                }
                // NULL can precede USB device removal. Always let Native check
                // the previously claimed USB generation before reporting done.
                self.backend.detach_session(owned).await?;
                state.ports.remove(&port);
                state.released_ports.insert(port);
                Ok(serde_json::json!({ "port": port, "detached": true }))
            }
            Request::Sessions => {
                let current = self.backend.sessions().await?;
                let sessions = current
                    .into_iter()
                    .map(|session| {
                        let managed = state
                            .ports
                            .get(&session.port)
                            .is_some_and(|owned| same_session(owned, &session));
                        serde_json::json!({ "session": session, "managed": managed })
                    })
                    .collect::<Vec<_>>();
                Ok(serde_json::json!(sessions))
            }
            Request::Status => Err(Error::State("status dispatch invariant".into())),
        }
    }

    async fn reconcile_released_ports(&self, state: &mut ManagedState) -> Result<()> {
        if let Some(conflict) = &state.conflicted_import {
            if !self.backend.session_cleanup_complete(conflict).await? {
                return Err(Error::State("conflicting imported generation is not released; no new import started; administrator reconciliation required".into()));
            }
            state.conflicted_import = None;
            // The ordinary map below still needs its own, separate proof.
        }
        let current = self.backend.port_states().await?;
        let candidates = state.released_port_candidates(&current)?;
        for owned in candidates {
            loop {
                if self.backend.session_cleanup_complete(&owned).await? {
                    state.ports.remove(&owned.port);
                    state.released_ports.insert(owned.port);
                    tracing::info!(
                        port = owned.port.0,
                        "previous managed kernel session release verified before import"
                    );
                    break;
                }
                tokio::time::sleep(Duration::from_millis(100)).await;
            }
        }
        Ok(())
    }
}

#[cfg(feature = "physical-usb")]
#[derive(Debug, PartialEq, Eq)]
enum RecoveryAttempt {
    Deferred,
    Skipped,
    Retired,
}

#[cfg(feature = "physical-usb")]
fn recovery_permitted(policy: &Policy, record: &super::journal::Record) -> bool {
    record.recovery == super::journal::RecoveryAuthorization::ConnectOriginalDrivers
        && ((policy.authorize_uid(record.owner.uid).is_ok()
            && policy
                .authorize_physical(&record.baseline.device_id)
                .is_ok())
            || policy
                .authorize_secure_physical(record.owner.uid, &record.baseline.device_id)
                .is_ok())
        && policy
            .physical_connect_restore_device_ids
            .contains(&record.baseline.device_id)
}

#[cfg(feature = "physical-usb")]
impl App {
    // Called only while the actual blocking task owns the global mutation guard.
    // Keep the journal mutex out of USB calls, so pending remains readable.
    fn recover_record(&self, lease_id: &str) -> Result<RecoveryAttempt> {
        let journal = self
            .journal
            .as_ref()
            .ok_or_else(|| Error::State("physical journal unavailable".into()))?;
        let record = journal
            .lock()
            .map_err(|_| Error::State("physical journal lock poisoned".into()))?
            .get(lease_id)?;
        let Some(record) = record else {
            return Ok(RecoveryAttempt::Skipped);
        };
        if !recovery_permitted(&self.policy, &record) {
            return Ok(RecoveryAttempt::Skipped);
        }
        if self
            .handoffs
            .lock()
            .map_err(|_| Error::State("physical handoff lock poisoned".into()))?
            .contains(lease_id)
            || !super::physical_recovery::owner_ended(&record)?
        {
            return Ok(RecoveryAttempt::Deferred);
        }
        tracing::info!(
            operation = "physical_auto_restore",
            lease_id,
            phase = "attempt",
            "original owner ended; checking authorized original driver restoration"
        );
        super::physical_recovery::restore(&record)?;
        journal
            .lock()
            .map_err(|_| Error::State("physical journal lock poisoned".into()))?
            .retire_verified(lease_id, BrokerCleanupProof::PhysicalBaselineRestored)?;
        tracing::info!(
            operation = "physical_auto_restore",
            lease_id,
            phase = "retired",
            "original baseline independently verified; physical record retired"
        );
        Ok(RecoveryAttempt::Retired)
    }
}

#[cfg(feature = "physical-usb")]
async fn locked_recovery<T: Send + 'static>(
    guard: tokio::sync::OwnedMutexGuard<ManagedState>,
    action: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    // Cancellation of this future cannot drop the guard held by the real
    // blocking task. Do not abort or replace that work after a timeout.
    let mut task = tokio::task::spawn_blocking(move || {
        let _state = guard;
        action()
    });
    loop {
        tokio::select! {
            result = &mut task => return result.map_err(|_| Error::State("physical automatic restoration task failed".into()))?,
            _ = tokio::time::sleep(CLEANUP_TIMEOUT) => {
                tracing::warn!(operation = "physical_auto_restore", "still awaiting actual restoration work; mutation lock retained");
            }
        }
    }
}

#[cfg(feature = "physical-usb")]
async fn recovery_loop(app: Arc<App>, mut stop: tokio::sync::watch::Receiver<bool>) -> Result<()> {
    let mut attempted = HashSet::<String>::new();
    let mut interval = tokio::time::interval(Duration::from_millis(250));
    interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            biased;
            _ = stop.changed() => return Ok(()),
            _ = interval.tick() => {}
        }
        if *stop.borrow() {
            return Ok(());
        }
        let snapshot = app.journal(|store| store.snapshot()).await?;
        // Completed leases cannot accumulate across an unbounded helper lifetime.
        attempted.retain(|id| snapshot.records.iter().any(|record| &record.lease_id == id));
        for record in snapshot.records {
            if attempted.contains(&record.lease_id) || !recovery_permitted(&app.policy, &record) {
                continue;
            }
            let guard = tokio::select! {
                biased;
                _ = stop.changed() => return Ok(()),
                guard = app.state.clone().lock_owned() => guard,
            };
            if *stop.borrow() {
                return Ok(());
            }
            let lease_id = record.lease_id;
            let recovering = lease_id.clone();
            let service = app.clone();
            match locked_recovery(guard, move || service.recover_record(&recovering)).await {
                Ok(RecoveryAttempt::Deferred) => {}
                Ok(_) => {
                    attempted.insert(lease_id);
                }
                Err(error) => {
                    let errno = match &error {
                        Error::Io(error) => error.raw_os_error(),
                        _ => None,
                    };
                    let reason = Response::failure(error).error;
                    tracing::warn!(
                        operation = "physical_auto_restore",
                        lease_id,
                        phase = "failed",
                        errno,
                        reason,
                        "automatic restoration stopped; record retained for administrator reconciliation"
                    );
                    attempted.insert(lease_id);
                }
            }
        }
    }
}

// The public lifecycle protocol stays at version 1. Private storage adds
// recovery authorization without changing the strict legacy record shape.
#[cfg(feature = "physical-usb")]
#[derive(serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct PendingRecord {
    lease_id: String,
    owner: super::journal::Owner,
    baseline: super::journal::Baseline,
}

#[cfg(feature = "physical-usb")]
fn pending_page(
    mut snapshot: super::journal::Snapshot,
    after: Option<String>,
) -> Result<serde_json::Value> {
    snapshot.records.sort_by(|a, b| a.lease_id.cmp(&b.lease_id));
    let mut page = Vec::new();
    let mut authorizations = Vec::new();
    // Reserve response envelope, cursor, and both array/object delimiters.
    let mut bytes = 1024;
    let mut more = false;
    for record in snapshot
        .records
        .into_iter()
        .filter(|record| after.as_ref().is_none_or(|after| &record.lease_id > after))
    {
        let authorization =
            serde_json::json!({"lease_id":record.lease_id,"recovery":record.recovery});
        let entry = PendingRecord {
            lease_id: record.lease_id,
            owner: record.owner,
            baseline: record.baseline,
        };
        let mut size = serde_json::to_vec(&entry).map_err(json_error)?.len() + 1;
        if snapshot.schema_version >= 2 {
            size += serde_json::to_vec(&authorization)
                .map_err(json_error)?
                .len()
                + 1;
        }
        if bytes + size >= MAX_FRAME_BYTES {
            more = true;
            break;
        }
        bytes += size;
        page.push(entry);
        if snapshot.schema_version >= 2 {
            authorizations.push(authorization);
        }
    }
    if more && page.is_empty() {
        return Err(Error::State(
            "physical journal record exceeds IPC page size".into(),
        ));
    }
    let next_after = if more {
        page.last().map(|record| record.lease_id.clone())
    } else {
        None
    };
    let mut result = serde_json::json!({"schema_version":1,"records":page,"next_after":next_after});
    if snapshot.schema_version >= 2 {
        result["storage_schema_version"] = serde_json::json!(snapshot.schema_version);
        result["recovery_authorizations"] = serde_json::json!(authorizations);
    }
    Ok(result)
}

pub(crate) fn same_device_generation(before: &Device, after: &Device) -> bool {
    before.id == after.id
        && before.bus_id == after.bus_id
        && before.device_address == after.device_address
        && before.topology == after.topology
        && before.vendor_id == after.vendor_id
        && before.product_id == after.product_id
}

fn same_session(before: &NativeSession, after: &NativeSession) -> bool {
    // Reused port and USB addresses are insufficient. ERROR snapshots lacking
    // the receipt remain unknown and cannot transfer ownership or authorize I/O.
    before.port == after.port
        && before.local_bus_id.is_some()
        && before.local_bus_id == after.local_bus_id
        && before.remote_device_id.is_some()
        && before.remote_device_id == after.remote_device_id
        && before.vendor_id == after.vendor_id
        && before.product_id == after.product_id
        && before.local_device_address.is_some()
        && before.local_device_address == after.local_device_address
        && before.local_device_generation.is_some()
        && before.local_device_generation == after.local_device_generation
}

pub(crate) async fn current_device(id: &str) -> Result<Device> {
    let id = id.to_owned();
    tokio::task::spawn_blocking(move || {
        let devices = SysfsDiscovery::default().scan()?;
        let mut matching = devices.into_iter().filter(|device| device.id == id);
        let device = matching.next().ok_or_else(|| {
            Error::State("approved device is absent; rediscover its current identity".into())
        })?;
        if matching.next().is_some() {
            return Err(Error::UnsafeDevice(
                "device fingerprint is ambiguous".into(),
            ));
        }
        Ok(device)
    })
    .await
    .map_err(|_| Error::State("device scan task failed".into()))?
}

fn json_error(_: serde_json::Error) -> Error {
    Error::Protocol("invalid local JSON message".into())
}

fn validate_metadata(metadata: &fs::Metadata, expected_uid: u32) -> Result<()> {
    if metadata.uid() != expected_uid || metadata.mode() & 0o022 != 0 {
        return Err(Error::Permission(
            "policy and socket directories must be root-owned and not group/other writable".into(),
        ));
    }
    Ok(())
}

pub(super) fn protected_directory(path: &Path, expected_uid: u32) -> Result<()> {
    if !path.is_absolute() {
        return Err(Error::InvalidInput("helper paths must be absolute".into()));
    }
    let mut cursor = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir | Component::Normal(_) => cursor.push(component),
            _ => {
                return Err(Error::InvalidInput(
                    "helper paths cannot contain parent/current directory components".into(),
                ));
            }
        }
        let metadata = fs::symlink_metadata(&cursor)?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err(Error::Permission(
                "helper paths cannot traverse symlinks or non-directories".into(),
            ));
        }
        validate_metadata(&metadata, expected_uid)?;
    }
    Ok(())
}

fn read_policy(config: &Path) -> Result<Policy> {
    let parent = config
        .parent()
        .ok_or_else(|| Error::InvalidInput("policy parent directory required".into()))?;
    protected_directory(parent, 0)?;
    let descriptor = rustix::fs::open(
        config,
        rustix::fs::OFlags::RDONLY
            | rustix::fs::OFlags::NOFOLLOW
            | rustix::fs::OFlags::CLOEXEC
            | rustix::fs::OFlags::NONBLOCK,
        rustix::fs::Mode::empty(),
    )
    .map_err(std::io::Error::from)?;
    let file = File::from(descriptor);
    let metadata = file.metadata()?;
    validate_metadata(&metadata, 0)?;
    if !metadata.is_file() || metadata.len() > MAX_FRAME_BYTES as u64 {
        return Err(Error::Permission(
            "policy must be a regular file no larger than 64 KiB".into(),
        ));
    }
    let mut bytes = Vec::new();
    file.take((MAX_FRAME_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(Error::InvalidInput("policy size limit exceeded".into()));
    }
    let policy: Policy = serde_json::from_slice(&bytes).map_err(json_error)?;
    policy.validate()?;
    Ok(policy)
}

pub(crate) async fn read_frame<R: AsyncRead + Unpin>(
    reader: &mut R,
    deadline: Duration,
) -> Result<Vec<u8>> {
    let limited = reader.take((MAX_FRAME_BYTES + 1) as u64);
    let mut buffered = BufReader::new(limited);
    let mut frame = Vec::new();
    timeout(deadline, buffered.read_until(b'\n', &mut frame))
        .await
        .map_err(|_| Error::Timeout("local IPC frame".into()))??;
    if frame.len() > MAX_FRAME_BYTES || frame.last() != Some(&b'\n') {
        return Err(Error::Protocol(
            "IPC requires one newline-terminated JSON frame no larger than 64 KiB".into(),
        ));
    }
    Ok(frame)
}

pub(crate) async fn write_frame<W: AsyncWrite + Unpin, T: Serialize>(
    writer: &mut W,
    value: &T,
) -> Result<()> {
    let mut frame = serde_json::to_vec(value).map_err(json_error)?;
    frame.push(b'\n');
    if frame.len() > MAX_FRAME_BYTES {
        return Err(Error::Protocol(
            "local response exceeds IPC size limit".into(),
        ));
    }
    timeout(WRITE_TIMEOUT, writer.write_all(&frame))
        .await
        .map_err(|_| Error::Timeout("local IPC write".into()))??;
    Ok(())
}

async fn serve_connection(mut stream: UnixStream, app: Arc<App>) -> Result<()> {
    let credentials = stream.peer_cred()?;
    let uid = credentials.uid();
    let mut operation = "unauthorized_or_invalid";
    #[cfg(feature = "physical-usb")]
    let mut physical_grant = None;
    let response = match if uid == 0 || app.policy.authorize_broker(uid).is_ok() {
        Ok(())
    } else {
        app.policy.authorize_uid(uid)
    } {
        Err(error) => Response::failure(error),
        Ok(()) => match read_frame(&mut stream, READ_TIMEOUT).await {
            Ok(frame) => match serde_json::from_slice::<Request>(&frame).map_err(json_error) {
                Ok(req) => {
                    operation = match &req {
                        Request::Controller { .. } => "controller",
                        Request::InspectStorage { .. } => "inspect_storage",
                        Request::InspectBrokerOperation { .. } => "inspect_broker_operation",
                        Request::BrokerPending { .. } => "broker_pending",
                        Request::PrepareNativeImportTracked { .. } => {
                            "prepare_native_import_tracked"
                        }
                        Request::PrepareNativeExportTracked { .. } => {
                            "prepare_native_export_tracked"
                        }
                        Request::OpenNativeStubTracked { .. } => "open_native_stub_tracked",
                        #[cfg(feature = "physical-usb")]
                        Request::OpenSecurePhysicalTracked { .. } => "open_secure_physical_tracked",
                        #[cfg(feature = "physical-usb")]
                        Request::OpenPhysical { .. } => "open_physical",
                        #[cfg(feature = "physical-usb")]
                        Request::OpenSecurePhysical { .. } => "open_secure_physical",
                        Request::PrepareNativeExport { .. } => "prepare_native_export",
                        Request::OpenNativeStub { .. } => "open_native_stub",
                        Request::PrepareNativeImport { .. } => "prepare_native_import",
                        Request::NativePending { .. } => "native_pending",
                        Request::ReconcileNative { .. } => "reconcile_native",
                        #[cfg(feature = "physical-usb")]
                        Request::FinishPhysical { .. } => "finish_physical",
                        #[cfg(feature = "physical-usb")]
                        Request::PhysicalPending { .. } => "physical_pending",
                        #[cfg(feature = "physical-usb")]
                        Request::ReconcilePhysical { .. } => "reconcile_physical",
                        Request::Export { .. } => "export",
                        Request::Unexport { .. } => "unexport",
                        Request::Attach { .. } => "attach",
                        Request::Detach { .. } => "detach",
                        Request::Sessions => "sessions",
                        Request::Status => "status",
                    };
                    if let Request::Controller { command } = req {
                        return super::controller::serve(stream, app, uid, command).await;
                    }
                    if let Request::InspectStorage { device_id } = req {
                        return super::storage::serve(stream, app, uid, device_id).await;
                    }
                    if matches!(
                        req,
                        Request::InspectBrokerOperation { .. } | Request::BrokerPending { .. }
                    ) {
                        let result = super::broker::serve(&app, uid, req).await;
                        let response = match result {
                            Ok(data) => Response::success(data),
                            Err(e) => Response::failure(e),
                        };
                        return write_frame(&mut stream, &response).await;
                    }
                    if let Request::PrepareNativeImportTracked {
                        target,
                        correlation,
                    } = req
                    {
                        return super::native_channel::serve_import(
                            stream,
                            app,
                            uid,
                            credentials.pid(),
                            target,
                            Some(correlation),
                        )
                        .await;
                    }
                    if let Request::PrepareNativeExportTracked {
                        device_id,
                        correlation,
                    } = req
                    {
                        return super::native_export::serve_export(
                            stream,
                            app,
                            uid,
                            credentials.pid(),
                            device_id,
                            Some(correlation),
                        )
                        .await;
                    }
                    if let Request::OpenNativeStubTracked {
                        lease_id,
                        correlation,
                    } = req
                    {
                        return super::native_export::serve_stub(
                            stream,
                            app,
                            uid,
                            credentials.pid(),
                            lease_id,
                            Some(correlation),
                        )
                        .await;
                    }
                    if let Request::PrepareNativeExport { device_id } = req {
                        return super::native_export::serve_export(
                            stream,
                            app,
                            uid,
                            credentials.pid(),
                            device_id,
                            None,
                        )
                        .await;
                    }
                    if let Request::OpenNativeStub { lease_id } = req {
                        return super::native_export::serve_stub(
                            stream,
                            app,
                            uid,
                            credentials.pid(),
                            lease_id,
                            None,
                        )
                        .await;
                    }
                    if let Request::PrepareNativeImport { target } = req {
                        return super::native_channel::serve_import(
                            stream,
                            app,
                            uid,
                            credentials.pid(),
                            target,
                            None,
                        )
                        .await;
                    }
                    if matches!(
                        req,
                        Request::NativePending { .. } | Request::ReconcileNative { .. }
                    ) {
                        return super::native_channel::serve_admin(stream, app, uid, req).await;
                    }
                    #[cfg(feature = "physical-usb")]
                    {
                        let (req, correlation) = match req {
                            Request::OpenSecurePhysicalTracked {
                                device_id,
                                journal_schema_version,
                                correlation,
                            } => (
                                Request::OpenSecurePhysical {
                                    device_id,
                                    journal_schema_version,
                                },
                                Some(correlation),
                            ),
                            request => (request, None),
                        };
                        let secure = matches!(req, Request::OpenSecurePhysical { .. });
                        let result = match req {
                            Request::OpenPhysical {
                                device_id,
                                journal_schema_version,
                            }
                            | Request::OpenSecurePhysical {
                                device_id,
                                journal_schema_version,
                            } => {
                                if journal_schema_version != 1 {
                                    Err(Error::Protocol("physical journal schema 1 required; upgrade the physical client and helper together".into()))
                                } else {
                                    match app
                                        .open_physical(
                                            uid,
                                            credentials.pid(),
                                            device_id,
                                            secure,
                                            correlation,
                                        )
                                        .await
                                    {
                                        Ok(handoff) => {
                                            let description = super::physical::GrantDescription {
                                                lease_id: handoff.grant.lease_id.clone(),
                                                device: handoff.grant.device.clone(),
                                                receipt: handoff.grant.receipt.clone(),
                                            };
                                            let data = serde_json::to_value(description)
                                                .map_err(json_error);
                                            physical_grant = Some(handoff);
                                            data
                                        }
                                        Err(error) => Err(error),
                                    }
                                }
                            }
                            Request::FinishPhysical { lease_id } => {
                                app.finish_physical(uid, credentials.pid(), lease_id, None)
                                    .await
                            }
                            Request::PhysicalPending { after } => {
                                app.physical_pending(uid, after).await
                            }
                            Request::ReconcilePhysical {
                                lease_id,
                                acknowledge_gone,
                            } => {
                                app.finish_physical(
                                    uid,
                                    credentials.pid(),
                                    lease_id,
                                    Some(acknowledge_gone),
                                )
                                .await
                            }
                            request => app.handle(uid, request).await,
                        };
                        match result {
                            Ok(data) => Response::success(data),
                            Err(error) => Response::failure(error),
                        }
                    }
                    #[cfg(not(feature = "physical-usb"))]
                    match app.handle(uid, req).await {
                        Ok(data) => Response::success(data),
                        Err(error) => Response::failure(error),
                    }
                }
                Err(error) => Response::failure(error),
            },
            Err(error) => Response::failure(error),
        },
    };
    tracing::info!(
        caller_uid = uid,
        operation,
        success = response.ok,
        "local helper operation finished"
    );
    if let Err(error) = write_frame(&mut stream, &response).await {
        if matches!(error, Error::Protocol(_)) {
            write_frame(&mut stream, &Response::failure(error)).await?;
            // The grant description was not delivered. Never send its FD after
            // the fallback failure, even if a caller sends an acknowledgement.
            return Ok(());
        } else {
            return Err(error);
        }
    }
    #[cfg(feature = "physical-usb")]
    if let Some(grant) = physical_grant {
        // JSON readers may buffer beyond a newline. Send ancillary data only
        // after a separate acknowledgement, so no ordinary read can eat it.
        let mut acknowledgement = [0; 1];
        timeout(READ_TIMEOUT, stream.read_exact(&mut acknowledgement))
            .await
            .map_err(|_| Error::Timeout("physical descriptor handoff acknowledgement".into()))??;
        if acknowledgement != [super::physical::FD_MARKER] {
            return Err(Error::Protocol(
                "invalid physical descriptor acknowledgement".into(),
            ));
        }
        super::physical::send_fd(&stream, &grant.grant.fd).await?;
    }
    Ok(())
}

pub(crate) async fn connect_trusted(socket: &Path) -> Result<UnixStream> {
    let parent = socket
        .parent()
        .ok_or_else(|| Error::InvalidInput("socket parent directory required".into()))?;
    protected_directory(parent, 0)?;
    let metadata = fs::symlink_metadata(socket)?;
    if metadata.uid() != 0 || !metadata.file_type().is_socket() {
        return Err(Error::Permission(
            "helper socket must be root-owned and not a symlink".into(),
        ));
    }
    let stream = timeout(READ_TIMEOUT, UnixStream::connect(socket))
        .await
        .map_err(|_| Error::Timeout("connecting to local helper".into()))??;
    if stream.peer_cred()?.uid() != 0 {
        return Err(Error::Permission("helper peer is not root".into()));
    }
    Ok(stream)
}

pub(super) async fn request(socket: &Path, req: Request) -> Result<Response> {
    let mut stream = connect_trusted(socket).await?;
    exchange(&mut stream, &req).await
}

// A helper can reject SO_PEERCRED and close before the caller writes anything.
// Preserve that already-buffered refusal instead of replacing it with EPIPE.
// This is used only on the same socket after connect_trusted; never retry work.
pub(crate) async fn exchange(stream: &mut UnixStream, req: &Request) -> Result<Response> {
    if let Err(write_error) = write_frame(stream, req).await {
        if matches!(write_error, Error::Io(_) | Error::Timeout(_))
            && let Ok(frame) = read_frame(stream, READ_TIMEOUT).await
            && let Ok(response) = serde_json::from_slice::<Response>(&frame)
            && !response.ok
            && response.data.is_null()
            && response
                .error
                .as_deref()
                .is_some_and(|message| !message.trim().is_empty())
        {
            return Ok(response);
        }
        return Err(write_error);
    }
    let frame = read_frame(stream, RESPONSE_TIMEOUT).await?;
    serde_json::from_slice(&frame).map_err(json_error)
}

#[cfg(feature = "physical-usb")]
pub(super) async fn open_physical(
    socket: &Path,
    device_id: String,
    secure: bool,
    correlation: Option<BrokerCorrelation>,
) -> Result<super::PhysicalGrant> {
    super::validate_device_id(&device_id)?;
    if let Some(c) = &correlation {
        c.validate()?;
    }
    let mut stream = connect_trusted(socket).await?;
    let response = exchange(
        &mut stream,
        &if let Some(correlation) = correlation {
            Request::OpenSecurePhysicalTracked {
                device_id,
                journal_schema_version: 1,
                correlation,
            }
        } else if secure {
            Request::OpenSecurePhysical {
                device_id,
                journal_schema_version: 1,
            }
        } else {
            Request::OpenPhysical {
                device_id,
                journal_schema_version: 1,
            }
        },
    )
    .await?;
    if !response.ok {
        return Err(Error::State(
            response
                .error
                .unwrap_or_else(|| "physical capability was denied".into()),
        ));
    }
    let description: super::physical::GrantDescription =
        serde_json::from_value(response.data).map_err(json_error)?;
    timeout(
        WRITE_TIMEOUT,
        stream.write_all(&[super::physical::FD_MARKER]),
    )
    .await
    .map_err(|_| Error::Timeout("physical capability acknowledgement".into()))??;
    let fd = super::physical::receive_fd(&stream).await?;
    super::physical::validate_received(fd, description)
}

pub(super) async fn serve(
    config: &Path,
    socket: &Path,
    physical_state: &Path,
    native_state: &Path,
    controller_state: Option<&Path>,
) -> Result<()> {
    if !rustix::process::geteuid().is_root() {
        return Err(Error::Permission(
            "only the restricted helper, installed by an administrator, may run as root".into(),
        ));
    }
    #[cfg(not(feature = "physical-usb"))]
    if controller_state.is_some() {
        require_no_unreadable_physical_history(physical_state)?;
    }
    let policy = read_policy(config)?;
    let native_exists = match fs::symlink_metadata(native_state) {
        Ok(_) => true,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
        Err(error) => return Err(error.into()),
    };
    let native_state = if !policy.secure_imports.is_empty()
        || !policy.secure_native_device_ids.is_empty()
        || native_exists
    {
        let path = native_state.to_owned();
        Some(Arc::new(std::sync::Mutex::new(
            tokio::task::spawn_blocking(move || super::native_state::Store::open(&path))
                .await
                .map_err(|_| Error::State("Native journal initialization task failed".into()))??,
        )))
    } else {
        None
    };
    let controllers = if let Some(path) = controller_state {
        let path = path.to_owned();
        Some(Arc::new(
            tokio::task::spawn_blocking(move || super::controller::Service::open(&path))
                .await
                .map_err(|_| {
                    Error::State("controller store initialization worker failed".into())
                })??,
        ))
    } else {
        None
    };
    let (native_stop, _) = tokio::sync::watch::channel(false);
    #[cfg(feature = "physical-usb")]
    let journal = {
        // Even an empty policy must reload existing unresolved state. First-time
        // Native-only installations need not create a physical journal.
        let exists = match fs::symlink_metadata(physical_state) {
            Ok(_) => true,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => false,
            Err(error) => return Err(error.into()),
        };
        if !policy.physical_device_ids.is_empty()
            || !policy.secure_physical_device_ids.is_empty()
            || exists
        {
            let path = physical_state.to_owned();
            Some(Arc::new(std::sync::Mutex::new(
                tokio::task::spawn_blocking(move || super::journal::Store::open(&path))
                    .await
                    .map_err(|_| {
                        Error::State("physical journal initialization task failed".into())
                    })??,
            )))
        } else {
            None
        }
    };
    #[cfg(not(feature = "physical-usb"))]
    let _ = physical_state;
    let parent = socket
        .parent()
        .ok_or_else(|| Error::InvalidInput("socket parent directory required".into()))?;
    if !parent.exists() {
        let ancestor = parent
            .parent()
            .ok_or_else(|| Error::InvalidInput("socket directory parent required".into()))?;
        protected_directory(ancestor, 0)?;
        fs::create_dir(parent)?;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o755))?;
    }
    protected_directory(parent, 0)?;
    if fs::symlink_metadata(socket).is_ok() {
        return Err(Error::State("socket path already exists; verify the previous helper stopped and reconcile its state before removing it".into()));
    }
    let listener = UnixListener::bind(socket)?;
    fs::set_permissions(socket, fs::Permissions::from_mode(0o666))?;
    let bound_metadata = fs::symlink_metadata(socket)?;
    let app = Arc::new(App {
        policy,
        backend: NativeBackend::default(),
        state: Arc::new(Mutex::new(ManagedState::default())),
        native_state,
        native_exports: Default::default(),
        controllers,
        storage_slots: Arc::new(Semaphore::new(1)),
        native_stop,
        #[cfg(feature = "physical-usb")]
        journal,
        #[cfg(feature = "physical-usb")]
        handoffs: Arc::new(std::sync::Mutex::new(HashSet::new())),
    });
    let limit = Arc::new(Semaphore::new(MAX_CONNECTIONS));
    let mut jobs = JoinSet::new();
    let mut termination =
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    tracing::info!(
        "root helper listening; unknown kernel ports are not owned; no built-in data encryption"
    );
    #[cfg(feature = "physical-usb")]
    let (recovery_stop, recovery_rx) = tokio::sync::watch::channel(false);
    // A JoinSet owns the sole scheduler. All ordinary/error serve exits below
    // signal it and join actual in-flight blocking work before releasing state.
    #[cfg(feature = "physical-usb")]
    let mut recovery_jobs = JoinSet::new();
    #[cfg(feature = "physical-usb")]
    if app.journal.is_some() && !app.policy.physical_connect_restore_device_ids.is_empty() {
        recovery_jobs.spawn(recovery_loop(app.clone(), recovery_rx));
    }
    let outcome: Result<()> = async {
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _) = accepted?;
                match limit.clone().try_acquire_owned() {
                    Ok(permit) => {
                        let app = app.clone();
                        jobs.spawn(async move {
                            let _permit = permit;
                            if serve_connection(stream, app).await.is_err() {
                                tracing::warn!("local helper connection failed or caller disconnected; inspect kernel state after any mutation");
                            }
                        });
                    }
                    Err(_) => { tracing::warn!("local helper concurrent connection limit reached"); }
                }
            }
            _ = termination.recv() => break,
            interrupt = tokio::signal::ctrl_c() => { interrupt?; break; }
            recovery = async {
                #[cfg(feature = "physical-usb")]
                if let Some(result) = recovery_jobs.join_next().await {
                    return result.map_err(|_| Error::State("physical recovery scheduler failed".into()))?;
                }
                std::future::pending::<Result<()>>().await
            } => {
                recovery?;
                return Err(Error::State("physical recovery scheduler stopped unexpectedly".into()));
            }
            Some(result) = jobs.join_next(), if !jobs.is_empty() => {
                if result.is_err() { tracing::error!("helper request task failed; administrator reconciliation may be needed"); }
            }
        }
    }
    Ok(())
    }.await;
    drop(listener);
    app.native_stop.send_replace(true);
    #[cfg(feature = "physical-usb")]
    let _ = recovery_stop.send(true);
    // Stop accepting work, but allow bounded in-flight native operations to
    // finish. Existing native kernel sessions intentionally survive shutdown.
    while let Some(result) = jobs.join_next().await {
        if result.is_err() {
            tracing::error!("helper request failed during shutdown");
        }
    }
    #[cfg(feature = "physical-usb")]
    while let Some(result) = recovery_jobs.join_next().await {
        result.map_err(|_| {
            Error::State("physical recovery scheduler failed during shutdown".into())
        })??;
    }
    let current = fs::symlink_metadata(socket)?;
    if current.dev() == bound_metadata.dev() && current.ino() == bound_metadata.ino() {
        fs::remove_file(socket)?;
    }
    outcome
}

#[cfg(test)]
mod tests {
    use super::*;

    fn session(port: u8, inode: u64) -> Result<NativeSession> {
        Ok(NativeSession {
            port: VhciPort(port),
            status: 6,
            local_bus_id: Some(rustusb_core::BusId::new("9-1")?),
            remote_device_id: Some(0x10002),
            vendor_id: Some(0x1234),
            product_id: Some(0xabcd),
            local_device_address: Some(2),
            local_device_generation: Some(rustusb_native::LocalDeviceGeneration {
                filesystem_device: 1,
                inode,
            }),
        })
    }

    #[test]
    fn failed_import_receipts_retain_ownership_and_clear_only_the_new_owner_tombstone() -> Result<()>
    {
        let known = session(0, 11)?;
        let mut state = ManagedState::default();
        state.released_ports.insert(known.port);
        state.retain_failed_import(known.clone())?;
        assert_eq!(state.ports.get(&known.port), Some(&known));
        assert!(!state.released_ports.contains(&known.port));
        state.retain_failed_import(known.clone())?;
        assert_eq!(state.ports.len(), 1);
        assert!(state.conflicted_import.is_none());
        Ok(())
    }

    #[test]
    fn failed_new_generation_cannot_overwrite_old_owner_or_enable_another_import() -> Result<()> {
        let old = session(0, 11)?;
        let new = session(0, 12)?;
        let mut state = ManagedState::default();
        state.ports.insert(old.port, old.clone());
        assert!(state.retain_failed_import(new.clone()).is_err());
        assert_eq!(state.ports.get(&old.port), Some(&old));
        assert_eq!(state.conflicted_import.as_ref(), Some(&new));
        assert!(
            state
                .released_port_candidates(std::slice::from_ref(&old))
                .is_err()
        );
        assert!(state.retain_failed_import(session(0, 13)?).is_err());
        assert_eq!(state.ports.get(&old.port), Some(&old));
        assert_eq!(state.conflicted_import.as_ref(), Some(&new));
        assert!(state.released_ports.is_empty());
        Ok(())
    }

    #[test]
    fn attach_reconciliation_preserves_live_sessions_and_only_selects_null_ports() -> Result<()> {
        let owned = session(0, 11)?;
        let mut state = ManagedState::default();
        state.ports.insert(owned.port, owned.clone());
        let mut free = session(1, 12)?;
        free.status = 4;
        // A healthy first connection must not prevent importing a second device.
        assert!(
            state
                .released_port_candidates(&[owned.clone(), free])
                .is_ok_and(|candidates| candidates.is_empty())
        );
        let mut released = owned.clone();
        released.status = 4;
        released.local_bus_id = None;
        released.local_device_generation = None;
        let candidates = state.released_port_candidates(&[released])?;
        assert_eq!(candidates, vec![owned.clone()]);
        // NULL alone is not proof of cleanup: retain ownership until the
        // backend separately confirms disappearance of the old generation.
        assert_eq!(state.ports.get(&owned.port), Some(&owned));
        assert!(state.released_ports.is_empty());
        Ok(())
    }

    #[test]
    fn replacement_and_unknown_port_identities_block_attach_before_mutation() -> Result<()> {
        let owned = session(0, 11)?;
        let mut state = ManagedState::default();
        state.ports.insert(owned.port, owned.clone());
        let replacement = session(0, 12)?;
        assert!(!same_session(&owned, &replacement));
        assert!(state.released_port_candidates(&[replacement]).is_err());
        for missing_address in [false, true] {
            let mut incomplete = owned.clone();
            if missing_address {
                incomplete.local_device_address = None;
            } else {
                incomplete.local_device_generation = None;
            }
            assert!(!same_session(&incomplete, &incomplete));
            assert!(state.released_port_candidates(&[incomplete]).is_err());
        }
        let mut unknown = owned.clone();
        unknown.status = 7;
        unknown.local_bus_id = None;
        unknown.remote_device_id = None;
        unknown.local_device_address = None;
        unknown.local_device_generation = None;
        assert!(state.released_port_candidates(&[unknown]).is_err());
        assert!(state.released_port_candidates(&[]).is_err());
        assert_eq!(state.ports.get(&owned.port), Some(&owned));
        Ok(())
    }

    fn device(exported: bool, address: u32) -> Result<Device> {
        Ok(Device {
            id: format!("usb-{}", "a".repeat(64)),
            bus_id: rustusb_core::BusId::new("1-1")?,
            vendor_id: 0x1234,
            product_id: 0x5678,
            manufacturer: None,
            product: None,
            serial: None,
            device_address: address,
            usb_version: "2.00".into(),
            speed: "12".into(),
            class: 0,
            subclass: 0,
            protocol: 0,
            topology: "devices/controller/ports/1".into(),
            driver: None,
            interfaces: vec![],
            exported,
            occupied: Some(false),
            export_blockers: vec![],
        })
    }

    #[test]
    fn unknown_export_cannot_be_adopted_or_released_after_restart() -> Result<()> {
        let bound = device(true, 2)?;
        let state = ManagedState::default();
        assert!(matches!(
            state.authorize_current_binding(&bound),
            Err(Error::Permission(_))
        ));
        let mut owned = ManagedState::default();
        owned.record_new_export(&device(false, 2)?, bound.clone())?;
        owned.authorize_current_binding(&bound)?;
        let restarted = ManagedState::default();
        assert!(restarted.authorize_current_binding(&bound).is_err());
        Ok(())
    }

    #[test]
    fn owned_export_requires_same_generation_and_verified_new_binding() -> Result<()> {
        let mut state = ManagedState::default();
        let unbound = device(false, 2)?;
        let bound = device(true, 2)?;
        assert!(state.record_new_export(&unbound, unbound.clone()).is_err());
        assert!(state.record_new_export(&bound, bound.clone()).is_err());
        assert!(state.record_new_export(&unbound, device(true, 3)?).is_err());
        assert!(state.exports.is_empty());
        state.record_new_export(&unbound, bound.clone())?;
        assert!(state.authorize_current_binding(&device(true, 3)?).is_err());
        state.authorize_current_binding(&bound)?;
        state.exports.remove(&bound.id);
        state.authorize_current_binding(&unbound)?;
        assert!(state.authorize_current_binding(&bound).is_err());
        Ok(())
    }

    fn app(allowed_uids: Vec<u32>) -> Arc<App> {
        Arc::new(App {
            policy: Policy {
                secure_broker_uid: None,
                secure_imports: vec![],
                secure_physical_device_ids: vec![],
                secure_native_device_ids: vec![],
                allowed_uids,
                physical_device_ids: vec![],
                physical_connect_restore_device_ids: vec![],
                export_device_ids: vec![],
                peers: vec![],
                trusted_network: false,
            },
            backend: NativeBackend::default(),
            state: Arc::new(Mutex::new(ManagedState::default())),
            native_state: None,
            native_exports: Default::default(),
            controllers: None,
            storage_slots: Arc::new(Semaphore::new(1)),
            native_stop: tokio::sync::watch::channel(false).0,
            #[cfg(feature = "physical-usb")]
            journal: None,
            #[cfg(feature = "physical-usb")]
            handoffs: Arc::new(std::sync::Mutex::new(HashSet::new())),
        })
    }

    #[cfg(feature = "physical-usb")]
    #[tokio::test]
    async fn cancelling_recovery_wait_does_not_unlock_actual_blocking_work() -> Result<()> {
        let state = Arc::new(Mutex::new(ManagedState::default()));
        let guard = state.clone().lock_owned().await;
        let (started_tx, started_rx) = tokio::sync::oneshot::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let waiter = tokio::spawn(locked_recovery(guard, move || {
            let _ = started_tx.send(());
            release_rx
                .recv()
                .map_err(|_| Error::State("test blocking gate disconnected".into()))?;
            Ok(())
        }));
        started_rx
            .await
            .map_err(|_| Error::State("test task did not start".into()))?;
        waiter.abort();
        let cancelled = waiter.await.is_err();
        let locked = state.try_lock().is_err();
        // Release before assertions, even if the ownership invariant regresses.
        release_tx
            .send(())
            .map_err(|_| Error::State("test blocking task disappeared".into()))?;
        let _done = timeout(Duration::from_secs(5), state.lock())
            .await
            .map_err(|_| Error::Timeout("test actual recovery completion".into()))?;
        assert!(cancelled);
        assert!(
            locked,
            "cancelling the waiter released the actual mutation lock"
        );
        Ok(())
    }

    #[cfg(feature = "physical-usb")]
    #[test]
    fn legacy_empty_pending_keeps_exact_protocol_shape() -> Result<()> {
        let page = pending_page(
            super::super::journal::Snapshot {
                schema_version: 1,
                records: vec![],
            },
            None,
        )?;
        assert_eq!(
            page,
            serde_json::json!({"schema_version":1,"records":[],"next_after":null})
        );
        Ok(())
    }

    #[cfg(feature = "physical-usb")]
    #[tokio::test]
    async fn management_reconciliation_never_uses_the_ordinary_uid_allowlist() -> Result<()> {
        let service = app(vec![1000]);
        assert!(matches!(
            service.physical_pending(1000, None).await,
            Err(Error::Permission(_))
        ));
        assert!(matches!(
            service
                .finish_physical(
                    1000,
                    Some(1),
                    "00000000-0000-0000-0000-000000000001".into(),
                    Some(false)
                )
                .await,
            Err(Error::Permission(_))
        ));
        assert_eq!(
            service.physical_pending(0, None).await?["records"],
            serde_json::json!([])
        );
        Ok(())
    }

    #[cfg(feature = "physical-usb")]
    #[test]
    fn pending_pages_preserve_complete_baselines_without_exceeding_ipc_bound() -> Result<()> {
        use super::super::journal::{Baseline, OriginalInterface, Owner, Record};
        let bus_id = rustusb_core::BusId::new("1-1")?;
        let records: Vec<_> = (0..6)
            .map(|number| Record {
                correlation: None,
                recovery: super::super::journal::RecoveryAuthorization::ConnectOriginalDrivers,
                lease_id: format!("00000000-0000-0000-0000-{number:012x}"),
                owner: Owner {
                    uid: 1000,
                    pid: 2,
                    start_time: 3,
                    boot_id: "00000000-0000-0000-0000-000000000001".into(),
                },
                baseline: Baseline {
                    device_id: format!("usb-{number:064x}"),
                    bus_id: bus_id.clone(),
                    receipt: rustusb_core::PhysicalDeviceReceipt {
                        sysfs_device: 1,
                        sysfs_inode: 2,
                        node_device: 3,
                        node_inode: 4,
                        bus_number: 1,
                        usb_device_number: 2,
                    },
                    configuration: 1,
                    interfaces: (0..=255)
                        .map(|number| OriginalInterface {
                            number,
                            alternate: 1,
                            driver: Some("a".repeat(64)),
                        })
                        .collect(),
                },
            })
            .collect();
        let mut policy = app(vec![1000]).policy.clone();
        policy.trusted_network = true;
        policy
            .physical_device_ids
            .push(records[0].baseline.device_id.clone());
        policy.physical_connect_restore_device_ids = policy.physical_device_ids.clone();
        assert!(recovery_permitted(&policy, &records[0]));
        let mut manual = records[0].clone();
        manual.recovery = super::super::journal::RecoveryAuthorization::ManualOnly;
        assert!(!recovery_permitted(&policy, &manual));
        for remove in 0..4 {
            let mut changed = policy.clone();
            match remove {
                0 => changed.allowed_uids.clear(),
                1 => changed.physical_device_ids.clear(),
                2 => changed.physical_connect_restore_device_ids.clear(),
                _ => changed.trusted_network = false,
            }
            assert!(!recovery_permitted(&changed, &records[0]));
        }
        let mut after = None;
        let mut observed = Vec::new();
        loop {
            let page = pending_page(
                super::super::journal::Snapshot {
                    schema_version: 2,
                    records: records.clone(),
                },
                after,
            )?;
            assert_eq!(page["schema_version"], 1);
            assert_eq!(page["storage_schema_version"], 2);
            let response = Response::success(page.clone());
            assert!(serde_json::to_vec(&response).map_err(json_error)?.len() + 1 < MAX_FRAME_BYTES);
            let entries: Vec<PendingRecord> =
                serde_json::from_value(page["records"].clone()).map_err(json_error)?;
            let authorizations = page["recovery_authorizations"]
                .as_array()
                .ok_or_else(|| Error::State("test authorization list".into()))?;
            assert_eq!(authorizations.len(), entries.len());
            for (entry, authorization) in entries.iter().zip(authorizations) {
                assert_eq!(authorization["lease_id"], entry.lease_id);
                assert_eq!(authorization["recovery"], "connect_original_drivers");
            }
            assert!(!entries.is_empty());
            assert!(
                entries
                    .iter()
                    .all(|record| record.baseline.interfaces.len() == 256)
            );
            observed.extend(entries.into_iter().map(|record| record.lease_id));
            after = page["next_after"].as_str().map(str::to_owned);
            if after.is_none() {
                break;
            }
        }
        assert_eq!(
            observed,
            records
                .into_iter()
                .map(|record| record.lease_id)
                .collect::<Vec<_>>()
        );
        Ok(())
    }

    #[tokio::test]
    async fn real_unix_peer_credentials_allow_status_and_deny_unknown_uid() -> Result<()> {
        for allowed in [true, false] {
            let (mut client, server) = UnixStream::pair()?;
            let uid = server.peer_cred()?.uid();
            let service = app(if allowed { vec![uid] } else { vec![] });
            let task = tokio::spawn(serve_connection(server, service));
            if allowed {
                write_frame(&mut client, &Request::Status).await?;
            }
            let frame = read_frame(&mut client, READ_TIMEOUT).await?;
            let response: Response = serde_json::from_slice(&frame).map_err(json_error)?;
            assert_eq!(response.ok, allowed);
            task.await
                .map_err(|_| Error::State("test server task failed".into()))??;
        }
        Ok(())
    }

    async fn early_uid_refusal(req: Request) -> Result<()> {
        let (mut client, server) = UnixStream::pair()?;
        // Finish the actual SO_PEERCRED rejection before sending the request,
        // deterministically reproducing the QEMU EPIPE ordering.
        serve_connection(server, app(vec![])).await?;
        let response = exchange(&mut client, &req).await?;
        assert!(!response.ok && response.data.is_null());
        assert!(
            response
                .error
                .is_some_and(|message| message.starts_with("permission denied:"))
        );
        Ok(())
    }

    #[tokio::test]
    async fn early_uid_refusal_survives_a_failed_status_request_write() -> Result<()> {
        early_uid_refusal(Request::Status).await
    }

    #[cfg(feature = "physical-usb")]
    #[tokio::test]
    async fn early_uid_refusal_of_physical_capability_requires_no_acknowledgement() -> Result<()> {
        early_uid_refusal(Request::OpenPhysical {
            journal_schema_version: 1,
            device_id: format!("usb-{}", "a".repeat(64)),
        })
        .await
    }

    #[tokio::test]
    async fn failed_request_write_cannot_be_replaced_by_invalid_or_success_response() -> Result<()>
    {
        for frame in [
            "{\"ok\":true,\"data\":null,\"error\":null}\n",
            "{\"ok\":false,\"data\":null,\"error\":null}\n",
            "{\"ok\":false,\"data\":true,\"error\":\"denied\"}\n",
            "{\"ok\":false,\"data\":null,\"error\":\" \"}\n",
            "{\"ok\":false,\"data\":null,\"error\":\"denied\",\"extra\":true}\n",
            "malformed\n",
            "{\"ok\":false}",
            "",
        ] {
            let (mut client, mut server) = UnixStream::pair()?;
            server.write_all(frame.as_bytes()).await?;
            drop(server);
            let error = exchange(&mut client, &Request::Status)
                .await
                .err()
                .ok_or_else(|| Error::State("invalid refusal replaced the failed write".into()))?;
            assert!(
                matches!(error, Error::Io(ref error) if error.kind() == std::io::ErrorKind::BrokenPipe)
            );
        }
        Ok(())
    }

    #[tokio::test]
    async fn bounded_frames_reject_overflow_missing_newline_and_timeout() -> Result<()> {
        for bytes in [
            vec![b'x'; MAX_FRAME_BYTES + 1],
            b"{\"op\":\"status\"}".to_vec(),
        ] {
            let (mut sender, mut receiver) = tokio::io::duplex(MAX_FRAME_BYTES + 2);
            sender.write_all(&bytes).await?;
            sender.shutdown().await?;
            assert!(read_frame(&mut receiver, READ_TIMEOUT).await.is_err());
        }
        let (_sender, mut receiver) = tokio::io::duplex(64);
        assert!(matches!(
            read_frame(&mut receiver, Duration::from_millis(20)).await,
            Err(Error::Timeout(_))
        ));
        Ok(())
    }

    #[tokio::test]
    async fn unix_unknown_fields_are_rejected_and_unapproved_actions_do_no_native_io() -> Result<()>
    {
        let (mut client, server) = UnixStream::pair()?;
        let uid = server.peer_cred()?.uid();
        let service = app(vec![uid]);
        let task = tokio::spawn(serve_connection(server, service.clone()));
        client
            .write_all(b"{\"op\":\"status\",\"command\":\"id\"}\n")
            .await?;
        let response: Response =
            serde_json::from_slice(&read_frame(&mut client, READ_TIMEOUT).await?)
                .map_err(json_error)?;
        assert!(!response.ok);
        task.await
            .map_err(|_| Error::State("test server task failed".into()))??;
        assert!(matches!(
            service
                .handle(
                    uid,
                    Request::Export {
                        device_id: format!("usb-{}", "a".repeat(64))
                    }
                )
                .await,
            Err(Error::Permission(_))
        ));
        assert!(matches!(
            service
                .handle(uid, Request::Detach { port: VhciPort(1) })
                .await,
            Err(Error::Permission(_))
        ));
        let peer = rustusb_core::Peer {
            host: rustusb_core::Host::new("192.0.2.1")?,
            port: std::num::NonZeroU16::new(3240)
                .ok_or_else(|| Error::State("test port".into()))?,
        };
        assert!(matches!(
            service
                .handle(
                    uid,
                    Request::Attach {
                        peer,
                        bus_id: rustusb_core::BusId::new("1-1")?
                    }
                )
                .await,
            Err(Error::Permission(_))
        ));
        Ok(())
    }

    #[cfg(feature = "physical-usb")]
    #[tokio::test]
    async fn physical_capability_denial_precedes_scanning_or_opening_usb() -> Result<()> {
        for (allow_uid, allow_device, trusted_network) in [
            (false, true, true),
            (true, false, true),
            (true, true, false),
        ] {
            let (mut client, server) = UnixStream::pair()?;
            let uid = server.peer_cred()?.uid();
            let id = format!("usb-{}", "b".repeat(64));
            let service = Arc::new(App {
                policy: Policy {
                    secure_broker_uid: None,
                    secure_imports: vec![],
                    secure_physical_device_ids: vec![],
                    secure_native_device_ids: vec![],
                    allowed_uids: if allow_uid { vec![uid] } else { vec![] },
                    physical_connect_restore_device_ids: vec![],
                    physical_device_ids: if allow_device {
                        vec![id.clone()]
                    } else {
                        vec![]
                    },
                    export_device_ids: vec![],
                    peers: vec![],
                    trusted_network,
                },
                backend: NativeBackend::default(),
                state: Arc::new(Mutex::new(ManagedState::default())),
                native_state: None,
                native_exports: Default::default(),
                controllers: None,
                storage_slots: Arc::new(Semaphore::new(1)),
                native_stop: tokio::sync::watch::channel(false).0,
                journal: None,
                handoffs: Arc::new(std::sync::Mutex::new(HashSet::new())),
            });
            let task = tokio::spawn(serve_connection(server, service));
            write_frame(
                &mut client,
                &Request::OpenPhysical {
                    device_id: id,
                    journal_schema_version: 1,
                },
            )
            .await?;
            let response: Response =
                serde_json::from_slice(&read_frame(&mut client, READ_TIMEOUT).await?)
                    .map_err(json_error)?;
            assert!(!response.ok);
            assert!(
                response
                    .error
                    .is_some_and(|message| message.starts_with("permission denied:"))
            );
            task.await
                .map_err(|_| Error::State("capability denial test server failed".into()))??;
            match super::super::physical::receive_fd(&client).await {
                Err(Error::Protocol(_)) => {}
                // An unknown UID is rejected before reading its frame. Closing
                // a Unix stream with unread bytes can report ECONNRESET.
                Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::ConnectionReset => {}
                _ => {
                    return Err(Error::State(
                        "denied capability connection did not close without an FD".into(),
                    ));
                }
            }
        }
        Ok(())
    }

    #[test]
    fn policy_metadata_rejects_writable_or_wrong_owner() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let file_path = directory.path().join("policy.json");
        fs::write(&file_path, b"{}")?;
        fs::set_permissions(&file_path, fs::Permissions::from_mode(0o644))?;
        let metadata = fs::metadata(&file_path)?;
        validate_metadata(&metadata, metadata.uid())?;
        assert!(validate_metadata(&metadata, metadata.uid().wrapping_add(1)).is_err());
        fs::set_permissions(&file_path, fs::Permissions::from_mode(0o666))?;
        assert!(validate_metadata(&fs::metadata(&file_path)?, metadata.uid()).is_err());
        Ok(())
    }

    #[test]
    fn root_socket_validation_rejects_user_writable_directory() {
        assert!(protected_directory(Path::new("/tmp"), 0).is_err());
        assert!(protected_directory(Path::new("relative"), 0).is_err());
    }
}

// Called only with the original global mutation owner retained by the worker.
pub(crate) fn controller_conflicts(
    app: &App,
    state: &ManagedState,
    pin: &rustusb_native::controller::PinnedController,
) -> Result<()> {
    let mut paths = app.native_exports.export_scopes()?;
    if let Some(store) = &app.native_state {
        paths.extend(
            store
                .lock()
                .map_err(|_| Error::State("Native journal lock poisoned".into()))?
                .export_scopes()?,
        );
    }
    for path in paths {
        if pin.contains_original_device(&path)? {
            return Err(Error::State(
                "controller has an unresolved Native export; reconcile its original owner first"
                    .into(),
            ));
        }
    }
    for device in state.exports.values() {
        let topology = device.topology.as_str();
        if legacy_controller_scope(topology)? == pin.canonical_path() {
            return Err(Error::State(
                "controller has an active legacy export".into(),
            ));
        }
    }
    #[cfg(feature = "physical-usb")]
    if let Some(store) = &app.journal {
        let records = store
            .lock()
            .map_err(|_| Error::State("physical journal lock poisoned".into()))?
            .records()?;
        for record in records {
            if pin.contains_physical_generation(
                &record.baseline.bus_id,
                &record.baseline.receipt,
                &record.owner.boot_id,
            )? {
                return Err(Error::State("controller has an unresolved physical grant; reconcile its original owner first".into()));
            }
        }
    }
    Ok(())
}
fn legacy_controller_scope(topology: &str) -> Result<PathBuf> {
    let (controller, ports) = topology
        .split_once("/ports/")
        .ok_or_else(|| Error::State("unknown legacy controller scope".into()))?;
    let path = Path::new(controller);
    if !controller.starts_with("devices/")
        || !path.components().all(|c| matches!(c, Component::Normal(_)))
        || format!("1-{ports}").parse::<rustusb_core::BusId>().is_err()
    {
        return Err(Error::State("invalid legacy controller scope".into()));
    }
    Ok(Path::new("/sys").join(path))
}

#[cfg(test)]
mod controller_scope_tests {
    use super::*;
    #[test]
    fn legacy_scope_is_saved_topology_not_a_new_device_lookup() -> Result<()> {
        assert_eq!(
            legacy_controller_scope("devices/pci0000:00/0000:00:06.0/ports/1.2")?,
            Path::new("/sys/devices/pci0000:00/0000:00:06.0")
        );
        assert_ne!(
            legacy_controller_scope("devices/pci0000:00/0000:00:06.00/ports/1.2")?,
            Path::new("/sys/devices/pci0000:00/0000:00:06.0")
        );
        for s in [
            "",
            "/sys/devices/pci/ports/1",
            "devices/pci/../other/ports/1",
            "devices/pci/ports/1/extra",
        ] {
            assert!(legacy_controller_scope(s).is_err(), "{s}");
        }
        Ok(())
    }
}

#[cfg(not(feature = "physical-usb"))]
fn require_no_unreadable_physical_history(path: &Path) -> Result<()> {
    match fs::symlink_metadata(path){
        Err(error) if error.kind()==std::io::ErrorKind::NotFound=>Ok(()),
        Err(error)=>Err(error.into()),
        Ok(_)=>Err(Error::Unsupported("controller management cannot exclude old physical grants in this build; use a helper with physical-usb to inspect and reconcile the existing physical state".into())),
    }
}
#[cfg(all(test, not(feature = "physical-usb")))]
mod controller_feature_tests {
    use super::*;
    #[test]
    fn existing_physical_history_cannot_disappear_in_a_default_build() -> Result<()> {
        use std::os::unix::fs::symlink;
        let root = tempfile::tempdir()?;
        let path = root.path().join("physical");
        require_no_unreadable_physical_history(&path)?;
        fs::create_dir(&path)?;
        assert!(require_no_unreadable_physical_history(&path).is_err());
        fs::remove_dir(&path)?;
        symlink(root.path().join("missing-target"), &path)?;
        assert!(require_no_unreadable_physical_history(&path).is_err());
        Ok(())
    }
}

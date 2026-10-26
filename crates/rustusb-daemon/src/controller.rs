//! Explicit administrator controller enrollment, separate from USB ownership.
//!
//! Query results describe retained historical requests, not live device grants.
use rustusb_core::{
    Error, Result,
    application::{InstanceId, RequestId},
};
use rustusb_native::controller::{
    Inspection, ObjectIdentity, PciAddress, PinnedController, RootObservation, Selector,
};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};
mod store;

/// Durable administrator policy identity, independent of request and helper epochs.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct PolicyId(String);
impl PolicyId {
    /// Validate a canonical UUID without granting controller access.
    pub fn new(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        RequestId::new(value.clone())?;
        Ok(Self(value))
    }
    /// Borrow the UUID.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl TryFrom<String> for PolicyId {
    type Error = Error;
    fn try_from(v: String) -> Result<Self> {
        Self::new(v)
    }
}
impl From<PolicyId> for String {
    fn from(v: PolicyId) -> Self {
        v.0
    }
}
impl std::str::FromStr for PolicyId {
    type Err = Error;
    fn from_str(v: &str) -> Result<Self> {
        Self::new(v)
    }
}
/// One explicitly requested policy transition. No variant authorizes a USB device.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Mutation {
    /// Reserve a new or retired policy slot for an exact current selection.
    Enroll {
        /// Exact PCI metadata obtained by inspection.
        selector: Selector,
        /// Actual boot UUID expected by the administrator.
        expected_boot: String,
    },
    /// Reverify and re-arm existing intent in the current helper/boot epoch.
    Reconcile {
        /// Actual boot UUID expected by the administrator.
        expected_boot: String,
    },
    /// Withdraw enrollment intent on an empty controller; retain kernel denial.
    Retire {
        /// Actual boot UUID expected by the administrator.
        expected_boot: String,
    },
}
impl Mutation {
    fn boot(&self) -> &str {
        match self {
            Self::Enroll { expected_boot, .. }
            | Self::Reconcile { expected_boot }
            | Self::Retire { expected_boot } => expected_boot,
        }
    }
}
/// Exact caller-retained mutation identity and global store revision.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Change {
    /// Durable policy slot.
    pub policy_id: PolicyId,
    /// Original request UUID; retry/query this identity after a lost response.
    pub request_id: RequestId,
    /// Global store revision returned by list/query, not a kernel generation.
    pub expected_revision: u64,
    /// Explicit requested transition.
    pub mutation: Mutation,
}
impl Change {
    fn validate(&self) -> Result<()> {
        RequestId::new(self.mutation.boot())?;
        Ok(())
    }
}
/// Root-only bounded controller management request.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub enum Command {
    /// Inspect current kernel metadata without changing defaults.
    Inspect {
        /// Canonical PCI location.
        address: PciAddress,
    },
    /// Apply one explicit durable transition.
    Change {
        /// Original caller-owned mutation identity.
        change: Change,
    },
    /// Read the last retained result for an exact identity; never replays it.
    Query {
        /// Policy being queried.
        policy_id: PolicyId,
        /// Original request UUID.
        request_id: RequestId,
    },
    /// List bounded historical policy state and writer status.
    List {},
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Verification {
    helper_epoch: InstanceId,
    boot_id: String,
    pci: ObjectIdentity,
    roots: Vec<RootObservation>,
}
impl Verification {
    fn observed(epoch: &InstanceId, s: &Inspection) -> Self {
        Self {
            helper_epoch: epoch.clone(),
            boot_id: s.boot_id.clone(),
            pci: s.pci,
            roots: s.roots.clone(),
        }
    }
    fn validate(&self) -> Result<()> {
        RequestId::new(&self.boot_id)?;
        if self.pci.device == 0
            || self.pci.inode == 0
            || self.roots.len() != 2
            || self.roots[0].bus >= self.roots[1].bus
            || self.roots[0].identity == self.roots[1].identity
            || self.roots.iter().filter(|r| r.speed == 480).count() != 1
            || self
                .roots
                .iter()
                .filter(|r| matches!(r.speed, 5000 | 10000 | 20000))
                .count()
                != 1
            || !self.roots.iter().all(|r| {
                r.bus > 0
                    && r.device_default == 0
                    && r.interface_default == 0
                    && r.identity.device == self.pci.device
                    && r.identity.inode > 0
                    && r.device_attribute.device == self.pci.device
                    && r.device_attribute.inode > 0
                    && r.interface_attribute.device == self.pci.device
                    && r.interface_attribute.inode > 0
            })
        {
            return Err(Error::State("invalid saved controller verification".into()));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
enum Outcome {
    Pending {},
    Armed { verification: Verification },
    Retired {},
    Failed { stage: String, message: String },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    policy_id: PolicyId,
    revision: u64,
    selector: Selector,
    retired: bool,
    last_request: Change,
    outcome: Outcome,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Disk {
    schema_version: u32,
    revision: u64,
    records: Vec<Record>,
}
impl Disk {
    fn empty() -> Self {
        Self {
            schema_version: 1,
            revision: 0,
            records: Vec::new(),
        }
    }
    fn validate(&self) -> Result<()> {
        if self.schema_version != 1 || self.records.len() > 16 {
            return Err(Error::State(
                "unsupported or oversized controller state".into(),
            ));
        }
        let mut addresses = std::collections::BTreeSet::new();
        if self.records.iter().map(|r| r.revision).max().unwrap_or(0) != self.revision {
            return Err(Error::State(
                "controller global revision has no retained record".into(),
            ));
        }
        let mut ids = std::collections::BTreeSet::new();
        let mut requests = std::collections::BTreeSet::new();
        for r in &self.records {
            r.last_request.validate()?;
            if !ids.insert(&r.policy_id)
                || !requests.insert(&r.last_request.request_id)
                || r.last_request.policy_id != r.policy_id
                || r.revision == 0
                || r.revision > self.revision
                || r.last_request.expected_revision.checked_add(1) != Some(r.revision)
            {
                return Err(Error::State("inconsistent controller history".into()));
            }
            if let Mutation::Enroll { selector, .. } = &r.last_request.mutation
                && selector != &r.selector
            {
                return Err(Error::State("saved controller selector mismatch".into()));
            }
            if !r.retired && !addresses.insert(&r.selector.address) {
                return Err(Error::State("duplicate active controller selection".into()));
            }
            if r.retired && !matches!(r.outcome, Outcome::Retired {}) {
                return Err(Error::State(
                    "retired controller has a nonterminal outcome".into(),
                ));
            }
            match &r.outcome {
                Outcome::Armed { verification } => {
                    verification.validate()?;
                    if r.retired
                        || matches!(r.last_request.mutation, Mutation::Retire { .. })
                        || verification.boot_id != r.last_request.mutation.boot()
                    {
                        return Err(Error::State(
                            "saved controller verification conflicts with its request".into(),
                        ));
                    }
                }
                Outcome::Retired {} => {
                    if !r.retired || !matches!(r.last_request.mutation, Mutation::Retire { .. }) {
                        return Err(Error::State("invalid controller retirement".into()));
                    }
                }
                Outcome::Pending {} => {}
                Outcome::Failed { stage, message } => {
                    if !matches!(stage.as_str(), "arm" | "retire")
                        || message.len() > 512
                        || message.chars().any(char::is_control)
                    {
                        return Err(Error::State("invalid controller failure record".into()));
                    }
                }
            }
        }
        Ok(())
    }
}
#[derive(Clone, Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
enum Writer {
    Idle,
    InFlight {
        policy_id: PolicyId,
        request_id: RequestId,
    },
    Unconfirmed,
}
#[derive(Clone, Serialize)]
struct View {
    schema_version: u32,
    helper_epoch: InstanceId,
    writer: Writer,
    historical: Disk,
    verified_in_this_helper: Vec<PolicyId>,
    semantics: &'static str,
}
struct Inner {
    store: store::Store,
    pins: BTreeMap<PolicyId, PinnedController>,
}
pub(crate) struct Service {
    epoch: InstanceId,
    inner: Mutex<Inner>,
    view: Mutex<View>,
    slots: Arc<tokio::sync::Semaphore>,
}
impl Service {
    pub(crate) fn open(path: &Path) -> Result<Self> {
        let store = store::Store::open(path)?;
        let epoch = InstanceId::new(crate::native_state::uuid("/proc/sys/kernel/random/uuid")?)?;
        Self::from_store(store, epoch)
    }
    fn from_store(store: store::Store, epoch: InstanceId) -> Result<Self> {
        let view = View {
            schema_version: 1,
            helper_epoch: epoch.clone(),
            writer: Writer::Idle,
            historical: store.disk().clone(),
            verified_in_this_helper: Vec::new(),
            semantics: "historical last-request results; same-helper verification is an observation, not current kernel authority; older requests are unknown or expired; no device authorization",
        };
        Ok(Self {
            epoch,
            inner: Mutex::new(Inner {
                store,
                pins: BTreeMap::new(),
            }),
            view: Mutex::new(view),
            slots: Arc::new(tokio::sync::Semaphore::new(1)),
        })
    }
    fn snapshot(&self) -> Result<View> {
        self.view
            .lock()
            .map(|v| v.clone())
            .map_err(|_| Error::State("controller observation view poisoned".into()))
    }
    fn writer(&self, value: Writer) -> Result<()> {
        self.view
            .lock()
            .map_err(|_| Error::State("controller observation view poisoned".into()))?
            .writer = value;
        Ok(())
    }
    fn publish(&self, inner: &Inner) -> Result<()> {
        let mut v = self
            .view
            .lock()
            .map_err(|_| Error::State("controller observation view poisoned".into()))?;
        if inner.store.poisoned() {
            v.writer = Writer::Unconfirmed;
            v.verified_in_this_helper.clear();
        } else {
            v.historical = inner.store.disk().clone();
            v.verified_in_this_helper = inner.pins.keys().cloned().collect();
            v.writer = Writer::Idle;
        }
        Ok(())
    }
    fn execute(
        &self,
        change: Change,
        conflicts: impl FnOnce(&PinnedController) -> Result<()>,
    ) -> Result<serde_json::Value> {
        let mut inner = match self.inner.lock() {
            Ok(inner) => inner,
            Err(_) => {
                self.writer(Writer::Unconfirmed)?;
                return Err(Error::State("controller store mutex poisoned".into()));
            }
        };
        let result = (|| {
            if let Some(old) = inner.store.replay(&change)? {
                return Ok(serde_json::json!({"historical":true,"record":old}));
            }
            let selector = inner.store.selection(&change)?;
            let mut pin = PinnedController::open(&selector.address)?;
            let observed = pin.prepare(&selector, change.mutation.boot())?;
            conflicts(&pin)?;
            if matches!(change.mutation, Mutation::Retire { .. }) && !observed.arrivals.is_empty() {
                return Err(Error::State("controller retirement requires an empty controller; device authorization is unchanged".into()));
            }
            inner.store.begin(&change, selector)?;
            inner.pins.remove(&change.policy_id);
            // Pending disk state is observable while the actual kernel owner runs.
            {
                let mut view = self
                    .view
                    .lock()
                    .map_err(|_| Error::State("controller observation view poisoned".into()))?;
                view.historical = inner.store.disk().clone();
                view.verified_in_this_helper = inner.pins.keys().cloned().collect();
            }
            let operation = if matches!(change.mutation, Mutation::Retire { .. }) {
                pin.inspect().and_then(|s| {
                    if s == observed && s.arrivals.is_empty() {
                        Ok(Outcome::Retired {})
                    } else {
                        Err(Error::State("controller changed before retirement".into()))
                    }
                })
            } else {
                pin.arm().map(|s| Outcome::Armed {
                    verification: Verification::observed(&self.epoch, &s),
                })
            };
            match operation {
                Ok(outcome) => {
                    let armed = matches!(outcome, Outcome::Armed { .. });
                    let record = inner.store.complete(&change, outcome)?;
                    if armed {
                        inner.pins.insert(change.policy_id.clone(), pin);
                    }
                    Ok(serde_json::json!({"historical":false,"record":record}))
                }
                Err(error) => {
                    let message = error
                        .to_string()
                        .chars()
                        .filter(|c| !c.is_control())
                        .take(128)
                        .collect();
                    let stage = if matches!(change.mutation, Mutation::Retire { .. }) {
                        "retire"
                    } else {
                        "arm"
                    };
                    inner.store.complete(
                        &change,
                        Outcome::Failed {
                            stage: stage.into(),
                            message,
                        },
                    )?;
                    Err(error)
                }
            }
        })();
        self.publish(&inner)?;
        result
    }
}

pub(crate) async fn serve(
    mut stream: tokio::net::UnixStream,
    app: Arc<crate::linux::App>,
    uid: u32,
    command: Command,
) -> Result<()> {
    let result = handle(app, uid, command).await;
    let response = match result {
        Ok(v) => crate::Response::success(v),
        Err(e) => crate::Response::failure(e),
    };
    crate::linux::write_frame(&mut stream, &response).await
}
async fn handle(
    app: Arc<crate::linux::App>,
    uid: u32,
    command: Command,
) -> Result<serde_json::Value> {
    if uid != 0 {
        return Err(Error::Permission(
            "controller enrollment requires root".into(),
        ));
    }
    let service=app.controllers.clone().ok_or_else(||Error::State("controller management is disabled; administrator must configure --controller-state-dir".into()))?;
    match command {
        Command::List {} => serde_json::to_value(service.snapshot()?)
            .map_err(|_| Error::State("controller snapshot serialization failed".into())),
        Command::Query {
            policy_id,
            request_id,
        } => {
            let view = service.snapshot()?;
            let settled = matches!(view.writer, Writer::Idle);
            let record = view
                .historical
                .records
                .iter()
                .find(|r| r.policy_id == policy_id && r.last_request.request_id == request_id)
                .cloned();
            Ok(
                serde_json::json!({"schema_version":1,"writer":view.writer,"helper_epoch":view.helper_epoch,"global_revision":view.historical.revision,"retained_historical_result":record,"settled_writer":settled,"absent_means":"unknown_or_expired_not_no_effect","current_kernel_authority":false}),
            )
        }
        Command::Inspect { address } => {
            let permit = service
                .slots
                .clone()
                .try_acquire_owned()
                .map_err(|_| Error::State("controller operation already running".into()))?;
            tokio::task::spawn_blocking(move || {
                let _permit = permit;
                let mut pin = PinnedController::open(&address)?;
                serde_json::to_value(pin.inspect()?)
                    .map_err(|_| Error::State("controller inspection serialization failed".into()))
            })
            .await
            .map_err(|_| Error::State("controller inspection worker failed".into()))?
        }
        Command::Change { change } => {
            change.validate()?;
            let permit = service.slots.clone().try_acquire_owned().map_err(|_| {
                Error::State(
                    "controller operation already running; query the original request".into(),
                )
            })?;
            let guard =
                tokio::time::timeout(Duration::from_secs(30), app.state.clone().lock_owned())
                    .await
                    .map_err(|_| {
                        Error::Timeout("controller mutation queue; no new mutation started".into())
                    })?;
            service.writer(Writer::InFlight {
                policy_id: change.policy_id.clone(),
                request_id: change.request_id.clone(),
            })?;
            let worker = service.clone();
            let result = run_owned(guard, permit, move |state| {
                worker.execute(change, |pin| {
                    super::linux::controller_conflicts(&app, state, pin)
                })
            })
            .await;
            match result {
                Ok(value) => value,
                Err(_) => {
                    service.writer(Writer::Unconfirmed)?;
                    Err(Error::State(
                        "controller worker failed; reconcile the original request".into(),
                    ))
                }
            }
        }
    }
}

// The real closure owns the serialization guard and quota even if its waiter is dropped.
async fn run_owned<T: Send + 'static>(
    guard: tokio::sync::OwnedMutexGuard<crate::linux::ManagedState>,
    permit: tokio::sync::OwnedSemaphorePermit,
    work: impl FnOnce(&crate::linux::ManagedState) -> Result<T> + Send + 'static,
) -> std::result::Result<Result<T>, tokio::task::JoinError> {
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        let guard = guard;
        work(&guard)
    })
    .await
}
#[cfg(test)]
mod tests;

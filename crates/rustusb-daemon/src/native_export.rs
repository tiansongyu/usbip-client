//! Broker-only Native registrations and supervised, single-owner stub channels.
use crate::{
    Request, Response, ancillary, linux,
    native_channel::{command_frame, decode, mutation_guard, store, with_store},
    native_state::{self, ExportRecord, Owner, StubPhase, StubRecord},
};
use rustusb_core::application::BrokerCorrelation;
use rustusb_core::{DeviceKey, Error, ExportBackend, Result};
use rustusb_native::export_channel::{ExportCapability, RegistrationSnapshot, StubReceipt};
use rustusb_protocol::RemoteDevice;
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeMap,
    os::fd::OwnedFd,
    path::{Path, PathBuf},
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::UnixStream,
    sync::{mpsc, oneshot, watch},
};
const DEADLINE: Duration = Duration::from_secs(5);
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExportDescription {
    schema_version: u32,
    lease_id: String,
    registration: RegistrationSnapshot,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StubDescription {
    schema_version: u32,
    lease_id: String,
    channel_id: String,
}
#[derive(Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Wire {
    Commit { channel_id: String },
    Close { channel_id: String },
}
enum Command {
    Commit(oneshot::Sender<Result<serde_json::Value>>),
    Close(oneshot::Sender<Result<serde_json::Value>>),
}
struct Control {
    commands: mpsc::Sender<Command>,
    invalidated: watch::Receiver<bool>,
}
impl Control {
    fn start(stream: UnixStream, id: String, can_commit: bool) -> Self {
        let (commands, receive) = mpsc::channel(2);
        let (notify, invalidated) = watch::channel(false);
        tokio::spawn(actor(stream, id, can_commit, receive, notify));
        Self {
            commands,
            invalidated,
        }
    }
    async fn close(self) -> Result<()> {
        let (send, receive) = oneshot::channel();
        self.commands
            .send(Command::Close(send))
            .await
            .map_err(|_| Error::State("Native control ended; cleanup unconfirmed".into()))?;
        let value = receive.await.map_err(|_| {
            Error::State("Native cleanup response lost; inspect pending state".into())
        })??;
        if value != serde_json::json!({"closed":true}) {
            return Err(Error::Protocol("invalid Native cleanup receipt".into()));
        }
        Ok(())
    }
}
async fn actor(
    mut stream: UnixStream,
    id: String,
    can_commit: bool,
    mut commands: mpsc::Receiver<Command>,
    invalidated: watch::Sender<bool>,
) {
    let mut committed = false;
    loop {
        let mut unexpected = [0u8; 1];
        let command = tokio::select! { command=commands.recv()=>command, _=stream.read(&mut unexpected)=>None };
        let (wire, reply, end) = match command {
            Some(Command::Commit(reply)) if can_commit && !committed => {
                committed = true;
                (
                    Wire::Commit {
                        channel_id: id.clone(),
                    },
                    reply,
                    false,
                )
            }
            Some(Command::Commit(reply)) => {
                let _ = reply.send(Err(Error::State(
                    "Native commit repeated or invalid for registration".into(),
                )));
                continue;
            }
            Some(Command::Close(reply)) => (
                Wire::Close {
                    channel_id: id.clone(),
                },
                reply,
                true,
            ),
            None => break,
        };
        // Only this actor owns framing. Caller cancellation never abandons a half-read response.
        let result = async {
            linux::write_frame(&mut stream, &wire).await?;
            let bytes = linux::read_frame(&mut stream, Duration::from_secs(120)).await?;
            decode(
                serde_json::from_slice(&bytes)
                    .map_err(|_| Error::Protocol("invalid Native response frame".into()))?,
            )
        }
        .await;
        let failed = result.is_err();
        let _ = reply.send(result);
        if failed || end {
            break;
        }
    }
    drop(stream);
    invalidated.send_replace(true);
}
/// A device registration with a real preexport publication snapshot and continuous control ownership.
/// Drop initiates cleanup; only `close().await` is a verified cleanup receipt.
pub struct NativeExportHandle {
    socket: PathBuf,
    lease_id: String,
    registration: RegistrationSnapshot,
    control: Control,
}
impl NativeExportHandle {
    /// Helper-issued registration identity, not a transferable capability.
    pub fn lease_id(&self) -> &str {
        &self.lease_id
    }
    /// Stable backend-qualified approved device identity.
    pub fn device_key(&self) -> &DeviceKey {
        &self.registration.device_key
    }
    /// Real preexport registration descriptors, not the current exported configuration.
    pub fn published_device(&self) -> &RemoteDevice {
        &self.registration.published_device
    }
    /// Atomically reserve one anonymous stub channel; a concurrent or unresolved channel is rejected.
    pub async fn open_stub(&self) -> Result<NativeStubHandle> {
        self.open_stub_inner(None).await
    }
    /// Reserve one stub with an independent durable application attempt.
    pub async fn open_stub_tracked(
        &self,
        correlation: BrokerCorrelation,
    ) -> Result<NativeStubHandle> {
        correlation.validate()?;
        self.open_stub_inner(Some(correlation)).await
    }
    async fn open_stub_inner(
        &self,
        correlation: Option<BrokerCorrelation>,
    ) -> Result<NativeStubHandle> {
        if *self.control.invalidated.borrow() {
            return Err(Error::State("Native registration has ended".into()));
        }
        let mut stream = linux::connect_trusted(&self.socket).await?;
        let request = match correlation {
            Some(correlation) => Request::OpenNativeStubTracked {
                lease_id: self.lease_id.clone(),
                correlation,
            },
            None => Request::OpenNativeStub {
                lease_id: self.lease_id.clone(),
            },
        };
        let description: StubDescription = decode(linux::exchange(&mut stream, &request).await?)?;
        if description.schema_version != 1 || description.lease_id != self.lease_id {
            return Err(Error::Protocol("invalid Native stub description".into()));
        }
        native_state::validate_id(&description.channel_id)?;
        tokio::time::timeout(DEADLINE, stream.write_all(&[ancillary::FD_MARKER]))
            .await
            .map_err(|_| Error::Timeout("Native stub acknowledgement".into()))??;
        let fd = ancillary::receive_fd(&stream).await?;
        if rustix::net::sockopt::socket_type(&fd).map_err(std::io::Error::from)?
            != rustix::net::SocketType::STREAM
            || rustix::net::sockopt::socket_domain(&fd).map_err(std::io::Error::from)?
                != rustix::net::AddressFamily::UNIX
            || rustix::net::sockopt::socket_acceptconn(&fd).map_err(std::io::Error::from)?
        {
            return Err(Error::Protocol(
                "Native stub relay is not an anonymous stream".into(),
            ));
        }
        let relay = std::os::unix::net::UnixStream::from(fd);
        if !relay.local_addr()?.is_unnamed() || !relay.peer_addr()?.is_unnamed() {
            return Err(Error::Protocol(
                "Native stub relay has a named endpoint".into(),
            ));
        }
        relay.set_nonblocking(true)?;
        Ok(NativeStubHandle {
            relay: Some(UnixStream::from_std(relay)?),
            control: Control::start(stream, description.channel_id.clone(), true),
            channel_id: description.channel_id,
            lease_id: self.lease_id.clone(),
        })
    }
    /// Stop active stubs, release the export and verify the complete original driver/configuration baseline.
    pub async fn close(self) -> Result<()> {
        self.control.close().await
    }
    /// Subscribe to orderly closure or loss of the registration control connection.
    pub fn invalidated(&self) -> watch::Receiver<bool> {
        self.control.invalidated.clone()
    }
}
/// A committed server stub receipt. USED confirms kernel socket ownership, not remote enumeration.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeStubReceipt {
    /// Original registration identifier.
    pub lease_id: String,
    /// Unique channel in that registration.
    pub channel_id: String,
    /// Actual original attribute, retained K and current configuration observation.
    pub kernel: StubReceipt,
}
/// One anonymous stub relay and its continuously supervised helper transaction.
pub struct NativeStubHandle {
    relay: Option<UnixStream>,
    control: Control,
    channel_id: String,
    lease_id: String,
}
impl NativeStubHandle {
    /// Unique helper-issued stub identity, not a bearer capability.
    pub fn channel_id(&self) -> &str {
        &self.channel_id
    }
    /// Original helper registration to which this stub belongs.
    pub fn lease_id(&self) -> &str {
        &self.lease_id
    }
    /// Take R once. Keep it idle until `commit` and the real IMPORT response have both succeeded.
    pub fn take_relay(&mut self) -> Result<UnixStream> {
        self.relay
            .take()
            .ok_or_else(|| Error::State("Native stub relay already taken".into()))
    }
    /// Install K into the original stub, returning at USED. Do not start raw pumping before IMPORT reply flush.
    pub async fn commit(&mut self) -> Result<NativeStubReceipt> {
        if self.relay.is_some() {
            return Err(Error::State("take Native stub relay before commit".into()));
        }
        let (send, receive) = oneshot::channel();
        self.control
            .commands
            .send(Command::Commit(send))
            .await
            .map_err(|_| Error::State("Native stub control ended".into()))?;
        let value = receive.await.map_err(|_| {
            Error::State("Native stub response lost; inspect pending state".into())
        })??;
        let receipt: NativeStubReceipt = serde_json::from_value(value)
            .map_err(|_| Error::Protocol("invalid Native stub receipt".into()))?;
        if receipt.lease_id != self.lease_id
            || receipt.channel_id != self.channel_id
            || receipt.kernel.usbip_status != 2
        {
            return Err(Error::Protocol(
                "Native stub receipt identity mismatch".into(),
            ));
        }
        Ok(receipt)
    }
    /// Explicitly shut down the owned socket and await independent AVAILABLE/gone verification.
    pub async fn close(self) -> Result<()> {
        self.control.close().await
    }
    /// Subscribe to helper or registration closure.
    pub fn invalidated(&self) -> watch::Receiver<bool> {
        self.control.invalidated.clone()
    }
}
/// Register one root-policy-approved device for the dedicated non-root broker.
/// The helper captures real configured descriptors before binding and retains the original kernfs attribute.
pub async fn prepare_native_export(socket: &Path, device_id: String) -> Result<NativeExportHandle> {
    prepare_export(socket, device_id, None).await
}
/// Register an export with durable application correlation and terminal evidence.
pub async fn prepare_native_export_tracked(
    socket: &Path,
    device_id: String,
    correlation: BrokerCorrelation,
) -> Result<NativeExportHandle> {
    correlation.validate()?;
    prepare_export(socket, device_id, Some(correlation)).await
}
async fn prepare_export(
    socket: &Path,
    device_id: String,
    correlation: Option<BrokerCorrelation>,
) -> Result<NativeExportHandle> {
    crate::validate_device_id(&device_id)?;
    let expected = DeviceKey::native(&device_id)?;
    let mut stream = linux::connect_trusted(socket).await?;
    let request = match correlation {
        Some(correlation) => Request::PrepareNativeExportTracked {
            device_id,
            correlation,
        },
        None => Request::PrepareNativeExport { device_id },
    };
    let description: ExportDescription = decode(linux::exchange(&mut stream, &request).await?)?;
    native_state::validate_id(&description.lease_id)?;
    description.registration.validate()?;
    if description.schema_version != 1 || description.registration.device_key != expected {
        return Err(Error::Protocol(
            "invalid Native registration description".into(),
        ));
    }
    Ok(NativeExportHandle {
        socket: socket.to_owned(),
        lease_id: description.lease_id.clone(),
        registration: description.registration,
        control: Control::start(stream, description.lease_id, false),
    })
}
#[derive(Default)]
pub(crate) struct Registry(Mutex<BTreeMap<String, Arc<Live>>>);
impl Registry {
    pub(crate) fn export_scopes(&self) -> Result<Vec<PathBuf>> {
        Ok(lock(&self.0)?
            .values()
            .map(|live| live.registration.generation.sysfs_path.clone())
            .collect())
    }
}
struct State {
    capability: ExportCapability,
    slot: Option<String>,
    failed: bool,
    closing: bool,
}
struct Live {
    id: String,
    owner: Owner,
    registration: RegistrationSnapshot,
    state: Mutex<State>,
    stop: watch::Sender<bool>,
    changed: watch::Sender<u64>,
}
fn lock<T>(mutex: &Mutex<T>) -> Result<std::sync::MutexGuard<'_, T>> {
    mutex
        .lock()
        .map_err(|_| Error::State("Native export ownership lock poisoned".into()))
}
async fn respond(stream: &mut UnixStream, result: Result<serde_json::Value>) -> Result<()> {
    let response = match result {
        Ok(value) => Response::success(value),
        Err(error) => Response::failure(error),
    };
    linux::write_frame(stream, &response).await
}
async fn initial(
    app: Arc<linux::App>,
    uid: u32,
    pid: Option<i32>,
    device_id: String,
    guard: tokio::sync::OwnedMutexGuard<linux::ManagedState>,
    cancel: Arc<AtomicBool>,
    correlation: Option<BrokerCorrelation>,
) -> Result<Arc<Live>> {
    let _guard = guard;
    let store = store(&app)?;
    let owner = native_state::owner(uid, pid)?;
    let id = native_state::uuid("/proc/sys/kernel/random/uuid")?;
    let result = async {
        let before = linux::current_device(&device_id).await?;
        if before.exported || !before.export_blockers.is_empty() {
            return Err(Error::UnsafeDevice(
                "Native registration requires an eligible unexported device".into(),
            ));
        }
        let bus = before.bus_id.clone();
        let capture_id = device_id.clone();
        let registration =
            tokio::task::spawn_blocking(move || RegistrationSnapshot::capture(&capture_id, bus))
                .await
                .map_err(|_| {
                    Error::State("Native registration observation task failed".into())
                })??;
        let immediate = linux::current_device(&device_id).await?;
        if immediate.exported || !immediate.export_blockers.is_empty()
            || !linux::same_device_generation(&before, &immediate)
            || immediate.device_address != registration.generation.device_number
            || immediate.vendor_id != registration.generation.vendor_id
            || immediate.product_id != registration.generation.product_id
            || immediate.bus_id != registration.generation.bus_id {
            return Err(Error::State("Native authorized generation changed before registration".into()));
        }
        let initial_store = store.clone();
        let record = ExportRecord {
            correlation,
            lease_id: id.clone(),
            owner: owner.clone(),
            registration: registration.clone(),
            bound: None,
            stub: None,
        };
        tokio::task::spawn_blocking(move || {
            with_store(&initial_store, |s| s.reserve_export(record))
        })
        .await
        .map_err(|_| Error::State("Native registration journal task failed".into()))??;
        let prebind = registration.clone(); let prebind_cancel = cancel.clone();
        tokio::task::spawn_blocking(move || {
            if prebind_cancel.load(Ordering::Acquire) { return Err(Error::State("Native export cancelled before binding; registration retained for read-only reconciliation".into())); }
            if !prebind.restored()? { return Err(Error::State("Native original generation or baseline changed before binding".into())); }
            Ok::<_, Error>(())
        }).await.map_err(|_| Error::State("Native pre-bind observation task failed".into()))??;
        // This task owns the mutation guard across the actual tool operation and subsequent observation.
        app.backend
            .export_fresh(registration.generation.bus_id.clone())
            .await?;
        let observed_registration = registration.clone();
        let observed_store = store.clone();
        let observed_id = id.clone();
        let capability = tokio::task::spawn_blocking(move || {
            let capability = ExportCapability::after_bind(observed_registration)?;
            with_store(&observed_store, |s| {
                s.update_export(&observed_id, |r| {
                    r.bound = Some(capability.receipt().clone());
                    Ok(())
                })
            })?;
            Ok::<_, Error>(capability)
        })
        .await
        .map_err(|_| Error::State("Native bound observation task failed".into()))??;
        tracing::info!(operation="native_secure_export", lease_id=%id, phase="registered", "original Native export attribute retained");
        let (stop, _) = watch::channel(false);
        let (changed, _) = watch::channel(0);
        Ok(Arc::new(Live {
            id: id.clone(),
            owner,
            registration,
            state: Mutex::new(State {
                capability,
                slot: None,
                failed: false,
                closing: false,
            }),
            stop,
            changed,
        }))
    }
    .await;
    if result.is_err()
        && let Ok(mut store) = store.lock()
    {
        store.unresolved(&id);
    }
    result
}
pub(crate) async fn serve_export(
    mut stream: UnixStream,
    app: Arc<linux::App>,
    uid: u32,
    pid: Option<i32>,
    device_id: String,
    correlation: Option<BrokerCorrelation>,
) -> Result<()> {
    if let Err(error) = correlation
        .as_ref()
        .map_or(Ok(()), BrokerCorrelation::validate)
        .and_then(|()| app.policy.authorize_secure_native(uid, &device_id))
    {
        return respond(&mut stream, Err(error)).await;
    }
    let cancel = Arc::new(AtomicBool::new(false));
    let mut stop = app.native_stop.subscribe();
    let guard = match mutation_guard(&mut stream, app.state.clone(), &mut stop, &cancel).await {
        Ok(guard) => guard,
        Err(error) => return respond(&mut stream, Err(error)).await,
    };
    let mut task = tokio::spawn(initial(
        app.clone(),
        uid,
        pid,
        device_id,
        guard,
        cancel.clone(),
        correlation,
    ));
    let mut unexpected = [0u8; 1];
    let result = tokio::select! { biased;
        _=stop.changed()=>{cancel.store(true,Ordering::Release);task.await},
        _=stream.read(&mut unexpected)=>{cancel.store(true,Ordering::Release);task.await},
        result=&mut task=>result,
    }
    .map_err(|_| Error::State("Native registration task failed; any intent retained".into()))
    .and_then(|r| r);
    let live = match result {
        Ok(live) => live,
        Err(error) => return respond(&mut stream, Err(error)).await,
    };
    let operation:Result<()>=async {
        if cancel.load(Ordering::Acquire)||*stop.borrow(){return Err(Error::State("Native registration control ended during preparation".into()));}
        lock(&app.native_exports.0)?.insert(live.id.clone(),live.clone());
        let description=ExportDescription{schema_version:1,lease_id:live.id.clone(),registration:live.registration.clone()};
        respond(&mut stream,serde_json::to_value(description).map_err(|_| Error::Protocol("cannot encode Native registration".into()))).await?;
        let bytes=tokio::select!{_=stop.changed()=>return Err(Error::State("helper stopping Native exports".into())),bytes=command_frame(&mut stream)=>bytes?};
        match serde_json::from_slice::<Wire>(&bytes).map_err(|_|Error::Protocol("invalid Native registration command".into()))? {
            Wire::Close{channel_id} if channel_id==live.id=>Ok(()),
            _=>Err(Error::Permission("invalid Native registration control action".into())),
        }
    }.await;
    let cleanup = cleanup_export(app.clone(), live.clone()).await;
    if let Ok(mut registry) = app.native_exports.0.lock() {
        registry.remove(&live.id);
    }
    respond(
        &mut stream,
        cleanup
            .and(operation)
            .map(|_| serde_json::json!({"closed":true})),
    )
    .await
}
async fn cleanup_export(app: Arc<linux::App>, live: Arc<Live>) -> Result<()> {
    let store = store(&app)?;
    let mut changed = live.changed.subscribe();
    {
        let mut state = lock(&live.state)?;
        state.closing = true;
    }
    live.stop.send_replace(true);
    let result = async {
        // Never hold the mutation guard while waiting for a stub worker that also needs it.
        loop {
            let (active, failed) = {
                let state = lock(&live.state)?;
                (state.slot.is_some(), state.failed)
            };
            if failed {
                return Err(Error::State(
                    "Native stub cleanup unresolved; export lease retained".into(),
                ));
            }
            if !active {
                break;
            }
            changed
                .changed()
                .await
                .map_err(|_| Error::State("Native stub supervisor ended unexpectedly".into()))?;
        }
        let _guard = app.state.clone().lock_owned().await;
        let observed = live.clone();
        let gone = tokio::task::spawn_blocking(move || {
            let state = lock(&observed.state)?;
            if observed.registration.gone()? {
                return Ok(true);
            }
            state.capability.available()?;
            Ok::<_, Error>(false)
        })
        .await
        .map_err(|_| Error::State("Native unexport observation task failed".into()))??;
        if !gone {
            app.backend
                .unexport(live.registration.generation.bus_id.clone())
                .await?;
        }
        let verified = live.clone();
        let verified_store = store.clone();
        tokio::task::spawn_blocking(move || {
            let proof=verified.registration.wait_restored_proven()?;
            with_store(&verified_store, |s| s.retire_export_verified(&verified.id,proof))?;
            tracing::info!(operation="native_secure_export", lease_id=%verified.id, phase="retired", "original Native baseline restored or original generation gone");
            Ok(())
        })
        .await
        .map_err(|_| Error::State("Native unexport verification task failed".into()))?
    }
    .await;
    if result.is_err()
        && let Ok(mut store) = store.lock()
    {
        store.unresolved(&live.id);
    }
    result
}
pub(crate) async fn serve_stub(
    mut stream: UnixStream,
    app: Arc<linux::App>,
    uid: u32,
    pid: Option<i32>,
    lease_id: String,
    correlation: Option<BrokerCorrelation>,
) -> Result<()> {
    let prepared = async {
        if let Some(c) = &correlation {
            c.validate()?;
        }
        app.policy.authorize_broker(uid)?;
        native_state::validate_id(&lease_id)?;
        let live = lock(&app.native_exports.0)?
            .get(&lease_id)
            .cloned()
            .ok_or_else(|| Error::Permission("Native export is not live in this helper".into()))?;
        if native_state::owner(uid, pid)? != live.owner {
            return Err(Error::Permission(
                "Native export belongs to another OS process".into(),
            ));
        }
        let store = store(&app)?;
        let prepare_live = live.clone();
        let prepare_store = store.clone();
        let (id, channel, relay) = tokio::task::spawn_blocking(move || {
            let mut state = lock(&prepare_live.state)?;
            if state.closing || state.failed || state.slot.is_some() {
                return Err(Error::State(
                    "Native export is closing, occupied or unresolved".into(),
                ));
            }
            let (channel, relay) = state.capability.prepare_stub()?;
            let id = native_state::uuid("/proc/sys/kernel/random/uuid")?;
            with_store(&prepare_store, |s| {
                s.reserve_stub(
                    &prepare_live.id,
                    StubRecord {
                        correlation,
                        channel_id: id.clone(),
                        state: StubPhase::Prepared {},
                    },
                )
            })?;
            state.slot = Some(id.clone());
            Ok::<_, Error>((id, channel, relay))
        })
        .await
        .map_err(|_| Error::State("Native stub preparation task failed".into()))??;
        Ok::<_, Error>((live, store, id, channel, relay))
    }
    .await;
    let (live, store, id, channel, relay) = match prepared {
        Ok(prepared) => prepared,
        Err(error) => return respond(&mut stream, Err(error)).await,
    };
    let channel = Arc::new(Mutex::new(channel));
    let cancel = Arc::new(AtomicBool::new(false));
    let mut stop = live.stop.subscribe();
    let operation:Result<()>=async {
        if *stop.borrow(){return Err(Error::State("Native export is stopping".into()));}
        let description=StubDescription{schema_version:1,lease_id:lease_id.clone(),channel_id:id.clone()};
        respond(&mut stream,serde_json::to_value(description).map_err(|_|Error::Protocol("cannot encode Native stub description".into()))).await?;
        let mut ack=[0u8;1];tokio::time::timeout(DEADLINE,stream.read_exact(&mut ack)).await.map_err(|_|Error::Timeout("Native stub handoff acknowledgement".into()))??;
        if ack!=[ancillary::FD_MARKER]{return Err(Error::Protocol("invalid Native stub acknowledgement".into()));}
        let relay:OwnedFd=relay.into();let sent=ancillary::send_fd(&stream,&relay).await;drop(relay);sent?;
        let mut committed=false;
        loop{
            let bytes=tokio::select!{_=stop.changed()=>return Err(Error::State("Native export control ended".into())),bytes=async{
                if committed{command_frame(&mut stream).await}else{tokio::time::timeout(DEADLINE,command_frame(&mut stream)).await.map_err(|_|Error::Timeout("Native stub commit not received".into()))?}
            }=>bytes?};
            match serde_json::from_slice::<Wire>(&bytes).map_err(|_|Error::Protocol("invalid Native stub command".into()))?{
                Wire::Close{channel_id} if channel_id==id=>return Ok(()),
                Wire::Commit{channel_id} if channel_id==id&&!committed=>{
                    committed=true;
                    let guard=mutation_guard(&mut stream,app.state.clone(),&mut stop,&cancel).await?;
                    let work_channel=channel.clone();let work_store=store.clone();let work_id=id.clone();let work_lease=lease_id.clone();let work_cancel=cancel.clone();
                    let mut task=tokio::task::spawn_blocking(move||{
                        let _guard=guard;let mut channel=lock(&work_channel)?;
                        let receipt=channel.commit(&work_cancel,||with_store(&work_store,|s|s.update_export(&work_lease,|r|set_stub(r,&work_id,StubPhase::Intent{}))))?;
                        with_store(&work_store,|s|s.update_export(&work_lease,|r|set_stub(r,&work_id,StubPhase::Observed{receipt:receipt.clone()})))?;
                        Ok::<_,Error>(receipt)
                    });
                    let mut unexpected=[0u8;1];
                    let receipt=tokio::select!{biased;_=stop.changed()=>{cancel.store(true,Ordering::Release);task.await},_=stream.read(&mut unexpected)=>{cancel.store(true,Ordering::Release);task.await},result=&mut task=>result}.map_err(|_|Error::State("Native stub mutation task failed; intent retained".into()))??;
                    if cancel.load(Ordering::Acquire){return Err(Error::State("Native stub control ended during commit".into()));}
                    respond(&mut stream,serde_json::to_value(NativeStubReceipt{lease_id:lease_id.clone(),channel_id:id.clone(),kernel:receipt}).map_err(|_|Error::Protocol("cannot encode Native stub receipt".into()))).await?;
                }
                _=>return Err(Error::Permission("Native stub command repeated or belongs to another transaction".into())),
            }
        }
    }.await;
    cancel.store(true, Ordering::Release);
    let guard = app.state.clone().lock_owned().await;
    let cleanup_live = live.clone();
    let cleanup_id = id.clone();
    let cleanup = tokio::task::spawn_blocking(move || {
        let _guard = guard;
        let result = (|| {
            let proof = lock(&channel)?.shutdown_proven()?;
            with_store(&store, |s| {
                s.retire_stub_verified(&cleanup_live.id, &cleanup_id, proof)
            })
        })();
        if result.is_err()
            && let Ok(mut store) = store.lock()
        {
            store.unresolved(&cleanup_live.id);
        }
        let mut state = lock(&cleanup_live.state)?;
        if state.slot.as_deref() != Some(cleanup_id.as_str()) {
            return Err(Error::State(
                "Native in-memory slot ownership changed".into(),
            ));
        }
        if result.is_ok() {
            state.slot = None;
        } else {
            state.failed = true;
        }
        result
    })
    .await
    .map_err(|_| Error::State("Native stub cleanup task failed; intent retained".into()))
    .and_then(|r| r);
    if cleanup.is_err()
        && let Ok(mut state) = live.state.lock()
    {
        state.failed = true;
    }
    live.changed
        .send_modify(|version| *version = version.wrapping_add(1));
    respond(
        &mut stream,
        cleanup
            .and(operation)
            .map(|_| serde_json::json!({"closed":true})),
    )
    .await
}
fn set_stub(record: &mut ExportRecord, id: &str, phase: StubPhase) -> Result<()> {
    let stub = record
        .stub
        .as_mut()
        .filter(|s| s.channel_id == id)
        .ok_or_else(|| Error::State("Native stub durable identity changed".into()))?;
    stub.state = phase;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn cancelled_stub_commit_consumes_reply_before_close() -> Result<()> {
        let (client, mut server) = UnixStream::pair()?;
        let control = Control::start(client, "channel".into(), true);
        let (reply, receive) = oneshot::channel();
        control
            .commands
            .send(Command::Commit(reply))
            .await
            .map_err(|_| Error::State("test actor ended".into()))?;
        let request: Wire =
            serde_json::from_slice(&linux::read_frame(&mut server, DEADLINE).await?)
                .map_err(|_| Error::Protocol("test command".into()))?;
        assert!(matches!(request,Wire::Commit{channel_id} if channel_id=="channel"));
        drop(receive); // Simulate the broker's cancelled commit future, not a cancelled actor.
        let close = tokio::spawn(control.close());
        linux::write_frame(
            &mut server,
            &Response::success(serde_json::json!({"committed":true})),
        )
        .await?;
        let request: Wire =
            serde_json::from_slice(&linux::read_frame(&mut server, DEADLINE).await?)
                .map_err(|_| Error::Protocol("test command".into()))?;
        assert!(matches!(request,Wire::Close{channel_id} if channel_id=="channel"));
        linux::write_frame(
            &mut server,
            &Response::success(serde_json::json!({"closed":true})),
        )
        .await?;
        close
            .await
            .map_err(|_| Error::State("test close task".into()))??;
        Ok(())
    }
    #[tokio::test]
    async fn dropped_export_handle_closes_control_without_claiming_cleanup() -> Result<()> {
        let (client, mut server) = UnixStream::pair()?;
        let control = Control::start(client, "lease".into(), false);
        let mut invalidated = control.invalidated.clone();
        drop(control);
        let mut byte = [0u8; 1];
        assert_eq!(
            tokio::time::timeout(DEADLINE, server.read(&mut byte))
                .await
                .map_err(|_| Error::Timeout("test EOF".into()))??,
            0
        );
        if !*invalidated.borrow() {
            invalidated
                .changed()
                .await
                .map_err(|_| Error::State("test invalidation".into()))?;
        }
        assert!(*invalidated.borrow());
        Ok(())
    }
}

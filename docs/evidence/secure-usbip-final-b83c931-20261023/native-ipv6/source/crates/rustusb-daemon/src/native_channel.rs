//! Broker-only Native descriptor transactions over a continuously supervised control UDS.
use crate::{
    Request, Response, ancillary, linux,
    native_state::{self, Phase, Record, Store},
};
use rustusb_core::{DeviceKey, Error, Result};
use rustusb_native::channel::{ImportChannel, ImportReceipt};
use rustusb_protocol::RemoteDevice;
use serde::{Deserialize, Serialize};
use std::{
    os::fd::OwnedFd,
    path::Path,
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
/// Authenticated remote selection supplied only by the administrator's dedicated broker.
/// The helper validates its configured logical pool; it does not itself verify TLS.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecureImportTarget {
    /// Fixed logical server ID from root-controlled broker configuration.
    pub server_id: String,
    /// Stable backend-qualified device selection, distinct from the current BusID.
    pub device_key: DeviceKey,
    /// Bounded metadata from the actual authenticated IMPORT response.
    pub remote: RemoteDevice,
}
impl SecureImportTarget {
    pub(crate) fn validate(&self) -> Result<()> {
        crate::validate_server_id(&self.server_id)?;
        rustusb_native::channel::validate_remote(&self.remote)
    }
}
/// Independent secure-channel receipt; never an official plain-usbip endpoint record.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NativeImportReceipt {
    /// Random helper-issued identifier, not a bearer authorization token.
    pub channel_id: String,
    /// Authorized logical selection and remote metadata.
    pub target: SecureImportTarget,
    /// Strong actual kernel/local-generation receipt.
    pub kernel: ImportReceipt,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Description {
    schema_version: u32,
    channel_id: String,
}
#[derive(Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum CommandWire {
    Commit { channel_id: String },
    Close { channel_id: String },
}
enum Command {
    Commit(oneshot::Sender<Result<NativeImportReceipt>>),
    Close(oneshot::Sender<Result<()>>),
}
/// An owned local helper transaction. Drop closes control asynchronously and invalidates the channel.
/// Only `close().await` confirms successful cleanup; dropping this value is not a cleanup receipt.
pub struct NativeImportHandle {
    relay: Option<UnixStream>,
    commands: mpsc::Sender<Command>,
    invalidated: watch::Receiver<bool>,
}
impl NativeImportHandle {
    /// Take the anonymous relay endpoint exactly once, before starting the TLS pump and committing.
    pub fn take_relay(&mut self) -> Result<UnixStream> {
        self.relay
            .take()
            .ok_or_else(|| Error::State("Native relay was already taken".into()))
    }
    /// Start the actual kernel attach. Cancellation cannot interrupt the private control actor's frame exchange.
    pub async fn commit(&mut self) -> Result<NativeImportReceipt> {
        if self.relay.is_some() {
            return Err(Error::State(
                "start the relay before committing Native import".into(),
            ));
        }
        let (send, receive) = oneshot::channel();
        self.commands
            .send(Command::Commit(send))
            .await
            .map_err(|_| Error::State("Native control transaction ended".into()))?;
        receive.await.map_err(|_| {
            Error::State("Native commit response was lost; inspect pending state".into())
        })?
    }
    /// Shut down this channel and await independent kernel cleanup verification.
    pub async fn close(self) -> Result<()> {
        let (send, receive) = oneshot::channel();
        self.commands
            .send(Command::Close(send))
            .await
            .map_err(|_| Error::State("Native control ended; cleanup is unconfirmed".into()))?;
        receive.await.map_err(|_| {
            Error::State("Native cleanup response was lost; inspect pending state".into())
        })?
    }
    /// Subscribe to helper/control failure or orderly transaction completion.
    pub fn invalidated(&self) -> watch::Receiver<bool> {
        self.invalidated.clone()
    }
}
/// Prepare a broker-authorized anonymous channel without performing a kernel attach.
/// No caller-provided FD, sysfs path, or localhost endpoint is accepted.
pub async fn prepare_native_import(
    socket: &Path,
    target: SecureImportTarget,
) -> Result<NativeImportHandle> {
    target.validate()?;
    let mut stream = linux::connect_trusted(socket).await?;
    let response = linux::exchange(&mut stream, &Request::PrepareNativeImport { target }).await?;
    let description: Description = decode(response)?;
    if description.schema_version != 1 {
        return Err(Error::Protocol("unsupported Native channel schema".into()));
    }
    native_state::validate_id(&description.channel_id)?;
    tokio::time::timeout(DEADLINE, stream.write_all(&[ancillary::FD_MARKER]))
        .await
        .map_err(|_| Error::Timeout("Native descriptor acknowledgement".into()))??;
    let fd = ancillary::receive_fd(&stream).await?;
    if rustix::net::sockopt::socket_type(&fd).map_err(std::io::Error::from)?
        != rustix::net::SocketType::STREAM
        || rustix::net::sockopt::socket_domain(&fd).map_err(std::io::Error::from)?
            != rustix::net::AddressFamily::UNIX
        || rustix::net::sockopt::socket_acceptconn(&fd).map_err(std::io::Error::from)?
    {
        return Err(Error::Protocol(
            "Native relay must be a connected anonymous Unix stream".into(),
        ));
    }
    let relay = std::os::unix::net::UnixStream::from(fd);
    if !relay.local_addr()?.is_unnamed() || !relay.peer_addr()?.is_unnamed() {
        return Err(Error::Protocol("Native relay is not anonymous".into()));
    }
    relay.set_nonblocking(true)?;
    let relay = UnixStream::from_std(relay)?;
    let (commands, receive) = mpsc::channel(2);
    let (invalidated, watch) = watch::channel(false);
    tokio::spawn(client_actor(
        stream,
        description.channel_id,
        receive,
        invalidated,
    ));
    Ok(NativeImportHandle {
        relay: Some(relay),
        commands,
        invalidated: watch,
    })
}
pub(crate) fn decode<T: serde::de::DeserializeOwned>(response: Response) -> Result<T> {
    if !response.ok {
        return Err(Error::State(
            response
                .error
                .unwrap_or_else(|| "Native channel operation failed".into()),
        ));
    }
    if response.error.is_some() {
        return Err(Error::Protocol("inconsistent Native response".into()));
    }
    serde_json::from_value(response.data)
        .map_err(|_| Error::Protocol("invalid Native channel response".into()))
}
async fn channel_exchange<T: serde::de::DeserializeOwned>(
    stream: &mut UnixStream,
    command: &CommandWire,
) -> Result<T> {
    linux::write_frame(stream, command).await?;
    let bytes = linux::read_frame(stream, Duration::from_secs(120)).await?;
    let response = serde_json::from_slice(&bytes)
        .map_err(|_| Error::Protocol("invalid Native response frame".into()))?;
    decode(response)
}
pub(crate) async fn command_frame(stream: &mut UnixStream) -> Result<Vec<u8>> {
    // Long-lived idle control is valid; once the first byte arrives, the rest is bounded.
    let mut first = [0u8; 1];
    stream.read_exact(&mut first).await?;
    if first[0] == b'\n' {
        return Err(Error::Protocol("empty Native command".into()));
    }
    let mut frame = vec![first[0]];
    frame.extend(linux::read_frame(stream, DEADLINE).await?);
    if frame.len() > crate::MAX_FRAME_BYTES {
        return Err(Error::Protocol("Native command exceeds bound".into()));
    }
    Ok(frame)
}
async fn client_actor(
    mut stream: UnixStream,
    id: String,
    mut commands: mpsc::Receiver<Command>,
    invalidated: watch::Sender<bool>,
) {
    let mut committed = false;
    loop {
        let mut unexpected = [0u8; 1];
        let command = tokio::select! {
            command=commands.recv() => command,
            _=stream.read(&mut unexpected) => None,
        };
        match command {
            Some(Command::Commit(reply)) => {
                if committed {
                    let _ = reply.send(Err(Error::State(
                        "Native channel was already committed".into(),
                    )));
                    continue;
                }
                committed = true;
                // This actor, not the caller's cancellable future, owns the entire exchange.
                let result = channel_exchange(
                    &mut stream,
                    &CommandWire::Commit {
                        channel_id: id.clone(),
                    },
                )
                .await;
                let failed = result.is_err();
                let _ = reply.send(result);
                if failed {
                    break;
                }
            }
            Some(Command::Close(reply)) => {
                let result = channel_exchange::<serde_json::Value>(
                    &mut stream,
                    &CommandWire::Close {
                        channel_id: id.clone(),
                    },
                )
                .await
                .and_then(|value| {
                    if value == serde_json::json!({"closed":true}) {
                        Ok(())
                    } else {
                        Err(Error::Protocol("invalid Native cleanup result".into()))
                    }
                });
                let _ = reply.send(result);
                break;
            }
            None => break,
        }
    }
    // Closing this UDS always triggers the helper supervisor. No successful cleanup is invented.
    drop(stream);
    let _ = invalidated.send(true);
}
pub(crate) fn store(app: &linux::App) -> Result<Arc<Mutex<Store>>> {
    app.native_state
        .clone()
        .ok_or_else(|| Error::Permission("secure Native state is not enabled".into()))
}
pub(crate) fn with_store<T>(
    store: &Mutex<Store>,
    action: impl FnOnce(&mut Store) -> Result<T>,
) -> Result<T> {
    let mut guard = store
        .lock()
        .map_err(|_| Error::State("Native state mutex poisoned".into()))?;
    action(&mut guard)
}

struct CancelOnDrop(Arc<AtomicBool>);
impl Drop for CancelOnDrop {
    fn drop(&mut self) {
        self.0.store(true, Ordering::Release);
    }
}

pub(crate) async fn mutation_guard(
    stream: &mut UnixStream,
    state: Arc<tokio::sync::Mutex<linux::ManagedState>>,
    stop: &mut watch::Receiver<bool>,
    cancel: &AtomicBool,
) -> Result<tokio::sync::OwnedMutexGuard<linux::ManagedState>> {
    if *stop.borrow() || cancel.load(Ordering::Acquire) {
        cancel.store(true, Ordering::Release);
        return Err(Error::State(
            "Native commit cancelled before mutation queue".into(),
        ));
    }
    let mut unexpected = [0u8; 1];
    let guard = tokio::select! {
        biased;
        _ = stop.changed() => {
            cancel.store(true, Ordering::Release);
            return Err(Error::State("helper stopped while Native commit was queued".into()));
        }
        _ = stream.read(&mut unexpected) => {
            cancel.store(true, Ordering::Release);
            return Err(Error::State("Native control ended or violated framing while queued".into()));
        }
        guard = tokio::time::timeout(Duration::from_secs(30), state.lock_owned()) => {
            guard.map_err(|_| Error::Timeout("Native mutation queue".into()))?
        }
    };
    // No further well-formed command is legal before the commit reply. Observe
    // an already-ready EOF even when it raced with granting the mutation lock.
    if *stop.borrow() || cancel.load(Ordering::Acquire) {
        cancel.store(true, Ordering::Release);
        return Err(Error::State(
            "Native commit cancelled before task start".into(),
        ));
    }
    match stream.try_read(&mut unexpected) {
        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => Ok(guard),
        _ => {
            cancel.store(true, Ordering::Release);
            Err(Error::State(
                "Native control ended before task start".into(),
            ))
        }
    }
}

pub(crate) async fn serve_import(
    mut stream: UnixStream,
    app: Arc<linux::App>,
    uid: u32,
    pid: Option<i32>,
    target: SecureImportTarget,
) -> Result<()> {
    if let Err(error) = app.policy.authorize_import(uid, &target) {
        linux::write_frame(&mut stream, &Response::failure(error)).await?;
        return Ok(());
    }
    let store = match store(&app) {
        Ok(store) => store,
        Err(error) => {
            linux::write_frame(&mut stream, &Response::failure(error)).await?;
            return Ok(());
        }
    };
    let initial_store = store.clone();
    let initial_target = target.clone();
    let prepared = tokio::task::spawn_blocking(move || {
        let owner = native_state::owner(uid, pid)?;
        let id = native_state::uuid("/proc/sys/kernel/random/uuid")?;
        let (channel, relay) = ImportChannel::prepare(initial_target.remote.clone())?;
        with_store(&initial_store, |s| {
            s.reserve(Record {
                channel_id: id.clone(),
                owner,
                target: initial_target,
                state: Phase::Prepared {},
            })
        })?;
        Ok::<_, Error>((id, channel, relay))
    })
    .await
    .map_err(|_| Error::State("Native channel preparation task failed".into()))
    .and_then(|result| result);
    let (id, channel, relay) = match prepared {
        Ok(prepared) => prepared,
        Err(error) => {
            linux::write_frame(&mut stream, &Response::failure(error)).await?;
            return Ok(());
        }
    };
    let channel = Arc::new(Mutex::new(channel));
    let cancel = Arc::new(AtomicBool::new(false));
    let _cancel_on_drop = CancelOnDrop(cancel.clone());
    let mut stop = app.native_stop.subscribe();
    let operation: Result<()> = async {
        if *stop.borrow() { return Err(Error::State("helper is stopping secure Native channels".into())); }
        linux::write_frame(&mut stream,&Response::success(serde_json::to_value(Description { schema_version:1,channel_id:id.clone() }).map_err(|_| Error::Protocol("cannot encode Native description".into()))?)).await?;
        let mut ack=[0u8;1]; tokio::time::timeout(DEADLINE,stream.read_exact(&mut ack)).await.map_err(|_| Error::Timeout("Native descriptor handoff acknowledgement".into()))??;
        if ack != [ancillary::FD_MARKER] { return Err(Error::Protocol("invalid Native descriptor acknowledgement".into())); }
        let relay: OwnedFd=relay.into();
        let sent=ancillary::send_fd(&stream,&relay).await;
        drop(relay); // Crucial: helper never retains a duplicate of the broker's endpoint.
        sent?;
        let mut committed=false;
        loop {
            let bytes=tokio::select! {
                _=stop.changed() => return Err(Error::State("helper is stopping secure Native channels".into())),
                bytes=async {
                    if committed { command_frame(&mut stream).await }
                    else { tokio::time::timeout(DEADLINE,command_frame(&mut stream)).await.map_err(|_| Error::Timeout("Native commit not received".into()))? }
                } => bytes?,
            };
            let request: CommandWire=serde_json::from_slice(&bytes).map_err(|_| Error::Protocol("invalid Native channel command".into()))?;
            match request {
                CommandWire::Close { channel_id } => {
                    if channel_id != id { return Err(Error::Permission("Native close belongs to another transaction".into())); }
                    return Ok(());
                }
                CommandWire::Commit { channel_id } => {
                    if channel_id != id || committed { return Err(Error::Permission("Native commit is duplicate or belongs to another transaction".into())); }
                    committed=true;
                    let guard=mutation_guard(&mut stream, app.state.clone(), &mut stop, &cancel).await?;
                    let work_channel=channel.clone(); let work_store=store.clone(); let work_id=id.clone(); let work_cancel=cancel.clone();
                    let mut task=tokio::task::spawn_blocking(move || {
                        let _guard=guard;
                        let mut channel=work_channel.lock().map_err(|_| Error::State("Native channel mutex poisoned".into()))?;
                        let result=channel.commit(&work_cancel,|intent| with_store(&work_store,|s| s.update(&work_id,Phase::Intent { intent:intent.clone() })));
                        if let Some(receipt)=channel.observed_receipt() { with_store(&work_store,|s| s.update(&work_id,Phase::Observed { receipt }))?; }
                        result
                    });
                    let mut unexpected=[0u8;1];
                    let result=tokio::select! {
                        biased;
                        _=stop.changed() => { cancel.store(true,Ordering::Release); task.await },
                        _=stream.read(&mut unexpected) => { cancel.store(true,Ordering::Release); task.await },
                        result=&mut task => result,
                    }.map_err(|_| Error::State("Native mutation task failed; intent retained".into()))??;
                    if cancel.load(Ordering::Acquire) { return Err(Error::State("Native control ended during commit".into())); }
                    let receipt=NativeImportReceipt { channel_id:id.clone(), target:target.clone(), kernel:result };
                    linux::write_frame(&mut stream,&Response::success(serde_json::to_value(receipt).map_err(|_| Error::Protocol("cannot encode Native receipt".into()))?)).await?;
                }
            }
        }
    }.await;
    cancel.store(true, Ordering::Release);
    let guard = app.state.clone().lock_owned().await;
    let cleanup = tokio::task::spawn_blocking(move || {
        let _guard = guard;
        let result = (|| -> Result<()> {
            let mut channel = channel
                .lock()
                .map_err(|_| Error::State("Native cleanup mutex poisoned".into()))?;
            let cleaned = channel.shutdown();
            if let Some(receipt) = channel.observed_receipt() {
                with_store(&store, |s| s.update(&id, Phase::Observed { receipt }))?;
            }
            cleaned?;
            with_store(&store, |s| s.retire(&id))
        })();
        if result.is_err()
            && let Ok(mut store) = store.lock()
        {
            store.unresolved(&id);
        }
        result
    })
    .await
    .map_err(|_| Error::State("Native cleanup task failed; pending intent retained".into()))?;
    let result = cleanup.and(operation);
    let response = match result {
        Ok(()) => Response::success(serde_json::json!({"closed":true})),
        Err(error) => Response::failure(error),
    };
    linux::write_frame(&mut stream, &response).await
}

pub(crate) async fn serve_admin(
    mut stream: UnixStream,
    app: Arc<linux::App>,
    uid: u32,
    request: Request,
) -> Result<()> {
    let guard = if uid == 0 && matches!(request, Request::ReconcileNative { .. }) {
        Some(
            tokio::time::timeout(Duration::from_secs(30), app.state.clone().lock_owned())
                .await
                .map_err(|_| Error::Timeout("Native reconciliation queue".into()))?,
        )
    } else {
        None
    };
    let result = if uid != 0 {
        Err(Error::Permission(
            "Native pending coordination requires root".into(),
        ))
    } else {
        match store(&app) {
            Err(error) => Err(error),
            Ok(store) => tokio::task::spawn_blocking(move || {
                let _guard = guard;
                with_store(&store, |store| match request {
                    Request::NativePending { after } => store.pending(after.as_deref()),
                    Request::ReconcileNative { channel_id } => {
                        native_state::validate_id(&channel_id)?;
                        store.reconcile(&channel_id)?;
                        Ok(serde_json::json!({"retired":true}))
                    }
                    _ => Err(Error::InvalidInput(
                        "invalid Native administration action".into(),
                    )),
                })
            })
            .await
            .map_err(|_| Error::State("Native coordination task failed".into()))?,
        }
    };
    let response = match result {
        Ok(value) => Response::success(value),
        Err(error) => Response::failure(error),
    };
    linux::write_frame(&mut stream, &response).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::{BusId, VhciPort, VirtualProfile};
    use rustusb_native::{
        LocalDeviceGeneration, NativeSession,
        channel::{ControllerReceipt, ImportIntent},
    };
    fn fixture() -> Result<NativeImportReceipt> {
        Ok(NativeImportReceipt {
            channel_id: "00000000-0000-4000-8000-000000000001".into(),
            target: SecureImportTarget {
                server_id: "test-server".into(),
                device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
                remote: RemoteDevice {
                    bus_id: BusId::new("1-1")?,
                    path: "/test/remote".into(),
                    bus_number: 1,
                    device_number: 2,
                    speed: 3,
                    vendor_id: 1,
                    product_id: 2,
                    device_version: 0x100,
                    class: 0,
                    subclass: 0,
                    protocol: 0,
                    configuration: 1,
                    num_configurations: 1,
                    interfaces: vec![rustusb_protocol::RemoteInterface {
                        class: 3,
                        subclass: 1,
                        protocol: 1,
                    }],
                },
            },
            kernel: ImportReceipt {
                intent: ImportIntent {
                    controller: ControllerReceipt {
                        path: "/test/controller".into(),
                        filesystem_device: 1,
                        inode: 2,
                        attribute_inode: 3,
                        attribute_device: 1,
                    },
                    port: VhciPort(0),
                },
                session: NativeSession {
                    port: VhciPort(0),
                    status: 6,
                    local_bus_id: Some(BusId::new("3-1")?),
                    remote_device_id: Some(65538),
                    vendor_id: Some(1),
                    product_id: Some(2),
                    local_device_address: Some(2),
                    local_device_generation: Some(LocalDeviceGeneration {
                        filesystem_device: 1,
                        inode: 4,
                    }),
                },
                kernel_socket_inode: 5,
                profile: None,
            },
        })
    }
    #[tokio::test]
    async fn cancelled_commit_waiter_cannot_consume_close_response() -> Result<()> {
        let (client, mut server) = UnixStream::pair()?;
        let receipt = fixture()?;
        let id = receipt.channel_id.clone();
        let (commands, receive) = mpsc::channel(2);
        let (invalidated, mut watched) = watch::channel(false);
        let actor = tokio::spawn(client_actor(client, id, receive, invalidated));
        let (reply, waiter) = oneshot::channel();
        commands
            .send(Command::Commit(reply))
            .await
            .map_err(|_| Error::State("fixture actor ended".into()))?;
        let bytes = linux::read_frame(&mut server, DEADLINE).await?;
        assert!(matches!(
            serde_json::from_slice::<CommandWire>(&bytes)
                .map_err(|_| Error::Protocol("fixture command".into()))?,
            CommandWire::Commit { .. }
        ));
        drop(waiter);
        let (reply, closed) = oneshot::channel();
        commands
            .send(Command::Close(reply))
            .await
            .map_err(|_| Error::State("fixture actor ended".into()))?;
        // The actor must finish consuming the first reply despite its cancelled caller.
        linux::write_frame(
            &mut server,
            &Response::success(
                serde_json::to_value(receipt)
                    .map_err(|_| Error::Protocol("fixture receipt".into()))?,
            ),
        )
        .await?;
        let bytes = linux::read_frame(&mut server, DEADLINE).await?;
        assert!(matches!(
            serde_json::from_slice::<CommandWire>(&bytes)
                .map_err(|_| Error::Protocol("fixture close".into()))?,
            CommandWire::Close { .. }
        ));
        linux::write_frame(
            &mut server,
            &Response::success(serde_json::json!({"closed":true})),
        )
        .await?;
        closed
            .await
            .map_err(|_| Error::State("fixture close lost".into()))??;
        actor
            .await
            .map_err(|_| Error::State("fixture actor panicked".into()))?;
        watched
            .changed()
            .await
            .map_err(|_| Error::State("fixture watch closed".into()))?;
        assert!(*watched.borrow());
        Ok(())
    }
    #[tokio::test]
    async fn dropped_handle_commands_close_control_and_invalidate() -> Result<()> {
        let (client, mut server) = UnixStream::pair()?;
        let (commands, receive) = mpsc::channel(2);
        let (invalidated, watched) = watch::channel(false);
        let actor = tokio::spawn(client_actor(client, "fixture".into(), receive, invalidated));
        drop(commands);
        let mut byte = [0u8; 1];
        assert_eq!(
            tokio::time::timeout(DEADLINE, server.read(&mut byte))
                .await
                .map_err(|_| Error::Timeout("fixture EOF".into()))??,
            0
        );
        actor
            .await
            .map_err(|_| Error::State("fixture actor panicked".into()))?;
        assert!(*watched.borrow());
        Ok(())
    }
    #[tokio::test]
    async fn queued_commit_observes_eof_before_lock_or_mutation() -> Result<()> {
        let (mut control, peer) = UnixStream::pair()?;
        let state = Arc::new(tokio::sync::Mutex::new(linux::ManagedState::default()));
        let held = state.clone().lock_owned().await;
        let (_stop, mut stopped) = watch::channel(false);
        let cancel = AtomicBool::new(false);
        drop(peer);
        let result = tokio::time::timeout(
            Duration::from_secs(1),
            mutation_guard(&mut control, state.clone(), &mut stopped, &cancel),
        )
        .await
        .map_err(|_| Error::Timeout("queued cancellation fixture".into()))?;
        assert!(result.is_err());
        assert!(cancel.load(Ordering::Acquire));
        assert!(state.try_lock().is_err());
        drop(held);
        Ok(())
    }
    #[tokio::test]
    async fn stopped_helper_wins_even_when_mutation_lock_is_free() -> Result<()> {
        let (mut control, _peer) = UnixStream::pair()?;
        let state = Arc::new(tokio::sync::Mutex::new(linux::ManagedState::default()));
        let (stop, mut stopped) = watch::channel(false);
        stop.send_replace(true);
        let cancel = AtomicBool::new(false);
        assert!(
            mutation_guard(&mut control, state.clone(), &mut stopped, &cancel)
                .await
                .is_err()
        );
        assert!(cancel.load(Ordering::Acquire));
        assert!(state.try_lock().is_ok());
        Ok(())
    }
}

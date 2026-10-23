//! Native TLS broker: authenticated management followed by an owned kernel relay.
//!
//! Published metadata is the helper's registered pre-export snapshot. It is not
//! a claim about the current configuration or interface state of usbip-host.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{DeviceKey, Error, Result};
use rustusb_daemon::{NativeExportHandle, NativeStubHandle, prepare_native_export};
use rustusb_protocol::{
    RemoteDevice,
    secure::{self, DeviceMapping, SecureOperation},
    server::{self, Management},
};
use rustusb_transport::{AuthenticatedStream, TlsServer, VerifiedPeer, config::LoadedServerConfig};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    collections::HashSet,
    io::{BufRead, Read},
    path::Path,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::{Semaphore, mpsc, watch},
    task::JoinSet,
};

const CONNECTIONS: usize = 16;
const CONTROL_LIMIT: usize = 4096;
const FRAME_DEADLINE: Duration = Duration::from_secs(5);

struct Export {
    key: DeviceKey,
    published: RemoteDevice,
    handle: NativeExportHandle,
    // A failed helper transaction remains unavailable until service cleanup.
    // This bit describes dispatch eligibility, not actual kernel occupancy.
    reserved: AtomicBool,
    cleanup_failed: AtomicBool,
}

impl Export {
    fn accepting(&self) -> bool {
        !self.reserved.load(Ordering::Acquire) && !*self.handle.invalidated().borrow()
    }
}

enum TaskResult {
    Connection(Result<()>),
    ExportEnded,
    Stopped,
}

async fn changed(receiver: &mut watch::Receiver<bool>) {
    if *receiver.borrow() {
        return;
    }
    loop {
        if receiver.changed().await.is_err() || *receiver.borrow() {
            return;
        }
    }
}

fn published_snapshot(exports: &[Arc<Export>]) -> Value {
    json!({
        "metadata_scope":"published_registration_not_live_configuration",
        "devices":exports.iter().map(|export| json!({
            "device_key":export.key,
            "published_device":export.published,
            "accepting_imports":export.accepting(),
        })).collect::<Vec<_>>()
    })
}

async fn close_exports(exports: Vec<Arc<Export>>) -> Result<()> {
    let mut first = None;
    for export in exports {
        let result = match Arc::try_unwrap(export) {
            Ok(export) => export.handle.close().await,
            Err(_) => Err(Error::State(
                "Native export is still referenced; cleanup is unconfirmed".into(),
            )),
        };
        if let Err(error) = result {
            tracing::warn!("Native export cleanup remains unresolved");
            if first.is_none() {
                first = Some(error);
            }
        }
    }
    first.map_or(Ok(()), Err)
}

pub(super) async fn run(
    socket: &Path,
    loaded: LoadedServerConfig,
    control_stdin: bool,
    mut signals: ShutdownSignals,
) -> Result<()> {
    let mut output = EventOutput::new()?;
    let mut exports = Vec::with_capacity(loaded.devices.len());
    let preparing_stopped = AtomicBool::new(false);
    let preparation = {
        let prepare = async {
            let mut buses = HashSet::new();
            for key in &loaded.devices {
                if preparing_stopped.load(Ordering::Acquire) {
                    break;
                }
                let id = key.fingerprint().ok_or_else(|| {
                    Error::InvalidInput("Native device fingerprint required".into())
                })?;
                let handle = prepare_native_export(socket, id.to_owned()).await?;
                let matches = handle.device_key() == key
                    && buses.insert(handle.published_device().bus_id.clone());
                let published = handle.published_device().clone();
                // Keep every received handle, including a malformed response's,
                // so no preparation error can skip an explicit close attempt.
                exports.push(Arc::new(Export {
                    key: key.clone(),
                    published,
                    handle,
                    reserved: AtomicBool::new(false),
                    cleanup_failed: AtomicBool::new(false),
                }));
                if !matches {
                    return Err(Error::Protocol("Native export identity differs".into()));
                }
            }
            let devices: Vec<_> = exports
                .iter()
                .map(|export| export.published.clone())
                .collect();
            server::devlist_reply(&devices)?;
            Ok(())
        };
        tokio::pin!(prepare);
        tokio::select! {
            biased;
            signal = signals.recv() => {
                // An accepted helper export may already be changing drivers.
                // Finish receiving its result, then close every known handle.
                preparing_stopped.store(true, Ordering::Release);
                let prepared = (&mut prepare).await;
                (prepared, Some(signal))
            }
            result = &mut prepare => (result, None),
        }
    };
    if preparation.0.is_err() || preparation.1.is_some() {
        let cleanup = close_exports(exports).await;
        cleanup?;
        preparation.0?;
        return match preparation.1 {
            Some(signal) => signal,
            None => Ok(()),
        };
    }
    // Bind is not cancelled after it may have acquired the listening socket.
    let listener = match TcpListener::bind(loaded.listen).await {
        Ok(listener) => listener,
        Err(error) => {
            close_exports(exports).await?;
            return Err(error.into());
        }
    };
    let address = match listener.local_addr() {
        Ok(address) => address,
        Err(error) => {
            drop(listener);
            close_exports(exports).await?;
            return Err(error.into());
        }
    };
    let (stopping, stop) = watch::channel(false);
    let budget = Arc::new(Semaphore::new(CONNECTIONS));
    let mut tasks = JoinSet::new();
    for export in &exports {
        let mut invalidated = export.handle.invalidated();
        let mut stop = stop.clone();
        tasks.spawn(async move {
            tokio::select! {
                biased;
                _ = changed(&mut stop) => TaskResult::Stopped,
                _ = changed(&mut invalidated) => TaskResult::ExportEnded,
            }
        });
    }
    let operation = async {
        output.event(json!({"schema_version":1,"event":"ready","backend":"native","transport":"mutual_tls","address":address,"snapshot":published_snapshot(&exports)})).await?;
        let mut input = if control_stdin {
            Some(controls()?)
        } else {
            None
        };
        loop {
            tokio::select! {
                accepted = listener.accept() => {
                    let (stream, _) = accepted?;
                    let Ok(permit) = budget.clone().try_acquire_owned() else { continue };
                    let tls = loaded.tls.clone();
                    let registry = exports.clone();
                    let stop = stop.clone();
                    tasks.spawn(async move {
                        // This permit covers TLS setup, the entire raw stream,
                        // and the actual helper stub-close response.
                        let _permit = permit;
                        TaskResult::Connection(connection(stream, tls, registry, stop).await)
                    });
                }
                task = tasks.join_next(), if !tasks.is_empty() => {
                    match task {
                        Some(Ok(TaskResult::Connection(result))) => {
                            tracing::debug!(success=result.is_ok(), "Native TLS connection finished");
                        }
                        Some(Ok(TaskResult::ExportEnded)) => return Err(Error::State("Native export control ended; service is stopping".into())),
                        Some(Ok(TaskResult::Stopped)) => {},
                        Some(Err(_)) => return Err(Error::State("Native service task failed; cleanup required".into())),
                        None => {},
                    }
                }
                control = async {
                    match &mut input {
                        Some(receiver) => receiver.recv().await,
                        None => std::future::pending().await,
                    }
                } => {
                    match control {
                        None => break Ok(()),
                        Some(Ok(Control::Shutdown { id })) => {
                            output.event(json!({"schema_version":1,"event":"ack","id":id,"ok":true})).await?;
                            break Ok(());
                        }
                        Some(Ok(Control::Snapshot { id })) => {
                            output.event(json!({"schema_version":1,"event":"snapshot","id":id,"snapshot":published_snapshot(&exports)})).await?;
                        }
                        Some(Err(_)) => {
                            output.event(json!({"schema_version":1,"event":"error","code":"invalid_control"})).await?;
                        }
                    }
                }
            }
        }
    };
    let outcome = {
        tokio::pin!(operation);
        tokio::select! {
            biased;
            signal = signals.recv() => signal,
            result = &mut operation => result,
        }
    };
    drop(listener);
    stopping.send_replace(true);
    let mut task_failure = false;
    // Never abort a task that may own a helper transaction. A blocked helper
    // call keeps its actual owner until its response/error and explicit close.
    while let Some(task) = tasks.join_next().await {
        if task.is_err() {
            task_failure = true;
        }
    }
    let count = exports.len();
    let stub_cleanup_failed = exports
        .iter()
        .any(|export| export.cleanup_failed.load(Ordering::Acquire));
    let cleanup = close_exports(exports).await;
    cleanup?;
    outcome?;
    if task_failure || stub_cleanup_failed {
        return Err(Error::State(
            "Native task or stub cleanup had an unresolved error".into(),
        ));
    }
    if output.in_flight {
        return Ok(());
    }
    tokio::select! {
        biased;
        signal = signals.recv() => signal,
        result = output.event(json!({"schema_version":1,"event":"stopped","backend":"native","transport":"mutual_tls","exports_closed":count,"export_cleanup_verified":true})) => result,
    }
}

async fn write_frame(writer: &mut (impl AsyncWrite + Unpin), bytes: &[u8]) -> Result<()> {
    tokio::time::timeout(FRAME_DEADLINE, async {
        writer.write_all(bytes).await?;
        writer.flush().await
    })
    .await
    .map_err(|_| Error::Timeout("Native TLS management response".into()))??;
    Ok(())
}

enum Selection {
    CatalogDone,
    Import(Arc<Export>),
}

async fn management(
    stream: &mut AuthenticatedStream<TcpStream>,
    exports: &[Arc<Export>],
    peer: &VerifiedPeer,
) -> Result<Selection> {
    let operation = secure::read_prelude(stream).await?;
    let request = server::read_management(stream).await?;
    match (operation, request) {
        (SecureOperation::Catalog, Management::Devlist) => {
            // Derive the mapping and the standard response from one snapshot.
            let catalog: Vec<_> = exports
                .iter()
                .filter(|export| export.accepting() && peer.allows(&export.key))
                .map(|export| (export.key.clone(), export.published.clone()))
                .collect();
            let mapping: Vec<_> = catalog
                .iter()
                .map(|(key, device)| DeviceMapping {
                    device_key: key.clone(),
                    bus_id: device.bus_id.clone(),
                })
                .collect();
            let devices: Vec<_> = catalog.into_iter().map(|(_, device)| device).collect();
            let reply = server::devlist_reply(&devices)?;
            secure::write_catalog_mapping(stream, &mapping).await?;
            write_frame(stream, &reply).await?;
            Ok(Selection::CatalogDone)
        }
        (SecureOperation::Import { device_key }, Management::Import(bus_id)) => {
            // Unauthorized/wrong-key requests never inspect occupancy or open
            // a helper stub. Cached registration is pinned by the strong lease.
            let selected = exports.iter().find(|export| {
                export.key == device_key
                    && export.published.bus_id == bus_id
                    && peer.allows(&export.key)
                    && !*export.handle.invalidated().borrow()
            });
            let Some(export) = selected else {
                write_frame(stream, &server::import_reply(4, None)?).await?;
                return Ok(Selection::CatalogDone);
            };
            if export
                .reserved
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_err()
            {
                write_frame(stream, &server::import_reply(2, None)?).await?;
                return Ok(Selection::CatalogDone);
            }
            Ok(Selection::Import(export.clone()))
        }
        _ => Err(Error::Protocol(
            "secure prelude and standard operation differ".into(),
        )),
    }
}

async fn connection(
    socket: TcpStream,
    tls: TlsServer,
    exports: Vec<Arc<Export>>,
    mut stop: watch::Receiver<bool>,
) -> Result<()> {
    socket.set_nodelay(true)?;
    let mut stream = tokio::select! {
        biased;
        _ = changed(&mut stop) => return Ok(()),
        result = tls.accept(socket) => result.map_err(|error| match error {
            rustusb_transport::Error::Timeout => Error::Timeout("Native mutual TLS handshake".into()),
            rustusb_transport::Error::ConnectionLimit => Error::State("Native TLS connection budget exhausted".into()),
            _ => Error::Permission("Native mutual TLS authentication failed".into()),
        })?,
    };
    let peer = stream.peer().clone();
    let selection = tokio::select! {
        biased;
        _ = changed(&mut stop) => return Ok(()),
        result = management(&mut stream, &exports, &peer) => result?,
    };
    let Selection::Import(export) = selection else {
        return Ok(());
    };
    if *stop.borrow() {
        export.reserved.store(false, Ordering::Release);
        return Ok(());
    }
    // Do not cancel a helper frame exchange after accepting its operation.
    // A stop received during it is acted on immediately after its result.
    let opened = export.handle.open_stub().await;
    let stopped = *stop.borrow();
    let stub = match opened {
        Ok(stub) => stub,
        Err(error) => {
            if !stopped {
                let status = if matches!(error, Error::State(_)) {
                    2
                } else {
                    4
                };
                write_frame(&mut stream, &server::import_reply(status, None)?).await?;
            }
            return Err(error);
        }
    };
    imported(stream, stub, &export, stop, stopped).await
}

async fn imported(
    stream: AuthenticatedStream<TcpStream>,
    mut stub: NativeStubHandle,
    export: &Export,
    stop: watch::Receiver<bool>,
    already_stopped: bool,
) -> Result<()> {
    let mut network = Some(stream);
    let operation = async {
        if already_stopped || *stop.borrow() {
            return Ok(());
        }
        let relay = stub.take_relay()?;
        if *stop.borrow() {
            return Ok(());
        }
        stub.commit().await?;
        if *stop.borrow() {
            return Ok(());
        }
        // The helper proves USED only. No bytes from the raw kernel endpoint
        // may race ahead of the complete, flushed management success reply.
        let stream = network
            .as_mut()
            .ok_or_else(|| Error::State("Native network owner missing".into()))?;
        write_frame(stream, &server::import_reply(0, Some(&export.published))?).await?;
        let stream = network
            .take()
            .ok_or_else(|| Error::State("Native network owner missing".into()))?;
        pump(
            stream,
            relay,
            stop.clone(),
            stub.invalidated(),
            export.handle.invalidated(),
        )
        .await
    }
    .await;
    // Both TLS halves and R have ended before helper cleanup; the outer task
    // permit still covers this actual close and its kernel-state verification.
    drop(network);
    let cleanup = stub.close().await;
    if cleanup.is_ok() {
        export.reserved.store(false, Ordering::Release);
    } else {
        export.cleanup_failed.store(true, Ordering::Release);
    }
    cleanup?;
    operation
}

async fn copy_direction(
    reader: &mut (impl AsyncRead + Unpin),
    writer: &mut (impl AsyncWrite + Unpin),
) -> Result<()> {
    let mut bytes = [0_u8; 16 * 1024];
    loop {
        let count = reader.read(&mut bytes).await?;
        if count == 0 {
            return Ok(());
        }
        writer.write_all(&bytes[..count]).await?;
        writer.flush().await?;
    }
}

async fn pump<A, B>(
    network: A,
    relay: B,
    mut stop: watch::Receiver<bool>,
    mut stub_ended: watch::Receiver<bool>,
    mut export_ended: watch::Receiver<bool>,
) -> Result<()>
where
    A: AsyncRead + AsyncWrite + Unpin,
    B: AsyncRead + AsyncWrite + Unpin,
{
    let (mut from_network, mut to_network) = tokio::io::split(network);
    let (mut from_kernel, mut to_kernel) = tokio::io::split(relay);
    {
        let inbound = copy_direction(&mut from_network, &mut to_kernel);
        let outbound = copy_direction(&mut from_kernel, &mut to_network);
        tokio::pin!(inbound, outbound);
        tokio::select! {
            biased;
            _ = changed(&mut stop) => Ok(()),
            _ = changed(&mut export_ended) => Err(Error::State("Native export ended during relay".into())),
            _ = changed(&mut stub_ended) => Ok(()),
            result = &mut inbound => result,
            result = &mut outbound => result,
        }
    }
}

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
enum Control {
    Snapshot { id: String },
    Shutdown { id: String },
}

fn parse_control(bytes: &[u8]) -> Result<Control> {
    let control: Control = serde_json::from_slice(bytes)
        .map_err(|_| Error::InvalidInput("invalid Native local control".into()))?;
    let id = match &control {
        Control::Snapshot { id } | Control::Shutdown { id } => id,
    };
    if id.is_empty()
        || id.len() > 64
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err(Error::InvalidInput(
            "invalid Native control identifier".into(),
        ));
    }
    Ok(control)
}

fn controls() -> Result<mpsc::Receiver<Result<Control>>> {
    let (sender, receiver) = mpsc::channel(2);
    std::thread::Builder::new()
        .name("native-server-stdin".into())
        .spawn(move || {
            let input = std::io::stdin();
            let mut reader = input.lock();
            loop {
                let mut line = Vec::new();
                let read = (&mut reader)
                    .take((CONTROL_LIMIT + 1) as u64)
                    .read_until(b'\n', &mut line);
                let result = match read {
                    Ok(0) => break,
                    Ok(n) if n <= CONTROL_LIMIT => parse_control(&line),
                    Ok(_) => Err(Error::InvalidInput("Native control exceeds limit".into())),
                    Err(error) => Err(error.into()),
                };
                let failed = result.is_err();
                if sender.blocking_send(result).is_err() || failed {
                    break;
                }
            }
        })?;
    Ok(receiver)
}

#[cfg(test)]
mod tests {
    use super::*;

    // These in-memory stream tests cover the broker's relay ownership only;
    // they do not claim helper, sysfs, kernel or physical-device validation.
    #[tokio::test]
    async fn relay_copies_both_directions_and_first_eof_releases_other_half() -> Result<()> {
        let (network, mut remote) = tokio::io::duplex(64);
        let (relay, mut kernel) = tokio::io::duplex(64);
        let (_stop, stop) = watch::channel(false);
        let (_stub, stub) = watch::channel(false);
        let (_export, export) = watch::channel(false);
        let task = tokio::spawn(pump(network, relay, stop, stub, export));
        tokio::time::timeout(Duration::from_secs(2), async {
            remote.write_all(b"request").await?;
            let mut request = [0u8; 7];
            kernel.read_exact(&mut request).await?;
            assert_eq!(&request, b"request");
            kernel.write_all(b"reply").await?;
            let mut reply = [0u8; 5];
            remote.read_exact(&mut reply).await?;
            assert_eq!(&reply, b"reply");
            drop(remote);
            task.await
                .map_err(|_| Error::State("relay test task failed".into()))??;
            let mut tail = [0u8; 1];
            assert_eq!(kernel.read(&mut tail).await?, 0);
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("relay test".into()))?
    }

    #[tokio::test]
    async fn shutdown_releases_both_streams_even_without_peer_eof() -> Result<()> {
        let (network, mut remote) = tokio::io::duplex(64);
        let (relay, mut kernel) = tokio::io::duplex(1);
        let (shutdown, stop) = watch::channel(false);
        let (_stub, stub) = watch::channel(false);
        let (_export, export) = watch::channel(false);
        let task = tokio::spawn(pump(network, relay, stop, stub, export));
        remote
            .write_all(b"larger than the unread kernel buffer")
            .await?;
        shutdown.send_replace(true);
        tokio::time::timeout(Duration::from_secs(2), async {
            task.await
                .map_err(|_| Error::State("relay test task failed".into()))??;
            let mut bytes = Vec::new();
            // A prefix may have reached the bounded relay before shutdown.
            kernel.read_to_end(&mut bytes).await?;
            assert!(b"larger than the unread kernel buffer".starts_with(&bytes));
            let mut tail = [0u8; 1];
            assert_eq!(remote.read(&mut tail).await?, 0);
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("relay shutdown test".into()))?
    }

    #[tokio::test]
    async fn export_invalidation_ends_idle_relay_with_error() -> Result<()> {
        let (network, mut remote) = tokio::io::duplex(64);
        let (relay, mut kernel) = tokio::io::duplex(64);
        let (_stop, stop) = watch::channel(false);
        let (_stub, stub) = watch::channel(false);
        let (invalidate, export) = watch::channel(false);
        let task = tokio::spawn(pump(network, relay, stop, stub, export));
        invalidate.send_replace(true);
        tokio::time::timeout(Duration::from_secs(2), async {
            let result = task
                .await
                .map_err(|_| Error::State("relay test task failed".into()))?;
            assert!(matches!(result, Err(Error::State(_))));
            let mut tail = [0u8; 1];
            assert_eq!(remote.read(&mut tail).await?, 0);
            assert_eq!(kernel.read(&mut tail).await?, 0);
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("relay invalidation test".into()))?
    }

    #[test]
    fn local_controls_are_strict_and_do_not_accept_device_mutations() {
        assert!(matches!(
            parse_control(br#"{"op":"snapshot","id":"s-1"}"#),
            Ok(Control::Snapshot { .. })
        ));
        for invalid in [
            br#"{"op":"snapshot","id":"s","extra":true}"#.as_slice(),
            br#"{"op":"snapshot","id":""}"#.as_slice(),
            br#"{"op":"snapshot","id":"line\nfeed"}"#.as_slice(),
            br#"{"op":"hid_key","id":"s","key":30}"#.as_slice(),
        ] {
            assert!(parse_control(invalid).is_err());
        }
        let oversized_id = format!(r#"{{"op":"shutdown","id":"{}"}}"#, "a".repeat(65));
        assert!(parse_control(oversized_id.as_bytes()).is_err());
    }
}

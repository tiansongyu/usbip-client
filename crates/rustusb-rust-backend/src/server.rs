//! Owned listener, connection task tree and serialized USB/IP session actors.

use crate::{
    device::VirtualBackend,
    session::{ConnectionAccess, ServingBackend, SessionLease, Started, TransferEvent, TransferId},
    wire::{self, DataCommand, Management, ReplyContext},
};
use rustusb_core::{Error, Result};
use rustusb_protocol::secure::{self, DeviceMapping, SecureOperation};
use rustusb_transport::TlsServer;
use std::{
    collections::VecDeque,
    future::poll_fn,
    net::{IpAddr, Ipv4Addr, SocketAddr},
    ops::{Deref, DerefMut},
    sync::atomic::{AtomicU64, Ordering},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::{Semaphore, mpsc, oneshot},
    task::{JoinHandle, JoinSet},
};

const MAX_CONNECTIONS: usize = 16;
const MAX_PENDING: usize = 64;
const CLEANUP_DEADLINE: Duration = Duration::from_secs(10);
static NEXT_TRANSFER: AtomicU64 = AtomicU64::new(1);

struct CloseOnDrop<L: SessionLease>(L);
impl<L: SessionLease> Deref for CloseOnDrop<L> {
    type Target = L;
    fn deref(&self) -> &L {
        &self.0
    }
}
impl<L: SessionLease> DerefMut for CloseOnDrop<L> {
    fn deref_mut(&mut self) -> &mut L {
        &mut self.0
    }
}
impl<L: SessionLease> Drop for CloseOnDrop<L> {
    fn drop(&mut self) {
        self.0.begin_close();
    }
}

/// Explicit network policy for the experimental USB/IP service.
#[derive(Debug, Clone)]
pub struct ServerConfig {
    /// Listen address; default loopback prevents accidental public exposure.
    pub listen: SocketAddr,
    /// Administrator declaration only, not proof of authentication or encryption.
    pub trusted_network: bool,
    /// Exact permitted peer IP addresses; required for non-loopback listeners.
    pub allowed_peers: Vec<IpAddr>,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self {
            listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 3240)),
            trusted_network: false,
            allowed_peers: Vec::new(),
        }
    }
}

impl ServerConfig {
    /// Validate the explicit listen/peer policy without opening sockets or
    /// acquiring devices. Frontends should call this before requesting USB capabilities.
    pub fn validate(&self) -> Result<()> {
        if !self.listen.ip().is_loopback()
            && (!self.trusted_network || self.allowed_peers.is_empty())
        {
            return Err(Error::Permission("non-loopback USB/IP serving requires a trusted-network declaration and exact peer allowlist".into()));
        }
        if self.allowed_peers.len() > 64
            || self
                .allowed_peers
                .iter()
                .any(|ip| ip.is_unspecified() || ip.is_multicast())
        {
            return Err(Error::InvalidInput(
                "USB/IP peer allowlist must contain at most 64 concrete unicast IP addresses"
                    .into(),
            ));
        }
        Ok(())
    }

    fn permits(&self, peer: IpAddr) -> bool {
        if self.allowed_peers.is_empty() {
            self.listen.ip().is_loopback() && peer.is_loopback()
        } else {
            self.allowed_peers.contains(&peer)
        }
    }
}

/// Listener settings for mandatory mutual TLS and stable device authorization.
/// This mode never creates a plaintext USB/IP listener or falls back to one.
#[derive(Debug, Clone)]
pub struct SecureServerConfig {
    /// Explicit TLS listen address; the default is loopback port 3241.
    pub listen: SocketAddr,
}

impl Default for SecureServerConfig {
    fn default() -> Self {
        Self {
            listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 3241)),
        }
    }
}

#[derive(Clone)]
enum ListenerMode {
    Legacy(ServerConfig),
    Tls(TlsServer),
    ManagedTls(TlsServer, crate::monitor::MonitorWriter),
}

impl ListenerMode {
    fn permits(&self, peer: IpAddr) -> bool {
        match self {
            Self::Legacy(config) => config.permits(peer),
            Self::Tls(_) | Self::ManagedTls(_, _) => true,
        }
    }
}

/// Owns the listening task and its entire connection/reader task tree.
/// `shutdown` cooperatively joins managed sessions and awaits actual cleanup.
/// Drop requests managed stop without claiming completion; legacy Drop aborts its tree.
#[derive(Debug)]
pub struct ServerHandle {
    address: SocketAddr,
    stop: Option<oneshot::Sender<()>>,
    task: Option<JoinHandle<Result<()>>>,
    monitor: Option<crate::monitor::MonitorWriter>,
}

impl ServerHandle {
    /// Bind an explicit policy and start the two-profile virtual service.
    pub async fn bind(config: ServerConfig, backend: VirtualBackend) -> Result<Self> {
        Self::bind_backend(config, backend).await
    }

    /// Serve explicitly registered USBFS devices through the same bounded engine.
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    pub async fn bind_physical(
        config: ServerConfig,
        backend: crate::physical::PhysicalBackend,
    ) -> Result<Self> {
        Self::bind_backend(config, backend).await
    }

    /// Serve virtual profiles over mandatory mTLS, filtered by certificate device ACLs.
    /// Catalog and IMPORT use the secure prelude; IMPORT and all URBs share one TLS stream.
    pub async fn bind_tls(
        config: SecureServerConfig,
        backend: VirtualBackend,
        tls: TlsServer,
    ) -> Result<Self> {
        Self::bind_tls_backend(config, backend, tls).await
    }

    /// Serve explicitly granted USBFS generations through the same authenticated engine.
    /// The helper capability pool does not replace each connection's pre-claim device ACL.
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    pub async fn bind_physical_tls(
        config: SecureServerConfig,
        backend: crate::physical::PhysicalBackend,
        tls: TlsServer,
    ) -> Result<Self> {
        Self::bind_tls_backend(config, backend, tls).await
    }

    /// Serve virtual profiles with pre-claim durable application admission and per-session control.
    /// Create and retain a monitor clone before binding; no callback is awaited while holding a lease.
    pub async fn bind_managed_tls(
        config: SecureServerConfig,
        backend: VirtualBackend,
        tls: TlsServer,
        monitor: crate::ServerMonitor,
    ) -> Result<Self> {
        Self::bind_listener(
            config.listen,
            ListenerMode::ManagedTls(tls, monitor.writer()),
            backend,
        )
        .await
    }

    /// Serve explicitly granted physical devices with managed admission and actual cleanup observations.
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    pub async fn bind_physical_managed_tls(
        config: SecureServerConfig,
        backend: crate::physical::PhysicalBackend,
        tls: TlsServer,
        monitor: crate::ServerMonitor,
    ) -> Result<Self> {
        Self::bind_listener(
            config.listen,
            ListenerMode::ManagedTls(tls, monitor.writer()),
            backend,
        )
        .await
    }

    /// Borrow a new observation handle if this is a managed server whose observer is still alive.
    pub fn monitor(&self) -> Option<crate::ServerMonitor> {
        self.monitor
            .as_ref()
            .and_then(crate::monitor::MonitorWriter::observer)
    }

    async fn bind_backend<B: ServingBackend>(config: ServerConfig, backend: B) -> Result<Self> {
        config.validate()?;
        Self::bind_listener(config.listen, ListenerMode::Legacy(config), backend).await
    }

    async fn bind_tls_backend<B: ServingBackend>(
        config: SecureServerConfig,
        backend: B,
        tls: TlsServer,
    ) -> Result<Self> {
        Self::bind_listener(config.listen, ListenerMode::Tls(tls), backend).await
    }

    async fn bind_listener<B: ServingBackend>(
        address: SocketAddr,
        mode: ListenerMode,
        backend: B,
    ) -> Result<Self> {
        let listener = TcpListener::bind(address).await?;
        let address = listener.local_addr()?;
        let monitor = match &mode {
            ListenerMode::ManagedTls(_, monitor) => {
                monitor.attach()?;
                Some(monitor.clone())
            }
            _ => None,
        };
        let (stop, receiver) = oneshot::channel();
        let task = tokio::spawn(listen(listener, mode, backend, receiver));
        Ok(Self {
            address,
            stop: Some(stop),
            task: Some(task),
            monitor,
        })
    }

    /// Return the actual address, including an OS-assigned ephemeral port.
    pub fn local_addr(&self) -> SocketAddr {
        self.address
    }

    /// Stop accepting, cancel every connection and await all lease/reader cleanup.
    pub async fn shutdown(mut self) -> Result<()> {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
        if let Some(task) = self.task.as_mut() {
            task.await.map_err(|_| {
                Error::State("USB/IP listener task failed during shutdown".into())
            })??;
        }
        self.task.take();
        Ok(())
    }
}

impl Drop for ServerHandle {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
        if let Some(task) = self.task.take() {
            if let Some(monitor) = &self.monitor {
                monitor.stop_all();
                // The owned listener remains alive to join actual cleanup. Drop
                // requests shutdown; it cannot manufacture a cleanup receipt.
                drop(task);
            } else {
                task.abort();
            }
        }
    }
}

async fn listen<B: ServingBackend>(
    listener: TcpListener,
    mode: ListenerMode,
    backend: B,
    mut stop: oneshot::Receiver<()>,
) -> Result<()> {
    let limit = std::sync::Arc::new(Semaphore::new(MAX_CONNECTIONS));
    let mut connections = JoinSet::new();
    let monitor = match &mode {
        ListenerMode::ManagedTls(_, monitor) => Some(monitor.clone()),
        _ => None,
    };
    let mut observed_stop = monitor
        .as_ref()
        .map(crate::monitor::MonitorWriter::stopping);
    let outcome = loop {
        tokio::select! {
            _ = &mut stop => break Ok(()),
            _ = async {
                match &mut observed_stop {
                    Some(receiver) => crate::monitor::server_stopped(receiver).await,
                    None => std::future::pending().await,
                }
            } => break Ok(()),
            accepted = listener.accept() => {
                let (stream, peer) = match accepted {
                    Ok(connection) => connection,
                    Err(error) => break Err(Error::Io(error)),
                };
                if !mode.permits(peer.ip()) {
                    continue;
                }
                let Ok(permit) = limit.clone().try_acquire_owned() else { continue };
                let backend = backend.clone();
                let mode = mode.clone();
                connections.spawn(async move {
                    // This outer permit includes the session's supervised cleanup.
                    // The TLS stream separately retains its own permit until Drop.
                    let _permit = permit;
                    let result = accepted_connection(stream, backend, mode).await;
                    tracing::debug!(success = result.is_ok(), "USB/IP connection finished");
                });
            }
            Some(result) = connections.join_next(), if !connections.is_empty() => {
                if result.is_err() {
                    tracing::warn!("USB/IP connection task failed; device cleanup was requested");
                }
            }
        }
    };
    drop(listener);
    if let Some(monitor) = &monitor {
        monitor.stop_all();
    } else {
        connections.abort_all();
    }
    while connections.join_next().await.is_some() {}
    tokio::time::timeout(CLEANUP_DEADLINE, backend.settle_closed())
        .await
        .map_err(|_| {
            Error::Timeout(
                "USB/IP cleanup did not settle; unresolved devices remain quarantined".into(),
            )
        })??;
    outcome
}

pub(crate) async fn write_frame(
    writer: &mut (impl AsyncWrite + Unpin),
    frame: &[u8],
) -> Result<()> {
    tokio::time::timeout(wire::FRAME_DEADLINE, async {
        writer.write_all(frame).await?;
        writer.flush().await?;
        Ok::<_, std::io::Error>(())
    })
    .await
    .map_err(|_| Error::Timeout("USB/IP response write/flush".into()))??;
    Ok(())
}

async fn handshake<B: ServingBackend>(
    reader: &mut (impl AsyncRead + Unpin),
    writer: &mut (impl AsyncWrite + Unpin),
    backend: &B,
    access: &ConnectionAccess,
    claimed: &mut Option<CloseOnDrop<B::Lease>>,
) -> Result<()> {
    let prelude = match access {
        ConnectionAccess::Legacy => None,
        ConnectionAccess::Authenticated(_) => Some(secure::read_prelude(reader).await?),
    };
    match wire::read_management(reader).await? {
        Management::Devlist => {
            if !matches!(prelude, None | Some(SecureOperation::Catalog)) {
                return Err(Error::Protocol(
                    "secure prelude does not match DEVLIST".into(),
                ));
            }
            // Both encodings derive from this one authorized registry snapshot.
            let snapshot = backend.catalog(true, access)?;
            let mappings: Vec<_> = snapshot
                .iter()
                .map(|entry| DeviceMapping {
                    device_key: entry.key.clone(),
                    bus_id: entry.metadata.bus_id.clone(),
                })
                .collect();
            let devices: Vec<_> = snapshot.into_iter().map(|entry| entry.metadata).collect();
            let reply = wire::devlist_reply(&devices)?;
            if prelude.is_some() {
                secure::write_catalog_mapping(writer, &mappings).await?;
            }
            write_frame(writer, &reply).await?;
            Ok(())
        }
        Management::Import(bus_id) => {
            let expected = match prelude {
                Some(SecureOperation::Import { device_key }) => Some(device_key),
                None => None,
                Some(SecureOperation::Catalog) => {
                    return Err(Error::Protocol(
                        "secure prelude does not match IMPORT".into(),
                    ));
                }
            };
            let lease = match tokio::time::timeout(
                wire::FRAME_DEADLINE,
                backend.claim(&bus_id, expected.as_ref(), access),
            )
            .await
            .map_err(|_| {
                Error::Timeout("USB/IP device claim; unfinished cleanup remains quarantined".into())
            })? {
                Ok(lease) => lease,
                Err(Error::State(_)) => {
                    write_frame(writer, &wire::import_reply(2, None)?).await?;
                    return Ok(());
                }
                Err(Error::InvalidInput(_) | Error::Permission(_)) => {
                    write_frame(writer, &wire::import_reply(4, None)?).await?;
                    return Ok(());
                }
                Err(error) => return Err(error),
            };
            let metadata = lease.device();
            // Give the connection owner the lease before any response encoding/write.
            // Even a failed TLS flush then follows its supervised cleanup path.
            *claimed = Some(CloseOnDrop(lease));
            write_frame(writer, &wire::import_reply(0, Some(&metadata))?).await
        }
    }
}

async fn accepted_connection<B: ServingBackend>(
    stream: TcpStream,
    backend: B,
    mode: ListenerMode,
) -> Result<()> {
    stream.set_nodelay(true)?;
    match mode {
        ListenerMode::Legacy(_) => connection(stream, backend, ConnectionAccess::Legacy).await,
        ListenerMode::ManagedTls(tls, monitor) => {
            crate::managed::accepted(stream, backend, tls, monitor).await
        }
        ListenerMode::Tls(tls) => {
            let stream = tls.accept(stream).await.map_err(|error| match error {
                rustusb_transport::Error::Timeout => Error::Timeout("mutual TLS handshake".into()),
                rustusb_transport::Error::ConnectionLimit => {
                    Error::State("TLS connection budget exhausted".into())
                }
                _ => Error::Permission(
                    "mutual TLS authentication or application protocol rejected".into(),
                ),
            })?;
            // Only the verified stream can introduce an authenticated context.
            let access = ConnectionAccess::Authenticated(stream.peer().clone());
            connection(stream, backend, access).await
        }
    }
}

async fn connection<B, S>(stream: S, backend: B, access: ConnectionAccess) -> Result<()>
where
    B: ServingBackend,
    S: AsyncRead + AsyncWrite + Unpin + Send,
{
    // Both halves retain the entire authenticated stream, including its permit.
    let (mut reader, mut writer) = tokio::io::split(stream);
    let mut claimed = None;
    let handshake_result =
        handshake(&mut reader, &mut writer, &backend, &access, &mut claimed).await;
    let Some(mut lease) = claimed else {
        return handshake_result;
    };
    let result = match handshake_result {
        Ok(()) => imported_connection(&mut *lease, reader, &mut writer).await,
        Err(error) => {
            drop(reader);
            Err(error)
        }
    };
    // Network teardown precedes the supervised hardware wait, including an
    // IMPORT reply that failed to write/flush after the lease was acquired.
    drop(writer);
    lease.begin_close();
    tokio::time::timeout(CLEANUP_DEADLINE, poll_fn(|cx| lease.poll_cleanup(cx)))
        .await
        .map_err(|_| {
            Error::Timeout(
                "USB/IP session cleanup is incomplete; device remains quarantined".into(),
            )
        })??;
    result
}

pub(crate) async fn imported_connection<L: SessionLease, R: AsyncRead + Unpin>(
    lease: &mut L,
    mut reader: R,
    writer: &mut (impl AsyncWrite + Unpin),
) -> Result<()> {
    let metadata = lease.device();
    let devid = metadata
        .bus_number
        .checked_shl(16)
        .filter(|_| {
            metadata.bus_number <= u32::from(u16::MAX)
                && metadata.device_number <= u32::from(u16::MAX)
        })
        .map(|bus| bus | metadata.device_number)
        .ok_or_else(|| Error::State("device addressing exceeds USB/IP devid".into()))?;
    // Two queued bounded frames plus one being read prevent a slow writer from
    // creating unbounded TCP-to-heap buffering. Only this reader owns read_exact.
    let (sender, mut receiver) = mpsc::channel(2);
    let read_frames = async move {
        loop {
            let result = wire::read_data(&mut reader, devid).await;
            let terminal = !matches!(result, Ok(Some(_)));
            if sender.send(result).await.is_err() || terminal {
                break;
            }
        }
    };
    // The reader future retains its partial frame across every actor wake.
    // Both futures live inside this connection task, so aborting/joining that
    // task also drops its reader synchronously, without a detached child.
    {
        let actor = imported(lease, writer, &mut receiver);
        tokio::pin!(read_frames, actor);
        tokio::select! {
            _ = &mut read_frames => actor.await,
            result = &mut actor => result,
        }
    }
}

struct Active {
    id: TransferId,
    reply: ReplyContext,
    unlink: Option<u32>,
}

fn active_sequence(active: &VecDeque<Active>, seq: u32) -> bool {
    active
        .iter()
        .any(|request| request.reply.seqnum == seq || request.unlink == Some(seq))
}

async fn imported<L: SessionLease>(
    lease: &mut L,
    writer: &mut (impl AsyncWrite + Unpin),
    receiver: &mut mpsc::Receiver<Result<Option<DataCommand>>>,
) -> Result<()> {
    let mut pending: VecDeque<Active> = VecDeque::new();
    loop {
        let command = tokio::select! {
            // Already-observable completion wins before another network command.
            biased;
            event = poll_fn(|cx| {
                if let std::task::Poll::Ready(result) = lease.poll_disconnect(cx) {
                    return std::task::Poll::Ready(result.map(|()| None));
                }
                if pending.is_empty() { std::task::Poll::Pending }
                else { lease.poll_event(cx).map(|result| result.map(Some)) }
            }) => {
                let Some(event) = event? else {
                    return Err(Error::State("USB/IP device session ended; supervised cleanup required".into()));
                };
                let (id, completion, cancelled) = match event {
                    TransferEvent::Completed { id, completion } => (id, completion, false),
                    TransferEvent::Cancelled { id, completion } => (id, completion, true),
                };
                let index = pending.iter().position(|request| request.id == id)
                    .ok_or_else(|| Error::State("backend returned an unknown transfer generation".into()))?;
                let request = pending.remove(index)
                    .ok_or_else(|| Error::State("active transfer disappeared".into()))?;
                if let Some(unlink) = request.unlink {
                    if !cancelled {
                        write_frame(writer, &wire::submit_reply(&request.reply, completion)?).await?;
                    } else {
                        // Cancelled IN may still own partial bytes. Retire its
                        // buffer before returning the physical budget permit.
                        drop(completion);
                    }
                    lease.record_unlink(cancelled)?;
                    write_frame(writer, &wire::unlink_reply(unlink, if cancelled { -104 } else { 0 })).await?;
                } else {
                    write_frame(writer, &wire::submit_reply(&request.reply, completion)?).await?;
                }
                lease.retire(id)?;
                continue;
            },
            message = receiver.recv() => match message {
                Some(Ok(Some(command))) => command,
                Some(Ok(None)) | None => return Ok(()),
                Some(Err(error)) => return Err(error),
            },
        };
        match command {
            DataCommand::Submit(request) => {
                if active_sequence(&pending, request.seqnum) || pending.len() >= MAX_PENDING {
                    return Err(Error::Protocol(
                        "duplicate active URB sequence number".into(),
                    ));
                }
                let reply = ReplyContext::from(&request);
                let id = NEXT_TRANSFER
                    .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |id| id.checked_add(1))
                    .map_err(|_| Error::State("internal transfer generation exhausted".into()))?;
                match lease.start(id, request)? {
                    Started::Ready(completion) => {
                        write_frame(writer, &wire::submit_reply(&reply, completion)?).await?;
                        lease.retire(id)?;
                    }
                    Started::Queued => {
                        pending.push_back(Active {
                            id,
                            reply,
                            unlink: None,
                        });
                    }
                }
            }
            DataCommand::Unlink { seqnum, target } => {
                if active_sequence(&pending, seqnum) {
                    return Err(Error::Protocol(
                        "UNLINK reuses an active URB sequence number".into(),
                    ));
                }
                if let Some(request) = pending
                    .iter_mut()
                    .find(|request| request.reply.seqnum == target && request.unlink.is_none())
                {
                    lease.request_cancel(request.id)?;
                    request.unlink = Some(seqnum);
                } else {
                    lease.record_unlink(false)?;
                    write_frame(writer, &wire::unlink_reply(seqnum, 0)).await?;
                }
            }
        }
    }
}

/// Exercise the actual bounded server parser without sockets or device effects.
/// This intentionally narrow entry point supports an external fuzz harness.
#[doc(hidden)]
pub async fn validate_server_frame(data: &[u8]) -> Result<()> {
    let mut reader = data;
    if data.starts_with(&[1, 0x11]) {
        wire::read_management(&mut reader).await?;
    } else {
        wire::read_data(&mut reader, 0x10001).await?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::AsyncReadExt;

    fn word(frame: &[u8], offset: usize) -> u32 {
        u32::from_be_bytes([
            frame[offset],
            frame[offset + 1],
            frame[offset + 2],
            frame[offset + 3],
        ])
    }

    fn import_frame(bus_id: &str) -> Vec<u8> {
        let mut frame = vec![1, 0x11, 0x80, 3, 0, 0, 0, 0];
        frame.extend_from_slice(bus_id.as_bytes());
        frame.resize(40, 0);
        frame
    }

    fn submit(
        seq: u32,
        devid: u32,
        direction: u32,
        endpoint: u32,
        length: u32,
        setup: [u8; 8],
    ) -> Vec<u8> {
        let mut frame = vec![0; 48];
        for (offset, value) in [
            (0, 1),
            (4, seq),
            (8, devid),
            (12, direction),
            (16, endpoint),
            (24, length),
        ] {
            frame[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
        }
        frame[40..48].copy_from_slice(&setup);
        frame
    }

    fn unlink(seq: u32, devid: u32, target: u32) -> Vec<u8> {
        let mut frame = vec![0; 48];
        for (offset, value) in [(0, 2), (4, seq), (8, devid), (20, target)] {
            frame[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
        }
        frame
    }

    async fn fixture() -> Result<(VirtualBackend, ServerHandle)> {
        let backend = VirtualBackend::new()?;
        let handle = ServerHandle::bind(
            ServerConfig {
                listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
                ..ServerConfig::default()
            },
            backend.clone(),
        )
        .await?;
        Ok((backend, handle))
    }

    async fn import(address: SocketAddr, bus_id: &str) -> Result<(TcpStream, u32)> {
        let mut peer = TcpStream::connect(address).await?;
        peer.write_all(&import_frame(bus_id)).await?;
        let mut header = [0; 8];
        peer.read_exact(&mut header).await?;
        assert_eq!(&header[..4], &[1, 0x11, 0, 3]);
        let status = word(&header, 4);
        if status == 0 {
            let mut metadata = [0; 312];
            peer.read_exact(&mut metadata).await?;
        }
        Ok((peer, status))
    }

    async fn reply(peer: &mut TcpStream, payload: bool) -> Result<(Vec<u8>, Vec<u8>)> {
        let mut header = vec![0; 48];
        tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut header))
            .await
            .map_err(|_| Error::Timeout("test response".into()))??;
        assert!(header[8..20].iter().all(|byte| *byte == 0));
        let mut body = vec![
            0;
            if payload {
                word(&header, 24) as usize
            } else {
                0
            }
        ];
        peer.read_exact(&mut body).await?;
        Ok((header, body))
    }

    async fn configured(address: SocketAddr, bus_id: &str, devid: u32) -> Result<TcpStream> {
        let (mut peer, status) = import(address, bus_id).await?;
        assert_eq!(status, 0);
        peer.write_all(&submit(1, devid, 0, 0, 0, [0, 9, 1, 0, 0, 0, 0, 0]))
            .await?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!(word(&header, 20), 0);
        Ok(peer)
    }

    async fn released(backend: &VirtualBackend) -> Result<()> {
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if backend.remote_devices(true)?.len() == 2 {
                    assert!(
                        backend
                            .snapshot()?
                            .devices
                            .iter()
                            .all(|device| !device.leased && device.pending_urbs == 0)
                    );
                    return Ok(());
                }
                tokio::time::sleep(Duration::from_millis(2)).await;
            }
        })
        .await
        .map_err(|_| Error::Timeout("test lease release".into()))?
    }

    async fn closed(peer: &mut TcpStream) -> Result<()> {
        let mut byte = [0; 1];
        let result = tokio::time::timeout(Duration::from_secs(2), peer.read(&mut byte))
            .await
            .map_err(|_| Error::Timeout("test connection closure".into()))?;
        // A close with unread invalid bytes may surface as a TCP reset.
        assert!(
            matches!(result, Ok(0))
                || matches!(result, Err(ref error) if error.kind() == std::io::ErrorKind::ConnectionReset)
        );
        Ok(())
    }

    #[tokio::test]
    async fn real_tcp_devlist_busy_import_and_phase_rejection_release_leases() -> Result<()> {
        let (backend, server) = fixture().await?;
        let address = server.local_addr();
        let mut list = TcpStream::connect(address).await?;
        list.write_all(&rustusb_protocol::DEVLIST_REQUEST).await?;
        assert_eq!(rustusb_protocol::read_devlist(&mut list).await?.len(), 2);
        let (mut owner, status) = import(address, "1-1").await?;
        assert_eq!(status, 0);
        let (_, busy) = import(address, "1-1").await?;
        assert_eq!(busy, 2);
        owner.write_all(&import_frame("1-2")).await?;
        closed(&mut owner).await?;
        released(&backend).await?;
        for frame in [
            vec![1, 0x12, 0x80, 5, 0, 0, 0, 0],
            submit(1, 0x10001, 1, 1, 8, [0; 8]),
        ] {
            let mut malformed = TcpStream::connect(address).await?;
            malformed.write_all(&frame).await?;
            closed(&mut malformed).await?;
        }
        let (mut truncated, status) = import(address, "1-1").await?;
        assert_eq!(status, 0);
        truncated
            .write_all(&submit(1, 0x10001, 1, 1, 8, [0; 8])[..29])
            .await?;
        truncated.shutdown().await?;
        closed(&mut truncated).await?;
        released(&backend).await?;
        server.shutdown().await
    }

    #[tokio::test]
    async fn one_byte_control_bounds_and_pending_unlink_have_no_late_submit() -> Result<()> {
        let (backend, server) = fixture().await?;
        let mut peer = configured(server.local_addr(), "1-1", 0x10001).await?;
        peer.write_all(&submit(2, 0x10001, 1, 0, 1, [0x80, 6, 0, 1, 0, 0, 18, 0]))
            .await?;
        let (header, data) = reply(&mut peer, true).await?;
        assert_eq!(
            (word(&header, 20), word(&header, 24), data),
            (0, 1, vec![18])
        );
        peer.write_all(&submit(3, 0x10001, 1, 0, 1, [0x81, 10, 0, 0, 255, 0, 1, 0]))
            .await?;
        let (header, data) = reply(&mut peer, true).await?;
        assert_eq!(word(&header, 20) as i32, -32);
        assert!(data.is_empty());
        peer.write_all(&submit(10, 0x10001, 1, 1, 8, [0; 8]))
            .await?;
        let mut byte = [0; 1];
        assert!(
            tokio::time::timeout(Duration::from_millis(40), peer.read(&mut byte))
                .await
                .is_err()
        );
        peer.write_all(&unlink(11, 0x10001, 10)).await?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!(
            (word(&header, 0), word(&header, 4), word(&header, 20) as i32),
            (4, 11, -104)
        );
        peer.write_all(&submit(12, 0x10001, 1, 0, 18, [0x80, 6, 0, 1, 0, 0, 18, 0]))
            .await?;
        let (header, data) = reply(&mut peer, true).await?;
        assert_eq!((word(&header, 4), data.len()), (12, 18));
        peer.write_all(&unlink(13, 0x10001, 10)).await?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!((word(&header, 4), word(&header, 20)), (13, 0));
        server.shutdown().await?;
        released(&backend).await?;
        closed(&mut peer).await
    }

    #[tokio::test]
    async fn fragmented_out_while_pending_and_wrapped_sequences_preserve_order() -> Result<()> {
        let (backend, server) = fixture().await?;
        let mut peer = configured(server.local_addr(), "1-2", 0x10002).await?;
        peer.write_all(&submit(10, 0x10002, 1, 2, 4, [0; 8]))
            .await?;
        let mut output = submit(11, 0x10002, 0, 2, 4, [0; 8]);
        output.extend_from_slice(b"abcd");
        peer.write_all(&output[..13]).await?;
        // Multiple actor timer wakes must not cancel or reset partial read_exact.
        tokio::time::sleep(Duration::from_millis(35)).await;
        peer.write_all(&output[13..]).await?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!((word(&header, 4), word(&header, 24)), (11, 4));
        let (header, bytes) = reply(&mut peer, true).await?;
        assert_eq!((word(&header, 4), bytes), (10, b"abcd".to_vec()));
        peer.write_all(&submit(u32::MAX, 0x10002, 1, 2, 1, [0; 8]))
            .await?;
        peer.write_all(&submit(0, 0x10002, 1, 2, 1, [0; 8])).await?;
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if backend
                    .snapshot()?
                    .devices
                    .iter()
                    .any(|device| device.pending_urbs == 2)
                {
                    return Ok::<_, Error>(());
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .map_err(|_| Error::Timeout("test pending registration".into()))??;
        backend.cdc_emit(b"xy")?;
        let (first, data) = reply(&mut peer, true).await?;
        assert_eq!((word(&first, 4), data), (u32::MAX, vec![b'x']));
        let (second, data) = reply(&mut peer, true).await?;
        assert_eq!((word(&second, 4), data), (0, vec![b'y']));
        server.shutdown().await?;
        released(&backend).await
    }

    #[tokio::test]
    async fn duplicate_active_sequence_and_pending_bound_fail_closed() -> Result<()> {
        let (backend, server) = fixture().await?;
        for duplicate_unlink in [false, true] {
            let mut peer = configured(server.local_addr(), "1-2", 0x10002).await?;
            peer.write_all(&submit(7, 0x10002, 1, 2, 1, [0; 8])).await?;
            peer.write_all(&if duplicate_unlink {
                unlink(7, 0x10002, 7)
            } else {
                submit(7, 0x10002, 1, 2, 1, [0; 8])
            })
            .await?;
            closed(&mut peer).await?;
            released(&backend).await?;
        }
        let mut peer = configured(server.local_addr(), "1-2", 0x10002).await?;
        for seq in 2..67 {
            peer.write_all(&submit(seq, 0x10002, 1, 2, 1, [0; 8]))
                .await?;
        }
        closed(&mut peer).await?;
        released(&backend).await?;
        server.shutdown().await
    }

    #[tokio::test]
    async fn failed_import_write_and_handle_drop_release_ownership() -> Result<()> {
        let backend = VirtualBackend::new()?;
        let (mut client, io) = tokio::io::duplex(512);
        client.write_all(&import_frame("1-1")).await?;
        drop(client);
        assert!(
            connection(io, backend.clone(), ConnectionAccess::Legacy)
                .await
                .is_err()
        );
        released(&backend).await?;
        let server = ServerHandle::bind(
            ServerConfig {
                listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
                ..ServerConfig::default()
            },
            backend.clone(),
        )
        .await?;
        let mut peer = configured(server.local_addr(), "1-2", 0x10002).await?;
        peer.write_all(&submit(2, 0x10002, 1, 2, 64, [0; 8]))
            .await?;
        peer.write_all(&[0, 0, 0, 1, 0, 0]).await?;
        drop(server);
        released(&backend).await?;
        closed(&mut peer).await
    }

    #[tokio::test]
    async fn listener_policy_and_connection_budget_are_enforced_before_spawn() -> Result<()> {
        let backend = VirtualBackend::new()?;
        let denied = ServerHandle::bind(
            ServerConfig {
                listen: SocketAddr::from((Ipv4Addr::UNSPECIFIED, 0)),
                ..ServerConfig::default()
            },
            backend.clone(),
        )
        .await;
        assert!(matches!(denied, Err(Error::Permission(_))));
        let server = ServerHandle::bind(
            ServerConfig {
                listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
                allowed_peers: vec![IpAddr::V4(Ipv4Addr::new(127, 0, 0, 2))],
                ..ServerConfig::default()
            },
            backend,
        )
        .await?;
        let mut peer = TcpStream::connect(server.local_addr()).await?;
        closed(&mut peer).await?;
        server.shutdown().await?;
        let (_, server) = fixture().await?;
        let mut held = Vec::new();
        for _ in 0..MAX_CONNECTIONS {
            held.push(TcpStream::connect(server.local_addr()).await?);
        }
        // Accepted handshakes stay idle for five seconds; the seventeenth must
        // close immediately without waiting for a permit inside a spawned task.
        let mut overflow = TcpStream::connect(server.local_addr()).await?;
        closed(&mut overflow).await?;
        drop(held);
        server.shutdown().await
    }

    // A controlled asynchronous USB driver: completions and cancellation
    // acknowledgements are injected independently of network scheduling.
    #[derive(Clone)]
    struct AsyncBackend(std::sync::Arc<std::sync::Mutex<AsyncState>>);
    struct AsyncState {
        metadata: rustusb_protocol::RemoteDevice,
        leased: bool,
        closing: bool,
        cleanup_allowed: bool,
        disconnected: bool,
        requests: Vec<(TransferId, crate::wire::Submit)>,
        held: std::collections::HashSet<TransferId>,
        cancellations: std::collections::HashSet<TransferId>,
        events: VecDeque<TransferEvent>,
        event_waker: Option<std::task::Waker>,
        cleanup_waker: Option<std::task::Waker>,
        disconnect_waker: Option<std::task::Waker>,
        retired: usize,
    }
    struct AsyncLease {
        backend: AsyncBackend,
        closed: bool,
    }
    impl AsyncBackend {
        fn new() -> Result<Self> {
            let metadata = VirtualBackend::new()?
                .remote_devices(false)?
                .into_iter()
                .next()
                .ok_or_else(|| Error::State("fixture metadata missing".into()))?;
            Ok(Self(std::sync::Arc::new(std::sync::Mutex::new(
                AsyncState {
                    metadata,
                    leased: false,
                    closing: false,
                    cleanup_allowed: true,
                    disconnected: false,
                    requests: Vec::new(),
                    held: std::collections::HashSet::new(),
                    cancellations: std::collections::HashSet::new(),
                    events: VecDeque::new(),
                    event_waker: None,
                    cleanup_waker: None,
                    disconnect_waker: None,
                    retired: 0,
                },
            ))))
        }
        fn state(&self) -> Result<std::sync::MutexGuard<'_, AsyncState>> {
            self.0
                .lock()
                .map_err(|_| Error::State("fixture lock poisoned".into()))
        }
        async fn server(&self) -> Result<ServerHandle> {
            ServerHandle::bind_backend(
                ServerConfig {
                    listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
                    ..ServerConfig::default()
                },
                self.clone(),
            )
            .await
        }
        async fn wait_request(&self, seq: u32, cancelled: bool) -> Result<TransferId> {
            tokio::time::timeout(Duration::from_secs(2), async {
                loop {
                    {
                        let state = self.state()?;
                        if let Some((id, _)) = state.requests.iter().find(|(id, request)| {
                            request.seqnum == seq
                                && (!cancelled || state.cancellations.contains(id))
                        }) {
                            return Ok(*id);
                        }
                    }
                    tokio::time::sleep(Duration::from_millis(1)).await;
                }
            })
            .await
            .map_err(|_| Error::Timeout("fixture request observation".into()))?
        }
        fn terminal(
            &self,
            seq: u32,
            completion: crate::wire::Completion,
            cancelled: bool,
        ) -> Result<()> {
            let mut state = self.state()?;
            let index = state
                .requests
                .iter()
                .position(|(_, request)| request.seqnum == seq)
                .ok_or_else(|| Error::State("fixture terminal target missing".into()))?;
            let (id, _) = state.requests.remove(index);
            assert!(state.held.contains(&id));
            state.events.push_back(if cancelled {
                TransferEvent::Cancelled { id, completion }
            } else {
                TransferEvent::Completed { id, completion }
            });
            if let Some(waker) = state.event_waker.take() {
                waker.wake();
            }
            Ok(())
        }
        fn reap_closed(&self) -> Result<()> {
            let mut state = self.state()?;
            assert!(state.closing);
            // The fixture's hardware controller confirms every outstanding
            // request is terminal before permitting the registry transition.
            state.requests.clear();
            state.events.clear();
            state.held.clear();
            state.cleanup_allowed = true;
            state.closing = false;
            state.leased = false;
            if let Some(waker) = state.cleanup_waker.take() {
                waker.wake();
            }
            Ok(())
        }
    }
    impl AsyncBackend {
        fn disconnect(&self) -> Result<()> {
            let mut state = self.state()?;
            state.disconnected = true;
            state.cleanup_allowed = false;
            if let Some(waker) = state.disconnect_waker.take() {
                waker.wake();
            }
            Ok(())
        }
    }
    impl ServingBackend for AsyncBackend {
        type Lease = AsyncLease;
        fn catalog(
            &self,
            available_only: bool,
            access: &ConnectionAccess,
        ) -> Result<Vec<crate::session::RegisteredDevice>> {
            let state = self.state()?;
            let key = rustusb_core::DeviceKey::virtual_profile(rustusb_core::VirtualProfile::Hid);
            Ok(
                if !access.allows(&key)
                    || state.disconnected
                    || available_only && (state.leased || state.closing)
                {
                    vec![]
                } else {
                    vec![crate::session::RegisteredDevice {
                        key,
                        metadata: state.metadata.clone(),
                    }]
                },
            )
        }
        async fn claim(
            &self,
            bus_id: &str,
            expected: Option<&rustusb_core::DeviceKey>,
            access: &ConnectionAccess,
        ) -> Result<Self::Lease> {
            let mut state = self.state()?;
            if bus_id != state.metadata.bus_id.as_str() {
                return Err(Error::InvalidInput("fixture unknown device".into()));
            }
            access.require(
                &rustusb_core::DeviceKey::virtual_profile(rustusb_core::VirtualProfile::Hid),
                expected,
            )?;
            if state.leased || state.closing {
                return Err(Error::State("fixture device busy or quarantined".into()));
            }
            if state.disconnected {
                return Err(Error::InvalidInput(
                    "fixture device generation is gone".into(),
                ));
            }
            state.leased = true;
            Ok(AsyncLease {
                backend: self.clone(),
                closed: false,
            })
        }
        async fn settle_closed(&self) -> Result<()> {
            poll_fn(|cx| {
                let mut state = match self.state() {
                    Ok(state) => state,
                    Err(error) => return std::task::Poll::Ready(Err(error)),
                };
                if state.closing {
                    state.cleanup_waker = Some(cx.waker().clone());
                    std::task::Poll::Pending
                } else {
                    std::task::Poll::Ready(Ok(()))
                }
            })
            .await
        }
    }
    impl SessionLease for AsyncLease {
        fn device(&self) -> rustusb_protocol::RemoteDevice {
            // Metadata is immutable; poison is avoided entirely for this accessor.
            match self.backend.0.lock() {
                Ok(state) => state.metadata.clone(),
                Err(poisoned) => poisoned.into_inner().metadata.clone(),
            }
        }
        fn start(&mut self, id: TransferId, request: crate::wire::Submit) -> Result<Started> {
            let mut state = self.backend.state()?;
            state.held.insert(id);
            if request.endpoint == 0 && request.setup[1] == 0x7e {
                return Ok(Started::Ready(crate::wire::Completion {
                    status: 0,
                    actual_length: 0,
                    data: vec![],
                }));
            }
            state.requests.push((id, request));
            Ok(Started::Queued)
        }
        fn poll_event(
            &mut self,
            cx: &mut std::task::Context<'_>,
        ) -> std::task::Poll<Result<TransferEvent>> {
            let mut state = match self.backend.state() {
                Ok(state) => state,
                Err(error) => return std::task::Poll::Ready(Err(error)),
            };
            if let Some(event) = state.events.pop_front() {
                std::task::Poll::Ready(Ok(event))
            } else {
                state.event_waker = Some(cx.waker().clone());
                std::task::Poll::Pending
            }
        }
        fn request_cancel(&mut self, id: TransferId) -> Result<()> {
            let mut state = self.backend.state()?;
            assert!(state.held.contains(&id));
            state.cancellations.insert(id);
            Ok(())
        }
        fn poll_disconnect(
            &mut self,
            cx: &mut std::task::Context<'_>,
        ) -> std::task::Poll<Result<()>> {
            let mut state = match self.backend.state() {
                Ok(state) => state,
                Err(error) => return std::task::Poll::Ready(Err(error)),
            };
            if state.disconnected {
                std::task::Poll::Ready(Ok(()))
            } else {
                state.disconnect_waker = Some(cx.waker().clone());
                std::task::Poll::Pending
            }
        }
        fn retire(&mut self, id: TransferId) -> Result<()> {
            let mut state = self.backend.state()?;
            assert!(state.held.remove(&id));
            state.retired += 1;
            Ok(())
        }
        fn record_unlink(&mut self, _cancelled: bool) -> Result<()> {
            Ok(())
        }
        fn begin_close(&mut self) {
            if self.closed {
                return;
            }
            self.closed = true;
            if let Ok(mut state) = self.backend.state() {
                state.closing = true;
                if state.cleanup_allowed && state.requests.is_empty() && state.held.is_empty() {
                    state.closing = false;
                    state.leased = false;
                }
            }
        }
        fn poll_cleanup(&mut self, cx: &mut std::task::Context<'_>) -> std::task::Poll<Result<()>> {
            let mut state = match self.backend.state() {
                Ok(state) => state,
                Err(error) => return std::task::Poll::Ready(Err(error)),
            };
            if state.closing {
                state.cleanup_waker = Some(cx.waker().clone());
                std::task::Poll::Pending
            } else {
                std::task::Poll::Ready(Ok(()))
            }
        }
    }
    impl Drop for AsyncLease {
        fn drop(&mut self) {
            self.begin_close();
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn asynchronous_control_in_out_preserve_original_errno_and_partial_bytes() -> Result<()> {
        let backend = AsyncBackend::new()?;
        let server = backend.server().await?;
        let (mut peer, status) = import(server.local_addr(), "1-1").await?;
        assert_eq!(status, 0);
        for (seq, status, bytes) in [
            (1, -32, vec![1, 2]),
            (2, -19, vec![]),
            (3, -75, vec![3]),
            (4, -71, vec![]),
            (5, -5, vec![4]),
        ] {
            peer.write_all(&submit(
                seq,
                0x10001,
                1,
                0,
                4,
                [0xc0, 0x70, 0, 0, 0, 0, 4, 0],
            ))
            .await?;
            backend.wait_request(seq, false).await?;
            backend.terminal(
                seq,
                crate::wire::Completion {
                    status,
                    actual_length: bytes.len() as u32,
                    data: bytes.clone(),
                },
                false,
            )?;
            let (header, actual) = reply(&mut peer, true).await?;
            assert_eq!(
                (
                    word(&header, 4),
                    word(&header, 20) as i32,
                    word(&header, 24),
                    actual
                ),
                (seq, status, bytes.len() as u32, bytes)
            );
        }
        let mut output = submit(6, 0x10001, 0, 2, 4, [0; 8]);
        output.extend_from_slice(b"abcd");
        peer.write_all(&output).await?;
        backend.wait_request(6, false).await?;
        backend.terminal(
            6,
            crate::wire::Completion {
                status: -32,
                actual_length: 2,
                data: vec![],
            },
            false,
        )?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!((word(&header, 20) as i32, word(&header, 24)), (-32, 2));
        assert_eq!(backend.state()?.retired, 6);
        server.shutdown().await
    }

    #[tokio::test(flavor = "current_thread")]
    async fn active_cancel_waits_for_terminal_and_completion_race_keeps_partial_out() -> Result<()>
    {
        let backend = AsyncBackend::new()?;
        let server = backend.server().await?;
        let (mut peer, _) = import(server.local_addr(), "1-1").await?;
        for (seq, direction, endpoint, setup) in [
            (10, 1, 0, [0xc0, 0x70, 0, 0, 0, 0, 4, 0]),
            (20, 1, 2, [0; 8]),
            (30, 0, 2, [0; 8]),
        ] {
            let mut frame = submit(seq, 0x10001, direction, endpoint, 4, setup);
            if direction == 0 {
                frame.extend_from_slice(b"abcd");
            }
            peer.write_all(&frame).await?;
            backend.wait_request(seq, false).await?;
            peer.write_all(&unlink(seq + 1, 0x10001, seq)).await?;
            let id = backend.wait_request(seq, true).await?;
            let mut byte = [0; 1];
            assert!(
                tokio::time::timeout(Duration::from_millis(20), peer.read(&mut byte))
                    .await
                    .is_err()
            );
            assert!(backend.state()?.held.contains(&id));
            backend.terminal(
                seq,
                crate::wire::Completion {
                    status: -104,
                    actual_length: 0,
                    data: vec![],
                },
                true,
            )?;
            let (header, _) = reply(&mut peer, false).await?;
            assert_eq!(
                (word(&header, 0), word(&header, 4), word(&header, 20) as i32),
                (4, seq + 1, -104)
            );
        }
        let mut frame = submit(40, 0x10001, 0, 2, 4, [0; 8]);
        frame.extend_from_slice(b"abcd");
        peer.write_all(&frame).await?;
        backend.wait_request(40, false).await?;
        peer.write_all(&unlink(41, 0x10001, 40)).await?;
        backend.wait_request(40, true).await?;
        backend.terminal(
            40,
            crate::wire::Completion {
                status: -32,
                actual_length: 2,
                data: vec![],
            },
            false,
        )?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!(
            (
                word(&header, 0),
                word(&header, 4),
                word(&header, 20) as i32,
                word(&header, 24)
            ),
            (3, 40, -32, 2)
        );
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!(
            (word(&header, 0), word(&header, 4), word(&header, 20)),
            (4, 41, 0)
        );
        assert_eq!(backend.state()?.retired, 4);
        server.shutdown().await
    }

    #[tokio::test(flavor = "current_thread")]
    async fn hung_usb_does_not_block_control_discovery_or_single_thread_timer() -> Result<()> {
        let backend = AsyncBackend::new()?;
        let server = backend.server().await?;
        let (mut peer, _) = import(server.local_addr(), "1-1").await?;
        peer.write_all(&submit(1, 0x10001, 1, 2, 4, [0; 8])).await?;
        backend.wait_request(1, false).await?;
        peer.write_all(&submit(2, 0x10001, 0, 0, 0, [0x40, 0x7e, 0, 0, 0, 0, 0, 0]))
            .await?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!((word(&header, 4), word(&header, 20)), (2, 0));
        let mut list = TcpStream::connect(server.local_addr()).await?;
        list.write_all(&rustusb_protocol::DEVLIST_REQUEST).await?;
        assert!(rustusb_protocol::read_devlist(&mut list).await?.is_empty());
        tokio::time::sleep(Duration::from_millis(5)).await;
        peer.write_all(&unlink(3, 0x10001, 1)).await?;
        backend.wait_request(1, true).await?;
        backend.terminal(
            1,
            crate::wire::Completion {
                status: -104,
                actual_length: 0,
                data: vec![],
            },
            true,
        )?;
        let (header, _) = reply(&mut peer, false).await?;
        assert_eq!((word(&header, 4), word(&header, 20) as i32), (3, -104));
        server.shutdown().await
    }

    #[tokio::test(flavor = "current_thread")]
    async fn unfinished_cleanup_remains_busy_and_shutdown_waits_for_reaping() -> Result<()> {
        let backend = AsyncBackend::new()?;
        backend.state()?.cleanup_allowed = false;
        let server = backend.server().await?;
        let (mut peer, _) = import(server.local_addr(), "1-1").await?;
        peer.write_all(&submit(1, 0x10001, 1, 2, 4, [0; 8])).await?;
        backend.wait_request(1, false).await?;
        // Close malformed TCP promptly while the supervised USB reap remains
        // blocked; neither transport failure nor EOF grants a fresh lease.
        peer.write_all(&[0, 0, 0, 9]).await?;
        closed(&mut peer).await?;
        drop(peer);
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if backend.state()?.closing {
                    return Ok::<_, Error>(());
                }
                tokio::task::yield_now().await;
            }
        })
        .await
        .map_err(|_| Error::Timeout("fixture closing state".into()))??;
        let (_, status) = import(server.local_addr(), "1-1").await?;
        assert_eq!(status, 2);
        assert!(backend.remote_devices(true)?.is_empty());
        let shutdown = server.shutdown();
        tokio::pin!(shutdown);
        assert!(
            tokio::time::timeout(Duration::from_millis(20), &mut shutdown)
                .await
                .is_err()
        );
        assert!(backend.state()?.closing && !backend.state()?.held.is_empty());
        backend.reap_closed()?;
        shutdown.await?;
        assert_eq!(backend.remote_devices(true)?.len(), 1);
        assert!(backend.state()?.held.is_empty());
        Ok(())
    }

    #[tokio::test(flavor = "current_thread")]
    async fn response_budget_is_retained_until_the_complete_frame_is_written() -> Result<()> {
        let backend = AsyncBackend::new()?;
        let mut lease =
            ServingBackend::claim(&backend, "1-1", None, &ConnectionAccess::Legacy).await?;
        let (mut writer, mut reader) = tokio::io::duplex(1);
        let (sender, mut receiver) = mpsc::channel(2);
        let frame = submit(1, 0x10001, 0, 0, 0, [0x40, 0x7e, 0, 0, 0, 0, 0, 0]);
        let command = wire::read_data(&mut frame.as_slice(), 0x10001)
            .await?
            .ok_or_else(|| Error::State("fixture request missing".into()))?;
        sender
            .send(Ok(Some(command)))
            .await
            .map_err(|_| Error::State("fixture channel closed".into()))?;
        {
            let actor = imported(&mut lease, &mut writer, &mut receiver);
            tokio::pin!(actor);
            assert!(
                tokio::time::timeout(Duration::from_millis(20), &mut actor)
                    .await
                    .is_err()
            );
            assert_eq!(backend.state()?.held.len(), 1);
            assert_eq!(backend.state()?.retired, 0);
            let drain = async {
                let mut header = [0; 48];
                reader.read_exact(&mut header).await?;
                assert_eq!(word(&header, 4), 1);
                sender
                    .send(Ok(None))
                    .await
                    .map_err(|_| Error::State("fixture channel closed".into()))?;
                Ok::<_, Error>(())
            };
            let (result, drained) = tokio::join!(actor, drain);
            result?;
            drained?;
        }
        assert_eq!(backend.state()?.retired, 1);
        assert!(backend.state()?.held.is_empty());
        lease.begin_close();
        Ok(())
    }

    #[tokio::test(flavor = "current_thread")]
    async fn shutdown_deadline_reports_quarantine_without_releasing_pending_hardware() -> Result<()>
    {
        let backend = AsyncBackend::new()?;
        backend.state()?.cleanup_allowed = false;
        let server = backend.server().await?;
        let (mut peer, _) = import(server.local_addr(), "1-1").await?;
        peer.write_all(&submit(1, 0x10001, 1, 2, 4, [0; 8])).await?;
        let id = backend.wait_request(1, false).await?;
        let result = server.shutdown().await;
        assert!(
            matches!(result, Err(Error::Timeout(ref context)) if context.contains("quarantined")),
            "{result:?}"
        );
        assert!(backend.state()?.closing);
        assert!(backend.state()?.held.contains(&id));
        assert!(backend.remote_devices(true)?.is_empty());
        assert!(matches!(
            ServingBackend::claim(&backend, "1-1", None, &ConnectionAccess::Legacy).await,
            Err(Error::State(_))
        ));
        backend.reap_closed()?;
        backend.settle_closed().await?;
        assert_eq!(backend.remote_devices(true)?.len(), 1);
        closed(&mut peer).await
    }

    #[tokio::test(flavor = "current_thread")]
    async fn idle_device_disconnect_closes_tcp_without_releasing_uncleaned_generation() -> Result<()>
    {
        let backend = AsyncBackend::new()?;
        let server = backend.server().await?;
        let (mut peer, status) = import(server.local_addr(), "1-1").await?;
        assert_eq!(status, 0);
        assert!(backend.state()?.requests.is_empty());
        assert!(backend.state()?.held.is_empty());
        // No SUBMIT is needed to subscribe to a generation's lifetime signal.
        backend.disconnect()?;
        closed(&mut peer).await?;
        assert!(backend.state()?.leased && backend.state()?.closing);
        assert!(backend.remote_devices(true)?.is_empty());
        let (_, status) = import(server.local_addr(), "1-1").await?;
        assert_eq!(status, 2);
        backend.reap_closed()?;
        // Cleanup completion for a removed generation must not advertise it.
        assert!(backend.remote_devices(true)?.is_empty());
        assert!(matches!(
            ServingBackend::claim(&backend, "1-1", None, &ConnectionAccess::Legacy).await,
            Err(Error::InvalidInput(_))
        ));
        server.shutdown().await
    }
}

#[cfg(test)]
#[path = "server_tls_tests.rs"]
mod tls_tests;

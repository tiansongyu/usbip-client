//! Owned listener, connection task tree and serialized USB/IP session actors.

use crate::{
    device::{DeviceLease, VirtualBackend},
    wire::{self, DataCommand, Management, Submit},
};
use rustusb_core::{Error, Result};
use std::{
    collections::VecDeque,
    net::{IpAddr, Ipv4Addr, SocketAddr},
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

/// Explicit network policy for the experimental virtual service.
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
    fn validate(&self) -> Result<()> {
        if !self.listen.ip().is_loopback()
            && (!self.trusted_network || self.allowed_peers.is_empty())
        {
            return Err(Error::Permission("non-loopback virtual serving requires a trusted-network declaration and exact peer allowlist".into()));
        }
        if self.allowed_peers.len() > 64
            || self
                .allowed_peers
                .iter()
                .any(|ip| ip.is_unspecified() || ip.is_multicast())
        {
            return Err(Error::InvalidInput(
                "virtual peer allowlist must contain at most 64 concrete unicast IP addresses"
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

/// Owns the listening task and its entire connection/reader task tree.
/// Dropping this handle aborts that tree; `shutdown` additionally awaits cleanup.
#[derive(Debug)]
pub struct ServerHandle {
    address: SocketAddr,
    stop: Option<oneshot::Sender<()>>,
    task: Option<JoinHandle<Result<()>>>,
}

impl ServerHandle {
    /// Bind an explicit policy and start the two-profile virtual service.
    pub async fn bind(config: ServerConfig, backend: VirtualBackend) -> Result<Self> {
        config.validate()?;
        let listener = TcpListener::bind(config.listen).await?;
        let address = listener.local_addr()?;
        let (stop, receiver) = oneshot::channel();
        let task = tokio::spawn(listen(listener, config, backend, receiver));
        Ok(Self {
            address,
            stop: Some(stop),
            task: Some(task),
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
                Error::State("virtual listener task failed during shutdown".into())
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
            task.abort();
        }
    }
}

async fn listen(
    listener: TcpListener,
    config: ServerConfig,
    backend: VirtualBackend,
    mut stop: oneshot::Receiver<()>,
) -> Result<()> {
    let limit = std::sync::Arc::new(Semaphore::new(MAX_CONNECTIONS));
    let mut connections = JoinSet::new();
    let outcome = loop {
        tokio::select! {
            _ = &mut stop => break Ok(()),
            accepted = listener.accept() => {
                let (stream, peer) = match accepted {
                    Ok(connection) => connection,
                    Err(error) => break Err(Error::Io(error)),
                };
                if !config.permits(peer.ip()) {
                    continue;
                }
                let Ok(permit) = limit.clone().try_acquire_owned() else { continue };
                let backend = backend.clone();
                connections.spawn(async move {
                    let _permit = permit;
                    let result = connection(stream, backend).await;
                    tracing::debug!(success = result.is_ok(), "virtual USB/IP connection finished");
                });
            }
            Some(result) = connections.join_next(), if !connections.is_empty() => {
                if result.is_err() {
                    tracing::warn!("virtual USB/IP connection task failed; its lease was dropped");
                }
            }
        }
    };
    drop(listener);
    connections.abort_all();
    while connections.join_next().await.is_some() {}
    outcome
}

async fn write_frame(writer: &mut (impl AsyncWrite + Unpin), frame: &[u8]) -> Result<()> {
    tokio::time::timeout(wire::FRAME_DEADLINE, writer.write_all(frame))
        .await
        .map_err(|_| Error::Timeout("virtual USB/IP response write".into()))??;
    Ok(())
}

async fn handshake(
    reader: &mut (impl AsyncRead + Unpin),
    writer: &mut (impl AsyncWrite + Unpin),
    backend: &VirtualBackend,
) -> Result<Option<DeviceLease>> {
    match wire::read_management(reader).await? {
        Management::Devlist => {
            write_frame(
                writer,
                &wire::devlist_reply(&backend.remote_devices(true)?)?,
            )
            .await?;
            Ok(None)
        }
        Management::Import(bus_id) => {
            let lease = match backend.claim(&bus_id) {
                Ok(lease) => lease,
                Err(Error::State(_)) => {
                    write_frame(writer, &wire::import_reply(2, None)?).await?;
                    return Ok(None);
                }
                Err(Error::InvalidInput(_)) => {
                    write_frame(writer, &wire::import_reply(4, None)?).await?;
                    return Ok(None);
                }
                Err(error) => return Err(error),
            };
            write_frame(writer, &wire::import_reply(0, Some(&lease.device()))?).await?;
            Ok(Some(lease))
        }
    }
}

async fn connection(stream: TcpStream, backend: VirtualBackend) -> Result<()> {
    stream.set_nodelay(true)?;
    let (mut reader, mut writer) = stream.into_split();
    let Some(lease) = handshake(&mut reader, &mut writer, &backend).await? else {
        return Ok(());
    };
    let metadata = lease.device();
    let devid = metadata
        .bus_number
        .checked_shl(16)
        .filter(|_| {
            metadata.bus_number <= u32::from(u16::MAX)
                && metadata.device_number <= u32::from(u16::MAX)
        })
        .map(|bus| bus | metadata.device_number)
        .ok_or_else(|| Error::State("virtual device addressing exceeds USB/IP devid".into()))?;
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
    let actor = imported(&lease, &mut writer, &mut receiver);
    tokio::pin!(read_frames, actor);
    // The reader future retains its partial frame across every actor wake.
    // Both futures live inside this connection task, so aborting/joining that
    // task also drops its reader synchronously, without a detached child.
    tokio::select! {
        _ = &mut read_frames => actor.await,
        result = &mut actor => result,
    }
}

async fn imported(
    lease: &DeviceLease,
    writer: &mut (impl AsyncWrite + Unpin),
    receiver: &mut mpsc::Receiver<Result<Option<DataCommand>>>,
) -> Result<()> {
    let mut pending: VecDeque<Submit> = VecDeque::new();
    loop {
        let notified = lease.notify().notified();
        tokio::pin!(notified);
        notified.as_mut().enable();
        // Sequence numbers can wrap and are peer-chosen. Input is assigned in
        // submission order, never in numeric sequence-number order.
        let count = pending.len();
        for _ in 0..count {
            let Some(request) = pending.pop_front() else {
                break;
            };
            if let Some(completion) = lease.poll_pending(&request)? {
                lease.set_pending_count(pending.len())?;
                write_frame(writer, &wire::submit_reply(&request, completion)?).await?;
            } else {
                pending.push_back(request);
            }
        }
        let command = tokio::select! {
            message = receiver.recv() => match message {
                Some(Ok(Some(command))) => command,
                Some(Ok(None)) | None => return Ok(()),
                Some(Err(error)) => return Err(error),
            },
            _ = &mut notified, if !pending.is_empty() => continue,
            // HID SET_IDLE is a timer condition; this does not emit empty IN
            // successes, and runs only while there are bounded pending URBs.
            _ = tokio::time::sleep(Duration::from_millis(10)), if !pending.is_empty() => continue,
        };
        match command {
            DataCommand::Submit(request) => {
                if pending.iter().any(|active| active.seqnum == request.seqnum) {
                    return Err(Error::Protocol(
                        "duplicate active URB sequence number".into(),
                    ));
                }
                match lease.submit(&request)? {
                    Some(completion) => {
                        write_frame(writer, &wire::submit_reply(&request, completion)?).await?
                    }
                    None => {
                        if request.direction != 1 || pending.len() >= MAX_PENDING {
                            return Err(Error::Protocol(
                                "pending URB count or direction is invalid".into(),
                            ));
                        }
                        pending.push_back(request);
                        lease.set_pending_count(pending.len())?;
                    }
                }
            }
            DataCommand::Unlink { seqnum, target } => {
                if pending.iter().any(|active| active.seqnum == seqnum) {
                    return Err(Error::Protocol(
                        "UNLINK reuses an active URB sequence number".into(),
                    ));
                }
                let removed = pending
                    .iter()
                    .position(|request| request.seqnum == target)
                    .and_then(|index| pending.remove(index))
                    .is_some();
                lease.set_pending_count(pending.len())?;
                lease.record_unlink(removed)?;
                write_frame(
                    writer,
                    &wire::unlink_reply(seqnum, if removed { -104 } else { 0 }),
                )
                .await?;
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
        let (mut reader, mut writer) = tokio::io::split(io);
        assert!(handshake(&mut reader, &mut writer, &backend).await.is_err());
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
}

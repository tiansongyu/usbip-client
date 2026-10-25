//! Private byte-stream adapters. Owners remain separate and are always joined.
use rustusb_core::{
    Error, Result,
    link_health::{LinkEnd, LinkHealth, LinkProtocol},
};
use rustusb_transport::{LinkMonitor, LinkOwner, TlsServer, VerifiedPeer};
use std::{
    pin::Pin,
    task::{Context, Poll},
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    net::TcpStream,
    sync::watch,
};

pub(super) trait ByteStream: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> ByteStream for T {}
pub(super) struct Io(Box<dyn ByteStream>);
impl Io {
    fn new(value: impl ByteStream + 'static) -> Self {
        Self(Box::new(value))
    }
}
impl AsyncRead for Io {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        b: &mut ReadBuf<'_>,
    ) -> Poll<std::io::Result<()>> {
        Pin::new(&mut *self.0).poll_read(cx, b)
    }
}
impl AsyncWrite for Io {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        b: &[u8],
    ) -> Poll<std::io::Result<usize>> {
        Pin::new(&mut *self.0).poll_write(cx, b)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut *self.0).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        Pin::new(&mut *self.0).poll_shutdown(cx)
    }
}
pub(super) struct Connection {
    pub io: Io,
    pub owner: Option<LinkOwner>,
    pub monitor: Option<LinkMonitor>,
}
pub(super) async fn connect(
    config: &rustusb_transport::config::LoadedClientConfig,
) -> Result<Connection> {
    let tcp = rustusb_protocol::connect::connect(&config.endpoint).await?;
    tcp.set_nodelay(true)?;
    if config.tls.protocol() == LinkProtocol::FramedV2 {
        let link = config
            .tls
            .connect_link(&config.server_name, tcp)
            .await
            .map_err(|_| Error::Permission("secure framed server authentication failed".into()))?;
        Ok(Connection {
            io: Io::new(link.io),
            owner: Some(link.owner),
            monitor: Some(link.monitor),
        })
    } else {
        let io = config
            .tls
            .connect(&config.server_name, tcp)
            .await
            .map_err(|_| Error::Permission("secure server authentication failed".into()))?;
        Ok(Connection {
            io: Io::new(io),
            owner: None,
            monitor: None,
        })
    }
}
pub(super) async fn accept(tls: &TlsServer, tcp: TcpStream) -> Result<(Connection, VerifiedPeer)> {
    tcp.set_nodelay(true)?;
    if tls.protocol() == LinkProtocol::FramedV2 {
        let link = tls
            .accept_link(tcp)
            .await
            .map_err(|_| Error::Permission("secure framed peer authentication failed".into()))?;
        let peer = link.io.peer().clone();
        Ok((
            Connection {
                io: Io::new(link.io),
                owner: Some(link.owner),
                monitor: Some(link.monitor),
            },
            peer,
        ))
    } else {
        let io = tls
            .accept(tcp)
            .await
            .map_err(|_| Error::Permission("secure peer authentication failed".into()))?;
        let peer = io.peer().clone();
        Ok((
            Connection {
                io: Io::new(io),
                owner: None,
                monitor: None,
            },
            peer,
        ))
    }
}
pub(super) fn error(reason: LinkEnd) -> Option<Error> {
    match reason {
        LinkEnd::LocalStop | LinkEnd::PeerEof => None,
        LinkEnd::LocalDataStall => Some(Error::Timeout(
            "data-link local receive drain stalled".into(),
        )),
        LinkEnd::ProbeTimeout => Some(Error::Timeout("data-link heartbeat timed out".into())),
        LinkEnd::Protocol => Some(Error::Protocol("data-link protocol failed".into())),
        LinkEnd::CounterOverflow => Some(Error::State("data-link counter exhausted".into())),
        LinkEnd::Io => Some(Error::Io(std::io::Error::other("data-link I/O failed"))),
    }
}
pub(super) fn check(monitor: Option<&LinkMonitor>) -> Result<()> {
    if let Some(monitor) = monitor {
        let sample = monitor
            .snapshot()
            .map_err(|_| Error::State("data-link observation unavailable".into()))?;
        if let Some(reason) = sample.end {
            return Err(
                error(reason).unwrap_or_else(|| Error::State("data-link already ended".into()))
            );
        }
    }
    Ok(())
}
pub(super) async fn ended(
    receiver: &mut Option<watch::Receiver<bool>>,
    monitor: Option<&LinkMonitor>,
) -> Error {
    let Some(receiver) = receiver else {
        return std::future::pending().await;
    };
    loop {
        if *receiver.borrow_and_update() {
            break;
        }
        if receiver.changed().await.is_err() {
            break;
        }
    }
    match monitor.map(LinkMonitor::snapshot) {
        Some(Ok(sample)) => match sample.end {
            // A clean, fully drained EOF belongs to the existing byte-relay
            // half-close policy. Do not race that EOF into an operation error.
            Some(LinkEnd::PeerEof) => std::future::pending().await,
            Some(reason) => {
                error(reason).unwrap_or_else(|| Error::State("data-link owner ended".into()))
            }
            None => Error::State("data-link owner ended".into()),
        },
        _ => Error::State("data-link owner ended".into()),
    }
}
pub(super) async fn finish(owner: &mut Option<LinkOwner>) -> (Result<()>, Option<LinkHealth>) {
    join(owner, true).await
}

// Call only after the complete management response was written or parsed.
// The stream stays alive until the exact driver joins. Only pure FIN/EOF I/O
// is interruptible; dropping an owner.join() waiter would detach that driver.
pub(super) async fn complete_management(
    io: &mut (impl AsyncRead + AsyncWrite + Unpin),
    owner: &mut Option<LinkOwner>,
    interrupted: impl std::future::Future<Output = Result<()>>,
) -> Result<bool> {
    if owner.is_none() {
        return Ok(true);
    }
    let result = tokio::select! {
        biased;
        result = interrupted => result.map(|()| false),
        result = rustusb_protocol::secure::finish_framed_management(io) => result.map(|()| true),
    };
    let (closed, health) = join(owner, !matches!(result, Ok(true))).await;
    #[cfg(test)]
    if closed.is_err() {
        eprintln!(
            "test-only management close: fin_eof_complete={} owner_error={} phase_end={:?}",
            matches!(result, Ok(true)),
            closed.is_err(),
            health.as_ref().map(|h| (
                h.phase,
                h.end,
                h.stats.as_ref().map(|s| s.receive_pending_bytes)
            ))
        );
    }
    #[cfg(not(test))]
    let _ = health;
    match (result, closed) {
        (Err(error), _) | (_, Err(error)) => Err(error),
        (Ok(completed), Ok(())) => Ok(completed),
    }
}

async fn join(
    owner: &mut Option<LinkOwner>,
    request_stop: bool,
) -> (Result<()>, Option<LinkHealth>) {
    let Some(owner) = owner.take() else {
        return (Ok(()), None);
    };
    if request_stop {
        owner.request_stop();
    }
    match owner.join().await {
        Ok(outcome) => (
            if !request_stop && outcome.reason != LinkEnd::PeerEof {
                Err(error(outcome.reason).unwrap_or_else(|| {
                    Error::State("framed management did not finish normally".into())
                }))
            } else {
                error(outcome.reason).map_or(Ok(()), Err)
            },
            Some(outcome.final_health),
        ),
        Err(_) => (
            Err(Error::State(
                "data-link driver completion unconfirmed".into(),
            )),
            None,
        ),
    }
}

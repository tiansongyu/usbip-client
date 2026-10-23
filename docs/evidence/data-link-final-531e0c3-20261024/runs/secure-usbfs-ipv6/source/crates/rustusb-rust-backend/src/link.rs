//! Internal authenticated stream adaptation; a monitor never owns the driver.
use rustusb_core::{
    Error, Result,
    link_health::{LinkEnd, LinkProtocol},
};
use rustusb_transport::{LinkMonitor, LinkOwner, TlsServer, VerifiedPeer};
use std::{
    future::Future,
    pin::Pin,
    task::{Context, Poll},
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    net::TcpStream,
    sync::watch,
};
trait ByteStream: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> ByteStream for T {}
pub(crate) struct Io(Box<dyn ByteStream>);
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
pub(crate) struct Accepted {
    pub io: Io,
    pub owner: Option<LinkOwner>,
    pub monitor: Option<LinkMonitor>,
    pub peer: VerifiedPeer,
}
pub(crate) async fn accept(tls: &TlsServer, tcp: TcpStream) -> Result<Accepted> {
    if tls.protocol() == LinkProtocol::FramedV2 {
        let link = tls
            .accept_link(tcp)
            .await
            .map_err(|_| Error::Permission("framed mutual TLS authentication failed".into()))?;
        let peer = link.io.peer().clone();
        Ok(Accepted {
            io: Io(Box::new(link.io)),
            owner: Some(link.owner),
            monitor: Some(link.monitor),
            peer,
        })
    } else {
        let io = tls
            .accept(tcp)
            .await
            .map_err(|_| Error::Permission("mutual TLS authentication failed".into()))?;
        let peer = io.peer().clone();
        Ok(Accepted {
            io: Io(Box::new(io)),
            owner: None,
            monitor: None,
            peer,
        })
    }
}
pub(crate) fn end_error(reason: LinkEnd) -> Option<Error> {
    match reason {
        LinkEnd::LocalStop | LinkEnd::PeerEof => None,
        LinkEnd::ProbeTimeout => Some(Error::Timeout("data-link heartbeat timed out".into())),
        LinkEnd::LocalDataStall => Some(Error::Timeout(
            "data-link local receive drain stalled".into(),
        )),
        LinkEnd::Protocol => Some(Error::Protocol("data-link protocol failed".into())),
        LinkEnd::CounterOverflow => Some(Error::State("data-link counter exhausted".into())),
        LinkEnd::Io => Some(Error::Io(std::io::Error::other("data-link I/O failed"))),
    }
}
pub(crate) async fn ended(
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
                end_error(reason).unwrap_or_else(|| Error::State("data-link owner ended".into()))
            }
            None => Error::State("data-link owner ended".into()),
        },
        _ => Error::State("data-link owner ended".into()),
    }
}
pub(crate) fn check(monitor: Option<&LinkMonitor>) -> Result<()> {
    if let Some(monitor) = monitor {
        let ended = monitor.ended();
        if *ended.borrow() || ended.has_changed().is_err() {
            let health = monitor
                .snapshot()
                .map_err(|_| Error::State("data-link observation unavailable".into()))?;
            return Err(health
                .end
                .and_then(end_error)
                .unwrap_or_else(|| Error::State("data-link already ended".into())));
        }
    }
    Ok(())
}
pub(crate) async fn finish(owner: &mut Option<LinkOwner>) -> Result<()> {
    let Some(owner) = owner.take() else {
        return Ok(());
    };
    owner.request_stop();
    let outcome = owner
        .join()
        .await
        .map_err(|_| Error::State("data-link driver completion unconfirmed".into()))?;
    end_error(outcome.reason).map_or(Ok(()), Err)
}

/// Finish only an already complete one-shot v2 response. Retain the actual IO
/// across FIN, EOF and natural join; stop may cancel pure IO, never the join.
pub(crate) async fn complete_management<R, W>(
    reader: &mut R,
    writer: &mut W,
    owner: &mut Option<LinkOwner>,
    stop: impl Future<Output = ()>,
) -> Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    if owner.is_none() {
        return Ok(()); // Frozen raw-v1 lifetime: do not introduce a FIN wait.
    }
    let result = {
        let mut stream = tokio::io::join(reader, writer);
        tokio::select! {
            biased;
            _ = stop => Err(Error::State("framed management stopped".into())),
            result = rustusb_protocol::secure::finish_framed_management(&mut stream) => result,
        }
    };
    if let Err(error) = result {
        // Preserve the original operation failure, while awaiting actual driver
        // stop even if that separate teardown also reports an error.
        let closed = finish(owner).await;
        tracing::debug!(
            network_closed = closed.is_ok(),
            "failed framed management owner joined"
        );
        return Err(error);
    }
    let actual = owner
        .take()
        .ok_or_else(|| Error::State("framed management owner missing".into()))?;
    let outcome = actual
        .join()
        .await
        .map_err(|_| Error::State("data-link driver completion unconfirmed".into()))?;
    match outcome.reason {
        LinkEnd::PeerEof => Ok(()),
        reason => Err(end_error(reason)
            .unwrap_or_else(|| Error::State("framed management did not end cleanly".into()))),
    }
}

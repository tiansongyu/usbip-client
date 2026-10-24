//! Actual TLS transport/permit contracts; memory and loopback fixtures, no USB.
use super::*;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

struct Untouched {
    calls: Arc<AtomicUsize>,
    dropped: Arc<AtomicBool>,
}
impl AsyncRead for Untouched {
    fn poll_read(
        self: Pin<&mut Self>,
        _: &mut Context<'_>,
        _: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Poll::Pending
    }
}
impl AsyncWrite for Untouched {
    fn poll_write(self: Pin<&mut Self>, _: &mut Context<'_>, _: &[u8]) -> Poll<io::Result<usize>> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Poll::Pending
    }
    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Poll::Pending
    }
    fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        Poll::Pending
    }
}
impl Drop for Untouched {
    fn drop(&mut self) {
        self.dropped.store(true, Ordering::SeqCst);
    }
}

fn limited(fixture: &Fixture, interval: u32) -> TestResult<(TlsServer, TlsClient)> {
    Ok((
        TlsServer::new_with_admission(
            fixture.ca.roots()?,
            fixture.server.identity()?,
            fixture.policy()?,
            crate::AdmissionPolicy::new(1, interval)?,
        )?,
        TlsClient::new(fixture.ca.roots()?, fixture.client.identity()?)?,
    ))
}

async fn refused_without_io(server: &TlsServer) -> TestResult {
    let calls = Arc::new(AtomicUsize::new(0));
    let dropped = Arc::new(AtomicBool::new(false));
    let stream = Untouched {
        calls: calls.clone(),
        dropped: dropped.clone(),
    };
    let result = tokio::time::timeout(Duration::from_secs(1), server.accept(stream)).await?;
    assert!(matches!(result, Err(Error::AdmissionRateLimited)));
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    assert!(dropped.load(Ordering::SeqCst));
    Ok(())
}

#[tokio::test]
async fn raw_rate_refusals_touch_no_io_and_preserve_original_authenticated_stream() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = limited(&fixture, 60_000)?;
    let clone = server.clone();
    let observer = server.admission_monitor();
    let weak = Arc::downgrade(&server.admission);
    let (mut incoming, mut outgoing) = connected(&server, &client).await?;
    for _ in 0..32 {
        refused_without_io(&clone).await?;
    }
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS - 1);
    outgoing.write_all(b"still usable").await?;
    outgoing.flush().await?;
    let mut bytes = [0; 12];
    incoming.read_exact(&mut bytes).await?;
    assert_eq!(&bytes, b"still usable");
    incoming.write_all(b"reply").await?;
    incoming.flush().await?;
    let mut response = [0; 5];
    outgoing.read_exact(&mut response).await?;
    assert_eq!(&response, b"reply");
    let current = observer.snapshot();
    assert_eq!((current.admitted, current.limited), (1, 32));
    let old = server.security_monitor().snapshot();
    assert_eq!(old.tls.attempts, 1);
    assert_eq!(old.tls.authenticated, 1);
    drop((incoming, outgoing));
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    drop((server, clone));
    assert!(weak.upgrade().is_none());
    assert_eq!(observer.snapshot(), current);
    Ok(())
}

#[tokio::test]
async fn cancelled_or_failed_handshake_does_not_refund_and_real_clock_replenishes() -> TestResult {
    let fixture = Fixture::new()?;
    for cancel in [false, true] {
        let (server, _) = limited(&fixture, 60_000)?;
        let (left, right) = duplex(64);
        if cancel {
            let mut pending = Box::pin(server.accept(left));
            poll_fn(|cx| {
                assert!(pending.as_mut().poll(cx).is_pending());
                Poll::Ready(())
            })
            .await;
            drop(pending);
            drop(right);
        } else {
            drop(right);
            assert!(matches!(
                server.accept(left).await,
                Err(Error::HandshakeFailed)
            ));
        }
        refused_without_io(&server).await?;
        assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
        assert_eq!(server.admission_monitor().snapshot().admitted, 1);
    }
    let (server, client) = limited(&fixture, 500)?;
    let original = connected(&server, &client).await?;
    tokio::time::sleep(Duration::from_millis(501)).await;
    let replacement = connected(&server, &client).await?;
    assert_eq!(server.admission_monitor().snapshot().admitted, 2);
    drop((original, replacement));
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn framed_rate_refusal_preserves_driver_data_and_existing_security_dto() -> TestResult {
    use tokio::net::{TcpListener, TcpStream};
    let fixture = Fixture::new()?;
    let probes = ProbePolicy::default();
    let server = TlsServer::new_with_link_and_admission(
        fixture.ca.roots()?,
        fixture.server.identity()?,
        fixture.policy()?,
        probes,
        crate::AdmissionPolicy::new(1, 60_000)?,
    )?;
    let client = TlsClient::new_with_link(fixture.ca.roots()?, fixture.client.identity()?, probes)?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let (outgoing, incoming) = tokio::join!(TcpStream::connect(address), listener.accept());
    let (incoming, outgoing) = tokio::join!(
        server.accept_link(incoming?.0),
        client.connect_link("server.test", outgoing?)
    );
    let (mut incoming, mut outgoing) = (incoming?, outgoing?);
    let (extra, accepted) = tokio::join!(TcpStream::connect(address), listener.accept());
    let mut extra = extra?;
    assert!(matches!(
        server.accept_link(accepted?.0).await,
        Err(Error::AdmissionRateLimited)
    ));
    assert_eq!(
        tokio::time::timeout(Duration::from_secs(1), extra.read(&mut [0])).await??,
        0
    );
    outgoing.io.write_all(b"original driver").await?;
    outgoing.io.flush().await?;
    let mut bytes = [0; 15];
    incoming.io.read_exact(&mut bytes).await?;
    assert_eq!(&bytes, b"original driver");
    let snapshot = server.security_monitor().snapshot();
    assert_eq!(snapshot.tls.attempts, 1);
    assert_eq!(snapshot.framing.ready, 1);
    assert_eq!(server.admission_monitor().snapshot().limited, 1);
    incoming.owner.request_stop();
    outgoing.owner.request_stop();
    incoming.owner.join().await?;
    outgoing.owner.join().await?;
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

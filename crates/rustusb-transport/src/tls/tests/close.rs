//! Real TLS close errors stay errors after our write half closes.
use super::*;
use crate::close::ClosingIo;
use tokio::net::{TcpListener, TcpStream};

#[tokio::test]
async fn completed_write_close_does_not_hide_missing_peer_tls_close_notify() -> TestResult {
    let fixture = Fixture::new()?;
    let probes = ProbePolicy::new(Duration::from_secs(1), Duration::from_secs(3))?;
    let server = TlsServer::new_with_link(
        fixture.ca.roots()?,
        fixture.server.identity()?,
        fixture.policy()?,
        probes,
    )?;
    let client = TlsClient::new_with_link(fixture.ca.roots()?, fixture.client.identity()?, probes)?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let (outgoing, incoming) = tokio::join!(
        TcpStream::connect(listener.local_addr()?),
        listener.accept()
    );
    let (incoming, outgoing) = tokio::join!(
        server.accept_with_deadline(incoming?.0, HANDSHAKE_TIMEOUT),
        client.connect_authenticated("server.test", outgoing?)
    );
    let mut incoming = incoming?;
    let mut outgoing = ClosingIo::new(outgoing?);
    let peer = tokio::spawn(async move {
        let mut payload = [0; 5];
        incoming.read_exact(&mut payload).await?;
        assert_eq!(&payload, b"known");
        let mut extra = [0];
        assert_eq!(incoming.read(&mut extra).await?, 0);
        // The actual client close_notify was read; the peer intentionally
        // drops its socket without writing its own TLS close_notify.
        drop(incoming);
        Ok::<(), io::Error>(())
    });
    let operation = async {
        outgoing.write_all(b"known").await?;
        outgoing.flush().await?;
        outgoing.shutdown().await
    }
    .await;
    peer.await??;
    assert_eq!(
        operation.err().map(|e| e.kind()),
        Some(io::ErrorKind::UnexpectedEof)
    );
    assert_eq!(
        outgoing.shutdown().await.err().map(|e| e.kind()),
        Some(io::ErrorKind::UnexpectedEof)
    );
    Ok(())
}

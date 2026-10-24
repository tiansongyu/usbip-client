//! Real loopback TCP + memory-DER mTLS contracts; no helper or USB claims.

use super::*;
use rustusb_core::link_health::{LinkEnd, LinkProtocol, ProbeState};
use tokio::net::{TcpListener, TcpStream};

fn endpoints(fixture: &Fixture) -> TestResult<(TlsServer, TlsClient)> {
    let probes = ProbePolicy::new(Duration::from_secs(1), Duration::from_secs(3))?;
    Ok((
        TlsServer::new_with_link(
            fixture.ca.roots()?,
            fixture.server.identity()?,
            fixture.policy()?,
            probes,
        )?,
        TlsClient::new_with_link(fixture.ca.roots()?, fixture.client.identity()?, probes)?,
    ))
}

async fn sockets() -> TestResult<(TcpStream, TcpStream)> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let address = listener.local_addr()?;
    let (client, accepted) = tokio::join!(TcpStream::connect(address), listener.accept());
    Ok((accepted?.0, client?))
}

#[tokio::test]
async fn framed_tcp_mtls_preserves_authentication_flush_and_actual_tuple() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = endpoints(&fixture)?;
    let (incoming, outgoing) = sockets().await?;
    let server_tuple = tcp_tuple(&incoming)?;
    let client_tuple = tcp_tuple(&outgoing)?;
    let (incoming, outgoing) = tokio::time::timeout(Duration::from_secs(6), async {
        tokio::join!(
            server.accept_link(incoming),
            client.connect_link("server.test", outgoing)
        )
    })
    .await?;
    let (mut incoming, mut outgoing) = (incoming?, outgoing?);
    let security = server.security_monitor().snapshot();
    assert_eq!(security.tls.authenticated, 1);
    assert_eq!(security.framing.ready, 1);
    assert_eq!(security.tls.in_flight + security.framing.in_flight, 0);
    assert!(incoming.io.peer().allows(&hid()));
    assert_eq!(incoming.io.peer().principal(), "lab-a");
    assert_eq!(outgoing.io.server_certificate_pin(), fixture.server.pin()?);
    assert_eq!(
        incoming
            .monitor
            .snapshot()?
            .identity
            .ok_or("missing server identity")?
            .tcp,
        server_tuple
    );
    assert_eq!(
        outgoing
            .monitor
            .snapshot()?
            .identity
            .ok_or("missing client identity")?
            .tcp,
        client_tuple
    );
    assert_eq!(client_tuple.local, server_tuple.peer);
    // Larger than one receive window: consumption must return credit while the
    // independent connection driver continues both DATA and control traffic.
    let payload = vec![0x5a; 96 * 1024];
    let mut received = vec![0; payload.len()];
    tokio::time::timeout(Duration::from_secs(4), async {
        let (written, read) = tokio::join!(
            async {
                outgoing.io.write_all(&payload).await?;
                outgoing.io.flush().await
            },
            incoming.io.read_exact(&mut received)
        );
        written?;
        read?;
        Ok::<(), io::Error>(())
    })
    .await??;
    assert_eq!(received, payload);
    incoming.io.write_all(b"reply").await?;
    incoming.io.flush().await?;
    let mut reply = [0; 5];
    outgoing.io.read_exact(&mut reply).await?;
    assert_eq!(&reply, b"reply");
    let mut changes = outgoing.monitor.changes();
    let sample = tokio::time::timeout(Duration::from_secs(4), async {
        loop {
            let sample = outgoing.monitor.snapshot()?;
            if sample.last_reply_sequence.is_some() {
                break Ok::<_, Error>(sample);
            }
            changes
                .changed()
                .await
                .map_err(|_| Error::LinkDriverFailed)?;
        }
    })
    .await??;
    assert_eq!(sample.protocol, LinkProtocol::FramedV2);
    assert!(matches!(
        sample.probe,
        ProbeState::ReplyObserved | ProbeState::Pending
    ));
    assert!(sample.last_round_trip_micros.is_some());
    assert_eq!(
        sample.stats.ok_or("missing v2 stats")?.app_write_bytes,
        payload.len() as u64
    );
    outgoing.owner.request_stop();
    incoming.owner.request_stop();
    assert_eq!(outgoing.owner.join().await?.reason, LinkEnd::LocalStop);
    assert_eq!(incoming.owner.join().await?.reason, LinkEnd::LocalStop);
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn explicit_v2_never_downgrades_to_raw_peer() -> TestResult {
    let fixture = Fixture::new()?;
    let (framed_server, framed_client) = endpoints(&fixture)?;
    let (raw_server, raw_client) = fixture.endpoints()?;
    let (incoming, outgoing) = sockets().await?;
    let (accepted, connected) = tokio::time::timeout(Duration::from_secs(6), async {
        tokio::join!(
            framed_server.accept_link(incoming),
            raw_client.connect("server.test", outgoing)
        )
    })
    .await?;
    assert!(accepted.is_err());
    assert!(connected.is_err());
    let (incoming, outgoing) = sockets().await?;
    let (accepted, connected) = tokio::time::timeout(Duration::from_secs(6), async {
        tokio::join!(
            raw_server.accept(incoming),
            framed_client.connect_link("server.test", outgoing)
        )
    })
    .await?;
    assert!(accepted.is_err());
    assert!(connected.is_err());
    assert_eq!(framed_server.limit.available_permits(), MAX_CONNECTIONS);
    assert_eq!(raw_server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn cancelled_authenticated_setup_has_no_detached_driver_or_permit() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = endpoints(&fixture)?;
    let (incoming, outgoing) = sockets().await?;
    let mut setup = Box::pin(server.accept_link(incoming));
    let authenticated = tokio::select! {
        result = client.connect_authenticated("server.test", outgoing) => result?,
        _ = &mut setup => return Err("server completed before h2 client preface".into()),
    };
    // Permit is already held by the authenticated setup future. No connection
    // driver is spawned until CONNECT setup has completed on this same socket.
    tokio::select! {
        _ = tokio::time::sleep(Duration::from_millis(30)) => {},
        _ = &mut setup => return Err("server accepted absent h2 preface".into()),
    }
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS - 1);
    drop(setup);
    drop(authenticated);
    let security = server.security_monitor().snapshot();
    assert_eq!(security.tls.authenticated, 1);
    assert_eq!(security.framing.cancelled, 1);
    assert_eq!(security.framing.ready + security.framing.in_flight, 0);
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn authenticated_invalid_and_silent_framing_have_distinct_observations() -> TestResult {
    for silent in [false, true] {
        let fixture = Fixture::new()?;
        let (server, client) = endpoints(&fixture)?;
        let (incoming, outgoing) = sockets().await?;
        let mut setup = Box::pin(server.accept_link(incoming));
        let mut authenticated = tokio::select! {
            result = client.connect_authenticated("server.test", outgoing) => result?,
            _ = &mut setup => return Err("setup ended before TLS client completion".into()),
        };
        if !silent {
            authenticated
                .write_all(b"INVALID HTTP/2 PREFACE\r\n\r\n")
                .await?;
            authenticated.flush().await?;
            drop(authenticated);
            assert!(
                tokio::time::timeout(Duration::from_secs(2), setup)
                    .await?
                    .is_err()
            );
        } else {
            assert!(matches!(
                tokio::time::timeout(Duration::from_secs(7), setup).await?,
                Err(Error::LinkSetupTimeout)
            ));
            drop(authenticated);
        }
        let security = server.security_monitor().snapshot();
        assert_eq!(security.tls.authenticated, 1);
        assert_eq!(security.framing.attempts, 1);
        assert_eq!(security.framing.in_flight, 0);
        assert_eq!(security.framing.ready + security.framing.cancelled, 0);
        assert_eq!(security.framing.timed_out, u64::from(silent));
        assert_eq!(security.framing.failed, u64::from(!silent));
        assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    }
    Ok(())
}

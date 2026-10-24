//! Real loopback catalog/supervisor tests. No helper or USB resource is used.

use super::*;
use rustusb_core::{DeviceKey, Host, Peer, VirtualProfile};
use rustusb_transport::config::LoadedClientConfig;
use std::{net::SocketAddr, num::NonZeroU16};
use tokio::{io::AsyncReadExt, net::TcpListener};

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

fn configured(
    fixture: &mut Fixture,
    address: SocketAddr,
    framed: bool,
) -> TestResult<rustusb_transport::TlsServer> {
    let (server, client) = crate::jobs::management_tests::credentials(framed)?;
    let config = Arc::get_mut(&mut fixture.runtime.config).ok_or("shared fixture config")?;
    config.manifest.operators[0]
        .actions
        .insert(Action::Discover);
    config.credentials.insert(
        PROFILE.into(),
        Credentials::Client(Arc::new(LoadedClientConfig {
            broker_uid: config.manifest.broker_uid,
            server_id: "test-server".into(),
            endpoint: Peer {
                host: Host::new(address.ip().to_string())?,
                port: NonZeroU16::new(address.port()).ok_or("zero test port")?,
            },
            server_name: "server.test".into(),
            device: DeviceKey::virtual_profile(VirtualProfile::Hid),
            tls: client,
        })),
    );
    Ok(server)
}

async fn send(
    sender: &mpsc::Sender<Command>,
    uid: u32,
    request: Request,
) -> TestResult<oneshot::Receiver<ResponseEnvelope>> {
    let (reply, receive) = oneshot::channel();
    sender
        .send(Command {
            uid,
            request,
            reply,
        })
        .await?;
    Ok(receive)
}

fn discover() -> Request {
    Request::Discover {
        profile: PROFILE.into(),
    }
}

// Keep the original supervisor and real peer until teardown even when the
// responsiveness assertion fails. The old product fails only after real join.
#[tokio::test]
async fn stalled_catalog_does_not_delay_an_unrelated_supervisor_request() -> TestResult {
    let mut fixture = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _server = configured(&mut fixture, listener.local_addr()?, false)?;
    let Fixture {
        root: _root,
        path: _,
        runtime,
    } = fixture;
    let (sender, commands) = mpsc::channel(16);
    let (stop, stopping) = watch::channel(false);
    let owner = tokio::spawn(runtime.run(commands, stopping));
    let discovery = send(&sender, operator_uid(), discover()).await?;
    let (mut peer, _) = tokio::time::timeout(Duration::from_secs(2), listener.accept()).await??;
    let mut header = [0; 5];
    tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut header)).await??;
    assert_eq!(
        header[0], 22,
        "peer must have received an actual TLS handshake"
    );
    let mut profiles = send(&sender, operator_uid(), Request::Profiles).await?;
    let observed = tokio::time::timeout(Duration::from_millis(200), &mut profiles).await;
    let responsive = matches!(
        observed,
        Ok(Ok(ResponseEnvelope {
            response: Response::Profiles { .. },
            ..
        }))
    );
    drop(peer);
    if observed.is_err() {
        let _ = tokio::time::timeout(Duration::from_secs(2), &mut profiles).await?;
    }
    let _ = tokio::time::timeout(Duration::from_secs(2), discovery).await?;
    stop.send_replace(true);
    tokio::time::timeout(Duration::from_secs(2), owner).await???;
    assert!(
        responsive,
        "remote catalog blocked the single durable supervisor"
    );
    Ok(())
}

async fn held_peer(listener: &TcpListener) -> TestResult<tokio::net::TcpStream> {
    let (mut peer, _) = tokio::time::timeout(Duration::from_secs(2), listener.accept()).await??;
    let mut header = [0; 5];
    tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut header)).await??;
    assert_eq!(header[0], 22);
    let mut hello = vec![0; u16::from_be_bytes([header[3], header[4]]) as usize];
    tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut hello)).await??;
    Ok(peer)
}

#[tokio::test]
async fn orphaned_catalogs_keep_capacity_and_shutdown_joins_them_for_both_stop_sources()
-> TestResult {
    for request_eof in [false, true] {
        let mut fixture = Fixture::new()?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let _server = configured(&mut fixture, listener.local_addr()?, false)?;
        let Fixture {
            root: _root,
            path,
            runtime,
        } = fixture;
        let (sender, commands) = mpsc::channel(16);
        let (stop, stopping) = watch::channel(false);
        let owner = tokio::spawn(runtime.run(commands, stopping));
        let first = send(&sender, operator_uid(), discover()).await?;
        let mut a = held_peer(&listener).await?;
        drop(first);
        let second = send(&sender, operator_uid(), discover()).await?;
        let mut b = held_peer(&listener).await?;
        let full = tokio::time::timeout(
            Duration::from_secs(1),
            send(&sender, operator_uid(), discover()).await?,
        )
        .await??;
        let denied = tokio::time::timeout(
            Duration::from_secs(1),
            send(&sender, other_uid(), discover()).await?,
        )
        .await??;
        let unknown = tokio::time::timeout(
            Duration::from_secs(1),
            send(
                &sender,
                operator_uid(),
                Request::Discover {
                    profile: "unknown".into(),
                },
            )
            .await?,
        )
        .await??;
        let no_third_connection =
            tokio::time::timeout(Duration::from_millis(30), listener.accept())
                .await
                .is_err();
        if request_eof {
            drop(sender);
        } else {
            stop.send_replace(true);
        }
        // Both real peers are still held. Only the actual discovery owner can
        // close these client sockets; waiter loss cannot substitute for it.
        tokio::time::timeout(Duration::from_secs(2), owner).await???;
        let second = second.await?;
        let mut tail = Vec::new();
        tokio::time::timeout(Duration::from_secs(1), a.read_to_end(&mut tail)).await??;
        tokio::time::timeout(Duration::from_secs(1), b.read_to_end(&mut tail)).await??;
        assert!(tail.is_empty(), "unexpected post-ClientHello traffic");
        assert!(matches!(
            full.response,
            Response::Error {
                code: Failure::State
            }
        ));
        assert!(matches!(
            denied.response,
            Response::Error {
                code: Failure::Permission
            }
        ));
        assert!(matches!(
            unknown.response,
            Response::Error {
                code: Failure::Permission
            }
        ));
        assert!(matches!(
            second.response,
            Response::Error {
                code: Failure::Cancelled
            }
        ));
        assert!(no_third_connection);
        let stored: State = serde_json::from_slice(&std::fs::read(path.join("state.json"))?)?;
        assert_eq!(stored.audit.len(), 2);
        assert!(
            stored
                .audit
                .iter()
                .all(|event| event.action == AuditAction::Denied)
        );
        assert!(stored.jobs.is_empty() && stored.sessions.is_empty());
    }
    Ok(())
}

#[tokio::test]
async fn authenticated_catalog_reply_waits_for_real_audit_commit_in_both_link_versions()
-> TestResult {
    for framed in [false, true] {
        let mut fixture = Fixture::new()?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let server = configured(&mut fixture, listener.local_addr()?, framed)?;
        let peer = tokio::spawn(crate::jobs::management_tests::serve_one_catalog(
            server, listener,
        ));
        let (stop, stopping) = watch::channel(false);
        let (reply, mut response) = oneshot::channel();
        fixture
            .runtime
            .receive_command(
                Command {
                    uid: operator_uid(),
                    request: discover(),
                    reply,
                },
                stopping.clone(),
                stopping,
            )
            .await;
        let completed = tokio::time::timeout(
            Duration::from_secs(3),
            fixture.runtime.discoveries.join_next(),
        )
        .await?
        .ok_or("missing catalog worker")??;
        peer.await??;
        assert!(matches!(&completed.result, Ok(Some(devices)) if devices.len() == 1));
        assert!(fixture.runtime.state.audit.is_empty());
        assert!(matches!(
            response.try_recv(),
            Err(oneshot::error::TryRecvError::Empty)
        ));
        let store = fixture.runtime.store.clone();
        let (locked, lock_received) = oneshot::channel();
        let (release, released) = std::sync::mpsc::channel();
        let blocker = tokio::task::spawn_blocking(move || {
            let _guard = store.lock().map_err(|_| lock_error())?;
            locked
                .send(())
                .map_err(|_| Error::State("catalog lock observer".into()))?;
            released
                .recv_timeout(Duration::from_secs(5))
                .map_err(|_| Error::Timeout("catalog store gate release".into()))?;
            Ok::<_, Error>(())
        });
        lock_received.await?;
        let (waiting, unacknowledged) = {
            let recording = fixture.runtime.catalog_finished(completed);
            tokio::pin!(recording);
            let waiting = tokio::time::timeout(Duration::from_millis(30), &mut recording)
                .await
                .is_err();
            let unacknowledged = matches!(
                response.try_recv(),
                Err(oneshot::error::TryRecvError::Empty)
            );
            // Release and join the actual blocker even if either assertion fails.
            release.send(())?;
            recording.await;
            (waiting, unacknowledged)
        };
        blocker.await??;
        let response = response.await?;
        assert!(
            waiting && unacknowledged,
            "catalog acknowledged before durable audit"
        );
        assert!(matches!(response.response, Response::Catalog { devices } if devices.len() == 1));
        assert!(response.journal_healthy);
        fixture.assert_committed()?;
        assert_eq!(fixture.runtime.state.audit.len(), 1);
        assert_eq!(fixture.runtime.state.audit[0].action, AuditAction::Discover);
        assert!(!fixture.runtime.config.manifest.helper_socket.exists());
        assert!(fixture.runtime.state.jobs.is_empty() && fixture.runtime.state.sessions.is_empty());
        drop(stop);
    }
    Ok(())
}

#[tokio::test]
async fn unhealthy_journal_refuses_catalog_without_starting_a_worker() -> TestResult {
    let mut fixture = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _server = configured(&mut fixture, listener.local_addr()?, false)?;
    fixture.runtime.healthy = false;
    let (_stop, stopping) = watch::channel(false);
    let (reply, response) = oneshot::channel();
    fixture
        .runtime
        .receive_command(
            Command {
                uid: operator_uid(),
                request: discover(),
                reply,
            },
            stopping.clone(),
            stopping,
        )
        .await;
    let response = response.await?;
    assert!(!response.journal_healthy);
    assert!(matches!(
        response.response,
        Response::Error {
            code: Failure::State
        }
    ));
    assert!(fixture.runtime.discoveries.is_empty());
    assert!(
        tokio::time::timeout(Duration::from_millis(30), listener.accept())
            .await
            .is_err()
    );
    Ok(())
}

#[tokio::test]
async fn shutdown_joins_framed_catalog_while_the_peer_keeps_reverse_fin_open() -> TestResult {
    let mut fixture = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let server = configured(&mut fixture, listener.local_addr()?, true)?;
    let (fin, fin_received) = oneshot::channel();
    let (release, released) = oneshot::channel();
    let peer = tokio::spawn(crate::jobs::management_tests::serve_held_catalog(
        server, listener, fin, released,
    ));
    let Fixture {
        root: _root,
        path: _,
        runtime,
    } = fixture;
    let (sender, commands) = mpsc::channel(16);
    let (stop, stopping) = watch::channel(false);
    let owner = tokio::spawn(runtime.run(commands, stopping));
    let response = send(&sender, operator_uid(), discover()).await?;
    tokio::time::timeout(Duration::from_secs(2), fin_received).await??;
    let unrelated = send(&sender, operator_uid(), Request::Profiles).await?;
    let observed = tokio::time::timeout(Duration::from_millis(200), unrelated).await;
    stop.send_replace(true);
    // Release the deliberate remote gate after the real service owner joins,
    // including on failure, so teardown cannot manufacture the earlier proof.
    let joined = tokio::time::timeout(Duration::from_secs(2), owner).await;
    release.send(()).map_err(|_| "held peer release gone")?;
    peer.await??;
    joined???;
    assert!(matches!(
        observed,
        Ok(Ok(ResponseEnvelope {
            response: Response::Profiles { .. },
            ..
        }))
    ));
    assert!(matches!(
        response.await?.response,
        Response::Error {
            code: Failure::Cancelled
        }
    ));
    Ok(())
}

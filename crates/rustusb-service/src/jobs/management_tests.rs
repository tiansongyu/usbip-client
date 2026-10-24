//! Real TCP/mTLS management ownership regressions; no USB/helper operations.
use super::*;
use crate::foreground::{self, ForegroundOptions};
use rcgen::{
    BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
    KeyUsagePurpose,
};
use rustusb_core::{BusId, DeviceKey, Host, Peer, VirtualProfile};
use rustusb_protocol::{RemoteDevice, RemoteInterface, secure, server};
use rustusb_transport::{
    AccessPolicy, CertificatePin, DerIdentity, PrincipalRule, ProbePolicy, TlsClient, TlsServer,
    TrustRoots,
};
use std::{net::SocketAddr, num::NonZeroU16, path::PathBuf};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpListener,
    sync::{oneshot, watch},
};

pub(super) type TestResult<T = ()> =
    std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

pub(crate) fn credentials(framed: bool) -> TestResult<(TlsServer, TlsClient)> {
    let mut ca = CertificateParams::new(Vec::<String>::new())?;
    ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    ca.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
    let ca_key = KeyPair::generate()?;
    let root = ca.self_signed(&ca_key)?.der().to_vec();
    let issuer = Issuer::new(ca, ca_key);
    let identity = |usage| -> TestResult<(DerIdentity, CertificatePin)> {
        let mut params = CertificateParams::new(vec!["server.test".into()])?;
        params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
        params.extended_key_usages = vec![usage];
        let key = KeyPair::generate()?;
        let certificate = params.signed_by(&key, &issuer)?.der().to_vec();
        let pin = CertificatePin::from_der(&certificate)?;
        Ok((
            DerIdentity::new(vec![certificate], key.serialize_der())?,
            pin,
        ))
    };
    let (server_identity, _) = identity(ExtendedKeyUsagePurpose::ServerAuth)?;
    let (client_identity, pin) = identity(ExtendedKeyUsagePurpose::ClientAuth)?;
    let policy = AccessPolicy::new(vec![PrincipalRule::new(
        "test-client",
        vec![pin],
        vec![DeviceKey::virtual_profile(VirtualProfile::Hid)],
    )?])?;
    let roots = || TrustRoots::new(vec![root.clone()]);
    if framed {
        Ok((
            TlsServer::new_with_link(roots()?, server_identity, policy, ProbePolicy::default())?,
            TlsClient::new_with_link(roots()?, client_identity, ProbePolicy::default())?,
        ))
    } else {
        Ok((
            TlsServer::new(roots()?, server_identity, policy)?,
            TlsClient::new(roots()?, client_identity)?,
        ))
    }
}

pub(super) fn loaded(tls: TlsClient, address: SocketAddr) -> Result<LoadedClientConfig> {
    Ok(LoadedClientConfig {
        broker_uid: 2000,
        server_id: "test-server".into(),
        endpoint: Peer {
            host: Host::new(address.ip().to_string())?,
            port: NonZeroU16::new(address.port())
                .ok_or_else(|| Error::State("test listener has zero port".into()))?,
        },
        server_name: "server.test".into(),
        device: DeviceKey::virtual_profile(VirtualProfile::Hid),
        tls,
    })
}

pub(super) fn foreground_context(
    path: PathBuf,
) -> (RunContext, watch::Sender<bool>, foreground::Frontend) {
    let (stop, receiver) = watch::channel(false);
    let (frontend, connection) = foreground::channel();
    (
        RunContext::foreground(ForegroundOptions {
            helper_socket: path,
            stop: receiver,
            connection,
        }),
        stop,
        frontend,
    )
}

pub(super) fn device() -> Result<RemoteDevice> {
    Ok(RemoteDevice {
        bus_id: BusId::new("1-1")?,
        path: "/test/management-only".into(),
        bus_number: 1,
        device_number: 2,
        speed: 2,
        vendor_id: 0x1234,
        product_id: 1,
        device_version: 0x100,
        class: 0,
        subclass: 0,
        protocol: 0,
        configuration: 1,
        num_configurations: 1,
        interfaces: vec![RemoteInterface {
            class: 3,
            subclass: 1,
            protocol: 1,
        }],
    })
}

#[cfg(feature = "rust-backend")]
#[tokio::test]
async fn foreground_security_observes_real_listener_overload_cancel_and_job_restart() -> TestResult
{
    use crate::foreground::{Control, Event};
    use rustusb_transport::LoadedServerConfig;
    use tokio::net::TcpStream;

    for framed in [false, true] {
        let (tls, _) = credentials(framed)?;
        let monitor = tls.security_monitor();
        let config = Arc::new(LoadedServerConfig {
            broker_uid: 2000,
            listen: "127.0.0.1:0".parse()?,
            devices: vec![DeviceKey::virtual_profile(VirtualProfile::Hid)],
            tls,
        });
        for restart in [false, true] {
            let (context, stop, mut frontend) = foreground_context("/unused-helper".into());
            let running = config.clone();
            let task = tokio::spawn(super::rust_server::virtual_server(running, context));
            let ready = tokio::time::timeout(Duration::from_secs(3), frontend.events.recv())
                .await?
                .ok_or("ready missing")?;
            let address = match &ready.event {
                Event::Ready { address, .. } => *address,
                _ => return Err("unexpected ready event".into()),
            };
            ready.complete(Ok(()));
            let mut peers = Vec::new();
            if !restart {
                for _ in 0..16 {
                    peers.push(TcpStream::connect(address).await?);
                }
                tokio::time::timeout(Duration::from_secs(2), async {
                    while monitor.snapshot().tls.in_flight != 16 {
                        tokio::task::yield_now().await;
                    }
                })
                .await?;
                let mut extra = TcpStream::connect(address).await?;
                assert_eq!(
                    tokio::time::timeout(Duration::from_secs(2), extra.read(&mut [0])).await??,
                    0
                );
            }
            frontend
                .controls
                .send(foreground::parse_control(
                    br#"{"op":"server_security","id":"security-1"}"#,
                )?)
                .await?;
            let delivery = tokio::time::timeout(Duration::from_secs(2), frontend.events.recv())
                .await?
                .ok_or("security reply missing")?;
            let Event::ServerSecurity { id, snapshot } = &delivery.event else {
                return Err("wrong security event".into());
            };
            assert_eq!(id.as_str(), "security-1");
            assert_eq!(snapshot.listener_connection_limit, 1);
            assert_eq!(snapshot.tls.attempts, 16);
            assert_eq!(snapshot.tls.in_flight, if restart { 0 } else { 16 });
            assert_eq!(
                snapshot.tls.cancelled,
                if restart && !framed { 16 } else { 0 }
            );
            assert_eq!(
                snapshot.tls.timed_out,
                if restart && framed { 16 } else { 0 }
            );
            let wire = delivery.event.to_json()?;
            assert_eq!(wire["event"], "server_security");
            assert!(serde_json::to_vec(&wire)?.len() < 1200);
            delivery.complete(Ok(()));
            frontend
                .controls
                .send(foreground::parse_control(
                    br#"{"op":"server_admission","id":"admission-1"}"#,
                )?)
                .await?;
            let admission = tokio::time::timeout(Duration::from_secs(2), frontend.events.recv())
                .await?
                .ok_or("admission reply missing")?;
            let Event::ServerAdmission { id, snapshot } = &admission.event else {
                return Err("wrong admission event".into());
            };
            assert_eq!(id.as_str(), "admission-1");
            assert_eq!(
                snapshot.policy,
                rustusb_transport::AdmissionPolicy::default()
            );
            assert_eq!(snapshot.admitted, 16);
            assert_eq!(snapshot.limited, 0);
            admission.complete(Ok(()));
            stop.send_replace(true);
            tokio::time::timeout(Duration::from_secs(7), task)
                .await??
                .into_result()?;
            drop(peers);
            let observed = monitor.snapshot();
            assert_eq!(observed.tls.in_flight, 0);
            // Existing raw listener aborts pre-authentication network tasks;
            // cooperative v2 joins them through their five-second TLS deadline.
            assert_eq!(observed.tls.cancelled, if framed { 0 } else { 16 });
            assert_eq!(observed.tls.timed_out, if framed { 16 } else { 0 });
            assert!(observed.consistent && !observed.saturated);
            // This new control remains separate from existing backend snapshots.
            assert!(matches!(
                foreground::parse_control(br#"{"op":"snapshot","id":"old"}"#)?,
                Control::Snapshot { .. }
            ));
        }
    }
    Ok(())
}

async fn catalog(io: &mut link::Io) -> Result<()> {
    assert!(matches!(
        secure::read_prelude(io).await?,
        secure::SecureOperation::Catalog
    ));
    assert!(matches!(
        server::read_management(io).await?,
        server::Management::Devlist
    ));
    let device = device()?;
    secure::write_catalog_mapping(
        io,
        &[secure::DeviceMapping {
            device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
            bus_id: device.bus_id.clone(),
        }],
    )
    .await?;
    io.write_all(&server::devlist_reply(&[device])?).await?;
    io.flush().await?;
    Ok(())
}

#[tokio::test]
async fn actual_client_refusal_keeps_original_failure_without_helper_attempt() -> TestResult {
    // Two independent authenticated connections: catalog, then an IMPORT that
    // returns a complete standard refusal. Both v1 and v2 keep their paths.
    tokio::time::timeout(Duration::from_secs(15), async {
        for framed in [false, true] {
            for status in [2, 4] {
                let (tls, client) = credentials(framed)?;
                let listener = TcpListener::bind("127.0.0.1:0").await?;
                let config = Arc::new(loaded(client, listener.local_addr()?)?);
                let peer = tokio::spawn(async move {
                    let (tcp, _) = listener.accept().await?;
                    let (mut connection, _) = link::accept(&tls, tcp).await?;
                    catalog(&mut connection.io).await?;
                    assert!(link::complete_management(
                        &mut connection.io,
                        &mut connection.owner,
                        std::future::pending(),
                    ).await?);
                    drop(connection);
                    let (tcp, _) = listener.accept().await?;
                    let (mut connection, _) = link::accept(&tls, tcp).await?;
                    assert!(matches!(secure::read_prelude(&mut connection.io).await?, secure::SecureOperation::Import { .. }));
                    assert!(matches!(server::read_management(&mut connection.io).await?, server::Management::Import(_)));
                    connection.io.write_all(&server::import_reply(status, None)?).await?;
                    connection.io.flush().await?;
                    assert!(link::complete_management(
                        &mut connection.io,
                        &mut connection.owner,
                        std::future::pending(),
                    ).await?);
                    Ok::<_, Error>(())
                });
                let directory = tempfile::tempdir()?;
                let helper = directory.path().join("no-helper.sock");
                let (context, _stop, _frontend) = foreground_context(helper.clone());
                let outcome = client::run(config, Duration::from_secs(5), context).await;
                assert!(matches!(outcome.operation, Err(Error::Protocol(ref message)) if message == "secure IMPORT refused or invalid reply"));
                assert!(outcome.cleanup.is_ok());
                assert!(outcome.recording.is_ok());
                assert!(outcome.final_event.is_none());
                assert!(!helper.exists());
                peer.await??;
            }
        }
        Ok::<_, Box<dyn std::error::Error + Send + Sync>>(())
    }).await??;
    Ok(())
}

#[tokio::test]
async fn discovery_stop_during_peer_fin_wait_retains_and_joins_the_driver() -> TestResult {
    tokio::time::timeout(Duration::from_secs(10), async {
        let (tls, client) = credentials(true)?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let config = loaded(client, listener.local_addr()?)?;
        let (client_fin, fin_received) = oneshot::channel();
        let (release, released) = oneshot::channel();
        let peer =
            tokio::spawn(async move {
                let (tcp, _) = listener.accept().await?;
                let (mut connection, _) = link::accept(&tls, tcp).await?;
                catalog(&mut connection.io).await?;
                let mut byte = [0];
                assert_eq!(connection.io.read(&mut byte).await?, 0);
                client_fin
                    .send(())
                    .map_err(|_| Error::State("FIN observer gone".into()))?;
                // Deliberately keep the reverse half open. No sleep or retry is used
                // to make the discovery close race pass.
                released
                    .await
                    .map_err(|_| Error::State("peer release lost".into()))?;
                let (_closed, sample) = link::finish(&mut connection.owner).await;
                // Peer I/O failure is expected after the deliberate interruption;
                // a missing actual driver completion is never accepted instead.
                assert!(sample.is_some_and(
                    |sample| sample.phase == rustusb_core::link_health::LinkPhase::Ended
                ));
                Ok::<_, Error>(())
            });
        let (stop, mut receiver) = watch::channel(false);
        let request = tokio::spawn(async move {
            discover_until(
                &config,
                tokio::time::Instant::now() + Duration::from_secs(8),
                async {
                    crate::context::stopped(&mut receiver).await;
                },
            )
            .await
        });
        fin_received.await?;
        stop.send_replace(true);
        assert!(
            tokio::time::timeout(Duration::from_secs(2), request)
                .await???
                .is_none()
        );
        release
            .send(())
            .map_err(|_| Error::State("peer release receiver gone".into()))?;
        peer.await??;
        Ok::<_, Box<dyn std::error::Error + Send + Sync>>(())
    })
    .await??;
    Ok(())
}

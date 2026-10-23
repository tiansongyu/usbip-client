//! Real mTLS through the production USB/IP actor; no forged VerifiedPeer fixture.

use super::*;
use rcgen::{
    BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
    KeyUsagePurpose,
};
use rustusb_core::{DeviceKey, VirtualProfile};
use rustusb_transport::{
    AccessPolicy, CertificatePin, ClientStream, DerIdentity, PrincipalRule, TlsClient, TrustRoots,
};
use std::{
    future::Future,
    io,
    pin::Pin,
    task::{Context, Poll},
};
use tokio::io::AsyncReadExt;

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
type PeerStream = ClientStream<TcpStream>;

struct Material {
    chain: Vec<Vec<u8>>,
    key: Vec<u8>,
}
impl Material {
    fn new(issuer: &Issuer<'_, KeyPair>, usage: ExtendedKeyUsagePurpose) -> TestResult<Self> {
        let mut params = CertificateParams::new(vec!["server.test".into()])?;
        params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
        params.extended_key_usages = vec![usage];
        let key = KeyPair::generate()?;
        let cert = params.signed_by(&key, issuer)?;
        Ok(Self {
            chain: vec![cert.der().to_vec()],
            key: key.serialize_der(),
        })
    }
    fn identity(&self) -> TestResult<DerIdentity> {
        Ok(DerIdentity::new(self.chain.clone(), self.key.clone())?)
    }
    fn pin(&self) -> TestResult<CertificatePin> {
        Ok(CertificatePin::from_der(&self.chain[0])?)
    }
}
struct Credentials {
    server: TlsServer,
    clients: Vec<TlsClient>,
    unmapped: TlsClient,
}
impl Credentials {
    fn new(rights: Vec<Vec<DeviceKey>>) -> TestResult<Self> {
        let mut ca = CertificateParams::new(Vec::<String>::new())?;
        ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        ca.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let ca_key = KeyPair::generate()?;
        let root = ca.self_signed(&ca_key)?.der().to_vec();
        let issuer = Issuer::new(ca, ca_key);
        let server_identity = Material::new(&issuer, ExtendedKeyUsagePurpose::ServerAuth)?;
        let mut clients = Vec::new();
        let mut rules = Vec::new();
        for (index, devices) in rights.into_iter().enumerate() {
            let material = Material::new(&issuer, ExtendedKeyUsagePurpose::ClientAuth)?;
            rules.push(PrincipalRule::new(
                &format!("client-{index}"),
                vec![material.pin()?],
                devices,
            )?);
            clients.push(TlsClient::new(
                TrustRoots::new(vec![root.clone()])?,
                material.identity()?,
            )?);
        }
        let unmapped = Material::new(&issuer, ExtendedKeyUsagePurpose::ClientAuth)?;
        Ok(Self {
            server: TlsServer::new(
                TrustRoots::new(vec![root.clone()])?,
                server_identity.identity()?,
                AccessPolicy::new(rules)?,
            )?,
            clients,
            unmapped: TlsClient::new(TrustRoots::new(vec![root])?, unmapped.identity()?)?,
        })
    }
}
fn hid() -> DeviceKey {
    DeviceKey::virtual_profile(VirtualProfile::Hid)
}
fn cdc() -> DeviceKey {
    DeviceKey::virtual_profile(VirtualProfile::CdcAcm)
}
async fn server(credentials: &Credentials) -> TestResult<(VirtualBackend, ServerHandle)> {
    let backend = VirtualBackend::new()?;
    let handle = ServerHandle::bind_tls(
        SecureServerConfig {
            listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        },
        backend.clone(),
        credentials.server.clone(),
    )
    .await?;
    Ok((backend, handle))
}
async fn connect(client: &TlsClient, address: SocketAddr) -> TestResult<PeerStream> {
    Ok(client
        .connect("server.test", TcpStream::connect(address).await?)
        .await?)
}
async fn send(peer: &mut (impl AsyncWrite + Unpin), bytes: &[u8]) -> TestResult {
    peer.write_all(bytes).await?;
    peer.flush().await?;
    Ok(())
}
fn word(bytes: &[u8], offset: usize) -> u32 {
    u32::from_be_bytes([
        bytes[offset],
        bytes[offset + 1],
        bytes[offset + 2],
        bytes[offset + 3],
    ])
}
fn import_frame(bus: &str) -> Vec<u8> {
    let mut bytes = vec![1, 0x11, 0x80, 3, 0, 0, 0, 0];
    bytes.extend_from_slice(bus.as_bytes());
    bytes.resize(40, 0);
    bytes
}
fn submit(
    seq: u32,
    devid: u32,
    direction: u32,
    endpoint: u32,
    length: u32,
    setup: [u8; 8],
) -> Vec<u8> {
    let mut bytes = vec![0; 48];
    for (offset, value) in [
        (0, 1),
        (4, seq),
        (8, devid),
        (12, direction),
        (16, endpoint),
        (24, length),
    ] {
        bytes[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
    }
    bytes[40..48].copy_from_slice(&setup);
    bytes
}
fn unlink(seq: u32, devid: u32, target: u32) -> Vec<u8> {
    let mut bytes = vec![0; 48];
    for (offset, value) in [(0, 2), (4, seq), (8, devid), (20, target)] {
        bytes[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
    }
    bytes
}
async fn catalog(
    client: &TlsClient,
    address: SocketAddr,
) -> TestResult<(Vec<DeviceMapping>, Vec<rustusb_protocol::RemoteDevice>)> {
    let mut peer = connect(client, address).await?;
    secure::write_prelude(&mut peer, &SecureOperation::Catalog).await?;
    send(&mut peer, &rustusb_protocol::DEVLIST_REQUEST).await?;
    let mapping = secure::read_catalog_mapping(&mut peer).await?;
    let devices = tokio::time::timeout(
        Duration::from_secs(2),
        rustusb_protocol::read_devlist(&mut peer),
    )
    .await??;
    assert_eq!(mapping.len(), devices.len());
    for (key, device) in mapping.iter().zip(&devices) {
        assert_eq!(key.bus_id, device.bus_id);
    }
    Ok((mapping, devices))
}
async fn import(
    client: &TlsClient,
    address: SocketAddr,
    key: DeviceKey,
    bus: &str,
) -> TestResult<(PeerStream, u32)> {
    let mut peer = connect(client, address).await?;
    secure::write_prelude(&mut peer, &SecureOperation::Import { device_key: key }).await?;
    send(&mut peer, &import_frame(bus)).await?;
    let mut header = [0; 8];
    tokio::time::timeout(Duration::from_secs(2), peer.read_exact(&mut header)).await??;
    assert_eq!(&header[..4], &[1, 0x11, 0, 3]);
    let status = word(&header, 4);
    if status == 0 {
        let mut metadata = [0; 312];
        peer.read_exact(&mut metadata).await?;
        let end = metadata[256..288]
            .iter()
            .position(|x| *x == 0)
            .ok_or("no bus terminator")?;
        assert_eq!(&metadata[256..256 + end], bus.as_bytes());
    }
    Ok((peer, status))
}
async fn reply(peer: &mut PeerStream, input: bool) -> TestResult<(Vec<u8>, Vec<u8>)> {
    tokio::time::timeout(Duration::from_secs(2), async {
        let mut header = vec![0; 48];
        peer.read_exact(&mut header).await?;
        let mut data = vec![0; if input { word(&header, 24) as usize } else { 0 }];
        peer.read_exact(&mut data).await?;
        Ok::<_, io::Error>((header, data))
    })
    .await?
    .map_err(Into::into)
}
async fn configure(peer: &mut PeerStream, devid: u32) -> TestResult {
    send(peer, &submit(1, devid, 0, 0, 0, [0, 9, 1, 0, 0, 0, 0, 0])).await?;
    let (header, _) = reply(peer, false).await?;
    assert_eq!(word(&header, 20), 0);
    Ok(())
}
async fn released(backend: &VirtualBackend) -> TestResult {
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let snapshot = backend.snapshot()?;
            if snapshot
                .devices
                .iter()
                .all(|x| !x.leased && x.pending_urbs == 0)
            {
                return Ok::<_, rustusb_core::Error>(());
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    })
    .await??;
    Ok(())
}
async fn closed(peer: &mut (impl AsyncRead + Unpin)) -> TestResult {
    let mut byte = [0];
    let result = tokio::time::timeout(Duration::from_secs(2), peer.read(&mut byte)).await?;
    assert!(
        matches!(result, Ok(0))
            || matches!(result,Err(ref error) if matches!(error.kind(), io::ErrorKind::UnexpectedEof | io::ErrorKind::ConnectionReset | io::ErrorKind::BrokenPipe | io::ErrorKind::ConnectionAborted)),
        "unexpected close result: {result:?}"
    );
    Ok(())
}

#[tokio::test]
async fn real_tls_catalog_import_control_hid_and_cdc_share_the_authenticated_engine() -> TestResult
{
    let credentials = Credentials::new(vec![vec![hid()], vec![cdc()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    for (client, key, bus) in [
        (&credentials.clients[0], hid(), "1-1"),
        (&credentials.clients[1], cdc(), "1-2"),
    ] {
        let (mapping, devices) = catalog(client, address).await?;
        assert_eq!(mapping.len(), 1);
        assert_eq!(mapping[0].device_key, key);
        assert_eq!(devices[0].bus_id.as_str(), bus);
    }
    let (mut keyboard, status) = import(&credentials.clients[0], address, hid(), "1-1").await?;
    assert_eq!(status, 0);
    configure(&mut keyboard, 0x10001).await?;
    send(
        &mut keyboard,
        &submit(2, 0x10001, 1, 0, 18, [0x80, 6, 0, 1, 0, 0, 18, 0]),
    )
    .await?;
    let (header, descriptor) = reply(&mut keyboard, true).await?;
    assert_eq!(word(&header, 20), 0);
    assert_eq!(descriptor.len(), 18);
    for seq in [3, 4] {
        send(&mut keyboard, &submit(seq, 0x10001, 1, 1, 8, [0; 8])).await?;
    }
    backend.hid_key(4)?;
    let (_, down) = reply(&mut keyboard, true).await?;
    let (_, up) = reply(&mut keyboard, true).await?;
    assert_eq!(down, vec![0, 0, 4, 0, 0, 0, 0, 0]);
    assert_eq!(up, vec![0; 8]);
    let (mut serial, status) = import(&credentials.clients[1], address, cdc(), "1-2").await?;
    assert_eq!(status, 0);
    configure(&mut serial, 0x10002).await?;
    send(&mut serial, &submit(10, 0x10002, 1, 2, 4, [0; 8])).await?;
    let mut output = submit(11, 0x10002, 0, 2, 4, [0; 8]);
    output.extend_from_slice(b"tls!");
    send(&mut serial, &output).await?;
    let (out, _) = reply(&mut serial, false).await?;
    let (input, data) = reply(&mut serial, true).await?;
    assert_eq!((word(&out, 4), word(&out, 24)), (11, 4));
    assert_eq!((word(&input, 4), data), (10, b"tls!".to_vec()));
    drop(keyboard);
    drop(serial);
    released(&backend).await?;
    handle.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn denied_direct_import_does_not_reveal_busy_or_change_the_existing_owner() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid()], vec![cdc()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    assert_eq!(
        import(&credentials.clients[0], address, cdc(), "1-2")
            .await?
            .1,
        4
    );
    assert!(
        backend
            .snapshot()?
            .devices
            .iter()
            .all(|x| x.counters.sessions_started == 0)
    );
    let (mut owner, status) = import(&credentials.clients[1], address, cdc(), "1-2").await?;
    assert_eq!(status, 0);
    configure(&mut owner, 0x10002).await?;
    assert_eq!(
        import(&credentials.clients[0], address, cdc(), "1-2")
            .await?
            .1,
        4
    );
    assert_eq!(
        import(&credentials.clients[0], address, hid(), "9-9")
            .await?
            .1,
        4
    );
    assert_eq!(
        import(&credentials.clients[1], address, cdc(), "1-2")
            .await?
            .1,
        2
    );
    assert_eq!(backend.snapshot()?.devices[1].counters.sessions_started, 1);
    send(
        &mut owner,
        &submit(2, 0x10002, 1, 0, 18, [0x80, 6, 0, 1, 0, 0, 18, 0]),
    )
    .await?;
    assert_eq!(reply(&mut owner, true).await?.1.len(), 18);
    drop(owner);
    released(&backend).await?;
    assert_eq!(
        import(&credentials.clients[0], address, cdc(), "1-2")
            .await?
            .1,
        4
    );
    handle.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn selected_key_must_match_bus_even_when_principal_allows_both_devices() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid(), cdc()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    let (mapping, _) = catalog(&credentials.clients[0], address).await?;
    assert_eq!(mapping.len(), 2);
    for (key, bus) in [(hid(), "1-2"), (cdc(), "1-1")] {
        assert_eq!(
            import(&credentials.clients[0], address, key, bus).await?.1,
            4
        );
    }
    assert!(
        backend
            .snapshot()?
            .devices
            .iter()
            .all(|x| !x.leased && x.counters.sessions_started == 0)
    );
    let (owner, status) = import(
        &credentials.clients[0],
        address,
        mapping[0].device_key.clone(),
        mapping[0].bus_id.as_str(),
    )
    .await?;
    assert_eq!(status, 0);
    drop(owner);
    released(&backend).await?;
    handle.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn backend_qualified_keys_do_not_authorize_virtual_bus_ids() -> TestResult {
    let fingerprint = format!("usb-{}", "a".repeat(64));
    let credentials = Credentials::new(vec![vec![
        DeviceKey::physical(&fingerprint)?,
        DeviceKey::native(&fingerprint)?,
    ]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    let (mapping, devices) = catalog(&credentials.clients[0], address).await?;
    assert!(mapping.is_empty() && devices.is_empty());
    assert_eq!(
        import(
            &credentials.clients[0],
            address,
            DeviceKey::physical(&fingerprint)?,
            "1-1"
        )
        .await?
        .1,
        4
    );
    assert_eq!(
        import(&credentials.clients[0], address, hid(), "1-1")
            .await?
            .1,
        4
    );
    assert!(
        backend
            .snapshot()?
            .devices
            .iter()
            .all(|x| x.counters.sessions_started == 0)
    );
    handle.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn mismatched_missing_or_malformed_secure_preludes_never_claim() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid(), cdc()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    for (operation, frame) in [
        (SecureOperation::Catalog, import_frame("1-1")),
        (
            SecureOperation::Import { device_key: hid() },
            rustusb_protocol::DEVLIST_REQUEST.to_vec(),
        ),
    ] {
        let mut peer = connect(&credentials.clients[0], address).await?;
        secure::write_prelude(&mut peer, &operation).await?;
        send(&mut peer, &frame).await?;
        closed(&mut peer).await?;
    }
    let malformed = [
        b"{\"schema_version\":1,\"op\":\"catalog\",\"extra\":1}".to_vec(),
        b"{\"schema_version\":1,\"op\":\"catalog\",\"op\":\"catalog\"}".to_vec(),
    ];
    for json in malformed {
        let mut peer = connect(&credentials.clients[0], address).await?;
        let mut frame = (json.len() as u32).to_be_bytes().to_vec();
        frame.extend(json);
        send(&mut peer, &frame).await?;
        closed(&mut peer).await?;
    }
    for frame in [
        4097_u32.to_be_bytes().to_vec(),
        0_u32.to_be_bytes().to_vec(),
        rustusb_protocol::DEVLIST_REQUEST.to_vec(),
    ] {
        let mut peer = connect(&credentials.clients[0], address).await?;
        send(&mut peer, &frame).await?;
        closed(&mut peer).await?;
    }
    assert!(
        backend
            .snapshot()?
            .devices
            .iter()
            .all(|x| x.counters.sessions_started == 0)
    );
    handle.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn plaintext_and_unmapped_certificate_cannot_reach_the_usbip_engine() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    let mut plain = TcpStream::connect(address).await?;
    send(&mut plain, &rustusb_protocol::DEVLIST_REQUEST).await?;
    // TLS may return an alert. It must never return a successful USB/IP header.
    let mut bytes = [0; 8];
    let read = tokio::time::timeout(Duration::from_secs(2), plain.read(&mut bytes)).await?;
    if let Ok(count) = read {
        assert!(count < 4 || bytes[..4] != [1, 0x11, 0, 5]);
    }
    if let Ok(mut unrecognized) = connect(&credentials.unmapped, address).await {
        // Client handshake completion alone is not server application acceptance.
        let _ = secure::write_prelude(&mut unrecognized, &SecureOperation::Catalog).await;
        assert!(
            tokio::time::timeout(
                Duration::from_secs(2),
                secure::read_catalog_mapping(&mut unrecognized)
            )
            .await?
            .is_err()
        );
    }
    assert!(
        backend
            .snapshot()?
            .devices
            .iter()
            .all(|x| x.counters.sessions_started == 0)
    );
    assert_eq!(catalog(&credentials.clients[0], address).await?.0.len(), 1);
    handle.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn tls_fragmentation_unlink_eof_and_third_import_keep_actor_cleanup() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid(), cdc()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    let (mut serial, status) = import(&credentials.clients[0], address, cdc(), "1-2").await?;
    assert_eq!(status, 0);
    configure(&mut serial, 0x10002).await?;
    send(&mut serial, &submit(10, 0x10002, 1, 2, 4, [0; 8])).await?;
    let mut frame = submit(11, 0x10002, 0, 2, 4, [0; 8]);
    frame.extend_from_slice(b"abcd");
    send(&mut serial, &frame[..13]).await?;
    tokio::time::sleep(Duration::from_millis(35)).await;
    send(&mut serial, &frame[13..]).await?;
    assert_eq!(word(&reply(&mut serial, false).await?.0, 4), 11);
    assert_eq!(reply(&mut serial, true).await?.1, b"abcd".to_vec());
    send(&mut serial, &submit(12, 0x10002, 1, 2, 4, [0; 8])).await?;
    send(&mut serial, &unlink(13, 0x10002, 12)).await?;
    let (header, _) = reply(&mut serial, false).await?;
    assert_eq!(
        (word(&header, 0), word(&header, 4), word(&header, 20) as i32),
        (4, 13, -104)
    );
    // Partial authenticated URB EOF cannot reset/reuse its parser in another connection.
    send(&mut serial, &submit(14, 0x10002, 1, 2, 4, [0; 8])[..29]).await?;
    drop(serial);
    released(&backend).await?;
    for _ in 0..2 {
        let (mut next, status) = import(&credentials.clients[0], address, cdc(), "1-2").await?;
        assert_eq!(status, 0);
        configure(&mut next, 0x10002).await?;
        send(
            &mut next,
            &submit(2, 0x10002, 1, 0, 18, [0x80, 6, 0, 1, 0, 0, 18, 0]),
        )
        .await?;
        assert_eq!(reply(&mut next, true).await?.1.len(), 18);
        drop(next);
        released(&backend).await?;
    }
    assert_eq!(backend.snapshot()?.devices[1].counters.sessions_started, 3);
    handle.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn tls_shutdown_owns_pending_handshakes_management_and_imported_reader() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid(), cdc()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    let mut raw = TcpStream::connect(address).await?;
    let mut management = connect(&credentials.clients[0], address).await?;
    let (mut imported, status) = import(&credentials.clients[0], address, hid(), "1-1").await?;
    assert_eq!(status, 0);
    configure(&mut imported, 0x10001).await?;
    send(&mut imported, &submit(2, 0x10001, 1, 1, 8, [0; 8])).await?;
    tokio::time::timeout(Duration::from_secs(2), handle.shutdown()).await??;
    released(&backend).await?;
    closed(&mut raw).await?;
    closed(&mut management).await?;
    closed(&mut imported).await?;
    // Reuse the same TLS budget after all connection tasks were joined.
    let next = ServerHandle::bind_tls(
        SecureServerConfig {
            listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        },
        backend.clone(),
        credentials.server.clone(),
    )
    .await?;
    let (owner, status) = import(&credentials.clients[0], next.local_addr(), hid(), "1-1").await?;
    assert_eq!(status, 0);
    drop(owner);
    released(&backend).await?;
    next.shutdown().await?;
    Ok(())
}

#[tokio::test]
async fn authenticated_idle_and_imported_streams_keep_the_connection_budget_until_close()
-> TestResult {
    let credentials = Credentials::new(vec![vec![hid(), cdc()]])?;
    let (backend, handle) = server(&credentials).await?;
    let address = handle.local_addr();
    let (owner, status) = import(&credentials.clients[0], address, hid(), "1-1").await?;
    assert_eq!(status, 0);
    let mut peers = Vec::new();
    for _ in 1..MAX_CONNECTIONS {
        peers.push(connect(&credentials.clients[0], address).await?);
    }
    if let Ok(mut extra) = connect(&credentials.clients[0], address).await {
        let _ = secure::write_prelude(&mut extra, &SecureOperation::Catalog).await;
        assert!(
            tokio::time::timeout(
                Duration::from_secs(2),
                secure::read_catalog_mapping(&mut extra)
            )
            .await?
            .is_err()
        );
    }
    assert_eq!(backend.snapshot()?.devices[0].counters.sessions_started, 1);
    drop(peers.pop());
    let mut available = tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            if let Ok(peer) = connect(&credentials.clients[0], address).await {
                return peer;
            }
            tokio::time::sleep(Duration::from_millis(2)).await;
        }
    })
    .await?;
    secure::write_prelude(&mut available, &SecureOperation::Catalog).await?;
    send(&mut available, &rustusb_protocol::DEVLIST_REQUEST).await?;
    assert_eq!(
        secure::read_catalog_mapping(&mut available).await?[0].device_key,
        cdc()
    );
    drop(available);
    drop(peers);
    drop(owner);
    released(&backend).await?;
    handle.shutdown().await?;
    Ok(())
}

struct DelayedFlush {
    write_ready: Pin<Box<tokio::time::Sleep>>,
    flush_ready: Option<Pin<Box<tokio::time::Sleep>>>,
    written: bool,
    flushed: bool,
}
impl AsyncWrite for DelayedFlush {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        std::task::ready!(this.write_ready.as_mut().poll(cx));
        this.written = true;
        Poll::Ready(Ok(bytes.len()))
    }
    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        let timer = this
            .flush_ready
            .get_or_insert_with(|| Box::pin(tokio::time::sleep(Duration::from_secs(3))));
        std::task::ready!(timer.as_mut().poll(cx));
        this.flushed = true;
        Poll::Ready(Ok(()))
    }
    fn poll_shutdown(self: Pin<&mut Self>, _cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Poll::Ready(Ok(()))
    }
}
#[tokio::test(start_paused = true)]
async fn response_write_and_flush_share_one_deadline() {
    let start = tokio::time::Instant::now();
    let mut writer = DelayedFlush {
        write_ready: Box::pin(tokio::time::sleep(Duration::from_secs(3))),
        flush_ready: None,
        written: false,
        flushed: false,
    };
    assert!(matches!(
        write_frame(&mut writer, b"frame").await,
        Err(Error::Timeout(_))
    ));
    assert!(writer.written && !writer.flushed);
    assert_eq!(start.elapsed(), wire::FRAME_DEADLINE);
}

#[tokio::test]
async fn production_secure_client_codecs_preserve_the_imported_tls_stream() -> TestResult {
    let credentials = Credentials::new(vec![vec![hid()]])?;
    let (backend, handle) = server(&credentials).await?;
    let catalog =
        secure::discover(connect(&credentials.clients[0], handle.local_addr()).await?).await?;
    assert_eq!(catalog.len(), 1);
    assert_eq!(catalog[0].device_key, hid());
    let (mut peer, remote) = secure::import_device(
        connect(&credentials.clients[0], handle.local_addr()).await?,
        &catalog[0],
    )
    .await?;
    assert_eq!(remote.bus_id.as_str(), "1-1");
    configure(&mut peer, 0x10001).await?;
    send(
        &mut peer,
        &submit(2, 0x10001, 1, 0, 18, [0x80, 6, 0, 1, 0, 0, 18, 0]),
    )
    .await?;
    assert_eq!(reply(&mut peer, true).await?.1.len(), 18);
    drop(peer);
    released(&backend).await?;
    handle.shutdown().await?;
    Ok(())
}

#[path = "server_managed_tests.rs"]
mod managed_tests;

// Candidate append to rustusb-rust-backend/src/server_tls_tests.rs.
// Actual public engine + loopbackTCP + memory-DER mTLS + production discover
// codec. No Native helper/USB or trusted-file-loader claim. No sleep/retry.
fn framed_catalog_credentials() -> TestResult<(TlsServer, TlsClient)> {
    let mut ca = CertificateParams::new(Vec::<String>::new())?;
    ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
    ca.key_usages = vec![KeyUsagePurpose::KeyCertSign];
    let ca_key = KeyPair::generate()?;
    let root = ca.self_signed(&ca_key)?.der().to_vec();
    let issuer = Issuer::new(ca, ca_key);
    let server = Material::new(&issuer, ExtendedKeyUsagePurpose::ServerAuth)?;
    let client = Material::new(&issuer, ExtendedKeyUsagePurpose::ClientAuth)?;
    let policy = AccessPolicy::new(vec![PrincipalRule::new(
        "short-catalog",
        vec![client.pin()?],
        vec![hid(), cdc()],
    )?])?;
    let probes =
        rustusb_transport::ProbePolicy::new(Duration::from_secs(1), Duration::from_secs(3))?;
    Ok((
        TlsServer::new_with_link(
            TrustRoots::new(vec![root.clone()])?,
            server.identity()?,
            policy,
            probes,
        )?,
        TlsClient::new_with_link(TrustRoots::new(vec![root])?, client.identity()?, probes)?,
    ))
}

#[tokio::test]
async fn framed_mtls_catalog_completes_full_reply_and_both_half_closes_without_claims() -> TestResult
{
    tokio::time::timeout(Duration::from_secs(10), async {
        let (tls, client) = framed_catalog_credentials()?;
        let backend = VirtualBackend::new()?;
        let server = ServerHandle::bind_tls(
            SecureServerConfig {
                listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
            },
            backend.clone(),
            tls,
        )
        .await?;
        let mut link = client
            .connect_link(
                "server.test",
                TcpStream::connect(server.local_addr()).await?,
            )
            .await?;
        let query = async {
            // Borrow IO so the full response and explicit FIN/EOF lifecycle
            // complete before its Drop requests a cooperative stop.
            let catalog = secure::discover(&mut link.io).await?;
            assert_eq!(catalog.len(), 2);
            assert_eq!(
                catalog
                    .iter()
                    .map(|d| d.device_key.clone())
                    .collect::<std::collections::BTreeSet<_>>(),
                [hid(), cdc()].into_iter().collect()
            );
            link.io.shutdown().await?;
            let mut extra = [0; 1];
            assert_eq!(
                link.io.read(&mut extra).await?,
                0,
                "short Catalog must end cleanly without extra application bytes"
            );
            Ok::<(), Box<dyn std::error::Error + Send + Sync>>(())
        }
        .await;
        if query.is_err() {
            link.owner.request_stop();
        }
        let completed = link.owner.join().await;
        drop(link.io);
        let shutdown = server.shutdown().await;
        // Retain separate actual outcomes before propagating an earlier failure.
        query?;
        assert_eq!(
            completed?.reason,
            rustusb_core::link_health::LinkEnd::PeerEof
        );
        shutdown?;
        assert!(
            backend
                .snapshot()?
                .devices
                .iter()
                .all(|d| d.counters.sessions_started == 0 && !d.leased && d.pending_urbs == 0)
        );
        Ok(())
    })
    .await?
}

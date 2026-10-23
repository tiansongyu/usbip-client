use super::*;
use crate::{DeviceKey, PrincipalRule, VirtualProfile};
use rcgen::{
    BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
    KeyUsagePurpose, date_time_ymd,
};
use std::{
    future::{Future, poll_fn},
    task::Poll,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt, DuplexStream, duplex};

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

struct Authority {
    root: Vec<u8>,
    issuer: Issuer<'static, KeyPair>,
}

impl Authority {
    fn new() -> TestResult<Self> {
        let mut params = CertificateParams::new(Vec::<String>::new())?;
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let key = KeyPair::generate()?;
        let root = params.self_signed(&key)?.der().to_vec();
        Ok(Self {
            root,
            issuer: Issuer::new(params, key),
        })
    }

    fn material(&self, usage: ExtendedKeyUsagePurpose, expired: bool) -> TestResult<Material> {
        let mut params = CertificateParams::new(vec!["server.test".into()])?;
        params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
        params.extended_key_usages = vec![usage];
        if expired {
            params.not_before = date_time_ymd(2000, 1, 1);
            params.not_after = date_time_ymd(2001, 1, 1);
        }
        let key = KeyPair::generate()?;
        let cert = params.signed_by(&key, &self.issuer)?;
        Ok(Material {
            chain: vec![cert.der().to_vec()],
            key: key.serialize_der(),
        })
    }

    fn roots(&self) -> Result<TrustRoots> {
        TrustRoots::new(vec![self.root.clone()])
    }
}

struct Material {
    chain: Vec<Vec<u8>>,
    key: Vec<u8>,
}

impl Material {
    fn identity(&self) -> Result<DerIdentity> {
        DerIdentity::new(self.chain.clone(), self.key.clone())
    }

    fn pin(&self) -> Result<CertificatePin> {
        let leaf = self.chain.first().ok_or(Error::InvalidCredentials)?;
        CertificatePin::from_der(leaf)
    }
}

struct Fixture {
    ca: Authority,
    server: Material,
    client: Material,
}

impl Fixture {
    fn new() -> TestResult<Self> {
        let ca = Authority::new()?;
        let server = ca.material(ExtendedKeyUsagePurpose::ServerAuth, false)?;
        let client = ca.material(ExtendedKeyUsagePurpose::ClientAuth, false)?;
        Ok(Self { ca, server, client })
    }

    fn policy(&self) -> Result<AccessPolicy> {
        AccessPolicy::new(vec![PrincipalRule::new(
            "lab-a",
            vec![self.client.pin()?],
            vec![hid()],
        )?])
    }

    fn endpoints(&self) -> TestResult<(TlsServer, TlsClient)> {
        Ok((
            TlsServer::new(self.ca.roots()?, self.server.identity()?, self.policy()?)?,
            TlsClient::new(self.ca.roots()?, self.client.identity()?)?,
        ))
    }
}

fn hid() -> DeviceKey {
    DeviceKey::virtual_profile(VirtualProfile::Hid)
}

async fn connected(
    server: &TlsServer,
    client: &TlsClient,
) -> TestResult<(
    AuthenticatedStream<DuplexStream>,
    ClientStream<DuplexStream>,
)> {
    let (left, right) = duplex(2048);
    let (server, client) = tokio::time::timeout(Duration::from_secs(3), async {
        tokio::join!(server.accept(left), client.connect("server.test", right))
    })
    .await?;
    Ok((server?, client?))
}

#[tokio::test]
async fn real_mutual_tls_maps_leaf_and_transfers_flushed_data_both_ways() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = fixture.endpoints()?;
    let (mut incoming, mut outgoing) = connected(&server, &client).await?;
    assert_eq!(incoming.peer().principal(), "lab-a");
    assert_eq!(incoming.peer().certificate_pin(), fixture.client.pin()?);
    assert_eq!(outgoing.server_certificate_pin(), fixture.server.pin()?);
    assert!(incoming.peer().allows(&hid()));
    assert!(
        !incoming
            .peer()
            .allows(&DeviceKey::virtual_profile(VirtualProfile::CdcAcm))
    );
    assert_eq!(incoming.peer().allowed_devices(), &[hid()]);
    assert_eq!(
        incoming.inner.get_ref().1.protocol_version(),
        Some(rustls::ProtocolVersion::TLSv1_3)
    );
    assert_eq!(outgoing.inner.get_ref().1.alpn_protocol(), Some(ALPN));
    assert_eq!(
        incoming.inner.get_ref().1.handshake_kind(),
        Some(rustls::HandshakeKind::Full)
    );
    // More than the duplex capacity, so progress requires real TLS backpressure
    // and concurrent reads rather than buffering an entire plaintext exchange.
    let payload = vec![0x5a; 24 * 1024];
    tokio::time::timeout(Duration::from_secs(3), async {
        let send = async {
            outgoing.write_all(&payload).await?;
            outgoing.flush().await?;
            let mut response = [0_u8; 12];
            outgoing.read_exact(&mut response).await?;
            assert_eq!(&response, b"server reply");
            io::Result::Ok(())
        };
        let receive = async {
            let mut observed = vec![0; payload.len()];
            incoming.read_exact(&mut observed).await?;
            assert_eq!(observed, payload);
            incoming.write_all(b"server reply").await?;
            incoming.flush().await?;
            io::Result::Ok(())
        };
        tokio::try_join!(send, receive)?;
        io::Result::Ok(())
    })
    .await??;
    Ok(())
}

#[tokio::test]
async fn wrong_server_name_fails_standard_verification() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = fixture.endpoints()?;
    let (left, right) = duplex(2048);
    let (accepted, connected) =
        tokio::join!(server.accept(left), client.connect("wrong.test", right));
    assert!(accepted.is_err());
    assert!(matches!(connected, Err(Error::HandshakeFailed)));
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn unknown_server_ca_is_rejected() -> TestResult {
    let fixture = Fixture::new()?;
    let other = Authority::new()?;
    let (server, _) = fixture.endpoints()?;
    let client = TlsClient::new(other.roots()?, fixture.client.identity()?)?;
    let (left, right) = duplex(2048);
    let (accepted, connected) =
        tokio::join!(server.accept(left), client.connect("server.test", right));
    assert!(accepted.is_err());
    assert!(matches!(connected, Err(Error::HandshakeFailed)));
    Ok(())
}

#[tokio::test]
async fn pinned_client_from_unknown_ca_is_still_rejected() -> TestResult {
    let fixture = Fixture::new()?;
    let other = Authority::new()?;
    let outsider = other.material(ExtendedKeyUsagePurpose::ClientAuth, false)?;
    let policy = AccessPolicy::new(vec![PrincipalRule::new(
        "pinned-outsider",
        vec![outsider.pin()?],
        vec![hid()],
    )?])?;
    let server = TlsServer::new(fixture.ca.roots()?, fixture.server.identity()?, policy)?;
    let client = TlsClient::new(fixture.ca.roots()?, outsider.identity()?)?;
    let (left, right) = duplex(2048);
    let (accepted, _) = tokio::join!(server.accept(left), client.connect("server.test", right));
    assert!(matches!(accepted, Err(Error::HandshakeFailed)));
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn absent_client_certificate_cannot_produce_authenticated_stream() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, _) = fixture.endpoints()?;
    let mut config =
        ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
            .with_protocol_versions(&[&rustls::version::TLS13])?
            .with_root_certificates(fixture.ca.roots()?.0)
            .with_no_client_auth();
    config.alpn_protocols = vec![ALPN.to_vec()];
    let connector = TlsConnector::from(Arc::new(config));
    let (left, right) = duplex(2048);
    let (accepted, _) = tokio::join!(
        server.accept(left),
        connector.connect(ServerName::try_from("server.test")?, right)
    );
    assert!(matches!(accepted, Err(Error::HandshakeFailed)));
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn expired_pinned_client_and_wrong_client_usage_fail_pki() -> TestResult {
    let fixture = Fixture::new()?;
    for (usage, expired) in [
        (ExtendedKeyUsagePurpose::ClientAuth, true),
        (ExtendedKeyUsagePurpose::ServerAuth, false),
    ] {
        let material = fixture.ca.material(usage, expired)?;
        let policy = AccessPolicy::new(vec![PrincipalRule::new(
            "lab-a",
            vec![material.pin()?],
            vec![hid()],
        )?])?;
        let server = TlsServer::new(fixture.ca.roots()?, fixture.server.identity()?, policy)?;
        let client = TlsClient::new(fixture.ca.roots()?, material.identity()?)?;
        let (left, right) = duplex(2048);
        let (accepted, _) = tokio::join!(server.accept(left), client.connect("server.test", right));
        assert!(matches!(accepted, Err(Error::HandshakeFailed)));
        assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    }
    Ok(())
}

#[tokio::test]
async fn expired_server_and_wrong_server_usage_fail_pki() -> TestResult {
    let fixture = Fixture::new()?;
    for (usage, expired) in [
        (ExtendedKeyUsagePurpose::ServerAuth, true),
        (ExtendedKeyUsagePurpose::ClientAuth, false),
    ] {
        let material = fixture.ca.material(usage, expired)?;
        let server = TlsServer::new(fixture.ca.roots()?, material.identity()?, fixture.policy()?)?;
        let client = TlsClient::new(fixture.ca.roots()?, fixture.client.identity()?)?;
        let (left, right) = duplex(2048);
        let (_, connected) =
            tokio::join!(server.accept(left), client.connect("server.test", right));
        assert!(matches!(connected, Err(Error::HandshakeFailed)));
    }
    Ok(())
}

#[tokio::test]
async fn valid_but_unmapped_or_removed_pin_is_not_authorized() -> TestResult {
    let fixture = Fixture::new()?;
    let (old_server, client) = fixture.endpoints()?;
    let (old_stream, old_client) = connected(&old_server, &client).await?;
    // A new immutable policy does not retrospectively mutate an old stream.
    let replacement = TlsServer::new(
        fixture.ca.roots()?,
        fixture.server.identity()?,
        AccessPolicy::new(vec![])?,
    )?;
    let (left, right) = duplex(2048);
    let (accepted, client_result) = tokio::join!(
        replacement.accept(left),
        client.connect("server.test", right)
    );
    assert!(matches!(accepted, Err(Error::UnmappedCertificate)));
    // TLS 1.3 client completion may precede server application authorization.
    if let Ok(mut stream) = client_result {
        let mut byte = [0];
        let result = tokio::time::timeout(Duration::from_secs(1), stream.read(&mut byte)).await?;
        assert!(matches!(result, Ok(0) | Err(_)));
    }
    assert_eq!(old_stream.peer().principal(), "lab-a");
    assert_eq!(replacement.limit.available_permits(), MAX_CONNECTIONS);
    drop((old_stream, old_client));
    Ok(())
}

#[tokio::test]
async fn missing_and_wrong_alpn_fail_closed() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = fixture.endpoints()?;
    for protocols in [vec![], vec![b"unrelated/1".to_vec()]] {
        let mut config = client.connector.config().as_ref().clone();
        config.alpn_protocols = protocols;
        let connector = TlsConnector::from(Arc::new(config));
        let (left, right) = duplex(2048);
        let (accepted, _) = tokio::join!(
            server.accept(left),
            connector.connect(ServerName::try_from("server.test")?, right)
        );
        assert!(matches!(
            accepted,
            Err(Error::ProtocolMismatch | Error::HandshakeFailed)
        ));
        assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    }
    Ok(())
}

#[tokio::test]
async fn resumption_is_disabled_on_repeated_real_handshakes() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = fixture.endpoints()?;
    let config = server.acceptor.config();
    assert_eq!(config.send_tls13_tickets, 0);
    assert_eq!(config.max_early_data_size, 0);
    assert!(!config.send_half_rtt_data);
    assert!(!config.session_storage.can_cache());
    assert!(!config.ticketer.enabled());
    assert!(!client.connector.config().enable_early_data);
    assert!(config.cert_compressors.is_empty() && config.cert_decompressors.is_empty());
    for _ in 0..2 {
        let (mut accepted, mut connected) = connected(&server, &client).await?;
        accepted.write_all(b"x").await?;
        accepted.flush().await?;
        let mut byte = [0];
        connected.read_exact(&mut byte).await?;
        assert_eq!(
            accepted.inner.get_ref().1.handshake_kind(),
            Some(rustls::HandshakeKind::Full)
        );
        assert_eq!(
            connected.inner.get_ref().1.handshake_kind(),
            Some(rustls::HandshakeKind::Full)
        );
    }
    Ok(())
}

#[tokio::test]
async fn authenticated_streams_hold_all_sixteen_permits_across_server_clones() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, client) = fixture.endpoints()?;
    let clone = server.clone();
    let mut streams = Vec::new();
    for _ in 0..MAX_CONNECTIONS {
        streams.push(connected(&clone, &client).await?);
    }
    assert_eq!(server.limit.available_permits(), 0);
    let (left, _right) = duplex(64);
    assert!(matches!(
        server.accept(left).await,
        Err(Error::ConnectionLimit)
    ));
    drop(streams.pop());
    assert_eq!(server.limit.available_permits(), 1);
    streams.push(connected(&server, &client).await?);
    drop(streams);
    assert_eq!(clone.limit.available_permits(), MAX_CONNECTIONS);
    Ok(())
}

#[tokio::test]
async fn cancelled_handshake_releases_its_already_acquired_permit() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, _) = fixture.endpoints()?;
    let (left, mut right) = duplex(64);
    let mut future = Box::pin(server.accept(left));
    poll_fn(|cx| {
        assert!(future.as_mut().poll(cx).is_pending());
        Poll::Ready(())
    })
    .await;
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS - 1);
    drop(future);
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    let mut byte = [0];
    assert_eq!(right.read(&mut byte).await?, 0);
    Ok(())
}

#[tokio::test]
async fn handshake_timeout_closes_transport_and_returns_permit() -> TestResult {
    let fixture = Fixture::new()?;
    let (server, _) = fixture.endpoints()?;
    let (left, mut right) = duplex(64);
    assert_eq!(HANDSHAKE_TIMEOUT, Duration::from_secs(5));
    let result = server
        .accept_with_deadline(left, Duration::from_millis(25))
        .await;
    assert!(matches!(result, Err(Error::Timeout)));
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    let mut byte = [0];
    assert_eq!(right.read(&mut byte).await?, 0);
    Ok(())
}

#[test]
fn stable_device_keys_and_certificate_hex_are_strict() -> TestResult {
    let fingerprint = format!("usb-{}", "a".repeat(64));
    let physical = DeviceKey::physical(&fingerprint)?;
    let native = DeviceKey::native(&fingerprint)?;
    assert_ne!(physical, native);
    assert_eq!(physical.as_str(), format!("physical:{fingerprint}"));
    for bad in ["1-2", "usb-", "usb-ABC", " usb-abc", "virtual:hid"] {
        assert!(DeviceKey::physical(bad).is_err());
        assert!(DeviceKey::native(bad).is_err());
    }
    assert!(DeviceKey::physical(&format!("usb-{}", "A".repeat(64))).is_err());
    assert!(CertificatePin::from_hex(&"a".repeat(64)).is_ok());
    for bad in [
        String::new(),
        "a".repeat(63),
        "A".repeat(64),
        "g".repeat(64),
        format!(" {}", "a".repeat(64)),
    ] {
        assert!(CertificatePin::from_hex(&bad).is_err());
    }
    assert!(CertificatePin::from_der(&[]).is_err());
    assert!(CertificatePin::from_der(&vec![0; MAX_CERT_BYTES + 1]).is_err());
    Ok(())
}

#[test]
fn principal_policy_rejects_ambiguous_empty_and_excessive_fields() -> TestResult {
    let pin = CertificatePin::from_hex(&"a".repeat(64))?;
    for id in ["", "has space", "line\nbreak", "中文", &"a".repeat(65)] {
        assert!(PrincipalRule::new(id, vec![pin], vec![hid()]).is_err());
    }
    assert!(PrincipalRule::new("a", vec![], vec![hid()]).is_err());
    assert!(PrincipalRule::new("a", vec![pin, pin], vec![hid()]).is_err());
    assert!(PrincipalRule::new("a", vec![pin; 3], vec![hid()]).is_err());
    assert!(PrincipalRule::new("a", vec![pin], vec![]).is_err());
    assert!(PrincipalRule::new("a", vec![pin], vec![hid(), hid()]).is_err());
    let devices = (0..17)
        .map(|i| DeviceKey::physical(&format!("usb-{i:064x}")))
        .collect::<rustusb_core::Result<Vec<_>>>()?;
    assert!(PrincipalRule::new("a", vec![pin], devices).is_err());
    let rule = PrincipalRule::new("a", vec![pin], vec![hid()])?;
    assert!(AccessPolicy::new(vec![rule.clone(), rule]).is_err());
    assert!(
        AccessPolicy::new(vec![
            PrincipalRule::new("a", vec![pin], vec![hid()])?,
            PrincipalRule::new("b", vec![pin], vec![hid()])?,
        ])
        .is_err()
    );
    let rules = (0..65)
        .map(|i| {
            PrincipalRule::new(
                &format!("p{i}"),
                vec![CertificatePin::from_hex(&format!("{i:064x}"))?],
                vec![hid()],
            )
        })
        .collect::<Result<Vec<_>>>()?;
    assert!(AccessPolicy::new(rules).is_err());
    assert!(AccessPolicy::new(vec![])?.verified_peer(pin).is_err());
    Ok(())
}

#[test]
fn credential_input_bounds_malformed_der_and_mismatched_key_fail_closed() -> TestResult {
    let fixture = Fixture::new()?;
    assert!(TrustRoots::new(vec![]).is_err());
    assert!(TrustRoots::new(vec![fixture.ca.root.clone(); MAX_ROOTS + 1]).is_err());
    assert!(TrustRoots::new(vec![fixture.ca.root.clone(); 2]).is_err());
    assert!(TrustRoots::new(vec![vec![0; MAX_CERT_BYTES + 1]]).is_err());
    assert!(TrustRoots::new(vec![vec![0; MAX_CERT_BYTES]; 5]).is_err());
    assert!(TrustRoots::new(vec![b"malformed certificate".to_vec()]).is_err());
    assert!(DerIdentity::new(vec![], fixture.server.key.clone()).is_err());
    assert!(
        DerIdentity::new(
            vec![fixture.server.chain[0].clone(); MAX_CHAIN + 1],
            fixture.server.key.clone()
        )
        .is_err()
    );
    assert!(DerIdentity::new(fixture.server.chain.clone(), vec![]).is_err());
    assert!(DerIdentity::new(fixture.server.chain.clone(), vec![0; MAX_KEY_BYTES + 1]).is_err());
    assert!(DerIdentity::new(fixture.server.chain.clone(), b"bad private key".to_vec()).is_err());
    assert!(DerIdentity::new(fixture.server.chain.clone(), fixture.client.key.clone()).is_err());
    let mut chain = fixture.server.chain.clone();
    chain.push(b"bad intermediate".to_vec());
    assert!(DerIdentity::new(chain, fixture.server.key.clone()).is_err());
    Ok(())
}

#[tokio::test]
async fn invalid_server_name_is_rejected_before_handshake() -> TestResult {
    let fixture = Fixture::new()?;
    let (_, client) = fixture.endpoints()?;
    for name in ["", "bad name", &"a".repeat(254)] {
        let (left, _right) = duplex(64);
        assert!(matches!(
            client.connect(name, left).await,
            Err(Error::InvalidInput("server name"))
        ));
    }
    Ok(())
}

#[tokio::test]
async fn debug_and_configuration_errors_do_not_expose_credentials_or_io() -> TestResult {
    let fixture = Fixture::new()?;
    let identity = fixture.server.identity()?;
    let roots = fixture.ca.roots()?;
    assert_eq!(format!("{identity:?}"), "DerIdentity([redacted])");
    assert_eq!(format!("{roots:?}"), "TrustRoots([redacted])");
    let (server, client) = fixture.endpoints()?;
    let (accepted, connected) = connected(&server, &client).await?;
    let dump = format!("{identity:?} {roots:?} {server:?} {client:?} {accepted:?} {connected:?}");
    assert!(!dump.contains(&format!("{:?}", fixture.server.key)));
    assert!(!dump.contains(&format!("{:?}", fixture.server.chain[0])));
    assert!(!dump.contains("DuplexStream"));
    let error = DerIdentity::new(
        vec![b"SECRET-DER-MARKER".to_vec()],
        b"SECRET-KEY-MARKER".to_vec(),
    )
    .err()
    .ok_or("invalid input accepted")?;
    assert!(!format!("{error:?} {error}").contains("SECRET"));
    Ok(())
}

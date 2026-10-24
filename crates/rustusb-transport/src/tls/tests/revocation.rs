//! Actual TLS verification with signed CRLs; no USB or trusted-file claims.

use super::*;
use rcgen::{
    CertificateRevocationListParams, DistinguishedName, DnType, KeyIdMethod, RevokedCertParams,
    SerialNumber,
};
use rustls::{pki_types::UnixTime, time_provider::TimeProvider};
use std::sync::atomic::{AtomicU64, Ordering};

struct Ca {
    der: Vec<u8>,
    issuer: Issuer<'static, KeyPair>,
}

impl Ca {
    fn new(name: &str, parent: Option<&Self>) -> TestResult<Self> {
        let mut params = CertificateParams::new(Vec::<String>::new())?;
        params.distinguished_name = DistinguishedName::new();
        params.distinguished_name.push(DnType::CommonName, name);
        params.serial_number = Some(SerialNumber::from(2_u64));
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        params.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let key = KeyPair::generate()?;
        let der = match parent {
            Some(parent) => params.signed_by(&key, &parent.issuer)?,
            None => params.self_signed(&key)?,
        }
        .der()
        .to_vec();
        Ok(Self {
            der,
            issuer: Issuer::new(params, key),
        })
    }

    fn material(&self, serial: u64, usage: ExtendedKeyUsagePurpose) -> TestResult<Material> {
        let mut params = CertificateParams::new(vec!["server.test".into()])?;
        params.serial_number = Some(serial.into());
        params.extended_key_usages = vec![usage];
        params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
        let key = KeyPair::generate()?;
        let cert = params.signed_by(&key, &self.issuer)?;
        Ok(Material {
            chain: vec![cert.der().to_vec()],
            key: key.serialize_der(),
        })
    }

    fn crl(&self, serials: &[u64], from: i32, until: i32, number: u64) -> TestResult<Vec<u8>> {
        let crl = CertificateRevocationListParams {
            this_update: date_time_ymd(from, 1, 1),
            next_update: date_time_ymd(until, 1, 1),
            crl_number: number.into(),
            issuing_distribution_point: None,
            revoked_certs: serials
                .iter()
                .map(|serial| RevokedCertParams {
                    serial_number: (*serial).into(),
                    revocation_time: date_time_ymd(2020, 1, 1),
                    reason_code: None,
                    invalidity_date: None,
                })
                .collect(),
            key_identifier_method: KeyIdMethod::Sha256,
        }
        .signed_by(&self.issuer)?;
        Ok(crl.der().to_vec())
    }

    fn roots(&self, crls: Vec<Vec<u8>>) -> TestResult<TrustRoots> {
        Ok(TrustRoots::new(vec![self.der.clone()])?.with_crls(crls)?)
    }
}

struct CrlFixture {
    ca: Ca,
    server: Material,
    client: Material,
}

impl CrlFixture {
    fn new() -> TestResult<Self> {
        let ca = Ca::new("root", None)?;
        let server = ca.material(101, ExtendedKeyUsagePurpose::ServerAuth)?;
        let client = ca.material(102, ExtendedKeyUsagePurpose::ClientAuth)?;
        Ok(Self { ca, server, client })
    }

    fn policy(&self) -> Result<AccessPolicy> {
        AccessPolicy::new(vec![PrincipalRule::new(
            "lab-a",
            vec![self.client.pin()?],
            vec![hid()],
        )?])
    }

    fn endpoints(
        &self,
        server_crls: Vec<Vec<u8>>,
        client_crls: Vec<Vec<u8>>,
    ) -> TestResult<(TlsServer, TlsClient)> {
        Ok((
            TlsServer::new(
                self.ca.roots(server_crls)?,
                self.server.identity()?,
                self.policy()?,
            )?,
            TlsClient::new(self.ca.roots(client_crls)?, self.client.identity()?)?,
        ))
    }
}

async fn outcomes(server: &TlsServer, client: &TlsClient) -> TestResult<(bool, bool)> {
    let (left, right) = duplex(8192);
    let (incoming, outgoing) = tokio::time::timeout(Duration::from_secs(3), async {
        tokio::join!(server.accept(left), client.connect("server.test", right))
    })
    .await?;
    let result = (incoming.is_ok(), outgoing.is_ok());
    // A TLS1.3 client's own Finished is not the server's acceptance receipt.
    drop(incoming);
    drop(outgoing);
    assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    Ok(result)
}

async fn exchange(
    incoming: &mut AuthenticatedStream<DuplexStream>,
    outgoing: &mut ClientStream<DuplexStream>,
) -> TestResult {
    outgoing.write_all(b"request").await?;
    outgoing.flush().await?;
    let mut request = [0; 7];
    incoming.read_exact(&mut request).await?;
    assert_eq!(&request, b"request");
    incoming.write_all(b"reply").await?;
    incoming.flush().await?;
    let mut reply = [0; 5];
    outgoing.read_exact(&mut reply).await?;
    assert_eq!(&reply, b"reply");
    Ok(())
}

#[tokio::test]
async fn fresh_crls_preserve_full_handshakes_pins_and_bidirectional_io() -> TestResult {
    let f = CrlFixture::new()?;
    let crl = f.ca.crl(&[], 2020, 2040, 1)?;
    let (server, client) = f.endpoints(vec![crl.clone()], vec![crl])?;
    for _ in 0..2 {
        let (mut incoming, mut outgoing) = connected(&server, &client).await?;
        assert_eq!(incoming.peer().certificate_pin(), f.client.pin()?);
        assert!(incoming.peer().allows(&hid()));
        assert_eq!(outgoing.server_certificate_pin(), f.server.pin()?);
        assert_eq!(
            incoming.inner.get_ref().1.handshake_kind(),
            Some(rustls::HandshakeKind::Full)
        );
        assert_eq!(
            outgoing.inner.get_ref().1.handshake_kind(),
            Some(rustls::HandshakeKind::Full)
        );
        exchange(&mut incoming, &mut outgoing).await?;
    }
    assert_eq!(server.security_monitor().snapshot().tls.authenticated, 2);
    Ok(())
}

#[tokio::test]
async fn revoked_client_and_server_are_rejected_despite_correct_pins() -> TestResult {
    let f = CrlFixture::new()?;
    let clean = f.ca.crl(&[], 2020, 2040, 1)?;
    let (server, client) =
        f.endpoints(vec![f.ca.crl(&[102], 2020, 2040, 2)?], vec![clean.clone()])?;
    assert!(!outcomes(&server, &client).await?.0);
    let snapshot = server.security_monitor().snapshot();
    assert_eq!(snapshot.tls.authenticated, 0);
    assert_eq!(snapshot.tls.handshake_failed, 1);
    let (server, client) = f.endpoints(vec![clean], vec![f.ca.crl(&[101], 2020, 2040, 2)?])?;
    assert_eq!(outcomes(&server, &client).await?, (false, false));
    Ok(())
}

#[tokio::test]
async fn wrong_issuer_key_and_modified_signature_fail_actual_verification() -> TestResult {
    let f = CrlFixture::new()?;
    let clean = f.ca.crl(&[], 2020, 2040, 1)?;
    let wrong_issuer = Ca::new("other issuer", None)?.crl(&[], 2020, 2040, 1)?;
    let wrong_key = Ca::new("root", None)?.crl(&[], 2020, 2040, 1)?;
    let mut bad_signature = clean.clone();
    *bad_signature.last_mut().ok_or("missing CRL signature")? ^= 1;
    for bad in [wrong_issuer, wrong_key, bad_signature] {
        let (server, client) = f.endpoints(vec![bad.clone()], vec![clean.clone()])?;
        assert!(!outcomes(&server, &client).await?.0);
        let (server, client) = f.endpoints(vec![clean.clone()], vec![bad])?;
        assert_eq!(outcomes(&server, &client).await?, (false, false));
    }
    Ok(())
}

#[tokio::test]
async fn intermediate_chain_requires_each_issuer_and_checks_intermediate_revocation() -> TestResult
{
    let mut f = CrlFixture::new()?;
    let intermediate = Ca::new("intermediate", Some(&f.ca))?;
    f.server = intermediate.material(101, ExtendedKeyUsagePurpose::ServerAuth)?;
    f.client = intermediate.material(102, ExtendedKeyUsagePurpose::ClientAuth)?;
    f.server.chain.push(intermediate.der.clone());
    f.client.chain.push(intermediate.der.clone());
    let root = f.ca.crl(&[], 2020, 2040, 1)?;
    let leaf = intermediate.crl(&[], 2020, 2040, 1)?;
    let full = vec![root.clone(), leaf.clone()];
    let (server, client) = f.endpoints(full.clone(), full.clone())?;
    assert_eq!(outcomes(&server, &client).await?, (true, true));
    for missing in [vec![root], vec![leaf]] {
        let (server, client) = f.endpoints(missing.clone(), full.clone())?;
        assert!(!outcomes(&server, &client).await?.0);
        let (server, client) = f.endpoints(full.clone(), missing)?;
        assert_eq!(outcomes(&server, &client).await?, (false, false));
    }
    let revoked = vec![
        f.ca.crl(&[2], 2020, 2040, 2)?,
        intermediate.crl(&[], 2020, 2040, 1)?,
    ];
    let (server, client) = f.endpoints(revoked.clone(), full.clone())?;
    assert!(!outcomes(&server, &client).await?.0);
    let (server, client) = f.endpoints(full, revoked)?;
    assert_eq!(outcomes(&server, &client).await?, (false, false));
    Ok(())
}

#[test]
fn crl_metadata_rejects_bounds_trailing_der_duplicate_issuers_and_invalid_windows() -> TestResult {
    let f = CrlFixture::new()?;
    let good = f.ca.crl(&[], 2020, 2040, 1)?;
    assert!(f.ca.roots(Vec::new()).is_err());
    assert!(f.ca.roots(vec![good.clone(); 17]).is_err());
    assert!(f.ca.roots(vec![Vec::new()]).is_err());
    assert!(f.ca.roots(vec![vec![0; 64 * 1024 + 1]]).is_err());
    assert!(f.ca.roots(vec![vec![0; 64 * 1024]; 5]).is_err());
    for end in 0..good.len() {
        assert!(f.ca.roots(vec![good[..end].to_vec()]).is_err());
    }
    let mut trailing = good.clone();
    trailing.push(0);
    assert!(f.ca.roots(vec![trailing]).is_err());
    let newer = f.ca.crl(&[102], 2021, 2040, 2)?;
    for overlap in [vec![good.clone(), newer.clone()], vec![newer, good.clone()]] {
        assert!(
            matches!(f.ca.roots(overlap), Err(e) if e.to_string().contains("duplicate CRL issuer"))
        );
    }
    assert!(f.ca.roots(vec![f.ca.crl(&[], 2030, 2040, 1)?]).is_err());
    assert!(f.ca.roots(vec![f.ca.crl(&[], 2000, 2001, 1)?]).is_err());
    assert!(
        f.ca.roots(vec![good.clone()])?
            .with_crls(vec![good])
            .is_err()
    );
    Ok(())
}

#[derive(Debug)]
struct MutableClock(AtomicU64);

impl TimeProvider for MutableClock {
    fn current_time(&self) -> Option<UnixTime> {
        Some(UnixTime::since_unix_epoch(Duration::from_secs(
            self.0.load(Ordering::SeqCst),
        )))
    }
}

fn clocked(mut roots: TrustRoots, clock: Arc<MutableClock>) -> TestResult<TrustRoots> {
    roots
        .revocation
        .as_mut()
        .ok_or("missing parsed CRL")?
        .replace_clock_for_test(clock)?;
    Ok(roots)
}

#[tokio::test]
async fn actual_clock_expiry_and_rollback_block_new_peers_but_keep_original_stream() -> TestResult {
    let f = CrlFixture::new()?;
    let crl = f.ca.crl(&[], 2020, 2040, 1)?;
    let unused = Ca::new("unused issuer", None)?.crl(&[], 2021, 2035, 1)?;
    let start = u64::try_from(date_time_ymd(2021, 1, 1).unix_timestamp())?;
    let until = u64::try_from(date_time_ymd(2035, 1, 1).unix_timestamp())?;
    for guarded_server in [true, false] {
        let clock = Arc::new(MutableClock(AtomicU64::new(start)));
        let server_roots = f.ca.roots(vec![crl.clone(), unused.clone()])?;
        let client_roots = f.ca.roots(vec![crl.clone(), unused.clone()])?;
        let (server_roots, client_roots) = if guarded_server {
            (clocked(server_roots, clock.clone())?, client_roots)
        } else {
            (server_roots, clocked(client_roots, clock.clone())?)
        };
        let server = TlsServer::new(server_roots, f.server.identity()?, f.policy()?)?;
        let client = TlsClient::new(client_roots, f.client.identity()?)?;
        let (mut incoming, mut outgoing) = connected(&server, &client).await?;
        for now in [until, start - 1] {
            clock.0.store(now, Ordering::SeqCst);
            // Keep the original actual stream alive while trying another peer.
            let (left, right) = duplex(8192);
            let (new_server, new_client) = tokio::time::timeout(Duration::from_secs(3), async {
                tokio::join!(server.accept(left), client.connect("server.test", right))
            })
            .await?;
            assert!(new_server.is_err());
            if !guarded_server {
                assert!(new_client.is_err());
            }
            drop(new_client);
            exchange(&mut incoming, &mut outgoing).await?;
            assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS - 1);
        }
        clock.0.store(until - 1, Ordering::SeqCst);
        let (left, right) = duplex(8192);
        let (new_server, new_client) =
            tokio::join!(server.accept(left), client.connect("server.test", right));
        assert!(new_server.is_ok());
        assert!(new_client.is_ok());
    }
    Ok(())
}

#[tokio::test]
async fn framed_tcp_crls_preserve_real_driver_io_and_reject_revoked_peers() -> TestResult {
    let f = CrlFixture::new()?;
    let probes = ProbePolicy::new(Duration::from_secs(1), Duration::from_secs(3))?;
    for revoked in [None, Some(101), Some(102)] {
        let server_crl = f.ca.crl(
            &if revoked == Some(102) {
                vec![102]
            } else {
                vec![]
            },
            2020,
            2040,
            1,
        )?;
        let client_crl = f.ca.crl(
            &if revoked == Some(101) {
                vec![101]
            } else {
                vec![]
            },
            2020,
            2040,
            1,
        )?;
        let server = TlsServer::new_with_link(
            f.ca.roots(vec![server_crl])?,
            f.server.identity()?,
            f.policy()?,
            probes,
        )?;
        let client =
            TlsClient::new_with_link(f.ca.roots(vec![client_crl])?, f.client.identity()?, probes)?;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let (outgoing, incoming) = tokio::join!(
            tokio::net::TcpStream::connect(listener.local_addr()?),
            listener.accept()
        );
        let (incoming, outgoing) = (incoming?.0, outgoing?);
        let (incoming, outgoing) = tokio::time::timeout(Duration::from_secs(7), async {
            tokio::join!(
                server.accept_link(incoming),
                client.connect_link("server.test", outgoing)
            )
        })
        .await?;
        match revoked {
            Some(_) => {
                assert!(incoming.is_err());
                assert!(outgoing.is_err());
            }
            None => {
                let (mut incoming, mut outgoing) = (incoming?, outgoing?);
                outgoing.io.write_all(b"crl").await?;
                outgoing.io.flush().await?;
                let mut data = [0; 3];
                incoming.io.read_exact(&mut data).await?;
                assert_eq!(&data, b"crl");
                incoming.owner.request_stop();
                outgoing.owner.request_stop();
                incoming.owner.join().await?;
                outgoing.owner.join().await?;
            }
        }
        assert_eq!(server.limit.available_permits(), MAX_CONNECTIONS);
    }
    Ok(())
}

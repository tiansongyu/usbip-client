use super::*;
use rcgen::{CertificateRevocationListParams, KeyIdMethod, RevokedCertParams, date_time_ymd};

fn crl(fixture: &Fixture, revoked: bool) -> TestResult<Vec<u8>> {
    Ok(CertificateRevocationListParams {
        this_update: date_time_ymd(2020, 1, 1),
        next_update: date_time_ymd(2040, 1, 1),
        crl_number: if revoked { 2_u64 } else { 1 }.into(),
        issuing_distribution_point: None,
        revoked_certs: if revoked {
            vec![RevokedCertParams {
                serial_number: 101_u64.into(),
                revocation_time: date_time_ymd(2020, 1, 1),
                reason_code: None,
                invalidity_date: None,
            }]
        } else {
            Vec::new()
        },
        key_identifier_method: KeyIdMethod::Sha256,
    }
    .signed_by(&fixture.issuer)?
    .der()
    .to_vec())
}

fn document(f: &Fixture, server: bool, version: u32) -> Value {
    let mut value = if server { f.server() } else { f.client() };
    value["schema_version"] = json!(version);
    if version == 2 {
        value["data_link"] =
            json!({"protocol":"framed_v2","interval_millis":1000,"response_timeout_millis":3000});
    }
    value
}

fn loads(f: &Fixture, server: bool, doc: &Value) -> bool {
    match f.config(doc) {
        Ok(path) => {
            if server {
                load_server_inner(&path, &f.context).is_ok()
            } else {
                load_client_inner(&path, &f.context).is_ok()
            }
        }
        Err(_) => false,
    }
}

#[test]
fn strict_crl_configuration_is_optional_but_present_values_must_be_valid() -> TestResult {
    let f = Fixture::new()?;
    f.write("crl.der", &crl(&f, false)?, 0o644)?;
    for version in [1, 2] {
        for server in [true, false] {
            let mut doc = document(&f, server, version);
            assert!(loads(&f, server, &doc));
            for invalid in [
                Value::Null,
                json!([]),
                json!("crl.der"),
                json!(vec![f.path("crl.der"); 17]),
                json!(["relative.der"]),
            ] {
                doc["certificate_revocation_lists"] = invalid;
                assert!(!loads(&f, server, &doc));
            }
            doc["certificate_revocation_lists"] = json!([f.path("crl.der")]);
            assert!(loads(&f, server, &doc));
            let bytes = serde_json::to_vec(&doc)?;
            let mut repeated = bytes[..bytes.len() - 1].to_vec();
            repeated.extend_from_slice(b",\"certificate_revocation_lists\":[]}");
            let path = f.write("duplicate.json", &repeated, 0o644)?;
            if server {
                assert!(load_server_inner(&path, &f.context).is_err());
            } else {
                assert!(load_client_inner(&path, &f.context).is_err());
            }
        }
    }
    Ok(())
}

#[test]
fn crl_files_use_existing_public_trust_checks_and_bounded_complete_reads() -> TestResult {
    let f = Fixture::new()?;
    let der = crl(&f, false)?;
    f.write("crl.der", &der, 0o644)?;
    symlink(f.path("crl.der"), f.path("symlink.der"))?;
    fs::hard_link(f.path("crl.der"), f.path("hardlink.der"))?;
    for server in [true, false] {
        let mut doc = document(&f, server, 1);
        for name in ["symlink.der", "hardlink.der", "crl.der"] {
            doc["certificate_revocation_lists"] = json!([f.path(name)]);
            assert!(!loads(&f, server, &doc));
        }
    }
    fs::remove_file(f.path("hardlink.der"))?;
    for (bytes, mode) in [
        (der.clone(), 0o666),
        (vec![0; crate::revocation::MAX_CRL_BYTES + 1], 0o644),
        (vec![0; 10], 0o644),
    ] {
        f.write("crl.der", &bytes, mode)?;
        for server in [true, false] {
            let mut doc = document(&f, server, 1);
            doc["certificate_revocation_lists"] = json!([f.path("crl.der")]);
            assert!(!loads(&f, server, &doc));
        }
    }
    f.write("crl.der", &der, 0o644)?;
    for server in [true, false] {
        let mut doc = document(&f, server, 1);
        doc["certificate_revocation_lists"] = json!([f.path("crl.der"), f.path("crl.der")]);
        assert!(!loads(&f, server, &doc));
    }
    Ok(())
}

async fn accepted(server: &TlsServer, client: &TlsClient) -> TestResult<bool> {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
    let (outgoing, incoming) = tokio::join!(
        tokio::net::TcpStream::connect(listener.local_addr()?),
        listener.accept()
    );
    let (incoming, outgoing) = (incoming?.0, outgoing?);
    tokio::time::timeout(std::time::Duration::from_secs(7), async {
        if server.protocol() == rustusb_core::link_health::LinkProtocol::RawV1 {
            let (incoming, outgoing) = tokio::join!(
                server.accept(incoming),
                client.connect("server.test", outgoing)
            );
            Ok(incoming.is_ok() && outgoing.is_ok())
        } else {
            let (incoming, outgoing) = tokio::join!(
                server.accept_link(incoming),
                client.connect_link("server.test", outgoing)
            );
            let ok = incoming.is_ok() && outgoing.is_ok();
            if let Ok(incoming) = incoming {
                incoming.owner.request_stop();
                incoming.owner.join().await?;
            }
            if let Ok(outgoing) = outgoing {
                outgoing.owner.request_stop();
                outgoing.owner.join().await?;
            }
            Ok::<_, Box<dyn std::error::Error + Send + Sync>>(ok)
        }
    })
    .await?
}

#[tokio::test]
async fn loaded_crl_snapshot_is_immutable_and_reload_enforces_revocation_for_both_roles()
-> TestResult {
    for version in [1, 2] {
        let f = Fixture::new()?;
        f.write("crl.der", &crl(&f, false)?, 0o644)?;
        let mut server = document(&f, true, version);
        let mut client = document(&f, false, version);
        server["certificate_revocation_lists"] = json!([f.path("crl.der")]);
        client["certificate_revocation_lists"] = server["certificate_revocation_lists"].clone();
        let leaf = fs::read(f.path("leaf.der"))?;
        let pin = CertificatePin::from_der(&leaf)?
            .digest()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect::<String>();
        server["principals"][0]["pins"] = json!([pin]);
        let old_server = load_server_inner(&f.config(&server)?, &f.context)?;
        let old_client = load_client_inner(&f.config(&client)?, &f.context)?;
        assert!(accepted(&old_server.tls, &old_client.tls).await?);
        f.write("crl.der", &crl(&f, true)?, 0o644)?;
        let new_server = load_server_inner(&f.config(&server)?, &f.context)?;
        let new_client = load_client_inner(&f.config(&client)?, &f.context)?;
        assert!(!accepted(&new_server.tls, &old_client.tls).await?);
        assert!(!accepted(&old_server.tls, &new_client.tls).await?);
        assert!(accepted(&old_server.tls, &old_client.tls).await?);
    }
    Ok(())
}

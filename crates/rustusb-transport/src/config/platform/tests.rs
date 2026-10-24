use super::*;
use rcgen::{BasicConstraints, CertificateParams, IsCa, Issuer, KeyPair};
use serde_json::{Value, json};
use std::{
    fs::OpenOptions,
    io::Write,
    os::unix::fs::{PermissionsExt, symlink},
    sync::atomic::{AtomicU64, Ordering},
};

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(1);

// These are ordinary-UID filesystem fixtures. Their private Context explicitly
// substitutes a fixture administrator and trust root; a passing fixture never
// asserts that /tmp or an ordinary-owned directory is trusted in production.
mod revocation;

struct Fixture {
    issuer: Issuer<'static, KeyPair>,
    root: PathBuf,
    context: Context,
}

impl Fixture {
    fn new() -> TestResult<Self> {
        let root = std::env::temp_dir().join(format!(
            "rustusb-transport-config-{}-{}",
            std::process::id(),
            NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&root)?;
        fs::set_permissions(&root, fs::Permissions::from_mode(0o700))?;
        let owner = rustix::process::geteuid().as_raw();
        let context = Context {
            uid: if owner == 0 { 1000 } else { owner },
            administrator_uid: owner,
            fixture_root: Some(root.clone()),
        };
        let mut params = CertificateParams::new(Vec::<String>::new())?;
        params.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        let ca_key = KeyPair::generate()?;
        let ca = params.self_signed(&ca_key)?;
        let fixture = Self {
            root,
            context,
            issuer: Issuer::new(params, ca_key),
        };
        fixture.write("ca.der", ca.der().as_ref(), 0o644)?;
        let key = KeyPair::generate()?;
        let mut leaf_params = CertificateParams::new(vec!["server.test".into()])?;
        leaf_params.serial_number = Some(101_u64.into());
        let leaf = leaf_params.signed_by(&key, &fixture.issuer)?;
        fixture.write("leaf.der", leaf.der().as_ref(), 0o644)?;
        fixture.write("private.der", &key.serialize_der(), 0o600)?;
        Ok(fixture)
    }

    fn path(&self, name: &str) -> PathBuf {
        self.root.join(name)
    }

    fn write(&self, name: &str, bytes: &[u8], mode: u32) -> TestResult<PathBuf> {
        let path = self.path(name);
        fs::write(&path, bytes)?;
        fs::set_permissions(&path, fs::Permissions::from_mode(mode))?;
        Ok(path)
    }

    fn server(&self) -> Value {
        json!({
            "schema_version": 1,
            "broker_uid": self.context.uid,
            "listen": "[::1]:3241",
            "devices": ["virtual:hid"],
            "principals": [{"id":"lab-a","pins":["ab".repeat(32)],"devices":["virtual:hid"]}],
            "ca_certificates": [self.path("ca.der")],
            "certificate_chain": [self.path("leaf.der")],
            "private_key": self.path("private.der"),
        })
    }

    fn client(&self) -> Value {
        json!({
            "schema_version": 1,
            "broker_uid": self.context.uid,
            "server_id": "lab-a",
            "endpoint": {"host":"server.test","port":3241},
            "server_name": "server.test",
            "device": "virtual:hid",
            "ca_certificates": [self.path("ca.der")],
            "certificate_chain": [self.path("leaf.der")],
            "private_key": self.path("private.der"),
        })
    }

    fn config(&self, value: &Value) -> TestResult<PathBuf> {
        self.write("config.json", &serde_json::to_vec(value)?, 0o644)
    }
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

#[test]
fn valid_fixture_loads_typed_immutable_server_and_client() -> TestResult {
    let fixture = Fixture::new()?;
    let path = fixture.config(&fixture.server())?;
    let server = load_server_inner(&path, &fixture.context)?;
    assert_eq!(server.broker_uid, fixture.context.uid);
    assert_eq!(server.listen, "[::1]:3241".parse::<SocketAddr>()?);
    assert_eq!(server.devices[0].as_str(), "virtual:hid");
    let client = load_client_inner(&fixture.config(&fixture.client())?, &fixture.context)?;
    assert_eq!(client.server_id, "lab-a");
    assert_eq!(client.endpoint.host.as_str(), "server.test");
    assert_eq!(client.device.as_str(), "virtual:hid");
    let mut updated = fixture.server();
    updated["devices"] = json!(["virtual:cdc-acm"]);
    fixture.config(&updated)?;
    assert_eq!(server.devices[0].as_str(), "virtual:hid");
    Ok(())
}

#[test]
fn explicit_v2_trusted_fixture_selects_framing_and_preserves_v1() -> TestResult {
    let fixture = Fixture::new()?;
    let probes =
        json!({"protocol":"framed_v2","interval_millis":1000,"response_timeout_millis":3000});
    let mut server = fixture.server();
    server["schema_version"] = json!(2);
    server["data_link"] = probes.clone();
    assert_eq!(
        load_server_inner(&fixture.config(&server)?, &fixture.context)?
            .tls
            .protocol(),
        rustusb_core::link_health::LinkProtocol::FramedV2
    );
    let mut client = fixture.client();
    client["schema_version"] = json!(2);
    client["data_link"] = probes;
    assert_eq!(
        load_client_inner(&fixture.config(&client)?, &fixture.context)?
            .tls
            .protocol(),
        rustusb_core::link_health::LinkProtocol::FramedV2
    );
    assert_eq!(
        load_client_inner(&fixture.config(&fixture.client())?, &fixture.context)?
            .tls
            .protocol(),
        rustusb_core::link_health::LinkProtocol::RawV1
    );
    Ok(())
}

#[test]
fn trusted_server_admission_policy_is_bounded_immutable_and_server_only() -> TestResult {
    let fixture = Fixture::new()?;
    for version in [1, 2] {
        let mut document = fixture.server();
        document["schema_version"] = json!(version);
        if version == 2 {
            document["data_link"] = json!({"protocol":"framed_v2","interval_millis":1000,"response_timeout_millis":3000});
        }
        let defaults = load_server_inner(&fixture.config(&document)?, &fixture.context)?;
        assert_eq!(
            defaults.tls.admission_monitor().snapshot().policy,
            crate::AdmissionPolicy::default()
        );
        document["authentication_limit"] = json!({"burst":3,"interval_millis":1000});
        let loaded = load_server_inner(&fixture.config(&document)?, &fixture.context)?;
        assert_eq!(
            loaded.tls.admission_monitor().snapshot().policy,
            crate::AdmissionPolicy::new(3, 1000)?
        );
        for bad in [
            Value::Null,
            json!({"burst":0,"interval_millis":1}),
            json!({"burst":257,"interval_millis":1}),
            json!({"burst":1,"interval_millis":60001}),
            json!({"burst":1,"interval_millis":1,"enabled":false}),
        ] {
            document["authentication_limit"] = bad;
            assert!(load_server_inner(&fixture.config(&document)?, &fixture.context).is_err());
        }
        assert_eq!(loaded.tls.admission_monitor().snapshot().policy.burst(), 3);
        assert_eq!(
            defaults.tls.admission_monitor().snapshot().policy.burst(),
            64
        );
    }
    let mut client = fixture.client();
    client["authentication_limit"] = json!({"burst":1,"interval_millis":1});
    assert!(load_client_inner(&fixture.config(&client)?, &fixture.context).is_err());
    Ok(())
}

#[test]
fn v2_policy_and_version_shapes_reject_ambiguity_before_credentials() -> TestResult {
    let fixture = Fixture::new()?;
    let valid =
        json!({"protocol":"framed_v2","interval_millis":1000,"response_timeout_millis":3000});
    for policy in [
        json!(null),
        json!({"protocol":"raw_v1","interval_millis":1000,"response_timeout_millis":3000}),
        json!({"protocol":"framed_v2","interval_millis":999,"response_timeout_millis":3000}),
        json!({"protocol":"framed_v2","interval_millis":1000,"response_timeout_millis":1999}),
        json!({"protocol":"framed_v2","interval_millis":1000,"response_timeout_millis":300001}),
        json!({"protocol":"framed_v2","interval_millis":1000,"response_timeout_millis":3000,"fallback":true}),
    ] {
        for is_server in [false, true] {
            let mut config = if is_server {
                fixture.server()
            } else {
                fixture.client()
            };
            config["schema_version"] = json!(2);
            config["data_link"] = policy.clone();
            config["private_key"] = json!(fixture.path("missing.key"));
            let path = fixture.config(&config)?;
            let failed = if is_server {
                load_server_inner(&path, &fixture.context).err()
            } else {
                load_client_inner(&path, &fixture.context).err()
            };
            assert!(matches!(failed, Some(Error::InvalidInput(_))));
        }
    }
    // A v1 document cannot smuggle v2 policy, and v2 requires its policy.
    let mut legacy = fixture.client();
    legacy["data_link"] = valid.clone();
    assert!(client_document(&serde_json::to_vec(&legacy)?).is_err());
    let mut missing = fixture.server();
    missing["schema_version"] = json!(2);
    assert!(server_document(&serde_json::to_vec(&missing)?).is_err());
    let mut config = fixture.client();
    config["schema_version"] = json!(2);
    config["data_link"] = valid;
    let raw = serde_json::to_string(&config)?.replace(
        "\"interval_millis\":1000",
        "\"interval_millis\":1000,\"interval_millis\":1000",
    );
    assert!(client_document(raw.as_bytes()).is_err());
    Ok(())
}

#[test]
fn schema_unknown_duplicate_and_broker_mismatch_fail_before_credentials() -> TestResult {
    let fixture = Fixture::new()?;
    for (field, value) in [
        ("schema_version", json!(2)),
        ("broker_uid", json!(0)),
        ("broker_uid", json!(fixture.context.uid.saturating_add(1))),
        ("unrecognized-secret-marker", json!("PRIVATE-BYTES")),
    ] {
        let mut config = fixture.server();
        config[field] = value;
        config["private_key"] = json!(fixture.path("missing.key"));
        assert!(matches!(
            load_server_inner(&fixture.config(&config)?, &fixture.context),
            Err(Error::InvalidInput(_))
        ));
    }
    let mut raw = serde_json::to_string(&fixture.server())?;
    raw.insert_str(1, "\"schema_version\":1,");
    let path = fixture.write("duplicate.json", raw.as_bytes(), 0o644)?;
    assert!(matches!(
        load_server_inner(&path, &fixture.context),
        Err(Error::InvalidInput("server configuration schema"))
    ));
    Ok(())
}

#[test]
fn ambiguous_pools_and_outside_principal_devices_fail_closed() -> TestResult {
    let fixture = Fixture::new()?;
    for (field, value) in [
        ("devices", json!([])),
        ("devices", json!(["virtual:hid", "virtual:hid"])),
        ("devices", json!(["1-1"])),
        (
            "principals",
            json!([{"id":"a","pins":["ab".repeat(32)],"devices":["virtual:cdc-acm"]}]),
        ),
        (
            "principals",
            json!([
                {"id":"a","pins":["ab".repeat(32)],"devices":["virtual:hid"]},
                {"id":"b","pins":["ab".repeat(32)],"devices":["virtual:hid"]}
            ]),
        ),
    ] {
        let mut config = fixture.server();
        config[field] = value;
        assert!(load_server_inner(&fixture.config(&config)?, &fixture.context).is_err());
    }
    // Distinct typed namespaces can be loaded; the selected CLI backend must
    // reject a mixed pool before acquiring any helper/device capability.
    let mut mixed = fixture.server();
    mixed["devices"] = json!(["virtual:hid", format!("physical:usb-{}", "a".repeat(64))]);
    assert_eq!(
        load_server_inner(&fixture.config(&mixed)?, &fixture.context)?
            .devices
            .len(),
        2
    );
    let mut too_many = fixture.server();
    let mut devices = vec!["virtual:hid".to_owned()];
    devices.extend((0..16).map(|n| format!("physical:usb-{n:064x}")));
    too_many["devices"] = json!(devices);
    assert!(matches!(
        load_server_inner(&fixture.config(&too_many)?, &fixture.context),
        Err(Error::InvalidInput("server device pool"))
    ));
    Ok(())
}

#[test]
fn client_alias_name_endpoint_and_device_are_strict() -> TestResult {
    let fixture = Fixture::new()?;
    for (field, value) in [
        ("server_id", json!("")),
        ("server_id", json!("line\nbreak")),
        ("server_id", json!("a".repeat(65))),
        ("server_name", json!("bad name")),
        ("server_name", json!("a".repeat(254))),
        ("endpoint", json!({"host":"server.test","port":0})),
        ("endpoint", json!({"host":"bad/name","port":3241})),
        ("device", json!("1-1")),
    ] {
        let mut config = fixture.client();
        config[field] = value;
        assert!(load_client_inner(&fixture.config(&config)?, &fixture.context).is_err());
    }
    for host in ["127.0.0.1", "::1", "server.test"] {
        let mut config = fixture.client();
        config["endpoint"]["host"] = json!(host);
        assert_eq!(
            load_client_inner(&fixture.config(&config)?, &fixture.context)?
                .endpoint
                .host
                .as_str(),
            host
        );
    }
    Ok(())
}

#[test]
fn symlink_hardlink_fifo_directory_and_unsafe_parent_are_refused() -> TestResult {
    let fixture = Fixture::new()?;
    let good = fixture.path("ca.der");
    let link = fixture.path("symlink.der");
    symlink(&good, &link)?;
    assert!(
        read_file(
            &link,
            Kind::Public,
            MAX_DER,
            "CA certificate",
            &fixture.context
        )
        .is_err()
    );
    let hard = fixture.path("hardlink.der");
    fs::hard_link(&good, &hard)?;
    assert!(
        read_file(
            &good,
            Kind::Public,
            MAX_DER,
            "CA certificate",
            &fixture.context
        )
        .is_err()
    );
    fs::remove_file(&hard)?;
    let fifo = fixture.path("pipe.der");
    rustix::fs::mkfifoat(
        rustix::fs::CWD,
        &fifo,
        rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR,
    )?;
    assert!(
        read_file(
            &fifo,
            Kind::Public,
            MAX_DER,
            "CA certificate",
            &fixture.context
        )
        .is_err()
    );
    assert!(
        read_file(
            &fixture.root,
            Kind::Public,
            MAX_DER,
            "CA certificate",
            &fixture.context
        )
        .is_err()
    );
    fs::set_permissions(&fixture.root, fs::Permissions::from_mode(0o770))?;
    assert!(
        read_file(
            &good,
            Kind::Public,
            MAX_DER,
            "CA certificate",
            &fixture.context
        )
        .is_err()
    );
    fs::set_permissions(&fixture.root, fs::Permissions::from_mode(0o700))?;
    let parent_link = fixture.path("parent-link");
    symlink(&fixture.root, &parent_link)?;
    assert!(
        read_file(
            &parent_link.join("ca.der"),
            Kind::Public,
            MAX_DER,
            "CA certificate",
            &fixture.context
        )
        .is_err()
    );
    Ok(())
}

#[test]
fn metadata_requires_real_expected_owner_and_private_key_mode() -> TestResult {
    let fixture = Fixture::new()?;
    let public = fs::metadata(fixture.path("ca.der"))?;
    let wrong = Context {
        uid: fixture.context.uid,
        administrator_uid: fixture.context.administrator_uid.saturating_add(1),
        fixture_root: Some(fixture.root.clone()),
    };
    assert!(validate_metadata(&public, Kind::Public, MAX_DER, &wrong).is_err());
    let key = fixture.path("private.der");
    assert!(
        validate_metadata(
            &fs::metadata(&key)?,
            Kind::Secret(fixture.context.uid.saturating_add(2)),
            MAX_DER,
            &wrong,
        )
        .is_err()
    );
    for mode in [0o644, 0o660, 0o700, 0o4600] {
        fs::set_permissions(&key, fs::Permissions::from_mode(mode))?;
        assert!(
            read_file(
                &key,
                Kind::Secret(fixture.context.uid),
                MAX_DER,
                "private key",
                &fixture.context
            )
            .is_err()
        );
    }
    fs::set_permissions(&key, fs::Permissions::from_mode(0o600))?;
    assert!(
        !read_file(
            &key,
            Kind::Secret(fixture.context.uid),
            MAX_DER,
            "private key",
            &fixture.context
        )?
        .is_empty()
    );
    Ok(())
}

#[test]
fn bounded_reads_reject_oversize_and_detect_changed_identity() -> TestResult {
    let fixture = Fixture::new()?;
    let oversized = fixture.write("oversize.der", &vec![0; MAX_DER + 1], 0o644)?;
    assert!(
        read_file(
            &oversized,
            Kind::Public,
            MAX_DER,
            "CA certificate",
            &fixture.context
        )
        .is_err()
    );
    let config = fixture.write("oversize.json", &vec![b' '; MAX_CONFIG + 1], 0o644)?;
    assert!(load_server_inner(&config, &fixture.context).is_err());
    let changed = fixture.write("changing.der", b"before", 0o644)?;
    let before = fs::metadata(&changed)?;
    OpenOptions::new()
        .append(true)
        .open(&changed)?
        .write_all(b"after")?;
    let after = fs::metadata(&changed)?;
    assert!(!same_file(&before, &after));
    let replacement = fixture.write("replacement.der", b"before", 0o644)?;
    assert!(!same_file(&before, &fs::metadata(replacement)?));
    Ok(())
}

#[test]
fn private_file_and_json_errors_never_include_input_bytes_or_paths() -> TestResult {
    let fixture = Fixture::new()?;
    let path = fixture.write(
        "SECRET-PATH.json",
        b"{\"SECRET-JSON-MARKER\":\"PRIVATE-KEY\"}",
        0o644,
    )?;
    let error = load_server_inner(&path, &fixture.context)
        .err()
        .ok_or("invalid schema accepted")?;
    assert!(!format!("{error:?} {error}").contains("SECRET"));
    let missing = fixture.path("SECRET-MISSING.der");
    let error = read_file(
        &missing,
        Kind::Public,
        MAX_DER,
        "CA certificate",
        &fixture.context,
    )
    .err()
    .ok_or("missing file accepted")?;
    assert!(!format!("{error:?} {error}").contains("SECRET"));
    let loaded = load_server_inner(&fixture.config(&fixture.server())?, &fixture.context)?;
    let key = fs::read(fixture.path("private.der"))?;
    let debug = format!("{loaded:?}");
    assert!(!debug.contains("private.der") && !debug.contains(&format!("{key:?}")));
    Ok(())
}

#[test]
fn path_traversal_and_production_trust_are_not_weakened_by_fixture_context() -> TestResult {
    let fixture = Fixture::new()?;
    for path in [
        PathBuf::from("relative.json"),
        fixture.root.join("../escape.json"),
        PathBuf::from("/etc/line\nbreak"),
    ] {
        assert!(valid_path(&path).is_err());
    }
    assert!(valid_path(&PathBuf::from(format!("/{}", "a".repeat(MAX_PATH)))).is_err());
    let config = fixture.config(&fixture.server())?;
    // Production validation really traverses /tmp, which must remain untrusted.
    assert!(load_server(&config).is_err());
    Ok(())
}

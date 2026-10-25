//! Validation only: no automatic runner, helper mutation or USB claim.

use super::*;
use serde_json::{Value, json};

type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;

fn spec() -> RuleSpec {
    // A constant valid fixture with construction failure preserved as a test
    // error by callers, rather than a core unwrap/expect path.
    RuleSpec {
        name: "Desk keyboard".into(),
        trigger: TriggerKind::LocalProfileReady,
        settle_seconds: 2,
        rearm: RearmKind::Once,
    }
}

#[test]
fn bounded_rule_definition_roundtrips_without_device_or_identity_authority() -> TestResult {
    for trigger in [
        TriggerKind::LocalProfileReady,
        TriggerKind::RemoteDeviceAvailable,
    ] {
        for rearm in [RearmKind::Once, RearmKind::NewObservation] {
            for seconds in [2, 120] {
                let value = RuleSpec::new("书桌 keyboard".into(), trigger, seconds, rearm)?;
                let bytes = serde_json::to_vec(&value)?;
                assert!(bytes.len() < 256);
                assert_eq!(serde_json::from_slice::<RuleSpec>(&bytes)?, value);
                assert_eq!(value.name(), "书桌 keyboard");
                assert_eq!(value.trigger(), trigger);
                assert_eq!(value.rearm(), rearm);
                assert_eq!(value.settle_seconds(), seconds);
            }
        }
    }
    let value = RuleSpec::new(
        "x".repeat(64),
        TriggerKind::LocalProfileReady,
        2,
        RearmKind::Once,
    )?;
    assert_eq!(value.name().len(), 64);
    Ok(())
}

#[test]
fn invalid_names_intervals_and_arbitrary_rule_fields_are_rejected() -> TestResult {
    for name in [
        "".into(),
        " ".into(),
        " name".into(),
        "name ".into(),
        "x\ny".into(),
        "x\0y".into(),
        "x\u{7f}y".into(),
        "\u{2003}name".into(),
        "x".repeat(65),
        "界".repeat(22),
    ] {
        assert!(
            RuleSpec::new(
                name.clone(),
                TriggerKind::LocalProfileReady,
                2,
                RearmKind::Once
            )
            .is_err()
        );
        let mut value = serde_json::to_value(spec())?;
        value["name"] = json!(name);
        assert!(serde_json::from_value::<RuleSpec>(value).is_err());
    }
    for seconds in [0, 1, 121, u16::MAX] {
        let mut value = serde_json::to_value(spec())?;
        value["settle_seconds"] = json!(seconds);
        assert!(serde_json::from_value::<RuleSpec>(value).is_err());
    }
    for key in [
        "operator_uid",
        "profile",
        "device",
        "devices",
        "endpoint",
        "path",
        "private_key",
        "command",
        "expression",
        "enabled",
    ] {
        let mut value = serde_json::to_value(spec())?;
        value[key] = json!("untrusted");
        assert!(
            serde_json::from_value::<RuleSpec>(value).is_err(),
            "accepted {key}"
        );
    }
    for (key, value) in [
        ("trigger", json!("shell")),
        ("rearm", json!("always")),
        ("trigger", json!({"local_profile_ready": {"extra": 1}})),
        ("settle_seconds", json!(-1)),
        ("settle_seconds", json!(2.5)),
        ("settle_seconds", json!(65536)),
    ] {
        let mut input = serde_json::to_value(spec())?;
        input[key] = value;
        assert!(serde_json::from_value::<RuleSpec>(input).is_err());
    }
    for missing in ["name", "trigger", "settle_seconds", "rearm"] {
        let mut value = serde_json::to_value(spec())?;
        value.as_object_mut().ok_or("rule object")?.remove(missing);
        assert!(serde_json::from_value::<RuleSpec>(value).is_err());
    }
    let duplicate = br#"{"name":"first","name":"second","trigger":"local_profile_ready","settle_seconds":2,"rearm":"once"}"#;
    assert!(serde_json::from_slice::<RuleSpec>(duplicate).is_err());
    Ok(())
}

#[test]
fn mutations_are_closed_struct_variants_with_checked_ids_and_revisions() -> TestResult {
    let id = RuleId::new("00000000-0000-0000-0000-000000000001")?;
    let edits = [
        RuleMutation::Create {
            spec: spec(),
            enabled: false,
        },
        RuleMutation::Replace {
            rule_id: id.clone(),
            expected_revision: ExpectedRevision::new(1)?,
            spec: spec(),
        },
        RuleMutation::SetEnabled {
            rule_id: id.clone(),
            expected_revision: ExpectedRevision::new(2)?,
            enabled: true,
        },
        RuleMutation::Delete {
            rule_id: id,
            expected_revision: ExpectedRevision::new(u64::MAX - 1)?,
        },
    ];
    assert_eq!(ExpectedRevision::new(1)?.get(), 1);
    for edit in edits {
        let value = serde_json::to_value(&edit)?;
        assert_eq!(serde_json::from_value::<RuleMutation>(value.clone())?, edit);
        for extra in [
            "operator_uid",
            "profile",
            "request_id",
            "job_id",
            "endpoint",
            "stop_current",
            "unknown",
        ] {
            let mut bad = value.clone();
            bad[extra] = json!(1);
            assert!(
                serde_json::from_value::<RuleMutation>(bad).is_err(),
                "accepted {extra}"
            );
        }
        for required in value.as_object().ok_or("mutation object")?.keys() {
            let mut bad = value.clone();
            bad.as_object_mut()
                .ok_or("mutation object")?
                .remove(required);
            assert!(serde_json::from_value::<RuleMutation>(bad).is_err());
        }
        if value.get("expected_revision").is_some() {
            for revision in [
                json!(0),
                json!(u64::MAX),
                json!(-1),
                json!(1.5),
                json!("1"),
                Value::Null,
            ] {
                let mut bad = value.clone();
                bad["expected_revision"] = revision;
                assert!(serde_json::from_value::<RuleMutation>(bad).is_err());
            }
            for id in [
                "",
                "../rule",
                "00000000-0000-0000-0000-00000000000A",
                "00000000-0000-0000-0000-000000000001\n",
            ] {
                let mut bad = value.clone();
                bad["rule_id"] = json!(id);
                assert!(serde_json::from_value::<RuleMutation>(bad).is_err());
            }
        }
    }
    for input in [
        "null",
        "\"delete\"",
        "{\"change\":\"delete\"}",
        "{\"change\":\"create\",\"enabled\":true}",
    ] {
        assert!(serde_json::from_str::<RuleMutation>(input).is_err());
    }
    assert!(ExpectedRevision::new(0).is_err());
    assert!(ExpectedRevision::new(u64::MAX).is_err());
    Ok(())
}

#[cfg(target_os = "linux")]
mod policy {
    use super::*;
    use crate::config::{Manifest, Operator, Profile};
    use rustusb_core::{Host, Peer, VirtualProfile};
    use rustusb_transport::config::{LoadedClientConfig, LoadedServerConfig};
    use std::{collections::BTreeMap, num::NonZeroU16, sync::Arc};

    const BROKER: u32 = 1001;
    const OPERATOR: u32 = 1002;

    fn fixture() -> std::result::Result<LoadedConfig, Box<dyn std::error::Error + Send + Sync>> {
        let (server, client) = crate::jobs::management_tests::credentials(false)?;
        let mut config = LoadedConfig {
            manifest: Manifest {
                schema_version: 1,
                broker_uid: BROKER,
                helper_socket: "/run/rustusb/helper.sock".into(),
                control_directory: "/run/rustusb/agent".into(),
                data_directory: "/var/lib/rustusb/agent".into(),
                profiles: vec![],
                operators: vec![Operator {
                    uid: OPERATOR,
                    profiles: BTreeSet::from([
                        "native".into(),
                        "usbfs".into(),
                        "virtual".into(),
                        "client".into(),
                    ]),
                    actions: BTreeSet::from([
                        Action::Read,
                        Action::Start,
                        Action::Stop,
                        Action::Discover,
                    ]),
                }],
            },
            credentials: BTreeMap::new(),
        };
        for (id, backend, devices) in [
            (
                "native",
                Backend::Native,
                vec![
                    DeviceKey::native(&format!("usb-{:064x}", 1))?,
                    DeviceKey::native(&format!("usb-{:064x}", 2))?,
                ],
            ),
            (
                "usbfs",
                Backend::Usbfs,
                vec![DeviceKey::physical(&format!("usb-{:064x}", 3))?],
            ),
            (
                "virtual",
                Backend::Virtual,
                vec![DeviceKey::virtual_profile(VirtualProfile::Hid)],
            ),
        ] {
            config.manifest.profiles.push(Profile {
                id: id.into(),
                backend,
                config: format!("/etc/rustusb/{id}.json").into(),
            });
            config.credentials.insert(
                id.into(),
                Credentials::Server(Arc::new(LoadedServerConfig {
                    broker_uid: BROKER,
                    listen: "127.0.0.1:4430".parse()?,
                    devices,
                    tls: server.clone(),
                })),
            );
        }
        config.manifest.profiles.push(Profile {
            id: "client".into(),
            backend: Backend::Client,
            config: "/etc/rustusb/client.json".into(),
        });
        config.credentials.insert(
            "client".into(),
            Credentials::Client(Arc::new(LoadedClientConfig {
                broker_uid: BROKER,
                server_id: "test-server".into(),
                endpoint: Peer {
                    host: Host::new("127.0.0.1")?,
                    port: NonZeroU16::new(4430).ok_or("fixture port")?,
                },
                server_name: "server.test".into(),
                device: DeviceKey::virtual_profile(VirtualProfile::Hid),
                tls: client,
            })),
        );
        config.manifest.validate()?;
        Ok(config)
    }

    fn document(
        config: &LoadedConfig,
        profile: &str,
    ) -> std::result::Result<Value, Box<dyn std::error::Error + Send + Sync>> {
        let (devices, trigger) = match config.credentials.get(profile).ok_or("fixture profile")? {
            Credentials::Client(loaded) => (
                vec![loaded.device.clone()],
                TriggerKind::RemoteDeviceAvailable,
            ),
            Credentials::Server(loaded) => (loaded.devices.clone(), TriggerKind::LocalProfileReady),
        };
        Ok(
            json!({"schema_version": 1, "broker_uid": BROKER, "grants": [{
                "operator_uid": OPERATOR, "profile": profile, "devices": devices,
                "triggers": [trigger], "rearm": ["once", "new_observation"], "max_rules": 4
            }]}),
        )
    }

    fn bind(config: &LoadedConfig, value: &Value) -> Result<AutomationPolicy> {
        let bytes =
            serde_json::to_vec(value).map_err(|_| Error::State("fixture policy encode".into()))?;
        AutomationPolicy::bind(&bytes, config)
    }

    fn matching_spec(profile: &str) -> Result<RuleSpec> {
        RuleSpec::new(
            "Desk keyboard".into(),
            if profile == "client" {
                TriggerKind::RemoteDeviceAvailable
            } else {
                TriggerKind::LocalProfileReady
            },
            2,
            RearmKind::Once,
        )
    }

    #[test]
    fn grants_bind_entire_loaded_effect_and_never_hold_credentials() -> TestResult {
        let config = fixture()?;
        for profile in ["native", "usbfs", "client"] {
            let mut input = document(&config, profile)?;
            input["grants"][0]["devices"]
                .as_array_mut()
                .ok_or("device list")?
                .reverse();
            let policy = bind(&config, &input)?;
            let grant = policy.authorize(&config, OPERATOR, profile, &matching_spec(profile)?)?;
            assert_eq!(grant.profile(), profile);
            assert_eq!(grant.operator_uid(), OPERATOR);
            assert_eq!(grant.max_rules(), MAX_RULES_PER_GRANT);
            assert_eq!(grant.rearm().len(), 2);
            assert_eq!(grant.triggers().len(), 1);
            assert_eq!(policy.grants(), std::slice::from_ref(grant));
            let public = serde_json::to_value(grant)?;
            assert!(public.get("tls").is_none());
            assert!(public.get("config").is_none());
            assert!(public.get("endpoint").is_none());
            assert_eq!(
                public["devices"].as_array().ok_or("device list")?.len(),
                grant.devices().len()
            );
        }
        // A simulated server has no physical LocalProfileReady condition.
        assert!(bind(&config, &document(&config, "virtual")?).is_err());
        Ok(())
    }

    #[test]
    fn manual_start_root_and_other_uids_do_not_gain_automation() -> TestResult {
        let mut config = fixture()?;
        let policy = bind(&config, &document(&config, "native")?)?;
        let spec = matching_spec("native")?;
        for uid in [0, BROKER, OPERATOR + 1] {
            assert!(policy.authorize(&config, uid, "native", &spec).is_err());
        }
        assert!(
            config
                .manifest
                .authorize(OPERATOR, "usbfs", Action::Start)
                .is_ok()
        );
        assert!(policy.authorize(&config, OPERATOR, "usbfs", &spec).is_err());
        config.manifest.operators.push(Operator {
            uid: 0,
            profiles: BTreeSet::from(["native".into()]),
            actions: BTreeSet::from([Action::Read, Action::Start, Action::Stop]),
        });
        // Even explicit root manual permissions are insufficient without its own grant.
        assert!(policy.authorize(&config, 0, "native", &spec).is_err());
        let mut explicit = document(&config, "native")?;
        explicit["grants"][0]["operator_uid"] = json!(0);
        assert!(
            bind(&config, &explicit)?
                .authorize(&config, 0, "native", &spec)
                .is_ok()
        );
        let empty = bind(
            &config,
            &json!({"schema_version":1,"broker_uid":BROKER,"grants":[]}),
        )?;
        assert!(empty.grants().is_empty());
        assert!(empty.authorize(&config, OPERATOR, "native", &spec).is_err());
        Ok(())
    }

    #[test]
    fn every_admission_rechecks_acl_revocation_and_rule_scope() -> TestResult {
        for profile in ["native", "client"] {
            for action in [Action::Read, Action::Start, Action::Stop, Action::Discover] {
                let mut config = fixture()?;
                config.manifest.operators[0].actions.remove(&action);
                // Revoked ACL does not prevent loading other profiles/cleanup.
                let policy = bind(&config, &document(&config, profile)?)?;
                let authorized = policy
                    .authorize(&config, OPERATOR, profile, &matching_spec(profile)?)
                    .is_ok();
                assert_eq!(
                    authorized,
                    profile == "native" && action == Action::Discover
                );
            }
        }
        let mut config = fixture()?;
        let mut input = document(&config, "native")?;
        input["grants"][0]["rearm"] = json!(["once"]);
        let policy = bind(&config, &input)?;
        assert!(
            policy
                .authorize(
                    &config,
                    OPERATOR,
                    "native",
                    &RuleSpec::new(
                        "later".into(),
                        TriggerKind::LocalProfileReady,
                        2,
                        RearmKind::NewObservation
                    )?
                )
                .is_err()
        );
        assert!(
            policy
                .authorize(&config, OPERATOR, "native", &matching_spec("client")?)
                .is_err()
        );
        config.manifest.operators.clear();
        assert!(
            policy
                .authorize(&config, OPERATOR, "native", &matching_spec("native")?)
                .is_err()
        );
        Ok(())
    }

    #[test]
    fn subset_extra_duplicate_and_cross_backend_device_selections_fail_closed() -> TestResult {
        let config = fixture()?;
        let original = document(&config, "native")?;
        for devices in [
            json!([]),
            json!([original["grants"][0]["devices"][0]]),
            json!([
                original["grants"][0]["devices"][0],
                original["grants"][0]["devices"][0]
            ]),
            json!([
                original["grants"][0]["devices"][0],
                original["grants"][0]["devices"][1],
                "virtual:hid"
            ]),
            json!(["virtual:hid"]),
            json!(["1-2"]),
        ] {
            let mut input = original.clone();
            input["grants"][0]["devices"] = devices;
            assert!(bind(&config, &input).is_err());
        }
        for (field, value) in [
            ("triggers", json!([])),
            (
                "triggers",
                json!(["local_profile_ready", "local_profile_ready"]),
            ),
            (
                "triggers",
                json!(["local_profile_ready", "remote_device_available"]),
            ),
            ("triggers", json!(["remote_device_available"])),
            ("rearm", json!([])),
            ("rearm", json!(["once", "once"])),
            ("rearm", json!(["always"])),
        ] {
            let mut input = original.clone();
            input["grants"][0][field] = value;
            assert!(bind(&config, &input).is_err());
        }
        Ok(())
    }

    #[test]
    fn changed_loaded_uid_pool_or_credential_kind_cannot_reuse_a_grant() -> TestResult {
        for change in 0..7 {
            let mut config = fixture()?;
            let policy = bind(&config, &document(&config, "native")?)?;
            match change {
                0 => config.manifest.broker_uid += 1,
                1 => {
                    config.credentials.remove("native");
                }
                2 => {
                    let other = config
                        .credentials
                        .get("client")
                        .ok_or("client credential")?
                        .clone();
                    config.credentials.insert("native".into(), other);
                }
                _ => {
                    let Credentials::Server(server) = config
                        .credentials
                        .get_mut("native")
                        .ok_or("native credential")?
                    else {
                        return Err("server kind".into());
                    };
                    let server = Arc::get_mut(server).ok_or("unshared fixture credential")?;
                    match change {
                        3 => server.broker_uid += 1,
                        4 => {
                            server.devices.pop();
                        }
                        5 => server.devices.push(server.devices[0].clone()),
                        _ => server.devices = vec![DeviceKey::virtual_profile(VirtualProfile::Hid)],
                    }
                }
            }
            assert!(
                policy
                    .authorize(&config, OPERATOR, "native", &matching_spec("native")?)
                    .is_err(),
                "accepted changed configuration {change}"
            );
        }
        Ok(())
    }

    #[test]
    fn policy_schema_grant_and_byte_limits_are_explicit() -> TestResult {
        let config = fixture()?;
        let original = document(&config, "native")?;
        for (field, value) in [
            ("schema_version", json!(0)),
            ("schema_version", json!(2)),
            ("broker_uid", json!(0)),
            ("broker_uid", json!(BROKER + 1)),
            ("endpoint", json!("untrusted")),
            ("grants", Value::Null),
        ] {
            let mut input = original.clone();
            input[field] = value;
            assert!(bind(&config, &input).is_err());
        }
        for field in ["schema_version", "broker_uid", "grants"] {
            let mut input = original.clone();
            input.as_object_mut().ok_or("policy object")?.remove(field);
            assert!(bind(&config, &input).is_err());
        }
        for field in [
            "operator_uid",
            "profile",
            "devices",
            "triggers",
            "rearm",
            "max_rules",
        ] {
            let mut input = original.clone();
            input["grants"][0]
                .as_object_mut()
                .ok_or("grant object")?
                .remove(field);
            assert!(bind(&config, &input).is_err());
        }
        for (field, value) in [
            ("max_rules", json!(0)),
            ("max_rules", json!(5)),
            ("max_rules", json!(256)),
            ("operator_uid", json!(-1)),
            ("profile", json!("absent")),
            ("profile", json!("../native")),
            ("command", json!("untrusted")),
            ("devices", Value::Null),
            ("rearm", Value::Null),
        ] {
            let mut input = original.clone();
            input["grants"][0][field] = value;
            assert!(bind(&config, &input).is_err());
        }
        let mut input = original.clone();
        input["grants"] = json!([original["grants"][0], original["grants"][0]]);
        assert!(bind(&config, &input).is_err());
        let mut grants = vec![];
        for uid in 0..MAX_GRANTS {
            let mut grant = original["grants"][0].clone();
            grant["operator_uid"] = json!(uid);
            grants.push(grant);
        }
        input["grants"] = json!(grants);
        assert_eq!(bind(&config, &input)?.grants().len(), MAX_GRANTS);
        grants.push(original["grants"][0].clone());
        input["grants"] = json!(grants);
        assert!(bind(&config, &input).is_err());
        let mut bytes = serde_json::to_vec(&original)?;
        bytes.resize(MAX_POLICY_BYTES, b' ');
        assert!(AutomationPolicy::bind(&bytes, &config).is_ok());
        bytes.push(b' ');
        assert!(AutomationPolicy::bind(&bytes, &config).is_err());
        let duplicate = format!(
            "{{\"schema_version\":1,\"schema_version\":1,\"broker_uid\":{BROKER},\"grants\":[]}}"
        );
        assert!(AutomationPolicy::bind(duplicate.as_bytes(), &config).is_err());
        Ok(())
    }

    #[test]
    fn complete_pool_limit_and_user_owned_policy_trust_are_preserved() -> TestResult {
        let mut config = fixture()?;
        for count in [MAX_PROFILE_DEVICES, MAX_PROFILE_DEVICES + 1] {
            let Credentials::Server(server) = config
                .credentials
                .get_mut("native")
                .ok_or("native credential")?
            else {
                return Err("server kind".into());
            };
            Arc::get_mut(server)
                .ok_or("unshared fixture credential")?
                .devices = (0..count)
                .map(|n| DeviceKey::native(&format!("usb-{n:064x}")))
                .collect::<Result<Vec<_>>>()?;
            assert_eq!(
                bind(&config, &document(&config, "native")?).is_ok(),
                count == MAX_PROFILE_DEVICES
            );
        }
        let temp = tempfile::tempdir()?;
        let path = temp.path().join("automation.json");
        let bytes = serde_json::to_vec(&document(&config, "client")?)?;
        std::fs::write(&path, &bytes)?;
        assert!(matches!(
            AutomationPolicy::read(&path, &config),
            Err(Error::Permission(_))
        ));
        assert_eq!(std::fs::read(path)?, bytes);
        Ok(())
    }
}

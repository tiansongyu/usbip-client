use super::*;
use rustusb_daemon::diagnostics::HelperDiagnostics;
use rustusb_native::diagnostics::{
    DiagnosticCheck, KernelDiagnostic, NativeDiagnostics, SystemDiagnostic, ToolDiagnostic,
};
type TestResult = std::result::Result<(), Box<dyn std::error::Error>>;

fn native() -> NativeObservations {
    let text = "PRIVATE_NATIVE_PATH_HOST_SERIAL_ERROR";
    let check = || DiagnosticCheck {
        status: DiagnosticStatus::Missing,
        detail: text.into(),
        action: text.into(),
    };
    let module = || KernelDiagnostic {
        module: text.into(),
        loaded: check(),
        sysfs: check(),
        availability: check(),
    };
    let tool = || ToolDiagnostic {
        path: format!("/home/{text}"),
        check: check(),
        version: Some(format!("usbip 2.0 /home/{text}")),
    };
    NativeObservations {
        native: Ok(NativeDiagnostics {
            system: SystemDiagnostic {
                os: "linux".into(),
                architecture: "aarch64".into(),
                kernel_release: Some("6.8.0-PRIVATE_KERNEL_SUFFIX".into()),
                kernel: check(),
            },
            usbip_host: module(),
            vhci_hcd: module(),
            usbip: tool(),
            usbipd: tool(),
            scope: text.into(),
        }),
        helper: HelperDiagnostics {
            status: HelperStatus::PermissionDenied,
            detail: text.into(),
            action: text.into(),
            peer_verified: true,
            kernel_available: None,
            active_sessions: None,
            external_trusted_network_confirmed: None,
        },
    }
}
fn page(records: Vec<logs::LogRecord>) -> logs::LogPage {
    logs::LogPage {
        schema_version: 1,
        records,
        next_after: None,
        oldest_available: None,
        gap: false,
        partial_tail: false,
        scanned_bytes: 0,
        more: false,
        status: None,
    }
}
fn record(event: logs::Event, sequence: u64) -> logs::Result<logs::LogRecord> {
    Ok(logs::LogRecord {
        schema_version: 1,
        epoch: logs::LogId::new("abcdefab-0000-0000-0000-000000000001")?,
        sequence,
        elapsed_millis: sequence,
        wall_unix_millis: Some(1_799_999_999_999),
        level: logs::Level::Info,
        event,
    })
}
fn parse(builder: &Builder) -> std::result::Result<serde_json::Value, Box<dyn std::error::Error>> {
    Ok(serde_json::from_slice(
        &builder.encode(CaptureOutcome::Complete)?,
    )?)
}
#[test]
fn actual_json_excludes_native_text_and_log_identity_but_preserves_observations() -> TestResult {
    let mut b = Builder::new();
    b.observe_local(native(), 7);
    let diagnostic = logs::Diagnostic {
        code: logs::EventCode::OperationFailed,
        target: "rustusb::private_target_sentinel".into(),
        line: Some(42),
        failure: Some(logs::FailureCategory::Io),
        errno: Some(5),
        fields: BTreeMap::from([(logs::Field::Success, logs::Scalar::Bool(false))]),
        omitted_fields: 3,
    };
    b.observe_logs(
        &page(vec![
            record(
                logs::Event::Started {
                    pid: 987654321,
                    uid: 123456789,
                },
                1,
            )?,
            record(logs::Event::Diagnostic(diagnostic), 2)?,
        ]),
        9,
    )?;
    b.set_outcome(Stage::Logs, SectionOutcome::Complete, None, 10);
    let bytes = b.encode(CaptureOutcome::Complete)?;
    let text = std::str::from_utf8(&bytes)?;
    for forbidden in [
        "PRIVATE_NATIVE",
        "PRIVATE_KERNEL",
        "private_target_sentinel",
        "987654321",
        "123456789",
        "abcdefab-0000",
        "1799999999999",
        "/home/",
    ] {
        assert!(!text.contains(forbidden), "{forbidden}");
    }
    let value: serde_json::Value = serde_json::from_slice(&bytes)?;
    assert_eq!(value["capture"], "complete");
    assert_eq!(value["native"]["kernel_numeric"]["major"], 6);
    assert_eq!(value["native"]["usbip"], "missing");
    assert_eq!(value["helper"]["status"], "permission_denied");
    assert!(value["helper"]["active_sessions"].is_null());
    assert!(value["logs"]["pages"][0]["live_status"].is_null());
    assert_eq!(value["logs"]["records"][1]["event"]["failure"], "io");
    assert_eq!(
        value["logs"]["records"][1]["event"]["fields"]["success"],
        false
    );
    Ok(())
}
#[test]
fn limitations_remain_partial_after_collector_marks_stage_complete() -> TestResult {
    for condition in 0..3 {
        let mut b = Builder::new();
        b.set_outcome(
            Stage::Logs,
            SectionOutcome::Incomplete,
            Some(CollectionFailure::Unavailable),
            0,
        );
        let mut p = page(Vec::new());
        match condition {
            0 => p.gap = true,
            1 => p.partial_tail = true,
            _ => {
                p.status = Some(logs::LogStatus {
                    healthy: false,
                    accepted: 3,
                    written: 3,
                    dropped: 2,
                    ..Default::default()
                })
            }
        }
        b.observe_logs(&p, 1)?;
        b.set_outcome(Stage::Logs, SectionOutcome::Complete, None, 2);
        assert!(!b.summary().complete);
        let v = parse(&b)?;
        assert_eq!(v["capture"], "partial");
        assert_eq!(v["summary"]["sections"][4]["outcome"], "incomplete");
    }
    // An in-flight marker or absence of a live worker is not permanent loss.
    let mut b = Builder::new();
    b.set_outcome(
        Stage::Logs,
        SectionOutcome::Incomplete,
        Some(CollectionFailure::Unavailable),
        0,
    );
    b.observe_logs(&page(Vec::new()), 1)?;
    b.set_outcome(Stage::Logs, SectionOutcome::Complete, None, 2);
    assert!(b.summary().complete);
    Ok(())
}
#[test]
fn rejected_source_bounds_and_alias_budget_are_explicit() -> TestResult {
    let mut b = Builder::new();
    let r = record(logs::Event::Started { pid: 1, uid: 0 }, 1)?;
    assert!(b.observe_logs(&page(vec![r; 17]), 1).is_err());
    b.set_outcome(Stage::Logs, SectionOutcome::Complete, None, 2);
    assert!(!b.summary().complete);
    let mut b = Builder::new();
    for i in 0..MAX_ALIASES {
        b.aliases.get(Namespace::Uid, &format!("{i}"))?;
    }
    assert!(b.aliases.get(Namespace::Uid, "additional").is_err());
    assert_eq!(b.aliases.get(Namespace::Uid, "0")?, 1);
    // A repeated source record is not silently exported as another observation.
    let mut b = Builder::new();
    let p = page(vec![record(logs::Event::Started { pid: 1, uid: 0 }, 1)?]);
    b.observe_logs(&p, 0)?;
    assert!(b.observe_logs(&p, 1).is_err());
    Ok(())
}
#[test]
fn serialized_budget_refuses_the_actual_crossing_write() -> TestResult {
    let mut writer = LimitedBytes {
        bytes: Vec::new(),
        limit: 8,
    };
    writer.write_all(b"1234567")?;
    assert!(writer.write_all(b"89").is_err());
    assert_eq!(writer.bytes, b"1234567");
    writer.write_all(b"8")?;
    assert_eq!(writer.bytes.len(), 8);
    // The same Write is the serializer sink, not a post-serialization len test.
    let mut writer = LimitedBytes {
        bytes: Vec::new(),
        limit: 8,
    };
    assert!(serde_json::to_writer(&mut writer, &[1_u64, 2, 3, 4, 5]).is_err());
    assert!(writer.bytes.len() <= 8);
    Ok(())
}

#[cfg(feature = "secure-transport")]
mod secure {
    use super::*;
    use rustusb_service::{
        config::Backend as ServiceBackend,
        diagnostics::*,
        health::{HealthAvailability, SessionHealth},
        model,
    };
    use std::num::NonZeroU64;
    const PROFILE: &str = "PRIVATE_PROFILE_SENTINEL";
    fn id(n: u8) -> String {
        format!("00000000-0000-0000-0000-{n:012}")
    }
    fn header() -> std::result::Result<DiagnosticHeader, Box<dyn std::error::Error>> {
        Ok(DiagnosticHeader {
            snapshot: DiagnosticToken {
                instance_id: InstanceId::new(id(1))?,
                number: NonZeroU64::new(1).ok_or("token")?,
            },
            backend: ServiceBackend::Native,
            committed_sequence: 10,
            publication_age_millis: 15,
            counts: DiagnosticCounts {
                jobs: 1,
                sessions: 1,
                audit: 1,
            },
            audit_oldest_retained_sequence: 1,
            expires_after_millis: 30_000,
        })
    }
    fn populate(b: &mut Builder) -> TestResult {
        b.begin_agent(&header()?, PROFILE, 123456789, 1)?;
        b.push_items(DiagnosticItems::Jobs {
            jobs: vec![model::Job {
                id: JobId::new(id(2))?,
                instance_id: InstanceId::new(id(1))?,
                profile: PROFILE.into(),
                operator_uid: 123456789,
                state: model::JobState::Finished,
                stop_requested: true,
                operation_error: Some(model::Failure::State),
            }],
        })?;
        b.push_items(DiagnosticItems::Sessions {
            sessions: vec![model::Session {
                id: SessionId::new(id(3))?,
                instance_id: InstanceId::new(id(1))?,
                job_id: JobId::new(id(2))?,
                profile: PROFILE.into(),
                role: model::SessionRole::Incoming,
                device_key: rustusb_core::DeviceKey::native(&format!("usb-{}", "a".repeat(64)))?,
                principal: Some("PRIVATE_PRINCIPAL_SENTINEL".into()),
                state: SessionState::Disconnected,
                evidence: model::Evidence::Released,
                stop_requested: true,
                terminal: true,
                operation_error: Some(model::Failure::State),
                cleanup: Some(model::Cleanup::Verified),
                metrics: model::Metrics {
                    app_rx_bytes: 35,
                    app_tx_bytes: 35,
                    saturated: false,
                    elapsed_millis: 100,
                },
                attempts: vec![model::Attempt {
                    kind: BrokerResourceKind::NativeStub,
                    correlation: BrokerCorrelation {
                        instance_id: InstanceId::new(id(1))?,
                        session_id: SessionId::new(id(3))?,
                        attempt_id: AttemptId::new(id(4))?,
                    },
                    resource_id: Some(ResourceId::new(id(5))?),
                    proof: Some(BrokerCleanupProof::NativeStubAvailable),
                }],
            }],
        })?;
        b.push_items(DiagnosticItems::Audit {
            events: vec![model::AuditEvent {
                sequence: 9,
                actor_uid: 123456789,
                profile: Some(PROFILE.into()),
                job_id: Some(JobId::new(id(2))?),
                session_id: Some(SessionId::new(id(3))?),
                action: model::AuditAction::Finish,
                failure: Some(model::Failure::State),
                state: Some(SessionState::Disconnected),
                evidence: Some(model::Evidence::Released),
                cleanup: Some(model::Cleanup::Verified),
            }],
        })?;
        b.set_outcome(Stage::Agent, SectionOutcome::Complete, None, 3);
        Ok(())
    }
    #[test]
    fn application_aliases_keep_relationships_without_network_or_raw_identity() -> TestResult {
        let mut b = Builder::new();
        populate(&mut b)?;
        let link = LinkHealth {
            protocol: LinkProtocol::FramedV2,
            identity: Some(LinkIdentity {
                connection: 987654321,
                tcp: TcpTuple {
                    local: "192.0.2.14:44111".parse()?,
                    peer: "198.51.100.71:44333".parse()?,
                },
            }),
            sample_sequence: 1,
            elapsed_millis: 100,
            phase: LinkPhase::Ended,
            probe: ProbeState::Stopped,
            last_probe_sequence: Some(1),
            last_probe_enqueued_millis: Some(10),
            last_reply_sequence: Some(1),
            last_reply_millis: Some(11),
            last_round_trip_micros: Some(1000),
            stats: Some(LinkStats {
                app_read_bytes: 35,
                app_write_bytes: 35,
                last_app_read_millis: Some(80),
                last_app_write_millis: Some(80),
                receive_pending_bytes: 0,
                send_capacity_blocked: false,
                transport_write_blocked: false,
            }),
            end: Some(LinkEnd::Protocol),
        };
        b.observe_health(
            &[
                SessionHealth {
                    session_id: SessionId::new(id(3))?,
                    instance_id: InstanceId::new(id(1))?,
                    role: model::SessionRole::Incoming,
                    availability: HealthAvailability::OwnerEnded,
                    link: Some(link),
                },
                SessionHealth {
                    session_id: SessionId::new(id(6))?,
                    instance_id: InstanceId::new(id(1))?,
                    role: model::SessionRole::Import,
                    availability: HealthAvailability::Live,
                    link: Some(LinkHealth::raw_v1()),
                },
            ],
            4,
        )?;
        b.set_outcome(Stage::Health, SectionOutcome::Complete, None, 5);
        let bytes = b.encode(CaptureOutcome::Complete)?;
        let text = std::str::from_utf8(&bytes)?;
        for secret in [
            PROFILE,
            "PRIVATE_PRINCIPAL",
            "192.0.2.14",
            "198.51.100.71",
            "987654321",
            "123456789",
            &"a".repeat(64),
        ] {
            assert!(!text.contains(secret), "{secret}");
        }
        for n in 1..=6 {
            assert!(!text.contains(&id(n)));
        }
        let v: serde_json::Value = serde_json::from_slice(&bytes)?;
        let s = &v["agent"]["sessions"][0];
        let a = &v["agent"]["audit"][0];
        let h = &v["health"]["samples"][0];
        assert_eq!(s["job"], v["agent"]["jobs"][0]["job"]);
        assert_eq!(s["session"], a["session"]);
        assert_eq!(s["session"], s["attempts"][0]["session"]);
        assert_eq!(s["session"], h["session"]);
        assert_eq!(s["operation_error"], "state");
        assert_eq!(s["cleanup"], "verified");
        assert_eq!(h["link"]["end"], "protocol");
        assert_eq!(h["not_in_committed_view"], false);
        assert_eq!(v["health"]["samples"][1]["not_in_committed_view"], true);
        assert!(v["health"]["samples"][1]["link"]["last_round_trip_micros"].is_null());
        assert!(a["application_epoch"].is_null());
        Ok(())
    }
    #[test]
    fn root_log_projection_still_excludes_other_callers_unscoped_denials() -> TestResult {
        let mut b = Builder::new();
        let mut h = header()?;
        h.counts = DiagnosticCounts {
            jobs: 0,
            sessions: 0,
            audit: 0,
        };
        b.begin_agent(&h, PROFILE, 0, 0)?;
        let audit =
            |actor,
             profile: Option<&str>|
             -> std::result::Result<logs::AuditProjection, Box<dyn std::error::Error>> {
                Ok(logs::AuditProjection {
                    application_instance: InstanceId::new(id(1))?,
                    audit_sequence: 1,
                    actor_uid: actor,
                    profile: profile.map(str::to_owned),
                    job_id: None,
                    session_id: None,
                    action: logs::AuditAction::Denied,
                    failure: Some(logs::FailureCategory::Permission),
                    state: None,
                    evidence: None,
                    cleanup: None,
                })
            };
        b.observe_logs(
            &page(vec![
                record(logs::Event::Audit(audit(0, None)?), 1)?,
                record(logs::Event::Audit(audit(777, None)?), 2)?,
                record(logs::Event::Audit(audit(888, Some("FOREIGN_PROFILE"))?), 3)?,
                record(logs::Event::Audit(audit(999, Some(PROFILE))?), 4)?,
                record(
                    logs::Event::Started {
                        pid: 8888,
                        uid: 777,
                    },
                    5,
                )?,
            ]),
            1,
        )?;
        b.set_outcome(Stage::Agent, SectionOutcome::Complete, None, 2);
        b.set_outcome(Stage::Logs, SectionOutcome::Complete, None, 2);
        let v = parse(&b)?;
        assert_eq!(v["logs"]["records"].as_array().ok_or("records")?.len(), 2);
        assert_eq!(v["logs"]["pages"][0]["privacy_filtered_records"], 3);
        let text = String::from_utf8(b.encode(CaptureOutcome::Complete)?)?;
        assert!(!text.contains("FOREIGN_PROFILE"));
        assert_eq!(v["alias_counts"]["uids"], 2);
        Ok(())
    }
    #[test]
    fn failed_begin_still_has_locked_scope_for_independent_logs() -> TestResult {
        let mut b = Builder::new();
        b.set_agent_scope(PROFILE, 0)?;
        b.set_agent_scope(PROFILE, 0)?;
        assert!(b.set_agent_scope("DIFFERENT_PROFILE", 0).is_err());
        assert!(b.set_agent_scope(PROFILE, 1).is_err());
        b.set_outcome(
            Stage::Agent,
            SectionOutcome::Incomplete,
            Some(CollectionFailure::Busy),
            1,
        );
        let mut records = Vec::new();
        for (sequence, uid) in [(1, 0), (2, 888)] {
            records.push(record(
                logs::Event::Audit(logs::AuditProjection {
                    application_instance: InstanceId::new(id(1))?,
                    audit_sequence: sequence,
                    actor_uid: uid,
                    profile: None,
                    job_id: None,
                    session_id: None,
                    action: logs::AuditAction::Denied,
                    failure: Some(logs::FailureCategory::Permission),
                    state: None,
                    evidence: None,
                    cleanup: None,
                }),
                sequence,
            )?);
        }
        b.observe_logs(&page(records), 2)?;
        b.set_outcome(Stage::Logs, SectionOutcome::Complete, None, 3);
        let v = parse(&b)?;
        assert_eq!(v["capture"], "partial");
        assert!(v["agent"].is_null());
        assert_eq!(v["logs"]["records"].as_array().ok_or("records")?.len(), 1);
        assert_eq!(
            v["requested_agent_scope"]["caller"],
            v["logs"]["records"][0]["event"]["actor"]
        );
        Ok(())
    }
    #[test]
    fn committed_counts_and_duplicate_rows_cannot_appear_complete() -> TestResult {
        let mut b = Builder::new();
        b.begin_agent(&header()?, PROFILE, 1000, 0)?;
        b.set_outcome(Stage::Agent, SectionOutcome::Complete, None, 1);
        assert!(!b.summary().complete);
        let mut b = Builder::new();
        populate(&mut b)?;
        assert!(
            b.push_items(DiagnosticItems::Audit {
                events: vec![model::AuditEvent {
                    sequence: 9,
                    actor_uid: 0,
                    profile: Some(PROFILE.into()),
                    job_id: None,
                    session_id: None,
                    action: model::AuditAction::Observe,
                    failure: None,
                    state: None,
                    evidence: None,
                    cleanup: None
                }]
            })
            .is_err()
        );
        b.set_outcome(Stage::Agent, SectionOutcome::Complete, None, 2);
        assert!(!b.summary().complete);
        Ok(())
    }
}

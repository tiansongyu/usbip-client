use super::*;
use crate::{
    config::{Operator, Profile},
    model::{Evidence, JobState, Metrics, SessionRole},
};
use rustusb_core::{
    DeviceKey, Error, Result, SessionState, VirtualProfile,
    application::{JobId, SessionId},
};
use std::collections::BTreeSet;

fn id(value: u64) -> String {
    format!("00000000-0000-4000-8000-{value:012x}")
}
fn ok<T>(value: DiagnosticResult<T>) -> Result<T> {
    value.map_err(|_| Error::State("diagnostic fixture unexpected failure".into()))
}
fn manifest() -> Manifest {
    Manifest {
        schema_version: 1,
        broker_uid: 2000,
        helper_socket: "/run/rustusb/helper.sock".into(),
        control_directory: "/run/rustusb/agent".into(),
        data_directory: "/var/lib/rustusb/agent".into(),
        profiles: ["selected", "other"]
            .into_iter()
            .map(|id| Profile {
                id: id.into(),
                backend: Backend::Client,
                config: "/etc/rustusb/profile.json".into(),
            })
            .collect(),
        operators: vec![
            Operator {
                uid: 1000,
                profiles: BTreeSet::from(["selected".into(), "other".into()]),
                actions: BTreeSet::from([Action::Read, Action::Audit]),
            },
            Operator {
                uid: 1001,
                profiles: BTreeSet::from(["selected".into()]),
                actions: BTreeSet::from([Action::Read]),
            },
            Operator {
                uid: 1002,
                profiles: BTreeSet::from(["selected".into()]),
                actions: BTreeSet::from([Action::Audit]),
            },
        ],
    }
}
fn state(rows: u64) -> Result<State> {
    let instance = InstanceId::new(id(1))?;
    let mut state = State::default();
    for n in 0..rows {
        let profile = if n % 2 == 0 { "selected" } else { "other" };
        let job = Job {
            id: JobId::new(id(100 + n))?,
            instance_id: instance.clone(),
            profile: profile.into(),
            operator_uid: 1000,
            state: JobState::Finished,
            stop_requested: false,
            operation_error: None,
        };
        state.sessions.push(Session {
            id: SessionId::new(id(10_000 + n))?,
            instance_id: instance.clone(),
            job_id: job.id.clone(),
            profile: profile.into(),
            role: SessionRole::Import,
            device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
            principal: None,
            state: SessionState::Connecting,
            evidence: Evidence::Intent,
            stop_requested: false,
            terminal: false,
            operation_error: None,
            cleanup: None,
            metrics: Metrics::default(),
            attempts: vec![],
        });
        state.jobs.push(job);
        state.audit(
            1000,
            Some(profile.into()),
            None,
            None,
            AuditAction::Observe,
            None,
        )?;
    }
    state.initialize_legacy_links();
    state.validate()?;
    Ok(state)
}
fn registry() -> Result<Registry> {
    Ok(Registry::new(InstanceId::new(id(2))?, state(70)?))
}
fn response(registry: Option<&Registry>, uid: u32, request: Request) -> Result<Response> {
    respond(registry, &manifest(), uid, &request)
        .ok_or_else(|| Error::State("fixture non-diagnostic request".into()))?
}

#[test]
fn coherent_complete_filtered_pages_survive_new_commits() -> Result<()> {
    let registry = registry()?;
    let now = Instant::now();
    let header = ok(registry.begin(1000, "selected", Backend::Client, now))?;
    assert_eq!(
        header.counts,
        DiagnosticCounts {
            jobs: 35,
            sessions: 35,
            audit: 35
        }
    );
    registry.publish(state(4)?);
    for kind in [
        DiagnosticKind::Jobs,
        DiagnosticKind::Sessions,
        DiagnosticKind::Audit,
    ] {
        let snapshot = ok(registry.pin(1000, "selected", &header.snapshot, now))?;
        let mut offset = 0;
        let mut seen = Vec::new();
        loop {
            let page = ok(snapshot.page(kind, offset))?;
            assert_eq!(page.committed_sequence, 70);
            assert_eq!(page.total, 35);
            assert_eq!(page.snapshot, header.snapshot);
            let values: Vec<String> = match page.items {
                DiagnosticItems::Jobs { jobs } => jobs
                    .into_iter()
                    .map(|j| {
                        assert_eq!(j.profile, "selected");
                        j.id.as_str().to_owned()
                    })
                    .collect(),
                DiagnosticItems::Sessions { sessions } => sessions
                    .into_iter()
                    .map(|s| {
                        assert_eq!(s.profile, "selected");
                        s.id.as_str().to_owned()
                    })
                    .collect(),
                DiagnosticItems::Audit { events } => events
                    .into_iter()
                    .map(|e| {
                        assert_eq!(e.profile.as_deref(), Some("selected"));
                        e.sequence.to_string()
                    })
                    .collect(),
            };
            assert!(!values.is_empty() && values.len() <= 16);
            seen.extend(values);
            match page.next_after {
                Some(next) => {
                    assert!(next > offset);
                    offset = next;
                }
                None => break,
            }
        }
        assert_eq!(seen.len(), 35);
        assert_eq!(seen.iter().collect::<BTreeSet<_>>().len(), 35);
        assert!(ok(snapshot.page(kind, 35))?.next_after.is_none());
        assert!(matches!(
            snapshot.page(kind, 36),
            Err(DiagnosticFailure::InvalidCursor)
        ));
    }
    ok(registry.end(1000, "selected", &header.snapshot, now))?;
    assert_eq!(
        ok(registry.begin(1000, "selected", Backend::Client, now))?.committed_sequence,
        4
    );
    Ok(())
}

#[test]
fn actual_snapshot_lifetime_keeps_global_and_uid_quota_after_end_and_expiry() -> Result<()> {
    let registry = registry()?;
    let now = Instant::now();
    let headers: Vec<_> = (1000..1004)
        .map(|uid| ok(registry.begin(uid, "selected", Backend::Client, now)))
        .collect::<Result<_>>()?;
    let pinned: Vec<_> = headers
        .iter()
        .enumerate()
        .map(|(index, h)| ok(registry.pin(1000 + index as u32, "selected", &h.snapshot, now)))
        .collect::<Result<_>>()?;
    assert!(matches!(
        registry.begin(2000, "selected", Backend::Client, now),
        Err(DiagnosticFailure::Busy)
    ));
    for (index, header) in headers.iter().enumerate() {
        ok(registry.end(1000 + index as u32, "selected", &header.snapshot, now))?;
    }
    assert_eq!(registry.quota.available_permits(), 0);
    assert!(matches!(
        registry.begin(1000, "selected", Backend::Client, now),
        Err(DiagnosticFailure::Busy)
    ));
    assert!(matches!(
        registry.begin(2000, "selected", Backend::Client, now),
        Err(DiagnosticFailure::Busy)
    ));
    drop(pinned);
    let header = ok(registry.begin(1000, "selected", Backend::Client, now))?;
    let pinned = ok(registry.pin(1000, "selected", &header.snapshot, now))?;
    assert!(matches!(
        registry.pin(1000, "selected", &header.snapshot, now + LIFETIME),
        Err(DiagnosticFailure::Expired)
    ));
    assert!(matches!(
        registry.begin(1000, "selected", Backend::Client, now + LIFETIME),
        Err(DiagnosticFailure::Busy)
    ));
    assert_eq!(ok(pinned.page(DiagnosticKind::Jobs, 0))?.total, 35);
    drop(pinned);
    let next = ok(registry.begin(1000, "selected", Backend::Client, now + LIFETIME))?;
    assert!(next.snapshot.number > header.snapshot.number);
    Ok(())
}

#[test]
fn permissions_precede_registry_and_foreign_end_is_indistinguishable() -> Result<()> {
    let registry = registry()?;
    for uid in [1001, 1002, 9999] {
        assert!(matches!(
            response(
                None,
                uid,
                Request::BeginDiagnostic {
                    profile: "selected".into()
                }
            ),
            Err(Error::Permission(_))
        ));
    }
    assert!(matches!(
        response(
            None,
            0,
            Request::BeginDiagnostic {
                profile: "unknown".into()
            }
        ),
        Err(Error::Permission(_))
    ));
    assert!(matches!(
        response(
            None,
            1000,
            Request::BeginDiagnostic {
                profile: "selected".into()
            }
        )?,
        Response::DiagnosticError {
            code: DiagnosticFailure::Unavailable
        }
    ));
    let header = match response(
        Some(&registry),
        1000,
        Request::BeginDiagnostic {
            profile: "selected".into(),
        },
    )? {
        Response::DiagnosticStarted { header } => header,
        _ => return Err(Error::State("fixture Begin".into())),
    };
    for (uid, profile) in [(0, "selected"), (1000, "other")] {
        assert!(matches!(
            response(
                Some(&registry),
                uid,
                Request::EndDiagnostic {
                    profile: profile.into(),
                    snapshot: header.snapshot.clone()
                }
            )?,
            Response::DiagnosticEnded {}
        ));
        assert!(matches!(
            response(
                Some(&registry),
                uid,
                Request::DiagnosticPage {
                    profile: profile.into(),
                    snapshot: header.snapshot.clone(),
                    kind: DiagnosticKind::Jobs,
                    after: u16::MAX
                }
            )?,
            Response::DiagnosticError {
                code: DiagnosticFailure::Expired
            }
        ));
    }
    assert!(
        registry
            .pin(1000, "selected", &header.snapshot, Instant::now())
            .is_ok()
    );
    for _ in 0..2 {
        assert!(matches!(
            response(
                Some(&registry),
                1000,
                Request::EndDiagnostic {
                    profile: "selected".into(),
                    snapshot: header.snapshot.clone()
                }
            )?,
            Response::DiagnosticEnded {}
        ));
    }
    Ok(())
}

#[test]
fn audit_filter_is_exact_profile_or_own_denial_even_for_root() -> Result<()> {
    let mut state = state(2)?;
    for (uid, action) in [
        (1000, AuditAction::Denied),
        (1000, AuditAction::Recovery),
        (1001, AuditAction::Denied),
        (0, AuditAction::Denied),
        (0, AuditAction::Recovery),
    ] {
        state.audit(uid, None, None, None, action, None)?;
    }
    let registry = Registry::new(InstanceId::new(id(2))?, state);
    for (uid, own_sequence) in [(1000, 3), (0, 6)] {
        let now = Instant::now();
        let header = ok(registry.begin(uid, "selected", Backend::Client, now))?;
        assert_eq!(header.counts.audit, 2);
        let page = ok(ok(registry.pin(uid, "selected", &header.snapshot, now))?
            .page(DiagnosticKind::Audit, 0))?;
        let DiagnosticItems::Audit { events } = page.items else {
            return Err(Error::State("fixture audit page".into()));
        };
        assert_eq!(
            events.iter().map(|e| e.sequence).collect::<Vec<_>>(),
            vec![1, own_sequence]
        );
    }
    Ok(())
}

#[test]
fn counter_overflow_poison_and_strict_nullable_are_explicit() -> Result<()> {
    let registry = registry()?;
    registry
        .inner
        .lock()
        .map_err(|_| Error::State("fixture lock".into()))?
        .next_number = u64::MAX;
    assert!(matches!(
        registry.begin(1000, "selected", Backend::Client, Instant::now()),
        Err(DiagnosticFailure::Unavailable)
    ));
    let token = DiagnosticToken {
        instance_id: InstanceId::new(id(2))?,
        number: NonZeroU64::MIN,
    };
    let mut value = serde_json::to_value(DiagnosticPage {
        snapshot: token,
        committed_sequence: 0,
        after: 0,
        total: 0,
        next_after: None,
        items: DiagnosticItems::Jobs { jobs: vec![] },
    })
    .map_err(|_| Error::State("fixture encoding".into()))?;
    let object = value
        .as_object_mut()
        .ok_or_else(|| Error::State("fixture shape".into()))?;
    object.remove("next_after");
    assert!(serde_json::from_value::<DiagnosticPage>(value).is_err());
    assert!(
        serde_json::from_str::<DiagnosticToken>(&format!(
            "{{\"instance_id\":\"{}\",\"number\":0}}",
            id(2)
        ))
        .is_err()
    );
    assert!(
        serde_json::from_str::<DiagnosticToken>(&format!(
            "{{\"instance_id\":\"{}\",\"number\":1,\"number\":2}}",
            id(2)
        ))
        .is_err()
    );
    let registry = Arc::new(registry);
    let other = registry.clone();
    let thread = std::thread::spawn(move || {
        if let Ok(_guard) = other.inner.lock() {
            std::panic::resume_unwind(Box::new("diagnostic fixture poison"));
        }
    });
    assert!(thread.join().is_err());
    registry.publish(State::default());
    assert!(matches!(
        registry.begin(0, "selected", Backend::Client, Instant::now()),
        Err(DiagnosticFailure::Unavailable)
    ));
    Ok(())
}

#[test]
fn row_and_frame_budgets_reject_oversized_rows_without_false_empty_end() -> Result<()> {
    let a = "x".repeat(40_000);
    let b = "y".repeat(40_000);
    let huge = "z".repeat(60_000);
    assert_eq!(ok(page_rows(&[&a, &b], 0))?.len(), 1);
    assert!(matches!(
        page_rows(&[&huge], 0),
        Err(DiagnosticFailure::EncodingLimit)
    ));
    assert!(ok(page_rows(&[&huge], 1))?.is_empty());
    let mut retained = state(512)?;
    for _ in 0..512 {
        retained.audit(
            1000,
            Some("selected".into()),
            None,
            None,
            AuditAction::Observe,
            None,
        )?;
    }
    for (index, session) in retained
        .sessions
        .iter_mut()
        .filter(|session| session.profile == "selected")
        .take(16)
        .enumerate()
    {
        for attempt in 0..32 {
            session.attempts.push(crate::model::Attempt {
                kind: rustusb_core::application::BrokerResourceKind::NativeImport,
                correlation: rustusb_core::application::BrokerCorrelation {
                    instance_id: session.instance_id.clone(),
                    session_id: session.id.clone(),
                    attempt_id: rustusb_core::application::AttemptId::new(id(1_000_000
                        + index as u64 * 32
                        + attempt))?,
                },
                resource_id: None,
                proof: None,
            });
        }
    }
    retained.validate()?;
    let registry = Registry::new(InstanceId::new(id(2))?, retained);
    let now = Instant::now();
    let header = ok(registry.begin(1000, "selected", Backend::Client, now))?;
    let snapshot = ok(registry.pin(1000, "selected", &header.snapshot, now))?;
    for kind in [
        DiagnosticKind::Jobs,
        DiagnosticKind::Sessions,
        DiagnosticKind::Audit,
    ] {
        let mut after = 0;
        loop {
            let page = ok(snapshot.page(kind, after))?;
            let next = page.next_after;
            if kind == DiagnosticKind::Sessions && after == 0 {
                let DiagnosticItems::Sessions { sessions } = &page.items else {
                    return Err(Error::State("fixture session section".into()));
                };
                assert!(
                    !sessions.is_empty() && sessions.len() < 16,
                    "actual complete attempt rows must reduce the page by bytes"
                );
                assert!(sessions.iter().all(|session| session.attempts.len() == 32));
            }
            let envelope = crate::rpc::ResponseEnvelope {
                schema_version: 1,
                journal_healthy: true,
                response: Response::DiagnosticPage { page },
            };
            assert!(
                serde_json::to_vec(&envelope)
                    .map_err(|_| Error::State("fixture encoding".into()))?
                    .len()
                    <= 65_536
            );
            match next {
                Some(next) => {
                    assert!(next > after);
                    after = next;
                }
                None => break,
            }
        }
    }
    Ok(())
}

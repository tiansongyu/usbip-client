//! Deterministic runtime/store regressions on a real temporary filesystem.
//!
//! Session/attempt fixtures are synthetic recorder inputs. These tests do not
//! load TLS credentials, contact a helper, claim USB, or prove kernel cleanup.

use super::*;
use crate::config::{Manifest, Operator, Profile};
use rustusb_core::{DeviceKey, VirtualProfile};
use std::{collections::BTreeSet, os::unix::fs::PermissionsExt, path::PathBuf};

const PROFILE: &str = "fixed-profile";

#[path = "runtime_logging_tests.rs"]
mod logging;

// Real production stop delivery and store await, with ordinary owned Unix
// endpoints as resource witnesses. This is not a helper/kernel USB test.
#[tokio::test]
async fn shutdown_fanout_reaches_owned_stream_before_stalled_store_ack() -> Result<()> {
    use tokio::io::AsyncReadExt;
    let mut fixture = Fixture::new()?;
    let id = JobId::new(uuid(101))?;
    let (job_stop, mut job_receiver) = watch::channel(false);
    let controls = fixture.runtime.controls.clone();
    controls.lock().map_err(|_| lock_error())?.register_job(
        id.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: id,
            stop: job_stop.clone(),
            stoppable: true,
        },
    );
    let (owned, mut observer) = tokio::net::UnixStream::pair()?;
    let owner = tokio::spawn(async move {
        crate::context::stopped(&mut job_receiver).await;
        drop(owned);
    });
    let store = fixture.runtime.store.clone();
    let (locked, lock_received) = oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let blocker = tokio::task::spawn_blocking(move || {
        let _guard = store.lock().map_err(|_| lock_error())?;
        locked
            .send(())
            .map_err(|_| Error::State("shutdown lock observer".into()))?;
        released
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| Error::Timeout("shutdown fixture release".into()))?;
        Ok::<_, Error>(())
    });
    lock_received
        .await
        .map_err(|_| Error::State("shutdown fixture lock".into()))?;
    let next = fixture.runtime.state.clone();
    let recording = fixture.runtime.persist(next);
    tokio::pin!(recording);
    assert!(
        tokio::time::timeout(Duration::from_millis(20), &mut recording)
            .await
            .is_err()
    );
    let (shutdown, queued) = watch::channel(false);
    crate::ipc::request_shutdown(&controls, &shutdown)?;
    assert!(*queued.borrow());
    let before_ack =
        tokio::time::timeout(Duration::from_millis(100), observer.read(&mut [0])).await;
    let closed_before_ack = matches!(before_ack, Ok(Ok(0)));
    assert!(
        tokio::time::timeout(Duration::from_millis(20), &mut recording)
            .await
            .is_err()
    );
    // Always release the real store gate, even for the original failing product.
    // This late fixture teardown is explicitly not the pre-ACK release proof.
    release
        .send(())
        .map_err(|_| Error::State("shutdown blocker abandoned".into()))?;
    recording.await?;
    blocker
        .await
        .map_err(|_| Error::State("shutdown blocker task".into()))??;
    job_stop.send_replace(true);
    owner
        .await
        .map_err(|_| Error::State("shutdown owner task".into()))?;
    assert!(
        closed_before_ack,
        "actual owned endpoint remained open while journal ACK was stalled"
    );
    Ok(())
}

#[test]
fn shutdown_late_registration_inherits_the_same_sticky_stop() -> Result<()> {
    let controls = Mutex::new(Controls::default());
    let (shutdown, _queued) = watch::channel(false);
    crate::ipc::request_shutdown(&controls, &shutdown)?;
    let id = JobId::new(uuid(102))?;
    let (job_stop, job_receiver) = watch::channel(false);
    let (session_stop, session_receiver) = watch::channel(false);
    let mut controls = controls.lock().map_err(|_| lock_error())?;
    controls.register_job(
        id.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: id.clone(),
            stop: job_stop,
            stoppable: true,
        },
    );
    controls.register_session(
        SessionId::new(uuid(103))?,
        Control {
            profile: PROFILE.into(),
            job_id: id,
            stop: session_stop,
            stoppable: true,
        },
    );
    assert!(*job_receiver.borrow(), "late job missed service shutdown");
    assert!(
        *session_receiver.borrow(),
        "late session missed service shutdown"
    );
    Ok(())
}

#[test]
fn shutdown_late_session_inherits_its_already_stopped_job() -> Result<()> {
    let fixture = Fixture::new()?;
    let id = JobId::new(uuid(104))?;
    let (job_stop, _job_receiver) = watch::channel(false);
    let (session_stop, session_receiver) = watch::channel(false);
    let mut controls = Controls::default();
    controls.register_job(
        id.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: id.clone(),
            stop: job_stop,
            stoppable: true,
        },
    );
    assert!(controls.request(
        &fixture.runtime.config,
        operator_uid(),
        &Request::StopJob { job_id: id.clone() }
    )?);
    controls.register_session(
        SessionId::new(uuid(105))?,
        Control {
            profile: PROFILE.into(),
            job_id: id,
            stop: session_stop,
            stoppable: true,
        },
    );
    assert!(
        *session_receiver.borrow(),
        "late session missed authorized job stop"
    );
    Ok(())
}

#[test]
fn shutdown_poison_still_fans_out_and_returns_an_explicit_error() -> Result<()> {
    let controls = Arc::new(Mutex::new(Controls::default()));
    let (owner_stop, owner_receiver) = watch::channel(false);
    let id = JobId::new(uuid(106))?;
    controls.lock().map_err(|_| lock_error())?.register_job(
        id.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: id,
            stop: owner_stop,
            stoppable: true,
        },
    );
    let poisoned = controls.clone();
    let unwound = std::thread::spawn(move || -> Result<()> {
        let _guard = poisoned.lock().map_err(|_| lock_error())?;
        // Deliberately unwind with the guard held; no application owner panics.
        std::panic::resume_unwind(Box::new("intentional controls poison fixture"));
    })
    .join();
    assert!(unwound.is_err());
    let (shutdown, queued) = watch::channel(false);
    assert!(matches!(
        crate::ipc::request_shutdown(&controls, &shutdown),
        Err(Error::State(_))
    ));
    assert!(*queued.borrow());
    assert!(*owner_receiver.borrow());
    Ok(())
}

fn uuid(value: u64) -> String {
    format!("00000000-0000-0000-0000-{value:012x}")
}

fn operator_uid() -> u32 {
    rustix::process::geteuid().as_raw()
}

fn other_uid() -> u32 {
    operator_uid() ^ 0x4000_0000
}

struct Fixture {
    root: tempfile::TempDir,
    path: PathBuf,
    runtime: Runtime,
}

impl Fixture {
    fn new() -> Result<Self> {
        let root = tempfile::tempdir()?;
        std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700))?;
        let path = root.path().join("data");
        std::fs::create_dir(&path)?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))?;
        let store = Store::fixture(&path, root.path())?;
        let manifest = Manifest {
            schema_version: 1,
            // No product loader or broker role is exercised here. Root-run
            // unit tests still need a nonzero manifest UID; operators below
            // always use the real test process UID.
            broker_uid: operator_uid().max(1),
            helper_socket: "/run/rustusb/unused-test-helper.sock".into(),
            control_directory: root.path().join("control"),
            data_directory: path.clone(),
            profiles: vec![Profile {
                id: PROFILE.into(),
                backend: Backend::Client,
                config: "/etc/rustusb/unused-test-client.json".into(),
            }],
            operators: vec![
                Operator {
                    uid: operator_uid(),
                    profiles: BTreeSet::from([PROFILE.into()]),
                    actions: BTreeSet::from([
                        Action::Read,
                        Action::Start,
                        Action::Stop,
                        Action::Audit,
                    ]),
                },
                Operator {
                    uid: other_uid(),
                    profiles: BTreeSet::from([PROFILE.into()]),
                    actions: BTreeSet::from([Action::Read, Action::Start]),
                },
            ],
        };
        manifest.validate()?;
        let (fact_sender, facts) = mpsc::channel(64);
        let runtime = Runtime {
            config: Arc::new(LoadedConfig {
                manifest,
                credentials: BTreeMap::new(),
            }),
            instance: InstanceId::new(uuid(1))?,
            state: State::default(),
            store: Arc::new(Mutex::new(store)),
            healthy: true,
            controls: Arc::new(Mutex::new(Controls::default())),
            facts,
            fact_sender,
            workers: JoinSet::new(),
            recoveries: JoinSet::new(),
            recovery_cursor: 0,
            logging: None,
        };
        Ok(Self {
            root,
            path,
            runtime,
        })
    }

    fn assert_committed(&self) -> Result<()> {
        let memory = serde_json::to_value(&self.runtime.state)
            .map_err(|_| Error::State("fixture runtime serialization".into()))?;
        let stored = {
            let store = self.runtime.store.lock().map_err(|_| lock_error())?;
            serde_json::to_value(&store.state)
                .map_err(|_| Error::State("fixture store serialization".into()))?
        };
        let disk: serde_json::Value =
            serde_json::from_slice(&std::fs::read(self.path.join("state.json"))?)
                .map_err(|_| Error::State("fixture disk decoding".into()))?;
        assert_eq!(
            memory, stored,
            "runtime and canonical committed store differ"
        );
        assert_eq!(memory, disk, "runtime and real persisted JSON differ");
        self.runtime.state.validate()
    }
}

fn job(value: u64, instance: &InstanceId, state: JobState) -> Result<Job> {
    Ok(Job {
        id: JobId::new(uuid(100 + value))?,
        instance_id: instance.clone(),
        profile: PROFILE.into(),
        operator_uid: operator_uid(),
        state,
        stop_requested: false,
        operation_error: None,
    })
}

fn session(value: u64, job: &Job, terminal: bool) -> Result<Session> {
    Ok(Session {
        id: SessionId::new(uuid(10_000 + value))?,
        instance_id: job.instance_id.clone(),
        job_id: job.id.clone(),
        profile: PROFILE.into(),
        role: SessionRole::Import,
        device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
        principal: None,
        state: if terminal {
            SessionState::Disconnected
        } else {
            SessionState::Connecting
        },
        evidence: if terminal {
            Evidence::Released
        } else {
            Evidence::Intent
        },
        stop_requested: false,
        terminal,
        operation_error: None,
        cleanup: terminal.then_some(Cleanup::Verified),
        metrics: Metrics::default(),
        attempts: vec![],
    })
}

fn request(value: u64, job: &Job) -> Result<RequestRecord> {
    Ok(RequestRecord {
        id: RequestId::new(uuid(100_000 + value))?,
        uid: operator_uid(),
        profile: PROFILE.into(),
        job_id: job.id.clone(),
    })
}

#[tokio::test]
async fn canonical_retention_matches_memory_store_and_reopened_disk() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let owner = job(1, &fixture.runtime.instance, JobState::Running)?;
    let mut next = State::default();
    next.jobs.push(owner.clone());
    // The recorder schema permits retained request records to reference an
    // existing job. This directly exercises both independent retention limits.
    for value in 0..300 {
        next.sessions.push(session(value, &owner, true)?);
        next.requests.push(request(value, &owner)?);
    }
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    assert_eq!(fixture.runtime.state.sessions.len(), 128);
    assert_eq!(fixture.runtime.state.requests.len(), 256);
    assert!(fixture.runtime.healthy);
    fixture.assert_committed()?;
    let mut next = fixture.runtime.state.clone();
    next.audit(
        operator_uid(),
        Some(PROFILE.into()),
        Some(owner.id),
        None,
        AuditAction::Observe,
        None,
    )?;
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    fixture.assert_committed()?;
    let expected = serde_json::to_value(&fixture.runtime.state)
        .map_err(|_| Error::State("fixture expected state".into()))?;
    let Fixture {
        root,
        path,
        runtime,
    } = fixture;
    drop(runtime);
    let reopened = Store::fixture(&path, root.path())?;
    let actual = serde_json::to_value(&reopened.state)
        .map_err(|_| Error::State("fixture reopened state".into()))?;
    assert_eq!(expected, actual);
    Ok(())
}

#[tokio::test]
async fn finished_job_with_retained_child_is_not_pruned_into_an_orphan() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let oldest = job(1, &fixture.runtime.instance, JobState::Finished)?;
    let mut next = State::default();
    next.jobs.push(oldest.clone());
    for value in 2..=129 {
        next.jobs
            .push(job(value, &fixture.runtime.instance, JobState::Finished)?);
    }
    // A long-lived job can be the oldest registration while its most recent
    // device terminal is still retained. Age of job and child are independent.
    let child = session(9_000, &oldest, true)?;
    next.sessions.push(child.clone());
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    assert_eq!(fixture.runtime.state.jobs.len(), 128);
    assert!(
        fixture
            .runtime
            .state
            .jobs
            .iter()
            .any(|job| job.id == oldest.id)
    );
    assert!(
        fixture
            .runtime
            .state
            .sessions
            .iter()
            .any(|session| session.id == child.id)
    );
    fixture.assert_committed()?;
    Ok(())
}

#[tokio::test]
async fn admission_capacity_refusal_keeps_journal_healthy_and_allows_finish() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let owner = job(1, &fixture.runtime.instance, JobState::Running)?;
    let mut next = State::default();
    next.jobs.push(owner.clone());
    let mut value = 0;
    loop {
        let size = serde_json::to_vec(&next)
            .map_err(|_| Error::State("fixture size encoding".into()))?
            .len();
        if size > crate::store::MAX_BYTES / 2 {
            break;
        }
        let mut child = session(value, &owner, false)?;
        for offset in 0..31 {
            child.attempts.push(Attempt {
                kind: BrokerResourceKind::NativeImport,
                correlation: BrokerCorrelation {
                    instance_id: fixture.runtime.instance.clone(),
                    session_id: child.id.clone(),
                    attempt_id: AttemptId::new(uuid(1_000_000 + value * 32 + offset))?,
                },
                resource_id: None,
                proof: None,
            });
        }
        next.sessions.push(child);
        value += 1;
        assert!(
            value < 512,
            "fixture must hit byte admission limit before record count"
        );
    }
    next.initialize_legacy_links();
    assert!(next.clone().preflight(true).is_err());
    next.preflight(false)?;
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    let target = fixture
        .runtime
        .state
        .sessions
        .first()
        .ok_or_else(|| Error::State("fixture target absent".into()))?
        .id
        .clone();
    let before = std::fs::read(fixture.path.join("state.json"))?;
    // This is the real runtime attempt admission path: it has room in its
    // per-session attempt count, but not in the reserved admission byte budget.
    assert!(
        fixture
            .runtime
            .attempt(target.clone(), BrokerResourceKind::NativeImport)
            .await
            .is_err()
    );
    assert!(fixture.runtime.healthy);
    assert_eq!(before, std::fs::read(fixture.path.join("state.json"))?);
    let final_metrics = Metrics {
        app_rx_bytes: 7,
        app_tx_bytes: 5,
        elapsed_millis: 12,
        saturated: false,
    };
    fixture
        .runtime
        .finish(
            target.clone(),
            Some(Failure::Io),
            Cleanup::Verified,
            final_metrics,
        )
        .await?;
    let completed = fixture
        .runtime
        .state
        .sessions
        .iter()
        .find(|session| session.id == target)
        .ok_or_else(|| Error::State("fixture completed target absent".into()))?;
    assert!(completed.terminal);
    assert_eq!(completed.cleanup, Some(Cleanup::Verified));
    assert_eq!(completed.operation_error, Some(Failure::Io));
    assert!(fixture.runtime.healthy);
    fixture.assert_committed()?;
    Ok(())
}

#[tokio::test]
async fn explicit_retry_never_starts_unknown_or_expired_requests() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let owner = job(1, &fixture.runtime.instance, JobState::Finished)?;
    let mut next = State::default();
    next.jobs.push(owner.clone());
    for value in 0..257 {
        next.requests.push(request(value, &owner)?);
    }
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    let expired = request(0, &owner)?.id;
    let retained = request(256, &owner)?.id;
    let unknown = RequestId::new(uuid(999_999))?;
    for id in [expired, unknown] {
        let response = fixture
            .runtime
            .dispatch(
                operator_uid(),
                Request::RetryStart {
                    profile: PROFILE.into(),
                    request_id: id.clone(),
                },
            )
            .await?;
        match response {
            Response::RequestExpired { request_id } => assert_eq!(request_id, id),
            _ => {
                return Err(Error::State(
                    "unknown retry did not return explicit expiry".into(),
                ));
            }
        }
    }
    match fixture
        .runtime
        .dispatch(
            operator_uid(),
            Request::RetryStart {
                profile: PROFILE.into(),
                request_id: retained.clone(),
            },
        )
        .await?
    {
        Response::Started {
            job,
            replayed: true,
        } => assert_eq!(job.id, owner.id),
        _ => {
            return Err(Error::State(
                "retained retry did not replay its original job".into(),
            ));
        }
    }
    assert!(matches!(
        fixture
            .runtime
            .dispatch(
                other_uid(),
                Request::RetryStart {
                    profile: PROFILE.into(),
                    request_id: retained
                }
            )
            .await,
        Err(Error::Permission(_))
    ));
    assert!(fixture.runtime.workers.is_empty());
    assert!(
        fixture
            .runtime
            .controls
            .lock()
            .map_err(|_| lock_error())?
            .jobs
            .is_empty()
    );
    assert_eq!(fixture.runtime.state.jobs.len(), 1);
    fixture.assert_committed()?;
    Ok(())
}

#[tokio::test]
async fn unauthorized_stop_does_not_signal_and_is_durably_audited() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let owner = job(1, &fixture.runtime.instance, JobState::Running)?;
    let child = session(1, &owner, false)?;
    let mut next = State::default();
    next.jobs.push(owner.clone());
    next.sessions.push(child.clone());
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    let (job_stop, observed_job) = watch::channel(false);
    let (session_stop, observed_session) = watch::channel(false);
    {
        let mut controls = fixture.runtime.controls.lock().map_err(|_| lock_error())?;
        controls.jobs.insert(
            owner.id.clone(),
            Control {
                profile: PROFILE.into(),
                job_id: owner.id.clone(),
                stop: job_stop,
                stoppable: true,
            },
        );
        controls.sessions.insert(
            child.id.clone(),
            Control {
                profile: PROFILE.into(),
                job_id: owner.id.clone(),
                stop: session_stop,
                stoppable: true,
            },
        );
        assert!(matches!(
            controls.request(
                &fixture.runtime.config,
                other_uid(),
                &Request::StopJob {
                    job_id: owner.id.clone()
                }
            ),
            Err(Error::Permission(_))
        ));
    }
    assert!(!*observed_job.borrow());
    assert!(!*observed_session.borrow());
    let response = fixture
        .runtime
        .command(other_uid(), Request::StopJob { job_id: owner.id })
        .await;
    assert!(matches!(
        response,
        Response::Error {
            code: Failure::Permission
        }
    ));
    assert!(!*observed_job.borrow());
    assert!(!*observed_session.borrow());
    assert!(!fixture.runtime.state.sessions[0].stop_requested);
    let denied = fixture
        .runtime
        .state
        .audit
        .last()
        .ok_or_else(|| Error::State("denial audit absent".into()))?;
    assert_eq!(denied.action, AuditAction::Denied);
    assert_eq!(denied.actor_uid, other_uid());
    assert_eq!(denied.failure, Some(Failure::Permission));
    fixture.assert_committed()?;
    Ok(())
}

#[tokio::test]
async fn verified_finish_from_error_audits_recovering_before_disconnected() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let owner = job(1, &fixture.runtime.instance, JobState::Running)?;
    let child = session(1, &owner, false)?;
    let mut next = State::default();
    next.jobs.push(owner);
    next.sessions.push(child.clone());
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    fixture
        .runtime
        .observe(
            child.id.clone(),
            SessionState::Error,
            Evidence::Unconfirmed,
            None,
        )
        .await?;
    fixture
        .runtime
        .finish(
            child.id.clone(),
            Some(Failure::Io),
            Cleanup::Verified,
            Metrics::default(),
        )
        .await?;
    let audit = &fixture.runtime.state.audit;
    assert_eq!(audit.len(), 3);
    assert_eq!(
        (audit[0].action, audit[0].state, audit[0].evidence),
        (
            AuditAction::Observe,
            Some(SessionState::Error),
            Some(Evidence::Unconfirmed)
        )
    );
    assert_eq!(
        (audit[1].action, audit[1].state, audit[1].evidence),
        (
            AuditAction::Observe,
            Some(SessionState::Recovering),
            Some(Evidence::Closing)
        )
    );
    assert_eq!(
        (
            audit[2].action,
            audit[2].state,
            audit[2].evidence,
            audit[2].cleanup
        ),
        (
            AuditAction::Finish,
            Some(SessionState::Disconnected),
            Some(Evidence::Released),
            Some(Cleanup::Verified)
        )
    );
    assert!(
        audit
            .windows(2)
            .all(|pair| pair[0].sequence < pair[1].sequence)
    );
    assert_eq!(audit[1].failure, Some(Failure::Io));
    assert_eq!(audit[2].failure, Some(Failure::Io));
    let actual = &fixture.runtime.state.sessions[0];
    assert!(actual.terminal);
    assert_eq!(actual.operation_error, Some(Failure::Io));
    assert_eq!(actual.cleanup, Some(Cleanup::Verified));
    // Exact repeated terminal reports are idempotent, without duplicate audit.
    fixture
        .runtime
        .finish(
            child.id,
            Some(Failure::Io),
            Cleanup::Verified,
            Metrics::default(),
        )
        .await?;
    assert_eq!(fixture.runtime.state.audit.len(), 3);
    fixture.assert_committed()?;
    Ok(())
}

#[test]
fn legacy_state_migrates_strictly_without_changing_public_session_or_metrics() -> Result<()> {
    let instance = InstanceId::new(uuid(1))?;
    let owner = job(1, &instance, JobState::Finished)?;
    let child = session(1, &owner, true)?;
    let old_session =
        serde_json::to_value(&child).map_err(|_| Error::State("fixture JSON".into()))?;
    let legacy = serde_json::json!({"schema_version":1,"sequence":0,"jobs":[owner],"sessions":[child],"audit":[],"requests":[]});
    let encode = |value: &serde_json::Value| {
        serde_json::to_vec(value).map_err(|_| Error::State("fixture JSON".into()))
    };
    let migrated = State::decode(&encode(&legacy)?)?;
    assert_eq!(migrated.schema_version, 2);
    assert_eq!(
        serde_json::to_value(&migrated.sessions[0])
            .map_err(|_| Error::State("fixture JSON".into()))?,
        old_session
    );
    assert_eq!(
        serde_json::to_value(Metrics::default())
            .map_err(|_| Error::State("fixture JSON".into()))?,
        serde_json::json!({"app_rx_bytes":0,"app_tx_bytes":0,"elapsed_millis":0,"saturated":false})
    );
    assert_eq!(
        migrated.link_history[0].link,
        Some(rustusb_core::link_health::LinkHealth::raw_v1())
    );
    let current =
        serde_json::to_value(&migrated).map_err(|_| Error::State("fixture JSON".into()))?;
    for corrupt in [
        {
            let mut value = current.clone();
            value
                .as_object_mut()
                .ok_or_else(|| Error::State("fixture object".into()))?
                .remove("link_history");
            value
        },
        {
            let mut value = current.clone();
            value["link_history"][0]
                .as_object_mut()
                .ok_or_else(|| Error::State("fixture object".into()))?
                .remove("link");
            value
        },
        {
            let mut value = current.clone();
            value["link_history"][0]["unknown"] = true.into();
            value
        },
        {
            let mut value = current.clone();
            value["link_history"][0]["instance_id"] = uuid(999).into();
            value
        },
        {
            let mut value = current.clone();
            value["schema_version"] = 3.into();
            value
        },
        {
            let mut value = legacy.clone();
            value["link_history"] = serde_json::json!([]);
            value
        },
    ] {
        assert!(State::decode(&encode(&corrupt)?).is_err());
    }
    State::decode(&encode(&current)?)?.validate()?;
    Ok(())
}

#[tokio::test]
async fn link_reads_remain_authorized_and_bounded_while_the_real_store_is_blocked() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let owner = job(1, &fixture.runtime.instance, JobState::Running)?;
    let mut next = State::default();
    next.jobs.push(owner.clone());
    for value in 0..20 {
        next.sessions.push(session(value, &owner, false)?);
    }
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    let config = fixture.runtime.config.clone();
    let controls = fixture.runtime.controls.clone();
    let reporter = crate::health::HealthReporter::default();
    reporter.direct(None)?;
    {
        let mut controls = controls.lock().map_err(|_| lock_error())?;
        let entry = controls
            .health
            .values_mut()
            .next()
            .ok_or_else(|| Error::State("fixture health".into()))?;
        entry.reporter = Some(reporter);
    }
    let store = fixture.runtime.store.clone();
    let (locked, lock_received) = oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let blocker = tokio::task::spawn_blocking(move || {
        let _guard = store.lock().map_err(|_| lock_error())?;
        locked
            .send(())
            .map_err(|_| Error::State("fixture lock observer".into()))?;
        released
            .recv_timeout(Duration::from_secs(2))
            .map_err(|_| Error::Timeout("fixture lock release".into()))?;
        Ok::<_, Error>(())
    });
    lock_received
        .await
        .map_err(|_| Error::State("fixture lock not acquired".into()))?;
    let next = fixture.runtime.state.clone();
    let pending = fixture.runtime.persist(next);
    tokio::pin!(pending);
    assert!(
        tokio::time::timeout(Duration::from_millis(20), &mut pending)
            .await
            .is_err()
    );
    {
        let controls = controls.lock().map_err(|_| lock_error())?;
        assert!(matches!(
            controls.health(&config, other_uid() ^ 1, PROFILE, None),
            Err(Error::Permission(_))
        ));
        let page = controls.health(&config, operator_uid(), PROFILE, None)?;
        assert!(page.journal_healthy);
        let Response::SessionHealth {
            sessions,
            next_after,
        } = page.response
        else {
            return Err(Error::State("fixture health response".into()));
        };
        assert_eq!(sessions.len(), 16);
        assert_eq!(
            sessions[0].availability,
            crate::health::HealthAvailability::Live
        );
        assert_eq!(
            sessions[0].link,
            Some(rustusb_core::link_health::LinkHealth::raw_v1())
        );
        let second = controls.health(&config, operator_uid(), PROFILE, next_after.as_ref())?;
        assert!(
            matches!(second.response,Response::SessionHealth{sessions,next_after:None} if sessions.len()==4)
        );
    }
    release
        .send(())
        .map_err(|_| Error::State("fixture lock owner lost".into()))?;
    pending.await?;
    blocker
        .await
        .map_err(|_| Error::State("fixture blocker task".into()))??;
    Ok(())
}

#[test]
fn health_growth_is_reserved_inside_the_existing_admission_budget() -> Result<()> {
    let instance = InstanceId::new(uuid(1))?;
    let owner = job(1, &instance, JobState::Running)?;
    let mut state = State::default();
    state.jobs.push(owner.clone());
    for value in 0..512 {
        state.sessions.push(session(value, &owner, false)?);
        state.initialize_legacy_links();
        if state.clone().preflight(true).is_err() {
            let size = serde_json::to_vec(&state)
                .map_err(|_| Error::State("fixture JSON".into()))?
                .len();
            assert!(
                size < crate::store::MAX_BYTES / 2,
                "future samples must be reserved before serialized size alone exhausts admission"
            );
            state.preflight(false)?;
            return Ok(());
        }
    }
    Err(Error::State(
        "health reservation never bounded admission".into(),
    ))
}

#[tokio::test]
async fn previous_epoch_health_is_historical_and_export_is_not_a_data_link() -> Result<()> {
    let mut fixture = Fixture::new()?;
    let previous = InstanceId::new(uuid(999))?;
    let owner = job(1, &previous, JobState::Finished)?;
    let mut next = State::default();
    next.jobs.push(owner.clone());
    next.sessions.push(session(1, &owner, true)?);
    let mut export = session(2, &owner, true)?;
    export.role = SessionRole::Export;
    next.sessions.push(export);
    next.initialize_legacy_links();
    fixture.runtime.persist(next).await?;
    let response = fixture
        .runtime
        .controls
        .lock()
        .map_err(|_| lock_error())?
        .health(&fixture.runtime.config, operator_uid(), PROFILE, None)?;
    let Response::SessionHealth { sessions, .. } = response.response else {
        return Err(Error::State("fixture health response".into()));
    };
    assert_eq!(
        sessions[0].availability,
        crate::health::HealthAvailability::PreviousEpoch
    );
    assert_eq!(
        sessions[0].link,
        Some(rustusb_core::link_health::LinkHealth::raw_v1())
    );
    assert_eq!(
        sessions[1].availability,
        crate::health::HealthAvailability::NotDataSession
    );
    assert_eq!(sessions[1].link, None);
    Ok(())
}

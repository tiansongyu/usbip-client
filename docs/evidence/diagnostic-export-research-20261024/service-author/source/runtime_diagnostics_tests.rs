//! Actual Store/UDS regressions; synthetic metadata does not prove USB cleanup.

use super::*;
use crate::diagnostics::{
    DiagnosticFailure, DiagnosticHeader, DiagnosticItems, DiagnosticKind, Registry,
};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

async fn rpc(
    request: Request,
    config: Arc<LoadedConfig>,
    controls: Arc<Mutex<Controls>>,
    registry: Arc<Registry>,
    sender: mpsc::Sender<Command>,
) -> TestResult<ResponseEnvelope> {
    let (mut client, server) = tokio::net::UnixStream::pair()?;
    let owner = tokio::spawn(crate::ipc::request(
        server,
        sender,
        config,
        controls,
        None,
        Some(registry),
    ));
    let bytes = serde_json::to_vec(&RequestEnvelope {
        schema_version: 1,
        request,
    })?;
    client.write_u32(u32::try_from(bytes.len())?).await?;
    client.write_all(&bytes).await?;
    let response = tokio::time::timeout(Duration::from_secs(2), async {
        let length = client.read_u32().await? as usize;
        if !(1..=65536).contains(&length) {
            return Err(std::io::Error::other("diagnostic fixture frame budget"));
        }
        let mut bytes = vec![0; length];
        client.read_exact(&mut bytes).await?;
        Ok::<_, std::io::Error>(bytes)
    })
    .await;
    // Drop only the pure IPC endpoint on an exceptional read. Retain and join
    // its real request task; this fixture owns no USB or filesystem worker.
    drop(client);
    let joined = tokio::time::timeout(Duration::from_secs(2), owner).await;
    let bytes = response??;
    joined???;
    Ok(serde_json::from_slice(&bytes)?)
}

fn header(response: ResponseEnvelope) -> TestResult<DiagnosticHeader> {
    match response.response {
        Response::DiagnosticStarted { header } => Ok(header),
        _ => Err(Error::State("fixture expected diagnostic header".into()).into()),
    }
}
fn begin() -> Request {
    Request::BeginDiagnostic {
        profile: PROFILE.into(),
    }
}
fn end(header: &DiagnosticHeader) -> Request {
    Request::EndDiagnostic {
        profile: PROFILE.into(),
        snapshot: header.snapshot.clone(),
    }
}
fn page(header: &DiagnosticHeader, kind: DiagnosticKind, after: u16) -> Request {
    Request::DiagnosticPage {
        profile: PROFILE.into(),
        snapshot: header.snapshot.clone(),
        kind,
        after,
    }
}

#[tokio::test]
async fn actual_store_wait_keeps_old_coherent_pages_and_fast_stop_independent() -> TestResult {
    let mut fixture = Fixture::new()?;
    let original = job(1, &fixture.runtime.instance, JobState::Running)?;
    let target = session(1, &original, false)?;
    let mut first = fixture.runtime.state.clone();
    first.jobs.push(original.clone());
    first.sessions.push(target);
    first.initialize_legacy_links();
    first.audit(
        operator_uid(),
        Some(PROFILE.into()),
        None,
        None,
        AuditAction::Start,
        None,
    )?;
    fixture.runtime.persist(first).await?;
    let config = fixture.runtime.config.clone();
    let controls = fixture.runtime.controls.clone();
    let registry = fixture.runtime.diagnostics();
    let (sender, mut commands) = mpsc::channel(1);
    let (resource_stop, stopped) = watch::channel(false);
    controls.lock().map_err(|_| lock_error())?.register_job(
        original.id.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: original.id.clone(),
            stop: resource_stop,
            stoppable: true,
        },
    );
    let store = fixture.runtime.store.clone();
    let (locked, lock_received) = oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let blocker = tokio::task::spawn_blocking(move || {
        let _guard = store.lock().map_err(|_| lock_error())?;
        locked.send(()).map_err(|_| lock_error())?;
        released
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| Error::Timeout("diagnostic fixture Store gate".into()))?;
        Ok::<_, Error>(())
    });
    tokio::time::timeout(Duration::from_secs(2), lock_received).await??;
    let mut next = fixture.runtime.state.clone();
    next.jobs
        .push(job(2, &fixture.runtime.instance, JobState::Running)?);
    next.audit(
        operator_uid(),
        Some(PROFILE.into()),
        None,
        None,
        AuditAction::Discover,
        None,
    )?;
    let (held, observation, still_pending, committed) = {
        let pending = fixture.runtime.persist(next);
        tokio::pin!(pending);
        let first_wait = tokio::time::timeout(Duration::from_millis(20), &mut pending).await;
        let held = first_wait.is_err();
        let observation: TestResult<_> = async {
            let header = header(
                rpc(
                    begin(),
                    config.clone(),
                    controls.clone(),
                    registry.clone(),
                    sender.clone(),
                )
                .await?,
            )?;
            let jobs = rpc(
                page(&header, DiagnosticKind::Jobs, 0),
                config.clone(),
                controls.clone(),
                registry.clone(),
                sender.clone(),
            )
            .await?;
            let invalid = rpc(
                page(&header, DiagnosticKind::Jobs, 2),
                config.clone(),
                controls.clone(),
                registry.clone(),
                sender.clone(),
            )
            .await?;
            let busy = rpc(
                begin(),
                config.clone(),
                controls.clone(),
                registry.clone(),
                sender.clone(),
            )
            .await?;
            let (shutdown, _) = watch::channel(false);
            crate::ipc::request_shutdown(&controls, &shutdown)?;
            Ok((header, jobs, invalid, busy, *stopped.borrow()))
        }
        .await;
        let last_wait = if held {
            tokio::time::timeout(Duration::from_millis(20), &mut pending).await
        } else {
            first_wait
        };
        let still_pending = last_wait.is_err();
        // Release before inspecting observations, including on a regression.
        let released = release.send(());
        let committed = if still_pending {
            tokio::time::timeout(Duration::from_secs(2), &mut pending).await
        } else {
            last_wait
        };
        released?;
        (held, observation, still_pending, committed)
    };
    tokio::time::timeout(Duration::from_secs(2), blocker).await???;
    committed??;
    let (old, jobs, invalid, busy, stop_delivered) = observation?;
    assert!(held && still_pending && stop_delivered);
    assert_eq!(old.committed_sequence, 1);
    assert_eq!(old.counts.jobs, 1);
    assert!(jobs.journal_healthy);
    match jobs.response {
        Response::DiagnosticPage { page } => {
            assert_eq!(page.committed_sequence, 1);
            match page.items {
                DiagnosticItems::Jobs { jobs } => assert_eq!(
                    jobs.iter().map(|j| &j.id).collect::<Vec<_>>(),
                    vec![&original.id]
                ),
                _ => return Err(Error::State("fixture section".into()).into()),
            }
        }
        _ => return Err(Error::State("fixture page".into()).into()),
    }
    assert!(matches!(
        invalid.response,
        Response::DiagnosticError {
            code: DiagnosticFailure::InvalidCursor
        }
    ));
    assert!(matches!(
        busy.response,
        Response::DiagnosticError {
            code: DiagnosticFailure::Busy
        }
    ));
    assert!(
        commands.try_recv().is_err(),
        "authorized diagnostic request reached WAL actor"
    );
    // The original token stays on its original commit even after actual commit.
    let old_page = rpc(
        page(&old, DiagnosticKind::Jobs, 0),
        config.clone(),
        controls.clone(),
        registry.clone(),
        sender.clone(),
    )
    .await?;
    assert!(
        matches!(old_page.response,Response::DiagnosticPage{page} if page.committed_sequence==1 && page.total==1)
    );
    rpc(
        end(&old),
        config.clone(),
        controls.clone(),
        registry.clone(),
        sender.clone(),
    )
    .await?;
    let current = header(rpc(begin(), config, controls, registry, sender).await?)?;
    assert_eq!(current.committed_sequence, 2);
    assert_eq!(current.counts.jobs, 2);
    fixture.assert_committed()?;
    Ok(())
}

#[tokio::test]
async fn failed_actual_commit_never_enters_diagnostic_publication() -> TestResult {
    let mut fixture = Fixture::new()?;
    let mut next = fixture.runtime.state.clone();
    next.audit(
        operator_uid(),
        Some(PROFILE.into()),
        None,
        None,
        AuditAction::Discover,
        None,
    )?;
    fixture.runtime.persist(next).await?;
    std::fs::write(
        fixture.path.join("state.json"),
        b"outside identity/content change",
    )?;
    let mut next = fixture.runtime.state.clone();
    next.audit(
        operator_uid(),
        Some(PROFILE.into()),
        None,
        None,
        AuditAction::Discover,
        None,
    )?;
    assert!(fixture.runtime.persist(next).await.is_err());
    let (sender, mut commands) = mpsc::channel(1);
    let response = rpc(
        begin(),
        fixture.runtime.config.clone(),
        fixture.runtime.controls.clone(),
        fixture.runtime.diagnostics(),
        sender,
    )
    .await?;
    assert!(!response.journal_healthy);
    assert_eq!(header(response)?.committed_sequence, 1);
    assert_eq!(fixture.runtime.state.sequence, 1);
    assert!(commands.try_recv().is_err());
    Ok(())
}

#[tokio::test]
async fn poisoned_diagnostics_preserve_actual_commit_and_existing_log_mirror() -> TestResult {
    let mut fixture = Fixture::new()?;
    let home = std::env::var_os("HOME").ok_or_else(|| Error::State("fixture HOME".into()))?;
    let directory = tempfile::Builder::new()
        .prefix(".rustusb-diagnostic-log-")
        .tempdir_in(home)?;
    std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o700))?;
    let owner = rustusb_logging::LogOwner::open(rustusb_logging::Options::new(
        directory.path().to_owned(),
    ))?;
    let monitor = owner
        .monitor()
        .ok_or_else(|| Error::State("fixture disabled logger".into()))?;
    fixture.runtime.logging = Some(monitor.clone());
    let poisoned = fixture.runtime.diagnostics();
    assert!(
        std::thread::spawn(move || poisoned.poison_for_fixture())
            .join()
            .is_err()
    );
    let mut next = fixture.runtime.state.clone();
    next.audit(
        operator_uid(),
        Some(PROFILE.into()),
        None,
        None,
        AuditAction::Discover,
        None,
    )?;
    let committed = fixture.runtime.persist(next).await;
    let (sender, mut commands) = mpsc::channel(1);
    let observed = rpc(
        begin(),
        fixture.runtime.config.clone(),
        fixture.runtime.controls.clone(),
        fixture.runtime.diagnostics(),
        sender,
    )
    .await;
    // Always drain/join the actual logger before returning an observation error.
    let shutdown = owner.shutdown();
    committed?;
    shutdown?;
    let response = observed?;
    assert!(response.journal_healthy && fixture.runtime.healthy);
    assert!(matches!(
        response.response,
        Response::DiagnosticError {
            code: DiagnosticFailure::Unavailable
        }
    ));
    assert!(commands.try_recv().is_err());
    assert_eq!(monitor.status().accepted, 1);
    assert_eq!(monitor.status().written, 1);
    fixture.assert_committed()?;
    let records = rustusb_logging::read_directory(directory.path(), None, 16)?;
    assert_eq!(records.records.iter().filter(|record|matches!(&record.event,rustusb_logging::Event::Audit(event) if event.audit_sequence==1)).count(),1);
    Ok(())
}

#[tokio::test]
async fn unknown_profile_denial_uses_actual_uid_actor_and_durable_audit() -> TestResult {
    let mut fixture = Fixture::new()?;
    let (sender, mut commands) = mpsc::channel::<Command>(1);
    let request = Request::BeginDiagnostic {
        profile: "not-configured".into(),
    };
    let pending = rpc(
        request,
        fixture.runtime.config.clone(),
        fixture.runtime.controls.clone(),
        fixture.runtime.diagnostics(),
        sender,
    );
    let recording = async {
        let command = tokio::time::timeout(Duration::from_secs(2), commands.recv())
            .await?
            .ok_or_else(|| Error::State("fixture missing denied command".into()))?;
        assert_eq!(command.uid, operator_uid());
        let response = fixture.runtime.command(command.uid, command.request).await;
        command
            .reply
            .send(ResponseEnvelope {
                schema_version: 1,
                journal_healthy: fixture.runtime.healthy,
                response,
            })
            .map_err(|_| Error::State("fixture denied reply dropped".into()))?;
        Ok::<_, Box<dyn std::error::Error + Send + Sync>>(())
    };
    let (response, recorded) = tokio::join!(pending, recording);
    recorded?;
    assert!(matches!(
        response?.response,
        Response::Error {
            code: Failure::Permission
        }
    ));
    assert_eq!(fixture.runtime.state.audit.len(), 1);
    let denial = &fixture.runtime.state.audit[0];
    assert_eq!(denial.actor_uid, operator_uid());
    assert_eq!(denial.profile, None);
    assert_eq!(denial.action, AuditAction::Denied);
    fixture.assert_committed()?;
    Ok(())
}

#[test]
fn additive_diagnostic_rpc_is_strict_and_old_ipc_one_shapes_are_unchanged() -> TestResult {
    assert_eq!(
        serde_json::to_value(Request::Profiles)?,
        serde_json::json!({"op":"profiles"})
    );
    for value in [
        serde_json::json!({"op":"begin_diagnostic","profile":PROFILE,"uid":0}),
        serde_json::json!({"op":"begin_diagnostic","profile":PROFILE,"path":"/etc/private"}),
        serde_json::json!({"op":"end_diagnostic","profile":PROFILE,"snapshot":{"instance_id":uuid(1),"number":0}}),
    ] {
        assert!(serde_json::from_value::<Request>(value).is_err());
    }
    assert!(
        serde_json::from_str::<Request>(
            r#"{"op":"begin_diagnostic","profile":"fixed-profile","profile":"other"}"#
        )
        .is_err()
    );
    let original = ResponseEnvelope {
        schema_version: 1,
        journal_healthy: true,
        response: Response::Error {
            code: Failure::Permission,
        },
    };
    assert_eq!(
        serde_json::to_value(original)?,
        serde_json::json!({"schema_version":1,"journal_healthy":true,"response":{"result":"error","code":"permission"}})
    );
    Ok(())
}

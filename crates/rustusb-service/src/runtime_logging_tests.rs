//! Real log files and Unix IPC with synthetic application facts, never USB proof.

use super::*;
use rustusb_logging::{Event, LogMonitor, LogOwner, Options, ReadFilter};
use tokio::io::{AsyncReadExt, AsyncWriteExt};

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;

struct Logs {
    owner: LogOwner,
    monitor: LogMonitor,
    _directory: tempfile::TempDir,
}
impl Logs {
    fn open() -> TestResult<Self> {
        // Exercise production ownership/ancestor checks. /tmp is deliberately
        // not substituted as a trusted root; no global subscriber is installed.
        let home = std::env::var_os("HOME")
            .ok_or_else(|| Error::State("logging fixture HOME unavailable".into()))?;
        let directory = tempfile::Builder::new()
            .prefix(".rustusb-service-log-test-")
            .tempdir_in(home)?;
        std::fs::set_permissions(directory.path(), std::fs::Permissions::from_mode(0o700))?;
        let owner = LogOwner::open(Options::new(directory.path().to_owned()))?;
        let monitor = owner
            .monitor()
            .ok_or_else(|| Error::State("fixture file logger disabled".into()))?;
        Ok(Self {
            owner,
            monitor,
            _directory: directory,
        })
    }
}

async fn written(monitor: &LogMonitor, count: u64) -> TestResult {
    tokio::time::timeout(Duration::from_secs(3), async {
        while monitor.status().written < count {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    })
    .await?;
    assert!(monitor.status().healthy);
    Ok(())
}

async fn rpc(
    request: Request,
    config: Arc<LoadedConfig>,
    controls: Arc<Mutex<Controls>>,
    logging: Option<LogMonitor>,
    sender: mpsc::Sender<Command>,
) -> TestResult<ResponseEnvelope> {
    let (mut client, server) = tokio::net::UnixStream::pair()?;
    let task = tokio::spawn(crate::ipc::request(
        server, sender, config, controls, logging, None,
    ));
    let bytes = serde_json::to_vec(&RequestEnvelope {
        schema_version: 1,
        request,
    })?;
    client.write_u32(u32::try_from(bytes.len())?).await?;
    client.write_all(&bytes).await?;
    let response = tokio::time::timeout(Duration::from_secs(3), async {
        let size = client.read_u32().await? as usize;
        assert!((1..=65536).contains(&size));
        let mut bytes = vec![0; size];
        client.read_exact(&mut bytes).await?;
        Ok::<_, std::io::Error>(bytes)
    })
    .await??;
    task.await??;
    Ok(serde_json::from_slice(&response)?)
}

fn read_request(after: Option<rustusb_logging::Cursor>, limit: u8) -> Request {
    Request::ReadLogs {
        profile: PROFILE.into(),
        after,
        limit,
    }
}

#[tokio::test]
async fn actual_uid_security_reads_and_unsupported_bypass_a_real_blocked_store() -> TestResult {
    let mut fixture = Fixture::new()?;
    let (tls, _) = crate::jobs::management_tests::credentials(false)?;
    let monitor = tls.security_monitor();
    let config = Arc::get_mut(&mut fixture.runtime.config).ok_or("shared fixture configuration")?;
    config.credentials.insert(
        PROFILE.into(),
        Credentials::Server(Arc::new(rustusb_transport::LoadedServerConfig {
            broker_uid: config.manifest.broker_uid,
            listen: "127.0.0.1:0".parse()?,
            devices: vec![rustusb_core::DeviceKey::virtual_profile(
                rustusb_core::VirtualProfile::Hid,
            )],
            tls,
        })),
    );
    config.manifest.profiles.push(crate::config::Profile {
        id: "client-only".into(),
        backend: Backend::Client,
        config: "/etc/rustusb/unused-client-test.json".into(),
    });
    for operator in &mut config.manifest.operators {
        operator.profiles.insert("client-only".into());
        if operator.uid == operator_uid() {
            // Read permission alone suffices; no Audit/Start/Stop is needed.
            operator.actions = std::collections::BTreeSet::from([Action::Read]);
        }
    }
    fixture.runtime.refresh_health()?;
    let controls = fixture.runtime.controls.clone();
    {
        let registry = controls.lock().map_err(|_| lock_error())?;
        assert!(matches!(
            registry.security(&fixture.runtime.config, operator_uid(), "unknown"),
            Err(Error::Permission(_))
        ));
        if operator_uid() != 0 {
            assert!(matches!(
                registry.security(&fixture.runtime.config, u32::MAX - 1, PROFILE),
                Err(Error::Permission(_))
            ));
        }
    }
    let config = fixture.runtime.config.clone();
    let store = fixture.runtime.store.clone();
    let (locked, ready) = oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let blocker = tokio::task::spawn_blocking(move || {
        let _guard = store.lock().map_err(|_| lock_error())?;
        let _ = locked.send(());
        released
            .recv_timeout(Duration::from_secs(10))
            .map_err(|_| Error::Timeout("security store gate".into()))?;
        Ok::<_, Error>(())
    });
    ready.await?;
    let (commands, mut queued) = mpsc::channel(1);
    let next = fixture.runtime.state.clone();
    let result = {
        let pending = fixture.runtime.persist(next);
        tokio::pin!(pending);
        assert!(
            tokio::time::timeout(Duration::from_millis(20), &mut pending)
                .await
                .is_err()
        );
        let reads = async {
            for count in [1, 2] {
                monitor.listener_connection_limit();
                let result = rpc(
                    Request::ServerSecurity {
                        profile: PROFILE.into(),
                    },
                    config.clone(),
                    controls.clone(),
                    None,
                    commands.clone(),
                )
                .await?;
                let Response::ServerSecurity {
                    instance_id,
                    snapshot,
                } = result.response
                else {
                    return Err("missing server counters".into());
                };
                assert_eq!(instance_id, InstanceId::new(uuid(1))?);
                assert_eq!(snapshot.listener_connection_limit, count);
                assert_eq!(snapshot.tls.attempts, 0);
                assert!(result.journal_healthy);
            }
            let unsupported = rpc(
                Request::ServerSecurity {
                    profile: "client-only".into(),
                },
                config.clone(),
                controls.clone(),
                None,
                commands,
            )
            .await?;
            assert!(matches!(
                unsupported.response,
                Response::Error {
                    code: Failure::Unsupported
                }
            ));
            assert!(
                queued.try_recv().is_err(),
                "authorized read entered WAL actor"
            );
            controls.lock().map_err(|_| lock_error())?.journal_unhealthy = true;
            let observed = controls.lock().map_err(|_| lock_error())?.security(
                &config,
                operator_uid(),
                PROFILE,
            )?;
            assert!(!observed.journal_healthy);
            Ok::<_, Box<dyn std::error::Error + Send + Sync>>(())
        }
        .await;
        // Always release the actual store worker before propagating read errors.
        release.send(())?;
        pending.await?;
        reads
    };
    blocker.await??;
    result?;
    assert_eq!(fixture.runtime.state.sequence, 0);
    Ok(())
}

#[tokio::test]
async fn denied_security_rpc_uses_actual_uid_and_durable_denial_without_counter_access()
-> TestResult {
    let mut fixture = Fixture::new()?;
    Arc::get_mut(&mut fixture.runtime.config)
        .ok_or("shared fixture configuration")?
        .manifest
        .operators
        .clear();
    let profiles = if operator_uid() == 0 {
        vec!["unknown"]
    } else {
        vec![PROFILE, "unknown"]
    };
    for profile in profiles {
        let config = fixture.runtime.config.clone();
        let controls = fixture.runtime.controls.clone();
        let (commands, mut queued) = mpsc::channel(1);
        let task = tokio::spawn(rpc(
            Request::ServerSecurity {
                profile: profile.into(),
            },
            config,
            controls,
            None,
            commands,
        ));
        let command = tokio::time::timeout(Duration::from_secs(3), queued.recv())
            .await?
            .ok_or("denial did not reach actor")?;
        assert_eq!(command.uid, operator_uid());
        let response = fixture.runtime.command(command.uid, command.request).await;
        assert!(matches!(
            response,
            Response::Error {
                code: Failure::Permission
            }
        ));
        let audit = fixture
            .runtime
            .state
            .audit
            .last()
            .ok_or("denial not persisted")?;
        assert_eq!(audit.action, AuditAction::Denied);
        assert_eq!(audit.actor_uid, operator_uid());
        let _ = command.reply.send(ResponseEnvelope {
            schema_version: 1,
            journal_healthy: true,
            response,
        });
        assert!(matches!(
            task.await??.response,
            Response::Error {
                code: Failure::Permission
            }
        ));
        fixture.assert_committed()?;
    }
    Ok(())
}

#[tokio::test]
async fn committed_audit_mirror_is_exact_once_and_failed_commit_is_not_projected() -> TestResult {
    let mut fixture = Fixture::new()?;
    let logs = Logs::open()?;
    fixture.runtime.logging = Some(logs.monitor.clone());
    let original_instance = InstanceId::new(uuid(99))?;
    let owner = job(1, &original_instance, JobState::Recovering)?;
    let target = session(1, &owner, false)?;
    let mut next = fixture.runtime.state.clone();
    next.jobs.push(owner.clone());
    next.sessions.push(target.clone());
    next.initialize_legacy_links();
    next.audit(
        operator_uid(),
        Some(PROFILE.into()),
        Some(owner.id.clone()),
        Some(target.id.clone()),
        AuditAction::Recovery,
        Some(Failure::Io),
    )?;
    fixture.runtime.persist(next).await?;
    written(&logs.monitor, 1).await?;
    let page = logs.monitor.read(ReadFilter::All, None, 16).await?;
    let projections: Vec<_> = page
        .records
        .iter()
        .filter_map(|record| {
            if let Event::Audit(value) = &record.event {
                Some(value)
            } else {
                None
            }
        })
        .collect();
    assert_eq!(projections.len(), 1);
    let projection = projections[0];
    assert_eq!(projection.application_instance, fixture.runtime.instance);
    assert_ne!(projection.application_instance, original_instance);
    assert_eq!(projection.audit_sequence, 1);
    assert_eq!(projection.actor_uid, operator_uid());
    assert_eq!(projection.profile.as_deref(), Some(PROFILE));
    assert_eq!(projection.job_id.as_ref(), Some(&owner.id));
    assert_eq!(projection.session_id.as_ref(), Some(&target.id));
    assert_eq!(projection.action, rustusb_logging::AuditAction::Recovery);
    assert_eq!(
        projection.failure,
        Some(rustusb_logging::FailureCategory::Io)
    );
    assert_eq!(projection.state, Some(target.state));
    assert_eq!(projection.evidence, Some(rustusb_logging::Evidence::Intent));
    assert_eq!(projection.cleanup, None);
    fixture
        .runtime
        .persist(fixture.runtime.state.clone())
        .await?;
    assert_eq!(logs.monitor.status().accepted, 1);
    // Force the real Store identity/content guard to reject the next commit.
    std::fs::write(
        fixture.path.join("state.json"),
        b"changed outside the store",
    )?;
    let mut next = fixture.runtime.state.clone();
    next.audit(
        operator_uid(),
        None,
        None,
        None,
        AuditAction::Denied,
        Some(Failure::Permission),
    )?;
    assert!(fixture.runtime.persist(next).await.is_err());
    assert!(!fixture.runtime.healthy);
    assert_eq!(fixture.runtime.state.sequence, 1);
    assert_eq!(logs.monitor.status().accepted, 1);
    logs.owner.shutdown()?;
    Ok(())
}

#[tokio::test]
async fn delayed_commit_never_projects_early_and_closed_logger_does_not_poison_business()
-> TestResult {
    let mut fixture = Fixture::new()?;
    let logs = Logs::open()?;
    fixture.runtime.logging = Some(logs.monitor.clone());
    let store = fixture.runtime.store.clone();
    let (locked, lock_received) = oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let blocker = tokio::task::spawn_blocking(move || {
        let _guard = store.lock().map_err(|_| lock_error())?;
        locked.send(()).map_err(|_| lock_error())?;
        released
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| Error::Timeout("log fixture Store gate".into()))?;
        Ok::<_, Error>(())
    });
    lock_received.await?;
    let mut next = fixture.runtime.state.clone();
    next.audit(
        operator_uid(),
        Some(PROFILE.into()),
        None,
        None,
        AuditAction::Discover,
        None,
    )?;
    {
        let pending = fixture.runtime.persist(next);
        tokio::pin!(pending);
        assert!(
            tokio::time::timeout(Duration::from_millis(20), &mut pending)
                .await
                .is_err()
        );
        assert_eq!(logs.monitor.status().accepted, 0);
        release.send(())?;
        pending.await?;
    }
    blocker.await??;
    written(&logs.monitor, 1).await?;
    logs.owner.shutdown()?;
    // The runtime's nonowning clone cannot keep admission alive. Its failed
    // best-effort mirror cannot roll back or poison a second actual commit.
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
    assert!(fixture.runtime.healthy);
    assert_eq!(fixture.runtime.state.sequence, 2);
    fixture.assert_committed()?;
    Ok(())
}

#[tokio::test]
async fn actual_uid_log_pages_and_fast_stop_work_while_real_store_is_blocked() -> TestResult {
    let mut fixture = Fixture::new()?;
    let logs = Logs::open()?;
    fixture.runtime.logging = Some(logs.monitor.clone());
    let owner = job(1, &fixture.runtime.instance, JobState::Running)?;
    let mut next = fixture.runtime.state.clone();
    next.jobs.push(owner.clone());
    for (uid, profile, action) in [
        (operator_uid(), Some(PROFILE), AuditAction::Start),
        (operator_uid(), Some("other-profile"), AuditAction::Discover),
        (other_uid(), None, AuditAction::Denied),
        (operator_uid(), None, AuditAction::Denied),
        (operator_uid(), Some(PROFILE), AuditAction::Discover),
    ] {
        next.audit(uid, profile.map(str::to_owned), None, None, action, None)?;
    }
    fixture.runtime.persist(next).await?;
    written(&logs.monitor, 5).await?;
    let config = fixture.runtime.config.clone();
    let controls = fixture.runtime.controls.clone();
    assert!(
        config
            .manifest
            .authorize(other_uid(), PROFILE, Action::Read)
            .is_ok()
    );
    assert!(
        config
            .manifest
            .authorize(other_uid(), PROFILE, Action::Audit)
            .is_err()
    );
    let (stop, mut stopped) = watch::channel(false);
    controls.lock().map_err(|_| lock_error())?.register_job(
        owner.id.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: owner.id.clone(),
            stop,
            stoppable: true,
        },
    );
    let (owned, mut released_socket) = tokio::net::UnixStream::pair()?;
    let owned_task = tokio::spawn(async move {
        crate::context::stopped(&mut stopped).await;
        drop(owned);
    });
    let store = fixture.runtime.store.clone();
    let (locked, lock_received) = oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let blocker = tokio::task::spawn_blocking(move || {
        let _guard = store.lock().map_err(|_| lock_error())?;
        locked.send(()).map_err(|_| lock_error())?;
        released
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| Error::Timeout("log read Store gate".into()))?;
        Ok::<_, Error>(())
    });
    lock_received.await?;
    let (sender, mut commands) = mpsc::channel(16);
    let stopped_rpc;
    {
        let next = fixture.runtime.state.clone();
        let pending = fixture.runtime.persist(next);
        tokio::pin!(pending);
        assert!(
            tokio::time::timeout(Duration::from_millis(20), &mut pending)
                .await
                .is_err()
        );
        let mut after = None;
        let mut seen = Vec::new();
        for _ in 0..8 {
            let result = rpc(
                read_request(after.clone(), 1),
                config.clone(),
                controls.clone(),
                Some(logs.monitor.clone()),
                sender.clone(),
            )
            .await?;
            assert!(result.journal_healthy);
            let Response::Logs { page } = result.response else {
                return Err(Error::State("expected independent log page".into()).into());
            };
            assert!(page.records.len() <= 1);
            for record in page.records {
                let Event::Audit(event) = record.event else {
                    return Err(Error::State("operator received generic process log".into()).into());
                };
                assert!(
                    event.profile.as_deref() == Some(PROFILE)
                        || event.profile.is_none()
                            && event.action == rustusb_logging::AuditAction::Denied
                            && (operator_uid() == 0 || event.actor_uid == operator_uid())
                );
                seen.push(event.audit_sequence);
            }
            if !page.more {
                break;
            }
            assert_ne!(page.next_after, after);
            after = page.next_after;
        }
        let expected = if operator_uid() == 0 {
            vec![1, 3, 4, 5]
        } else {
            vec![1, 4, 5]
        };
        assert_eq!(seen, expected);
        assert!(matches!(
            commands.try_recv(),
            Err(mpsc::error::TryRecvError::Empty)
        ));
        for (limit, monitor, expected) in [
            (0, Some(logs.monitor.clone()), LogReadFailure::InvalidInput),
            (17, Some(logs.monitor.clone()), LogReadFailure::InvalidInput),
            (16, None, LogReadFailure::Disabled),
        ] {
            let response = rpc(
                read_request(None, limit),
                config.clone(),
                controls.clone(),
                monitor,
                sender.clone(),
            )
            .await?;
            assert!(response.journal_healthy);
            assert!(matches!(response.response, Response::LogError { code } if code == expected));
        }
        assert!(matches!(
            commands.try_recv(),
            Err(mpsc::error::TryRecvError::Empty)
        ));
        stopped_rpc = tokio::spawn(rpc(
            Request::StopJob { job_id: owner.id },
            config.clone(),
            controls.clone(),
            Some(logs.monitor.clone()),
            sender.clone(),
        ));
        let mut byte = [0];
        assert_eq!(
            tokio::time::timeout(Duration::from_secs(1), released_socket.read(&mut byte)).await??,
            0
        );
        assert!(
            !stopped_rpc.is_finished(),
            "Stop response must still await the real business record"
        );
        release.send(())?;
        pending.await?;
    }
    blocker.await??;
    owned_task.await?;
    let command = commands
        .recv()
        .await
        .ok_or_else(|| Error::State("missing actual stop command".into()))?;
    assert_eq!(command.uid, operator_uid());
    let response = fixture.runtime.command(command.uid, command.request).await;
    assert!(matches!(
        response,
        Response::StopAccepted {
            owner_present: true
        }
    ));
    command
        .reply
        .send(ResponseEnvelope {
            schema_version: 1,
            journal_healthy: fixture.runtime.healthy,
            response,
        })
        .map_err(|_| Error::State("stop client disappeared".into()))?;
    assert!(stopped_rpc.await??.journal_healthy);
    logs.owner.shutdown()?;
    Ok(())
}

#[tokio::test]
async fn denied_log_request_uses_actual_uid_and_existing_durable_denial_path() -> TestResult {
    let mut fixture = Fixture::new()?;
    let (sender, mut commands) = mpsc::channel(16);
    let config = fixture.runtime.config.clone();
    let controls = fixture.runtime.controls.clone();
    let client = tokio::spawn(rpc(
        Request::ReadLogs {
            profile: "not-configured".into(),
            after: None,
            limit: 16,
        },
        config,
        controls,
        None,
        sender,
    ));
    let command = tokio::time::timeout(Duration::from_secs(1), commands.recv())
        .await?
        .ok_or_else(|| Error::State("log denial not routed to existing audit path".into()))?;
    assert_eq!(command.uid, operator_uid());
    let response = fixture.runtime.command(command.uid, command.request).await;
    assert!(matches!(
        response,
        Response::Error {
            code: Failure::Permission
        }
    ));
    command
        .reply
        .send(ResponseEnvelope {
            schema_version: 1,
            journal_healthy: fixture.runtime.healthy,
            response,
        })
        .map_err(|_| Error::State("log denial client disappeared".into()))?;
    assert!(client.await??.journal_healthy);
    assert_eq!(fixture.runtime.state.audit.len(), 1);
    let denied = &fixture.runtime.state.audit[0];
    assert_eq!(denied.actor_uid, operator_uid());
    assert_eq!(denied.profile, None);
    assert_eq!(denied.action, AuditAction::Denied);
    assert!(fixture.runtime.state.jobs.is_empty());
    fixture.assert_committed()?;
    for extra in [
        "\"directory\":\"/root\"",
        "\"actor_uid\":0",
        "\"root_admin\":true",
    ] {
        let input = format!(
            "{{\"schema_version\":1,\"request\":{{\"op\":\"read_logs\",\"profile\":\"fixed-profile\",\"after\":null,\"limit\":16,{extra}}}}}"
        );
        assert!(serde_json::from_str::<RequestEnvelope>(&input).is_err());
    }
    Ok(())
}

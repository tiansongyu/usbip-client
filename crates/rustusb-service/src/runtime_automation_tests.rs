//! Actual journal/socket ownership plus synthetic observations; no USB/helper claims.
use super::super::tests::{Fixture, operator_uid, other_uid, uuid};
use super::*;
use crate::{config::Profile, store::RequestRecord};
use rustusb_core::{BusId, Device, Host, Interface, Peer, VirtualProfile};
use rustusb_transport::config::{LoadedClientConfig, LoadedServerConfig};
use serde_json::json;
use std::{net::SocketAddr, num::NonZeroU16};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, UnixStream},
};

type TestResult<T = ()> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
const PROFILE: &str = "fixed-profile";
fn request(n: u64) -> Result<RequestId> {
    RequestId::new(uuid(n))
}
fn token(c: char) -> Result<ObservationToken> {
    ObservationToken::new(c.to_string().repeat(64))
}
fn spec() -> Result<RuleSpec> {
    RuleSpec::new(
        "Desk keyboard".into(),
        TriggerKind::RemoteDeviceAvailable,
        2,
        RearmKind::NewObservation,
    )
}

fn configured(
    f: &mut Fixture,
    address: SocketAddr,
    framed: bool,
) -> TestResult<rustusb_transport::TlsServer> {
    let (server, tls) = crate::jobs::management_tests::credentials(framed)?;
    let config = Arc::get_mut(&mut f.runtime.config).ok_or("unshared fixture config")?;
    config.manifest.operators[0]
        .actions
        .insert(Action::Discover);
    config.credentials.insert(
        PROFILE.into(),
        Credentials::Client(Arc::new(LoadedClientConfig {
            broker_uid: config.manifest.broker_uid,
            server_id: "test-server".into(),
            endpoint: Peer {
                host: Host::new(address.ip().to_string())?,
                port: NonZeroU16::new(address.port()).ok_or("test port")?,
            },
            server_name: "server.test".into(),
            device: DeviceKey::virtual_profile(VirtualProfile::Hid),
            tls,
        })),
    );
    Ok(server)
}

async fn policy(f: &mut Fixture, max: u8) -> TestResult {
    let config = &f.runtime.config;
    let value = json!({"schema_version":1,"broker_uid":config.manifest.broker_uid,"grants":[{"operator_uid":operator_uid(),"profile":PROFILE,
        "devices":["virtual:hid"],"triggers":["remote_device_available"],"rearm":["once","new_observation"],"max_rules":max}]});
    let policy = AutomationPolicy::fixture(&serde_json::to_vec(&value)?, config)?;
    f.runtime.configure_automation(Some(policy)).await?;
    Ok(())
}

async fn create(f: &mut Fixture, n: u64, enabled: bool) -> TestResult<RuleMutationOutcome> {
    match f
        .runtime
        .mutate_rule(
            operator_uid(),
            PROFILE.into(),
            request(n)?,
            Some(RuleMutation::Create {
                spec: spec()?,
                enabled,
            }),
        )
        .await?
    {
        Response::RuleMutationApplied {
            outcome,
            replayed: false,
            ..
        } => Ok(outcome),
        _ => Err("original mutation result missing".into()),
    }
}
fn committed(f: &Fixture) -> TestResult<State> {
    Ok(State::decode(&std::fs::read(f.path.join("state.json"))?)?)
}

#[tokio::test]
async fn explicit_policy_crud_cas_quota_peer_scope_and_revocation_are_real_commits() -> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    assert!(
        f.runtime
            .mutate_rule(
                operator_uid(),
                PROFILE.into(),
                request(100)?,
                Some(RuleMutation::Create {
                    spec: spec()?,
                    enabled: false
                })
            )
            .await
            .is_err()
    );
    assert_eq!(f.runtime.state.schema_version, 2);
    policy(&mut f, 2).await?;
    let one = create(&mut f, 101, false).await?;
    let two = create(&mut f, 102, false).await?;
    assert_eq!(committed(&f)?.schema_version, 3);
    assert_eq!(committed(&f)?.automation.ok_or("journal")?.rules.len(), 2);
    assert!(create(&mut f, 103, false).await.is_err());
    assert!(
        f.runtime
            .mutate_rule(
                other_uid(),
                PROFILE.into(),
                request(104)?,
                Some(RuleMutation::SetEnabled {
                    rule_id: one.rule_id.clone(),
                    expected_revision: ExpectedRevision::new(1)?,
                    enabled: true
                })
            )
            .await
            .is_err()
    );
    let views = f.runtime.controls.lock().map_err(|_| lock_error())?.rules(
        &f.runtime.config,
        other_uid(),
        PROFILE,
        None,
    )?;
    assert!(matches!(views.response,Response::Rules{rules,grant:None,..} if rules.is_empty()));
    let stale = RuleMutation::SetEnabled {
        rule_id: one.rule_id.clone(),
        expected_revision: ExpectedRevision::new(2)?,
        enabled: true,
    };
    assert!(
        f.runtime
            .mutate_rule(operator_uid(), PROFILE.into(), request(105)?, Some(stale))
            .await
            .is_err()
    );
    let original = std::fs::read(f.path.join("state.json"))?;
    let replay = f
        .runtime
        .mutate_rule(operator_uid(), PROFILE.into(), request(101)?, None)
        .await?;
    assert!(
        matches!(replay,Response::RuleMutationApplied{outcome,replayed:true,..} if outcome==one)
    );
    assert_eq!(std::fs::read(f.path.join("state.json"))?, original);
    assert!(
        f.runtime
            .mutate_rule(
                operator_uid(),
                PROFILE.into(),
                request(101)?,
                Some(RuleMutation::Create {
                    spec: spec()?,
                    enabled: true
                })
            )
            .await
            .is_err()
    );
    Arc::get_mut(&mut f.runtime.config)
        .ok_or("unshared config")?
        .manifest
        .operators[0]
        .actions
        .remove(&Action::Start);
    assert!(
        f.runtime
            .mutate_rule(
                operator_uid(),
                PROFILE.into(),
                request(106)?,
                Some(RuleMutation::SetEnabled {
                    rule_id: one.rule_id.clone(),
                    expected_revision: ExpectedRevision::new(1)?,
                    enabled: true
                })
            )
            .await
            .is_err()
    );
    assert!(matches!(
        f.runtime
            .mutate_rule(operator_uid(), PROFILE.into(), request(101)?, None)
            .await?,
        Response::RuleMutationApplied { replayed: true, .. }
    ));
    f.runtime
        .mutate_rule(
            operator_uid(),
            PROFILE.into(),
            request(107)?,
            Some(RuleMutation::SetEnabled {
                rule_id: one.rule_id,
                expected_revision: ExpectedRevision::new(1)?,
                enabled: false,
            }),
        )
        .await?;
    f.runtime
        .mutate_rule(
            operator_uid(),
            PROFILE.into(),
            request(108)?,
            Some(RuleMutation::Delete {
                rule_id: two.rule_id,
                expected_revision: ExpectedRevision::new(1)?,
            }),
        )
        .await?;
    f.runtime.configure_automation(None).await?;
    assert_eq!(committed(&f)?.schema_version, 3);
    assert_eq!(committed(&f)?.automation.ok_or("journal")?.rules.len(), 1);
    assert!(f.runtime.workers.is_empty());
    assert!(f.runtime.discoveries.is_empty());
    assert!(!f.runtime.config.manifest.helper_socket.exists());
    Ok(())
}

fn observed(n: u8) -> TestResult<crate::discovery::ObservedDevice> {
    Ok(crate::discovery::ObservedDevice {
        device: Device {
            id: format!("usb-{:064x}", u64::from(n)),
            bus_id: BusId::new(format!("1-{n}"))?,
            vendor_id: 0x1234,
            product_id: u16::from(n),
            manufacturer: None,
            product: None,
            serial: Some("private fixture serial".into()),
            device_address: u32::from(n),
            usb_version: "2.00".into(),
            speed: "12".into(),
            class: 0,
            subclass: 0,
            protocol: 0,
            topology: format!("fixture/{n}"),
            driver: Some("usb".into()),
            interfaces: vec![Interface {
                number: 0,
                alternate_setting: 0,
                class: 3,
                subclass: 1,
                protocol: 1,
                driver: Some("usbhid".into()),
                endpoints: vec![],
            }],
            exported: false,
            occupied: None,
            export_blockers: vec![],
        },
        stamp: crate::discovery::ObservationStamp {
            sysfs_device: 1,
            sysfs_inode: u64::from(n),
            bus_number: 1,
            device_number: n,
        },
    })
}
fn snapshot(records: Vec<crate::discovery::ObservedDevice>) -> crate::discovery::DeviceSnapshot {
    crate::discovery::DeviceSnapshot {
        instance: "observer-epoch".into(),
        revision: 1,
        state: crate::discovery::DiscoveryState::Current,
        failure: None,
        reason: crate::discovery::DiscoveryReason::Periodic,
        sampled_ms: 1,
        sampled_at: std::time::Instant::now(),
        devices: Some(records.into()),
        changes: vec![],
        counters: crate::discovery::DiscoveryCounters::default(),
        receive_buffer_bytes: 65536,
    }
}

#[test]
fn local_whole_pool_freshness_storage_ambiguity_and_occurrence_keys_are_distinct() -> TestResult {
    let records = vec![observed(1)?, observed(2)?];
    let keys = records
        .iter()
        .map(|d| DeviceKey::native(&d.device.id))
        .collect::<Result<BTreeSet<_>>>()?;
    let boot = InstanceId::new(uuid(900))?;
    let good = snapshot(records.clone());
    let (first, _) =
        local_observation(&good, &keys, &boot).map_err(|_| "complete safe pool rejected")?;
    let mut repeated = good.clone();
    repeated.instance = "new-observer".into();
    repeated.revision = 100;
    repeated.sampled_ms = 5000;
    assert_eq!(
        local_observation(&repeated, &keys, &boot)
            .map_err(|_| "repeat rejected")?
            .0,
        first
    );
    assert_ne!(
        local_observation(&good, &keys, &InstanceId::new(uuid(901))?)
            .map_err(|_| "new boot rejected")?
            .0,
        first
    );
    let mut changed = records.clone();
    changed[0].stamp.sysfs_inode += 10;
    changed[0].stamp.device_number = 3;
    changed[0].device.device_address = 3;
    assert_ne!(
        local_observation(&snapshot(changed), &keys, &boot)
            .map_err(|_| "new observation rejected")?
            .0,
        first
    );
    assert_eq!(
        local_observation(&snapshot(vec![records[0].clone()]), &keys, &boot),
        Err(RuleStatus::Waiting)
    );
    let mut duplicate = records.clone();
    duplicate.push(records[0].clone());
    assert_eq!(
        local_observation(&snapshot(duplicate), &keys, &boot),
        Err(RuleStatus::BlockedUnsafe)
    );
    for mode in 0..7 {
        let mut value = records.clone();
        match mode {
            0 => value[0].device.class = 8,
            1 => value[0].device.interfaces[0].class = 8,
            2 => value[0]
                .device
                .export_blockers
                .push("unreadable alternate/configuration".into()),
            3 => value[0].device.exported = true,
            4 => value[0].device.occupied = Some(true),
            5 => value[0].device.interfaces.clear(),
            _ => value[0].device.device_address = 99,
        }
        assert_eq!(
            local_observation(&snapshot(value), &keys, &boot),
            Err(RuleStatus::BlockedUnsafe)
        );
    }
    for mode in 0..3 {
        let mut old = good.clone();
        match mode {
            0 => old.state = crate::discovery::DiscoveryState::Stale,
            1 => old.devices = None,
            _ => old.sampled_at = std::time::Instant::now() - Duration::from_secs(7),
        };
        assert_eq!(
            local_observation(&old, &keys, &boot),
            Err(RuleStatus::Stale)
        );
    }
    Ok(())
}

#[tokio::test]
async fn remote_known_profile_safety_and_expired_completion_never_infer_physical_generation()
-> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    policy(&mut f, 4).await?;
    let outcome = create(&mut f, 200, true).await?;
    let r = f.runtime.state.automation.as_ref().ok_or("journal")?.rules[0].clone();
    let key = DeviceKey::virtual_profile(VirtualProfile::Hid);
    let d = crate::jobs::management_tests::device()?;
    let good = vec![rustusb_protocol::secure::CatalogDevice {
        device_key: key,
        device: d.clone(),
    }];
    assert!(f.runtime.remote_condition(&r, &good).is_ok());
    assert_eq!(
        f.runtime.remote_condition(&r, &[]),
        Err(RuleStatus::Waiting)
    );
    for mode in 0..4 {
        let mut rows = good.clone();
        match mode {
            0 => rows[0].device.interfaces[0].class = 8,
            1 => rows[0].device.num_configurations = 2,
            2 => rows[0].device.interfaces.clear(),
            _ => rows.push(good[0].clone()),
        };
        assert_eq!(
            f.runtime.remote_condition(&r, &rows),
            Err(RuleStatus::BlockedUnsafe)
        );
    }
    f.runtime.automation.remote_inflight = Some((outcome.rule_id.clone(), 1));
    f.runtime
        .remote_rule_finished(
            operator_uid(),
            PROFILE,
            outcome.rule_id.clone(),
            1,
            Instant::now() - Duration::from_secs(7),
            Response::Catalog { devices: good },
        )
        .await;
    assert!(f.runtime.workers.is_empty());
    assert!(f.runtime.state.jobs.is_empty());
    assert_eq!(
        f.runtime.automation.remote[&outcome.rule_id].status,
        RuleStatus::Stale
    );
    let config = Arc::get_mut(&mut f.runtime.config).ok_or("unshared config")?;
    let Some(Credentials::Client(client)) = config.credentials.get_mut(PROFILE) else {
        return Err("client fixture".into());
    };
    Arc::get_mut(client).ok_or("unshared client")?.device =
        DeviceKey::native(&format!("usb-{:064x}", 1))?;
    let value = json!({"schema_version":1,"broker_uid":config.manifest.broker_uid,"grants":[{"operator_uid":operator_uid(),"profile":PROFILE,"devices":[Arc::get_mut(client).ok_or("unshared client")?.device],"triggers":["remote_device_available"],"rearm":["new_observation"],"max_rules":4}]});
    f.runtime.automation.policy = Some(Arc::new(AutomationPolicy::fixture(
        &serde_json::to_vec(&value)?,
        config,
    )?));
    assert_eq!(
        f.runtime.remote_condition(&r, &[]),
        Err(RuleStatus::SafetyEvidenceUnavailable)
    );
    assert!(f.runtime.state.jobs.is_empty());
    assert!(!f.runtime.config.manifest.helper_socket.exists());
    Ok(())
}

struct Gate {
    release: std::sync::mpsc::Sender<()>,
    owner: tokio::task::JoinHandle<Result<()>>,
}
impl Gate {
    async fn hold(runtime: &Runtime) -> TestResult<Self> {
        Self::hold_store(runtime.store.clone()).await
    }
    async fn hold_store(store: Arc<Mutex<Store>>) -> TestResult<Self> {
        let (ready, prepared) = oneshot::channel();
        let (release, released) = std::sync::mpsc::channel();
        let owner = tokio::task::spawn_blocking(move || {
            let _guard = store.lock().map_err(|_| lock_error())?;
            ready
                .send(())
                .map_err(|_| Error::State("rule gate observer".into()))?;
            released
                .recv_timeout(Duration::from_secs(5))
                .map_err(|_| Error::Timeout("rule fixture gate release".into()))?;
            Ok(())
        });
        prepared.await?;
        Ok(Self { release, owner })
    }
    async fn finish(self) -> TestResult {
        let sent = self.release.send(());
        let ended = self.owner.await;
        sent?;
        ended??;
        Ok(())
    }
}

async fn read_response(stream: &mut UnixStream) -> TestResult<ResponseEnvelope> {
    let len = stream.read_u32().await? as usize;
    if len == 0 || len > 65536 {
        return Err("bounded rule frame".into());
    }
    let mut bytes = vec![0; len];
    stream.read_exact(&mut bytes).await?;
    Ok(serde_json::from_slice(&bytes)?)
}

#[tokio::test]
async fn unknown_mutation_keeps_original_uuid_and_authorized_reads_bypass_a_real_store_gate()
-> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    policy(&mut f, 4).await?;
    let id = request(300)?;
    let (reply, receive) = oneshot::channel();
    drop(receive);
    let (commands, command_queue) = mpsc::channel(1);
    let (mut client, peer) = UnixStream::pair()?;
    let config = f.runtime.config.clone();
    let controls = f.runtime.controls.clone();
    let encoded = serde_json::to_vec(&RequestEnvelope {
        schema_version: 1,
        request: Request::Rules {
            profile: PROFILE.into(),
            after: None,
        },
    })?;
    client.write_u32(encoded.len() as u32).await?;
    client.write_all(&encoded).await?;
    let (stop, stopping) = watch::channel(false);
    let gate = Gate::hold(&f.runtime).await?;
    let (waiting, read_before_ack, recorded, read_result, rpc_result) = {
        let mutation = f.runtime.receive_command(
            Command {
                uid: operator_uid(),
                request: Request::MutateRule {
                    profile: PROFILE.into(),
                    request_id: id.clone(),
                    mutation: RuleMutation::Create {
                        spec: spec()?,
                        enabled: false,
                    },
                },
                reply,
            },
            stopping.clone(),
            stopping,
        );
        tokio::pin!(mutation);
        let waiting = tokio::time::timeout(Duration::from_millis(20), &mut mutation)
            .await
            .is_err();
        let rpc = tokio::spawn(crate::ipc::request(
            peer, commands, config, controls, None, None,
        ));
        let reading = read_response(&mut client);
        tokio::pin!(reading);
        let read = tokio::time::timeout(Duration::from_millis(100), &mut reading).await;
        let read_before_ack = matches!(&read,Ok(Ok(ResponseEnvelope{response:Response::Rules{rules,..},..})) if rules.is_empty());
        let disk = State::decode(&std::fs::read(f.path.join("state.json"))?)?;
        let recorded = disk
            .automation
            .as_ref()
            .is_some_and(|j| j.rules.is_empty() && j.mutations.is_empty());
        gate.finish().await?;
        // A regression which queues this read must also be unblocked and joined.
        drop(command_queue);
        if waiting {
            mutation.await;
        }
        let read_result = match read {
            Ok(result) => result,
            Err(_) => reading.await,
        };
        let rpc_result = rpc.await;
        (waiting, read_before_ack, recorded, read_result, rpc_result)
    };
    drop(stop);
    read_result?;
    rpc_result??;
    let saved = std::fs::read(f.path.join("state.json"))?;
    let replay = f
        .runtime
        .command(
            operator_uid(),
            Request::RetryRuleMutation {
                profile: PROFILE.into(),
                request_id: id,
            },
        )
        .await;
    assert!(matches!(
        replay,
        Response::RuleMutationApplied { replayed: true, .. }
    ));
    assert_eq!(std::fs::read(f.path.join("state.json"))?, saved);
    assert!(matches!(
        f.runtime
            .command(
                operator_uid(),
                Request::RetryRuleMutation {
                    profile: PROFILE.into(),
                    request_id: request(999)?
                }
            )
            .await,
        Response::RuleMutationExpired { .. }
    ));
    assert_eq!(committed(&f)?.automation.ok_or("journal")?.rules.len(), 1);
    assert!(
        waiting && read_before_ack && recorded,
        "read/ACK ordering failed across actual store wait"
    );
    assert!(f.runtime.workers.is_empty());
    assert!(!f.runtime.config.manifest.helper_socket.exists());
    Ok(())
}

#[tokio::test]
async fn stop_fanout_and_newer_suppression_survive_an_enable_commit_wait() -> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    policy(&mut f, 4).await?;
    let rule = create(&mut f, 400, false).await?;
    let old = Job {
        id: JobId::new(uuid(401))?,
        instance_id: f.runtime.instance.clone(),
        profile: PROFILE.into(),
        operator_uid: operator_uid(),
        state: JobState::Running,
        stop_requested: false,
        operation_error: None,
    };
    let mut next = f.runtime.state.clone();
    next.jobs.push(old.clone());
    f.runtime.persist(next).await?;
    let controls = f.runtime.controls.clone();
    let config = f.runtime.config.clone();
    let (send, mut stopping) = watch::channel(false);
    controls.lock().map_err(|_| lock_error())?.register_job(
        old.id.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: old.id.clone(),
            stop: send,
            stoppable: true,
        },
    );
    let (endpoint, mut observer) = UnixStream::pair()?;
    let witness = tokio::spawn(async move {
        crate::context::stopped(&mut stopping).await;
        drop(endpoint);
    });
    let gate = Gate::hold(&f.runtime).await?;
    let (waiting, closed_before_ack, resumed) = {
        let recording = f.runtime.mutate_rule(
            operator_uid(),
            PROFILE.into(),
            request(402)?,
            Some(RuleMutation::SetEnabled {
                rule_id: rule.rule_id.clone(),
                expected_revision: ExpectedRevision::new(1)?,
                enabled: true,
            }),
        );
        tokio::pin!(recording);
        let poll = tokio::time::timeout(Duration::from_millis(20), &mut recording).await;
        let waiting = poll.is_err();
        let owner = controls.lock().map_err(|_| lock_error())?.request(
            &config,
            operator_uid(),
            &Request::StopJob {
                job_id: old.id.clone(),
            },
        );
        let closed = matches!(
            tokio::time::timeout(Duration::from_millis(100), observer.read(&mut [0])).await,
            Ok(Ok(0))
        );
        gate.finish().await?;
        let result = match poll {
            Ok(value) => value,
            Err(_) => recording.await,
        };
        (waiting, closed, (owner, result))
    };
    witness.await?;
    assert!(resumed.0?);
    resumed.1?;
    let still_suppressed = controls
        .lock()
        .map_err(|_| lock_error())?
        .automation
        .suppressed
        .contains(PROFILE);
    f.runtime
        .command(
            operator_uid(),
            Request::StopJob {
                job_id: old.id.clone(),
            },
        )
        .await;
    f.runtime.job_finished(old.id, Ok(())).await;
    let paused = committed(&f)?.automation.ok_or("journal")?.rules[0].clone();
    let result = f
        .runtime
        .mutate_rule(
            operator_uid(),
            PROFILE.into(),
            request(403)?,
            Some(RuleMutation::SetEnabled {
                rule_id: rule.rule_id,
                expected_revision: ExpectedRevision::new(paused.revision)?,
                enabled: true,
            }),
        )
        .await?;
    assert!(matches!(
        result,
        Response::RuleMutationApplied {
            replayed: false,
            ..
        }
    ));
    assert!(
        !controls
            .lock()
            .map_err(|_| lock_error())?
            .automation
            .suppressed
            .contains(PROFILE)
    );
    assert!(waiting && closed_before_ack && still_suppressed);
    assert!(paused.paused_by_user);
    assert!(f.runtime.workers.is_empty());
    Ok(())
}

#[test]
fn late_automatic_registration_inherits_suppression_and_old_resume_cannot_clear_it() -> TestResult {
    let mut controls = Controls::default();
    let auto = JobId::new(uuid(501))?;
    let manual = JobId::new(uuid(502))?;
    controls.automation.automatic_jobs.insert(auto.clone());
    controls.automation.suppress(PROFILE);
    let first = controls.automation.epoch(PROFILE);
    controls.automation.suppress(PROFILE);
    controls.automation.resume(PROFILE, first);
    let (stop, observed) = watch::channel(false);
    controls.register_job(
        auto.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: auto,
            stop,
            stoppable: true,
        },
    );
    let (stop, ordinary) = watch::channel(false);
    controls.register_job(
        manual.clone(),
        Control {
            profile: PROFILE.into(),
            job_id: manual,
            stop,
            stoppable: true,
        },
    );
    assert!(*observed.borrow());
    assert!(!*ordinary.borrow());
    let current = controls.automation.epoch(PROFILE);
    controls.automation.resume(PROFILE, current);
    assert!(!controls.automation.suppressed.contains(PROFILE));
    controls.automation.epochs.insert(PROFILE.into(), u64::MAX);
    controls.automation.suppress(PROFILE);
    controls.automation.resume(PROFILE, u64::MAX);
    assert!(controls.automation.suppressed.contains(PROFILE));
    Ok(())
}

#[tokio::test]
async fn two_real_authenticated_observations_admit_one_durable_job_and_stop_joins_it() -> TestResult
{
    for framed in [false, true] {
        let mut f = Fixture::new()?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let tls = configured(&mut f, listener.local_addr()?, framed)?;
        policy(&mut f, 4).await?;
        let created = create(&mut f, 600, true).await?;
        let peer = tokio::spawn(crate::jobs::management_tests::serve_catalog_count(
            tls, listener, 2,
        ));
        let (stop, stopping) = watch::channel(false);
        for n in 0..2 {
            if n == 1 {
                let next = f.runtime.automation.remote[&created.rule_id].next_poll;
                tokio::time::sleep_until(next).await;
            }
            f.runtime
                .automatic_tick(stopping.clone(), stopping.clone())
                .await;
            let complete = f
                .runtime
                .discoveries
                .join_next()
                .await
                .ok_or("actual automatic poll missing")??;
            f.runtime.catalog_finished(complete).await;
        }
        let network_result = peer.await;
        let capture: TestResult<_> = (|| {
            let s = committed(&f)?;
            let j = s.automation.as_ref().ok_or("journal")?;
            let origin = j.origins.first().ok_or("durable origin")?;
            Ok((
                s.jobs.len(),
                s.requests.len(),
                j.origins.len(),
                origin.job_id.clone(),
                origin.request_id.clone(),
                j.events
                    .iter()
                    .filter(|e| e.kind == RuleEventKind::Fired)
                    .count(),
                f.runtime.workers.len(),
            ))
        })();
        let mut stop_accepted = false;
        if let Ok((_, _, _, id, _, _, _)) = &capture {
            stop_accepted = matches!(
                f.runtime
                    .command(operator_uid(), Request::StopJob { job_id: id.clone() })
                    .await,
                Response::StopAccepted {
                    owner_present: true
                }
            );
        }
        stop.send_replace(true);
        let Fixture {
            root: _root,
            path,
            runtime,
        } = f;
        let (_commands, receiver) = mpsc::channel(1);
        let ended = runtime.run(receiver, stopping).await;
        network_result??;
        ended?;
        let (jobs, requests, origins, id, request_id, firings, owners) = capture?;
        let final_state = State::decode(&std::fs::read(path.join("state.json"))?)?;
        assert_eq!((jobs, requests, origins, firings, owners), (1, 1, 1, 1, 1));
        assert!(stop_accepted);
        assert_eq!(final_state.jobs[0].id, id);
        assert_eq!(final_state.jobs[0].state, JobState::Finished);
        // Stop commits before this fixture starts processing Begin. The existing
        // admission returns State("job is stopping"); preserve that exact result
        // independently of the no-resource/actual-owner-joined outcome.
        assert_eq!(final_state.jobs[0].operation_error, Some(Failure::State));
        assert!(final_state.sessions.is_empty());
        assert_eq!(final_state.requests[0].id, request_id);
        assert!(
            final_state
                .sessions
                .iter()
                .all(|s| s.attempts.is_empty() && s.cleanup == Some(Cleanup::NotStarted))
        );
        assert!(final_state.automation.as_ref().ok_or("journal")?.rules[0].paused_by_user);
        assert_eq!(
            final_state
                .audit
                .iter()
                .filter(|e| e.action == AuditAction::Discover)
                .count(),
            2
        );
        assert_eq!(
            final_state
                .audit
                .iter()
                .filter(|e| e.action == AuditAction::Start)
                .count(),
            1
        );
    }
    Ok(())
}

async fn old_origin(f: &mut Fixture, rule: &RuleMutationOutcome) -> TestResult<(JobId, RequestId)> {
    let job = Job {
        id: JobId::new(uuid(700))?,
        instance_id: f.runtime.instance.clone(),
        profile: PROFILE.into(),
        operator_uid: operator_uid(),
        state: JobState::Finished,
        stop_requested: false,
        operation_error: None,
    };
    let request_id = request(701)?;
    let mut state = f.runtime.state.clone();
    state.jobs.push(job.clone());
    state.requests.push(RequestRecord {
        id: request_id.clone(),
        uid: operator_uid(),
        profile: PROFILE.into(),
        job_id: job.id.clone(),
    });
    state.audit(
        operator_uid(),
        Some(PROFILE.into()),
        Some(job.id.clone()),
        None,
        AuditAction::Start,
        None,
    )?;
    state
        .automation
        .as_mut()
        .ok_or("journal")?
        .fire(RuleOrigin {
            sequence: 0,
            rule_id: rule.rule_id.clone(),
            revision: rule.revision,
            operator_uid: operator_uid(),
            profile: PROFILE.into(),
            observation: token('a')?,
            request_id: request_id.clone(),
            job_id: job.id.clone(),
        })?;
    f.runtime.persist(state).await?;
    Ok((job.id, request_id))
}

#[tokio::test]
async fn firing_intent_is_atomic_and_a_stop_during_commit_stops_late_registration() -> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    policy(&mut f, 4).await?;
    let rule = create(&mut f, 702, true).await?;
    let (old, _) = old_origin(&mut f, &rule).await?;
    let controls = f.runtime.controls.clone();
    let config = f.runtime.config.clone();
    let path = f.path.clone();
    let gate = Gate::hold(&f.runtime).await?;
    let (waiting, before, started) = {
        let admitting = f.runtime.start_with_origin(
            operator_uid(),
            PROFILE.into(),
            request(703)?,
            false,
            Some(RuleAdmission {
                id: rule.rule_id.clone(),
                revision: 1,
                token: token('b')?,
                source_at: Instant::now(),
            }),
        );
        tokio::pin!(admitting);
        let polled = tokio::time::timeout(Duration::from_millis(20), &mut admitting).await;
        let waiting = polled.is_err();
        let before = State::decode(&std::fs::read(path.join("state.json"))?)?;
        let owner = controls.lock().map_err(|_| lock_error())?.request(
            &config,
            operator_uid(),
            &Request::StopJob {
                job_id: old.clone(),
            },
        );
        gate.finish().await?;
        let result = match polled {
            Ok(value) => value,
            Err(_) => admitting.await,
        };
        (waiting, before, (owner, result))
    };
    let capture: TestResult<_> = (|| {
        if started.0? {
            return Err("old empty intent unexpectedly had an owner".into());
        }
        let Response::Started {
            job,
            replayed: false,
        } = started.1?
        else {
            return Err("original automatic Start missing".into());
        };
        let inherited = controls
            .lock()
            .map_err(|_| lock_error())?
            .jobs
            .get(&job.id)
            .is_some_and(|c| *c.stop.borrow());
        let after = committed(&f)?;
        let j = after.automation.as_ref().ok_or("journal")?;
        let origin = j
            .origins
            .iter()
            .find(|o| o.job_id == job.id)
            .ok_or("same transaction origin")?;
        Ok((
            job.id.clone(),
            inherited,
            after
                .requests
                .iter()
                .any(|r| r.id == origin.request_id && r.job_id == job.id),
            j.origins.len(),
        ))
    })();
    f.runtime
        .command(operator_uid(), Request::StopJob { job_id: old })
        .await;
    let (stop, stopping) = watch::channel(false);
    crate::ipc::request_shutdown(&controls, &stop)?;
    let Fixture {
        root: _root,
        path,
        runtime,
    } = f;
    let (_sender, commands) = mpsc::channel(1);
    let ended = runtime.run(commands, stopping).await;
    ended?;
    let (job, inherited, associated, origins) = capture?;
    let final_state = State::decode(&std::fs::read(path.join("state.json"))?)?;
    assert!(waiting && inherited && associated);
    assert_eq!(before.jobs.len(), 1);
    assert_eq!(before.automation.ok_or("before journal")?.origins.len(), 1);
    assert_eq!(origins, 2);
    let job = final_state
        .jobs
        .iter()
        .find(|j| j.id == job)
        .ok_or("finished late owner")?;
    assert_eq!(job.state, JobState::Finished);
    assert_eq!(job.operation_error, Some(Failure::State));
    assert!(final_state.sessions.is_empty());
    assert!(
        final_state
            .sessions
            .iter()
            .all(|s| s.attempts.is_empty() && s.cleanup == Some(Cleanup::NotStarted))
    );
    assert!(final_state.automation.ok_or("journal")?.rules[0].paused_by_user);
    Ok(())
}

#[tokio::test]
async fn retained_origin_resolves_start_retry_after_fifo_eviction_without_new_admission()
-> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    policy(&mut f, 4).await?;
    let rule = create(&mut f, 710, true).await?;
    let (job, request_id) = old_origin(&mut f, &rule).await?;
    let mut next = f.runtime.state.clone();
    next.requests.clear();
    f.runtime.persist(next).await?;
    let bytes = std::fs::read(f.path.join("state.json"))?;
    for retry in [false, true] {
        assert!(
            matches!(f.runtime.start(operator_uid(),PROFILE.into(),request_id.clone(),retry).await?,Response::Started{job:old,replayed:true} if old.id==job)
        );
    }
    assert!(
        f.runtime
            .start(other_uid(), PROFILE.into(), request_id, true)
            .await
            .is_err()
    );
    assert_eq!(std::fs::read(f.path.join("state.json"))?, bytes);
    assert!(f.runtime.workers.is_empty());
    Ok(())
}

#[tokio::test]
async fn overlapping_native_usbfs_scope_blocks_live_and_unconfirmed_metadata() -> TestResult {
    let mut f = Fixture::new()?;
    let (tls, _) = crate::jobs::management_tests::credentials(false)?;
    let key = DeviceKey::native(&format!("usb-{:064x}", 1))?;
    let config = Arc::get_mut(&mut f.runtime.config).ok_or("unshared config")?;
    config.manifest.profiles[0].backend = Backend::Native;
    config.credentials.insert(
        PROFILE.into(),
        Credentials::Server(Arc::new(LoadedServerConfig {
            broker_uid: config.manifest.broker_uid,
            listen: "127.0.0.1:0".parse()?,
            devices: vec![key.clone()],
            tls: tls.clone(),
        })),
    );
    config.manifest.profiles.push(Profile {
        id: "other-local".into(),
        backend: Backend::Usbfs,
        config: "/etc/rustusb/other.json".into(),
    });
    config.credentials.insert(
        "other-local".into(),
        Credentials::Server(Arc::new(LoadedServerConfig {
            broker_uid: config.manifest.broker_uid,
            listen: "127.0.0.1:0".parse()?,
            devices: vec![DeviceKey::physical(
                key.fingerprint().ok_or("fingerprint")?,
            )?],
            tls,
        })),
    );
    let raw = json!({"schema_version":1,"broker_uid":config.manifest.broker_uid,"grants":[{"operator_uid":operator_uid(),"profile":PROFILE,"devices":[key],"triggers":["local_profile_ready"],"rearm":["new_observation"],"max_rules":4}]});
    let policy = AutomationPolicy::fixture(&serde_json::to_vec(&raw)?, config)?;
    // Setup only: no monitor or USB runner is launched by this metadata fixture.
    f.runtime.automation.policy = Some(Arc::new(policy));
    let mut next = f.runtime.state.clone();
    next.enable_automation()?;
    f.runtime.persist(next).await?;
    let out = f
        .runtime
        .mutate_rule(
            operator_uid(),
            PROFILE.into(),
            request(720)?,
            Some(RuleMutation::Create {
                spec: RuleSpec::new(
                    "local".into(),
                    TriggerKind::LocalProfileReady,
                    2,
                    RearmKind::NewObservation,
                )?,
                enabled: true,
            }),
        )
        .await?;
    assert!(matches!(out, Response::RuleMutationApplied { .. }));
    let rule = f.runtime.state.automation.as_ref().ok_or("journal")?.rules[0].clone();
    assert!(!f.runtime.local_scope_busy(&rule));
    let mut next = f.runtime.state.clone();
    next.jobs.push(Job {
        id: JobId::new(uuid(721))?,
        instance_id: f.runtime.instance.clone(),
        profile: "other-local".into(),
        operator_uid: operator_uid(),
        state: JobState::Recovering,
        stop_requested: true,
        operation_error: Some(Failure::State),
    });
    f.runtime.persist(next).await?;
    assert!(f.runtime.local_scope_busy(&rule));
    let mut next = f.runtime.state.clone();
    next.jobs[0].state = JobState::Finished;
    next.sessions.push(Session {
        id: SessionId::new(uuid(722))?,
        instance_id: f.runtime.instance.clone(),
        job_id: next.jobs[0].id.clone(),
        profile: "other-local".into(),
        role: SessionRole::Export,
        device_key: DeviceKey::physical(&format!("usb-{:064x}", 1))?,
        principal: None,
        state: SessionState::Error,
        evidence: Evidence::Unconfirmed,
        stop_requested: true,
        terminal: true,
        operation_error: Some(Failure::State),
        cleanup: Some(Cleanup::Unconfirmed),
        metrics: Metrics::default(),
        attempts: vec![],
    });
    next.initialize_legacy_links();
    f.runtime.persist(next).await?;
    assert!(f.runtime.local_scope_busy(&rule));
    let mut next = f.runtime.state.clone();
    next.sessions[0].cleanup = Some(Cleanup::Verified);
    next.sessions[0].state = SessionState::Disconnected;
    next.sessions[0].evidence = Evidence::Released;
    f.runtime.persist(next).await?;
    assert!(!f.runtime.local_scope_busy(&rule));
    Ok(())
}

async fn send_command(
    sender: &mpsc::Sender<Command>,
    uid: u32,
    request: Request,
) -> TestResult<oneshot::Receiver<ResponseEnvelope>> {
    let (reply, received) = oneshot::channel();
    sender
        .send(Command {
            uid,
            request,
            reply,
        })
        .await?;
    Ok(received)
}
async fn client_hello(listener: &TcpListener) -> TestResult<tokio::net::TcpStream> {
    let (mut peer, _) = listener.accept().await?;
    let mut header = [0; 5];
    peer.read_exact(&mut header).await?;
    if header[0] != 22 {
        return Err("actual TLS ClientHello absent".into());
    }
    let mut body = vec![0; usize::from(u16::from_be_bytes([header[3], header[4]]))];
    peer.read_exact(&mut body).await?;
    Ok(peer)
}

#[tokio::test]
async fn automatic_poll_keeps_its_actual_slot_after_disable_and_leaves_one_manual_slot()
-> TestResult {
    for eof in [false, true] {
        let mut f = Fixture::new()?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let _tls = configured(&mut f, listener.local_addr()?, false)?;
        policy(&mut f, 4).await?;
        let created = create(&mut f, 800, true).await?;
        let controls = f.runtime.controls.clone();
        let helper = f.runtime.config.manifest.helper_socket.clone();
        let Fixture {
            root: _root,
            path,
            runtime,
        } = f;
        let (commands, receiver) = mpsc::channel(16);
        let (stop, stopping) = watch::channel(false);
        let owner = tokio::spawn(runtime.run(receiver, stopping));
        let gathered: TestResult<_> = async {
            let auto =
                tokio::time::timeout(Duration::from_secs(3), client_hello(&listener)).await??;
            let manual = send_command(
                &commands,
                operator_uid(),
                Request::Discover {
                    profile: PROFILE.into(),
                },
            )
            .await?;
            let other =
                tokio::time::timeout(Duration::from_secs(3), client_hello(&listener)).await??;
            drop(manual);
            let disabled = send_command(
                &commands,
                operator_uid(),
                Request::MutateRule {
                    profile: PROFILE.into(),
                    request_id: request(801)?,
                    mutation: RuleMutation::SetEnabled {
                        rule_id: created.rule_id,
                        expected_revision: ExpectedRevision::new(1)?,
                        enabled: false,
                    },
                },
            )
            .await?
            .await?;
            let full = send_command(
                &commands,
                operator_uid(),
                Request::Discover {
                    profile: PROFILE.into(),
                },
            )
            .await?
            .await?;
            let denied_uid = send_command(
                &commands,
                other_uid(),
                Request::Discover {
                    profile: PROFILE.into(),
                },
            )
            .await?
            .await?;
            let denied_profile = send_command(
                &commands,
                operator_uid(),
                Request::Discover {
                    profile: "unconfigured".into(),
                },
            )
            .await?
            .await?;
            let profiles = send_command(&commands, operator_uid(), Request::Profiles).await?;
            let responsive = matches!(
                tokio::time::timeout(Duration::from_millis(200), profiles).await,
                Ok(Ok(ResponseEnvelope {
                    response: Response::Profiles { .. },
                    ..
                }))
            );
            Ok((
                auto,
                other,
                disabled,
                full,
                denied_uid,
                denied_profile,
                responsive,
            ))
        }
        .await;
        let shutdown = if eof {
            drop(commands);
            Ok(())
        } else {
            let result = crate::ipc::request_shutdown(&controls, &stop);
            drop(commands);
            result
        };
        let observations = match gathered {
            Ok((mut auto, mut other, disabled, full, denied_uid, denied_profile, responsive)) => {
                let auto_closed = matches!(
                    tokio::time::timeout(Duration::from_millis(200), auto.read(&mut [0])).await,
                    Ok(Ok(0))
                );
                let manual_closed = matches!(
                    tokio::time::timeout(Duration::from_millis(200), other.read(&mut [0])).await,
                    Ok(Ok(0))
                );
                Ok((
                    disabled,
                    full,
                    denied_uid,
                    denied_profile,
                    responsive,
                    auto_closed,
                    manual_closed,
                ))
            }
            Err(error) => Err(error),
        };
        let ended = owner.await;
        ended??;
        shutdown?;
        let (disabled, full, denied_uid, denied_profile, responsive, auto_closed, manual_closed) =
            observations?;
        assert!(matches!(
            disabled.response,
            Response::RuleMutationApplied {
                replayed: false,
                ..
            }
        ));
        assert!(matches!(
            full.response,
            Response::Error {
                code: Failure::State
            }
        ));
        assert!(matches!(
            denied_uid.response,
            Response::Error {
                code: Failure::Permission
            }
        ));
        assert!(matches!(
            denied_profile.response,
            Response::Error {
                code: Failure::Permission
            }
        ));
        assert!(
            responsive && auto_closed && manual_closed,
            "original automatic/manual sockets did not close and join on the same stop/EOF"
        );
        let final_state = State::decode(&std::fs::read(path.join("state.json"))?)?;
        assert!(final_state.jobs.is_empty() && final_state.sessions.is_empty());
        assert_eq!(
            final_state
                .audit
                .iter()
                .filter(|e| e.action == AuditAction::Denied)
                .count(),
            2
        );
        assert_eq!(
            final_state
                .audit
                .iter()
                .filter(|e| e.action == AuditAction::Discover)
                .count(),
            0
        );
        assert!(!helper.exists());
    }
    Ok(())
}

async fn two_monitor_permits_after_stop() -> TestResult<bool> {
    let deadline = Instant::now() + Duration::from_secs(1);
    loop {
        let first = crate::discovery::DeviceMonitor::open().await?;
        let second = crate::discovery::DeviceMonitor::open().await;
        match second {
            Ok(second) => {
                let a = first.shutdown().await;
                let b = second.shutdown().await;
                a?;
                b?;
                return Ok(true);
            }
            Err(error) => {
                first.shutdown().await?;
                if !matches!(&error,Error::State(message) if message=="local discovery workers are busy")
                {
                    return Err(error.into());
                }
                if Instant::now() >= deadline {
                    return Ok(false);
                }
                tokio::task::yield_now().await;
            }
        }
    }
}

#[tokio::test]
async fn actual_read_only_observer_stops_and_returns_worker_permit_before_wal_ack() -> TestResult {
    let mut f = Fixture::new()?;
    let (tls, _) = crate::jobs::management_tests::credentials(false)?;
    let key = DeviceKey::native(&format!("usb-{:064x}", 1))?;
    let config = Arc::get_mut(&mut f.runtime.config).ok_or("unshared config")?;
    config.manifest.profiles[0].backend = Backend::Native;
    config.credentials.insert(
        PROFILE.into(),
        Credentials::Server(Arc::new(LoadedServerConfig {
            broker_uid: config.manifest.broker_uid,
            listen: "127.0.0.1:0".parse()?,
            devices: vec![key.clone()],
            tls,
        })),
    );
    let raw = json!({"schema_version":1,"broker_uid":config.manifest.broker_uid,"grants":[{"operator_uid":operator_uid(),"profile":PROFILE,"devices":[key],"triggers":["local_profile_ready"],"rearm":["once"],"max_rules":4}]});
    let policy = AutomationPolicy::fixture(&serde_json::to_vec(&raw)?, config)?;
    f.runtime.configure_automation(Some(policy)).await?;
    let controls = f.runtime.controls.clone();
    let store = f.runtime.store.clone();
    let helper = f.runtime.config.manifest.helper_socket.clone();
    let Fixture {
        root: _root,
        path,
        runtime,
    } = f;
    let (commands, receiver) = mpsc::channel(16);
    let (stop, stopping) = watch::channel(false);
    let owner = tokio::spawn(runtime.run(receiver, stopping));
    let gathered: TestResult<_> = async {
        // This response is processed only after the actual monitor was prepared.
        let ready = send_command(&commands, operator_uid(), Request::Profiles)
            .await?
            .await?;
        let gate = Gate::hold_store(store).await?;
        let attempted: TestResult<_> = async {
            let mut mutation = send_command(
                &commands,
                operator_uid(),
                Request::MutateRule {
                    profile: PROFILE.into(),
                    request_id: request(900)?,
                    mutation: RuleMutation::Create {
                        spec: RuleSpec::new(
                            "disabled fixture".into(),
                            TriggerKind::LocalProfileReady,
                            2,
                            RearmKind::Once,
                        )?,
                        enabled: false,
                    },
                },
            )
            .await?;
            let pending = tokio::time::timeout(Duration::from_millis(30), &mut mutation)
                .await
                .is_err();
            let stopped = crate::ipc::request_shutdown(&controls, &stop);
            // These ordinary read-only monitors inspect no USB node or helper and
            // print no host inventory. Acquiring both finite permits proves that
            // the original blocking worker actually ended while the WAL stayed gated.
            let permits = two_monitor_permits_after_stop().await;
            let still_pending = matches!(
                mutation.try_recv(),
                Err(oneshot::error::TryRecvError::Empty)
            );
            Ok((ready, pending, stopped, permits, still_pending, mutation))
        }
        .await;
        // Even an observation error must release and actually join this gate.
        let released = gate.finish().await;
        let (ready, pending, stopped, permits, still_pending, mutation) = attempted?;
        let mutation = mutation.await;
        Ok((
            ready,
            pending,
            stopped,
            permits,
            still_pending,
            released,
            mutation,
        ))
    }
    .await;
    let shutdown = crate::ipc::request_shutdown(&controls, &stop);
    drop(commands);
    let ended = owner.await;
    ended??;
    shutdown?;
    let (ready, pending, stopped, permits, still_pending, released, mutation) = gathered?;
    stopped?;
    released?;
    let permits = permits?;
    let mutation = mutation?;
    assert!(matches!(ready.response, Response::Profiles { .. }));
    assert!(matches!(
        mutation.response,
        Response::RuleMutationApplied { .. }
    ));
    assert!(
        pending && still_pending && permits,
        "actual observer kept a permit or ACK escaped the held WAL"
    );
    let state = State::decode(&std::fs::read(path.join("state.json"))?)?;
    assert!(state.jobs.is_empty() && state.sessions.is_empty());
    assert!(!helper.exists());
    Ok(())
}

#[path = "runtime_rule_decision_tests.rs"]
mod decisions;

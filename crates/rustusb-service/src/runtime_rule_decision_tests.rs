//! Real atomic journals and retained Store tasks; no helper/USB/device claims.
use super::*;
use crate::store::Store;
use std::os::unix::fs::PermissionsExt;

async fn decisions(f: &mut Fixture, max: u8) -> TestResult {
    let config = &f.runtime.config;
    let value = json!({"schema_version":2,"settled_rule_results":true,"broker_uid":config.manifest.broker_uid,"grants":[{"operator_uid":operator_uid(),"profile":PROFILE,"devices":["virtual:hid"],"triggers":["remote_device_available"],"rearm":["once","new_observation"],"max_rules":max}]});
    let bytes = serde_json::to_vec(&value)?;
    assert!(AutomationPolicy::fixture(&bytes, config).is_err());
    let policy = AutomationPolicy::fixture_decisions(&bytes, config)?;
    assert!(policy.settled_rule_results());
    f.runtime.configure_automation(Some(policy)).await?;
    Ok(())
}
fn create_edit() -> Result<RuleMutation> {
    Ok(RuleMutation::Create {
        spec: spec()?,
        enabled: false,
    })
}
fn applied(response: Response) -> TestResult<RuleMutationOutcome> {
    match response {
        Response::RuleDecided {
            decision: RuleDecision::Applied { outcome },
            replayed: false,
            ..
        } => Ok(outcome),
        other => Err(format!("not an actual applied edit: {other:?}").into()),
    }
}

#[tokio::test]
async fn explicit_root2_and_disk4_migration_freeze_legacy_shapes_and_reopen_real_store()
-> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    policy(&mut f, 2).await?;
    let old = create(&mut f, 701, false).await?;
    let positive = serde_json::to_value(committed(&f)?.automation)?;
    decisions(&mut f, 2).await?;
    let disk = committed(&f)?;
    assert_eq!(disk.schema_version, 4);
    assert_eq!(serde_json::to_value(&disk.automation)?, positive);
    assert!(
        disk.rule_rejections
            .as_ref()
            .ok_or("rejections")?
            .entries
            .is_empty()
    );
    let complete = serde_json::to_value(&disk)?;
    for field in ["automation", "rule_rejections"] {
        let mut missing = complete.clone();
        missing.as_object_mut().ok_or("state object")?.remove(field);
        assert!(State::decode(&serde_json::to_vec(&missing)?).is_err());
        let mut null = complete.clone();
        null[field] = serde_json::Value::Null;
        assert!(State::decode(&serde_json::to_vec(&null)?).is_err());
    }
    for version in [1, 2, 3, 5] {
        let mut wrong = complete.clone();
        wrong["schema_version"] = json!(version);
        assert!(State::decode(&serde_json::to_vec(&wrong)?).is_err());
    }
    let mut extra = complete.clone();
    extra["rule_rejections"]["unknown"] = json!(true);
    assert!(State::decode(&serde_json::to_vec(&extra)?).is_err());
    let mut legacy = State::default();
    legacy.enable_automation()?;
    let mut wrong = serde_json::to_value(legacy)?;
    wrong["rule_rejections"] = json!({"sequence":0,"entries":[]});
    assert!(State::decode(&serde_json::to_vec(&wrong)?).is_err());
    f.runtime.configure_automation(None).await?;
    assert_eq!(committed(&f)?.schema_version, 4);
    let replay = f
        .runtime
        .decide_rule(operator_uid(), PROFILE.into(), request(701)?, None)
        .await?;
    assert!(
        matches!(replay, Response::RuleDecided { decision: RuleDecision::Applied { outcome }, replayed: true, .. } if outcome == old)
    );
    let path = f.path.clone();
    drop(f.runtime);
    let reopened = Store::fixture(&path, f.root.path())?;
    assert_eq!(reopened.state.schema_version, 4);
    assert_eq!(serde_json::to_value(&reopened.state.automation)?, positive);
    Ok(())
}

#[tokio::test]
async fn permission_rejection_is_durable_once_and_cannot_change_after_grant_or_protocol_change()
-> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    decisions(&mut f, 2).await?;
    let edit = create_edit()?;
    let first = f
        .runtime
        .decide_rule(
            other_uid(),
            PROFILE.into(),
            request(710)?,
            Some(edit.clone()),
        )
        .await?;
    assert!(matches!(
        first,
        Response::RuleDecided {
            decision: RuleDecision::Rejected {
                sequence: 1,
                failure: Failure::Permission
            },
            replayed: false,
            ..
        }
    ));
    let state = committed(&f)?;
    assert!(state.jobs.is_empty() && state.sessions.is_empty() && state.requests.is_empty());
    assert_eq!(state.audit.len(), 1);
    assert_eq!(state.audit[0].action, AuditAction::Denied);
    assert_eq!(state.audit[0].actor_uid, other_uid());
    assert_eq!(
        state
            .rule_rejections
            .as_ref()
            .ok_or("rejection")?
            .entries
            .len(),
        1
    );
    let bytes = std::fs::read(f.path.join("state.json"))?;
    for mutation in [Some(edit.clone()), None] {
        let replay = f
            .runtime
            .decide_rule(other_uid(), PROFILE.into(), request(710)?, mutation)
            .await?;
        assert!(matches!(
            replay,
            Response::RuleDecided {
                decision: RuleDecision::Rejected {
                    sequence: 1,
                    failure: Failure::Permission
                },
                replayed: true,
                ..
            }
        ));
    }
    assert!(matches!(
        f.runtime
            .mutate_rule(
                other_uid(),
                PROFILE.into(),
                request(710)?,
                Some(edit.clone())
            )
            .await?,
        Response::Error {
            code: Failure::Permission
        }
    ));
    assert!(
        f.runtime
            .decide_rule(operator_uid(), PROFILE.into(), request(710)?, None)
            .await
            .is_err()
    );
    let mut changed = edit.clone();
    if let RuleMutation::Create { enabled, .. } = &mut changed {
        *enabled = true;
    }
    assert!(
        f.runtime
            .decide_rule(other_uid(), PROFILE.into(), request(710)?, Some(changed))
            .await
            .is_err()
    );
    assert_eq!(std::fs::read(f.path.join("state.json"))?, bytes);
    let config = Arc::get_mut(&mut f.runtime.config).ok_or("unshared config")?;
    config.manifest.operators[1]
        .actions
        .extend([Action::Stop, Action::Discover]);
    let value = json!({"schema_version":2,"settled_rule_results":true,"broker_uid":config.manifest.broker_uid,"grants":[{"operator_uid":other_uid(),"profile":PROFILE,"devices":["virtual:hid"],"triggers":["remote_device_available"],"rearm":["once","new_observation"],"max_rules":2}]});
    let new_policy = AutomationPolicy::fixture_decisions(&serde_json::to_vec(&value)?, config)?;
    f.runtime.configure_automation(Some(new_policy)).await?;
    let replay = f
        .runtime
        .decide_rule(other_uid(), PROFILE.into(), request(710)?, Some(edit))
        .await?;
    assert!(matches!(
        replay,
        Response::RuleDecided {
            decision: RuleDecision::Rejected { .. },
            replayed: true,
            ..
        }
    ));
    assert_eq!(std::fs::read(f.path.join("state.json"))?, bytes);
    assert!(!f.runtime.config.manifest.helper_socket.exists());
    Ok(())
}

#[tokio::test]
async fn cas_and_capacity_rejections_leave_rules_unchanged_until_an_explicit_new_edit() -> TestResult
{
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    assert!(
        f.runtime
            .decide_rule(
                operator_uid(),
                PROFILE.into(),
                request(720)?,
                Some(create_edit()?)
            )
            .await
            .is_err()
    );
    assert_eq!(f.runtime.state.schema_version, 2);
    decisions(&mut f, 1).await?;
    let created = applied(
        f.runtime
            .decide_rule(
                operator_uid(),
                PROFILE.into(),
                request(721)?,
                Some(create_edit()?),
            )
            .await?,
    )?;
    let capacity = f
        .runtime
        .decide_rule(
            operator_uid(),
            PROFILE.into(),
            request(722)?,
            Some(create_edit()?),
        )
        .await?;
    assert!(matches!(
        capacity,
        Response::RuleDecided {
            decision: RuleDecision::Rejected {
                failure: Failure::State,
                ..
            },
            ..
        }
    ));
    let stale = RuleMutation::Delete {
        rule_id: created.rule_id.clone(),
        expected_revision: ExpectedRevision::new(2)?,
    };
    let refusal = f
        .runtime
        .decide_rule(operator_uid(), PROFILE.into(), request(723)?, Some(stale))
        .await?;
    assert!(matches!(
        refusal,
        Response::RuleDecided {
            decision: RuleDecision::Rejected {
                sequence: 2,
                failure: Failure::State
            },
            replayed: false,
            ..
        }
    ));
    assert_eq!(
        f.runtime.state.automation.as_ref().ok_or("rules")?.rules[0].revision,
        1
    );
    let changed = applied(
        f.runtime
            .decide_rule(
                operator_uid(),
                PROFILE.into(),
                request(724)?,
                Some(RuleMutation::SetEnabled {
                    rule_id: created.rule_id,
                    expected_revision: ExpectedRevision::new(1)?,
                    enabled: false,
                }),
            )
            .await?,
    )?;
    assert_eq!(changed.revision, 2);
    let before = std::fs::read(f.path.join("state.json"))?;
    assert!(matches!(
        f.runtime
            .decide_rule(operator_uid(), PROFILE.into(), request(725)?, None)
            .await?,
        Response::RuleDecisionExpired { .. }
    ));
    assert!(matches!(
        f.runtime
            .mutate_rule(operator_uid(), PROFILE.into(), request(723)?, None)
            .await?,
        Response::Error {
            code: Failure::State
        }
    ));
    assert_eq!(std::fs::read(f.path.join("state.json"))?, before);
    assert_eq!(
        f.runtime
            .state
            .automation
            .as_ref()
            .ok_or("rules")?
            .events
            .len(),
        2
    );
    assert_eq!(
        f.runtime
            .state
            .rule_rejections
            .as_ref()
            .ok_or("rejections")?
            .entries
            .len(),
        2
    );
    Ok(())
}

#[tokio::test]
async fn actual_writer_identity_failure_never_returns_a_settled_rejection() -> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    decisions(&mut f, 2).await?;
    let bytes = std::fs::read(f.path.join("state.json"))?;
    std::fs::rename(
        f.path.join("state.json"),
        f.path.join("displaced-state.json"),
    )?;
    std::fs::write(f.path.join("state.json"), &bytes)?;
    std::fs::set_permissions(
        f.path.join("state.json"),
        std::fs::Permissions::from_mode(0o600),
    )?;
    assert!(
        f.runtime
            .decide_rule(
                other_uid(),
                PROFILE.into(),
                request(730)?,
                Some(create_edit()?)
            )
            .await
            .is_err()
    );
    assert!(!f.runtime.healthy);
    assert!(
        f.runtime
            .state
            .rule_rejections
            .as_ref()
            .ok_or("rejections")?
            .entries
            .is_empty()
    );
    assert_eq!(std::fs::read(f.path.join("state.json"))?, bytes);
    assert!(matches!(
        f.runtime
            .decide_rule(other_uid(), PROFILE.into(), request(730)?, None)
            .await?,
        Response::RuleDecisionExpired { .. }
    ));
    assert!(
        f.runtime
            .decide_rule(
                operator_uid(),
                PROFILE.into(),
                request(731)?,
                Some(create_edit()?)
            )
            .await
            .is_err()
    );
    Ok(())
}

async fn postcommit_registry_error(positive: bool) -> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    decisions(&mut f, 2).await?;
    let store = f.runtime.store.clone();
    let controls = f.runtime.controls.clone();
    let (ready, received) = oneshot::channel();
    let (release, released) = std::sync::mpsc::channel();
    let holder = tokio::task::spawn_blocking(move || -> Result<()> {
        let _guard = store.lock().map_err(|_| lock_error())?;
        ready
            .send(())
            .map_err(|_| Error::State("decision gate observer".into()))?;
        released
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| Error::Timeout("decision gate release".into()))?;
        Ok(())
    });
    received.await?;
    let uid = if positive {
        operator_uid()
    } else {
        other_uid()
    };
    let result;
    let pending;
    {
        let future =
            f.runtime
                .decide_rule(uid, PROFILE.into(), request(740)?, Some(create_edit()?));
        tokio::pin!(future);
        pending = tokio::time::timeout(Duration::from_millis(30), &mut future)
            .await
            .is_err();
        let poisoned = std::thread::spawn(move || -> Result<()> {
            let _guard = controls.lock().map_err(|_| lock_error())?;
            std::panic::resume_unwind(Box::new("intentional postcommit registry fixture"));
        })
        .join()
        .is_err();
        // Release/join actual accepted work before any assertion on the witness.
        let released = release.send(()).is_ok();
        result = future.await;
        let joined = holder.await;
        joined??;
        assert!(pending && poisoned && released);
    }
    let response = result?;
    let state = committed(&f)?;
    if positive {
        assert!(matches!(
            response,
            Response::RuleDecided {
                decision: RuleDecision::Applied { .. },
                replayed: false,
                ..
            }
        ));
        assert_eq!(state.automation.as_ref().ok_or("rules")?.mutations.len(), 1);
        assert!(
            state
                .rule_rejections
                .as_ref()
                .ok_or("rejections")?
                .entries
                .is_empty()
        );
    } else {
        assert!(matches!(
            response,
            Response::RuleDecided {
                decision: RuleDecision::Rejected {
                    failure: Failure::Permission,
                    ..
                },
                replayed: false,
                ..
            }
        ));
        assert_eq!(
            state
                .rule_rejections
                .as_ref()
                .ok_or("rejections")?
                .entries
                .len(),
            1
        );
        assert!(
            state
                .automation
                .as_ref()
                .ok_or("rules")?
                .mutations
                .is_empty()
        );
    }
    assert!(state.jobs.is_empty() && state.sessions.is_empty());
    assert!(f.runtime.workers.is_empty() && f.runtime.observers.is_empty());
    Ok(())
}
#[tokio::test]
async fn committed_positive_wins_over_a_real_postcommit_registry_error() -> TestResult {
    postcommit_registry_error(true).await
}
#[tokio::test]
async fn committed_rejection_survives_a_real_postcommit_registry_error() -> TestResult {
    postcommit_registry_error(false).await
}

#[tokio::test]
async fn root2_requires_its_explicit_complete_shape_and_original_grant_checks() -> TestResult {
    let mut f = Fixture::new()?;
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let _tls = configured(&mut f, listener.local_addr()?, false)?;
    let value = json!({"schema_version":2,"settled_rule_results":true,"broker_uid":f.runtime.config.manifest.broker_uid,"grants":[]});
    assert!(
        AutomationPolicy::fixture_decisions(&serde_json::to_vec(&value)?, &f.runtime.config)?
            .settled_rule_results()
    );
    for field in [
        "schema_version",
        "settled_rule_results",
        "broker_uid",
        "grants",
    ] {
        let mut missing = value.clone();
        missing.as_object_mut().ok_or("policy")?.remove(field);
        assert!(
            AutomationPolicy::fixture_decisions(&serde_json::to_vec(&missing)?, &f.runtime.config)
                .is_err()
        );
    }
    for opt in [json!(false), json!(null), json!("true")] {
        let mut bad = value.clone();
        bad["settled_rule_results"] = opt;
        assert!(
            AutomationPolicy::fixture_decisions(&serde_json::to_vec(&bad)?, &f.runtime.config)
                .is_err()
        );
    }
    let mut extra = value.clone();
    extra["endpoint"] = json!("arbitrary");
    assert!(
        AutomationPolicy::fixture_decisions(&serde_json::to_vec(&extra)?, &f.runtime.config)
            .is_err()
    );
    let mut legacy = value.clone();
    legacy["schema_version"] = json!(1);
    assert!(
        AutomationPolicy::fixture_decisions(&serde_json::to_vec(&legacy)?, &f.runtime.config)
            .is_err()
    );
    let too_large = vec![b' '; MAX_POLICY_BYTES + 1];
    assert!(AutomationPolicy::fixture_decisions(&too_large, &f.runtime.config).is_err());
    let mut wrong = value;
    wrong["grants"] = json!([{"operator_uid":operator_uid(),"profile":PROFILE,"devices":["virtual:cdc-acm"],"triggers":["remote_device_available"],"rearm":["once"],"max_rules":1}]);
    assert!(
        AutomationPolicy::fixture_decisions(&serde_json::to_vec(&wrong)?, &f.runtime.config)
            .is_err()
    );
    assert_eq!(f.runtime.state.schema_version, 2);
    Ok(())
}

#[tokio::test]
async fn authorized_support_rpc_uses_real_peer_and_bypasses_the_held_store_and_actor() -> TestResult
{
    for settled in [false, true] {
        let mut f = Fixture::new()?;
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let _tls = configured(&mut f, listener.local_addr()?, false)?;
        if settled {
            decisions(&mut f, 2).await?;
        } else {
            policy(&mut f, 2).await?;
        }
        let gate = Gate::hold(&f.runtime).await?;
        let (commands, queue) = mpsc::channel(1);
        let (mut client, peer) = UnixStream::pair()?;
        let bytes = serde_json::to_vec(&RequestEnvelope {
            schema_version: 1,
            request: Request::RuleDecisionSupport {
                profile: PROFILE.into(),
            },
        })?;
        client.write_u32(bytes.len() as u32).await?;
        client.write_all(&bytes).await?;
        let rpc = tokio::spawn(crate::ipc::request(
            peer,
            commands,
            f.runtime.config.clone(),
            f.runtime.controls.clone(),
            None,
            None,
        ));
        let reading = read_response(&mut client);
        tokio::pin!(reading);
        let first = tokio::time::timeout(Duration::from_millis(100), &mut reading).await;
        let bypassed = matches!(&first,Ok(Ok(ResponseEnvelope{response:Response::RuleDecisionSupport{enabled},..}))if *enabled==settled);
        gate.finish().await?;
        drop(queue);
        let joined = rpc.await;
        let response = match first {
            Ok(result) => result,
            Err(_) => reading.await,
        };
        joined??;
        response?;
        assert!(
            bypassed,
            "authorized support read was queued behind the actor/WAL"
        );
        assert!(!f.runtime.config.manifest.helper_socket.exists());
    }
    Ok(())
}

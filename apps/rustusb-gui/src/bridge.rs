use rustusb_core::application::{JobId, RuleId, SessionId};
use rustusb_frontend::{
    Options,
    state::{Command, Envelope, Mutation, PageKind, Snapshot, text},
};
use rustusb_service::automation::{ExpectedRevision, RuleMutation, RuleSpec, RuleView};
use serde::Deserialize;
use serde_json::{Value, json};
use std::sync::Mutex;
use tauri::Manager;
use tokio::sync::{mpsc, watch};

const MAX_VIEW: usize = 512 * 1024;
const MAX_DETAIL: usize = 64 * 1024;
struct Gate {
    sequence: u64,
    sender: mpsc::Sender<Envelope>,
}
pub(crate) struct Bridge {
    gate: Mutex<Gate>,
    views: watch::Receiver<Snapshot>,
    stop: watch::Sender<bool>,
    projection: Mutex<()>,
}
impl Bridge {
    pub(crate) fn new(
        sender: mpsc::Sender<Envelope>,
        views: watch::Receiver<Snapshot>,
        stop: watch::Sender<bool>,
    ) -> Self {
        Self {
            gate: Mutex::new(Gate {
                sequence: 0,
                sender,
            }),
            views,
            stop,
            projection: Mutex::new(()),
        }
    }
    pub(crate) fn request_stop(&self) {
        self.stop.send_replace(true);
    }
    fn action(&self, action: Action) -> Result<String, String> {
        if *self.stop.borrow() {
            return Err("GUI is stopping".into());
        }
        let mut gate = self.gate.try_lock().map_err(|_| "Action admission busy")?;
        let view = self.views.borrow();
        if gate.sequence > view.acknowledged {
            return Err("An accepted action is still pending".into());
        }
        let command = action.command(&view)?;
        let sequence = gate
            .sequence
            .checked_add(1)
            .ok_or("Action sequence exhausted")?;
        gate.sender
            .try_send(Envelope { sequence, command })
            .map_err(|_| "Worker busy or closed")?;
        gate.sequence = sequence;
        Ok(sequence.to_string())
    }
}

// No endpoint, path, credentials, arbitrary RPC or caller-chosen Start UUID.
#[derive(Deserialize)]
#[serde(tag = "action", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Action {
    Refresh {},
    Profile {
        id: String,
    },
    Remote {
        active: bool,
    },
    Automation {
        active: bool,
    },
    Surface {
        remote: bool,
        rules: bool,
    },
    RuleCreate {
        spec: RuleSpec,
    },
    RuleReplace {
        id: String,
        revision: String,
        spec: RuleSpec,
    },
    RuleSetEnabled {
        id: String,
        revision: String,
        enabled: bool,
    },
    RuleDelete {
        id: String,
        revision: String,
    },
    RuleResolve {},
    Start {},
    StopJob {
        id: String,
    },
    StopSession {
        id: String,
    },
    Options {
        refresh_seconds: u64,
        auto_refresh: bool,
    },
    SaveSettings {},
    ReloadSettings {},
    Page {
        section: Section,
        next: bool,
    },
}
#[derive(Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum Section {
    Jobs,
    Sessions,
    Audit,
    Rules,
    RuleEvents,
}
impl Action {
    fn command(self, view: &Snapshot) -> Result<Command, String> {
        Ok(match self {
            Self::Refresh {} => Command::Refresh,
            Self::Profile { id } if id.len() <= 64 && view.profiles.iter().any(|p| p.id == id) => {
                Command::Profile(id)
            }
            Self::Profile { .. } => return Err("Select a readable profile".into()),
            Self::Remote { active } => Command::Remote(active),
            Self::Automation { active } => Command::Rules(active),
            Self::Surface { remote, rules } => {
                if remote && rules {
                    return Err("Choose one observation surface".into());
                }
                Command::Observe { remote, rules }
            }
            Self::RuleResolve {} if view.unresolved_rule.is_some() => {
                Command::Mutate(Mutation::ResolveRule)
            }
            Self::RuleResolve {} => return Err("No original rule edit to query".into()),
            Self::RuleCreate { spec } => {
                rule_grant(view, &spec)?;
                rule_command(
                    view,
                    RuleMutation::Create {
                        spec,
                        enabled: false,
                    },
                )?
            }
            Self::RuleReplace { id, revision, spec } => {
                let (rule, expected_revision) = observed_rule(view, &id, &revision)?;
                rule_grant(view, &spec)?;
                rule_command(
                    view,
                    RuleMutation::Replace {
                        rule_id: rule.id.clone(),
                        expected_revision,
                        spec,
                    },
                )?
            }
            Self::RuleSetEnabled {
                id,
                revision,
                enabled,
            } => {
                let (rule, expected_revision) = observed_rule(view, &id, &revision)?;
                if enabled {
                    rule_grant(view, &rule.spec)?;
                }
                rule_command(
                    view,
                    RuleMutation::SetEnabled {
                        rule_id: rule.id.clone(),
                        expected_revision,
                        enabled,
                    },
                )?
            }
            Self::RuleDelete { id, revision } => {
                let (rule, expected_revision) = observed_rule(view, &id, &revision)?;
                rule_command(
                    view,
                    RuleMutation::Delete {
                        rule_id: rule.id.clone(),
                        expected_revision,
                    },
                )?
            }
            Self::Start {} => Command::Mutate(Mutation::Start(
                view.profile.clone().ok_or("No readable profile selected")?,
            )),
            Self::StopJob { id } => {
                let id = JobId::new(&id).map_err(|_| "Invalid job ID")?;
                if !view.jobs.iter().any(|j| j.id == id) {
                    return Err("Job is outside the current page; refresh first".into());
                }
                Command::Mutate(Mutation::StopJob(id))
            }
            Self::StopSession { id } => {
                let id = SessionId::new(&id).map_err(|_| "Invalid session ID")?;
                if !view.sessions.iter().any(|s| s.id == id) {
                    return Err("Session is outside the current page; refresh first".into());
                }
                Command::Mutate(Mutation::StopSession(id))
            }
            Self::Options {
                refresh_seconds,
                auto_refresh,
            } => {
                rustusb_service::catalog::RefreshInterval::new(refresh_seconds)
                    .map_err(|_| "Refresh must be 2..120 seconds")?;
                Command::Options(Options {
                    refresh_seconds,
                    auto_refresh,
                    ..view.options
                })
            }
            Self::SaveSettings {} => Command::SaveSettings,
            Self::ReloadSettings {} => Command::ReloadSettings,
            Self::Page { section, next } => {
                let kind = match section {
                    Section::Jobs => PageKind::Jobs,
                    Section::Sessions => PageKind::Sessions,
                    Section::Audit => PageKind::Audit,
                    Section::Rules => PageKind::Rules,
                    Section::RuleEvents => PageKind::RuleEvents,
                };
                if next {
                    Command::Next(kind)
                } else {
                    Command::First(kind)
                }
            }
        })
    }
}

fn rule_command(view: &Snapshot, mutation: RuleMutation) -> Result<Command, String> {
    if view.journal == Some(false) {
        return Err(
            "Agent journal unhealthy; new edits unavailable; original queries remain available"
                .into(),
        );
    }
    if view.unresolved_rule.is_some() {
        return Err("Original rule edit unresolved; query only its original UUID".into());
    }
    if !view.rules.requested || !view.rules.current {
        return Err("Refresh the current rule page before editing".into());
    }
    if view.rules.decision_support.is_none() {
        return Err("Rule-edit support unavailable; refresh or upgrade the agent".into());
    }
    Ok(Command::Mutate(Mutation::Rule {
        profile: view.profile.clone().ok_or("No readable profile selected")?,
        mutation,
    }))
}
fn rule_grant(view: &Snapshot, spec: &RuleSpec) -> Result<(), String> {
    let grant = view
        .rules
        .grant
        .as_ref()
        .ok_or("No automation grant for this profile")?;
    if view.rules.policy_enabled != Some(true)
        || !grant.triggers.contains(&spec.trigger())
        || !grant.rearm.contains(&spec.rearm())
    {
        return Err("Rule is outside the administrator's fixed-profile grant".into());
    }
    Ok(())
}
fn observed_rule<'a>(
    view: &'a Snapshot,
    id: &str,
    revision: &str,
) -> Result<(&'a RuleView, ExpectedRevision), String> {
    let id = RuleId::new(id).map_err(|_| "Invalid rule ID")?;
    if revision.is_empty()
        || revision.len() > 20
        || revision.starts_with('0')
        || !revision.bytes().all(|b| b.is_ascii_digit())
    {
        return Err("Revision must be a canonical positive decimal string".into());
    }
    let value = revision
        .parse::<u64>()
        .map_err(|_| "Invalid rule revision")?;
    let revision = ExpectedRevision::new(value).map_err(|_| "Exhausted rule revision")?;
    let rule = view
        .rules
        .rules
        .iter()
        .find(|r| r.id == id && Some(&r.profile) == view.profile.as_ref() && !r.deleted)
        .ok_or("Rule is outside the current page; refresh first")?;
    if rule.revision != value {
        return Err("Rule revision changed; inspect the latest state before editing again".into());
    }
    Ok((rule, revision))
}

pub(crate) fn local_origin(url: &tauri::Url) -> bool {
    url.scheme() == "tauri"
        && url.host_str() == Some("localhost")
        && url.username().is_empty()
        && url.password().is_none()
        && url.port().is_none()
}
fn authorized(webview: &tauri::WebviewWindow) -> Result<(), String> {
    if webview.label() != "main"
        || !local_origin(&webview.url().map_err(|_| "WebView origin unavailable")?)
    {
        return Err("Only the packaged main WebView is authorized".into());
    }
    Ok(())
}
fn state(webview: &tauri::WebviewWindow) -> Result<tauri::State<'_, Bridge>, String> {
    webview
        .try_state::<Bridge>()
        .ok_or_else(|| "Frontend state unavailable".into())
}
#[tauri::command]
pub(crate) fn gui_action(webview: tauri::WebviewWindow, action: Action) -> Result<String, String> {
    authorized(&webview)?;
    state(&webview)?.action(action)
}
#[tauri::command]
pub(crate) fn gui_snapshot(webview: tauri::WebviewWindow) -> Result<tauri::ipc::Response, String> {
    authorized(&webview)?;
    let state = state(&webview)?;
    // Nonwaiting application projection quota; not a claim about Tauri's earlier JSON parsing or later IPC buffering.
    let _permit = state.projection.try_lock().map_err(|_| "Projection busy")?;
    let view = state.views.borrow().clone();
    let sequence = state
        .gate
        .try_lock()
        .map_err(|_| "Action admission busy")?
        .sequence;
    let value = projection(&view, sequence)?;
    Ok(tauri::ipc::Response::new(value))
}
#[tauri::command]
pub(crate) fn gui_detail(
    webview: tauri::WebviewWindow,
    key: String,
) -> Result<tauri::ipc::Response, String> {
    authorized(&webview)?;
    if key.len() > 1024 {
        return Err("Invalid observation identity".into());
    }
    let state = state(&webview)?;
    let _permit = state.projection.try_lock().map_err(|_| "Projection busy")?;
    let view = state.views.borrow();
    let local = view.local.as_ref().ok_or("Local inventory not sampled")?;
    let found = local
        .devices
        .as_ref()
        .and_then(|items| items.iter().find(|d| identity(d) == key))
        .ok_or("Observation no longer present; select again")?;
    let value = json!({"status":view.local_status,"instance":local.instance,"revision":local.revision,"observation":found});
    Ok(tauri::ipc::Response::new(encode(value, MAX_DETAIL)?))
}
fn identity(device: &rustusb_discovery::ObservedDevice) -> String {
    format!(
        "{}:{}:{}:{}:{}",
        device.device.id,
        device.stamp.sysfs_device,
        device.stamp.sysfs_inode,
        device.stamp.bus_number,
        device.stamp.device_number
    )
}
fn projection(view: &Snapshot, sequence: u64) -> Result<String, String> {
    let local = view.local.as_ref().map(|snapshot| {
        let devices = snapshot.devices.as_ref().map(|items| items.iter().map(|d| json!({
            "key":identity(d), "bus_id":d.device.bus_id, "vid_pid":format!("{:04x}:{:04x}",d.device.vendor_id,d.device.product_id),
            "name":text(d.device.product.as_deref().or(d.device.manufacturer.as_deref()).unwrap_or("USB device"),120),
            "address":d.device.device_address,"speed":text(&d.device.speed,32),"driver":d.device.driver.as_deref().map(|v|text(v,80)),
            "exported":d.device.exported,"occupied":d.device.occupied,"eligible":d.device.export_blockers.is_empty()
        })).collect::<Vec<_>>());
        json!({"state":format!("{:?}",snapshot.state),"instance":snapshot.instance,"revision":snapshot.revision,"sampled_ms":snapshot.sampled_ms,"failure":snapshot.failure.map(|f|format!("{f:?}")),"devices":devices})
    });
    let remote = view.remote.as_ref().map(|s|json!({"instance":s.instance,"revision":s.revision,"state":s.state,"sampled_ms":s.sampled_ms,"failure":s.failure,"devices":s.devices.as_deref()}));
    encode(
        json!({
            "revision":view.revision,"acknowledged":view.acknowledged,"pending":(sequence > view.acknowledged).then_some(sequence),"profiles":view.profiles,"profile":view.profile,
            "options":view.options,"saved_options":view.saved_options,"preferences_status":view.preferences_status,
            "local":local,"remote":remote,"jobs":view.jobs,"sessions":view.sessions,"health":view.health,"audit":view.audit,
            "next_job":view.next_job,"next_session":view.next_session,"next_audit":view.next_audit,"journal":view.journal,
            "status":view.status,"jobs_status":view.jobs_status,"sessions_status":view.sessions_status,"audit_status":view.audit_status,
            "local_status":view.local_status,"remote_status":view.remote_status,"remote_page":view.remote_page,
            "unresolved":view.unresolved.as_ref().map(|i|json!({"profile":i.profile,"request_id":i.request_id}))
            ,"rules":{"requested":view.rules.requested,"current":view.rules.current,"decision_support":view.rules.decision_support,"decision_support_status":view.rules.decision_support_status,"policy_enabled":view.rules.policy_enabled,"grant":view.rules.grant,"rules":view.rules.rules,"next_rule":view.rules.next_rule,"status":view.rules.status,"events":view.rules.events,"next_event":view.rules.next_event,"events_status":view.rules.events_status,"result":view.rules.result}
            ,"unresolved_rule":view.unresolved_rule
            ,"last_rule_decision":view.last_rule_decision.as_ref().map(|r|json!({"original":r.original,"decision":r.decision,"replayed":r.replayed}))
        }),
        MAX_VIEW,
    )
}
// JavaScript never receives lossy u64 counters or observation identities.
fn stringify_numbers(value: &mut Value) {
    match value {
        Value::String(s) => *s = text(s, 4096),
        Value::Number(n) => *value = Value::String(n.to_string()),
        Value::Array(items) => items.iter_mut().for_each(stringify_numbers),
        Value::Object(fields) => fields.values_mut().for_each(stringify_numbers),
        _ => {}
    }
}
fn encode(mut value: Value, limit: usize) -> Result<String, String> {
    stringify_numbers(&mut value);
    let encoded = serde_json::to_string(&value).map_err(|_| "Projection encoding failed")?;
    if encoded.len() > limit {
        return Err(
            "Metadata projection exceeds its byte limit; no partial inventory returned".into(),
        );
    }
    Ok(encoded)
}
pub(crate) async fn stopped(mut receiver: watch::Receiver<bool>) {
    loop {
        if *receiver.borrow() || receiver.changed().await.is_err() {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn rule_view() -> Result<Snapshot, Box<dyn std::error::Error>> {
        use rustusb_service::automation::{
            AutomationGrantInfo, RearmKind, RuleStatus, TriggerKind,
        };
        let mut view = Snapshot::new(Options::default());
        view.profile = Some("fixed".into());
        view.rules.requested = true;
        view.rules.current = true;
        view.rules.decision_support = Some(false);
        view.rules.policy_enabled = Some(true);
        view.rules.grant = Some(AutomationGrantInfo {
            devices: vec![],
            triggers: vec![TriggerKind::LocalProfileReady],
            rearm: vec![RearmKind::Once],
            max_rules: 4,
        });
        view.rules.rules.push(RuleView {
            id: RuleId::new("00000000-0000-4000-8000-000000000001")?,
            revision: 9_007_199_254_740_993,
            operator_uid: 1000,
            profile: "fixed".into(),
            spec: RuleSpec::new(
                "keyboard".into(),
                TriggerKind::LocalProfileReady,
                2,
                RearmKind::Once,
            )?,
            enabled: false,
            paused_by_user: false,
            deleted: false,
            status: RuleStatus::Disabled,
            last_origin: None,
            last_job: None,
            observation_error: None,
            admission_error: None,
        });
        Ok(view)
    }
    #[test]
    fn exact_rule_revisions_reject_lossy_or_noncanonical_inputs()
    -> Result<(), Box<dyn std::error::Error>> {
        let view = rule_view()?;
        let id = view.rules.rules[0].id.to_string();
        for revision in [
            "",
            "0",
            "01",
            "+1",
            " 1",
            "1e3",
            "9007199254740992",
            "18446744073709551615",
            "18446744073709551616",
            "999999999999999999999999",
        ] {
            assert!(
                Action::RuleDelete {
                    id: id.clone(),
                    revision: revision.into()
                }
                .command(&view)
                .is_err()
            );
        }
        match (Action::RuleDelete {
            id,
            revision: "9007199254740993".into(),
        })
        .command(&view)?
        {
            Command::Mutate(Mutation::Rule {
                mutation:
                    RuleMutation::Delete {
                        expected_revision, ..
                    },
                ..
            }) => assert_eq!(expected_revision.get(), 9_007_199_254_740_993),
            _ => return Err("wrong exact mutation".into()),
        }
        Ok(())
    }
    #[test]
    fn rule_admission_requires_current_identity_and_never_invents_a_grant()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut view = rule_view()?;
        let rule = view.rules.rules[0].clone();
        view.rules.current = false;
        assert!(
            Action::RuleCreate {
                spec: rule.spec.clone()
            }
            .command(&view)
            .is_err()
        );
        view.rules.current = true;
        view.rules.grant = None;
        assert!(
            Action::RuleSetEnabled {
                id: rule.id.to_string(),
                revision: rule.revision.to_string(),
                enabled: true
            }
            .command(&view)
            .is_err()
        );
        // Losing a grant must not prevent a still-authorized future-only disable/delete.
        assert!(
            Action::RuleSetEnabled {
                id: rule.id.to_string(),
                revision: rule.revision.to_string(),
                enabled: false
            }
            .command(&view)
            .is_ok()
        );
        view.profile = Some("another".into());
        assert!(
            Action::RuleDelete {
                id: rule.id.to_string(),
                revision: rule.revision.to_string()
            }
            .command(&view)
            .is_err()
        );
        Ok(())
    }
    #[test]
    fn rule_actions_are_closed_and_create_is_always_disabled()
    -> Result<(), Box<dyn std::error::Error>> {
        let view = rule_view()?;
        let spec = view.rules.rules[0].spec.clone();
        assert!(matches!(
            Action::RuleCreate { spec: spec.clone() }.command(&view)?,
            Command::Mutate(Mutation::Rule {
                mutation: RuleMutation::Create { enabled: false, .. },
                ..
            })
        ));
        for extra in [
            json!({"enabled":true}),
            json!({"request_id":"chosen"}),
            json!({"operator_uid":0}),
            json!({"path":"/tmp/policy"}),
        ] {
            let mut value = json!({"action":"rule_create","spec":spec});
            value
                .as_object_mut()
                .ok_or("object missing")?
                .extend(extra.as_object().ok_or("extra missing")?.clone());
            assert!(serde_json::from_value::<Action>(value).is_err());
        }
        assert!(
            serde_json::from_value::<Action>(json!({"action":"rule_delete","id":"x","revision":1}))
                .is_err()
        );
        assert!(serde_json::from_value::<Action>(json!({"action":"rule_create","spec":{"name":"x","trigger":"local_profile_ready","settle_seconds":2,"rearm":"once","script":"run"}})).is_err());
        assert!(
            Action::Surface {
                remote: true,
                rules: true
            }
            .command(&view)
            .is_err()
        );
        Ok(())
    }
    #[test]
    fn rule_identity_survives_renderer_loss_and_blocks_new_edits()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut view = rule_view()?;
        let spec = view.rules.rules[0].spec.clone();
        let request =
            rustusb_core::application::RequestId::new("00000000-0000-4000-8000-000000000002")?;
        view.rule_request(
            "original".into(),
            RuleMutation::Create {
                spec: spec.clone(),
                enabled: false,
            },
            request.clone(),
        );
        assert!(Action::RuleCreate { spec }.command(&view).is_err());
        assert!(matches!(
            Action::RuleResolve {}.command(&view)?,
            Command::Mutate(Mutation::ResolveRule)
        ));
        let value: Value = serde_json::from_str(&projection(&view, 1)?)?;
        assert_eq!(value["unresolved_rule"]["request_id"], request.to_string());
        assert_eq!(value["unresolved_rule"]["profile"], "original");
        assert_eq!(value["rules"]["rules"][0]["revision"], "9007199254740993");
        assert_eq!(value["pending"], "1");
        Ok(())
    }

    #[test]
    fn unavailable_support_blocks_new_edits_but_never_redirects_original_queries()
    -> Result<(), Box<dyn std::error::Error>> {
        let mut view = rule_view()?;
        let rule = view.rules.rules[0].clone();
        view.rules.decision_support = None;
        assert!(
            Action::RuleCreate {
                spec: rule.spec.clone()
            }
            .command(&view)
            .is_err()
        );
        assert!(
            Action::RuleDelete {
                id: rule.id.to_string(),
                revision: rule.revision.to_string()
            }
            .command(&view)
            .is_err()
        );
        view.rules.decision_support = Some(true);
        view.journal = Some(false);
        assert!(
            Action::RuleCreate {
                spec: rule.spec.clone()
            }
            .command(&view)
            .is_err()
        );
        let id = rustusb_core::application::RequestId::new("00000000-0000-4000-8000-000000000002")?;
        view.rule_request_with_protocol(
            "original".into(),
            RuleMutation::Create {
                spec: rule.spec,
                enabled: false,
            },
            id.clone(),
            true,
        );
        assert!(matches!(
            Action::RuleResolve {}.command(&view)?,
            Command::Mutate(Mutation::ResolveRule)
        ));
        assert!(
            matches!(view.rule_retry(), Some(rustusb_service::rpc::Request::RetryRuleDecision { profile, request_id }) if profile == "original" && request_id == id)
        );
        Ok(())
    }
    #[test]
    fn rejected_draft_projection_retains_exact_original_revision_across_renderer_loss()
    -> Result<(), Box<dyn std::error::Error>> {
        use rustusb_service::{automation::RuleDecision, model::Failure, rpc::Response};
        let mut view = rule_view()?;
        let rule = view.rules.rules[0].clone();
        let id = rustusb_core::application::RequestId::new("00000000-0000-4000-8000-000000000002")?;
        view.rule_request_with_protocol(
            "original".into(),
            RuleMutation::Replace {
                rule_id: rule.id,
                expected_revision: ExpectedRevision::new(rule.revision)?,
                spec: rule.spec,
            },
            id.clone(),
            true,
        );
        view.rule_response(Response::RuleDecided {
            request_id: id.clone(),
            decision: RuleDecision::Rejected {
                sequence: u64::MAX,
                failure: Failure::State,
            },
            replayed: true,
        });
        let value: Value = serde_json::from_str(&projection(&view, 1)?)?;
        assert!(value["unresolved_rule"].is_null());
        assert_eq!(
            value["last_rule_decision"]["original"]["request_id"],
            id.to_string()
        );
        assert_eq!(
            value["last_rule_decision"]["original"]["mutation"]["expected_revision"],
            "9007199254740993"
        );
        assert_eq!(
            value["last_rule_decision"]["decision"]["sequence"],
            "18446744073709551615"
        );
        assert_eq!(
            value["last_rule_decision"]["original"]["profile"],
            "original"
        );
        assert_eq!(
            value["last_rule_decision"]["original"]["settled_protocol"],
            true
        );
        assert_eq!(value["pending"], "1");
        Ok(())
    }
    #[test]
    fn admission_retains_slot_after_queue_is_drained() -> Result<(), String> {
        let (tx, mut rx) = mpsc::channel(1);
        let (v, views) = watch::channel(Snapshot::new(Options::default()));
        let (stop, _) = watch::channel(false);
        let bridge = Bridge::new(tx, views, stop);
        assert_eq!(bridge.action(Action::Refresh {})?, "1");
        assert!(rx.try_recv().is_ok());
        assert!(bridge.action(Action::Refresh {}).is_err());
        v.send_modify(|view| view.acknowledged = 1);
        assert_eq!(bridge.action(Action::Refresh {})?, "2");
        bridge.request_stop();
        assert!(bridge.action(Action::Refresh {}).is_err());
        Ok(())
    }
    #[test]
    fn pending_projection_survives_renderer_state_loss() -> Result<(), String> {
        let mut view = Snapshot::new(Options::default());
        let before: Value =
            serde_json::from_str(&projection(&view, 1)?).map_err(|e| e.to_string())?;
        assert_eq!(before["pending"], "1");
        view.acknowledged = 1;
        let after: Value =
            serde_json::from_str(&projection(&view, 1)?).map_err(|e| e.to_string())?;
        assert!(after["pending"].is_null());
        Ok(())
    }
    #[test]
    fn exact_integer_and_complete_projection_bounds() -> Result<(), String> {
        let result = encode(json!({"counter":u64::MAX,"nested":[42,true]}), 1024)?;
        assert!(result.contains("\"18446744073709551615\""));
        assert!(result.contains("[\"42\",true]"));
        assert!(encode(json!({"field":"too long"}), 4).is_err());
        Ok(())
    }
    #[test]
    fn commands_reject_unobserved_identity_and_invalid_interval() {
        let view = Snapshot::new(Options::default());
        assert!(
            Action::Options {
                refresh_seconds: 1,
                auto_refresh: true
            }
            .command(&view)
            .is_err()
        );
        assert!(
            Action::Profile {
                id: "unknown".into()
            }
            .command(&view)
            .is_err()
        );
        assert!(Action::Start {}.command(&view).is_err());
        assert!(
            Action::StopJob {
                id: "00000000-0000-0000-0000-000000000001".into()
            }
            .command(&view)
            .is_err()
        );
        assert!(
            serde_json::from_str::<Action>(r#"{"action":"start","path":"/tmp/arbitrary"}"#)
                .is_err()
        );
    }
    #[test]
    fn origin_is_exact_local_and_not_a_remote_alias() -> Result<(), Box<dyn std::error::Error>> {
        for url in [
            "https://localhost/",
            "tauri://evil/",
            "tauri://localhost.evil/",
            "tauri://user@localhost/",
            "tauri://localhost:80/",
        ] {
            assert!(!local_origin(&url.parse()?));
        }
        assert!(local_origin(&"tauri://localhost/index.html".parse()?));
        Ok(())
    }
}

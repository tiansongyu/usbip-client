use rustusb_core::application::{JobId, SessionId};
use rustusb_frontend::{
    Options,
    state::{Command, Envelope, Mutation, PageKind, Snapshot, text},
};
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
    let value = projection(&view)?;
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
fn projection(view: &Snapshot) -> Result<String, String> {
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
            "revision":view.revision,"acknowledged":view.acknowledged,"profiles":view.profiles,"profile":view.profile,
            "options":view.options,"saved_options":view.saved_options,"preferences_status":view.preferences_status,
            "local":local,"remote":remote,"jobs":view.jobs,"sessions":view.sessions,"health":view.health,"audit":view.audit,
            "next_job":view.next_job,"next_session":view.next_session,"next_audit":view.next_audit,"journal":view.journal,
            "status":view.status,"jobs_status":view.jobs_status,"sessions_status":view.sessions_status,"audit_status":view.audit_status,
            "local_status":view.local_status,"remote_status":view.remote_status,"remote_page":view.remote_page,
            "unresolved":view.unresolved.as_ref().map(|i|json!({"profile":i.profile,"request_id":i.request_id}))
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

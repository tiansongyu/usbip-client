//! Local application service adapter; global signals and stdout remain CLI-owned.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{
    Error, Result,
    application::{JobId, RequestId, RuleId, SessionId},
};
use rustusb_service::{
    automation::{
        AutomationPolicy, ExpectedRevision, RearmKind, RuleMutation, RuleSpec, TriggerKind,
    },
    config::LoadedConfig,
    rpc::{Request, Response},
};
use std::path::PathBuf;
use tokio::sync::watch;

#[derive(Debug, Clone, clap::Subcommand)]
pub(super) enum Command {
    /// Own jobs until cooperative SIGINT/SIGTERM and their actual cleanup finish.
    Run {
        /// Explicit root-managed automatic-rule delegation; absent means disabled.
        #[arg(long)]
        automation_policy: Option<PathBuf>,
        /// Explicit root schema2 policy opting in to disk4 settled edit results.
        #[arg(long, conflicts_with = "automation_policy")]
        automation_decisions_policy: Option<PathBuf>,
    },
    /// List fixed profiles readable by this OS identity.
    Profiles,
    /// Read this OS user's persistent rules and current observation states.
    Rules {
        profile: String,
        #[arg(long)]
        after: Option<RuleId>,
    },
    /// Create/edit/pause/retire rules within explicit administrator delegation.
    Rule {
        profile: String,
        /// Use the explicit settled-result protocol for this edit/original query.
        #[arg(long)]
        settled: bool,
        #[command(subcommand)]
        command: RuleCommand,
    },
    /// Read the independent committed rule audit (requires Audit).
    RuleEvents {
        profile: String,
        #[arg(long, default_value_t = 0)]
        after: u64,
    },
    /// Start a fixed profile independently of this requesting process.
    Start {
        profile: String,
        /// Retry with the same UUID only within the bounded retained request window.
        #[arg(long)]
        request_id: Option<RequestId>,
        /// Retry an existing request only; expired or unknown IDs cannot start work.
        #[arg(long, requires = "request_id")]
        retry: bool,
    },
    /// Request job stop; inspect sessions for actual completion.
    StopJob { job_id: JobId },
    /// Stop one imported/incoming session, preserving other devices.
    StopSession { session_id: SessionId },
    /// Read a bounded job page.
    Jobs {
        profile: String,
        #[arg(long)]
        after: Option<JobId>,
    },
    /// Read a bounded session page, including retained previous-epoch uncertainty.
    Sessions {
        profile: String,
        #[arg(long)]
        after: Option<SessionId>,
    },
    /// Read actual same-data-link health; raw v1 reports unavailable.
    Health {
        profile: String,
        #[arg(long)]
        after: Option<SessionId>,
    },
    /// Read volatile authentication and framing counters of a fixed server profile.
    ServerSecurity { profile: String },
    /// Read configured authentication quota and pre-TLS admission totals.
    ServerAdmission { profile: String },
    /// Read retained lifecycle events; the response reports expired history.
    Events {
        profile: String,
        #[arg(long, default_value_t = 0)]
        after: u64,
    },
    /// Read minimal bounded audit history with separate authorization.
    Audit {
        profile: String,
        #[arg(long, default_value_t = 0)]
        after: u64,
    },
    /// Discover the authenticated catalog using a fixed client profile.
    Discover {
        profile: String,
        /// Refresh authorized remote snapshots without importing USB.
        #[arg(long)]
        watch: bool,
        /// Delay after completion (2..=120 seconds); failures back off to 120.
        #[arg(long, default_value_t=2, requires="watch", value_parser=clap::value_parser!(u64).range(2..=120))]
        refresh_seconds: u64,
    },
}

#[derive(Debug, Clone, Copy, clap::ValueEnum)]
pub(super) enum RuleTrigger {
    LocalProfileReady,
    RemoteDeviceAvailable,
}
#[derive(Debug, Clone, Copy, clap::ValueEnum)]
pub(super) enum RuleRearm {
    Once,
    NewObservation,
}

#[derive(Debug, Clone, clap::Args)]
pub(super) struct RuleDefinition {
    #[arg(long)]
    name: String,
    #[arg(long, value_enum)]
    trigger: RuleTrigger,
    #[arg(long, default_value_t=2, value_parser=clap::value_parser!(u16).range(2..=120))]
    settle_seconds: u16,
    #[arg(long, value_enum, default_value = "once")]
    rearm: RuleRearm,
}
impl RuleDefinition {
    fn spec(self) -> Result<RuleSpec> {
        RuleSpec::new(
            self.name,
            match self.trigger {
                RuleTrigger::LocalProfileReady => TriggerKind::LocalProfileReady,
                RuleTrigger::RemoteDeviceAvailable => TriggerKind::RemoteDeviceAvailable,
            },
            self.settle_seconds,
            match self.rearm {
                RuleRearm::Once => RearmKind::Once,
                RuleRearm::NewObservation => RearmKind::NewObservation,
            },
        )
    }
}

#[derive(Debug, Clone, clap::Args)]
pub(super) struct RuleEdit {
    rule_id: RuleId,
    #[arg(long, value_parser=clap::value_parser!(u64).range(1..u64::MAX))]
    expected_revision: u64,
    /// Preserve this original UUID if the response is unknown; use retry, not a new edit.
    #[arg(long)]
    request_id: Option<RequestId>,
}

#[derive(Debug, Clone, clap::Subcommand)]
pub(super) enum RuleCommand {
    /// Create a bounded rule; future triggering is disabled unless --enabled.
    Create {
        #[command(flatten)]
        definition: RuleDefinition,
        #[arg(long)]
        enabled: bool,
        #[arg(long)]
        request_id: Option<RequestId>,
    },
    /// Replace/rearm a definition with CAS; existing manual pauses remain.
    Replace {
        #[command(flatten)]
        edit: RuleEdit,
        #[command(flatten)]
        definition: RuleDefinition,
    },
    /// Explicitly enable/rearm future conditions after a committed pause.
    Enable(RuleEdit),
    /// Disable future triggers without asserting actual current-job cleanup.
    Disable(RuleEdit),
    /// Retire future intent while retaining unresolved old ownership.
    Delete(RuleEdit),
    /// Query only the original mutation; unknown/expired never reapplies it.
    Retry { request_id: RequestId },
}

fn mutation_request(profile: String, command: RuleCommand, settled: bool) -> Result<Request> {
    let (request_id, mutation) = match command {
        RuleCommand::Create {
            definition,
            enabled,
            request_id,
        } => (
            request_id,
            RuleMutation::Create {
                spec: definition.spec()?,
                enabled,
            },
        ),
        RuleCommand::Replace { edit, definition } => (
            edit.request_id,
            RuleMutation::Replace {
                rule_id: edit.rule_id,
                expected_revision: ExpectedRevision::new(edit.expected_revision)?,
                spec: definition.spec()?,
            },
        ),
        RuleCommand::Enable(edit) => (
            edit.request_id,
            RuleMutation::SetEnabled {
                rule_id: edit.rule_id,
                expected_revision: ExpectedRevision::new(edit.expected_revision)?,
                enabled: true,
            },
        ),
        RuleCommand::Disable(edit) => (
            edit.request_id,
            RuleMutation::SetEnabled {
                rule_id: edit.rule_id,
                expected_revision: ExpectedRevision::new(edit.expected_revision)?,
                enabled: false,
            },
        ),
        RuleCommand::Delete(edit) => (
            edit.request_id,
            RuleMutation::Delete {
                rule_id: edit.rule_id,
                expected_revision: ExpectedRevision::new(edit.expected_revision)?,
            },
        ),
        RuleCommand::Retry { request_id } => {
            return Ok(if settled {
                Request::RetryRuleDecision {
                    profile,
                    request_id,
                }
            } else {
                Request::RetryRuleMutation {
                    profile,
                    request_id,
                }
            });
        }
    };
    let request_id = match request_id {
        Some(id) => id,
        None => {
            RequestId::new(std::fs::read_to_string("/proc/sys/kernel/random/uuid")?.trim_end())?
        }
    };
    Ok(if settled {
        Request::DecideRule {
            profile,
            request_id,
            mutation,
        }
    } else {
        Request::MutateRule {
            profile,
            request_id,
            mutation,
        }
    })
}

pub(super) async fn run(
    path: PathBuf,
    command: Command,
    logs: Option<rustusb_logging::LogMonitor>,
    json: bool,
) -> Result<()> {
    if let Command::Run {
        automation_policy,
        automation_decisions_policy,
    } = &command
    {
        return serve(
            path,
            automation_policy.clone(),
            automation_decisions_policy.clone(),
            logs,
        )
        .await;
    }
    if let Command::Rule {
        profile,
        command,
        settled,
    } = &command
    {
        return rule_call(path, profile.clone(), command.clone(), *settled).await;
    }
    if let Command::Discover {
        profile,
        watch: true,
        refresh_seconds,
    } = &command
    {
        let mut signals = ShutdownSignals::new()?;
        let loading_path = path.clone();
        let mut loading = tokio::task::spawn_blocking(move || {
            rustusb_service::config::Manifest::read(&loading_path)
        });
        let manifest = tokio::select! {
            biased;
            signal=signals.recv()=>{let _=loading.await;return signal;},
            result=&mut loading=>result.map_err(|_|Error::State("catalog manifest task failed".into()))??,
        };
        let source = rustusb_service::catalog::CatalogSource::agent(
            std::sync::Arc::new(manifest),
            profile.clone(),
        )?;
        return super::catalog_watch::run(
            source,
            rustusb_service::catalog::RefreshInterval::new(*refresh_seconds)?,
            json,
            signals,
        )
        .await;
    }
    let request = match command {
        Command::Run { .. } => return Err(Error::State("invalid agent dispatch".into())),
        Command::Profiles => Request::Profiles,
        Command::Rules { profile, after } => Request::Rules { profile, after },
        Command::Rule { .. } => return Err(Error::State("invalid rule dispatch".into())),
        Command::RuleEvents { profile, after } => Request::RuleEvents { profile, after },
        Command::Start {
            profile,
            request_id,
            retry,
        } => {
            let request_id = match request_id {
                Some(id) => id,
                None => RequestId::new(
                    std::fs::read_to_string("/proc/sys/kernel/random/uuid")?.trim_end(),
                )?,
            };
            if retry {
                Request::RetryStart {
                    profile,
                    request_id,
                }
            } else {
                Request::Start {
                    profile,
                    request_id,
                }
            }
        }
        Command::StopJob { job_id } => Request::StopJob { job_id },
        Command::StopSession { session_id } => Request::StopSession { session_id },
        Command::Jobs { profile, after } => Request::Jobs { profile, after },
        Command::Sessions { profile, after } => Request::Sessions { profile, after },
        Command::Health { profile, after } => Request::SessionHealth { profile, after },
        Command::ServerSecurity { profile } => Request::ServerSecurity { profile },
        Command::ServerAdmission { profile } => Request::ServerAdmission { profile },
        Command::Events { profile, after } => Request::Events { profile, after },
        Command::Audit { profile, after } => Request::Audit { profile, after },
        Command::Discover { profile, .. } => Request::Discover { profile },
    };
    let response = rustusb_service::ipc::call_path(&path, request).await?;
    let failure = match response.response {
        Response::Error { code } => Some(code),
        Response::RequestExpired { .. } => Some(rustusb_service::model::Failure::State),
        Response::RuleMutationExpired { .. } => Some(rustusb_service::model::Failure::State),
        _ => None,
    };
    let value =
        serde_json::to_value(response).map_err(|_| Error::State("agent output encoding".into()))?;
    EventOutput::new()?.event(value).await?;
    if let Some(code) = failure {
        return Err(Error::State(format!("agent request failed: {code:?}")));
    }
    Ok(())
}

async fn rule_call(
    path: PathBuf,
    profile: String,
    command: RuleCommand,
    settled: bool,
) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let request = mutation_request(profile, command, settled)?;
    let (request_id, event) = match &request {
        Request::MutateRule { request_id, .. } => (request_id.clone(), "rule_mutation_intent"),
        Request::RetryRuleMutation { request_id, .. } => {
            (request_id.clone(), "rule_mutation_query")
        }
        Request::DecideRule { request_id, .. } => (request_id.clone(), "rule_decision_intent"),
        Request::RetryRuleDecision { request_id, .. } => {
            (request_id.clone(), "rule_decision_query")
        }
        _ => return Err(Error::State("invalid rule request identity".into())),
    };
    let mut loading =
        tokio::task::spawn_blocking(move || rustusb_service::config::Manifest::read(&path));
    let manifest = tokio::select! {
        biased;
        signal=signals.recv()=>{let _=loading.await;return signal;},
        result=&mut loading=>result.map_err(|_|Error::State("rule manifest task failed".into()))??,
    };
    let mut output = EventOutput::new()?;
    {
        let mut intent =
            serde_json::json!({"schema_version":1,"event":event,"request_id":request_id});
        if settled {
            intent["protocol"] = serde_json::json!("settled");
        }
        let prepared = output.event(intent);
        tokio::pin!(prepared);
        tokio::select! {
            biased;
            signal=signals.recv()=>return signal,
            result=&mut prepared=>result?,
        }
    }
    let response = tokio::select! {
        biased;
        signal=signals.recv()=>{
            signal?;
            return Err(Error::State(format!("rule mutation {request_id} response unknown; retry only the original identity")));
        },
        response=rustusb_service::ipc::call(&manifest,request)=>response.map_err(|error|Error::State(format!("rule mutation {request_id} response unknown: {error}; retry only the original identity")))?,
    };
    let failure = match &response.response {
        Response::Error { code } => Some(*code),
        Response::RuleMutationExpired { .. } => Some(rustusb_service::model::Failure::State),
        Response::RuleDecisionExpired { .. } => Some(rustusb_service::model::Failure::State),
        Response::RuleDecided {
            decision: rustusb_service::automation::RuleDecision::Rejected { failure, .. },
            ..
        } => Some(*failure),
        _ => None,
    };
    {
        let writing = output.event(
            serde_json::to_value(response)
                .map_err(|_| Error::State("rule result encoding".into()))?,
        );
        tokio::pin!(writing);
        tokio::select! {
            biased;
            signal=signals.recv()=>{
                signal?;return Err(Error::State(format!("rule mutation {request_id} response output interrupted; query the original identity")));
            },
            result=&mut writing=>result?,
        }
    }
    if let Some(code) = failure {
        return Err(Error::State(format!(
            "rule mutation {request_id} query/edit returned {code:?}; an expired query never reapplies an edit"
        )));
    }
    Ok(())
}

async fn serve(
    path: PathBuf,
    policy_path: Option<PathBuf>,
    decisions_path: Option<PathBuf>,
    logs: Option<rustusb_logging::LogMonitor>,
) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let mut loading = tokio::task::spawn_blocking(move || {
        let config = LoadedConfig::load(&path)?;
        let policy = if let Some(p) = &decisions_path {
            Some(AutomationPolicy::read_decisions(p, &config)?)
        } else {
            policy_path
                .as_ref()
                .map(|p| AutomationPolicy::read(p, &config))
                .transpose()?
        };
        Ok::<_, Error>((config, policy))
    });
    let (config, policy) = tokio::select! {
        biased;
        signal=signals.recv()=>{let _=loading.await;return signal;},
        config=&mut loading=>config.map_err(|_|Error::State("agent startup task failed".into()))??,
    };
    let (sender, receiver) = watch::channel(false);
    let service = rustusb_service::ipc::serve_with_automation(config, receiver, logs, policy);
    tokio::pin!(service);
    let mut signal_error = None;
    loop {
        tokio::select! {
            signal=signals.recv()=>{sender.send_replace(true);if let Err(error)=signal{signal_error=Some(error);}},
            result=&mut service=>{result?;return signal_error.map_or(Ok(()),Err);},
        }
    }
}

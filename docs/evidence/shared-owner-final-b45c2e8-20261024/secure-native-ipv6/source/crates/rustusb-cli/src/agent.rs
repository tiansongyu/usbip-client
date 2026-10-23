//! Local application service adapter; global signals and stdout remain CLI-owned.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{
    Error, Result,
    application::{JobId, RequestId, SessionId},
};
use rustusb_service::{
    config::LoadedConfig,
    rpc::{Request, Response},
};
use std::path::PathBuf;
use tokio::sync::watch;

#[derive(Debug, Clone, clap::Subcommand)]
pub(super) enum Command {
    /// Own jobs until cooperative SIGINT/SIGTERM and their actual cleanup finish.
    Run,
    /// List fixed profiles readable by this OS identity.
    Profiles,
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
    Discover { profile: String },
}

pub(super) async fn run(path: PathBuf, command: Command) -> Result<()> {
    if matches!(command, Command::Run) {
        return serve(path).await;
    }
    let request = match command {
        Command::Run => return Err(Error::State("invalid agent dispatch".into())),
        Command::Profiles => Request::Profiles,
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
        Command::Events { profile, after } => Request::Events { profile, after },
        Command::Audit { profile, after } => Request::Audit { profile, after },
        Command::Discover { profile } => Request::Discover { profile },
    };
    let response = rustusb_service::ipc::call_path(&path, request).await?;
    let failure = match response.response {
        Response::Error { code } => Some(code),
        Response::RequestExpired { .. } => Some(rustusb_service::model::Failure::State),
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

async fn serve(path: PathBuf) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let mut loading = tokio::task::spawn_blocking(move || LoadedConfig::load(&path));
    let config = tokio::select! {
        biased;
        signal=signals.recv()=>{let _=loading.await;return signal;},
        config=&mut loading=>config.map_err(|_|Error::State("agent startup task failed".into()))??,
    };
    let (sender, receiver) = watch::channel(false);
    let service = rustusb_service::ipc::serve(config, receiver);
    tokio::pin!(service);
    let mut signal_error = None;
    loop {
        tokio::select! {
            signal=signals.recv()=>{sender.send_replace(true);if let Err(error)=signal{signal_error=Some(error);}},
            result=&mut service=>{result?;return signal_error.map_or(Ok(()),Err);},
        }
    }
}

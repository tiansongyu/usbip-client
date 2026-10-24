//! Bounded read-only local/authorized-agent log observer; owns no USB job.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{Error, Result};
use rustusb_logging::{Cursor, LogPage};
use std::{path::PathBuf, time::Duration};

#[derive(Debug, Clone, clap::Args)]
pub(super) struct Args {
    /// Private store owned by this UID; root can inspect an administrator's store.
    #[arg(
        long,
        conflicts_with = "agent_config",
        required_unless_present = "agent_config"
    )]
    directory: Option<PathBuf>,
    /// Fixed trusted agent manifest; requires the secure-transport build feature.
    #[arg(long, conflicts_with = "directory", requires = "profile")]
    agent_config: Option<PathBuf>,
    /// Exact configured profile with independent Audit authorization.
    #[arg(long, requires = "agent_config")]
    profile: Option<String>,
    /// Generated segment/complete-line cursor, never a filename or authority.
    #[arg(long)]
    after: Option<Cursor>,
    /// Maximum returned records in one bounded page.
    #[arg(long,default_value_t=16,value_parser=clap::value_parser!(u8).range(1..=16))]
    limit: u8,
    /// Observe new pages until interrupted; never stops the underlying USB jobs.
    #[arg(long)]
    follow: bool,
}

enum ReadOutcome {
    Page(LogPage),
    Busy,
    Stopped,
}

fn logging_error(error: rustusb_logging::Error) -> Error {
    use rustusb_logging::Error as LogError;
    match error {
        LogError::InvalidInput(message) => Error::InvalidInput(format!("log reader: {message}")),
        LogError::Permission(message) => Error::Permission(format!("log reader: {message}")),
        LogError::Corrupt(message) => Error::State(format!(
            "log history corrupt: {message}; preserve the original files"
        )),
        LogError::Io(error) => Error::Io(error),
        LogError::Busy => Error::State("log reader busy".into()),
        LogError::Disabled => Error::Unsupported("agent file logging is disabled or closed".into()),
        LogError::Unsupported => Error::Unsupported("checked log reading requires Linux".into()),
        LogError::Worker(message) => Error::State(format!("log reader worker: {message}")),
        LogError::LostRecords => Error::State(
            "operational log records were lost; this is not a USB cleanup result".into(),
        ),
    }
}

async fn local_page(
    args: &Args,
    after: Option<Cursor>,
    signals: &mut ShutdownSignals,
) -> Result<ReadOutcome> {
    let directory = args
        .directory
        .clone()
        .ok_or_else(|| Error::InvalidInput("log directory required".into()))?;
    let limit = args.limit;
    let mut task = tokio::task::spawn_blocking(move || {
        rustusb_logging::read_directory(&directory, after, limit)
    });
    let result = tokio::select! {
        biased;
        signal = signals.recv() => {
            // A real filesystem read cannot be cancelled by dropping its task.
            let joined = task.await;
            signal?;
            // Cancellation asks for no page. Still join the actual task; its
            // completed read result is not a USB mutation or cleanup witness.
            let _ = joined.map_err(|_| Error::State("log reader task failed".into()))?;
            return Ok(ReadOutcome::Stopped);
        }
        result = &mut task => result.map_err(|_| Error::State("log reader task failed".into()))?,
    };
    match result {
        Ok(page) => Ok(ReadOutcome::Page(page)),
        Err(rustusb_logging::Error::Busy) => Ok(ReadOutcome::Busy),
        Err(error) => Err(logging_error(error)),
    }
}

#[cfg(all(feature = "secure-transport", target_os = "linux"))]
async fn agent_page(
    args: &Args,
    after: Option<Cursor>,
    signals: &mut ShutdownSignals,
) -> Result<ReadOutcome> {
    use rustusb_service::rpc::{LogReadFailure, Request, Response};
    let path = args
        .agent_config
        .as_ref()
        .ok_or_else(|| Error::InvalidInput("agent configuration required".into()))?;
    let profile = args
        .profile
        .clone()
        .ok_or_else(|| Error::InvalidInput("log profile required".into()))?;
    let response = tokio::select! {
        biased;
        signal=signals.recv()=>{signal?;return Ok(ReadOutcome::Stopped);},
        result=rustusb_service::ipc::call_path(path,Request::ReadLogs{profile,after,limit:args.limit})=>result?,
    };
    match response.response {
        Response::Logs { page } => Ok(ReadOutcome::Page(page)),
        Response::LogError {
            code: LogReadFailure::Busy,
        } => Ok(ReadOutcome::Busy),
        Response::LogError { code } => {
            Err(Error::State(format!("agent log read failed: {code:?}")))
        }
        Response::Error { code } => Err(Error::Permission(format!(
            "agent log request refused: {code:?}"
        ))),
        _ => Err(Error::Protocol("unexpected agent log response".into())),
    }
}

#[cfg(not(all(feature = "secure-transport", target_os = "linux")))]
async fn agent_page(
    _args: &Args,
    _after: Option<Cursor>,
    _signals: &mut ShutdownSignals,
) -> Result<ReadOutcome> {
    Err(Error::Unsupported(
        "agent log reading requires Linux and the secure-transport build feature".into(),
    ))
}

async fn show_page(output: &mut EventOutput, page: &LogPage, json: bool) -> Result<()> {
    if json {
        output
            .event(serde_json::json!({"schema_version":1,"event":"logs","page":page}))
            .await
    } else {
        let mut text = String::new();
        if page.gap {
            text.push_str("[log history gap: retention or an incomplete old tail]\n");
        }
        if let Some(status) = &page.status {
            if !status.healthy {
                text.push_str("[operational logger unhealthy; USB cleanup is a separate result]\n");
            }
        }
        for record in &page.records {
            text.push_str(&record.format_human().map_err(logging_error)?);
            if !text.ends_with('\n') {
                text.push('\n');
            }
        }
        if page.records.is_empty() {
            text.push_str("No matching records in this scan window.\n");
        }
        if let Some(cursor) = &page.next_after {
            text.push_str(&format!("Next cursor: {cursor}\n"));
        }
        output.text(text).await
    }
}

pub(super) async fn run(args: Args, json: bool) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let mut output = EventOutput::new()?;
    let mut after = args.after.clone();
    let mut busy_announced = false;
    loop {
        let read = if args.directory.is_some() {
            local_page(&args, after.clone(), &mut signals).await?
        } else {
            agent_page(&args, after.clone(), &mut signals).await?
        };
        match read {
            ReadOutcome::Stopped => return Ok(()),
            ReadOutcome::Busy => {
                if !args.follow {
                    return Err(Error::State(
                        "log reader busy; no USB operation was requested".into(),
                    ));
                }
                if !busy_announced {
                    if json {
                        output
                            .event(serde_json::json!({"schema_version":1,"event":"logs_busy"}))
                            .await?;
                    } else {
                        output.text("Log reader busy; retrying.\n".into()).await?;
                    }
                    busy_announced = true;
                }
            }
            ReadOutcome::Page(page) => {
                busy_announced = false;
                let advanced = page.next_after != after;
                let more = page.more;
                show_page(&mut output, &page, json).await?;
                if !args.follow {
                    return Ok(());
                }
                after = page.next_after;
                if more && advanced {
                    continue;
                }
            }
        }
        tokio::select! {
            signal=signals.recv()=>return signal,
            ()=tokio::time::sleep(Duration::from_millis(250))=>{},
        }
    }
}

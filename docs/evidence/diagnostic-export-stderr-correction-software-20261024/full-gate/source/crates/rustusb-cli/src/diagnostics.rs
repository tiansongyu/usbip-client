//! Private diagnostic observer, with retained work across both shutdown signals.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{Error, Result};
use rustusb_diagnostics::{AgentSource, ExportOptions};
use std::path::PathBuf;

#[derive(Debug, Clone, clap::Args)]
pub(super) struct Args {
    /// Fresh absolute filename in an existing caller-owned private 0700 directory.
    #[arg(long)]
    output: PathBuf,
    /// Public trusted agent manifest; requires Linux and secure-transport.
    #[arg(long, requires = "profile", conflicts_with = "logs_directory")]
    agent_config: Option<PathBuf>,
    /// Exact profile requiring both Read and Audit authorization.
    #[arg(long, requires = "agent_config")]
    profile: Option<String>,
    /// Optional local private stored log history, without starting a writer.
    #[arg(long, conflicts_with = "agent_config")]
    logs_directory: Option<PathBuf>,
}

pub(super) async fn run(args: Args, socket: PathBuf, json: bool) -> Result<()> {
    // Register before starting probes or accepting artifact file work.
    let mut signals = ShutdownSignals::new()?;
    let mut output = EventOutput::new()?;
    let agent = match (args.agent_config, args.profile) {
        (Some(manifest), Some(profile)) => Some(AgentSource { manifest, profile }),
        (None, None) => None,
        _ => {
            return Err(Error::InvalidInput(
                "diagnostics requires both agent-config and profile".into(),
            ));
        }
    };
    let (stop, stopped) = tokio::sync::watch::channel(false);
    let capture = rustusb_diagnostics::export(
        ExportOptions {
            output: args.output,
            helper_socket: socket,
            agent,
            logs_directory: args.logs_directory,
        },
        stopped,
    );
    tokio::pin!(capture);
    let mut signal_error = None;
    let receipt = loop {
        tokio::select! {
            // A ready receipt retains the actual completed publication result.
            biased;
            receipt = &mut capture => break receipt,
            signal = signals.recv() => {
                stop.send_replace(true);
                if let Err(error) = signal {
                    signal_error = Some(error);
                    // The notification source failed; still join accepted work.
                    break capture.await;
                }
                // Repeated signals keep the stop sticky and never drop capture.
            }
        }
    };
    if json {
        output
            .event(
                serde_json::to_value(&receipt)
                    .map_err(|_| Error::State("could not encode diagnostic receipt".into()))?,
            )
            .await?;
    } else {
        let text = if receipt.is_complete() {
            "Diagnostic capture complete; private artifact published.\n"
        } else {
            "Diagnostic capture incomplete; inspect the publication and section receipt.\n"
        };
        output
            .text(format!(
                "{text}{}\n",
                serde_json::to_string(&receipt)
                    .map_err(|_| Error::State("could not encode diagnostic receipt".into()))?
            ))
            .await?;
    }
    if let Some(error) = signal_error {
        return Err(error);
    }
    if receipt.is_complete() {
        Ok(())
    } else {
        Err(Error::State(
            "diagnostic capture or artifact publication incomplete".into(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use clap::Parser;

    #[test]
    fn diagnostic_sources_and_process_writer_are_exclusive()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let base = ["rustusb", "diagnostics", "--output", "/private/result.json"];
        assert!(crate::Cli::try_parse_from(base).is_ok());
        for suffix in [
            vec!["--profile", "test"],
            vec!["--agent-config", "/trusted/manifest.json"],
            vec![
                "--agent-config",
                "/trusted/manifest.json",
                "--profile",
                "test",
                "--logs-directory",
                "/private/logs",
            ],
        ] {
            assert!(crate::Cli::try_parse_from(base.into_iter().chain(suffix)).is_err());
        }
        let cli = crate::Cli::try_parse_from(
            base.into_iter().chain(["--log-directory", "/private/logs"]),
        )?;
        assert!(matches!(
            crate::logging_options(&cli),
            Err(rustusb_logging::Error::InvalidInput(_))
        ));
        Ok(())
    }
}

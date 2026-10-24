//! Signal adapter around the actual ordinary-user terminal frontend.
use super::shutdown_signals::ShutdownSignals;
use rustusb_core::{Error, Result};
use std::path::PathBuf;
use tokio::sync::watch;

#[derive(Debug, Clone, clap::Args)]
pub(super) struct Args {
    /// Root-managed public agent manifest; never a TLS private configuration.
    #[arg(long)]
    config: PathBuf,
    /// Delay after completed profile refresh (2..=120 seconds).
    #[arg(long,value_parser=clap::value_parser!(u64).range(2..=120))]
    refresh_seconds: Option<u64>,
    /// Begin with automatic profile/catalog polling paused.
    #[arg(long, conflicts_with = "auto_refresh")]
    no_auto_refresh: bool,
    /// Enable automatic polling even when saved preferences pause it.
    #[arg(long)]
    auto_refresh: bool,
    /// Use text emphasis without application colors.
    #[arg(long, conflicts_with = "color")]
    monochrome: bool,
    /// Use application colors even when saved preferences select monochrome.
    #[arg(long)]
    color: bool,
    /// Checked caller-owned 0700 directory, containing only frontend preferences.
    #[arg(long, conflicts_with = "no_preferences")]
    preferences_directory: Option<PathBuf>,
    /// Do not read or save user preferences; invocation-local options only.
    #[arg(long)]
    no_preferences: bool,
}
pub(super) async fn run(args: Args, json: bool) -> Result<()> {
    if json {
        return Err(Error::InvalidInput(
            "TUI cannot use --json; use agent commands for JSON".into(),
        ));
    }
    let mut signals = ShutdownSignals::new()?;
    let mut loading = tokio::task::spawn_blocking(move || {
        let manifest = rustusb_service::config::Manifest::read(&args.config)?;
        let directory = if args.no_preferences {
            None
        } else {
            args.preferences_directory
                .clone()
                .or_else(rustusb_tui::Settings::default_directory)
        };
        let settings = directory.map(rustusb_tui::Settings::load).transpose()?;
        let options = effective_options(
            &args,
            settings
                .as_ref()
                .map(rustusb_tui::Settings::options)
                .unwrap_or_default(),
        );
        Ok::<_, Error>((manifest, options, settings))
    });
    let (manifest, options, settings) = tokio::select! {
        biased;
        signal=signals.recv()=>{let _=loading.await;return signal;},
        loaded=&mut loading=>loaded.map_err(|_|Error::State("TUI manifest worker failed".into()))??,
    };
    let (sender, receiver) = watch::channel(false);
    let frontend = rustusb_tui::run_with_settings(manifest, options, settings, receiver);
    tokio::pin!(frontend);
    let mut signal_error = None;
    loop {
        tokio::select! {
            signal=signals.recv()=>{sender.send_replace(true);if let Err(error)=signal{signal_error=Some(error);}},
            result=&mut frontend=>{result?;return signal_error.map_or(Ok(()),Err);},
        }
    }
}

fn effective_options(args: &Args, mut options: rustusb_tui::Options) -> rustusb_tui::Options {
    if let Some(seconds) = args.refresh_seconds {
        options.refresh_seconds = seconds;
    }
    if args.auto_refresh {
        options.auto_refresh = true;
    }
    if args.no_auto_refresh {
        options.auto_refresh = false;
    }
    if args.monochrome {
        options.monochrome = true;
    }
    if args.color {
        options.monochrome = false;
    }
    options
}

#[cfg(test)]
mod tests {
    use super::*;
    use clap::Parser;
    #[derive(Parser)]
    struct Launch {
        #[command(flatten)]
        args: Args,
    }
    #[test]
    fn saved_preferences_have_explicit_cli_precedence_without_implicit_save() -> Result<()> {
        let saved = rustusb_tui::Options {
            refresh_seconds: 17,
            auto_refresh: false,
            monochrome: true,
        };
        let plain = Launch::try_parse_from(["tui", "--config", "/etc/rustusb/agent.json"])
            .map_err(|_| Error::State("valid TUI args rejected".into()))?;
        assert_eq!(effective_options(&plain.args, saved), saved);
        let explicit = Launch::try_parse_from([
            "tui",
            "--config",
            "/etc/rustusb/agent.json",
            "--refresh-seconds",
            "5",
            "--auto-refresh",
            "--color",
        ])
        .map_err(|_| Error::State("valid TUI override rejected".into()))?;
        assert_eq!(
            effective_options(&explicit.args, saved),
            rustusb_tui::Options {
                refresh_seconds: 5,
                auto_refresh: true,
                monochrome: false
            }
        );
        for pair in [
            ["--auto-refresh", "--no-auto-refresh"],
            ["--color", "--monochrome"],
        ] {
            assert!(
                Launch::try_parse_from([
                    "tui",
                    "--config",
                    "/etc/rustusb/agent.json",
                    pair[0],
                    pair[1]
                ])
                .is_err()
            );
        }
        assert!(
            Launch::try_parse_from([
                "tui",
                "--config",
                "/etc/rustusb/agent.json",
                "--refresh-seconds",
                "1"
            ])
            .is_err()
        );
        assert!(
            Launch::try_parse_from([
                "tui",
                "--config",
                "/etc/rustusb/agent.json",
                "--no-preferences",
                "--preferences-directory",
                "/tmp/prefs"
            ])
            .is_err()
        );
        Ok(())
    }
}

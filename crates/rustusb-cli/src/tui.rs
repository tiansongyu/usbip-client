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
    #[arg(long,default_value_t=2,value_parser=clap::value_parser!(u64).range(2..=120))]
    refresh_seconds: u64,
    /// Begin with automatic profile/catalog polling paused.
    #[arg(long)]
    no_auto_refresh: bool,
    /// Use text emphasis without application colors.
    #[arg(long)]
    monochrome: bool,
}
pub(super) async fn run(args: Args, json: bool) -> Result<()> {
    if json {
        return Err(Error::InvalidInput(
            "TUI cannot use --json; use agent commands for JSON".into(),
        ));
    }
    let mut signals = ShutdownSignals::new()?;
    let mut loading =
        tokio::task::spawn_blocking(move || rustusb_service::config::Manifest::read(&args.config));
    let manifest = tokio::select! {
        biased;
        signal=signals.recv()=>{let _=loading.await;return signal;},
        loaded=&mut loading=>loaded.map_err(|_|Error::State("TUI manifest worker failed".into()))??,
    };
    let (sender, receiver) = watch::channel(false);
    let frontend = rustusb_tui::run(
        manifest,
        rustusb_tui::Options {
            refresh_seconds: args.refresh_seconds,
            auto_refresh: !args.no_auto_refresh,
            monochrome: args.monochrome,
        },
        receiver,
    );
    tokio::pin!(frontend);
    let mut signal_error = None;
    loop {
        tokio::select! {
            signal=signals.recv()=>{sender.send_replace(true);if let Err(error)=signal{signal_error=Some(error);}},
            result=&mut frontend=>{result?;return signal_error.map_or(Ok(()),Err);},
        }
    }
}

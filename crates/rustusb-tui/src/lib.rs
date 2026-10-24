//! Ordinary-user Ratatui frontend of the existing authenticated application API.
//!
//! This frontend never owns USB resources. Closing it leaves service jobs running.
#![cfg(target_os = "linux")]

pub use rustusb_frontend::{Options, Settings};
use rustusb_frontend::{state, worker};
mod terminal;

use rustusb_core::{Error, Result};
use rustusb_service::config::Manifest;
use std::sync::Arc;
use tokio::sync::{mpsc, watch};

/// Run until quit, terminal failure or cooperative stop, then join actual observers.
///
/// The caller registers signals before manifest loading and must await this future;
/// dropping it is not observer cleanup. Every mutation still requires real agent
/// authorization. An unresolved start is returned as an error containing its safe
/// retry identity, after terminal restoration and actual metadata-owner joins.
pub async fn run(
    manifest: Manifest,
    options: Options,
    external: watch::Receiver<bool>,
) -> Result<()> {
    run_with_settings(manifest, options, None, external).await
}

/// Run with a checked user-settings location loaded by an owned blocking worker.
/// Options are the effective invocation values; save/reload work is truly joined.
pub async fn run_with_settings(
    manifest: Manifest,
    options: Options,
    settings: Option<Settings>,
    mut external: watch::Receiver<bool>,
) -> Result<()> {
    if rustix::process::geteuid().is_root() {
        return Err(Error::Permission("TUI must run as an ordinary user".into()));
    }
    manifest.validate()?;
    rustusb_service::catalog::RefreshInterval::new(options.refresh_seconds)?;
    let (stop, receiver) = watch::channel(*external.borrow());
    let (commands, requests) = mpsc::channel(1);
    let mut snapshot = state::Snapshot::new(options);
    if let Some(settings) = &settings {
        snapshot.saved_options = settings.saved();
        snapshot.preferences_status = format!(
            "{}; [w] save / [l] reload / [d] defaults",
            if settings.has_saved() {
                "Loaded checked preferences"
            } else {
                "No saved preferences yet"
            }
        );
    }
    let (views, observations) = watch::channel(snapshot);
    let mut service = tokio::spawn(worker::run(
        Arc::new(manifest),
        requests,
        views,
        receiver.clone(),
        settings,
    ));
    let mut screen =
        tokio::task::spawn_blocking(move || terminal::run(observations, commands, receiver));
    let mut screen_result = None;
    let mut service_result = None;
    let mut signal_error = None;
    while screen_result.is_none() || service_result.is_none() {
        tokio::select! {
            result = &mut screen, if screen_result.is_none() => {
                screen_result = Some(result.map_err(|_| Error::State("terminal worker failed".into())).and_then(|v|v));
                stop.send_replace(true);
            }
            result = &mut service, if service_result.is_none() => {
                service_result = Some(result.map_err(|_| Error::State("frontend service worker failed".into())).and_then(|v|v));
                stop.send_replace(true);
            }
            changed = external.changed(), if !*stop.borrow() => {
                if changed.is_err() || *external.borrow() {
                    stop.send_replace(true);
                    if changed.is_err() { signal_error = Some(Error::State("TUI stop source ended".into())); }
                }
            }
        }
    }
    // Both handles have actually returned. No abort or timeout invents cleanup.
    let screen_result =
        screen_result.ok_or_else(|| Error::State("terminal result absent".into()))?;
    let service_result =
        service_result.ok_or_else(|| Error::State("frontend result absent".into()))?;
    match (screen_result, service_result) {
        (Err(screen), Err(service)) => return Err(Error::State(format!("{screen}; {service}"))),
        (Err(error), _) | (_, Err(error)) => return Err(error),
        _ => {}
    }
    signal_error.map_or(Ok(()), Err)
}

//! Dedicated nonroot client broker: authenticated catalog/import and owned VHCI relay.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{Error, Result};
use rustusb_transport::config::{LoadedClientConfig, load_client};
use std::{
    path::{Path, PathBuf},
    sync::Arc,
    time::Duration,
};

async fn load(path: PathBuf, signals: &mut ShutdownSignals) -> Result<Option<LoadedClientConfig>> {
    let mut task = tokio::task::spawn_blocking(move || load_client(&path));
    tokio::select! {
        biased;
        signal = signals.recv() => {
            // This task owns no USB resource. Its synchronous file/crypto work
            // nevertheless must really finish before the broker returns.
            let _ = task.await;
            signal?;
            Ok(None)
        }
        result = &mut task => {
            let loaded = result.map_err(|_| Error::State("secure client configuration task failed".into()))?
                .map_err(|error| Error::Permission(format!("secure client configuration: {error}")))?;
            Ok(Some(loaded))
        }
    }
}

/// Read-only CLI operation; the catalog is matched by stable key rather than list order.
pub(super) async fn discover(
    path: PathBuf,
    timeout: Duration,
    watch: bool,
    refresh_seconds: u64,
    json: bool,
) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let config = load(path, &mut signals)
        .await?
        .ok_or_else(|| Error::State("secure discovery cancelled".into()))?;
    if watch {
        let source = rustusb_service::catalog::CatalogSource::secure(Arc::new(config), timeout)?;
        return super::catalog_watch::run(
            source,
            rustusb_service::catalog::RefreshInterval::new(refresh_seconds)?,
            json,
            signals,
        )
        .await;
    }
    let devices = tokio::select! {
        biased;
        signal = signals.recv() => {
            signal?;
            Err(Error::State("secure discovery cancelled".into()))
        }
        result = rustusb_service::jobs::discover(&config, timeout) => result,
    }?;
    let value = serde_json::to_value(devices)
        .map_err(|_| Error::State("could not encode secure catalog".into()))?;
    let mut output = EventOutput::new()?;
    tokio::select! {
        biased;
        signal = signals.recv() => signal,
        result = output.event(value) => result,
    }
}

/// Adapt the shared client owner to the foreground signal/output contract.
pub(super) async fn attach(socket: &Path, path: PathBuf, timeout: Duration) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let Some(config) = load(path, &mut signals).await? else {
        return Ok(());
    };
    super::foreground::run(
        rustusb_service::jobs::LoadedJob::Client {
            config: Arc::new(config),
            timeout,
        },
        socket.to_path_buf(),
        false,
        signals,
    )
    .await
}

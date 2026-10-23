//! Dedicated nonroot client broker: authenticated catalog/import and owned VHCI relay.

use super::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{Error, Result};
use rustusb_daemon::{SecureImportTarget, prepare_native_import};
use rustusb_protocol::{
    RemoteDevice,
    secure::{self, CatalogDevice},
};
use rustusb_transport::{
    ClientStream,
    config::{LoadedClientConfig, load_client},
};
use serde_json::json;
use std::{
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{net::TcpStream, sync::watch};

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

async fn connect(config: &LoadedClientConfig) -> Result<ClientStream<TcpStream>> {
    let stream =
        TcpStream::connect((config.endpoint.host.as_str(), config.endpoint.port.get())).await?;
    stream.set_nodelay(true)?;
    config
        .tls
        .connect(&config.server_name, stream)
        .await
        .map_err(|error| Error::Permission(format!("secure server authentication: {error}")))
}

async fn catalog(config: &LoadedClientConfig, timeout: Duration) -> Result<Vec<CatalogDevice>> {
    tokio::time::timeout(timeout, async {
        secure::discover(connect(config).await?).await
    })
    .await
    .map_err(|_| Error::Timeout("secure discovery including DNS/TCP/TLS".into()))?
}

/// Read-only CLI operation; the catalog is matched by stable key rather than list order.
pub(super) async fn discover(path: PathBuf, timeout: Duration) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let config = load(path, &mut signals)
        .await?
        .ok_or_else(|| Error::State("secure discovery cancelled".into()))?;
    let devices = tokio::select! {
        biased;
        signal = signals.recv() => {
            signal?;
            Err(Error::State("secure discovery cancelled".into()))
        }
        result = catalog(&config, timeout) => result,
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

async fn select_and_import(
    config: &LoadedClientConfig,
    timeout: Duration,
) -> Result<(ClientStream<TcpStream>, RemoteDevice)> {
    tokio::time::timeout(timeout, async {
        let selected = catalog(config, timeout)
            .await?
            .into_iter()
            .find(|entry| entry.device_key == config.device)
            .ok_or_else(|| {
                Error::Permission("configured remote device is unavailable or unauthorized".into())
            })?;
        let stream = connect(config).await?;
        secure::import_device(stream, &selected).await
    })
    .await
    .map_err(|_| Error::Timeout("secure device selection/import including DNS/TCP/TLS".into()))?
}

async fn invalidated(mut receiver: watch::Receiver<bool>) {
    loop {
        if *receiver.borrow() {
            return;
        }
        if receiver.changed().await.is_err() {
            return;
        }
    }
}

/// Hold the encrypted network/anonymous kernel relay until signal, EOF or helper invalidation.
/// Every path after receiving a helper handle awaits its real close operation.
pub(super) async fn attach(socket: &Path, path: PathBuf, timeout: Duration) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let mut output = EventOutput::new()?;
    let Some(config) = load(path, &mut signals).await? else {
        return Ok(());
    };
    let connected = {
        let import = select_and_import(&config, timeout);
        tokio::pin!(import);
        tokio::select! {
            biased;
            signal = signals.recv() => { signal?; None },
            result = &mut import => Some(result?),
        }
    };
    let Some((mut stream, remote)) = connected else {
        return Ok(());
    };
    // Do not race creation of an owned helper handle. A pending signal is
    // observed below; no kernel commit occurs until the relay task is running.
    let mut handle = prepare_native_import(
        socket,
        SecureImportTarget {
            server_id: config.server_id.clone(),
            device_key: config.device.clone(),
            remote,
        },
    )
    .await?;
    let mut relay = match handle.take_relay() {
        Ok(relay) => relay,
        Err(error) => {
            handle.close().await?;
            return Err(error);
        }
    };
    let loss = invalidated(handle.invalidated());
    tokio::pin!(loss);
    let mut copying =
        tokio::spawn(async move { tokio::io::copy_bidirectional(&mut stream, &mut relay).await });
    let mut copy_finished = false;
    let result: Result<()> = {
        let operations = async {
            let receipt = handle.commit().await?;
            output.event(json!({"schema_version":1,"event":"connected","transport":"mtls","server_id":config.server_id,"device_key":config.device,"receipt":receipt})).await?;
            let result = (&mut copying).await;
            copy_finished = true;
            result.map_err(|_| Error::State("secure relay task failed".into()))??;
            Ok(())
        };
        tokio::pin!(operations);
        tokio::select! {
            biased;
            signal = signals.recv() => signal,
            // The actor sends its final RPC error before invalidation. Preserve
            // that completed response when both observations are ready.
            result = &mut operations => result,
            _ = &mut loss => Err(Error::State("secure helper channel became invalid".into())),
        }
    };
    // Stop admitting network/USB bytes before asking the helper to verify
    // shutdown. Join the real relay task; dropping a JoinHandle does not stop it.
    if !copy_finished {
        copying.abort();
        let _ = copying.await;
    }
    match (result, handle.close().await) {
        (Err(operation), Err(cleanup)) => {
            return Err(Error::State(format!(
                "secure device operation failed ({operation}); cleanup unconfirmed ({cleanup})"
            )));
        }
        (Err(error), Ok(())) | (Ok(()), Err(error)) => return Err(error),
        (Ok(()), Ok(())) => {}
    }
    if output.in_flight {
        return Ok(());
    }
    tokio::select! {
        biased;
        signal = signals.recv() => signal,
        result = output.event(json!({"schema_version":1,"event":"disconnected","transport":"mtls","cleanup_verified":true})) => result,
    }
}

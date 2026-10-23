//! Ordinary-user physical service: helper-issued descriptors, no arbitrary paths.

use super::{
    shutdown_signals::ShutdownSignals,
    virtual_server::{Control, EventOutput, controls, parse},
};
use rustusb_core::{Error, Result};
use rustusb_daemon::{Request, open_physical, request};
use rustusb_rust_backend::{PhysicalBackend, PhysicalSource, ServerConfig, ServerHandle};
use serde_json::json;
use std::{
    collections::HashSet,
    net::{IpAddr, SocketAddr},
    path::Path,
};

pub(super) async fn run(
    socket: &Path,
    device_ids: Vec<String>,
    listen: SocketAddr,
    trusted_network: bool,
    allowed_peers: Vec<IpAddr>,
    control_stdin: bool,
) -> Result<()> {
    if device_ids.is_empty()
        || device_ids.len() > 16
        || device_ids.iter().collect::<HashSet<_>>().len() != device_ids.len()
    {
        return Err(Error::InvalidInput(
            "select 1..16 distinct approved physical device fingerprints".into(),
        ));
    }
    let config = ServerConfig {
        listen,
        trusted_network,
        allowed_peers,
    };
    config.validate()?;
    let mut signals = ShutdownSignals::new()?;
    let mut output = EventOutput::new()?;
    let backend = {
        // Preparation cannot accept IMPORT or claim interfaces. Cancelling it
        // does not terminate any already-running blocking initialization call.
        let prepare = async {
            let status = request(socket, Request::Status).await?;
            if !status.ok {
                return Err(Error::State(status.error.unwrap_or_else(|| {
                    "helper rejected physical service caller".into()
                })));
            }
            if status
                .data
                .get("physical_usb_capability_enabled")
                .and_then(|value| value.as_bool())
                != Some(true)
            {
                return Err(Error::Unsupported("install a matching helper built with physical-usb and configure its physical_device_ids allowlist".into()));
            }
            let mut sources = Vec::with_capacity(device_ids.len());
            for device_id in device_ids {
                let grant = open_physical(socket, device_id).await?;
                sources.push(PhysicalSource {
                    fd: grant.fd,
                    device: grant.device,
                    receipt: grant.receipt,
                });
            }
            PhysicalBackend::from_sources(sources).await
        };
        tokio::pin!(prepare);
        tokio::select! {
            biased;
            signal = signals.recv() => return signal,
            backend = &mut prepare => backend?,
        }
    };
    // Do not race bind: its returned handle may already own a listener task.
    let server = ServerHandle::bind_physical(config, backend.clone()).await?;
    let operations = async {
        output.event(json!({"schema_version":1,"event":"ready","address":server.local_addr(),"snapshot":backend.snapshot()?})).await?;
        if !control_stdin {
            std::future::pending::<()>().await;
        }
        let mut input = controls()?;
        while let Some(line) = input.recv().await {
            let control = match line.and_then(|line| parse(&line)) {
                Ok(control) => control,
                Err(_) => {
                    output
                        .event(json!({"schema_version":1,"event":"error","code":"invalid_control"}))
                        .await?;
                    continue;
                }
            };
            let id = control.id().to_owned();
            match control {
                Control::Snapshot { .. } => {
                    output.event(json!({"schema_version":1,"event":"snapshot","id":id,"snapshot":backend.snapshot()?})).await?;
                }
                Control::Shutdown { .. } => {
                    output
                        .event(json!({"schema_version":1,"event":"ack","id":id,"ok":true}))
                        .await?;
                    break;
                }
                _ => {
                    output.event(json!({"schema_version":1,"event":"error","id":id,"code":"unsupported_physical_control"})).await?;
                }
            }
        }
        Ok(())
    };
    let result: Result<()> = {
        tokio::pin!(operations);
        tokio::select! {
            biased;
            signal = signals.recv() => signal,
            result = &mut operations => result,
        }
    };
    // Even failed/blocked output cannot skip device cancellation and recovery.
    let cleanup = server.shutdown().await;
    cleanup?;
    result?;
    if output.in_flight {
        return Ok(());
    }
    tokio::select! {
        biased;
        signal = signals.recv() => signal,
        result = output.event(json!({"schema_version":1,"event":"stopped","snapshot":backend.snapshot()?})) => result,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn invalid_selection_and_network_policy_fail_before_helper_io() -> Result<()> {
        let socket = Path::new("/this-helper-must-never-be-contacted/rustusb.sock");
        let id = format!("usb-{}", "a".repeat(64));
        let loopback = "127.0.0.1:0"
            .parse()
            .map_err(|_| Error::State("test address".into()))?;
        for devices in [vec![], vec![id.clone(); 2], vec![id.clone(); 17]] {
            assert!(matches!(
                run(socket, devices, loopback, false, vec![], false).await,
                Err(Error::InvalidInput(_))
            ));
        }
        let public = "0.0.0.0:3240"
            .parse()
            .map_err(|_| Error::State("test address".into()))?;
        assert!(matches!(
            run(socket, vec![id], public, false, vec![], false).await,
            Err(Error::Permission(_))
        ));
        Ok(())
    }
}

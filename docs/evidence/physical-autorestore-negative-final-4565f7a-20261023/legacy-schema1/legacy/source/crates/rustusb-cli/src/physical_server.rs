//! Ordinary-user physical service: helper-issued descriptors, no arbitrary paths.

use super::{
    shutdown_signals::ShutdownSignals,
    virtual_server::{Control, EventOutput, controls, parse},
};
use rustusb_core::{Error, Result};
use rustusb_daemon::{Request, open_physical, request};
use rustusb_rust_backend::{
    PhysicalBackend, PhysicalSnapshot, PhysicalSource, ServerConfig, ServerHandle,
};
use serde_json::json;
use std::{
    collections::HashSet,
    future::Future,
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
    let mut lease_ids = Vec::with_capacity(device_ids.len());
    let mut initializing = false;
    let prepared = {
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
            if status
                .data
                .get("physical_journal_schema_version")
                .and_then(|value| value.as_u64())
                != Some(1)
            {
                return Err(Error::Unsupported(
                    "install a matching helper with physical delegation journal schema 1".into(),
                ));
            }
            let mut sources = Vec::with_capacity(device_ids.len());
            for device_id in device_ids {
                let grant = open_physical(socket, device_id).await?;
                // Keep received identities outside the cancelable preparation
                // future; dropping its sources cannot lose the finish list.
                lease_ids.push(grant.lease_id);
                sources.push(PhysicalSource {
                    fd: grant.fd,
                    device: grant.device,
                    receipt: grant.receipt,
                });
            }
            initializing = true;
            let result = PhysicalBackend::from_sources(sources).await;
            initializing = false;
            result
        };
        tokio::pin!(prepare);
        tokio::select! {
            biased;
            signal = signals.recv() => signal.map(|()| None),
            backend = &mut prepare => backend.map(Some),
        }
    };
    let backend = match prepared {
        Ok(Some(backend)) => backend,
        result => {
            if initializing {
                // A dropped blocking initializer may still own an FD. Do not
                // assert that this process has finished its known capabilities.
                return Err(Error::State(
                    "physical initialization has not ended; received delegations remain pending for administrator reconciliation".into(),
                ));
            }
            // The preparation future and all staged source FDs are now gone.
            finish_leases(socket, &lease_ids).await?;
            return result.map(|_| ());
        }
    };
    // Do not race bind: its returned handle may already own a listener task.
    let server = match ServerHandle::bind_physical(config, backend.clone()).await {
        Ok(server) => server,
        Err(error) => {
            close_and_finish(socket, backend, &lease_ids).await?;
            return Err(error);
        }
    };
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
    // Finish is not raced against signals or stdout. The helper independently
    // checks its saved baseline after our known owners and descriptors end.
    let closed = close_and_finish(socket, backend, &lease_ids).await;
    if let Err(error) = &closed {
        tracing::warn!(error = %error, "physical delegation finish remains unresolved");
    }
    cleanup?;
    let snapshot = closed?;
    result?;
    if output.in_flight {
        return Ok(());
    }
    tokio::select! {
        biased;
        signal = signals.recv() => signal,
        result = output.event(json!({"schema_version":1,"event":"stopped","snapshot":snapshot})) => result,
    }
}

async fn close_and_finish(
    socket: &Path,
    backend: PhysicalBackend,
    lease_ids: &[String],
) -> Result<PhysicalSnapshot> {
    let snapshot = backend.close().await?;
    finish_leases(socket, lease_ids).await?;
    Ok(snapshot)
}

async fn finish_leases(socket: &Path, lease_ids: &[String]) -> Result<()> {
    finish_all(lease_ids, |lease_id| async move {
        let response = request(socket, Request::FinishPhysical { lease_id }).await?;
        if !response.ok {
            return Err(Error::State(response.error.unwrap_or_else(|| {
                "helper could not verify physical delegation completion".into()
            })));
        }
        Ok(())
    })
    .await
}

async fn finish_all<F, T>(lease_ids: &[String], mut finish: F) -> Result<()>
where
    F: FnMut(String) -> T,
    T: Future<Output = Result<()>>,
{
    let mut first_error = None;
    for lease_id in lease_ids {
        if let Err(error) = finish(lease_id.clone()).await {
            tracing::warn!(error = %error, "physical delegation could not be finished");
            if first_error.is_none() {
                first_error = Some(error);
            }
        }
    }
    match first_error {
        Some(error) => Err(error),
        None => Ok(()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn finish_attempts_every_received_lease_and_preserves_first_error() {
        let mut attempted = Vec::new();
        let leases = vec!["first".into(), "second".into(), "third".into()];
        let result = finish_all(&leases, |lease_id| {
            attempted.push(lease_id.clone());
            std::future::ready(match lease_id.as_str() {
                "first" => Err(Error::Permission("first rejection".into())),
                "second" => Err(Error::State("second rejection".into())),
                _ => Ok(()),
            })
        })
        .await;
        assert_eq!(attempted, leases);
        assert!(matches!(result, Err(Error::Permission(_))));
    }

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

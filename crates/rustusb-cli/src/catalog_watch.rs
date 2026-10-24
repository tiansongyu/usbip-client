//! Signal/output adapter for the common authenticated catalog observer.
use crate::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::{Error, Result};
use rustusb_service::catalog::{
    CatalogFailure, CatalogMonitor, CatalogSource, CatalogState, CatalogUpdate, RefreshInterval,
};
use serde_json::json;

fn frames(update: &CatalogUpdate) -> impl Iterator<Item = serde_json::Value> + '_ {
    let view = &update.snapshot;
    let count = view.devices.as_ref().map(|devices| devices.len());
    let begin = json!({"event":"catalog_begin","schema_version":1,"instance":view.instance,
        "revision":view.revision,"origin":view.origin,"state":view.state,"failure":view.failure,
        "attempts":view.attempts,"sampled_ms":view.sampled_ms,"last_success_ms":view.last_success_ms,
        "journal_healthy":view.journal_healthy,"device_count":count,"change_count":view.changes.len(),
        "next_refresh_seconds":view.next_refresh_seconds,"resync_required":update.resync_required,
        "observation_only":true});
    let devices=view.devices.as_deref().unwrap_or_default().iter().map(|device|json!({
        "event":"catalog_device","schema_version":1,"instance":view.instance,"revision":view.revision,"catalog":device
    }));
    let changes=view.changes.iter().map(|change|json!({
        "event":"catalog_change","schema_version":1,"instance":view.instance,"revision":view.revision,"change":change
    }));
    let end = json!({"event":"catalog_end","schema_version":1,"instance":view.instance,
        "revision":view.revision,"device_count":count,"change_count":view.changes.len()});
    std::iter::once(begin)
        .chain(devices)
        .chain(changes)
        .chain(std::iter::once(end))
}

async fn update(output: &mut EventOutput, update: &CatalogUpdate, json: bool) -> Result<()> {
    if json {
        for frame in frames(update) {
            output.event(frame).await?;
        }
    } else {
        let view = &update.snapshot;
        output
            .text(format!(
                "CATALOG {:?} revision={} devices={} failure={:?} resync={}\n",
                view.state,
                view.revision,
                view.devices
                    .as_ref()
                    .map(|d| d.len().to_string())
                    .unwrap_or_else(|| "unknown".into()),
                view.failure,
                update.resync_required
            ))
            .await?;
        if let Some(devices) = &view.devices {
            for entry in devices.iter() {
                let device = &entry.device;
                output
                    .text(format!(
                        "{} {} {:04x}:{:04x} address={}:{}\n",
                        entry.device_key,
                        device.bus_id,
                        device.vendor_id,
                        device.product_id,
                        device.bus_number,
                        device.device_number
                    ))
                    .await?;
            }
        }
        for change in &view.changes {
            output
                .text(format!("{:?} {}\n", change.kind, change.device_key))
                .await?;
        }
        output
            .text(format!("END revision={}\n", view.revision))
            .await?;
    }
    Ok(())
}

fn terminal_error(failure: Option<CatalogFailure>) -> Error {
    match failure {
        Some(CatalogFailure::Permission) => {
            Error::Permission("catalog observation permission or TLS verification failed".into())
        }
        Some(CatalogFailure::InvalidInput) => {
            Error::InvalidInput("catalog observation source rejected".into())
        }
        Some(CatalogFailure::Protocol) => {
            Error::Protocol("catalog observation metadata rejected".into())
        }
        Some(CatalogFailure::Unsupported) => {
            Error::Unsupported("catalog observation unavailable".into())
        }
        _ => Error::State("catalog observation ended with a terminal failure".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::{BusId, DeviceKey};
    use rustusb_protocol::{RemoteDevice, RemoteInterface, secure::CatalogDevice};
    use rustusb_service::catalog::{
        CatalogChange, CatalogChangeKind, CatalogOrigin, CatalogSnapshot,
    };
    use std::sync::Arc;

    fn snapshot() -> CatalogSnapshot {
        CatalogSnapshot {
            instance: "test-observer".into(),
            revision: 7,
            origin: CatalogOrigin::AgentProfile,
            state: CatalogState::Current,
            failure: None,
            attempts: 5,
            sampled_ms: 10000,
            last_success_ms: Some(10000),
            journal_healthy: Some(true),
            devices: None,
            changes: Vec::new(),
            next_refresh_seconds: 2,
            resync_required: false,
        }
    }

    #[test]
    fn maximum_catalog_batch_is_complete_bounded_and_key_qualified() -> Result<()> {
        let mut view = snapshot();
        let mut devices = Vec::new();
        for index in 0..16_u8 {
            let key = DeviceKey::native(&format!("usb-{index:064x}"))?;
            devices.push(CatalogDevice {
                device_key: key.clone(),
                device: RemoteDevice {
                    bus_id: BusId::new(format!("65535-{}", index + 1))?,
                    path: "p".repeat(255),
                    bus_number: 65535,
                    device_number: 255,
                    speed: 6,
                    vendor_id: 65535,
                    product_id: 65535,
                    device_version: 65535,
                    class: 255,
                    subclass: 255,
                    protocol: 255,
                    configuration: 255,
                    num_configurations: 255,
                    interfaces: vec![
                        RemoteInterface {
                            class: 255,
                            subclass: 255,
                            protocol: 255
                        };
                        32
                    ],
                },
            });
            view.changes.push(CatalogChange {
                kind: CatalogChangeKind::Added,
                device_key: key,
            });
            view.changes.push(CatalogChange {
                kind: CatalogChangeKind::Removed,
                device_key: DeviceKey::native(&format!("usb-{:064x}", index + 32))?,
            });
        }
        rustusb_protocol::secure::validate_catalog(&devices)?;
        view.devices = Some(devices.into());
        let update = CatalogUpdate {
            snapshot: Arc::new(view),
            resync_required: true,
        };
        let frames = frames(&update).collect::<Vec<_>>();
        assert_eq!(frames.len(), 50);
        assert_eq!(frames[0]["event"], "catalog_begin");
        assert_eq!(frames[0]["device_count"], 16);
        assert_eq!(frames[0]["change_count"], 32);
        assert_eq!(frames[49]["event"], "catalog_end");
        assert_eq!(frames[49]["device_count"], 16);
        for frame in frames {
            assert!(
                serde_json::to_vec(&frame)
                    .map_err(|_| Error::State("test encode".into()))?
                    .len()
                    < 65536
            );
            assert_eq!(frame["instance"], "test-observer");
            assert_eq!(frame["revision"], 7);
        }
        Ok(())
    }

    #[test]
    fn no_success_or_terminal_clear_is_unknown_and_never_an_empty_success() {
        let mut view = snapshot();
        view.state = CatalogState::Failed;
        view.failure = Some(CatalogFailure::Permission);
        let update = CatalogUpdate {
            snapshot: Arc::new(view),
            resync_required: true,
        };
        let frames = frames(&update).collect::<Vec<_>>();
        assert_eq!(frames.len(), 2);
        assert!(frames[0]["device_count"].is_null() && frames[1]["device_count"].is_null());
        assert_eq!(frames[0]["state"], "failed");
        assert_eq!(frames[0]["failure"], "permission");
    }
}

pub(super) async fn run(
    source: CatalogSource,
    interval: RefreshInterval,
    json: bool,
    mut signals: ShutdownSignals,
) -> Result<()> {
    let mut output = EventOutput::new()?;
    let owner = CatalogMonitor::start(source, interval)?;
    let mut subscription = owner.subscribe();
    let result = loop {
        let next = tokio::select! {
            biased;
            signal=signals.recv()=>break signal,
            result=subscription.next()=>result,
        };
        let next = match next {
            Ok(next) => next,
            Err(error) => break Err(error),
        };
        let written = tokio::select! {
            biased;
            signal=signals.recv()=>break signal,
            result=update(&mut output,&next,json)=>result,
        };
        if let Err(error) = written {
            break Err(error);
        }
        if next.snapshot.state == CatalogState::Failed {
            break Err(terminal_error(next.snapshot.failure));
        }
        if next.snapshot.state == CatalogState::Stopped {
            break Ok(());
        }
    };
    // Never race/drop the actual join on another signal or output failure.
    owner.shutdown().await?;
    result?;
    if !output.in_flight {
        tokio::select! {
            signal=signals.recv()=>signal?,
            result=async {
                if json {output.event(json!({"event":"stopped","schema_version":1,"catalog_observer_joined":true,"observation_only":true})).await}
                else {output.text("Remote catalog observation stopped; local observer joined.\n".into()).await}
            }=>result?,
        }
    }
    Ok(())
}

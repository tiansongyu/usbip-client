//! Device observation output, without device operations or a second scanner.

use crate::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::Result;
use rustusb_service::discovery::{DeviceMonitor, DeviceUpdate, DiscoveryState};
use serde_json::json;

async fn update(output: &mut EventOutput, update: &DeviceUpdate, json: bool) -> Result<()> {
    let view = &update.snapshot;
    let count = view.devices.as_ref().map(|devices| devices.len());
    if json {
        output.event(json!({
            "event":"snapshot_begin", "schema_version":1, "instance":view.instance,
            "revision":view.revision, "state":view.state, "failure":view.failure,
            "reason":view.reason, "sampled_ms":view.sampled_ms, "resync_required":update.resync_required,
            "device_count":count, "changes":view.changes, "counters":view.counters,
            "receive_buffer_bytes":view.receive_buffer_bytes,
            "source":"kernel_uevent_hints_and_sysfs", "observation_only":true,
        })).await?;
        if let Some(devices) = &view.devices {
            for device in devices.iter() {
                output
                    .event(
                        json!({"event":"device", "schema_version":1, "instance":view.instance,
                    "revision":view.revision, "observation":device}),
                    )
                    .await?;
            }
        }
        output
            .event(
                json!({"event":"snapshot_end", "schema_version":1, "instance":view.instance,
            "revision":view.revision, "device_count":count}),
            )
            .await
    } else {
        let summary = if view.state == DiscoveryState::Stale {
            format!(
                "STALE revision={} failure={:?}; retaining {}\n",
                view.revision,
                view.failure,
                count
                    .map(|count| format!("{count} devices from last successful scan"))
                    .unwrap_or_else(|| "no successful inventory yet".into())
            )
        } else {
            format!(
                "SNAPSHOT revision={} devices={} resync={} reason={:?}\n",
                view.revision,
                count.unwrap_or_default(),
                update.resync_required,
                view.reason
            )
        };
        output.text(summary).await?;
        if let Some(devices) = &view.devices {
            for observation in devices.iter() {
                let device = &observation.device;
                output
                    .text(format!(
                        "{} {} {:04x}:{:04x} {} / {}\n",
                        device.id,
                        device.bus_id,
                        device.vendor_id,
                        device.product_id,
                        device.product.as_deref().unwrap_or("unknown"),
                        if device.exported {
                            "exported"
                        } else if device.export_blockers.is_empty() {
                            "eligible; unverified"
                        } else {
                            "not eligible; inspect devices --json"
                        }
                    ))
                    .await?;
            }
        }
        for change in &view.changes {
            output
                .text(format!(
                    "{:?} {} {}\n",
                    change.kind, change.device_id, change.bus_id
                ))
                .await?;
        }
        output
            .text(format!("END revision={}\n", view.revision))
            .await
    }
}

pub(super) async fn run(json: bool) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    let mut output = EventOutput::new()?;
    let opening = DeviceMonitor::open();
    tokio::pin!(opening);
    let owner = tokio::select! {
        result = &mut opening => result?,
        stopped = signals.recv() => {
            // Preserve the actual accepted preparation, even if it is still
            // waiting for its blocking owner to open the notification source.
            stopped?;
            let owner = opening.await?;
            return owner.shutdown().await;
        }
    };
    let mut subscription = owner.subscribe();
    let result = loop {
        let next = tokio::select! {
            result = subscription.next() => result,
            stopped = signals.recv() => break stopped,
        };
        let next = match next {
            Ok(next) => next,
            Err(error) => break Err(error),
        };
        let result = tokio::select! {
            result = update(&mut output, &next, json) => result,
            stopped = signals.recv() => break stopped,
        };
        if let Err(error) = result {
            break Err(error);
        }
    };
    // Never select/drop this join on a second signal. No output owns the scanner.
    owner.shutdown().await?;
    result?;
    if !output.in_flight {
        tokio::select! {
            result = async {
                if json { output.event(json!({"event":"stopped","schema_version":1,"observation_worker_joined":true})).await }
                else { output.text("Local device observation stopped; worker joined.\n".into()).await }
            } => result?,
            stopped = signals.recv() => stopped?,
        }
    }
    Ok(())
}

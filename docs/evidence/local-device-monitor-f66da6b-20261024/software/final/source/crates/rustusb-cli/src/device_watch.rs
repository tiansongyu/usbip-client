//! Device observation output, without device operations or a second scanner.

use crate::{event_output::EventOutput, shutdown_signals::ShutdownSignals};
use rustusb_core::Result;
use rustusb_discovery::{DeviceMonitor, DeviceUpdate, DiscoveryState};
use serde_json::json;

fn frames(update: &DeviceUpdate) -> impl Iterator<Item = serde_json::Value> + '_ {
    let view = &update.snapshot;
    let count = view.devices.as_ref().map(|devices| devices.len());
    let begin = json!({
        "event":"snapshot_begin", "schema_version":1, "instance":view.instance,
        "revision":view.revision, "state":view.state, "failure":view.failure,
        "reason":view.reason, "sampled_ms":view.sampled_ms, "resync_required":update.resync_required,
        "device_count":count, "change_count":view.changes.len(), "counters":view.counters,
        "receive_buffer_bytes":view.receive_buffer_bytes,
        "source":"kernel_uevent_hints_and_sysfs", "observation_only":true,
    });
    let devices = view
        .devices
        .as_deref()
        .unwrap_or_default()
        .iter()
        .map(|device| {
            json!({
                "event":"device", "schema_version":1, "instance":view.instance,
                "revision":view.revision, "observation":device
            })
        });
    let changes = view.changes.iter().map(|change| {
        json!({
            "event":"device_change", "schema_version":1, "instance":view.instance,
            "revision":view.revision, "change":change
        })
    });
    let end = json!({"event":"snapshot_end", "schema_version":1, "instance":view.instance,
        "revision":view.revision, "device_count":count, "change_count":view.changes.len()});
    std::iter::once(begin)
        .chain(devices)
        .chain(changes)
        .chain(std::iter::once(end))
}

async fn update(output: &mut EventOutput, update: &DeviceUpdate, json: bool) -> Result<()> {
    let view = &update.snapshot;
    let count = view.devices.as_ref().map(|devices| devices.len());
    if json {
        for frame in frames(update) {
            output.event(frame).await?;
        }
        Ok(())
    } else {
        let summary = if view.state == DiscoveryState::Initializing {
            "INITIALIZING kernel source bound; no successful inventory yet\n".into()
        } else if view.state == DiscoveryState::Stale {
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

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::{BusId, Device, Error};
    use rustusb_discovery::{
        DeviceChange, DeviceChangeKind, DeviceSnapshot, DiscoveryCounters, DiscoveryFailure,
        DiscoveryReason, ObservationStamp, ObservedDevice,
    };
    use std::sync::Arc;

    fn view() -> DeviceSnapshot {
        DeviceSnapshot {
            instance: "epoch".into(),
            revision: 7,
            state: DiscoveryState::Current,
            failure: None,
            reason: DiscoveryReason::UsbHint,
            sampled_ms: 1,
            devices: None,
            changes: Vec::new(),
            counters: DiscoveryCounters::default(),
            receive_buffer_bytes: 131072,
        }
    }

    #[test]
    fn maximum_legal_change_batch_is_complete_and_each_frame_is_bounded() -> Result<()> {
        let mut snapshot = view();
        let mut records = Vec::new();
        for index in 0..256_u16 {
            let id = format!("usb-{}", "a".repeat(64));
            let bus_id = BusId::new(format!("{}-2", index + 1001))?;
            let device = Device {
                id: id.clone(),
                bus_id: bus_id.clone(),
                vendor_id: 0x1234,
                product_id: 0x5678,
                manufacturer: None,
                product: Some(if index == 0 {
                    "x".repeat(46 * 1024)
                } else {
                    "fixture".into()
                }),
                serial: None,
                device_address: 4,
                usb_version: "2.00".into(),
                speed: "480".into(),
                class: 3,
                subclass: 0,
                protocol: 0,
                topology: "test/port2".into(),
                driver: None,
                interfaces: Vec::new(),
                exported: false,
                occupied: None,
                export_blockers: Vec::new(),
            };
            records.push(ObservedDevice {
                device,
                stamp: ObservationStamp {
                    sysfs_device: 1,
                    sysfs_inode: index.into(),
                    bus_number: index + 1001,
                    device_number: 4,
                },
            });
            snapshot.changes.push(DeviceChange {
                kind: DeviceChangeKind::Removed,
                device_id: id.clone(),
                bus_id: BusId::new(format!("{}-2", index + 1))?,
            });
            snapshot.changes.push(DeviceChange {
                kind: DeviceChangeKind::Added,
                device_id: id,
                bus_id,
            });
        }
        snapshot.devices = Some(records.into());
        let update = DeviceUpdate {
            resync_required: true,
            snapshot: Arc::new(snapshot),
        };
        let frames = frames(&update).collect::<Vec<_>>();
        assert_eq!(frames.len(), 770);
        assert_eq!(frames[0]["event"], "snapshot_begin");
        assert_eq!(frames[0]["device_count"], 256);
        assert_eq!(frames[0]["change_count"], 512);
        assert!(frames[0].get("changes").is_none());
        for frame in &frames {
            assert!(
                serde_json::to_vec(frame)
                    .map_err(|_| Error::State("test encode".into()))?
                    .len()
                    < 65_536
            );
            assert_eq!(frame["revision"], 7);
            assert_eq!(frame["instance"], "epoch");
        }
        assert_eq!(frames[769]["event"], "snapshot_end");
        assert_eq!(frames[769]["device_count"], 256);
        assert_eq!(frames[769]["change_count"], 512);
        Ok(())
    }

    #[test]
    fn unavailable_inventory_is_explicitly_null_in_both_batch_boundaries() {
        let mut snapshot = view();
        snapshot.state = DiscoveryState::Stale;
        snapshot.failure = Some(DiscoveryFailure::Permission);
        let update = DeviceUpdate {
            resync_required: true,
            snapshot: Arc::new(snapshot),
        };
        let frames = frames(&update).collect::<Vec<_>>();
        assert_eq!(frames.len(), 2);
        assert!(frames[0]["device_count"].is_null());
        assert!(frames[1]["device_count"].is_null());
        assert_eq!(frames[0]["state"], "stale");
        assert_eq!(frames[0]["failure"], "permission");
    }
}

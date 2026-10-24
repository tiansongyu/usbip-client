# Observe local USB changes

`devices --watch` maintains a read-only local USB inventory for an ordinary Linux
user. Kernel USB notifications trigger a fresh sysfs scan; periodic reconciliation
also checks for changes. Device discovery does not establish redirection support.

Build with the [normal source instructions](../README.md#build-from-source), then run:

```sh
target/release/rustusb devices --watch
# Complete, versioned JSON batches for a local integration:
target/release/rustusb devices --watch --json
```

No helper configuration is required for observation. The command does not export,
import, unbind, rebind or automatically reconnect a device. The QEMU test's separate
guest administrator performs its controlled driver changes.

Press Ctrl-C or send SIGTERM to stop. The command cooperatively stops and joins its
actual observation worker. Output failure also starts that cleanup. A blocked OS
filesystem read can delay the join; an observation timeout does not cancel such a
read. A slow stdout consumer does not acquire the scanner's ownership.

## Read the quality and identity fields

- `initializing`: the event source is bound; no complete inventory is available yet.
- `current`: this scan succeeded. Eligibility blockers and compatibility still need
  their separate checks, and hardware can change after any snapshot.
- `stale`: the latest scan failed. Any devices are retained from the previous
  success. A first failure has no successful inventory to retain.

Candidate fingerprints combine descriptor identity, optional serial and physical
port topology. Bus IDs are current addresses. Observation stamps additionally
record sysfs device/inode and USB bus/address. These fields help distinguish
observations; none authenticate hardware or authorize a device operation. A
replacement without a distinguishing serial can retain the same fingerprint.

JSON device records contain ordinary discovery metadata, including serial numbers
when readable. Redact them before sharing. The process test keeps raw host output
in private local directories and exports only a closed projection.

## Consume complete JSON batches

Each line is one JSON frame with schema version 1. Apply a batch only after its
matching `snapshot_end`:

```text
snapshot_begin: instance, revision, state, counts, reason and resync flag
  device: one complete observation per device
  device_change: one small change index per observed difference
snapshot_end: matching instance, revision and counts
```

`device_count: null` means no successful inventory yet. A successful empty
inventory has `device_count: 0`. A stale retained inventory must not be interpreted
as a new availability decision or a set of removals.

Changes distinguish added, removed, metadata changed and generation changed.
The source publishes bounded latest views, rather than durable history of every
kernel event. When `resync_required` is true, replace the view from the complete
batch and discard assumed delta continuity. The flag covers first delivery,
revision gaps, explicit notification loss and stale recovery. Compare revisions
only within the same instance. An interrupted batch is incomplete, even when
some device frames were printed.

When output remains usable, `stopped` with `observation_worker_joined: true`
records the completed worker join. Output backpressure can prevent that last
frame; inspect the process exit status and retain the last complete batch.

## Boundaries and integration

The shared `rustusb-discovery` library provides `scan`, `DeviceMonitor`,
`DeviceSubscription` and typed observations. The application service re-exports
the same implementation as `rustusb_service::discovery`. CLI, TUI and GUI callers
can share this service without parsing a CLI subprocess. `subscribe` retains only
metadata; `shutdown().await` joins the actual accepted worker. Drop requests stop.

Two actual local discovery workers are admitted per process. A monitor holds its
permit until the worker ends; cancelling a waiter does not return actual capacity.
A published inventory is limited to 256 devices, 48 KiB per encoded observation
and 4 MiB in aggregate. Capacity rejection preserves the last good inventory.
Each output frame stays below 64 KiB.

The kernel source uses group 1, validates the actual sender address, and keeps
raw notification environment out of product logs. Queue overflow, truncation and
detected malformed trusted input have fixed counters and require resynchronizing.
Non-USB notifications are ignored. A 50 ms debounce, minimum 100 ms scan-start
interval and two-second reconciliation period limit repeated scans. These are
scheduling policies, not latency guarantees or bounds on kernel/OS work. Rapid
transient changes can be coalesced or missed; current state is re-scanned.

This entry point observes the local machine. Authenticated remote catalogs and
managed USB sessions retain their existing authorization and lifecycle contracts.
It does not implement automatic sharing rules, storage recovery, a TUI or a GUI.
See the [tested revisions and retained failures](research/local-device-monitor-validation.md).

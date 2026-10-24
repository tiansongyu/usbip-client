# Watch an authenticated remote catalog

RustUSB can refresh the remote catalog continuously without importing a device.
The CLI and future local frontends use the same Linux application-service
observer. Entries are authorized, currently visible metadata; they are not device
reservations or proof that a device is compatible or physically attached.

## Choose the caller

For an ordinary operator, first provision the
[application service](application-service-guide.md) and grant the fixed client
profile's `discover` action to that operator's actual Unix UID. Then run:

```sh
rustusb agent --config /etc/rustusb/agent.json discover lab-keyboard --watch
```

The operator reads the public trusted manifest and calls the agent. The agent
uses its protected TLS identity; the operator does not load its private key.
The profile fixes the endpoint and device rules. Each refresh is independently
authorized, and each successful agent discovery still commits its existing
audit entry. Frequent refresh therefore consumes WAL I/O and bounded audit
history. Multiple frontends are independent observers, not a shared server cache.

A dedicated broker account with its own protected client TLS configuration can
observe directly, after following the [secure setup guide](secure-guide.md):

```sh
rustusb secure-discover --config /etc/rustusb/client.json --watch
```

Both commands require a CLI built with `secure-transport` (included by
`--all-features`). Default Native `discover HOST` remains the existing one-shot
command over an administrator-protected network; there is no insecure fallback.
Both authenticated commands retain their original one-shot JSON when `--watch`
is absent.

## Refresh and stop

The default delay is two seconds **after** an attempt finishes. Set a whole-second
normal delay from 2 to 120 with `--refresh-seconds`:

```sh
rustusb agent --config /etc/rustusb/agent.json discover lab-keyboard \
  --watch --refresh-seconds 5 --json
```

Only one attempt runs at a time within an observer. Transient IO/timeout/service
state failures back off exponentially, up to 120 seconds; there are no catch-up
bursts. The agent retains its existing four-second remote operation and five-second
local-request limits. Direct TLS discovery uses the existing global `--timeout`
for each attempt, not as an overall watch lifetime.

Press Ctrl-C or send SIGTERM. The CLI stops and joins its actual local observer,
including a direct TLS framing driver it owns. Stopping an agent observer can
close its read-only local request; the agent still owns any accepted remote
operation and its successful audit. An observer join is not remote USB cleanup.
Drop requests cooperative stop in the Rust API; callers must await `shutdown`
to join. Permanent OS/filesystem calls and existing libc resolver workers are
not claimed cancellable or bounded by these deadlines.

## Interpret a view

| State | Inventory meaning |
| --- | --- |
| `initializing` | No successful response yet; inventory is absent |
| `current` | Latest completed refresh returned this valid authorized snapshot |
| `stale` | A transient read failed; prior inventory is retained, if one exists |
| `failed` | Terminal permission/input/protocol/unsupported failure; current view cleared |
| `stopped` | Local observer ended; retained data is no longer refreshed |

An unavailable inventory is `null`; a successful empty inventory has count 0.
Failures never fabricate removal changes. Permission, invalid-input, protocol or
unsupported failure ends automatic refresh with a nonzero CLI exit. The existing
TLS path classifies a generic handshake failure as `permission`; this does not
identify a particular certificate fault, and an interruption at that stage also
ends the watch. Repair the configuration or connection before explicitly starting
a new observer.

Visibility can change because of authorization, export state or exclusive use.
In particular, the Native catalog hides reserved devices: a serial device can
disappear from the catalog while an existing connection is still using it, and
reappear when that owner releases it. Inspect sessions separately. A `removed`
change is not a physical-unplug or cleanup receipt.

Changes are keyed by the exact backend-qualified `device_key`, not list order.
`address_changed` reports a Bus ID/bus number/device address change for that key;
`metadata_changed` reports other descriptor changes. Neither establishes a
physical generation or authorizes reuse. Fingerprints remain candidate identities.

## Consume JSONL safely

Add `--json` for schema-1 `catalog_begin`, `catalog_device`, `catalog_change` and
`catalog_end` frames. Apply a view only after its complete matching instance,
revision, counts and end frame. A truncated or interrupted batch is incomplete.
Each frame is below 64 KiB, and a validated catalog contains at most 16 records.

The local observer instance UUID is not a remote server epoch or a token.
Revisions are monotonic; subscriptions may skip intermediate updates.
`resync_required` means replace the current display from the full snapshot.
Local elapsed `sampled_ms`/`last_success_ms` and the actually observed agent
`journal_healthy` describe observation history, not RTT, USB progress or a lease.
Slow consumers can miss short-lived changes. Existing copies retained by a caller
cannot be erased when the current view is cleared.

A final `stopped` receipt with `catalog_observer_joined: true` refers only to this
local observation task. It can be omitted after an interrupted output write;
do not manufacture a complete batch or remote cleanup proof from process exit.
Output backpressure holds metadata, not the actual polling/TLS owner.

The public Rust interfaces are in `rustusb_service::catalog`: `CatalogSource`,
`RefreshInterval`, `CatalogMonitor`, `CatalogSubscription` and `CatalogUpdate`.
Two actual observer owners are admitted per process. Metadata subscriptions do
not retain their source, private TLS configuration, sockets or quota.

See the [exact validation and retained failures](research/remote-catalog-watch-validation.md).
These are latest-state observations; real hardware, full G3, automatic sharing
and a completed TUI/GUI remain outside this delivery.

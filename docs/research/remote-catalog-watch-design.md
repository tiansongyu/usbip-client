# Shared authenticated remote catalog observation

2026-10-24, baseline `4e6a9081`. Status: implementing. This implements automatic
remote-list refresh from requirement 4.3 through the shared service library. It
does not complete Phase 3, implement remote auto-sharing, or advance the TUI/GUI
gates. The original Phase 0–7 and hardware requirements remain intact.

## Interface and reuse

Add Linux `rustusb_service::catalog` with a real asynchronous observation owner,
latest immutable snapshots, subscriptions and explicit cooperative shutdown.
Sources are either an already-loaded client TLS configuration (dedicated broker)
or a public trusted agent manifest plus a fixed client profile (ordinary
operator). The latter uses the existing trusted IPC client and `Discover`
request; the operator never reads a private TLS key. Endpoint, credentials and
device pool cannot be overridden by a watch request. No new RPC, disk schema,
crate, dependency, helper action or USB operation is required.

```rust,ignore
CatalogSource::agent(Arc<Manifest>, String) -> Result<CatalogSource>
CatalogSource::secure(Arc<LoadedClientConfig>, Duration) -> Result<CatalogSource>
RefreshInterval::new(u64) -> Result<RefreshInterval> // 2..=120 seconds
CatalogMonitor::start(CatalogSource, RefreshInterval) -> Result<CatalogMonitor>
CatalogMonitor::subscribe(&self) -> CatalogSubscription
CatalogMonitor::request_stop(&self)
CatalogMonitor::shutdown(self) -> impl Future<Output = Result<()>>
CatalogSubscription::next(&mut self) -> impl Future<Output = Result<CatalogUpdate>>
```

Two actual observation owners per process are admitted without waiting. Their
permits remain in the actual task until it returns, including network closing.
Subscribers hold metadata only. Drop requests stop; `shutdown` awaits the original
task and does not race/drop that join on another signal. Initial UUID allocation
reuses the existing Linux service UUID source; no UUID or candidate device key
is an authorization token. The API is called inside a live Tokio runtime.

The worker calls existing `jobs::discover_until` for direct TLS: raw-v1/framed-v2
and actual FIN/driver ownership are unchanged. For agent IPC, stopping may close
the read-only local request. The independent agent owner from E42 retains any
accepted remote read, its two-entry quota and successful audit even after that
caller disappears. Joining the local observer does **not** prove that remote
request or any USB resource has been cleaned up. Actual server shutdown still
joins its original owners. Direct TLS observers stop and join their own driver.
An existing libc lookup can still outlive its awaiter under the original four
actual resolver permits; observer completion is not resolver cancellation.

Use locked Tokio 1.54.1 watch/JoinHandle/semaphore/time APIs in their existing
ordinary patterns and the previously audited management codec. The design
skill's existing-stack exception applies: no new component research, generic
component skill, or independent delegation is needed. The existing E42 source
review and local observer contracts supply the ownership precedents. Configuration
loading stays in the CLI's owned blocking startup task and is joined on signals;
hard filesystem/libc/kernel blocking is not claimed cancellable.

## Freshness, identity and bounds

- Poll immediately, then wait the configured interval **after** completion;
  failures back off exponentially to 120 seconds. There are no catch-up bursts,
  overlapping reads within one observer, automatic import, authentication retry
  across alternative identities, or insecure fallback. A new polling attempt
  independently authenticates using the fixed source. A successful agent poll
  retains the existing successful-discovery audit, so frequent polling consumes
  WAL I/O and bounded audit history. Observers are not a shared server cache.
- States: `initializing`, `current`, `stale`, `failed`, `stopped`. Before the first
  successful result the catalog is `None`, not an invented empty list. Transient
  IO/timeout/state failures retain the prior catalog and publish stale quality
  without removal changes. Permission, invalid-input, protocol or unsupported
  failures clear the current view, publish terminal failure and stop; old copies
  already held by a caller cannot be erased. A successful empty catalog is real.
  The existing TLS handshake path uses the Permission class for a generic
  handshake failure; that category does not establish a specific certificate
  fault and it terminates the watcher, including an interruption at that stage.
- Sort by exact backend-qualified DeviceKey; validate at most 16 records, unique
  keys and Bus IDs, the existing secure addressing/path/interface bounds, and at
  most 60 KiB of encoded catalog. Changes distinguish added, removed, address
  changed, and metadata changed. A remote address change is not a hardware
  generation receipt; catalog removal can mean ACL/export visibility rather
  than physical unplug. Current means a successfully observed authorized snapshot,
  not a device reservation, compatibility result, heartbeat or cleanup proof.
- Each publication carries a fresh local observer instance UUID and a monotonic
  revision, attempt count, local elapsed sample/last-success time, and agent
  journal health when actually observed. No invented server epoch or RTT.
  Subscribers may skip revisions and must replace from the complete latest view
  on first delivery, gaps, stale recovery or terminal failure. Internal retention
  is bounded; external consumers can still retain their own old Arc copies.

## CLI and compatibility

```sh
rustusb agent --config /etc/rustusb/agent.json discover keyboard --watch
rustusb agent --config /etc/rustusb/agent.json discover keyboard --watch --json
rustusb secure-discover --config /etc/rustusb/client.json --watch --refresh-seconds 5 --json
```

The shared CLI adapter owns signals/stdout only. Both commands register INT/TERM
before configuration loading and bypass the outer generic helper cancellation
branch. Existing one-shot JSON and exit contracts remain unchanged. Legacy
unauthenticated `discover` remains one-shot and requires its existing protected
network; no implicit fallback or new default service dependency is added.

JSONL has version-1 `catalog_begin`, `catalog_device`, `catalog_change` and
`catalog_end` frames. Each frame stays below the existing 64 KiB EventOutput
bound. Apply only a complete matching instance/revision/end batch. Output
backpressure never owns the polling task or TLS driver. On signal/output error,
stop and join the real observer before returning; never append a generic JSON
error object to an incomplete streaming batch. A final stopped receipt refers
only to this observer and is omitted after an interrupted in-flight write.

## Validation

Use meaningful state/boundary tests and real loopback mTLS/raw-v1/framed-v2
observation/stop/FIN tests. Verify subscriber gaps, stale retention/recovery,
terminal permission clearing, stable-key address/metadata changes, no-overlap,
actual quota and malformed/duplicate/maximum input. Check CLI schema, one-shot
compatibility, both signals and actual closed/full stdout. Run all normal Cargo
gates, rustdoc and relevant existing regressions.

Add clean disposable QEMU evidence for an actual nonroot watcher observing remote
catalog changes and failures through the real installed agent/CLI, while retaining
real USB I/O and original cleanup proofs. Freeze source, executed ELF, raw output,
actual process waits, guest exits and failures. This is polling/latest-state
observation; do not claim every transient event, a remote physical generation,
real hardware support, complete G3 or a finished TUI.

The focused QEMU extension retains the existing managed Native two-epoch/three-I/O
fixture and every original verifier requirement. The operator watcher survives a
deliberately observed local-agent outage, then observes the actual remote server's
original export sessions being stopped individually (2 -> 1 -> 0), while the same
empty-catalog listener remains alive. It stops before the original server restart;
the old second-epoch I/O and complete root proofs/baselines still run. A fresh
complete catalog publication precedes a guest-only SIGSTOP/SIGCONT gate around
the agent kill, avoiding an intentionally partial TLS handshake in the original
all-positive packet classifier. No USB or production observer behavior is faked.
Separate installed CLI children cover direct TLS observation/TERM, an actually
full FIFO/blocked writer plus INT, closed output/exit1, and UID1002 denial. Record
real process UID/ELF/FD/wchan/waits and distinguish any fast denial lacking a live
ELF receipt. Keep the original ciphertext classifier intact.

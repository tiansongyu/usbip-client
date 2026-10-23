# Rust secure foreground and managed runner unification

Date: 2026-10-24. Status: proposed implementation slice, read-only source review. Base HEAD `9314aa08c41d84672467b06df8d0cf51e5d7ecf2`; working tree clean at initial observation. No repository edits, Cargo, USB, QEMU, new dependency research, or runtime tests performed. Applied the repository rustusb-development skill and user technical-design guidance; this is a local extraction of already implemented components, not a new technology decision or new component-skill project.

## Recommendation

Put the actual secure Rust backend/listener/physical-grant owner into one service runner, with a concrete private execution mode. Keep foreground JSON/stdin/signals in CLI. Keep managed persistence, tracked capabilities and per-IMPORT admission genuinely managed. Do not fabricate a JobContext, ephemeral application session IDs, an in-memory JobMessage acknowledger, or an auto-admitting ServerMonitor for foreground.

```
CLI renderer + stdin parser + signals
    -> bounded typed foreground commands / independent cooperative stop
    -> service Rust runner -> backend + ServerHandle + helper grant owner
agent runtime -> JobContext -> same service Rust runner
```

The shared runner has a small explicit branch at admission/helper/bind and a common real prepare/bind/serve/shutdown/close/Finish structure. Both branches preserve TLS authentication and the same atomic device-key/BusID ACL enforcement. The branch is a lifecycle policy, not an alternate TCP forwarding implementation.

## Current source facts and extraction seams

| Current code | Fact / required treatment |
| --- | --- |
| `cli/secure_server.rs:38` | Registers signals before blocking credential loading; joins the loader when a signal wins. Validates backend namespace before helper/bind. Preserve this pre-resource adapter behavior. |
| `cli/secure_server.rs:62` / `server_binding.rs:29` | Secure virtual currently uses `bind_tls`, physical `bind_physical_tls`. Preserve these untracked backend APIs for foreground; do not call managed bind and auto-admit. |
| `cli/virtual_server.rs:134` | Owns VirtualBackend and ServerHandle, emits ready, handles HID/CDC/snapshot/shutdown, then always awaits server shutdown. Move owner logic to service, keep parser and JSON rendering in CLI. |
| `cli/physical_server.rs:45` | Secure foreground calls `open_secure_physical` without correlation, keeps every received lease ID outside the cancelable prepare, and closes workers before FinishPhysical. The legacy plaintext command additionally has its old Status/schema checks; do not copy these into secure mode. |
| `cli/event_output.rs:43` | Two-entry encoded-output queue, individual event <65536 B, one deadline covers send plus actual stdout write/flush receipt; cancellation leaves in_flight true. A std thread owns only stdout/bytes, never resources. Preserve exactly in CLI. |
| `service/jobs/rust_server.rs:89` | Managed incoming begin_session is durably acknowledged before admit; observation persists real lifecycle events and only acknowledges an actual cleanup=Some terminal. Keep this path intact. |
| `service/jobs/rust_server.rs:238` | Monitor consumer continues while ServerHandle shutdown joins actual attempts. Output must never enter this admission/cleanup dependency. |
| `service/jobs/rust_server.rs:277,367` | Exports begin before helper; managed grants use individual tracked attempts, then resource acknowledgements. Foreground must not call these methods or silently upgrade journal schemas. |
| `backend/server.rs:162,183,263,290` | Both bind families already exist. Unmanaged shutdown cancels connection tasks and then awaits the existing backend settle deadline; managed shutdown signals each owned attempt and joins it. Preserve their existing different contracts, rather than claiming foreground has newly acquired managed per-attempt proof. |

`BackendSnapshot` and `PhysicalSnapshot` already expose the exact foreground snapshots. No JSON Value should cross the library lifecycle boundary. A physical stopped snapshot must be the value returned by the actual final `PhysicalBackend::close`, not a later fresh backend or an empty synthesized snapshot.

## Minimal concrete API candidate

Root should share names with the Native/client extraction. The following Rust-specific types can be variants of a common foreground module; they need no backend/core API change. All fields containing backend-specific types are feature-gated so Native-only builds stay independent of rust-backend/nusb.

```rust,ignore
// Public service API; illustrative signatures, not implemented.
pub enum RustServerSnapshot {
    Virtual(BackendSnapshot),
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    Physical(PhysicalSnapshot),
}
pub struct RustServerReady {
    pub address: SocketAddr,
    pub snapshot: RustServerSnapshot,
}
pub enum RustServerCommand {
    Snapshot,
    HidKey(u8),
    CdcEmit(CdcBytes), // validated, private Vec; 1..=1024 bytes
}
pub enum RustServerReply {
    Applied,
    Snapshot(RustServerSnapshot),
}
// Envelope has one reply sender. Receiver never waits for a renderer to consume it.
pub struct ForegroundRustContext { /* private stop receiver, ready sender,
                                      bounded command receiver */ }
pub struct ForegroundRustControl { /* private stop sender, command sender,
                                      one-use ready receiver */ }
pub fn foreground_rust_channel()
    -> (ForegroundRustControl, ForegroundRustContext);
impl ForegroundRustControl {
    pub fn request_stop(&self); // independent of command queue; idempotent
    pub async fn ready(&mut self) -> Result<RustServerReady>;
    pub async fn command(&self, op: RustServerCommand) -> Result<RustServerReply>;
}
pub struct RustServerOutcome {
    pub operation: Result<()>,
    pub cleanup: Result<()>,
    pub final_snapshot: Option<RustServerSnapshot>,
}
pub async fn run_foreground_rust(
    job: LoadedJob, // or a small exact Virtual/Physical server enum
    helper_socket: PathBuf,
    io: ForegroundRustContext,
) -> RustServerOutcome;
```

The existing `run_job(LoadedJob, JobContext)` signature can stay stable. Internally its two Rust variants call the same owner function as foreground through private `RustRunMode::{Managed(JobContext),Foreground(ForegroundRustContext)}`. The mode chooses real bind/grant/context actions. A private concrete reporter enum or Option of actual SessionContexts is sufficient; do not build a generic callback framework or duplicate complete run functions.

Use fixed command queue capacity 2, with private construction; CDC bytes validated both at public service constructor and CLI hex parsing. No arbitrary paths, JSON fields, certificates, listener addresses, FDs or helper requests can be supplied via commands. Foreground control is local test control only; do not add HID/CDC commands to production agent RPC. Physical mode returns a typed unsupported-control result for HID/CDC.

Ready is a single one-shot fact after actual bind/snapshot. Reply sends are nonblocking one-shot sends. Slow consumers must not accumulate events or retain cleanup. A command reply receiver dropping does not cancel the backend operation. The runner handles only these synchronous bounded backend controls, never output I/O. Stop is biased ahead of queued commands; channel disappearance requests stop in foreground. No arbitrary command ID is needed in service: CLI retains its validated id and pairs it with the single outstanding command response.

The owner future must be independently retained (spawned by the CLI adapter, or pinned and always rejoined). If a public Running handle owns a JoinHandle, Drop may request stop and detach its live owner, but never claim completion. The explicit successful path always awaits join. Cancellation of a CLI output/ready/command waiter cannot drop the actual runner. A bare caller-created future must document the same retain-and-join contract as existing run_job.

An alternative common `ForegroundEvent` envelope with an output delivery receipt is acceptable for Native/client compatibility, but that receipt means only CLI output delivery. It is not durable admission. If used, delivery failure/deadline requests stop and the owner still cleans up. Terminal data is returned after cleanup, not delivered through a channel whose receiver could block cleanup. Rust ready and command request/reply alone do not need an output acknowledgement inside the owner.

## Mode-specific effects and shared ownership

| Effect | Foreground | Managed |
| --- | --- | --- |
| Application sessions | None; no application store creation | Real JobContext/SessionContext durable Begin/Observe/Finish |
| USBFS grants | `open_secure_physical` | `attempt(UsbfsGrant)` then `open_secure_physical_tracked`, resource ack |
| Rust listener | `bind_tls` / `bind_physical_tls` | `bind_managed_tls` / physical variant plus consumer before accepted admission |
| IMPORT permission | Existing verified TLS peer plus engine atomic ACL | Same plus real durable admission |
| Per-session controls | No new foreground session API in this slice | Existing monitor/per-session stop |
| Endpoint output | CLI ready/snapshot/ack/error/stopped | Existing application facts; no stdout |
| Common teardown | Await actual listener shutdown; physical close; attempt Finish for every received grant when owners ended | Same concrete physical owners, plus existing tracked completion facts; consumer retained during shutdown |

Helper format preservation means **no new tracked writes due to foreground**, not unconditional disk schema 1. Fresh untracked Native remains schema 1; physical remains existing schema 1 or explicitly authorized schema 2. Already upgraded stores remain sticky Native2/physical3. Do not downgrade them or invent correlation None fields in old raw JSON.

For physical preparation, shared state must retain sources, received lease IDs and per-device tracked state outside any frontend waiter. Before starting each next helper request or listener bind, check cooperative stop. An accepted helper request and `PhysicalBackend::from_sources` must be joined; after it returns, stop closes the produced owner before any listener/ready. Do not cancel a possibly accepted helper request then pretend no grant exists. Unknown outcome remains unresolved, not a success receipt.

This improves a current foreground edge deliberately: `physical_server.rs:110-132` presently allows signal cancellation of an in-flight initializer and returns an explicit unresolved-pending error. The managed runner already awaits initialization. The shared secure runner should preserve early stop before acquisition and **no listener after stop**, while joining actual accepted initialization rather than abandoning it. This is a documented safety change, not a promise of bounded kernel completion; a truly stuck blocking initializer may still delay termination. Leave the unrelated legacy plaintext commands on their existing path for this slice unless root explicitly authorizes their extraction too.

Always retain operation and cleanup errors independently in the outcome. Keep attempting all known Finish requests after an individual Finish failure. If actual physical close is unconfirmed, do not issue a falsely authoritative cooperative Finish for still-live descriptors. No path returns `stopped` with a fabricated all-zero snapshot after a failure.

## CLI compatibility choreography

1. Register ShutdownSignals before configuration loading, retain current bounded/trusted reader and backend validation. Build EventOutput before resource acquisition. Start the independent service owner.
2. Race only adapter work (ready wait, typed command receipt, EventOutput) with signals. A signal/output failure/stdin EOF initiates cooperative stop and joins the same owner; repeated signals cannot replace or cancel the join.
3. Emit existing virtual ready `{schema_version:1,event:"ready",address,devices:<snapshot.devices>,transport:"mutual_tls"}`. Physical ready keeps its `snapshot` field. Do not replace these with managed session JSON.
4. Retain current strict <=4096 byte line parser, safe 1..64 ASCII id, hex validation, capacity-2 stdin thread, redacted invalid_control and control_rejected/unsupported_physical_control errors. Snapshot emits exactly the existing snapshot shape. Payload never enters events/tracing.
5. For shutdown control, emit the existing ack before ending the operations loop. If that ack blocks/fails, deadline or signal still requests stop and joins; do not wait forever for acknowledgement before cleanup. Stdin EOF stops only control-enabled foreground; no-control mode waits for signal.
6. After join, combine adapter operation failure and runner operation/cleanup without losing cleanup failures. Only successful cleanup and operation permit final stopped. If EventOutput.in_flight is true, skip final output as today. Otherwise race the final bounded event only with signals, after all owners have ended.

This preserves public JSON contract, foreground exit/backpressure behavior and the absence of application persistence. It does not retroactively provide managed crash recovery or durable sessions for a foreground process.

## Minimum meaningful verification after implementation

- **Real in-memory TLS virtual runner:** use actual trusted test configs/real TLS to discover/import and perform USB/IP requests through the new foreground service entry; assert no admission wait, real ready/snapshot/control operation, stop closes the real stream and final lease/queue counts are zero. Use the actual existing managed tests to retain durable-before-claim and terminal acknowledgement behavior. The foreground branch should have no JobContext/monitor at all.
- **Typed control isolation and bounds:** cap queue/CDC payload; reject physical HID/CDC; retain strict parser and redacted error tests. A cancelled command waiter and a dropped ready receiver must still result in a joined owner, not leaked listener or false terminal.
- **Stopped during accepted preparation:** deterministic controllable fake initializer/helper boundary; request stop after acceptance, assert owner future remains pending until real completion, no ready/listener afterward, every received grant closed/Finish attempted. Separately return unknown grant outcome and preserve unresolved error. This proves cancellation policy, not USB recovery.
- **Rendering compatibility:** exact ready/snapshot/ack/error/stopped shape tests against typed replies, including virtual devices versus physical snapshot and mutual_tls only on secure commands. Keep physical finish-all first-error/all-attempts test.
- **Real stdout backpressure/signals:** existing local two backpressure/four signal scripts target the plaintext `virtual-server`, so retain them but do not claim they cover the newly extracted secure command. Add a secure foreground child fixture using temporary trusted config and real TLS imports, block stdout, exercise deadline and SIGINT/SIGTERM; prove data socket closure and expected exit/in_flight final-output behavior. Do not make tests call fake persistence ack.
- **Fresh clean QEMU:** preserve original secure virtual/USBFS IPv4+actual IPv6 four paths with stdin HID/CDC, HID/FTDI raw bytes, two cycles/normal TERM/reuse and full external capture; full six matrix also needs Native after N's extraction. Foreground helper raw records remain untracked and final empty under the real fresh schema, with existing identity/baseline/NULL/Gone proofs. Managed Native agent regressions remain separate; they cannot substitute for new managed Rust QEMU coverage.

Existing 495 tests / previous QEMU results remain historical until new source is built and executed. This proposal does not complete heartbeat/RTT, bounded-history rotation, Rust crash-session proof, hardware G1/G2, or all Phase3 goals.

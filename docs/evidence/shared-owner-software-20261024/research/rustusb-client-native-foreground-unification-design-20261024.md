# Foreground and managed runner unification — read-only proposal

Baseline: clean `9314aa08c41d84672467b06df8d0cf51e5d7ecf2`, 2026-10-24. No repository edits, Cargo, product CLI, helper or QEMU execution. This proposes an implementation contract for root to accept; no interface below is already implemented.

## Smallest viable structure

Keep one real client owner and one Native server/export/stub owner under `rustusb-service::jobs`; each has a private explicit mode. `Managed(JobContext)` continues the current durable application Begin/Attempt/Resource/Observe/Finish and tracked helper methods. `Foreground(ForegroundContext)` uses the original untracked helper methods and typed presentation/control channels. It never fabricates a JobContext, SessionContext, app journal, monitor admission or an in-memory actor returning fake durable acknowledgements.

The public adapters should be concrete entry points such as `run_client_foreground(config: Arc<LoadedClientConfig>, timeout: Duration, context: ForegroundContext) -> ForegroundOutcome` and `run_native_foreground(config: Arc<LoadedServerConfig>, context: ForegroundContext) -> ForegroundOutcome`. Root may instead expose one concrete `ForegroundJob` enum. Reuse the same owner functions called by `run_job`; do not keep a second set of prepare/commit/relay/close functions behind a new facade. Rust virtual/USBFS integration is coordinated separately with I; no new crate or plugin trait is needed.

`ForegroundContext` carries a fixed helper path, independent sticky watch stop, bounded typed command receiver and bounded presentation sender. It contains no global stdin, stdout, process signal registration, clap type, principal constructor or arbitrary helper Request. Public library entry points accept already loaded immutable configurations; CLI still registers signals before owned `spawn_blocking` config loading, and joins that load when stopped. Namespace/UID/pool checks stay before any helper acquisition or listener. The agent RPC still accepts only root-manifest profile IDs and never exposes this foreground constructor as an operator override.

## Typed presentation and outcome

Use bounded typed presentation messages, not JSON as an internal control protocol. Required events are:

- `ClientConnected { server_id, device_key, receipt: NativeImportReceipt }`, retaining the actual complete returned receipt without reconstructing it from SessionState.
- `NativeReady { address, snapshot: NativePublishedSnapshot }` and `NativeSnapshot { request_id, snapshot }`; snapshot keeps `metadata_scope=published_registration_not_live_configuration` and the original per-device key/published_device/accepting_imports fields.
- Typed ack/invalid-control notification for the old stdin test contract. Native supports snapshot/shutdown only; virtual HID/CDC test injection remains explicit local foreground testing, never agent RPC.

Each presentation delivery can have a oneshot result. It confirms only output delivery, not application admission, root authorization, persistence or resource release. Keep this type/name separate from managed admission. Bound messages/counts, use channel capacity2 as today, and ensure the total event delivery budget remains5s. A shared absolute deadline carried to an `EventOutput::event_until` adapter avoids adding a second fresh5s window after a queue delay. Do not replace the existing independent std writer thread with Tokio stdout or a synchronous JSON fallback.

Return terminal data in `ForegroundOutcome { operation: Result<()>, cleanup: Result<()>, final_state: ... }` after the actual owner has finished cleanup. Client final state can retain receipt/counters; Native final state includes actual number of exports closed and cleanup confirmation. Do not queue a final Stopped/Disconnected event that cleanup/join must wait for: the frontend may already be gone or its output may be blocked. The CLI emits its existing final JSON only after a successful operation/cleanup projection and when `EventOutput.in_flight` is false.

The frontend driver must continue polling the owner while observing signals and output. On signal, control EOF/shutdown or output failure it sends stop and then joins the same owner; it must not drop/abort that future. A signal while output is blocked cancels only output waiting, preserves `in_flight`, skips additional final output, and still waits cleanup. Buffered presentation messages must be rejected/dropped without making a post-stop owner wait for a renderer. Repeated signals never replace cleanup. Record the external stop/output cause separately so a renderer-induced cancellation cannot erase the original output error; preserve cleanup error independently too.

## Required authorization and disk compatibility

Foreground dispatch must call `prepare_native_import`, `prepare_native_export`, `NativeExportHandle::open_stub`, and `open_secure_physical` exactly as the current foreground does. Managed dispatch keeps the corresponding `_tracked` calls, independent fresh correlations per import/export/stub/grant, and actual durable ack before mutation. Foreground Rust serving uses ordinary `bind_tls` / `bind_physical_tls`; managed uses `bind_managed_tls` and the durable monitor admission path.

Fresh untracked foreground Native state must remain disk1 with no correlation/terminal proof creation caused by an artificial app context. If a helper store was already upgraded by managed work it must not be downgraded; untracked records still remain untracked. The same compatibility rule applies to the original physical journal format. No new implicit opt-in restoration permission, fake trusted_network declaration, endpoint/key/pool override or OS role change is justified by extracting common code.

The dedicated broker UID remains the existing trust domain. Cross-UID operator/profile ACL stays in the managed IPC runtime, not in a JSON-supplied act_as_uid or an arbitrary callback. Low-level foreground library access does not claim to isolate malicious processes sharing the broker UID.

## Actual differences that cannot be silently flattened

1. Client relay EOF: foreground uses Tokio `copy_bidirectional`, which half-closes the opposite writer after an EOF and continues the remaining direction; managed `jobs::io::relay` returns at first EOF and drops both halves. Preserve explicit mode-specific termination policy during extraction, or record a separate behavior change with loss/half-close regression tests. This is not cosmetic deduplication. Counted network wrappers can preserve the pinned Tokio utility while collecting successful reads/writes; no engine dependency is needed for Native-only builds.
2. Client full receipt: foreground emits the real commit return value; managed currently uses commit only as LocalEnumeration evidence. The common owner must retain the real value for foreground output, not synthesize a partial receipt.
3. Native failed open: foreground sends a bounded standard IMPORT failure status2/4 when not stopped; managed currently closes the network on helper open failure. Preserve foreground negative reply behavior or explicitly approve/verify a common changed policy. Do not clear a failed/unconfirmed export back to Available.
4. Native cleanup structure: foreground owns direct handles and closes after joining connection tasks; managed has independently stoppable export sessions, an exclusive use lock and optional handle ownership. A common export owner can keep the stronger per-export structure, but foreground must not need a dummy persisted SessionContext. The whole-listener16 permit still covers TLS, raw relay and actual stub close; all stub tasks end before export baseline verification.
5. Native stop during prepare: foreground currently finishes an accepted prepare and then closes known handles; managed watches stop between non-cancelled helper requests. A common sticky stop implements both without dropping prepare or starting another device after stop. Do not race bind after it may own a listener.
6. Error projection: foreground client preserves both operation and cleanup contexts in its dual-error message; Native foreground has older cleanup-first `?` branches. Return separate actual results rather than relying on managed `combined`'s generic string. A refactor should not newly lose either result or call failed cleanup verified.
7. Foreground output backpressure intentionally stops its owned job. A slow or disconnected managed viewer must not stop a managed job. Do not route both through the same presentation sender.
8. Configuration load/discover: reuse `jobs::discover` for the actual read-only network operation, but preserve foreground array JSON (agent RPC uses a wrapper). Keep signal-aware owned loading and current cancellation/error behavior; global signals/stdio belong to CLI.
9. Rust physical preparation also differs: foreground currently can return explicit unconfirmed if a blocking initializer was cancelled, while managed waits initialization. I owns that design. Do not silently call this identical behavior or fabricate Finish while a real initializer may retain an FD.

## Foreground compatibility contract

Keep schema1 and the existing exact public event fields: client `connected` transport `mtls`, server_id, device_key, full receipt; successful final `disconnected` transport `mtls`, cleanup_verified true. Native `ready`/`snapshot`/`ack`/`stopped` keep backend native, transport mutual_tls, address/snapshot/id and exports_closed/export_cleanup_verified as applicable. Snapshot remains published pre-export registration, not current config/interface state.

CLI stdin remains a bounded4096-byte parser on its independent std reader, with capacity2 and1..64 ASCII identifiers. Invalid input is not echoed. Native EOF/shutdown stops its foreground job; no stdin reader is spawned when control_stdin is false. Preserve valid request ack ordering, signal cancellation of blocked output, and the no-second-output rule. Keep main's owns_signal_cleanup classification and no synchronous --json error fallback for these owner commands. Native-only secure-transport must not pull the Rust engine/nusb; non-Linux feature gates must still compile or return the existing Unsupported boundary.

## Minimum meaningful verification

- Deterministic real duplex tests distinguish first-EOF vs bidirectional half-close/drain, count partial successful writes, and prove cancellation closes both transport owners after joining. They do not claim USB behavior.
- Bounded foreground delivery/ack tests cover slow consumer, receiver drop, signal during an outstanding event, no final-event dependency, and no fake durable actor. Root should verify original EventOutput5s and in_flight semantics in actual subprocess tests.
- Preserve the original two backpressure/four signal tests, but describe them accurately: current scripts exercise legacy plaintext virtual-server, not all secure adapters. Reuse a truly common frontend driver and add secure-specific adapter validation rather than claiming that old path alone covered new TLS/Native branches.
- Frame/serialization regression keeps complete receipts and original ready/snapshot/ack/stopped fields, strict stdin limits, and no raw USB/certificate/key logging.
- Unit/helper fixtures must prove foreground takes untracked requests and managed still waits the real durable admission; no claim before ack and no in-memory substitute. Fresh helper disk1 behavior and existing upgraded-store no-downgrade are separate checks.
- Keep clean six foreground secure QEMU (three engines × IPv4/IPv6) and Native managed dual-family actual-config tests, matching each source/ELF. Verify real I/O, same-channel encryption/ACL, full baseline/NULL+old-generation cleanup, targeted stop and exact old root proof. New tests/collector counts are audit predicates, not USB case totals.

Hardware G1/G2, full Phase3, raw-v1 heartbeat/RTT, journal rotation, FD revocation and Rust crash sessions without independent proof remain outside this extraction. No new dependency or generic config framework is required.

## Fixed source note

Tokio1.54.1 local registry `src/io/util/copy_bidirectional.rs` lines43–57 and implementation lines126–145 establish the half-close/drain distinction. SHA256 `f4c99fbf7bfd1db974bcf2c2eaa0deb670486bc33d5787ecd09c3fe5b865bac2`. This is source inspection, not a new runtime experiment.

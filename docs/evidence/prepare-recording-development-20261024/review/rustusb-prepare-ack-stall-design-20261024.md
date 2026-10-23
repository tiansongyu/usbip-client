# Preparation ACK stalls: ownership review and proposed repair

Date: 2026-10-24. Status: **source diagnosis and proposed implementation**, not a runtime validation. This review makes no repository changes and executes no Git, Cargo, QEMU, USB operation, or existing verifier. Current bytes are locked in the accompanying JSON; no new HEAD attribution is inferred. The previous 626-software / clean531 fifteen-QEMU evidence does not cover this fault.

## Decision

The unresolved problem is broader than `Resource` alone. A durable wait after *any* resource acquisition can prevent the enclosing owner from reaching cleanup. Cover the complete preparation collection, then preserve each already-created recording future until its actual result. Separate **actual resource cleanup** from **recording finalization**. Stop initiates cleanup; it neither substitutes an ACK nor makes the job terminal. If storage remains blocked indefinitely, resources can be closed while the retained job remains pending indefinitely.

Use the existing trusted service and tracked/untracked contracts. No helper, application disk schema, IPC schema, ALPN, timeout budget, or admission bypass is required. No newly invented background recovery service is necessary.

## Current source facts

| Path | Actual owned resource during the wait | Current behavior |
| --- | --- | --- |
| Native export `native.rs:228–241` | Returned `NativeExportHandle`, real helper export | `Resource.await` has no stop selection. Handle remains in `prepare`; no top-level export watcher exists yet. |
| Native later-device prepare `native.rs:348–367` | All earlier successful exports | Top-level directly awaits the next `prepare`. Its `Begin`, Intent, Attempt, Resource or failure Finish ACK can prevent all earlier exports reaching `close_all`. Locally fixing the current export alone is insufficient. |
| Native ready `native.rs:387–419` | Listener and all exports | Available/ExportReady awaits precede installation of export watchers (`422–441`), so an ACK stall can hold the entire collection. |
| Rust USBFS `rust_server.rs:629–672` | `PhysicalSource` FDs plus received lease IDs | Resource ACK stalls before the initializer. A later device's Attempt ACK also stalls while earlier source FDs remain in the local vector. |
| Rust ready `rust_server.rs:451–506` | `ServerHandle`, physical/virtual Owner; potentially incoming leases | Available waits occur before the main stop select. The listener is already live. Stop cannot reach `server.shutdown()` until that ACK returns. |
| Client `client.rs:127–153` | Successful remote IMPORT, network IO and optional framed LinkOwner | Attempt ACK is unselected. Even without a local VHCI handle, the remote session can remain held. V2 driver failure does not itself advance this local future to owner join. |
| Native incoming `native.rs:691–714` | Authenticated network plus device reservation; no stub/use_lock yet | Begin/Intent/Attempt ACKs remain unselected. Export actual close is not locked out, but connection IO/driver/reservation and task joining can remain pending. Include this network preparation path if the scope is all actual preparation owners. |

`Context` does not implement cancellation internally: Begin/Attempt/Resource/Observe/Finish each await bounded-channel send and then its exact oneshot (`context.rs:86–186`). Thus the fault exists both while enqueueing into a full channel and after the actor accepts a transaction. `RunContext` preserves the distinction: foreground Begin is local, Attempt returns None, Resource/Observe/Finish are local no-ops; managed forwards actual calls (`run_context.rs:66–78,147–190`). Do not make all context methods silently stop-return-success.

### Existing correct boundaries to preserve

* Client after the actual helper handle: Resource and Prepared use retained futures (`client.rs:169–177`), as do Connected and samples. Stop or link termination leaves the original reporter-only future pending; network ends, LinkOwner joins, actual helper close runs, then the original record is awaited (`212–263`). Existing tests use actual in-memory streams/production helpers, not kernel proof.
* Native incoming after stub delivery similarly retains Resource/Prepared/Connected/sample (`native.rs:762–815`), ends network, closes the stub and releases `use_lock` **before** retained ACK / Closing / Finish (`820–875`). Accepted stub prepare and commit are still total-awaited.
* Native `close_all` starts all sibling close tasks before joining (`523–545`); `Export::close` performs actual close before its own Closing/Finish (`145–185`). This does not help during earlier preparation because the caller has not reached it.
* Rust incoming observer already requests actual backend disconnect while retaining its observer future during a recording wait (`rust_server.rs:207–216`). Backend is the owner, not the observer. Initial Begin may delay the observer, but managed admission has not yet claimed; actual backend admission/stop remains its separate bounded contract.
* Rust normal shutdown keeps the observer alive, then closes the registry and issues **every** Finish before waiting for export recording (`503–515,698–782,864–879`). Preserve that ordering.
* Pure read-only legacy Status remains cancelable (`rust_server.rs:590–599`). Accepted helper mutation and an already-started `PhysicalBackend::from_sources` remain total-awaited (`647–658,677–683`). A new ACK fix cannot claim to cancel a kernel-hard-blocked initializer.
* Foreground stdout delivery already selects sticky stop (`run_context.rs:82–92`). A delivery receipt is not a durable ACK.

## Stop delivery must not depend on the stalled actor

`ipc::serve` currently sends only the runtime shutdown watch (`ipc.rs:142`). `Runtime::run` fans out to Controls only after its select regains control (`runtime.rs:298–320`). `persist` awaits a blocking Store commit (`266–294`), so a stalled fsync can prevent SIGTERM from reaching any job. CLI correctly retains/awaits service (`agent.rs:132–138`); this is not a CLI cancellation problem.

Add a short, synchronous `Controls::begin_shutdown()` that sets a **sticky shutdown latch** and sends true to all existing job/session watches. Call it directly from the IPC stopping/error path before waiting for request tasks or the runtime; still send the runtime watch and total-await the owner. Do not hold this mutex over any await or filesystem work. Preserve authorization in the ordinary StopJob/StopSession fast path (`ipc.rs:189–205`, `runtime.rs:96–121`). This signal-only administrative fanout is not a new unprivileged bypass.

A one-shot broadcast without a latch is insufficient: `Start` can finish persist then register a fresh false watch/spawn (`runtime.rs:668–688`); Begin can likewise register a false session watch after persist (`853–874`). Registration must inherit the latch under the same short lock before its receiver reaches a worker. Also inherit an already-stopped parent job for late session registration. Reject new non-cleanup admissions after shutdown becomes visible; preserve the actual result of any transaction already started. A newly spawned stopped job must check sticky stop before any helper request. Do not rewrite an accepted Start transaction as if it never existed.

## Minimal private implementation shape

Use one internal, generic retained-record primitive rather than a new application-facing API:

```rust
// Sketch, not compiled code; F contains reporter/IDs only, never a handle.
enum RecordWait<T, F> {
    Done(rustusb_core::Result<T>),
    Interrupted { cause: StopCause, original: Pin<Box<F>> },
}
// Await exactly one original future against job/session stop and applicable
// invalidation/link-ended watches. On interruption transfer, never recreate it.
```

The typed output matters: Begin returns a real RunSession; Attempt returns the real optional correlation. They must not be collapsed into `Result<()>`, discarded, retried, or replaced with a synthetic ACK. Resource/Observe use unit output. Preserve only one pending stage per sequential preparation pipeline; bounded connections/exports retain existing bounds. The waiter must distinguish explicit stop, sender disappearance, device invalidation and typed link failure; only operation causes affect operation error. Recording failures remain `JobOutcome.recording`; actual close failures remain cleanup.

### Native export collection

1. Separate actual closing from lifecycle recording: `close_actual` takes the one handle, waits for any actual stub use, closes it, releases the use lock and returns an owned `ClosedExport` receipt containing session, actual cleanup result and immutable metadata. `ClosedExport::record` performs original pending stage (if any), then Closing and Finish. It owns no handle or lock.
2. There must be exactly one close authority per export. Prefer moving/taking the owning collection into its cleanup phase over cloning Arcs and calling `close` from two paths. A taken handle returning None must not be reclassified as verified success. Keep existing active-listener watcher ownership once serving starts.
3. During the sequential preparation loop, an in-flight current prepare future is retained. On stop, begin actual close for every earlier export immediately; do not await current prepare/its ACK first. The current prepare observes stop around recording waits and actually closes any returned current handle before waiting for that original ACK. If it is still inside accepted helper prepare, keep polling it to the actual reply and then close; all earlier independent resources can close meanwhile.
4. Do not have the current prepare's failure Finish block the earlier collection's cleanup: either return a cleanup receipt plus deferred recording to the parent, or use a retained current-preparation future concurrently with the moved previous collection's actual-close tasks. Both remain parent-owned and joined. The former makes the phase separation easier to audit.
5. Available needs the same separation. Stop drops the listener and runs all actual closes; the original Available future remains intact, then each session records Closing/Finish. Check stop between Available events so no later event is newly generated after actual close. An already-submitted Available is a historical fact, not current availability.

**Avoid the tempting shortcut** of concurrently spawning the existing `Export::close` while its same-session Resource/Available future may still be waiting to enqueue. Closing could be sent before the original event. Actual close may run concurrently; recording finalization must wait for the original event, preserving per-session order.

### Rust virtual/USBFS collection

1. Move source/lease tracking into a preparation owner that is visible to the outer cleanup phase, or return it together with an `Interrupted` result. Record futures capture cloned sessions and IDs only. A Resource or later Attempt interruption returns control to the outer owner **without awaiting the retained ACK**.
2. Before initialization has begun, stop drops every actual source FD, then performs Finish for every received lease. Do not call Finish while source FDs remain live. Unknown attempted grants without a returned ID remain Unconfirmed under the current contract.
3. After initialization has begun, retain and await that exact initializer; once it returns, stop prevents bind and the returned backend is actually closed. No select/drop of initializer, no timeout proof.
4. During Available waiting, preserve the real ServerHandle outside the cancelable record future. Stop runs `server.shutdown()`, closes Owner, issues every helper Finish, and only then drains pending record/observer ACKs and terminal recording. The observer's delayed ACK must not be awaited before physical close/Finish.
5. A stop after Attempt returns must be checked before another helper request. Foreground None with no actual request stays NotStarted. Existing durable reserved-but-unsent attempts stay conservative; do not silently introduce new proof semantics as part of this change.

### Client and Native incoming network preparation

Retain the original typed Attempt/Begin/Intent future while dropping the pure network IO and stopping/joining LinkOwner first. On eventual ACK, do not proceed to helper prepare; finalize the resulting session/attempt consistently. No network future may hide a LinkOwner taken into a cancelable select. Successful IMPORT must not use short-management FIN; normal network close plus retained owner join applies. Keep all accepted helper prepare/commit operations total-awaited, even if their caller was stopped while awaiting the response.

The client post-handle Resource/Prepared mechanism is already suitable; extend its reach instead of rewriting it. The Native incoming pre-stub fix need not hold `use_lock`: it currently acquires it only after durable Intent/Attempt, and that ordering must remain.

## Minimum regression plan

These are proposed gates, not executed results. Add meaningful phase/owner tests, not tests that merely duplicate helper code.

1. **Native two-export preparation**: real production orchestration with controlled fact replies. First export acquired; second Resource reply held. Send sticky stop (also test sender loss). Observe both actual close operations begin/complete while the original ACK sender is still alive and unresolved; no listener/Ready, no fake terminal. Then deliver original success/error and verify exact once/order of original Resource, Closing, Finish. Repeat held second Begin/Attempt and held Available, covering the sibling regression that a single-handle test misses.
2. **USBFS two-grant preparation**: hold first/second Resource or later Attempt; assert all received source FDs close before all Finish requests, no initializer/bind after stop. A helper/initializer already accepted is a separate test: delay its actual reply/completion, assert no detachment/terminal, release it, then verify exact close+Finish. Do not label a fake regular FD as a USB driver test.
3. **Rust Available**: actual bound virtual server (and physical harness counterpart), held ExportReady ACK, stop closes listener and exact active leases before ACK release. Ensure consumer/records continue afterward and do not prevent owner close. Keep original managed admission negative tests.
4. **Client / Native incoming pre-helper**: actual TCP+mTLS IMPORT or actual authenticated reservation, hold Attempt/Begin ACK. Stop/link-ended closes network and joins driver before ACK release; helper must see no new request. Resource/Prepared tests remain as regressions, with recording and actual-close errors independently retained.
5. **Actor/IPC shutdown**: use a test-only Store commit gate in an actual Runtime/IPC path (no production environment hook). Hold a transaction, stop service, verify independent existing controls are true. Hold Start/Begin transaction across shutdown and release: late registrations inherit true and no helper operation starts. Check unauthorized direct stop still has no effect; request timeout is not cleanup proof. Owner must remain joinable until the same transaction and recording tasks finish.
6. **Actual QEMU preparation cleanup**: a guest-only fixture can use the public managed `run_job`/`JobContext` against the real helper and real QEMU USB, while its explicitly test-only fact actor withholds a selected exact Resource/Available reply. This isolates owner behavior; it is not an actual fsync-stall claim. Native two-device and USBFS two-device cases must snapshot original full config/IF/alt/driver/generation, capture actual returned IDs/correlations, request stop, prove exact Root terminal/USBFS Finish and full baseline **before** releasing the ACK, then release it and run fresh actual HID/FTDI I/O. Include duplicate stop and failure ACK. No fake production success, no same-process resource-zero substitute. If claiming the complete deployed agent under real fsync blocking, add a separate externally observed gate on the actual journal transaction (e.g. a guest-only syscall tracer at the identified state temp-file fsync); establish TID/file/phase and keep other Tokio threads runnable. Do not substitute whole-agent SIGSTOP or an RPC wait for a disk stall.

The smallest honest delivery can combine the production Runtime software gate with the real-QEMU owner/USB fixture and explicitly state their separate coverage. Existing raw-v1/v2 foreground/agent, signal and backpressure regressions must continue; no need to inflate every phase into a new compatibility platform.

## Boundaries

* No timeout grants cleanup or terminal status. Permanent WAL blockage can leave final bookkeeping/job join pending after actual resource close.
* Accepted helper syscall, accepted commit, started blocking initializer, and actual close may themselves block; this review does not solve or reclassify those as ACK stalls.
* OS process crash/SIGKILL, arbitrary FD revocation, cross-configuration restoration, storage device safety, real hardware and full Phase3 remain separate.
* Source evidence here is an independent review method but not independent implementation authorship: this reviewer wrote portions of the shared owner and v2 service integration in earlier turns.

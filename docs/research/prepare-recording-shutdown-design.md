# Shutdown while preparation recording is stalled

2026-10-24. Baseline clean `e10b5fbe9df89216c0002ea11a51d41837fd9b03`. Status: proposed / implementation in progress. The preceding goal turn made verified progress: framed-v2 data-link health, 626 software executions, clean `531e0c3` fifteen-run QEMU validation and the English README/site publication. The original Phase 0–7 objective remains active.

This change addresses requirements 4.5 and 4.11: cooperative stop must reach actual owners while a preparation/ready journal acknowledgement is delayed. It does not make a stuck filesystem or kernel syscall cancellable, promise bounded process exit, or complete Phase 3. Hardware G1/G2 remains unexecuted.

## Current failure and affected owners

Source inspection at the baseline finds these concrete waits:

| Boundary | Actual ownership while the record waits | Required stop behavior |
| --- | --- | --- |
| Native export `prepare`, Resource | Received `NativeExportHandle`, exact tracked attempt | Actual helper close precedes waiting for the original Resource ACK |
| Native next-device Begin/Attempt/Resource | Earlier exports already prepared | Every received export has an independently reachable actual close; a later device's record cannot retain siblings |
| Native Available/ready | Listener and all exports | Stop accepting, close all actual exports, then preserve record order |
| USBFS Resource or later Attempt | Earlier received grant FDs in `sources` | Drop every actual source FD, attempt every received grant's Finish, then await original records |
| Rust server Available | Bound `ServerHandle` and backend owner | Actual server shutdown, backend close and grant Finish precede retained records |
| Client Attempt after successful remote IMPORT | Actual authenticated remote data connection; no local helper request yet | Stop/join the network before waiting for that original Attempt; do not start local helper work afterward |
| Agent process stop during `Runtime::persist` | Supervisor awaits a retained blocking store transaction | Independent sticky stop fanout reaches current and subsequently registered controls, while the real supervisor remains joined |

Existing client Resource/Prepared/Connected/sample and Native incoming record waiters already retain their exact futures and close real owners first. These are retained contracts, not evidence for the uncovered preparation paths. The old startup USBFS SIGTERM test stops at read-only Status with helper acceptance false; it does not prove accepted initializer shutdown.

## Ownership and sequencing

Continue using the same shared foreground/managed pipelines. Foreground delivery remains distinct from managed durable admission. No fake actor, untracked fallback, new privileged RPC, arbitrary endpoint or production fault-injection option is introduced.

1. Retain every already-started helper exchange and USBFS initializer to its actual result. A stop prevents later bind/claim/work; it never drops such a future to manufacture cleanup.
2. Recording waits hold only owned reporter/identity metadata. A private retained-record helper may preserve `Result<()>` or a correlation result across stop. It must not own a helper handle, stream, FD, lease, backend, permit or mutation lock. Do not discard a pending Begin's eventual session registration with a generic result mapper.
3. Interrupted preparation returns its original already-polled record to the resource-owning cleanup path. Stop is neither a successful ACK nor a persistence error. Preserve a real late ACK error independently from operation and cleanup results.
4. Native separates actual export close from Closing/Finish recording. Each capability has one close authority. Start all relevant actual closes before joining any record waiter; avoid cloning an owner into two close paths. An original Resource/Available transaction must settle before that same session's Closing/Finish is sent. Do not rely on scheduling or channel FIFO to repair a reversed sequence.
5. USBFS drops all received source FDs or closes the actual initialized backend, shuts down any actual server, and attempts all received grant Finishes before waiting for retained application records. One Finish error does not skip another grant. Never call Finish while a source/backend still owns the corresponding FD.
6. Original interrupted record ACKs precede subsequent Closing/Finish. Resources may already be gone while the job/session remains pending: neither process exit nor a durable terminal is fabricated. A stopped client Attempt cannot lead to a new helper request after the late ACK arrives.

The finite pool is at most sixteen exports. Retained metadata and close tasks remain bounded by actual prepared resources and the first interrupted preparation stage. No per-probe WAL work or unbounded background queue is added.

## Sticky service shutdown

The IPC serve loop has an independent OS-stop receiver. On stop or accept failure it must atomically latch shutdown in `Controls` and send cooperative stop to every current control before waiting for journal progress. The supervisor still receives its existing shutdown signal and is awaited through all real workers and recording tasks.

Registration of a job or session uses the same short controls mutex and inherits the latch. This closes the race where Start/Begin commits after the initial fanout and creates a fresh false watch. No mutex is held across an await. Authorized StopJob/StopSession retain their current fast path and durable response contract; a delayed RPC may time out even when actual stop has reached its owner. Unauthorized requests still cannot trigger stop.

Shutdown before startup completion does not prove a stuck store/loader is forcibly terminated. Lock poison remains an explicit failure and cannot cause an early return that abandons a live supervisor.

## Evidence and validation plan

The repository RustUSB and technical-documentation skills apply. The source contracts are [application service](application-service-design.md), [shared owners](shared-owner-design.md), [shared-owner limitations](shared-owner-validation.md) and [framed-v2](data-link-health-design.md). No dependency or transport policy change is planned.

Tokio is fixed at 1.54.1. Its installed upstream `macros/select.rs` documents dropped-future cancellation, and `task/join_set.rs` documents abort-on-drop and implements `shutdown` using abort. Preserve actual owner tasks and join them cooperatively. The versioned docs.rs select/JoinSet pages were attempted on 2026-10-24 but inaccessible through the web tool; local upstream source bytes and hashes will be recorded rather than claiming online verification.

Software regressions must use the actual production sequencing helpers, real `JobMessage` oneshots and owned stream/control receipts where possible. Ordinary-user fixtures cannot bypass the daemon client's root credential/path validation or claim real kernel cleanup. Required negatives include a delayed original ACK, late rejection, simultaneous stop, multiple exports, a later Attempt, Available after bind, cleanup failure, and controls registered after the shutdown latch. Observe actual close before releasing the record gate; a timeout is a failed assertion, not release evidence.

Disposable QEMU guests provide the privileged evidence. A guest-only ptrace gate may stop the actual store worker at the selected `state.next` fsync entry after the complete Resource transaction bytes identify the exact received capability; all unrelated threads keep running. Retain original state/next bytes, syscall/process identities, FD observations and helper proof. Use authorized StopJob or actual agent SIGTERM, independently observe resource release while the record gate remains held, then release the same syscall and join the original owner. The initial unmodified product run must retain a real failure if it cannot release under the gate.

An accepted USBFS initializer gate is separate: observe the real initialization syscall after the helper FD was accepted, deliver stop, keep the initializer task retained, release that same syscall, then require no bind and actual backend/FD close plus grant Finish. This is syscall-entry cooperative shutdown evidence; it is not an internal kernel hard-block or IMPORT/URB claim proof.

Freeze a clean source revision for actual QEMU runs, preserve every failure, collect source/tool/ELF/guest-exit receipts, and reconcile selected originals separately from the harness verifier. Run the complete formatting, default/all-features Clippy/tests/release/rustdoc/vendor gate and relevant existing signal/backpressure and secure/QEMU regressions. Keep software executions, diagnostic predicates, QEMU runs and physical-device acceptance separate.

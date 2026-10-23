# Owner wakeup while application recording is stalled

2026-10-24. Narrow supplement to the [service candidate](rustusb-phase3-link-health-service-design-20261024.md). Read-only review of actual Native/client/managed-engine ownership. No implementation or runtime test is claimed.

## Required ended signal

`LinkMonitor::ended()` should return an independent read-only watch receiver of a sticky `Option<LinkTermination>`. The driver sets the reason before closing application IO. A receiver first checks the current value, then awaits changes; subscribing after failure must still observe it. Sender disappearance without a terminal value means unconfirmed driver failure, never successful EOF. A task's actual `LinkOwner::join` supplies the definitive transport outcome and frozen counters. Holding this receiver cannot retain TLS/permit/USB capability ownership.

Failure must wake the actual owner even if no further `LinkIo::poll_read/write/flush` occurs. On failure those methods must also return the typed error immediately, rather than delivering already-buffered DATA to a new USB operation. Normal directional EOF is distinct: a foreground half-closed link may drain its reverse direction; the transport reviewer proposes a separate Draining observation and continued probes on that same connection. Local stop, first-EOF policy and transport timeout remain distinct terminal reasons.

The copied byte relay also needs an independent ended branch. Merely dropping a driver-side application IO endpoint is insufficient: `io::relay` may be awaiting a write to kernel R and never poll network EOF. Retain a relay task that selects its pure byte-copy future against link end/cooperative stop. On end, cancel **only that pure IO future**, drop its R endpoint and join the task. Do not abort helper preparation, commit exchange or USB blocking work. The owner retains and joins both this task and the link driver before classifying network teardown.

## Native incoming: concrete order

`jobs/native.rs::RelayStops` already wraps pending Resource/Prepared/Connected/sample recording with `record_or_stop`. Add a link-ended receiver there, available as soon as this data connection has a driver. A timeout is a `StageError::Operation(Timeout)`; do not recategorize it as successful operator stop or a persistence error.

When ended wins while a recording future waits:

1. Move the **same already-polled recording future** to `postponed_recording`, as current code does for stop. Do not recreate its transaction or return a false ACK.
2. Request link/relay stop and join their actual network tasks. Drop R even if a kernel write was blocked.
3. Call the original `stub.close().await`. Record cleanup failure before dropping `export.use_lock`, exactly as `cleanup_then_unlock` currently does. This allows an export's own real close to proceed independently of WAL.
4. Only then await the retained recording, followed by Closing/Finish. If that ACK remains stalled, the job/recording remains pending; actual stub cleanup may already have completed. Do not report terminal solely from the timeout watch.

`open_stub` and `stub.commit` remain accepted capability operations owned by their existing helper/control machinery. A link failure latches while they are in flight; once the actual result/handle is received, close that same attempt. Do not race an accepted request with a new request or call it NotStarted. Before beginning any subsequent mutation, recheck the sticky link-ended value along with existing stop/export checks.

## Client: sample-WAL wakeup without losing helper ownership

Add link end to the outer select surrounding `operations` in `jobs/client.rs:94–118`. That select covers the Connected/sample durable wait, so link failure can enter cleanup while the ACK is unresolved. The byte relay independently watches end as described above; then join relay and `LinkOwner` and preserve the timeout as the operation outcome.

The current `NativeImportHandle::commit` is a caller waiter over a private control actor. `native_channel.rs:235–291` owns the entire accepted Commit exchange even if its oneshot receiver is dropped; queued Close executes after that exchange, or control failure invalidates the handle and makes cleanup unconfirmed. Therefore adding the outer ended branch does not authorize aborting the private actor or a kernel attach. Preserve the handle and call actual `close().await`. The existing `tokio::join!(Closing-record, actual-close)` starts cleanup even if Closing recording stalls; Finish is still awaited afterward. Do not call a still-pending record a completed terminal.

This does **not** solve the earlier preparation Resource ACK await at `client.rs:85–86`, before the current interruptible operations scope. The acknowledged limitation remains: an indefinitely stalled preparation receipt can retain an owner. This slice must not claim bounded cleanup under every WAL stall merely because Connected/sample waits now have link wakeup.

## Rust managed: stage-by-stage finding

| Stage | Device ownership and required reaction to link end |
| --- | --- |
| TLS/prelude/catalog | No device lease. Stop/join the driver and discard only pure network parsing. Catalog health never substitutes for a data session. |
| Registry Preparing / `tracked.admission()` | No claim yet; five-second admission already bounded. Race admission with sticky link end and classify terminal NotStarted. The service's `begin_session` may be stalled, but the engine owns no new USB capability here. A late admit cannot restart the ended connection. |
| Immediately before Claiming | Check both application stop and link end. Preserve the atomic ACL/key/bus lookup. A normal race that crosses this boundary is an actual started attempt, not NotStarted. |
| `claim_managed` ready waiter | Add ended to the existing stop/claim/deadline select. Dropping that waiter requests the claim supervisor's cleanup. Retain and await **that attempt's** `completed` receiver; the soft ten-second notice cannot release the outer permit or create terminal proof. |
| Lease acquired / IMPORT response | Owner already holds `Owned(lease)`. Race the complete write/real-flush future against ended; on error close the actual lease. Never publish Connected from enqueued h2 DATA. |
| Forwarding | Add ended to the outer select around `imported_connection`, rather than relying solely on the internal reader noticing EOF. Drop the nested parser/actor futures, stop/join driver, then actual `begin_close`/`poll_cleanup`. |
| Closing | Link has ended; continue the unique hardware completion. A new timeout/stop does not abort cleanup. Record terminal only with the actual result. |
| Terminal but application ACK pending | Monitor retains its terminal event/slot. No USB lease is held merely to wait for that ACK; no new ping events consume lifecycle slots. Capacity exhaustion rejects later admission instead of dropping old evidence. |

The reason to add a direct Forwarding branch is concrete: `server.rs::imported_connection` has a capacity-two parsed-command queue. Its reader can be blocked in `sender.send`, while the actor is writing a reply or processing queued DATA. Closing the h2 connection alone is not a proof that this parser/actor has already stopped. A biased ended branch outside the actor prevents another queued command from being polled after failure is observed; an already synchronous lease-start call remains an actual attempt governed by normal cleanup.

The managed engine does not await the service's WAL after admission: `Tracked::phase/finish` mutate bounded monitor state under a short synchronous mutex; service `incoming` performs WAL waits in a different task. Thus no new manager ACK needs to be introduced into the engine's resource-close path. A stalled application consumer may delay its own final return, but must not delay backend cleanup. Monitor mutex poisoning remains an error and never licenses dropping the real owner.

## Targeted tests for this supplement

- Pending Native sample/Connected ACK, two real relay endpoints and a link timeout: prove relay R closes and actual stub close/unlock occurs before the retained ACK is released; the original ACK is subsequently completed once, then Closing/Finish.
- Fill the kernel-write direction so relay cannot read network EOF. End link while WAL sampling is also pending; require direct-ended cancellation of pure IO and joined task, not a passing test caused by an extra kernel read.
- Client sample ACK pending and helper Commit accepted: end link; the private helper actor finishes that exchange and observes the queued original Close. Cleanup failure remains Unconfirmed; no new helper attempt is created.
- Rust admission pending: ended then late admit produces no claim. Claim pending: ended produces exactly the attempt's completion witness. Forwarding: fill the parsed-command queue, terminate link, and ensure no additional queued URB starts after the ended branch wins; existing URBs receive actual cancellation/cleanup.
- Stalled final recording may keep job completion pending even after all actual resources close. Assert these two observations separately. No arbitrary preparation-ACK or kernel-hard-block claim follows.

## Transport reviewer clarification

The fixed h2 review reports that its user PING payload is library-fixed. Application `probe_sequence` is local bookkeeping, **not a fresh wire nonce**. The earlier candidate's wrong/replayed/unsolicited-reply test language must be interpreted against h2's actual one-outstanding-PING state and library validation, not as an invented per-probe nonce protocol. Record RTT from the actual chosen local enqueue point to the matched library pong result, including local queue/scheduling time. The single-connection DATA flush fence and directional END_STREAM fence remain transport implementation gates; this supplement does not independently verify those h2 source claims.

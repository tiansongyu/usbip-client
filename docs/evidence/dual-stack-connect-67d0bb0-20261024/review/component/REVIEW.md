# Dual-stack connector: fixed-component integration review

2026-10-24. Read-only source review against the fixed Tokio1.54.1 audit and
`docs/research/dual-stack-connect-design.md`. No repository edits, Cargo commands,
new tests, networking, QEMU or USB operations were performed in this review.
The earlier isolated library probes retain their separate scope.

**Result: no remaining product must-fix found in the selected source.** An
initial test-hang issue and a test-coverage wording issue were returned to the
author and corrected before the final stable capture. This is a component
integration audit, not product/runtime acceptance or an independent security
audit.

## Scope and captures

Nine requested source/build/design inputs were captured initially. Two caller
files were captured separately to verify the real operation deadlines and stop
boundaries. The author announced a redundant-closure style fix, then corrected
the tests/docs following review feedback. Initial captures and every observed
author diff remain separate. The final eleven files are captured before and
after this final review and are byte-identical. This does not claim the initial
capture stayed unchanged while the author was editing.

The author reports eight protocol tests and one raw/framed service test passing;
this reviewer did not execute or independently recount those runs. Their actual
results must come from the root's separate software evidence.

## Resolver ownership and bounds

- Numeric literals return a single full SocketAddr without acquiring DNS quota.
- Hostnames acquire one of four owned permits before `spawn_blocking` submission.
  The closure captures it even while queued, then keeps the named local binding
  through std lookup, iterator validation and completion. Async-waiter Drop only
  detaches the task. Actual completion/error/unwind drops the permit; a queued
  closure discarded without starting also drops its captures. No premature
  capacity return or extra resolver submission loop is present.
- The guard also covers errors from `ordered` and unwinding from lookup. This is
  source reasoning; the selected integration tests do not separately execute
  panic or queued-before-start cancellation. Started OS resolution remains
  noncancellable and can delay runtime shutdown.
- `ordered` reads at most65 entries, rejects raw count above64 and unique count
  above16, rejects empty results and stably deduplicates. It preserves complete
  SocketAddr values, including IPv6 flow/scope, and each family's order while
  alternating from the OS-preferred family. Comparisons and retained lists are
  bounded. The corrected docs explicitly exclude std/libc/OS internal allocation
  and lookup duration from these limits.

## TCP ownership, pacing and cancellation

- At most16 candidates can be created, and the pending collection contains only
  owned, boxed single-address futures. No dial task is spawned or detached.
- Every poll checks existing candidates first. Completed failures are removed
  and dropped; a winner is moved to the result. Explicit dropping of the whole
  pending collection happens before that result is returned. Dropping the outer
  future likewise destroys all candidates inline. A not-yet-polled candidate
  owns no connected socket; a polled Tokio candidate owns its actual stream.
- The loop either removes completed candidates, launches a finite remaining
  candidate, or returns Pending on candidate/timer wakers. Normal starts are
  250ms apart; failure can shorten the interval to100ms after the latest start.
  A late observed failure can make a launch immediately due, while the next
  failed launch still schedules its own100ms floor. No busy retry/self-wake loop
  was found.
- Fixed Tokio's `deadline_to_tick` rounds up to the end of a millisecond. The
  timer-ready branch therefore does not repeatedly fire from a rounded-down
  deadline while the exact Instant remains in the future. Once no addresses
  remain, the scheduler waits only on actual candidate futures; an old timer can
  produce at most a stale wake, without a polling loop.

## Deadline and TLS integration

Legacy DEVLIST retains one enclosing timeout. Secure discovery and client IMPORT
reuse their original absolute deadline through DNS/TCP/TLS/catalog/IMPORT and
retain the existing outer stop selects. The connector adds no fresh deadline
or premature per-address timeout. Public callers are told to supply the total
operation boundary.

The service obtains exactly one TCP winner before applying the original
independent `server_name` and TLS protocol/PKI/ALPN rules. Authentication error
returns directly, without another address/TLS attempt or insecure fallback.
The change does not touch device authorization, actual helper ownership,
server authentication admission or USB cleanup. Only the existing protocol
crate is reused; its added `test-util` feature is a dev dependency.

## Initial findings corrected by the author

1. The first hostname test timed out only its connect branch inside `join!`, so
   DNS/connect failure could leave accept waiting forever. The final test bounds
   the complete connect/accept/data exchange. The socket-pair helper and actual
   accept/EOF observations are also bounded.
2. The first auth-negative test bound both-family listeners but could not prove
   that the OS actually returned two localhost addresses. The final test states
   its exact scope: real hostname Catalog success with independent server.test
   identity, plus wrong-name rejection, in raw and framed modes. Explicit
   two-family installed evidence remains a separate QEMU task; local tests do
   not claim it.
3. The cancellation test now observes actual retained peer sockets returning
   WouldBlock before cancellation, followed by EOF after the owned race drops.
   Generic winner/loser Drop receipts remain separate from these real sockets.
4. Public/docs wording now explicitly excludes std as well as libc resolver
   allocation. The style-only redundant closure was also removed.

No additional runtime, performance, full RFC8305, hardware or complete G3 claim
follows from this source review.

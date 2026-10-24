# Independent managed catalog requests

2026-10-24; baseline `30fb57a7`. Status: implementing. This is a Phase 3
responsiveness fix for the existing authenticated application service. It does
not complete G3, add remote auto-sharing, or advance the TUI/GUI phases.

## Problem and decision

`Runtime::dispatch(Request::Discover)` currently awaits up to four seconds of
DNS/TCP/TLS/catalog I/O inside the single durable-state supervisor. During that
wait the supervisor cannot process other commands, job facts, or completed
owners. The independent stop controls still reach existing jobs, but recording
their completion and unrelated application replies are delayed by the remote
peer. A disconnected discovery caller does not remove this wait.

Move only the network portion into a separate supervisor-owned `JoinSet` with
at most two entries, including completed results not yet consumed. Requests
retain the existing kernel UID, exact profile and `Discover` authorization;
credentials and endpoint remain administrator-loaded. Capacity is checked after
authorization. Exhaustion returns the existing `Error { code: state }` response
without starting network work. An unhealthy journal refuses new discovery.
No wire or disk schema and no third-party dependency changes are required.

```text
authenticated local request -> supervisor authorizes -> bounded catalog task
                                   |                         |
                          other commands/facts         actual TLS close/join
                                   |                         |
                                   +<- completion + original reply sender
                                   |
                           successful audit commit -> catalog reply
```

Each task calls the existing `jobs::discover_until` with the original four-second
total deadline and service stop receiver. It owns its network operation until
the existing raw-v1/framed-v2 closing path actually returns. Dropping an RPC
waiter neither aborts the task nor returns its slot. Service shutdown requests
cooperative stop and continues joining every original discovery task, alongside
all USB job/recovery owners. Do not use `JoinSet::shutdown` or abort for these
tasks. A successful catalog is returned only after the existing `Discover`
audit commit. Failed or stopped reads do not create a successful discovery audit;
authorization denials retain their durable denial path. Network workers never
write the store or obtain helper/USB capabilities.

The supervisor can still wait on actual durable commits. This change does not
make WAL I/O cancellable, turn a catalog into a device availability guarantee,
or promise a hard shutdown deadline for libc DNS/filesystem/kernel work. The
existing actual resolver-worker quota remains in force after a waiter stops.

## Reuse and source check

The existing runtime already uses locked Tokio 1.54.1 `JoinSet`, `watch`, and
oneshot APIs in this ordinary pattern. Its installed `src/task/join_set.rs`
documents that Drop aborts entries and `shutdown` calls `abort_all`; `len` counts
tracked tasks until consumed. We will retain and drain, not drop, the set.
The fixed-version [official JoinSet page](https://docs.rs/tokio/1.54.1/tokio/task/struct.JoinSet.html)
was requested on 2026-10-24 but unavailable through the web tool; the installed
locked source is the evidence, not a fabricated successful page fetch.
The existing `jobs::discover_until` / `jobs::link` actual close and owner-join
contracts remain unchanged. No new component, technology choice, component
skill, or independent research delegation is needed under the design skill's
existing-stack exception and repository scope rules.

## Validation plan

1. Before the fix, reproduce a stalled TCP/TLS discovery delaying an unrelated
   supervisor request on a real loopback connection. Always release the peer and
   join the original supervisor before asserting, so a failed test leaks no task.
2. Test bounded acceptance, rejected unauthorized/profile requests, waiter loss,
   actual socket close on shutdown, successful authenticated catalogs/audit, and
   both raw-v1 and framed-v2. A retained task must not free capacity early.
3. Run the complete default/all-features software checks and actual existing
   process lifecycle regressions. Use the QEMU managed-service fixture where
   applicable, keeping old and new source/ELF and hardware scopes distinct.
4. Record actual commands, exit codes, failures and remaining gaps before
   ordinary commits and the already-authorized push.

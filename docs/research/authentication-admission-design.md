# Bounded TLS authentication admission

2026-10-24. Baseline `30481f05ceb7d7dc3c48a61afe2f50d021cae520`.
Status: implemented, targeted development tests passed; complete clean software
and installed validation remain pending at this checkpoint.

The existing sixteen connection permits bound simultaneous work, but a peer can
immediately replace a failed handshake. The security model explicitly leaves
retry rate control incomplete. Add a nonwaiting authentication rate gate to all
three secure server backends, preserving actual stream/device ownership and the
existing connection budget. This advances Phase 3; hardware G1/G2, full G3 and
the later TUI/GUI stages remain open.

## Decision and fixed-source research

Use `governor = { version = "=0.10.4", default-features = false, features = ["std"] }`.
The published MIT crate is dated 2025-12-16, registry checksum
`9efcab3c1958580ff1f25a2a41be1668f7603d849bb63af523b208a3cc1223b8`,
VCS `9f3a79dd47dd32acd589c562b8d4fefe99b93372` (`governor/`).
The manifest declares no MSRV; an isolated probe actually builds/runs with our
pinned Rust 1.97.1. Keep research in this report and its evidence, following the
repository's convention rather than generating unrelated component skills.

| Alternative | Evidence / consequence | Decision |
| --- | --- | --- |
| Existing semaphore alone | Failed attempts immediately return their permits | Retain it for connection ownership; add a temporal bound |
| Local token bucket | Small, but adds another refill/rounding/concurrency algorithm to audit | Prefer the maintained GCRA implementation |
| Governor direct nonwaiting check | Fixed quota, one in-memory state, no peer-key map, readiness future or refill task | Use the fixed std-only subset |

[Quota 0.10.4](https://docs.rs/governor/0.10.4/governor/struct.Quota.html)
defines `with_period(Duration) -> Option<Quota>` and
`allow_burst(NonZeroU32)`. The
[fixed direct source](https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/state/direct.rs)
shows `direct`/`direct_with_clock`/`check` using the clock, GCRA and AtomicU64 state.
The selected path starts no timer/thread. Std still compiles futures timer/util/
sink and parking_lot dependencies; quanta, keyed dashmap and jitter/rand features
are disabled. Actual project resolution and licenses must be recorded before
delivery, not inferred from this candidate graph.

The [clock](https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/clock.rs)
uses MonotonicClock without quanta. Production will explicitly select
`RateLimiter<NotKeyed, InMemoryState, MonotonicClock>` and `direct_with_clock`,
so later Cargo feature unification cannot silently select QuantaClock. The
[nanosecond representation](https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/nanos.rs)
has a u64 horizon of about 584 years and internal expect/add/multiply operations;
bounded quotas keep configured arithmetic far below it, but are not a claim of
unbounded service uptime or immunity to broken OS/VM clocks. Backward time is
conservative/saturating, not a reported clock fault. Do not format NotUntil,
construct future Instants, or use async readiness/jitter APIs. Independent
candidate probes passed exact 64-burst/125ms recovery, repeated noncharging
refusals, 100 concurrent clones with exactly 64 successes, and a clock moved
before the limiter start without minted capacity. These are library probes,
not RustUSB software, network, QEMU, USB or performance acceptance.

## Policy and insertion point

The immutable validated `AdmissionPolicy` has `burst` 1..256 and
`interval_millis` 1..60000. Default: 64 initial admissions and one replenished
cell per 125ms. Both root-trusted server config schema 1 and 2 accept the optional
strict object `authentication_limit`; omission selects the default. Zero, null,
out-of-range, duplicate/unknown fields and client-side overrides fail closed.
Old binaries reject the new optional key rather than silently ignoring it.
No RPC chooses or changes quotas. Memory-DER constructors use the same default;
new explicit raw/framed constructors accept a validated policy. Clones and job
restarts share the original gate; configuration reload starts a new one.

```json
"authentication_limit": { "burst": 64, "interval_millis": 125 }
```

Order in `TlsServer::accept_with_deadline`:

1. Nonwaiting acquisition of the existing sixteen-stream permit. A refusal
   retains the existing TLS `ConnectionLimit` attempt observation and does not
   consume authentication capacity.
2. Nonwaiting rate decision, before TLS verifier, plaintext I/O or device access.
   Refusal drops the input/temporary permit immediately. It never queues work
   or sleeps. Admission is consumed even if the peer subsequently fails,
   times out, cancels or closes; those events cannot refund capacity.
3. Existing TLS observation/handshake/ALPN/pin mapping and stream ownership.
   Framed setup remains separately observed after successful TLS.

The global per-configuration quota bounds aggregate new TLS work. It does not
promise per-source fairness or uninterrupted admission during an attack: one
peer can consume the shared quota. There is no unbounded address map or stored
peer address. Existing authenticated streams/URBs do not pass through the gate
again. This does not replace administrator network policy, the sixteen-task
listener budgets, packet bounds, deadlines, PKI or device ACLs, and does not
qualify arbitrary public exposure or complete denial-of-service resistance.

## Observations and compatibility

Keep the E38 `SecuritySnapshot` shape and equation unchanged. A rate refusal is
before its TLS stage; do not call it a failed certificate, TLS attempt or outer
connection-budget refusal. Add separate fixed-size `AdmissionSnapshot` metadata:
loaded-configuration scope, exact policy, admitted/limited decision totals,
saturation and consistency. The monitor owns no governor limiter, credentials,
certificate policy, stream, permit, helper handle or task. It may outlive the
server without retaining admission or network ownership.

Serialize the short check/counter update under a metadata mutex. No logging,
filesystem, user callback, async wait or owner cleanup is performed under it.
Poison rejects new handshakes with a fixed unavailable error; observations expose
inconsistent metadata. Overflow is visible and never wraps. No per-failure
synchronous trace or unsolicited stdout output is introduced.

Add separate `server_admission` foreground control/event and
`agent server-admission PROFILE` request/response. Actual kernel UID and exact
profile Read authorization precede sampling. Every authorized result, including
client-profile Unsupported, bypasses WAL; denied reads remain durable. Include
the real service instance ID and independent journal health. Existing request/
response variants, Session/Metrics, private disk formats and diagnostic bundle
schema 1 are unchanged. The new reads confer no endpoint, device or credential
override and do not prove audit persistence, revocation or USB cleanup.

## Validation before delivery

- Deterministic actual gate tests cover burst, exact refill boundary, no refund,
  many concurrent clones, policy bounds, saturation/poison and observer lifetime.
- Real TLS proves rate refusal before any transport I/O, returned permits,
  unchanged existing authenticated I/O, cancellation and eventual admission.
  Check both raw/framed entry points and strict trusted config loading.
- Actual Unix IPC verifies Read-only access, denied/unknown/client profiles and
  authorized reads during a held real Store commit; foreground controls expose
  real decisions without blocking existing owners. Preserve all old DTO tests.
- Run complete `scripts/check.sh`, Native-only feature isolation and relevant
  signal/backpressure checks; record the actual locked dependency graph/licenses,
  clean revision, selected source inputs and executable digests.
- A new clean paired-QEMU fixture must exhaust a configured gate while original
  HID/serial imports survive, save actual admissions/refusals, recover after
  replenishment and complete subsequent real I/O and original root proofs/full
  baseline restoration. Retain old failures and separate raw/framed results.
  Run applicable secure regressions; do not convert old 808/c097 results into
  validation of this new dependency or default behavior.

## Initial checks and dependency resolution

Actual targeted checks passed eight transport/config tests (four gate tests,
three real TLS transport tests and one trusted-config test), three extended
service tests, all-feature workspace compilation and all-target all-feature
Clippy. The service fixtures exercise real loopback foreground controls, actual
Unix peer UID, durable denials and independent reads while a real Store commit
waits. Existing TLS snapshots remain separate and actual admitted/refused totals
sum to all eighty failed fixture connections.

The first extended Store test FAILED because its old assertion still expected
zero TLS attempts after adding those eighty connections; it observed sixty-four.
The corrected test checks admitted-versus-TLS and admitted-plus-refused equations
instead. This is a dirty development fixture correction, not a clean installed
product failure or grounds to reinterpret older runs.

Project resolution adds fourteen locked packages and exports 174 package/license
metadata records without missing license metadata. The Governor subtree selects
only std and excludes quanta/dashmap/rand/getrandom; this exclusion does not apply
to TLS/ring's legitimate random source. A read-only fixed-component review found
no must-fix and verified nine captured source/build files remained unchanged;
its first helper applied that exclusion too broadly and corrected the helper.
Neither the component review nor library probes are product/USB acceptance.

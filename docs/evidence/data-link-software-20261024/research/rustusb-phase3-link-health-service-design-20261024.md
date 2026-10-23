# Same-data-link health: service integration candidate

2026-10-24. **Proposed, read-only source review**, starting at clean `031f63d`. This is the service/monitor/persistence/CLI part of requirements 4.4–4.5. The transport reviewer owns the bounded v2 transport implementation and fixed h2/Tokio/rustls source research; the harness reviewer owns actual QEMU fault injection. No repository edit, build, test, USB action, QEMU or commit was performed here. Source hashes are recorded beside this report.

## Recommendation

Use a transport-owned, independently driven `Link { io, owner, monitor }` for each authenticated USB data connection. The current IMPORT owner retains `LinkOwner`, actual USB/kernel owner and outer connection permit until their respective completion. `LinkMonitor` owns only current facts; it must not keep TLS, a USB lease, a server observer or a handshake permit alive.

The v2 choice is explicit administrator configuration and negotiated authenticated ALPN. Do not fall back to raw v1 when v2 is required. Raw v1 remains usable under its existing contract and reports **heartbeat/RTT unavailable**, rather than zero RTT, false healthy, or a ping from a separate catalog/control connection. Root is evaluating h2 flow control/PING; this report deliberately does not define custom wire framing.

A successful same-connection probe proves responsiveness of the remote transport endpoint over that TLS connection. It does **not** prove USB completion, kernel progress or that application DATA has escaped flow control. Keep application-progress ages separate. RTT is a local monotonic probe round trip, including local queue/scheduler and remote processing delay; it is not one-way latency or a throughput result.

## Existing source constraints

| Source | Actual behavior that affects this change |
| --- | --- |
| `service/jobs/native.rs:723–740` | The current Native `io::relay` future is polled by the same loop that awaits a periodic durable observation. A pending WAL ACK pauses that relay future. Adding a heartbeat tick to that loop would be incorrect. |
| `service/jobs/client.rs:91–126` | Client relay is a retained spawned task; outer owner stop/error joins network work before actual helper close. Foreground uses bidirectional half-close/drain; managed uses first EOF. |
| `service/jobs/rust_server.rs:115–218` | The managed consumer persists admission/lifecycle events. Stop requests reach the engine even while that same observer awaits recording, then the observer remains retained until real attempt completion. |
| `rust-backend/monitor.rs` | 16 active / 64 total records; only eight lifecycle events per session. A ping/pong must not consume those slots or advance lifecycle revisions. |
| `rust-backend/managed.rs:103–300` | Managed IMPORT acquires durable admission before claim, owns the real lease before reply, and publishes Connected only after complete IMPORT write/flush. Ten-second cleanup notice is soft; the unique claim/lease completion remains awaited. |
| `service/model.rs:90–151`, `runtime.rs:846–884` | `Metrics` is four fixed fields and is embedded in strict `Session`; periodic sampling currently goes through durable Observe. Counter/elapsed values must not move backwards. |
| `service/store.rs` | Disk schema 1 is strict. Admission is limited to 1 MiB within a 2 MiB file, reserving room for final evidence. Sessions ≤512, retained terminal ≤128, audit ≤1024. |
| `service/rpc.rs`, `ipc.rs` | Envelope 1, strict request/response/session types, profile/action authorization, 64 KiB transport frames and bounded pages. Blindly adding optional fields to existing Session does not preserve old readers. |

The path prefixes in this table are under `crates/rustusb-`. Current location details are also in the source manifest. Existing preparation-stage Resource ACK stalls remain a separate limitation; this design does not turn arbitrary WAL stalls or kernel hard blocks into bounded-cleanup claims.

## Concrete interfaces and facts

Keep the transport reviewer's concrete interface shape:

```text
Link { io: LinkIo, owner: LinkOwner, monitor: LinkMonitor }
LinkIo: AsyncRead + AsyncWrite + Unpin
LinkOwner::request_stop(&self)
LinkOwner::join(self) -> Future<Output = LinkOutcome>
LinkMonitor::snapshot(&self) -> LinkHealth
LinkMonitor::changes(&self) -> watch::Receiver<u64>
```

`LinkOwner::join` must return typed termination plus the frozen final sample; a monitor notification is not a substitute for that result. Constructor input is a verified TLS stream with its negotiated version, never a caller-supplied principal or an unverified raw TCP stream. Drop requests stop; normal paths explicitly retain and join. No public `into_inner` can separate the live TLS stream from its permit/driver.

A pure core DTO candidate follows. It uses no Tokio or wall-clock timestamps. Constructors/validators must enforce the invariants below; its public wire shape is bounded.

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkProtocol { RawV1, FramedV2 }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkProbeState {
    UnavailableRawV1,
    AwaitingFirstReply,
    ReplyObserved,
    ProbePending,
    TimedOut,
    Ended,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkEnd { LocalStop, PeerEof, Io, Protocol, ProbeTimeout }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinkHealth {
    pub protocol: LinkProtocol,
    pub sample_sequence: u64,
    pub elapsed_millis: u64,
    pub state: LinkProbeState,
    pub last_probe_sequence: Option<u64>,
    pub last_probe_sent_millis: Option<u64>,
    pub last_reply_sequence: Option<u64>,
    pub last_reply_received_millis: Option<u64>,
    pub last_round_trip_micros: Option<u64>,
    pub last_app_read_millis: Option<u64>,
    pub last_app_write_millis: Option<u64>,
    pub end: Option<LinkEnd>,
}
```

This is a type-level API draft, not compiled implementation evidence. The name `FramedV2` avoids prescribing the eventual frame engine. `Export` sessions have no single data link: the read API returns an explicit `not_a_data_session` availability value instead of inventing a health sample for their listener. An incoming/client session has one non-reused local link generation, qualified by its existing instance/session IDs.

Validation: raw v1 has no probe/reply/RTT values; reply sequence cannot exceed a sent sequence; elapsed/sample sequence and probe identities do not decrease or wrap. Local monotonic sent/received values belong to the same driver generation and lie within the sample interval. RTT may increase **or decrease** and must not use the existing monotonic-byte comparison. Unknown/malformed/unsolicited reply cannot refresh liveness. Timeout is sticky for that link; a delayed pong cannot reopen it. Final samples freeze. A later attempt creates a new session/link generation rather than resetting counters on an old Connected record.

## Driver, owner and cancellation integration

1. Start the v2 driver at the transport-approved boundary on the actual IMPORT data TLS connection. The peer identity/device authorization and atomic stable-key/Bus ID claim remain unchanged. Catalog connections do not create device health sessions or substitute for a data-link probe.
2. The driver reads/writes/times out independently of the service actor, output renderer and durable sampling. All queues and per-frame/stream buffers are bounded by the transport design. A full application receiver must not prevent the same TLS connection's control processing indefinitely; DATA backpressure is reported separately from probe responsiveness.
3. Native: make the raw USB-to-`LinkIo` relay an independently retained task for v2, so a WAL sample wait cannot prevent it from reacting to link closure. Add link termination to the existing `RelayStops` path used by `record_or_stop`: actual failure retains the original recording future, closes the actual stub and unlocks first, then awaits the original recording and Closing/Finish. Do not leave a dead link waiting solely for the next sample or WAL ACK.
4. Client: own link driver and relay tasks explicitly. Watch driver termination alongside job/session/helper stop. Request stop, stop/join the byte relay, and join the driver before returning from network teardown; then receive the original helper's real close result. Preserve operation and recording errors independently. Do not make a monitor clone an owner or classify an aborted watcher as successful cleanup.
5. Rust engine: preserve the unique claim-completion witness. Driver timeout while claim/initialization is pending requests this attempt's close and remains associated with it; it does not turn cancellation into NotStarted. After a real lease exists, driver end enters the existing begin_close/poll_cleanup path. The outer permit remains held through actual cleanup. Observer/WAL failure cannot prevent the engine from closing its own lease.
6. Managed mode still requires durable admission before mutation; framing never supplies a fake admission. Foreground still uses untracked helper APIs and its existing stdout/control adapter. Neither mode gains automatic storage reconnect or old-epoch adoption.

**Blocking integration question:** current server Connected means IMPORT reply complete write **and flush**. `h2::SendStream::send_data` acceptance or a bounded queue write is not that fence. A PING ACK that may pass DATA is also not such a fence. Root has assigned the transport reviewer a `FlushTrackingIo` candidate between the h2 encoder and TLS: parse only local frame headers/length/stream identity, count actual TLS `poll_write`-accepted DATA payload offsets, and publish an offset only after TLS `poll_flush` returns Ready(Ok). `LinkIo::flush` must wait for its previously accepted application-data target offset. This is a proposed mechanism, not a verified fact in this report; one connection/stream 1, no push/padding, partial headers/writes, overflow and final cleanup all require fixed-source and runtime checks. PING cannot advance the DATA fence. Keep the existing ImportReplyFlushed evidence; do not substitute an application ACK or weaker evidence to bypass it. Client Connected still requires actual VHCI enumeration.

EOF must also be finalized by transport: current foreground client half-close/drain and managed first-EOF are not interchangeable. Raw-v1 paths remain byte-for-byte policy compatible. V2 must explicitly define directional EOF/close, late reverse DATA, heartbeat after one half closes, and the final driver's stop deadline. Do not treat a local stop as timeout merely because it prevents a pending pong; do not erase an already-observed timeout with a later operator stop.

## Live observation and minimum persistence/IPC changes

Use a bounded **nonowning live observation registry**, keyed by existing instance/session plus local link generation. The active owner registers its `LinkMonitor` once. Registry queries read the latest sample directly; they do not await an Observe WAL transaction. Native/client can use the direct monitor. Rust can expose a weak `ServerControl::session_snapshot(id)` / health read so the registry does not hold the owning `ServerMonitor` observer alive. Do not add per-ping events to the existing eight-event lifecycle queue.

`SessionHealth` responses distinguish `live`, `historical_owner_ended`, `historical_previous_epoch`, `observer_unavailable`, and `not_a_data_session`. Live sample age is computed against the same process monotonic source; persisted elapsed values are never compared to a new process Instant. A persisted ReplyObserved is historical after restart, even if the old journal says Connected. When an owner stops or its driver sender ends, a registry entry is frozen/removed; the service must not keep returning a stale healthy sample. Last RTT can remain visible **as historical** with its age/availability, not as current health.

Minimum compatible disk proposal: strict private **schema 2** adds a bounded `link_history` collection keyed one-to-one to retained session/instance IDs; keep the existing public `Session` and `Metrics` serialization unchanged. Each record holds requested/negotiated mode (separate until actual TLS result), last checkpoint/final LinkHealth and terminal reason. Decode old schema 1 with a frozen strict DTO, then explicitly migrate old client/incoming records to raw-v1 unavailable and exports to not-applicable. No missing-field default is allowed to fabricate v2 provenance. Schema 2 requires its fields and rejects unknown fields/versions; upgrade only via the existing durable atomic commit and never downgrade. Retain existing session/attempt/root-proof identities unchanged.

Validate uniqueness, exact instance/session association and role; remove link_history only with its associated bounded session pruning. Bound the collection by the session limit. Keep health fields fixed-size/no free text or probe history. Recalculate worst-case post-admission terminal growth and 60 KiB response pagination before retaining the existing 1 MiB reservation claim. Do not raise limits merely to avoid the calculation.

Do not fsync on every ping/pong. Live samples are explicitly volatile; checkpoint opportunistically with existing durable lifecycle/periodic transactions, and persist the final snapshot after actual cleanup. Audit only meaningful state/failure transitions, not every successful heartbeat. A failed final write remains a recording error; cleanup proceeds first. A stalled audit transaction may still delay journal/UI responses, but cannot pause the new driver or prevent actual owner stop.

Minimum compatible IPC proposal: **keep envelope 1 and existing Sessions/Events bodies unchanged**. Add one explicit read-only operation `Request::SessionHealth { profile, after: Option<SessionId> }` and matching response containing at most sixteen observations/60 KiB. Apply existing `Action::Read` per profile before lookup. Old clients never request the new variant and remain compatible. Old agents reject the unknown operation explicitly; the new CLI must report capability unavailable, not retry as a healthy v1 session. This follows the repository's existing additive Request pattern and avoids duplicating every IPC type just to add health. If all Sessions payloads are instead changed, a real IPC2 plus explicit v1 projection is required.

Expose this through `rustusb agent health <profile> [--after <session-id>]`, using the same library API for future TUI/GUI. It reports data-link responsiveness/RTT, sample freshness, application byte counters/progress ages and original role-specific state separately. It never derives Connected from a pong. Existing foreground schema-1 ready/connected/stopped output is unchanged by default; v2 failure uses an explicit bounded timeout category. Optional foreground health events, if added later, need their own negotiated/output-version contract and cannot become a dependency of cleanup.

## Tests required before an implementation claim

- Real same-connection protocol exchange plus deterministic timer tests: valid reply/RTT, missing reply, wrong/replayed/unsolicited reply, full DATA window, stalled app consumer, writer backpressure, partial frame, control flood and deadline. Successful DATA alone does not satisfy an outstanding probe unless the approved protocol defines that exact rule.
- Stall service sample recording while a real link is active: independently observe control processing, link timeout, actual relay/driver join and helper/lease cleanup starting before that ACK is released. Retain/finish the original ACK after resource close; a final pending WAL receipt is not a completed job.
- Stop during pending probe, full buffers, write/flush, helper prepare/commit and Rust claim; check unique attempt completion, stable failure precedence, no leaked permits/tasks and no close of another device. The existing kernel-hard-block/initializer gate remains a separate actual test obligation.
- DTO/store: strict real schema1 fixture migration, v2 missing/unknown fields, malformed relationship, pruning, terminal capacity, stale old epoch, nonmonotonic sequence, decreasing valid RTT, frozen terminal sample, old Sessions serialization unchanged, unauthorized health lookup and unsupported old agent.
- V1 regression: unchanged bytes/ALPN/EOF behavior and explicit unavailable health, no silent v2 fallback. V2 test both half-close/managed policies selected by the approved transport contract.
- Actual QEMU: map the exact live data connection tuple/attempt before injecting a data-only bidirectional and asymmetric blackhole while an independent catalog/control connection still responds. Prove timeout belongs to that link, actual cleanup/root proof or conservative Unconfirmed, unaffected sibling device, and a new explicit attempt with fresh real USB I/O. Also obtain a positive same-data-link RTT sample during real I/O over both address families. Parent/H determine the minimal backend matrix without relabelling software predicates as USB cases.

No source observation or in-memory probe test proves real hardware, arbitrary WAN timing, USB progress under a responsive h2 endpoint, or full Phase 3 completion.

## Owner-wakeup supplement

The [stage-by-stage owner-wakeup supplement](rustusb-phase3-link-health-owner-wakeup-addon-20261024.md) refines the ended signal, stalled-WAL cleanup ordering, kernel-write relay cancellation, managed admission/claim/queued-command boundaries and the h2 reviewer's local-sequence clarification. It takes precedence over the initial candidate where more specific.

# Runtime log rotation and reading: proposed implementation contract

2026-10-24. Read-only research, not an implementation or acceptance result. Prior software/QEMU/CI results remain attached to their original versions. The original requirement is `docs/requirements.md:392–428` (4.11): JSON/human logs, levels, rotation, redaction, export, and lifecycle/error observations. This slice should ship real process logs and authorized reading; diagnostic export can follow separately, rather than substituting another collector for logging.

## Recommendation

Keep mature `tracing 0.1.44` / `tracing-subscriber 0.3.23` for collection/filtering and console integration. Add one small, implemented `rustusb-logging` crate for the concrete shared CLI/helper sink, safe rotating storage, typed projection and bounded reader. It depends only on existing workspace primitives (`rustusb-core`, tracing/subscriber, serde/json, rustix, Tokio, std); it must have actual functionality and tests in the same slice, not an empty observability framework. CLI, helper and service can depend on it without the forbidden daemon→service→daemon cycle. Do not put filesystem I/O into core or overload transport with unrelated log storage.

No new appender dependency is required for the recommended ownership/error contract. I evaluated two mature appenders against it, rather than assuming their names provide these guarantees:

- `tracing-appender 0.2.5`, MIT, exact commit `53e14901f484a455d6edd59474ee87fd2f5f6e54`. `non_blocking.rs:67,238–255,282–300` uses a default 128,000-item queue, copies supplied buffers, makes flush a no-op, and has a lossy mode; its drop path waits on two shutdown sends without joining its retained thread handle. The timeout branch can print to stdout. `worker.rs:30–103` does not propagate ordinary writer errors to the application. `rolling.rs:655–746,786–806` has time/count rotation and ordinary path opens, plus internal stderr failure messages. These are reasons not to use its bare guard/rolling writer as the RustUSB persistent, checked, stdout-safe owner.
- `file-rotate 0.8.0`, MIT, official crate checksum `6e8e2fa049328a1f3295991407a88585805d126dfaadf74b9fe8c194c730aafc`, VCS `8ee371fd0e50d281e87d8a755aaeaa2c824f86b1`. It supplies byte/count rotation and caller OpenOptions, but `src/lib.rs:478–485,706–728` can retain no open file and still return successful writes/flush. `Bytes` can split records and `BytesSurpassed` can overshoot a buffer. It does not by itself meet explicit error visibility or shutdown ownership.

These are fixed-source compatibility findings, not newly reproduced RustUSB runtime failures, library security advisories, or a claim that no other mature appender could work. Fixed sources and hashes are in the sibling provenance files. Revisit only if an appender exposes the required owned shutdown, secure-file hook, finite-byte bound and failure result; do not silently relax those requirements to add a package.

## What already exists, and the real gaps

| Source | Existing fact | Missing behavior |
| --- | --- | --- |
| `cli/src/main.rs:45–56,539–559,592–597` | stderr human/JSON `tracing`, `RUST_LOG`, final error logging | No persistent path/size/count/age control or logs command; synchronous stderr remains an inherited backpressure boundary. Raw `%error` is not a persisted-redaction contract. |
| `daemon/src/main.rs:25–43` | Helper stderr subscriber | No equivalent level/JSON/rotation configuration or shared sink lifecycle. |
| `service/src/store.rs:7–13,123–157` | Atomic state, 2 MiB bound, 1,024 retained typed audit events | A pruned audit ring is not process logging or long-term audit archival. |
| `service/src/runtime.rs:577–621`, `rpc.rs:62–73,144–157` | UID/profile/action-authorized 16-event pages and explicit gap | Reusable authorization pattern; not a raw log-file API. |
| `service/src/model.rs:42–77,218–240` | Static Failure and payload-free AuditEvent | Preserve and reuse categories/correlation; do not add arbitrary error text. |
| `native/src/diagnostics.rs:20–27,65–127`; `daemon/src/diagnostics.rs:33–93` | Bounded, read-only doctor DTOs, safe tool versions and root-peer checks | No portable sanitized diagnostic export. |
| `core/src/lib.rs:208–241` | Full Device explicitly includes sensitive `serial`, descriptor strings and topology | Never serialize Device, full state/root journals or stdout as a diagnostic/log shortcut. |
| `transport/src/quiet.rs`; `transport/src/link/quiet_tests.rs` | Existing h2 poll/call/drop silence and negative sentinel coverage | Preserve it; an environment target filter is not a replacement. |

Current source inspection does not establish a new specific serial/key leak. It establishes that arbitrary Display errors and a generic tracing format are insufficient guarantees for a new persistent/exported channel. Existing sanitized helper errors and fixed-input protocol parsing are valuable and should remain.

## Concrete user behavior

1. Agent root-managed manifest opts into a fixed log directory and validated limits. Keep old schema-1/no-file behavior supported; use an explicit new manifest version/strict struct for the enabled logging shape instead of silently changing old schemas or allowing an RPC override. The final schema number is for root's implementation design to settle.
2. Ordinary operators read, without becoming the broker UID:
   ```sh
   rustusb logs --agent-config /etc/rustusb/agent.json --profile lab-hid
   rustusb logs --agent-config /etc/rustusb/agent.json --profile lab-hid --after CURSOR --json
   rustusb logs --agent-config /etc/rustusb/agent.json --profile lab-hid --follow
   ```
   A same-operation `agent logs` alias is optional; do not implement two separate readers. Each request uses SO_PEERCRED and existing profile Audit authorization (or an explicit new Logs action). Root can read; read permission never implies Start/Stop. No RPC accepts a filesystem path, endpoint, grep expression, shell command or credentials.
3. Root/helper logs are a separate stream: `rustusb logs --helper` reads the fixed root-only helper store locally, root only. Do not expose the whole root log through a generic broker helper-file RPC. One-off/foreground CLI may opt into its own caller-owned checked directory; it must not override an agent's root-defined destination. Legacy stderr and stdout event formats stay compatible.
4. `--follow` is bounded polling/notification of sanitized pages, not `tail -f` or a shell. It reports retention gaps, dropped records and logger failures. Ctrl-C stops this observer only; it never disconnects USB jobs. stdout uses the existing bounded EventOutput semantics, separate from process logging.
5. Do not label this complete 4.11 until event coverage and redaction tests pass. Diagnostic export is a subsequent command/API slice. A future single bounded JSON bundle is sufficient; ZIP/tar and arbitrary filesystem collection are unnecessary.

## Storage, permissions and finite bounds

Suggested defaults, validated hard limits rather than an unbounded config framework:

- Agent `/var/log/rustusb/agent-<broker_uid>`: root-owned non-writable ancestors, broker-owned leaf exactly0700, files/lock0600. Helper `/var/log/rustusb/helper`: root0700, root0600 files. Administrator provisions service leaves; logger never chowns directories or changes device policy. A separate caller-owned leaf is allowed only for explicit ordinary foreground logging, with its own checked trust rule.
- One nonblocking exclusive `flock` on a fixed lock for each store, held by the writer owner. Never delete another process's lock or replace a live owner. Use descriptor-relative safe rustix opens: DIRECTORY/NOFOLLOW/CLOEXEC on directories, regular/nlink1/expectedUID/mode checks for files; CREATE|EXCL0600 for new files, NONBLOCK so FIFO substitution cannot hang startup, and verify pinned directory/file identities before rotation and reads. No process-wide umask change.
- NDJSON always on disk, each complete record ≤2 KiB before enqueue, 256 queued records (≤512 KiB payload plus finite metadata), eight total segments including active, each≤4 MiB (≤32 MiB payload/store). Check counters and serialized size. Rate-limit identical debug/auth-failure events with a typed suppressed-count summary. Audit WAL events are not dropped or rate-limited by this sink.
- Rotate **before** a complete next record exceeds the segment size; never split JSON/UTF-8. Strict finite filenames and segment header UUID/cursor, no latest symlink, compression, arbitrary directory glob deletion or copytruncate. Bound directory scan, reject unexpected types/identity replacement, and only unlink validated logger-owned retained segments.
- Size/count cap is the primary disk guarantee. Optional seven-day retention is evaluated on startup/rotation/read, not a promise of deletion while the service is stopped; backward clock changes cannot bypass byte/count caps. Do not claim secure erasure or an immutable audit trail.
- On restart, acquire the same exclusive lock and validate the bounded existing segments before creating a fresh epoch/segment. A strict generated segment ordinal plus UUID identifies retention order without relying on wall-clock order; overflow or ambiguous/replaced files fail closed. Preserve an old incomplete tail as an explicit gap and never append a new JSON event onto it, parse it as a successful event, or rewrite it as a fabricated successful shutdown. A reader returns complete prior lines and a gap marker; a newly initialized writer may be healthy while the old-epoch gap remains visible. Do not automatically reopen/restart after a runtime I/O error: that process remains unhealthy. Runtime logging is not the application admission WAL and does not provide cleanup proof.

## Typed records, tracing and the authoritative audit

```rust
// Proposed interfaces, not existing API.
struct LogRecordV1 { schema_version: u32, epoch: LogEpoch, sequence: u64,
    elapsed_millis: u64, wall_unix_millis: Option<u64>, level: Level,
    scope: Scope, code: EventCode, job: Option<JobId>, session: Option<SessionId>,
    attempt: Option<AttemptId>, result: Option<Failure>, errno: Option<i32> }
struct LogStatus { healthy: bool, queued: u32, dropped: u64,
    last_written: Option<Cursor>, failure: Option<LogFailure> }
struct LogPage { records: Vec<LogRecordV1>, next_after: Option<Cursor>,
    oldest_available: Option<Cursor>, gap: bool, status: LogStatus }
struct ReadLogs { profile: ProfileId, after: Option<Cursor>, limit: u8 }
// Receiver authorizes OS UID + profile before evaluating the opaque cursor.
// Cursor identifies a generated segment UUID + complete-record offset, never a path.
struct LogStoreOwner { /* actual writer thread/task, private FDs and queue */ }
impl LogStoreOwner { async fn shutdown(self) -> Result<LogShutdown>; }
```

Use fixed event codes/messages and safe field types, not a regex over an already formatted `%error`, raw Request, byte buffer or Debug struct. Prefer explicit typed producer events; a tracing adapter only persists approved RustUSB targets/fields and discards arbitrary debug/message values. Existing subscriber filtering remains useful for levels, but enabling `RUST_LOG=h2=trace` or another library target must not bypass persistent redaction. Render human/JSON **read output** from the same typed record.

Log discovery counts, authorization decisions, preparation/import/export outcomes, real lifecycle transitions, network errors, actual cleanup results and end. Join identifiers may be retained privately under authorization; raw serial/manufacturer strings, USB bytes/HID events, content hashes derived from USB payload, keys, PEM/DER, pins, tokens, raw subprocess stderr, config contents, arbitrary paths/addresses and full authentication requests are excluded from persistent records. Do not hash low-entropy secrets as alleged anonymization.

The application AuditEvent ring remains the durable authority. After a successful Store commit, optionally mirror those exact typed events with original audit sequence/epoch into the runtime stream. A mirror can have gaps; it must never acknowledge admission or change journal_healthy. Reuse Failure categories and IDs. LogError/queue loss has its own status, not a fabricated USB failure, cleanup success, or WAL receipt. Diagnostic export later aliases allowed profile/device/principal/user/session identifiers consistently per bundle and whitelists doctor/status summaries; it must not copy the raw state/config/journal files.

## Failure, concurrency and shutdown

- Configure/open the sink in an owned blocking operation before any device acquisition. Required logging startup failure refuses new startup with a fixed secret-free category. No fallback to a more permissive directory. Existing schema1 stderr-only mode is explicit, not fallback after a configured sink failure.
- Emitting logs never waits on filesystem, writer queue capacity, the audit actor, stdout or a mutex held across device awaits. Queue full increments a visible saturating dropped counter and sequence gap. A writer's real I/O/rename/fsync/identity error latches unhealthy and is queryable via a nonowning monitor. Do not recursively log that error into the failed sink or print payload/error Debug to stdout.
- Keep file and stderr destinations independent. In file-enabled long-running mode do not synchronously tee to stderr from USB tasks. Either select the asynchronous file sink only by default, or give the optional console its separate finite best-effort path. Retaining legacy stderr-only mode does not establish bounded exit against an arbitrary full stderr pipe.
- The writer owns no USB FD, helper handle or mutation lock. Stop closes listener/USB owners and completes their actual cleanup/required WAL work first, then closes log admission, drains the logger and joins its actual owner. The global subscriber holds a weak sender so it cannot keep the worker alive forever. Partial final write is not durable success; flush/sync result and actual join must be distinguishable.
- A finite observation deadline may return a pending/error status, but cannot cancel a blocking filesystem syscall or claim its worker is joined. Keep the owner retained; permanent FS/kernel blocking can still prevent process exit, as in the existing contract. A SIGKILL may lose queued records; no final flush is promised.
- Operational log I/O failure need not stop active USB sessions or mark the admission WAL unhealthy. It must produce explicit logger status and a nonzero final logger result after actual device cleanup. A stricter refuse-new-admission logging policy, if desired, must be an explicit admin policy, not accidental error propagation from tracing.
- Log reads run outside the Runtime persistence path, with separate finite concurrency (e.g.2), a max256 KiB scanned/page and at most16 records/32 KiB output under the existing64 KiB IPC limit. Filter by OS UID/profile before returning. Cursor is a bounded opaque segment UUID+offset, never a path or an authorization token; rotation gives gap/expired rather than reading a replaced inode. A stalled log read cannot suppress fast sticky Stop controls.

## Necessary acceptance, without new USB hardware

1. Real temp-FS tests for size/count/age, full records, restart/torn tail/gap, simultaneous second-writer refusal, wrongUID/mode, symlink/hardlink/FIFO, replaced directory/file and malicious filenames; inject real writer errors and prove unhealthy is observable.
2. Actual tracing/typed producer and logs-reader tests with secret sentinels in Error strings, Device.serial/descriptor text, malformed JSON, config paths, certificate/key bytes and native stderr. Scan persistent files, stderr, machine stdout and any later export. Keep the existing quiet-h2 sentinel regression. No assertions that merely serialize a duplicate implementation.
3. Hold the real writer syscall/software gate, fill its256-record queue, then request actual production stop and verify the owned USB/network fixture cleanup happens while log writes remain blocked. Release that same writer and join it. Do not manufacture a durable ACK or claim a permanently blocked filesystem is cancellable.
4. Ordinary subprocess tests of JSON versus human logs, no ANSI/control injection, zero logging bytes on machine stdout, read/follow Ctrl-C, output-backpressure deadline, log initialization/write/shutdown error exits after actual owner cleanup, and SIGKILL's explicit tail-loss limitation.
5. Actual service IPC UID/profile denial leaves jobs/resources unchanged; a limited user cannot read another profile or root/helper logs, forge scope using a cursor, change level/path/retention, or turn log polling into device work. Reuse current staged/root-path fixture patterns; only disposable QEMU proves real installed root/nonroot directory behavior.
6. Add a focused installed QEMU log test only for privilege/path/real lifecycle integration if needed. Do not substitute another large old USB matrix or a sanitized collector for the required real process logging/read/rotate behavior. Existing software, exact-source and meaningful signal/backpressure gates remain applicable.

This is a proposed engineering contract for root to finalize. No repository file, Cargo manifest, dependency lock, skill, program, USB device, network configuration or QEMU instance was changed or run by this research.

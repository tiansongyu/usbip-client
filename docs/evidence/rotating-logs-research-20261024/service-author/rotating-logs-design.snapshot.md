# Bounded process logs and authorized reading

2026-10-24. Baseline `bd6e08be8b304767452dc98b6606e2e65448c2da`. Status: proposed implementation contract. The preceding goal turn made verified progress on preparation-stop ownership and publication. The full Phase 0–7 objective remains active. This slice implements the missing process-log rotation and reading in requirement 4.11; diagnostic export, complete security qualification and real-device G1/G2 remain outstanding.

## Decision and existing behavior

Keep the locked `tracing 0.1.44`, `tracing-subscriber 0.3.23`, Tokio 1.54.1, rustix 1.1.5, serde and serde_json. Add a nonempty shared `rustusb-logging` crate used by the CLI, helper and application service. Core stays free of filesystem writers. No new external dependency or cryptographic construction is needed.

Current CLI logging is synchronous stderr, with human/JSON format and `RUST_LOG`; the helper has its own stderr setup. The application `state.json` transaction contains a 1,024-event audit ring. That ring is the authoritative admission/recovery journal, not a rotating process log. Keep its existing disk2, IPC1, acknowledgements, capacity and root-proof rules.

Use the technical-design skill's fixed-source research method, with the repository's existing research directory. Per user instructions, this does not generate unrelated component skills or reorganize the project. The logging research is preserved separately from runtime acceptance.

| Alternative | Fixed evidence | Decision |
| --- | --- | --- |
| tracing-appender 0.2.5 | [nonblocking owner](https://github.com/tokio-rs/tracing/blob/53e14901f484a455d6edd59474ee87fd2f5f6e54/tracing-appender/src/non_blocking.rs), [worker](https://github.com/tokio-rs/tracing/blob/53e14901f484a455d6edd59474ee87fd2f5f6e54/tracing-appender/src/worker.rs), [rotation](https://github.com/tokio-rs/tracing/blob/53e14901f484a455d6edd59474ee87fd2f5f6e54/tracing-appender/src/rolling.rs) | Bare guard does not join its worker; timeout can print to stdout; ordinary writer failures are not returned to the owner. Time/count rotation lacks the required secure-open hooks and byte bound. |
| file-rotate 0.8.0 | Official crate checksum `6e8e2fa049328a1f3295991407a88585805d126dfaadf74b9fe8c194c730aafc`, VCS `8ee371fd0e50d281e87d8a755aaeaa2c824f86b1` | Some failed-open paths acknowledge writes to no file; byte policies do not alone give complete-record, explicit-failure ownership. |
| Checked local sink behind mature tracing | Existing descriptor-relative filesystem conventions and the bounded interfaces below | Implement the missing storage/owner contract, without importing an appender whose semantics would have to be hidden or weakened. |

These are compatibility findings for these exact sources, not reproduced RustUSB bugs or security advisories about those libraries.

## User behavior and configuration

- Default stderr behavior and all command/stdout protocols remain compatible when file logging is absent.
- The CLI and helper accept explicit startup `--log-directory`, optional `--log-max-bytes` and `--log-retain` limits. The helper also gains `--log-json` and the same level handling. The directory already exists and is private. File startup failure happens before device acquisition and does not silently fall back to another path.
- Startup flags are supplied by the process owner/administrator, just like the helper's existing journal directories. They cannot override an agent profile, endpoint, TLS identity or device pool. No local RPC accepts a log path or logging configuration. A broker UID remains the existing whole-pool trust domain. No manifest/schema change is needed merely to choose an operational log destination.
- In file mode, process log events go only to the finite asynchronous NDJSON sink: there is no synchronous or asynchronous stderr tee. `--log-json` selects the legacy stderr format and conflicts with `--log-directory`; file records always use the strict new JSON schema. Human/JSON rendering is available through the read-only logs command. Final fixed error diagnostics can still use stderr after actual cleanup and logger join; an unread stderr pipe can delay that final diagnostic, not active file progress. Legacy stderr-only behavior is retained explicitly.
- `rustusb logs --directory DIR [--after CURSOR] [--limit 16] [--follow] [--json]` reads a caller-owned log store, or an administrator's local store when root. It never invokes a helper device operation.
- `rustusb logs --agent-config FILE --profile ID ...` uses the authenticated agent socket. Every page requires existing `Action::Audit` for that exact profile; Read alone does not grant Logs. It returns that profile's projected audit events, plus only the same caller's unscoped denials (root may read unscoped administrative observations). Cursor possession never grants access. Root/helper logs are not exposed through the broker RPC.
- Directory mode and agent mode are mutually exclusive. A logs observer cannot enable a writer in the store it reads. Ctrl-C/SIGTERM stops the observer, not USB jobs. Follow uses bounded pages and reports gaps/status, not a shell or raw tail operation.

## Records and redaction

Diagnostic records accept only fixed event/failure codes, static RustUSB target/line metadata and approved numeric/boolean observations. String-bearing generic messages/fields are excluded. Typed AuditProjection is a separate closed DTO, not a tracing field whose arbitrary Debug is parsed. Profile reads match only this audit variant, never a generic record carrying a profile-looking field. Records have schema version, process epoch UUID, checked sequence, monotonic elapsed milliseconds, optional wall-clock milliseconds, fixed level/event code, optional profile/actor/job/session/attempt identifiers, fixed failure category/errno and a closed set of bounded scalar observations. A generated segment UUID and complete-line offset form the opaque cursor; they never name a file supplied by a caller.

Only approved RustUSB targets and typed fields are persisted. Arbitrary tracing messages, Display/Debug errors, subprocess stderr, Device structs, serial/manufacturer/descriptor strings, buffers, paths, network addresses, pins, PEM/DER, keys and config contents are omitted rather than regex-scrubbed after formatting. The adapter must not even format an unapproved Debug/Error value. Unknown fields are counted, not copied. Enabling another `RUST_LOG` target does not bypass this projection; the existing h2 quiet call/poll/drop boundary remains intact.

Human messages are fixed templates keyed by event code. Meaningful CLI/helper failures supply a fixed `FailureCategory` and optional OS errno, never an arbitrary persisted error string. Existing detailed legacy error output remains separate. Generic audited RustUSB diagnostics can retain only approved scalar fields and their static source/target identifier.

After a successful application Store commit and `self.state = next`, mirror newly committed typed AuditEvents with their original audit sequence and application epoch before the separately fallible health refresh. Compare against the prior sequence; do not replay the startup ring. Never emit an uncommitted audit event as committed. This best-effort mirror is not the WAL, cannot acknowledge admission and cannot mark cleanup verified or journal unhealthy. It may have gaps. A runtime-log failure is distinct from a USB operation or persistence failure.

## Storage and resource bounds

- Suggested defaults: 2 KiB per complete record, queue 256, 4 MiB per segment and eight segments including active. Hard ceilings: segment 8 MiB, 16 segments, at most 128 MiB of segment payload per directory. Segment minimum is large enough for a header and a complete maximum record. Queue capacity is fixed and finite, not an unbounded administrator option.
- The leaf directory is exactly0700 and owned by the writer UID. Ancestors are root/current-owner protected and not group/other writable. Files/locks are exactly0600, regular and singly linked. No global umask change. Opens are safe descriptor-relative NOFOLLOW/CLOEXEC/NONBLOCK; new files use CREATE/EXCL. Pin and recheck directory/file identity. Other-user, symlink, hardlink, FIFO, replacement and ambiguous files fail closed.
- Use a dedicated directory and finite fixed names: writer/access locks and numbered segment slots. Each complete segment header has a fresh UUID, epoch and checked monotonic ordinal; validate unique identities and ordinal order rather than using a filename as proof of chronology. Unexpected incomplete/malformed headers fail closed with original bytes preserved. A genuinely empty private directory is an empty read and never causes file creation; segments without their required validated access lock are corruption. Never delete arbitrary glob matches or unrelated files. Validate all relevant identities before a retention mutation. Keep the actual writer lock for its whole lifetime. Snapshot readers take a separate short shared access lock; append/rotation uses the exclusive access lock. Every reader/writer access-lock operation opens a fresh descriptor/OFD of the same validated lock inode: `dup`/`File::try_clone` does not provide independent flock ownership. No USB owner holds either lock, and no console write occurs under either lock.
- Rotate before the next whole line would cross the configured byte limit. Revalidate, sync, move only known validated segments, create the next segment exclusively and sync its header/directory. Never split a JSON/UTF-8 record, use copytruncate or a latest symlink. Multi-file rotation is not a business transaction or power-loss atomicity claim.
- Each process and each segment has a fresh UUID. Restart seals a prior active segment before starting a new epoch. A trailing incomplete final record is preserved and reported as a gap; it is never appended to, returned as valid or rewritten as successful shutdown. Unexpected malformed headers/full records, duplicate segment identity or unsafe files must produce explicit corruption/permission errors, with the originals preserved. Do not silently reset history. Administrator repair may be needed for corrupt logs; logging can be explicitly omitted during separate recovery work.
- Runtime write/rename/sync/identity failure latches that process's sink unhealthy. Do not automatically reopen it as healthy. Size/count retention is the disk guarantee; age-based expiry and compression are deferred and are not required for this byte/count implementation. This is private operational history, not immutable audit or secure erasure.

## Ownership, queues and shutdown

```text
actual USB/application owners -> bounded nonblocking typed record admission
                              -> independent file writer owner
authorized log observer      -> at most two actual bounded reader workers
application WAL              -> unchanged durable admission/cleanup facts
```

The global tracing layer holds only a weak recorder reference. Producers do no filesystem I/O, never wait for queue capacity, and do not take a lock held over I/O. Queue full/oversize/overflow increments checked or saturating loss counters and yields visible gaps. The owner exposes a nonowning status/reader monitor; it holds no USB FD, helper handle, mutation lock or business acknowledgement.

Keep at most two blocking reader operations active and two queued requests, including after an RPC deadline/client disconnect. The actual reader worker/permit remains owned until its operation returns. Scan at most256 KiB and return at most16 records/32 KiB per page, plus bounded metadata under the existing64 KiB IPC frame. Return progress cursors when filtering consumes a scan window. An expired/missing cursor yields gap; no caller-controlled path or unlimited scan. Check UID/profile authorization before opening/evaluating history.

Authorized log reads bypass a stalled Runtime/WAL actor for all outcomes, including Busy, disabled or read failure; they never fall back into its command queue. Authorization failures retain the existing denial path. Saturated log reads return Busy rather than occupying all ordinary RPC slots waiting for a reader permit. Rejected requests retain the existing bounded denial/audit behavior. Fast sticky Stop controls keep their existing path.

Stop actual listeners/connections/backends and finish required business records first. Then close new log admission, drain accepted records, flush/sync, and join the real writer and readers. The synchronous `read_directory` API is only called inside an explicitly owned blocking operation; service reads always use the monitor's actual bounded workers. The subscriber or observer cannot keep admission alive forever. A filesystem hard block can still prevent process exit; an observation timeout cannot pretend the worker was cancelled/joined. SIGKILL can lose queued records and leaves no successful shutdown claim.

Startup logging errors fail before device work. Runtime logging errors do not roll back a successful USB operation or poison the business WAL, but must remain queryable and produce a distinct final logging failure after actual cleanup. Console diagnostics contain fixed categories/counters only and never go to stdout. Existing operation errors retain priority/context; a logging failure must not be mistaken for proof that a USB mutation did not occur.

## Module and API boundary

The implementation will keep concrete types, not a generic telemetry framework:

```rust,ignore
Options { directory: PathBuf, segment_bytes: u64, retained_segments: u8 }
initialize(options: Option<Options>, json_console: bool) -> Result<LogOwner>
LogOwner::monitor(&self) -> Option<LogMonitor>
LogOwner::shutdown(self) -> Result<ShutdownReport> // actual owned join
LogMonitor::read(&self, filter: ReadFilter, after: Option<Cursor>, limit: u8)
    -> impl Future<Output = Result<LogPage>>
read_directory(path: &Path, after: Option<Cursor>, limit: u8) -> Result<LogPage>
```

`storage.rs` owns secure files, record-boundary rotation and bounded snapshots; `worker.rs` owns finite queues, real threads, status and joins; `record.rs` owns the strict DTO, redaction/field validation and fixed render templates. CLI/helper adapters use a synchronous outer main: configure/open the owner before constructing Tokio, run the existing asynchronous operation with the same signal/cleanup paths, then join logging after that operation returns and before dropping the runtime. Blocking log startup therefore never occupies a Tokio worker, and existing service signal registration still precedes its first await/resource acquisition. CLI/helper adapters configure and finish that owner. Service adds one authorized log-read request/response and a nonowning monitor, retaining the existing `serve` wrapper for callers without logging. No root helper RPC, TLS framing, USB operation or Session/Metrics schema changes.

## Validation and delivery

1. Real filesystem tests: bounds/rotation, complete JSON, retained order/cursors, reader/writer concurrency, restart/partial tail, second writer, ownership/modes, links/FIFO, replaced files/directories, unknown names and real I/O failures.
2. Real tracing tests: secret sentinels/unknown Debug values are never persisted or formatted; allowed fixed failures survive; existing quiet-h2 negative/positive remains. Queue pressure and status/overflow failures are observed, not silently swallowed.
3. Actual subprocess tests: CLI/helper argument/help, human/JSON pages, profile/UID refusal, follow cancellation and output backpressure, machine stdout has no logger text, startup refusal and runtime/shutdown failure exits after actual network/virtual-owner cleanup.
4. Hold the actual logger access/syscall gate, fill its queue, stop a real software USB/network owner and observe cleanup before releasing that same log gate; then drain/join. A bounded gate is not a permanent filesystem cancellation claim.
5. A focused clean QEMU installed-path scenario verifies root/nonroot private directories, authorized log pages/refusals, actual USB lifecycle/audit projection, rotation, original cleanup and fresh reuse. It does not replace or relabel earlier 23-run evidence. A second fault scenario is justified if needed to prove a privileged boundary that software cannot establish.
6. Run complete fmt/default+all-features Clippy/tests/release/rustdoc/vendor checks, native-only dependency isolation, meaningful signal/backpressure regressions, exact input/binary receipts and four native CI. Preserve all failures, commands, source scope and missing hardware evidence. Update README/site facts only after actual evidence; use meaningful commits and normal SSH push.

Other G3 gaps identified in this audit stay explicit: pure Rust crash records need independent actual cleanup witnesses; process death/flock is insufficient. Trusted credentials are startup snapshots, so supported pin replacement presently requires cooperative whole-agent restart, not StopJob/StartJob or hot revocation. No CRL/OCSP or new claim about live-session revocation is introduced here.

## Service interface clarification

The additive IPC1 request is `ReadLogs { profile, after: Option<Cursor>, limit: u8 }`; a successful authorized read returns `Response::Logs { page: LogPage }`. Logging failures return `Response::LogError { code: LogReadFailure }` with the closed categories `invalid_input`, `permission`, `corrupt`, `io`, `busy`, `disabled`, `unsupported`, `worker`, and `lost_records`. All authorized outcomes bypass the Runtime/WAL queue. Profile authorization failures retain the existing business `Error { code: permission }` and bounded durable denial path. Filesystem log permission failures are distinct from profile authorization failures. Neither logger failure nor disabled logging changes `journal_healthy`. No arbitrary logging error string is serialized.

`serve_with_logging(config, stop, Option<LogMonitor>)` adds the nonowning read/projection capability while `serve(config, stop)` remains the no-logging wrapper. Runtime mirrors only new audit sequences after the real Store commit succeeds, before any fallible health-registry refresh. Each projection carries the current committing application instance and original audit fields; it does not invent an attempt ID or rewrite the referenced job/session identity. Old retained history is not replayed as newly committed work at startup.

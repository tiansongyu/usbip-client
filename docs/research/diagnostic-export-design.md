# Private diagnostic exports

2026-10-24. Baseline clean `46cd950affb0c949b164d0051be7bf70f365fac9`. Status: proposed implementation contract. The preceding goal turn made verified progress on bounded logs and publication. The complete Phase 0–7 objective remains active. This increment implements the remaining diagnostic-export part of requirement 4.11; it does not turn an observation into USB readiness, a recovery proof or complete Phase 3 acceptance.

## Evidence and decision

Read the actual CLI, Native/helper diagnostics, service Runtime/IPC/store, health and logging sources. The existing `doctor` makes only fixed read-only probes, but its DTO contains paths, arbitrary explanation/action strings and a loosely filtered version line. Serializing it wholesale is not privacy redaction. Loading a broker's `LoadedConfig` would also require its actual UID and read private credentials, so an operator export must use only the existing protected public manifest/client endpoint loader.

The service's Jobs/Sessions/Audit requests wait for its single actor. A transaction awaiting filesystem progress can therefore block the very information needed for diagnosis. Successful health and all authorized log reads already have independent paths. Reuse those paths and add a small read-only publication of **already committed** application state; do not read private journals or send file paths through IPC.

| Component | Reuse and boundary |
| --- | --- |
| Rust 1.97.1 / Tokio 1.54.1 | Actual fixed Native probes; retain and await the observer task. A dedicated runtime inside its owned blocking task joins internal Tokio filesystem work even after a probe's local timeout. Runtime destruction and OS calls may remain blocked; no hard exit bound is invented. |
| serde / serde_json 1.0.154 | Serialize only a closed export DTO through a byte-limited `Write`, with no arbitrary `Value`, error string or public generic Serialize entry point. |
| rustix 1.1.5 / std files | Reuse descriptor-relative trusted-parent and identity checks. Fresh EXCL/NOFOLLOW/NONBLOCK/CLOEXEC staging, file sync, NOREPLACE publication and directory sync. Business Store's replacing rename is unsuitable. |
| Existing service and logging | Same SO_PEERCRED/profile authorization, state semantics, healthy/loss categories and actual reader quotas. Snapshots and pages hold metadata only. |

Fixed source, license and failure-mode research is retained separately. Use the technical-design skill's source-research method and this repository's existing research directory; do not create unrelated component skills or architecture records.

## User interface and artifact

Add `rustusb diagnostics --output /absolute/private/bundle.json`. Default builds collect Native/helper prerequisites. A secure-transport build additionally accepts `--agent-config FILE --profile ID` for one authorized application profile, including its committed history, independent health and bounded projected process logs. A default build can optionally read `--logs-directory DIR`; this is mutually exclusive with agent mode. The command cannot simultaneously enable a process file writer with global `--log-directory`, matching the existing logs observer.

The output parent already exists, is owned by the caller with mode0700, and has protected root/caller-owned ancestors. Existing output files are refused. The caller chooses only a local artifact path; no agent/helper RPC accepts an output path, input log path, credentials, new endpoint or arbitrary command. The command never loads modules, changes USB drivers, starts a server, imports, stops or reconciles a device.

The schema1 artifact is a single private JSON document, not a tar archive or a copy of the state/config/log files. Its hard serialized limit is **4 MiB**. A capture records section outcomes, limits, retention/gaps, capture ordering and privacy policy. Observing a missing/unhealthy component is a successful diagnostic observation; it is not a statement that sharing works. Missing requested data, expiry, truncation, cancellation or collection failure are explicit partial outcomes.

Only allowlisted enums, bounded numbers, booleans and generated aliases enter the document. Replace profile, principal, fingerprint/device key, process/application epoch, actor UID and job/session/attempt/resource identities with separate bounded namespaces, preserving relationships within this one export. Do not write the reverse mapping. Omit raw PIDs, hostnames, IPs/TCP tuples, paths, cursors/tokens, serial/manufacturer/descriptor strings, raw USB data, credentials, certificates/pins, arbitrary tracing targets/messages, Native stdout/stderr and Display/Debug errors. Kernel identification is a strict numeric projection, not the untrusted release string. Keep explicit operation failure, cleanup and transport-end categories independently, including State/Protocol or Io with Verified cleanup.

Default local information comprises compile-target OS/architecture categories, program version, numeric kernel information, separate loaded/sysfs/install-time module observations, fixed usbip/usbipd check categories and sanitized helper status/counts. Tool version text and human explanation/action fields are excluded. There is no local raw device scan or kernel/session/config dump.

Agent collection includes all retained authorized profile jobs, sessions/attempt outcomes and audit observations from its pinned commit. Retention remains bounded, not permanent history. Independent health pages retain their own sample sequence/availability; omit network identities in the artifact. Process-log collection is bounded to at most32 pages/512 records and separately marks more/gap/partial-tail, unavailable live status, corruption, Busy or loss. It does not claim state, health and file logs were sampled atomically.

## Committed service view and pagination

Use an additive IPC1 interface in a service diagnostics module; old DTOs and private disk2 remain unchanged. Exact Rust names will be fixed by implementation review:

```rust,ignore
BeginDiagnostic { profile }
DiagnosticPage { profile, snapshot, kind: Jobs | Sessions | Audit, after: u16 }
EndDiagnostic { profile, snapshot }

DiagnosticToken { instance_id: InstanceId, number: NonZeroU64 }
DiagnosticHeader { token, committed_sequence, counts, publication_age_millis, ... }
DiagnosticPage { token, committed_sequence, kind, items, next_after, ... }
```

Every operation independently requires **both Read and Audit** on the exact profile, then binds the snapshot to the kernel UID and profile. A token is not authorization. Authorized results, including Busy/Expired/invalid-cursor failures, bypass Runtime and WAL. Unauthorized requests retain the existing durable denial path; no token/history lookup occurs first. Root uses the same exact-profile interface, not arbitrary state-file access.

Publish an immutable metadata state view only after real Store commit and `self.state = next`, while preserving the existing committed log mirror before any independently fallible registry refresh. Initialize from the verified committed store before binding the listener. A publication problem must be visible as unavailable/stale diagnostics, never poison WAL, suppress a committed audit mirror or acknowledge an uncommitted fact. Views have no device FD, handle, stream, permit, command channel or mutation lock.

Pin at most **four actual snapshot objects**, at most one per UID, with a fixed **30-second** access expiry. The metadata quota belongs to the snapshot's actual Arc lifetime: removing an entry on End/expiry cannot free its quota while a page is still copying it. Keep a bounded weak owner index so a new Begin cannot bypass the per-UID limit during such a copy. Reap expired entries/unused weak references on registry operations; idle memory remains finite until the next operation or service teardown. Each slot references one existing bounded committed state (current serialized state at most2MiB); no private store file is reopened. Generate tokens with the current application epoch and a checked monotonic counter, not filesystem/RNG work under a registry mutex. Counter overflow refuses a new snapshot. Do not evict an active snapshot to make a concurrent request appear successful.

Pages contain at most16 items and at most60000 encoded payload bytes under the existing64KiB frame. Each requested kind and offset is validated, with fixed maximum counts from the original512-job/512-session/1024-audit store. Offset means rows consumed **after authorization/profile filtering**: zero starts, equal-to-length is an empty final page and greater-than-length is invalid. Audit includes the exact profile plus only the requesting UID's unscoped Denied events, including for root; the exporter applies this same filter again to agent log pages. Copies/encoding occur after releasing short registry/control locks. A disconnect or timeout does not keep creating snapshots or reserve unbounded background work. Finish is idempotent and releases metadata only; unknown/expired/foreign tokens have the same no-effect acknowledgement after authorization. Abandoned snapshots expire and never own USB resources. The exporter always attempts End after a successfully returned token, including partial/cancelled collection.

## Owned probes, cancellation and output

```text
CLI signals -> shared export collector -> retained read-only probe owner
                                    -> authorized pinned committed pages
                                    -> independent health and actual log readers
                                    -> alias projection / finite serialization
                                    -> actual private file worker / explicit receipt
USB job owners and WAL remain independent of every exporter worker
```

Keep a nonempty shared `rustusb-diagnostics` library for collection, projection and artifact lifecycle, so later frontends use the same behavior. Its default Native dependencies do not include service/transport/Rust engine; a secure-transport feature adds the existing application client. Core retains domain-only types. Do not introduce a generic telemetry/archive framework or change the USB data plane.

Register SIGINT/SIGTERM before beginning collection. Cancellation stops new stages, closes a pinned diagnostic snapshot if possible, and waits for already accepted actual probe/output work. Reuse Native's fixed trusted tool invocations, but add `diagnose_native_with_child_reaping() -> rustusb_core::Result<NativeDiagnostics>` for the export observer: every failed pipe/output/wait/deadline path starts kill and actual wait rather than declaring a child gone after a two-second observation. Even a failed kill is followed by actual wait; a wait error returns a typed failure and cannot be described as reaped. Preserve the old doctor's interface/behavior. Both fixed probes are joined even if one fails. This new probe future must itself be retained; `kill_on_drop` alone is not a reap receipt. The dedicated observer runtime is destroyed/joined before its blocking owner returns, covering internal timed-out Tokio FS work; this does not substitute for explicit child wait. No actual USB owner is acquired.

Prepare the checked private staging file before expensive collection. All blocking filesystem operations run in retained blocking work and outside Tokio workers. Limit serialization before unrestricted allocation; retain no more than the stated source/page/alias budgets. A cancelled capture can leave a valid document explicitly marked partial under its generated `.partial` name. A logical partial capture may publish a final valid JSON with `complete:false` and exit1; no requested failure is silently converted into an empty healthy section.

The optional application stage runs in one retained blocking owner with its own runtime. This also isolates the public manifest client's synchronous path checks and joins its accepted internal filesystem tasks when the private runtime is destroyed. Local log history similarly uses one retained blocking owner. A 60-second soft collection budget stops new requests/stages and bounds cancellable IPC waits; it does not terminate an accepted filesystem syscall, replace actual child reaping or promise an exit deadline. A returned snapshot token is always followed by an End attempt, even after cancellation or a partial page failure; a lost Begin reply leaves only the service's bounded expiring metadata slot.

Use a fresh fixed-prefix staging name and mode0600. Check original parent/FD/file identity, type, mode and nlink before and after writes. File sync precedes `renameat_with(NOREPLACE)`, followed by directory sync and identity verification. Never overwrite an existing destination or unlink an unknown/replaced artifact. Failed writes preserve actual partial bytes. A failure after rename can leave the final file present; report publication/durability uncertainty rather than asserting zero side effects. Permanent regular-file/fsync/kernel blocking is not made cancellable by NONBLOCK, a dropped future or a local deadline.

Machine stdout emits one bounded receipt with capture outcome, publication state and fixed failure categories after actual work is joined; it does not dump raw diagnostics or logger text. Human messages use fixed templates. Use the existing bounded EventOutput path. Cancellation/output failure cannot stop the separate USB service. A signal racing a completed publication must preserve the actual publication result; it does not undo a completed file operation.

## Validation and delivery

1. Closed projection/alias tests with secrets in every excluded string field, malformed/oversized input, identity relationships, duplicate/missing/unknown fields and byte/row/alias limits. Tests must inspect actual JSON, not just a function return label.
2. Real temporary filesystem tests for private parents, links/FIFO/replacement/unsafe modes, preexisting destinations, write/sync/publish failures and concurrent NOREPLACE. Keep originals and distinguish pre/post-rename errors.
3. Actual process probes and CLI exports: default/secure feature help, JSON receipt/stdout separation, Native/helper component failures, existing-output refusal, partial/corrupt log input, SIGINT/SIGTERM, actual writer failure, output backpressure and accepted-worker ownership. No host USB mutation.
4. Service tests with actual temporary store and UDS: Read-only and Audit-only refusal, UID/profile/token isolation, four slots/one-per-UID/expiry/counter overflow, complete coherent pagination during new commits, real unacknowledged/failed commit exclusion, actual WAL wait with independent reads, no lock held over I/O, cancellation/End semantics and unchanged sticky-stop/committed-mirror behavior.
5. A focused installed Native IPv4 QEMU scenario: real UID0 helper/UID2000 agent/UID1000 exporter; real HID/FTDI I/O before and after a private export; exact original resources preserved. Hold the real application WAL fsync-entry gate, export the last committed view plus independent health/log observations before releasing that same gate, verify withheld work is absent, output permissions/limits/redaction, unauthorized refusal and true publication/cleanup receipts. A removable gate is not permanent-kernel cancellation or hardware G1/G2 acceptance. Prior matrices keep their original revisions.
6. Full fmt/default+all-features Clippy/test/release/rustdoc/vendor checks, Native-only dependency isolation, actual process regressions and native Ubuntu22.04/24.04 x86_64/aarch64 CI. Record all source/ELF/HEAD/clean scopes and every failure; update operator docs, README/site and maintenance rules only against actual evidence, commit meaningful units and use normal SSH push.

The focused local process runner uses an ordinary-UID Linux ptrace syscall-entry gate for the actual fresh partial file's fchmod (prepare) and fsync (accepted publication). It matches the actual open FD and its namespace inode, retains the original process pidfd, forwards real signals and waits for the original syscall return and child exit. A signal is delivered while the selected worker remains held; only afterwards is the same syscall released. The two gates distinguish a valid cancelled partial from already accepted publication. This is an intentionally removable entry stop, not a permanently blocked in-kernel syscall. Startup/observation/collection failures preserve their original verdict and never authorize a signal to a reused PID or a fabricated wait receipt.

### Installed tracer launch correction

The first actual clean `bde3aa9` run remains FAIL before server agent startup: the original preparation tracer intentionally accepts exactly15 fixed arguments, while the new logging-enabled launch supplies21. Its actual stderr is `prepare-gate: invalid fixed test invocation`. Both guests exited0 through collection, with all four source/binary/HEAD/clean guards true; there was no successful diagnostic export or USB I/O acceptance. The original strict tracer is behaving as designed.

Keep that existing C file unchanged. Generate a separate diagnostic-test variant from its exact source using two single-occurrence substitutions: argc15→21 and the explicit six logging arguments inserted before `agent`. The new fixed allowlist admits only `--log-directory /var/log/rustusb/agent --log-max-bytes 262144 --log-retain 4`; directory, setpriv UID/GID, agent config, USB-node validation and every other argument remain exact. Preserve the original ptrace selectors, register/return behavior, FD checks,30-second hold,120-second process limit, nonce, failure handling and cleanup code byte-for-byte. Do not accept arbitrary extra CLI arguments or modify production logging to accommodate the test.

Save the generated C source and original-source provenance beside the compiled gate/actual executable receipts. Add static allowlist mutations and an exact reverse-substitution comparison to prove only the launch template changed. The subsequent runtime uses a fresh clean commit and evidence directory; it does not replace the first verdict or reattribute its source-only reviews as a successful launch.

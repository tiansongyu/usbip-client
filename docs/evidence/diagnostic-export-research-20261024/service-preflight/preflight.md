# Single-profile diagnostic export: service preflight

2026-10-24. **Proposed; source analysis only.** Fixed clean baseline `46cd950affb0c949b164d0051be7bf70f365fac9`. No implementation, build, product command, QEMU or old verifier was run. This reviewer authored parts of the service and logging implementation; this is a source-method cross-review, not an independent implementation audit. Exact inspected source hashes and Git-blob correspondence are in `source-before.json` / `source-after.json`.

## Decision

A useful diagnostic bundle can reuse the current authenticated agent endpoint, typed lifecycle/error enums, health monitors and bounded log workers. **It should not serialize existing RPC responses wholesale, read private journal files, or merely loop Jobs/Sessions/Audit while claiming that this works during a stalled journal.** Add a small committed, immutable diagnostic view, published only after successful commit, and a dedicated authorized read path. Keep collection, output writing and device ownership separate.

Use one explicitly selected profile. Require both existing `Read` and `Audit` actions; neither implies the other. Do not discover all profiles/devices or call remote Discover as a side effect. Existing feature gates and IPC envelope version 1 can remain; the new requests/responses are additive, with a separate diagnostic schema 1. An old agent's inability to serve them is `unsupported/unavailable`, never an empty healthy snapshot. This does not finish all requirement 4.11 observability work or Phase 3.

## Existing behavior, with source anchors

| Operation | Authorization and result | Actor / boundedness |
| --- | --- | --- |
| Profiles | Filters immutable manifest by `Read`; returns ID + backend, not credential paths | Still dispatched by the single Runtime actor; at most 16 definitions |
| Jobs | Exact current profile `Read`; root bypasses only Read/Audit after profile existence check | Actor; sorted UUID cursor, 16 records; no public commit revision/gap |
| Sessions | Same Read boundary; full role/evidence/error/cleanup/attempt metadata | Actor; at most 16 and 60,000 encoded record bytes, then outer 65,536-byte frame; cursor has no snapshot revision |
| SessionHealth | Exact profile Read | Successful reads bypass actor through Controls; 16/60,000-byte bound. Errors fall back to actor. Reporter snapshot is metadata, not USB ownership |
| Events | Exact profile Read; selected lifecycle actions | Actor; 16 from retained audit ring, monotonic cursor, explicit global oldest/gap |
| Audit | Exact profile Audit; includes own unscoped events, root all unscoped events | Actor; 16; cursor past current sequence rejected. Empty filtered page advances to current sequence |
| ReadLogs | Exact profile Audit checked before cursor/history/reader admission | Every authorized outcome bypasses actor, including Busy, disabled and filesystem error; 16 records, 256 KiB record scan, 2 actual readers + 2 queued |
| Discover | Separate Discover permission and fixed client credentials | Actor; real network discovery plus a durable audit commit. **Not a passive local diagnostic snapshot** |

Evidence: `service/config.rs:154–165`, `service/rpc.rs:11–86,150–241`, `service/ipc.rs:209–312`, `service/runtime.rs:406–655`. All paths here mean `crates/rustusb-.../src/...`.

Transport is a 4-byte-length JSON frame, max 65,536 bytes; one server request has a 5-second deadline, 16 concurrent request tasks and a 16-command actor queue (`ipc.rs:25–26,132–153,315–330`). Client `call` verifies the root-managed endpoint directory/socket and actual broker UID, then applies its own 5-second deadline (`ipc.rs:367–401`). Deadline expiry disconnects a caller, not an actual USB owner. Already enqueued actor commands can still run after their reply receiver disappears.

The actor awaits `command`, `fact` and persistent Store transactions serially (`runtime.rs:306–345,352–375`). Thus current Profiles, Jobs, Sessions and Audit can time out during another transaction's fsync. Successful health/log reads are useful exceptions, not proof that all diagnostics are independent of WAL. Avoid filling the 16 ordinary IPC slots with a new waiting export operation.

Persistent state has 2 MiB total encoded limit; admission reserves at most 1 MiB plus bounded future health growth. Hard cardinalities are jobs/sessions <=512, audit <=1024, requests <=256, attempts/session <=32 and encoded link history/entry <=2048 bytes (`store.rs:7–13,96–119,202–275`). The prune target is 128 verified/no-start terminal sessions and 128 Finished jobs, but Finished jobs still referenced by retained sessions are preserved; it is not a blanket 128-entry history guarantee (`store.rs:159–199`). Removed profiles remain inaccessible to current RPC authorization, including root, rather than exposing the old private state via an arbitrary path.

## A diagnostic-specific projection is necessary

Existing DTOs are bounded and payload-free but not an automatic shareable privacy contract:

- `Session.principal`, profile IDs and audit profile names are caller/admin-chosen labels. Validation is only 1..64 ASCII alphanumeric/`-_.` (`config.rs:253–262`); a valid label can still contain an internal name or sentinel.
- `Session.device_key` is a persistent device fingerprint; LinkHealth contains actual local/peer socket addresses (`model.rs:122–155`, `core/link_health.rs:81–102`). Neither is raw USB data, but both link records to a device or network.
- Existing audit contains actor UID, job/session UUIDs and a monotonic sequence. These are useful correlations, not authorization tokens or a proof that current kernel state matches historical state.
- Doctor has free-form `detail/action`, tool path/version and kernel-release strings. Even bounded safe ASCII is not a privacy policy (`native/diagnostics.rs:65–125`, `daemon/diagnostics.rs:35–49`). Do not serialize it wholesale.

Recommended default export projection: preserve enum/numeric observations, canonical generated instance/job/session/attempt/resource UUIDs, original audit sequence, role, evidence, operation failure, cleanup category and recorded proof enum. Replace profile text with `selected_profile`; map each selected device key to a bundle-local ordinal; retain principal presence, not its text; retain connection number/epoch and address family, not TCP addresses. If preserving actor distinctions is useful, use caller/broker/root/other classes rather than arbitrary names. Apply the same projection to log audit mirrors. Do not insert fake replacement strings into an existing DeviceKey/LinkHealth DTO: define new diagnostic DTOs with explicit redaction fields.

For doctor, reuse fixed probes but export only tool/module identifiers, closed status values and booleans. Version/kernel strings need an explicit additional policy before inclusion; no arbitrary Error Display/Debug, original path, manifest, config, certificates, pins, keys, serials, payload, subprocess output, private state/Root journal or log segment bytes. Helper `active_sessions` is global pool metadata, not profile-scoped: omit it from the default single-profile package rather than broadening the operator's view. A local observer's doctor is labeled local/observer-UID, not the remote USB server or the broker's permission context.

## Minimal additive service architecture

1. Add a private metadata-only `CommittedDiagnosticView` holding the current application instance, committed sequence, committed monotonic observation time, and typed bounded jobs/sessions/audit. Publish an `Arc` only after Store commit succeeds and `self.state = next` (`runtime.rs:325–340`), before any later fallible health refresh can suppress the fact. Never expose the Store mutex, disk pathname, request deduplication identities or pending candidate state. Initialize it after startup's actual state load/reconciliation. A failed commit retains the old view; separately report current journal health.
2. Use a separate short publication lock with `try_lock`/fixed Busy or poison error. Clone metadata references and release locks before projection, encoding, network writes or log reads. Do not hold Controls or a data-link reporter lock across those operations. The view never contains a control sender, helper FD, lease, stream, permit or durable ACK.
3. For full bounded pagination, freeze one selected-profile projection in a small private snapshot registry. Candidate bounds: four actual metadata snapshots total, one per UID, 30-second lifetime, <=2 MiB per snapshot, <=60,000 encoded bytes and <=16 items per page. These are **proposed limits**, not current facts. A slot remains charged while an in-flight page holds its snapshot; expiry/caller cancellation must not allow unbounded still-referenced snapshots. No disk or USB action is involved.
4. Bind the opaque snapshot ID to actual peer UID, exact profile, current service instance and immutable committed sequence. Reauthorize every page before inspecting the token/cursor. No cross-UID/profile reuse, even with a copied cursor. Expired/unknown cursors return a fixed explicit result; do not silently restart and merge versions. Snapshot cache pressure returns Busy, never evicts somebody's in-use view or stops a job.
5. A smaller first implementation may instead return a **deliberately limited recent slice** in one frame, with total/truncated counts and a fixed selection order that prioritizes unconfirmed/current sessions. That avoids snapshot leases, but must be presented as a partial diagnostic slice, not all retained history. Simply adding a sequence to independently changing ordinary pages is insufficient for a consistent complete export.

Candidate public shape, to freeze with the root before implementation (not compiled in this review):

```rust,ignore
Request::OpenDiagnostics { profile: String }
Request::ReadDiagnostics {
    profile: String,
    snapshot_id: DiagnosticSnapshotId, // canonical generated identity
    section: DiagnosticSection,       // Jobs | Sessions | Audit
    after: Option<DiagnosticCursor>,  // bound section + index, not path
}
Response::DiagnosticsOpened { header: DiagnosticHeader }
Response::DiagnosticPage { page: DiagnosticPage }
Response::DiagnosticError { code: DiagnosticFailure }
```

`DiagnosticHeader` identifies diagnostic schema, snapshot/service instance, commit sequence, snapshot age at collection, backend, total counts, configured limits and current journal-health sample. It echoes no profile path or credentials. A page has the same snapshot identity, its section, at most 16 projected records, continuation and omitted/truncation counts. Strict serde enums/unknown-field rejection and explicit required nullable fields apply. All complete serialized envelopes are checked against 65,536 bytes. End-to-end bundle capacity is separate; a suggested <=4 MiB file, <=128 service pages, <=8 log pages/128 log records, <=32 health pages and a fixed total collection deadline make repeated changing/gapped histories finite. These values require byte-budget fixtures before acceptance.

No new broad Diagnostics permission is needed: require **Read AND Audit**, including for log-containing exports. Root still must select a currently configured profile. Authorized invalid/Busy/unavailable diagnostic reads should use the independent path rather than falling back into a stalled actor. Denied requests retain the existing bounded denial path; no cached snapshot or log cursor is evaluated first. Preserve current per-action permissions and old Session/Metrics/IPC body shapes.

## Health, logs, cancellation and truthful completeness

Health remains a separate sampling pass over the selected snapshot session IDs. Use the current read-only reporter API and preserve `Live`, `OwnerEnded`, `PreviousEpoch`, `NotObserved`, `NotDataSession`; raw-v1 remains unavailable. A current sample can be newer than the committed lifecycle; attach its own session/instance and sample_sequence/elapsed fields. If a session disappeared or changed between passes, record that fact rather than attaching a sample to another session. Never replace a historical terminal sample with a fresh connection or promote PONG to USB progress.

Reuse `LogMonitor::read` and existing ReadLogs permissions, filter and error codes. Its worker owns an accepted read after the RPC future disappears (`worker.rs:254–296,593–630`); actual 2+2 slots do not become free when the collector times out. Snapshot logs once through bounded pages, not `follow`: preserve cursor, gap, partial_tail, scanned_bytes, loss/status and finite budget exhaustion. An empty filtered page may still have progress (`storage/linux/reader.rs:163–211`). Capture the log read interval separately; postcommit mirrors are best effort, can be absent or newer than the state snapshot, and have no durable or cleanup authority.

The local collector can cancel pure IPC waits and stop starting more pages on INT/TERM. It must not stop jobs or alter devices. A timed-out log request leaves its actual worker with LogOwner, which is still joined after device cleanup on agent shutdown. The local output writer/doctor task must likewise remain owned until actual completion; a detached `spawn_blocking` or dropped join handle is not cancellation. Native doctor already has internal async filesystem deadlines and version-process handling; blindly wrapping/dropping `diagnose_native()` is not proof that all underlying filesystem work ended. Either retain its owning task and honestly allow real completion to delay exit, or add a separately audited collector API with explicit actual worker/child ownership. Permanent filesystem/kernel blocking remains unbounded.

Collection deadline, cancellation, permission refusal, missing agent, log-disabled, log-Busy, old-agent unsupported, corruption and capacity truncation are **section outcomes**, not empty success. Use a valid final JSON envelope with `complete: false` and fixed causes if a partial package is published. Do not publish a truncated JSON file as successful output. File creation, private-parent validation, exclusive destination and actual write/sync/join are N/root's storage/CLI workstream; the service RPC must never receive that path.

## Necessary verification, without claiming it ran

1. **Actual service/WAL stall fixture:** commit a real state, hold a later real Store transaction before completion, request diagnostic snapshot via authenticated Unix IPC. Assert original committed sequence only; uncommitted candidate absent; authorized response arrives independently; release and confirm new snapshot advances. A failure after commit but during health refresh must not erase the committed view.
2. **Authorization and privacy:** Read-only, Audit-only, wrong UID/profile, removed profile, root-selected-unknown, cross-UID token and malformed cursor/unknown fields. No data/cache existence leaks before authorization; no device operation. Seed valid-looking secrets in profile/principal/kernel/tool detail/version and peer addresses; encoded package excludes them and all private paths/keys/payload while UUID/proof/error correlations remain useful.
3. **Bounded histories:** real 512-session/1024-audit/32-attempt extremes, byte-limited pages, 65,536 boundary, total file cap, record larger than remaining frame, expiry, four occupied snapshots, repeated cancelled requests. Count omitted/truncated observations; no silent skip/empty-complete. Pin quota lasts through in-flight actual page references.
4. **Actual logger obstruction:** hold the real access-lock OFD, occupy two real readers and two queued requests, cancel observer/timeout export, then verify next read Busy rather than new workers. Release the gate and join originals; USB owner/I/O must remain unchanged. Include disabled/corrupt/gap/partial-tail/zero-visible-progress cases.
5. **Temporal truth:** previous-epoch unresolved sessions, current terminal State/Protocol with Verified cleanup, pending/Unknown proof, raw-v1 and newer live v2 samples. Export preserves categories and times, never sets cleanup or health, never calls Start/Stop/Discover/Inspect to manufacture a better result.
6. **Actual process/installed check:** nonroot authorized one-profile export while Native HID/FTDI operate; other profile/cert/path/UID sentinels excluded; deny reads without side effects; cancel exporter and continue original I/O. A removable WAL/log gate verifies the limited independence windows. QEMU should preserve original capability/Rootproof/ELF/source and final baseline, not infer cleanup from the diagnostic file. This is a new focused evidence scope, not a replacement for old 23-run or logging epochs, and not hardware acceptance.

## Implementation boundary

Expected additive changes: service `diagnostic.rs` typed projection/cache, `runtime.rs` postcommit publication, `ipc.rs` authorized independent path, `rpc.rs` new variants; CLI/library collector and bounded checked output path; narrow doctor projection/ownership changes if needed. Reuse core enums and existing log-worker ownership. Do not alter helper mutation APIs, root proof rules, private journal format, TLS framing or USB owner lifecycle merely to export diagnostics. Do not run diagnostic collection in the Runtime actor or wait on its WAL for authorized pages.

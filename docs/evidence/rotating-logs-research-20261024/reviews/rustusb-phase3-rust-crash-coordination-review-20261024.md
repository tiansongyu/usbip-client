# Phase 3 gaps and Rust-session crash coordination

Date: 2026-10-24. Read-only source review at `bd6e08be8b304767452dc98b6606e2e65448c2da`. Proposed design, not an accepted implementation or runtime result. No Cargo, QEMU, product execution, repository edit or Git mutation. This reviewer authored parts of the service/backend/harness; this is cross-review of the current ownership model, not an independent implementation claim.

## Decision

Rust crash coordination is a real remaining G3 lifecycle gap, not merely a missing status label. A killed managed Rust server leaves virtual export and Rust incoming sessions without a queryable helper attempt. Restart cannot retire them, and the same profile cannot start again. The current conservative refusal is correct, but it does not satisfy requirements 4.5/279–280 (no permanent occupancy; restart coordinates existing state).

Do not repair this by treating PID death, a fresh instance ID, acquired directory lock, empty new registry, remote TCP EOF, or a new successful import as the old session's cleanup proof. The current journal lacks the relationships needed for such a conclusion.

The smallest useful first recovery extension is a **durably bound physical-parent dependency** for USBFS incoming sessions, backed by the exact original grant's root terminal. Pure virtual crash coordination needs an explicitly different authority. A surviving, nonroot actual-owner guardian can produce a real cleanup receipt after the management agent dies; a process-death-only shortcut cannot preserve the current meaning of `Cleanup::Verified`. This guardian is a proposed architectural extension, not a prerequisite to every other G3 increment.

For sequencing: immediately capture the current Rust crash behavior in two real QEMU negative cases and fix/approve the evidence model before implementation. In parallel, logs/retention/redaction is a more direct, bounded original requirement (4.11) than introducing a second ownership process; it can be implemented next without declaring Rust recovery done. Credential replacement/revocation and negative authorization coverage are also explicit G3 gaps. TUI/GUI must not replace those unfinished requirements.

## Current guarantees and concrete source locations

| Area | Actual guarantee and limit |
| --- | --- |
| Original requirement | `docs/requirements.md:259–288` requires restart coordination, idempotent cleanup and conservative storage behavior. `docs/roadmap.md` G3 additionally requires security negatives, full state/failure paths and recovery. Acceptance 4.5 explicitly lists Rust virtual export/incoming crash sessions as unresolved. |
| Durable admission | `service/runtime.rs:810–882` commits Begin before returning a SessionContext. `jobs/rust_server.rs:115–145` gets that Begin before managed `monitor.admit`. Claim therefore cannot precede its committed session, but Begin carries no durable export-parent or actual-owner identity. |
| Instance versus OS owner | `runtime.rs:180` generates a fresh random instance UUID. `model.rs:121–152,173–187` persists instance/job/session identities and helper attempts, but no boot/PID/starttime, owner task token or incoming→export dependency. The UUID is correlation, not process or cleanup proof. |
| Live task owner | `runtime.rs:695–718` registers control then retains the job in JoinSet. `runtime.rs:324–347` joins workers/recoveries and processes facts. A task JoinError marks the journal unhealthy and logs unproven cleanup; it does not identify a verified release. Same-process abnormal-task loss must not be treated as process exit. |
| Resource owners | `jobs/rust_server.rs:897–985` owns initialization, server shutdown, backend close, all grant Finish calls, and observer join. `server.rs:267–295` shutdown joins the listener; Drop only requests cooperative cleanup and may leave its task alive. `server.rs:354–368` joins connections before backend settle. A dropped frontend or handle is not proof these tasks returned. |
| Managed lease completion | `managed.rs:284–294` retains the exact claim completion after cancelling its ready waiter. `monitor.rs:86–108,564–595` retains bounded in-memory terminal observations; Drop of a touched unfinished tracker is Unconfirmed. These facts disappear with the process. |
| Virtual resources | `backend/session.rs:139–187,307–310` and `device.rs` keep virtual leases/queues in process; there is no helper capability or durable root receipt. Their local lifetime differs from the remote client's VHCI resource, which is owned and proven on the client. |
| USBFS export versus incoming | `jobs/rust_server.rs:707–729` obtains an exact tracked PhysicalGrant and records its lease ID on the export session. Incoming only records Begin/phase/Finish (`115–220`); it has no helper attempt and no persisted link to that grant. Exact same-key equality is insufficient to substitute an unrelated or later grant. |
| Persistent store | `store.rs:26–83` private disk2 carries jobs/sessions/audit/requests/link_history. `store.rs:335–362` takes the protected-directory lock and strictly loads/migrates. `fs.rs:110–141` checks owner/mode/link/identity and uses nonblocking exclusive flock. This excludes a concurrent conforming store owner; it does not prove detached backend tasks or delegated USB state have ended. |
| Restart | `runtime.rs:191–224` preserves released sessions and converts others to Recovering/Unconfirmed. Zero-session jobs can finish because every real resource request follows Begin. Nonzero sessions with no attempts are not equivalent to intent-only jobs. |
| Exact helper reconciliation | `runtime.rs:1191–1203` selects only sessions with no live job and nonempty attempts. `1234–1305` requires exact kind/correlation/key/UID/resource and all attempt proofs before Verified/RootJournalProof. It cannot currently select Rust incoming or virtual export. |
| Admission after restart | `runtime.rs:656–664` refuses a profile with any unfinished prior job. Thus even a USBFS export whose own root grant was restored can remain blocked by its unrelated-at-the-schema-level incoming session. No new attempt silently retires the old one. |
| Public semantics | `model.rs:78–87` defines Verified as every owned resource's actual cleanup. `model.rs:34–39` distinguishes actual-owner release from exact root proof. `context.rs:8–66,168–186` records real facts via original durable ACK, not requested intent. Reusing these values for inferred process disappearance would silently change their contract. |

Paths above are under `crates/rustusb-service/src/` unless explicitly marked backend (`crates/rustusb-rust-backend/src/`). Frozen hashes accompany this report.

## What cannot safely be inferred from today's records

1. **Empty attempts is ambiguous.** A virtual Connected incoming session has no helper attempts but did own a network/lease; a Begin committed before claim also has no attempts. Restart must not turn both into NotStarted.
2. **Same device key is not causality.** Another export or later epoch can have the same key; its root terminal cannot retire this old incoming session. A child relationship must be recorded before admission, tied to the exact original grant.
3. **Process death is not a general driver receipt.** USBFS may have altered drivers/configuration; existing helper recovery still needs original generation and baseline verification. Remote VHCI cleanup is a separate machine's ownership fact.
4. **A lock is not a task join.** Store/control locks can become available without proving all detached same-process owners completed; current ServerHandle Drop explicitly initiates rather than attests cleanup. A PID/starttime record would improve identity, but alone still would not supply device or remote-kernel evidence.
5. **A present baseline is not complete execution history.** It can support the existing helper's narrowly authorized retirement; it does not prove an old transfer completed or that its side effects were rolled back.

## Minimal proposed proof model

Introduce a strict, bounded private recovery-binding table (next disk version) rather than silently enriching legacy Session attempts. Bindings are immutable after admission. Existing disk1/2 records become explicit `LegacyUnproven`, not automatically assigned an owner or parent based on current profile/key. Keep original instance/session IDs, failures and counters; no replay of a device operation during reconciliation.

Illustrative internal types (API proposal, not compiled):

```rust
struct RecoveryBinding {
    instance_id: InstanceId,
    job_id: JobId,
    session_id: SessionId,
    owner_id: OwnerId,
    resource: RecoveryResource,
}
enum RecoveryResource {
    LegacyUnproven,
    RuntimeOnly { profile: VirtualProfile },
    PhysicalChild {
        export_session_id: SessionId,
        grant: BrokerCorrelation,
        // Same actual resource ID if already received; absence remains explicit.
        resource_id: Option<ResourceId>,
    },
}
enum RecoveryWitness {
    ActualOwnerRelease(OwnerReleaseReceipt),
    ExactPhysicalParentRelease { grant: BrokerCorrelation, terminal: BrokerTerminal },
}
```

`bind_recovery(session, binding)` must commit before `monitor.admit`; parent belongs to the same original job/instance and the physical key matches. A parent terminal is accepted only for the exact delegated grant and original trusted lifecycle contract: cooperative Finish follows all actual owner FDs ending, or existing authorized recovery verifies the old owner and baseline. It is **derived parent evidence**, not a fabricated helper attempt for the child. Root proof that merely permits a future regrant must not be broadened into remote enumeration/transfer-completion proof.

Before using this derived proof as a terminal, the implementation must also establish that the original local connection owner cannot resume: either a matching actual-owner release receipt, or a separately approved process-domain termination rule with explicit scope. The current data model has neither. Parent baseline proof alone must not close an independently live incoming network task.

A recovery result must expose its proof kind and scope. Do not serialize a new `Evidence` enum variant under unchanged IPC1 and claim old strict clients are compatible. Options to decide explicitly are a negotiated versioned session response, or a new recovery-detail operation while retaining conservative old DTO projection. Private disk migration and RPC compatibility are separate changes. Preserve bounds (sessions512, live limits, 2MiB store with admission reserve), missing/duplicate fields rejection, immutable links, terminal retention, and no cycles/cross-job grafts.

## Actual-owner guardian candidate for pure virtual and full Rust job recovery

A nonroot guardian that survives **management-agent** failure can keep the actual ServerHandle/backend/claim completions and existing RunContext owner pipeline, observe control-channel loss, perform real shutdown and write a scoped receipt after actual cleanup. This is the useful success path: old owner really cleans, a restarted agent consumes its exact receipt, then a new attempt performs real I/O. Killing both agent and guardian before a receipt remains Unconfirmed; no design should hide that boundary.

Suggested responsibilities:

- The controller persists Job/Begin/Attempt and recovery binding before admitting work. The guardian executes the same library owner, not a second copied CLI implementation. No arbitrary executable/path/helper request surface.
- A private inherited/local authenticated bounded channel conveys typed intent, exact IDs, durable replies and stop. Its loss triggers independent sticky stop. The protocol must distinguish message acceptance from the existing durable admission ACK.
- Actual handles never enter the receipt writer or controller. Existing accepted helper/initializer operations remain retained. Prior resource cleanup still precedes original pending-record completion as in E34; receipt recording failure cannot fabricate success or prevent other real closes.
- `OwnerReleaseReceipt` is emitted only after exact listener/connections/backend/claim joins and, for USBFS, all original grant Finish results. It includes owner/job/instance/session binding, final per-session operation/cleanup outcome and original root dependencies, but no USB payload/credentials. Unknown/soft-deadline/error is preserved as unknown/error.
- Receipt storage is bounded, protected and crash-consistent, with capacity reserved before admission. It is a trusted broker receipt, **not root authority**. Old agent startup reads it under the same current profile/action policy. A late old receipt cannot overwrite a newer session or authorize a new device.
- The guardian must not die merely because the agent's cancellation future was dropped. It needs an actual supervised lifecycle and safe orphan shutdown. Its own restart/death/reboot policy, executable/config identity, duplicate guardian prevention, stale channel handling and publication order need design plus actual tests before adoption.

This is more work than adding a column to the journal. Do not rush it just to turn all historical records green. No kernel/process API behavior or deployment isolation is claimed verified in this source-only proposal.

## Concrete next acceptance plan

### First capture the present failure

Two isolated two-guest scenarios using actual managed virtual and USBFS server profiles. Establish both devices and real data first (CDC echo for virtual; HID/FTDI for USBFS). Capture exact app job/export/incoming identities, current helper grants, process identity/ELF and baseline. SIGKILL the server agent and actually reap137. Keep the client observation separate. Restart with the same protected store and record Recovering/start refusal. USBFS opt-in recovery must independently show its exact original grant terminal/baseline; it must not make the no-attempt incoming disappear. This is a meaningful expected-negative, not a fake success obtained from resource0.

### Required software proofs for a repair

- Strict old-format migration remains LegacyUnproven; missing/duplicate recovery fields, unknown scope, cross-job/instance/key/correlation, cycles, stale resource IDs and exhausted receipt capacity refuse before admission.
- Actual receipt publication after cleanup only: hold owner/claim cleanup, request stop and block or fail original WAL ACK. No terminal proof or re-admission until the real owner completes; sibling cleanup must still run.
- Exact original receipt consumed once/idempotently, wrong witness or unknown/pruned terminal leaves old state unresolved; late receipt cannot finish a new epoch.
- Live/same-process owner and process-identity reuse do not retire sessions. Crash after Begin but before admission remains distinguishable from a session that reached IMPORT.
- Persistence crash windows: receipt durable but app Finish absent can reconcile; app admission durable but guardian/binding absent stays conservative; torn/corrupt/identity-changed receipt rejects. No startup write to new-generation USB.
- ACL-read/stop/profile rules, old IPC DTOs and raw-v1 unavailable health remain unchanged unless explicitly versioned. No more free-form secrets in diagnostics.

### Required real positive and negative QEMU evidence

For a guardian path: stop/kill only the management agent after real I/O, verify the same original guardian remains to close the exact owners, and capture its real completion before accepting its durable receipt. Restart agent, query old session with scope-correct proof, start a new actual job and repeat original data bytes. USBFS requires exact old grant/root terminals and complete original config/allIF/alt/drivers, not new-null baseline. Virtual requires actual old listener/data stream termination and client VHCI cleanup as separate observations; no physical-server proof is invented.

Also kill the guardian before release receipt (must remain unresolved), restart controller while old owner is alive (must not duplicate claim), preserve another device during targeted stop, and test wrong/expired witness/no opt-in/external occupancy/new generation. A bounded syscall-entry gate is useful but does not prove kernel-internal cancellation. No arbitrary dead worker or PID disappearance alone counts as recovery success.

## G3 priority and remaining original scope

1. **Highest lifecycle blocker:** honest Rust restart coordination; current same-profile permanent conservative block needs an implemented proof path and fresh actual I/O, not a status-only change.
2. **More direct near-term functional gap:** bounded audit persistence/rotation, permission/retention/overflow policy and secret scanning (original4.11). N is reviewing this separately; it can make progress without deciding a guardian architecture.
3. **Other G3 release gates:** actual credential replacement/revocation semantics and tests (startup replacement versus live revocation must be explicit), remaining authorization/replay/concurrency/fuzz and deployment isolation matrices. The six positive TLS QEMU runs do not cover all certificate negatives.
4. **Other original gaps remain:** bandwidth/time-window reporting and realistic network/long-run evidence, hotplug/list refresh and full independent Rust export stop, G1/G2 real hardware/ISO/general configurations, TUI/GUI/install/systemd/performance/Windows. The user chose QEMU execution, not a redefinition of those requirements as completed.

Recommendation: retain full G3 scope; authorize a narrowly documented Rust negative + recovery-evidence design milestone, while implementing the direct logging gap if the proof architecture is not yet accepted. Do not spend another turn widening unrelated prepared-stop fixtures or call a generic PID-based cleanup rule the completed recovery feature.

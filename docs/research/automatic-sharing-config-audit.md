# Automatic sharing: configuration/authorization audit

Status: proposed alternatives, not accepted architecture or implementation. Read-only audit at clean `c4f72f6e02120487077733a4c6eb046094db3248`,2026-10-25. No repository/ELF/build/device changes; no new dependency or external-version claim. Evidence below is current project source, not runtime validation.

## Existing contracts we can reuse

- `crates/rustusb-service/src/config.rs:15,45,57,69,99,154`: Action is only Read/Start/Stop/Discover/Audit; Manifest is strict schema1,16 unique fixed profiles/64 operators, UID/profile/action ACL. Root only has implicit Read/Audit, not Start/Stop. Profile owns backend + root-protected TLS config path; RPC cannot choose either.
- `config.rs:194` loads every credential at startup for the actual dedicated nonzero broker UID; `:234` checks server key namespaces. `rustusb-transport/src/config.rs:14,37` holds immutable exact server device pool/client device + endpoint. `:104,131,554` exposes root-document loading capped64KiB with the existing protected-file trust path; no live multi-file reload guarantee.
- `rustusb-service/src/ipc.rs:218` derives caller UID from SO_PEERCRED; `rpc.rs:10,125,185` strictly decodes operation/envelope/result, IPC1, no paths/credentials/claimed UID. Unknown new operations fail closed on old agents. New authorized rule reads should follow the existing independent metadata path; denials remain audited.
- `runtime.rs:870` starts only after current Start authorization, healthy journal, UID/profile/request binding, no live/unresolved same-profile job, atomic intent/audit preflight/persist. It owns the runner after caller disconnects. Reuse this admission path rather than start a CLI subprocess or forge an operator RPC.
- `store.rs:26,49,97` currently has strict disk2 with exact disk1 decoder/migration;2MiB snapshot,1MiB new-admission budget,256 request records. RequestRecord currently means Start→job only. Rules/mutation dedupe/trigger occurrence data require an explicit new disk schema or a separately designed atomic journal—not optional silent disk2 fields or another unsynchronized writer.

## Candidate policy and GUI scope

Prefer explicit root-managed automation grants **plus** user-managed bounded rule definitions. Fixed admin rule enable/disable is useful but does not alone satisfy full rule creation/editing. A user may create/refine rules only within allowed profile/key/trigger bounds; never upload endpoint, TLS/helper path, credential, executable, arbitrary expression or owner UID. Manual Start permission is a prerequisite, not sufficient delegation of indefinite background automatic starts.

Two implementable authority envelopes:

1. Separate strict root `AutomationPolicy` loaded only by an explicit agent startup option. Keep Manifest1 unchanged; absent option means automation disabled. GUI discovers public grant metadata through new finite RPCs. This minimizes legacy DTO churn; service/packaging must provision the new startup option, and policy removal/change requires cooperative restart unless independently implementing reload.
2. New strict Manifest2 DTO + explicit frozen Manifest1 decoder, normalized startup policy, no implicit grants to old files. This centralizes admin configuration but changes public Manifest/LoadedConfig construction and client readers; cannot simply add optional authority fields to Manifest1.

Suggested shape (candidate, not existing API):

```rust
struct AutomationGrant { uid: u32, profile: String,
    triggers: BTreeSet<TriggerKind>, max_rules: u8 }
struct RuleDefinition { id: RuleId, revision: u64, profile: String,
    trigger: Trigger, enabled: bool }
// Caller UID comes only from OS peer; targets remain the complete fixed profile.
enum RuleRequest { List { profile: String, after: Option<RuleId> },
    Create { request_id: RequestId, definition: NewRule },
    Replace { request_id: RequestId, id: RuleId, expected_revision: u64, definition: NewRule },
    SetEnabled { request_id: RequestId, id: RuleId, expected_revision: u64, enabled: bool },
    Delete { request_id: RequestId, id: RuleId, expected_revision: u64 } }
```

Use separate typed mutation retry namespace or a strict tagged dedupe record: current Start RequestRecord cannot represent Create/Replace/Delete. CAS revision prevents silent concurrent overwrite. Bound grants/rules, bytes, typed conditions, mutation retention and trigger records explicitly; account them in existing admission reserve. Persist rule change before ACK. Background firing rechecks immutable grant + Start ACL, journals exact originating rule/revision/UID/trigger identity with the start intent, then follows original owner path. Existing `Job.operator_uid` says kernel-reported starting UID (`model.rs:180`): automation must explicitly record delegated enabling UID/origin, rather than pretend an internal synthetic call had that OS peer.

## Device/safety limits and failure modes

- `rustusb-core/src/device_key.rs:27,68`: exact backend-qualified fingerprint expresses admin intent, not cryptographic hardware identity. Scanner fingerprint hashes VID/PID/serial/topology (`rustusb-linux/src/lib.rs:493`), not Bus ID. `observation.rs:8,53` stamps remain observations; kernel can reuse identifiers. Evaluate only a fresh Current complete snapshot; revalidate through original helper on every mutation. Stale/Initializing/ambiguous/missing data must not fire.
- `rustusb-linux/src/lib.rs:124` checks all interfaces, storage drivers, block descendants and cached configurations; errors remain blockers. `rustusb-daemon/src/physical.rs:53` rejects exported/claimed/incomplete devices; `linux.rs:780` rejects ambiguous fingerprints. Rules cannot waive these. Remote descriptor presence is not proof of absent mounts/open handles; do not auto-import or reconnect storage, and reject unknown/incomplete safety classification.
- A server profile starts its **entire** configured pool. A UI condition selecting device A cannot silently start A+B; show full effect and require all configured targets satisfy the rule, or require administrator-created per-device profiles. Never implement rule narrowing by rewriting TLS config/helper pools.
- Persist occurrence identity/inhibition so repeated notifications, process restart, lost RPC replies, manual Stop and transient network errors do not create automatic restart storms. A live or Recovering old job blocks a fresh start; fresh discovery does not retire old resources. Disable/delete must distinguish preventing future triggers from requesting actual current-job cleanup; old root proof obligations remain.

## Open decisions and confidence

High confidence: current DTOs, authorization and safety checks (exact source above). Proposed: separate policy versus Manifest2, trigger language, rearm/startup-edge semantics, per-UID quotas and disable/current-job policy. These need a concrete implementation contract and positive/negative QEMU evidence: no-grant user, revoked policy on restart, stale generation, storage/composite blockers, duplicate trigger/lost ACK, manual stop inhibition, GUI close and agent restart. No performance/hardware/full Phase5 conclusion follows from this audit.

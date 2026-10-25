# Agent-owned automatic sharing rules

Status: **draft**,2026-10-25. Source review at
`c4f72f6e02120487077733a4c6eb046094db3248`; not implemented or accepted, and not
runtime evidence. Continues original requirements4.3/4.5/4.10 and Phase5.
Automatic rules must work after a GUI exits and remain subject to both ends'
administrator policy, exact original device/owner cleanup and storage restrictions.

## Decisions proposed from current source

| Area | Candidate | Source/reason |
|---|---|---|
| Authority |Separate strict root AutomationPolicy, explicit startup opt-in; absent policy disables automation|[Configuration audit](automatic-sharing-config-audit.md); preserves strict Manifest1 and never treats manual Start as indefinite background delegation|
| User control |GUI can create/replace/enable/pause/delete typed bounded rules inside root grants|An enable-only fixed auto-start switch is insufficient for the original rules goal|
| Execution |Agent-owned rule supervisor; reuse the original Runtime admission/runner and audit path|[Owner audit](automatic-sharing-owner-audit.md); no GUI loop, shell/CLI child or self-RPC with a forged UID|
| Persistence |Strict private disk3 migration; rule occurrence/origin and Start intent in one transaction|Current disk2/256 Start request records cannot permanently deduplicate triggers|
| Observation |One shared local monitor; remote discovery through the existing authorized two-slot supervisor|No per-rule monitor/task multiplication; a catalog missing a busy entry is not physical unplug|
| Backend effect |Explicit complete-profile rules plus a separately implemented dynamic admitted pool|Current Native/USBFS prepare fixed entire pools; one matching key must not silently activate extra devices|

The existing stable Rust/Tokio, typed IDs, checked store, discovery and GUI bridge
are reused. This design introduces no new library/version choice. The two
component digests document actual repository APIs and pitfalls; ordinary existing
Tokio usage needs no duplicate technology skill. Repository research/skill
conventions take precedence over creating unrelated per-component directories.

## Authority and public interface

Load immutable root-protected automation policy with an explicit agent startup
option. Use the same protected-file trust rules as other administrator documents.
No RPC receives a path, endpoint, credential, command, claimed UID or expression.
Policy change/revocation initially requires a documented cooperative agent restart;
live policy reload is a separate contract, never implied by editing a file.

A grant binds an actual operator UID to a configured profile, allowed typed
triggers/conditions, device-key constraints and quotas. Existing Read/Start/Stop/
Discover authorization remains necessary; the automation grant is additional.
Use existing OS peer identity for GUI rule mutation. Background execution uses an
explicit trusted delegation originating in that persisted authorized mutation,
not a claim that the agent's own socket peer was the operator. Preserve separate
automation-origin metadata instead of inventing a kernel-reported identity.

Candidate interfaces, **not existing API declarations**:

```rust,ignore
struct AutomationGrant {
    operator_uid: u32,
    profile: String,
    allowed_triggers: Vec<TriggerKind>,
    max_rules: u8,
}
struct RuleDefinition {
    id: RuleId,
    revision: u64,
    profile: String,
    trigger: Trigger,
    enabled: bool,
}
enum RuleRequest {
    List { profile: String, after: Option<RuleId> },
    Create { request_id: RequestId, rule: NewRule },
    Replace { request_id: RequestId, id: RuleId,
              expected_revision: u64, rule: NewRule },
    SetEnabled { request_id: RequestId, id: RuleId,
                 expected_revision: u64, enabled: bool },
    Delete { request_id: RequestId, id: RuleId, expected_revision: u64 },
    RetryMutation { request_id: RequestId },
}
```

Use strict finite DTOs, exact revision CAS and a distinct typed mutation retry
namespace. Old agents must reject unsupported operations; no silent downgrade.
Keep legacy Session/Metrics/Job DTOs and IPC1 meanings stable through independent
rule/origin metadata. Provisional limits:64 rules globally,4 per UID/profile,
16 rows/60KiB per page, one latest candidate and one admitted attempt per rule.
Final constants and admission byte reserves must be frozen with boundary tests,
not increased later to hide store growth.

## State, occurrence and actual ownership

Candidate rule states are Disabled, Waiting, Candidate, Admitting, Active,
Cooldown, PausedByUser, BlockedPermission, BlockedUnsafe and NeedsReconciliation.
Display condition, last actual trigger, origin request/job IDs, current owner and
independent operation/cleanup results. User preferences are separate from rules;
saving frontend preferences never authorizes background USB actions.

Within the existing Runtime, recheck the immutable grant and current Start ACL,
then atomically persist rule ID/revision, delegated UID, occurrence token, original
request/job IDs and Start intent before registering the actual runner. Reuse the
original session/attempt durability and root proof path. No second uncoordinated
store writer or metadata observer may own a USB capability. Rule mutations must
persist before ACK; renderer/RPC loss retains the same mutation identity.

Do not depend on Start's256-record retention for permanent suppression. Persist
bounded per-rule occurrence state and retain tombstones/old attempt association
until resolved. Unknown Start result queries only its original mapping/RetryStart;
expired or ambiguous state goes to reconciliation, never a fresh automatic Start.
Recovering/Unconfirmed old jobs block new attempts regardless of a new process's
empty resources or fresh catalog.

Manual Stop must atomically pause/suppress the associated current occurrence
before sending the original owner's cooperative stop. A finished job while its
condition remains true must not immediately restart against user intent. Disable,
delete and replace distinguish future-trigger prevention from requesting cleanup
of a current job; deleting a rule cannot erase its unresolved ownership history.
The GUI must make that distinction explicit and default destructive confirmation
to Cancel. Service shutdown blocks candidates, stops and truly joins observation,
discovery, actual USB/recovery owners and persistence; no abort or soft timeout
creates a terminal proof. Permanent OS/DNS/FS blocking retains existing limits.

## Device triggers and backend work still required

Local conditions use complete Current observations and exact configured stable
keys plus same-boot observation stamps. Stamps/Bus IDs are metadata; original
helper generation/fingerprint/storage checks remain authoritative at mutation.
Stale/Initializing/ambiguous/unsafe/incomplete input does not trigger. Remote
advertised tokens are explicitly catalog observations, not physical generations.
Every remote poll uses the bound operator's actual Discover authorization and
committed audit through Runtime; agent self-RPC would use broker SO_PEERCRED and
is unsuitable. Active ownership is not ended because its own busy device vanishes
from a catalog. Scheduling/backoff needs a tested finite fairness policy so
automatic polls cannot starve manual discovery.

Never auto-export/import/reconnect storage, including storage Composite interfaces
or unknown safety classification. A fresh nonstorage attempt requires prior
actual owner completion, Finished job and Verified/NotStarted old sessions.
Permission/configuration/unsafe/unknown outcomes do not loop forever. Startup
rearm, bounded reconnect policy and occurrence edge semantics must be explicit
in policy/UI and validated before being advertised.

Current Native/USBFS server Start prepares the **entire fixed pool**. A complete
profile rule therefore requires all configured targets and a confirmation showing
the full effect; a selected device row cannot narrow it. This is only an initial
automation increment. Full device-triggered sharing additionally requires a typed
dynamic pool path that admits a newly present, already administrator-authorized
key/generation to the same listener while a sibling stays connected. It must reuse
per-device durable session/attempt/export registration, exact helper resources,
local stop semantics and real close/Finish proofs. Removing one generation must
not stop safe siblings or silently substitute another device. Native and USBFS
need separate actual proofs; Virtual has no physical hotplug claim. These APIs
are not present today. Do not describe fixed-profile auto-start as completing
this dynamic device-rule requirement or all Phase5.

## Delivery and acceptance gates

1. Implement strict policy/config and disk migration, typed rule CRUD/retry/CAS,
   same-transaction firing, suppression, bounded scheduling and original actual
   owner shutdown. Unit/integration tests cover permissions, limits, stale tokens,
   lost ACK, restart, tombstones and unresolved old ownership.
2. Implement actual GUI rule editing/inspection/confirmation through shared
   service APIs; ordinary user, finite projection, original mutation identity
   across renderer loss, clear permission/safety/error and pause state.
3. QEMU complete-profile positive rules: GUI exit, actual add/reinsert, one durable
   admission and kernel I/O; duplicate hints, replacement at the same Bus ID,
   no-grant/revoked policy, storage/Composite/ambiguous/stale negatives.
4. Implement and validate dynamic admitted pools: insert a second device later,
   retain the original sibling's exact capability and continuing I/O, then remove
   only the target. Test each backend's actual supported scope explicitly.
5. Test manual Stop/Pause, mutation/prepare/WAL gates, exact-flow failure, verified
   release/fresh reuse, startup and expired dedupe without duplicate actions.
   Run root/GUI gates and original QEMU regressions; keep failures and real ELF/
   source/owner evidence. Four-platform software is not ARM USB/hardware proof.
6. Integrate policy provisioning/revocation, rule-state preservation/deletion and
   service ordering into the original `.deb`/systemd install/upgrade/uninstall/
   reinstall/reboot acceptance. Full G5, real hardware and later phases stay open
   until their own complete evidence exists.

All decisions above remain draft pending implementation/review and real evidence.
The original full objective is not replaced by these delivery increments.

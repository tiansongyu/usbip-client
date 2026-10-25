# Agent-owned automatic sharing rules

Status: **draft**,2026-10-25. Initial source review at
`c4f72f6e02120487077733a4c6eb046094db3248`. The [authorization/input foundation](automatic-sharing-foundation-validation.md)
is implemented and software-tested at `52fd127d`. The [fixed-profile runtime](automatic-sharing-runtime-validation.md)
at `20a15a01` adds persistence, CLI/RPC and actual background execution, with Native
local-rule QEMU evidence. Dynamic pools, physical remote safety receipts and GUI
integration remain unfinished; this overall design is not accepted or itself runtime evidence.
Continues original requirements4.3/4.5/4.10 and Phase5.
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

The overall design remains draft pending integration/review and real evidence.
The original full objective is not replaced by these delivery increments.

## Implementation contract: authorization and rule input foundation

2026-10-25, before implementation. The first implementation unit is a reusable
validation boundary in `rustusb-service::automation`; it does **not** add an agent
startup option, RPC, automatic runner or GUI enable control. Those are withheld
until the atomic journal/admission and actual owner paths are integrated. Existing
Manifest1, disk2, Job/Session/Metrics and IPC1 remain unchanged in this unit.

Choose the separate root policy alternative. Its strict schema1 contains only
`schema_version`, `broker_uid` and up to64 grants. Each grant binds one exact
`operator_uid`/`profile` pair, the complete fixed device-key set (1..16), allowed
trigger/rearm kinds and `max_rules` (1..4). Reject duplicates before normalizing
sets, unknown fields, wrong broker, mismatched credential kind/namespace, missing
or additional device keys and triggers unsuitable for the selected backend.
LocalProfileReady is limited to Native/USBFS; RemoteDeviceAvailable requires a
Client profile. Virtual server startup has no physical arrival condition.

The only public policy constructor reads through the existing root-document
trust path, capped at64KiB. No deserialization constructor can turn ordinary user
JSON into a validated policy. Loaded TLS inputs remain immutable and are not
cloned into the policy. Every rule authorization rechecks exact UID/profile,
policy binding, Read/Start/Stop and remote Discover as applicable. Loading a
policy after an operator ACL is revoked may still succeed; authorization then
fails, allowing the future service to report blocked rules instead of preventing
unrelated cleanup. Root gets no implicit automation delegation.

Rule input uses closed finite types: a canonical RuleId, a name of1..64 UTF-8
bytes without controls or surrounding whitespace, a trigger kind, a2..120 second
settle period, and an explicit Once or NewObservation rearm kind. No path,
endpoint, credential, executable, arbitrary expression or supplied operator UID
appears in a user rule. Create/Replace/SetEnabled/Delete use strict struct
variants; edits require a nonzero expected revision with room for its successor.
Mutation request identity and durable retry outcomes will be integrated with the
new journal in the next unit, rather than reusing Start's request namespace.

This is validation code, not a storage-safety classification: a grant or valid
rule never proves nonstorage, freshness, helper authority or cleanup. The future
scheduler must still reject unsafe/unknown candidates and implement all original
delivery gates above. Software tests for this unit must cover exact pool binding,
duplicate/unknown input, backend mismatch, revoked/foreign/root identities,
boundaries and strict rule mutation shapes. Actual QEMU automatic firing is not
claimed by these tests.

## Runtime integration contract (before implementation)

2026-10-25, source `595c3411`. This unit connects the validation boundary to
actual complete-profile automation. Full dynamic device pools, GUI editing and
installation remain required subsequent work, not replaced by these rules.

The first paired QEMU increment replaces only the server's manual Start with a
real local rule. Preserve the original three HID/FTDI I/O rounds, exact Root
proofs, two epochs and targeted-stop/recovery assertions. Keep the original
verifier's disk2 default; an explicit new server-only expectation requires the
entire strict disk3 shape and separately validates automation records. First run
the old no-policy denial checks, then stop/join that idle agent and enable a
root-owned policy. Create/Retry must use the original mutation UUID. Read back
automatic Start through RetryStart, never issue a second Start. A targeted Stop
must persist pause through restart; only an explicit CAS Enable may rearm the
second epoch. No fixture-generated start receipt may replace a real RPC reply.
This validates fixed-pool Native automation, not remote physical safety, dynamic
pools or physical hardware. Test CLI intent backpressure/signals separately
against a disposable metadata-only Unix peer; that peer is not a real service
commit or a USB test.

- Add explicit `agent run --automation-policy PATH`, loaded in the original
  retained startup task alongside fixed credentials. Preserve old service APIs
  with automation disabled. No policy means no observer or automatic admission.
- Disk2 stays byte-for-byte shaped as before when automation has never been
  enabled. Disk3 requires a strict nonnull automation journal. Freeze separate
  disk2/disk3 decoders; reject version/shape mixing, missing/null/unknown fields.
  Preserve strict disk1→2 migration and the existing atomic writer/trust checks.
  Enabling policy explicitly commits the upgrade before the listener is exposed;
  removing policy never downgrades disk3 or erases old ownership.
- Bound the journal to64 rules including unresolved tombstones,4 per operator/
  profile,256 mutation results,512 retained job origins and256 rule audit events.
  Keep the original2MiB completion/1MiB admission budgets and health reserve.
  Prune only resolved deleted rules; retain each live rule's last job, and all
  unresolved origin obligations. Historical resolved origins may outlive deleted
  rules. An evicted mutation result never authorizes a new retry mutation.
- Rule CRUD uses exact revision CAS and a separate request namespace. Persist
  successful edits and original outcomes before ACK. Retry only reads the
  original UID/profile mapping, without performing an edit or firing. A missing
  result is unknown/expired, not proof that a late original cannot commit. The
  frontend must retain the original UUID and never silently resubmit with a new
  one. Read-authorized owners may pause/delete after delegation is revoked;
  create/replace/enable and every firing recheck explicit policy and action ACLs.
  New CLI `agent rule` operations emit a complete bounded NDJSON intent/query
  receipt with the original UUID before any RPC dispatch, then the ordinary
  response envelope. This receipt is not a commit. Register INT/TERM first,
  retain/join accepted manifest loading, and do not dispatch after a preflight
  signal or incomplete/blocked intent output. After dispatch, interrupting the
  caller ends only its wait; an accepted service mutation may still commit.
  Report uncertainty with the original UUID and suppress generic extra JSON on
  this new stream. Preserve older Start and other agent command output formats.
- Extend RPC with independent finite Rules/MutateRule/RetryRuleMutation and
  RuleEvents variants. Rules and authorized rule-audit reads use a bounded
  immutable metadata registry outside WAL; denials remain durably audited.
  Positive rule audit has its own sequence/ring/schema and RPC. Do not add enum
  variants/fields to the frozen legacy audit/log/diagnostic schemas or mislabel
  a rule mutation as a device observation. Existing Start/Stop audit remains in
  the old path; rule firing also records its exact origin in the new journal.
- Admission reuses the original Start implementation: one transaction contains
  job, Start request, legacy Start audit, rule/revision/delegated UID/occurrence
  origin and rule audit. Only then register/spawn the actual original job runner.
  No live or Recovering profile job, unfinished old owner, or Unconfirmed old
  session can be bypassed by a fresh scan or new epoch. Same occurrence remains
  consumed independently of Start's256-entry FIFO. Explicit authorized edits
  can rearm; failed outcomes do not silently loop on the same occurrence.
  A local automatic server also waits for any live/unresolved local profile
  using the same fingerprint, including a Native/USBFS namespace overlap.
  This prevents simultaneous automatic intents for one physical candidate;
  original helper exclusivity and fresh mutation checks remain authoritative.
  After the Start WAL await, recheck observation age and the current local
  tuple. If it expired, or a Stop was already inherited before backend entry,
  the supervised task must finish with the existing State failure and no session
  or backend poll. Do not rely on a backend's cooperative preparation to mean
  "never started". Stops after backend entry keep its original actual cleanup.
  Hash explicit frozen primitive tuples, not an extensible DTO's serialization,
  so adding metadata fields cannot silently manufacture a new occurrence.
- Valid manual Stop sets a per-profile sticky suppression generation under the
  existing Controls mutex before cooperative fanout. Persist the profile's rule
  pauses with that Stop transaction. Late registered automatic jobs inherit
  suppression, while actual resource cleanup remains independent of WAL waits.
  Explicit enable/create may clear suppression only after its commit and only
  if no newer Stop arrived during that await. Disable/delete prevent future
  firings and preserve current-job cleanup history; they do not assert release.
- One local monitor is owned by a directly stopped and actually joined task;
  subscriptions/candidate maps hold metadata only. Only complete Current scans
  received within6 seconds can contribute to a2..120-second stable condition.
  Hash boot ID plus ordered exact keys and observation stamps, without serial
  or raw USB contents. Observer epoch/repeated hints are not new occurrences.
  Add a process-local monotonic scan-completion timestamp to the in-memory
  snapshot, leaving wire batches unchanged: arrival at a consumer after a WAL
  stall must not make an old completed scan fresh again.
  Identical periodic scans currently suppress wire revisions. Refresh their
  in-memory completion/counter metadata with the same revision and add a latest
  snapshot accessor; `next()` must still emit only changed revisions. Automation
  can then check actual source freshness without adding watch wire chatter.
  Local candidate checks retain the full existing scanner/helper storage,
  generation and all-configuration checks; helpers still revalidate mutation.
- Remote polls retain actual operator Discover authorization, original success
  audit and the existing two-slot owner path. At most one automatic poll is
  outstanding, preserving a manual slot; round-robin rules,2-second successful
  completion delay and exponential2..120-second failure delay are finite.
  Polls/observations never stop an already owned job merely because it is busy.
  Remote tokens describe advertised tuples, not physical generations.
  A remote settle window requires matching successful observations separated by
  the configured interval and no observed failure/change between them. It fires
  only on a fresh completed poll, using the completion time captured before any
  audit/WAL await. It does not promise uninterrupted availability between polls;
  queued or expired data cannot fire. This defines the formerly draft
  "continuously fresh" wording as an observation condition, without allowing
  rule count or one slow peer to make every other rule's settle timer restart
  forever. No speculative tick can turn a cached remote snapshot into a trigger.
- DEVLIST lacks all alternate/configuration safety descriptors. For this first
  runtime increment, only the fixed virtual HID/CDC profiles with matching known
  descriptor shapes can automatically import. Physical/Native remote keys report
  SafetyEvidenceUnavailable rather than inferring nonstorage from current class
  tuples. Full physical remote rules require a separately versioned authenticated
  safety receipt generated by the actual server's validated device owner, with
  old peers failing closed; this remains necessary work. Never call these initial
  rules complete physical remote automation or drop that requirement.
- Service stop/command EOF directly stop the observer and network owner trees,
  then actually drain observer/discovery/USB/recovery tasks. No abort or timeout
  is a join or cleanup receipt. Metadata failures block automation without
  inventing device absence; permanent OS/DNS/FS blocking keeps prior limits.

Tests must cover strict migration and real atomic files, CRUD/retry/CAS/quota/
ownership/revocation, same-transaction origin, duplicate/stale/unsafe input,
manual Stop during a real store gate and late registration, pending mutation
and actual-owner shutdown. QEMU must validate actual rule-triggered Native
export/virtual import, real I/O and exact cleanup, followed by original
regressions. No runtime or QEMU success is claimed by this contract.

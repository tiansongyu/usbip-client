# Settled automatic-rule mutation results

Status: implementation contract,2026-10-25, before source changes. Continues the
full original Phase0–7 objective and [automatic-rule design](automatic-sharing-design.md).
[E55](gui-automatic-rules-validation.md) deliberately retains generic Error/
Expired identities because `Runtime::persist` can return a registry error after
committing. A real conflict must become recoverable without treating an uncertain
write as a proven rejection. This contract is not validation or complete Phase5.

Reuse existing stable Rust/Tokio, strict serde types, root-document loader,
original atomic Store writer/actor, real OS peer and shared frontend. There is no
new external dependency/technology choice or USB owner path. Existing source
[audit](automatic-sharing-config-audit.md) and [owner audit](automatic-sharing-owner-audit.md)
cover these institutional components; no duplicate technology skills/delegation.

## Explicit version and authority

Keep `AutomationPolicy::read` and its schema1 strictly unchanged. Add a distinct
trusted `read_decisions` entry and agent `--automation-decisions-policy` startup
option, mutually exclusive with `--automation-policy`. The new root document has
strict schema2, the same broker/grants plus required `settled_rule_results:true`.
All old full-pool/backend/UID/Read/Start/Stop/Discover checks remain. No user JSON
constructor, implicit root grant or ordinary preference authorizes it.

Only this explicit option/document can initially upgrade application disk2/3 to
strict disk4. Preserve complete frozen disk2/3 shapes. Disk4 requires automation
and a nonnull independent `rule_rejections` journal; reject missing/null/unknown/
version-mismatched shapes. Reopen disk4 without policy without downgrading or
resetting history; old agents refuse it. Policy1 alone still creates disk3.
New grants do not erase an earlier request's settled decision.

## Durable decision model

Keep the existing positive RuleJournal/MutationRecord shape and RuleEvent enum.
Add a bounded rejection journal with monotonic sequence and at most256 entries.
Each exact record contains sequence, original request UUID, actual UID, fixed
profile, complete typed original RuleMutation and fixed Failure category. It has
no arbitrary error message, path, credential or USB data. Validate bounds, ordered
sequence, canonical types, unique request IDs and disjointness from positive rule
mutation IDs. Start's namespace remains independent. Journal bytes stay within
existing2MiB/1MiB admission limits; never evict live owner/proof to fit a receipt.

A Read-authorized new decision request first checks original positive/negative
history and exact UID/profile/payload binding. A replay returns the original
result and never edits or writes another rejection. Foreign identity or payload
mismatch cannot overwrite a retained decision. Query is readback only; eviction
or an unknown identity returns an explicit expired result, never reapplication.

Reuse the actual original `mutate_rule` operation. After its actual return, inspect
authoritative in-memory committed positive history. A present exact record means
Applied even if registry/control publication subsequently failed. If no positive
exists and the journal is unhealthy/uncertain, preserve generic Error and original
identity. Only a completed precommit failure with a healthy journal and no original
positive can be recorded as Rejected in a cloned state and persisted by the same
actor/writer before ACK. A writer error with no authoritative committed rejection
is unknown; it must not produce a rejection merely because preparation failed.
A real committed rejection still resolves if a later registry publication fails.
Read denial remains the original durable permission path, without leaking another
UID/profile's decision. A Read-authorized Permission rejection records the original
Denied audit exactly once in its same transaction; query does not duplicate it.

Existing legacy mutation/retry operations keep their reply shapes. They consult
negative history to prevent reuse of an ID previously rejected by the new path.
The new opt-in RPC operations have their own closed support, decide, query,
Applied/Rejected and Expired responses; no fields are silently added to old Rules,
Job/Session/Metrics/audit/log/diagnostic or USB/TLS wire shapes. An independent
Read-authorized support query is metadata, not delegation. Disk4 determines
retained decision support; current root grants still determine every edit.

## Frontend and CLI

The ordinary-user GUI discovers support while Settings is requested. Legacy
policy1 returns explicit false and keeps the original conservative behavior.
An old/unavailable peer that cannot answer support is read-only for new GUI
edits until refreshed/upgraded; original retained queries remain available.
Do not silently select a different protocol after an uncertain capability read.
Known unhealthy journals block new GUI edits before minting an identity; original
queries and independent cooperative Stop remain available.
Save the selected
query protocol with the original UUID/profile/payload before issuing IPC; a later
profile/capability change must not redirect its query. Only an exact original
Applied or durably Rejected reply clears that in-process intent. Generic Error,
Expired, wrong identity and uncertain persistence remain blocked/query-only.
Positive receipts also bind their exact rule/revision/deleted effect to the
original typed mutation; malformed zero-sequence/Persistence refusals cannot
clear an identity.

Keep a bounded last actual decision and original payload in Rust so a renderer
reload can inspect/restore a rejected Create/Replace draft. Restore does not
rebase its revision or submit it. A settled rejection requires inspecting fresh
state and a new explicit default-Cancel user decision. Start/Stop remain separate;
future-only Disable/Delete never claim old job cleanup. Whole-process pending-ID
persistence and complete GUI disclosure/accessibility coverage remain separate
unfinished requirements, not permission to clear uncertainty on restart.

CLI exposes explicit settled-protocol mutation/query selection and prints the
complete original identity/protocol intent before IPC using the original signal/
backpressure ownership path. Legacy command frames/behavior remain unchanged.
No caller-chosen UID, endpoint/path/key, arbitrary RPC or shell command is added.

## Verification

Test strict root1/root2 and disk2/3/4 migrations/shapes, real atomic reopen,
positive/negative dedupe, foreign UID/profile, mismatched payload, bounded eviction,
CAS/capacity/permission rejection, unhealthy and postcommit publication failures,
old protocol interaction, actual actor/Store task joins, exact frontend query
protocol/identity and GUI draft/revision recovery. Preserve original assertions.

Use actual native UID1000 QEMU GUI for a stale service CAS: pause frontend automatic
refresh after observing revision2, let an actual CLI advance it to3, then submit
the original revision2 through the still-valid observed bridge page. This reaches
the real agent CAS instead of being rejected only in JavaScript. Stop the guest
agent around dispatch to lose the actual IPC reply and reload the actual renderer;
resume it, verify exactly one late durable rejection/no original edit, cooperatively
restart the agent, then query only the original UUID from the same GUI. Restore
and inspect the rejected draft explicitly, read latest3, and continue actual
positive CRUD/automatic jobs/Stop pause/I/O/native close with the original Native
264/kernel/Root proofs. No synthetic result or OS-peer proxy. Add permission and
other negatives with exact independently expected audit/source scope.

Run both address families, full unchanged root/separate GUI gates and relevant
legacy policy1 GUI/rule/Native/v2 regressions at frozen clean source/ELFs. Archive
failures and actual waits; guest0 alone is not a verdict. Hardware G1/G2/fullG3,
dynamic pools, physical remote safety receipts, install/upgrade/uninstall/reboot,
storage/ISO/performance/Windows and all other original work remain incomplete.

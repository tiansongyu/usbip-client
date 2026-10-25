# Native GUI automatic-rule editing

Status: implementation contract, 2026-10-25; continues original Phase5 and
requirements4.10. This document is not validation or complete GUI acceptance.
Read with [GUI ownership](native-gui-design.md),
[automatic sharing](automatic-sharing-design.md) and its two source audits.

Reuse the existing locked Tauri2.12.5, shared frontend worker, checked IDs,
RuleSpec, RuleMutation and authorized agent IPC. No new dependency, technology
choice, helper operation, renderer-supplied endpoint/path/key/UID or generic RPC.
The existing source audits and GUI research cover these institutional components;
no duplicate component skills or research delegation is necessary for this glue.

## Model and admission

The Settings page contains a separate automatic-sharing section. Opening it asks
the shared worker for independently qualified Rules and RuleEvents pages (at most
16 each). Keep their cursors/freshness independent of jobs/sessions/legacy audit.
Only sample them while requested; TUI defaults and its existing RPC behavior stay
unchanged. Display policy absent, no grant, permission failure, unavailable/old
agent, retained stale data and actual rule status distinctly. Show the complete
grant device pool, permitted triggers/rearm and quota. These metadata do not
authorize the renderer; every actual request is checked by the service again.

Add closed GUI CreateDisabled/Replace/SetEnabled/Delete/Resolve actions.
Deserialize RuleSpec with its original UTF-8 byte/control/settle bounds. Revisions
are canonical positive decimal strings through JavaScript and checked u64 /
ExpectedRevision in Rust; no Number conversion. Existing rule mutations must match
an exact current page identity/profile/revision before queue admission. Renderer
reload cannot return the one accepted-action slot before the worker ACK.

Retain a distinct original rule-mutation UUID, profile and complete typed payload
in Rust before issuing IPC. Never reuse the Start namespace or let Stop clear it.
New edits are blocked while this identity is unresolved. Resolve sends only
RetryRuleMutation for the original profile/UUID, including after profile changes,
transport loss, renderer reload, Error or Expired. Only an exact original Applied
reply clears it. A refusal retains the draft and identity: current generic Error
does not distinguish a CAS conflict from every post-persistence failure. Do not
invent a negative durable receipt or silently mint a new UUID. This conservative
limitation must be visible and documented; protocol-level settled negative
receipts/reconciliation are separate unfinished work. On frontend exit report
any original unresolved rule identity after joining actual local work. Identity
retention is in-process, not a new crash-persistent authority-bearing preference.

## Interaction

Keep six pages and existing slate/cyan console tokens. Create defaults disabled.
All submitted edits use default-Cancel confirmation describing complete-profile
effect; Enable explicitly rearms future consideration. Disable/Delete affect
future intent only. Show paused-by-user independently from last active job and
origin; job/session stop and actual verified release remain separate actions.
Show independent rule events, retention gap and last-operation result. Preserve
draft values, their original revision and focus across automatic refresh/profile
changes; never silently rebase an edit. A profile change requires choosing a new
draft. Validate inline with a focusable error summary/announcement, textContent
only. Keep current projection512KiB/detail64KiB and exact string-form counters.

## Verification

Add shared-state tests for unknown/expired/mismatched responses, separate Start/
Stop identities, profile changes and exact payload retention; bridge negatives
for malformed/large revisions, stale/unobserved rules and unauthorized shapes.
Run unchanged complete root and separate GUI gates at a clean frozen commit.
Use actual native UID1000 WebKit in disposable QEMU for disabled Create, Replace,
Enable/Disable/Delete, event/origin readback, draft/focus refresh, renderer reload,
permission/refusal, original UUID query and WM_DELETE_WINDOW/real wait/join while
the agent remains independent. Preserve original Native I/O/kernel/root proofs
and original regression assertions; no synthetic successful service receipt.
For the lost-ACK test, stop only the disposable guest agent after the native GUI
has read its grant. Issue a disabled Create, retain the pending Rust slot through
actual WebDriver renderer refresh, and wait for the real five-second IPC timeout.
Resume the same agent and observe its late real commit; native Resolve must only
query the original UUID and leave its journal unchanged. A failure trap resumes
the stopped guest agent before original cleanup. Do not proxy OS peer identity.
Concurrent CLI revision advancement tests stale-draft rejection before dispatch;
it is not a claim to have deterministically raced the GUI against service CAS.
Capture Delete while its old job is actually running. After the original owner
cleanup, the existing journal intentionally retires a resolved deleted record;
verify its retained mutation/origin/event history and exact cleanup instead of
requiring that resolved tombstone to remain forever.

Archive failures and source/ELF provenance. Hardware G1/G2, dynamic pools,
physical remote safety receipts, settled-negative edit recovery, packaging and
the remaining Phase0–7 objective stay unfinished.

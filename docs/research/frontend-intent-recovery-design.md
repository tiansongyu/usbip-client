# Frontend request recovery across process restarts

Status: implementation contract written before source changes,2026-10-25.
Continues the full Phase0–7 objective and requirements4.5/4.9/4.10. E56 is verified
progress at clean650c53a8; its [report](settled-rule-decisions-validation.md) retains
a whole-frontend-process persistence gap. This document is not validation or full
Phase5 acceptance.

## Reuse and boundaries

Reuse the shared frontend worker, root-loaded Manifest, original authenticated
IPC, typed UUIDs/RuleMutation/RuleDecision and checked preferences file primitives.
The existing [GUI contract](native-gui-design.md), [settings filesystem contract](tui-settings-actions-design.md),
[rule contract](gui-automatic-rules-design.md), [decision contract](settled-rule-decisions-design.md)
and [authorization](automatic-sharing-config-audit.md)/[owner](automatic-sharing-owner-audit.md)
audits supply institutional source context. Existing locked SHA-256 names files;
it supplies no authentication or USB authority. No new external dependency,
backend/helper operation, service disk/RPC schema or component skill is selected.

Preserve both UUID-bearing namespaces: one unresolved Start and one unresolved
rule edit, plus one last exact rule decision/original draft. Stop remains an
explicit exact-job/session operation, independent of these slots. StopAccepted
is not release evidence. No frontend file holds a USB handle, delegation, private
credential or replayable command. A rule payload is retained for display and
explicit draft restoration; recovery never dispatches that payload.

## Private state and concurrent frontends

Use `$XDG_STATE_HOME/rustusb`, otherwise absolute `$HOME/.local/state/rustusb`;
ignore relative/empty values. This is separate from preferences and
`--no-preferences`. Without a usable checked location, observation, original
queries when retained and independent Stop remain available, but new Start/rule
edits are unavailable. Native GUI and TUI use the same shared worker/store.

The [XDG specification0.8](https://specifications.freedesktop.org/basedir/0.8/)
places restart-persistent local state here. Reuse descriptor-relative ancestor
walking and caller0700/0600/single-link/NOFOLLOW/NONBLOCK/identity checks. Factor
these existing primitives for both preferences and recovery, preserving the
original strict preferences document and regression tests.

Derive a bounded filename from a fixed domain tag and the loaded Manifest's
control socket path, not renderer input. Store schema1, actual operator UID,
expected broker UID and required nonnull closed slots for pending Start, pending
rule and last rule receipt. Cap the document at16KiB. Validate profile syntax,
canonical IDs, original protocol, typed payload and exact outcome/rejection
invariants; reject unknown/missing/null/oversize fields and conflicting receipt/
pending identity. A profile removed from the current Manifest must not silently
erase its old intent; original queries still use that retained profile and the
current trusted endpoint, with real agent authorization.

A stable per-endpoint lock uses a freshly opened OFD/nonblocking exclusive flock
held for the actual worker lifetime, with CLOEXEC and current lock-file identity
checks. A second frontend may observe and request exact Stop; it cannot create
new UUID work or clear the original writer's recovery file. It may read retained
identities and explicitly query them, retaining local slots if it cannot commit
a matching local update. Different endpoints remain independent. Broker/UID
mismatch, malformed files, directory replacement or unexpected previous bytes
block writes and do not reset/overwrite records.

[flock(2)](https://man7.org/linux/man-pages/man2/flock.2.html) binds the lock to the
open description; copies share it and closure releases it only after all copies
close. Keep the actual file in the owned store through blocking tasks. Never use
PID death alone to clear an intent, delete a lock to unlock it, or dupe an OFD to
claim a second independent writer.

## Dispatch and settlement order

1. Validate current action and local recovery availability before a fresh UUID.
2. Construct and retain the exact profile/UUID, and rule payload/protocol if any.
3. In an owned blocking task, validate the old checked file/lock/directory, write
   an EXCL temporary, sync it, compare previous identity/bytes, rename and sync
   the directory, then recheck the published identity. Only a successful actual
   return permits the new IPC dispatch. Publish the retained identity to the UI.
4. If persistence fails or shutdown arrives before dispatch, issue no new IPC.
   Preserve any uncertain stored identity; never roll back a published file by
   deleting it. Mark local recovery unavailable for new edits until repaired and
   reopened. A saved intent is not evidence that it was sent or committed.
5. Await every issued IPC through shutdown. Apply only a matching actual result.
   Started must come from the exact original Start/RetryStart request and match
   the retained profile and actual UID. Unrelated Stop replies cannot clear it.
   Rule settlement keeps E56's exact protocol/UUID/revision/effect/refusal checks.
6. Commit the resulting pending/last-receipt state through the same blocking
   writer before clearing the UI slot. If local settlement recording fails,
   retain the original identity and report the actual agent outcome separately
   from local recording failure. Another explicit original query can recover it
   after restart. Generic Error, Expired and unexpected responses keep it.

[fsync(2)](https://man7.org/linux/man-pages/man2/fsync.2.html) distinguishes file
sync from directory-entry sync; both are used. The original checked atomic writer
preserves unexpected files and reports post-rename uncertainty. All accepted
blocking tasks are actually joined; permanent filesystem blocking still delays
exit. Process-kill recovery does not prove power-loss behavior on every filesystem
or defend against arbitrary hostile code under the same UID.

## Restart and interaction

Load and qualify private state before observers/actions. Restore both original
slots and the last local receipt/draft, independently of current profile, renderer
reload and service epoch. Never issue Start/DecideRule/MutateRule automatically.
An operator confirms Query original; it sends only RetryStart or the original
rule query protocol, even if current support/policy/profile has changed. Expired
or uncertain results keep new same-namespace work blocked. No new UUID, rebase,
background retry, record purge or implicit authorization follows from restart.

Keep the existing six pages/tokens, default-Cancel confirmations and focus/input
preservation. Add independent local recovery status to the bounded snapshot,
System/Settings and TUI. Explain missing storage, busy other writer, malformed
state and local save failure, with concrete repair/restart guidance. Disable new
Start/rule controls consistently in JS, Rust bridge and worker; original queries
and independent Stop remain usable. Restored last Rejected draft still requires
the E56 explicit default-Cancel action and original revision. Locally retained
receipts are user metadata, not independent authoritative cleanup/audit evidence.

## Verification contract

Keep all original gates/verifiers. Unit tests cover strict byte/type/trust/identity
bounds, original pending and last-draft roundtrips, per-endpoint exclusion, changed
broker/previous-file refusal, real child kill/reap/reopen, failed publication and
matching Start result admission. Preserve original preference tests. Bridge tests
cover local unavailable/busy admission and original query/Stop independence.

At a clean frozen source/three ELFs, run complete root and separate GUI gates.
Use actual native UID1000 WebKit in disposable QEMU on both families: pause the
actual agent, submit a Start/rule, capture the checked file and original UUID,
SIGKILL/reap the actual GUI process (not just renderer reload), resume the same
agent and observe its actual late commit/refusal. Relaunch the actual GUI,
verify original identities/payload/protocol and explicit query without new
mutation/audit, then actual close/join and original Native HID/FTDI I/O/264/full
Rootproof. Include a settled refusal surviving agent restart and a separate
frontend-store failure/competing writer admission negative. Preserve existing
rule/GUI actions/link/single regressions and every actual failure/provenance.

HardwareG1/G2/fullG3, dynamic device pools, physical remote safety receipts,
complete GUI disclosure/a11y/error-state coverage, packaging/install lifecycle,
performance and Windows remain required original work. This increment must not
be labeled a full phase or general crash/USB driver recovery implementation.

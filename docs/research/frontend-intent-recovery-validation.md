# Frontend process-restart request recovery: validation

Local result: **PASS**,2026-10-25. Product and complete software gates are clean
`f174f434f7e86659692226fcd9b7e43b87a57e15`. Final QEMU uses clean
`10117a0e83dab40df1a102bda604df9b6b60ff10`; only the new fixture's `.py`/`.sh` changed
since the gate. Product Rust/JS/manifests/locks/three ELFs are unchanged between
these revisions. [Contract](frontend-intent-recovery-design.md),
[operator guide](../frontend-recovery.md). This is scoped progress toward the
original Phase0–7 objective; it does not complete a phase.

## Implemented behavior

The shared GUI/TUI worker retains one original Start, one original typed rule
edit/protocol and one last exact rule receipt/draft in a strict bounded private
state document. It uses XDG state separately from preferences, a stable trusted
endpoint digest, actual UID/broker binding and a fresh per-endpoint lock OFD held
through the real worker lifetime. Required nonnull closed slots reject missing,
null, unknown, mismatched, oversized or invalid records. Removed/renamed current
profiles do not erase their old identities. Another writer or invalid storage
makes new UUID work unavailable, while observation, original queries and exact
Stop remain independent. No file confers service/helper/USB authority.

Both users of checked preferences/recovery share the original descriptor-relative
0700/0600/regular/single-link/NOFOLLOW/identity and atomic publication primitives.
Existing strict preferences and their six tests are retained. A new intent is
actually written/synced/renamed/directory-synced before IPC; a confirmed result is
locally recorded before clearing its UI slot. Recording uncertainty preserves
original identity and reports local failure separately from the actual service
result. Every accepted blocking task and issued IPC is truly awaited on stop.

Restart restores metadata and sends no automatic mutation. The operator confirms
only the original query, retaining UUID/profile and rule protocol regardless of
current selection or service epoch. Started must match the actual original
Start/RetryStart and retained profile/actual UID; unrelated Stop replies cannot
clear it. Rule settlement preserves E56's exact outcome/refusal checks. The last
settled rule payload can be explicitly restored with its original revision and
default-Cancel confirmation. Unconfirmed form edits are not a saved intent.

There is no new external package or privilege operation; agent disk/RPC/helper/
TLS versions remain unchanged. Adding the frontend's dependency on existing sha20.10.9 changes only
that internal package block in each lock. Offline locked Cargo metadata reproduces
both [inventories](../evidence/frontend-intent-recovery-20261025/dependency-review.json)
byte-for-byte: root222 and GUI475 packages, no missing license metadata. This is
metadata checking, not a full license/security audit.

## Complete software gate

Unchanged `scripts/check.sh` and separate `scripts/check-gui.sh` pass at f174f434:

| Gate | Passed executions | Failed / ignored |
|---|---:|---:|
| Root default/all features, rustdoc and original vendor tests |1,076|0 /0|
| Separate GUI and original GLib regression |18|0 /0|

These1,094 are summed gate executions, not unique USB cases. All428 inputs,
HEAD/clean guards and actual release files are checked. The original34 GLib
warnings are retained without suppression or version/backport changes.

| Executed release file | Bytes | SHA-256 |
|---|---:|---|
| rustusb |22,447,632|`54a67744f657fa79f4c184341db77f89affda88a35c470e62f5225ed92259bd8`|
| rustusb-helper |7,525,544|`475b2f97e8b4e31dd1af479193913c637d09dc8c3b1debd13442c0f88f6748f3`|
| rustusb-gui |17,897,832|`5c9ec587bf2bcb385da6af26824567572f094cbc000c9a472b7787ce6bef9ba9`|

Focused development checks include23 shared-frontend/9 TUI and18 GUI tests,
all-feature Clippy, strict/cross-endpoint records, real child SIGKILL/reap/reopen,
independent lock descriptions, conflicting old files and matching Start replies.
Original CLI metadata-adapter five-process checks also pass: unknown/query/blocked
timeout exit1, blocked INT/TERM exit0, with no RPC after incomplete output. Those
adapter checks do not establish an actual agent commit or USB I/O.

## Final frozen QEMU matrix

All16 final invocations pass at10117a0e,31 guests actually exit0, no forced
termination and all applicable source/head/clean/CLI/helper/GUI/driver guards true.
The first final IPv4 run remains its own actual invocation; the matrix explicitly
references it rather than pretending to execute it twice. Later independent
invocations run at most two at a time and are all actually awaited before edits.

| Invocations | Original/additional evidence retained |
|---|---|
| New process recovery, IPv4 and IPv6 |Each original264 +46 GUI +27 settled-decision +78 recovery checks; three actual Native HID/FTDI I/O rounds|
| Original E56 settled rules, IPv4 and IPv6 |Actual stale CAS refusal, renderer reload, original query after agent restart and old CRUD/I/O|
| Original E55 legacy rules, IPv4 and IPv6 |Strict disk3 and original conservative legacy query/CRUD/owner proofs|
| Original GUI actions, IPv4 and IPv6 |Confirmed fixed-profile Start/exact Stop, remote catalog, window joins and actual I/O|
| Original GUI v2 link, IPv4 |Three directional15-second faults, seven I/O rounds, packet/lifecycle/sibling/full Root proofs|
| Original single GUI |Actual read-only/hotplug/preferences/unknown-ID contract; original expected frontend exit1|
| Original TUI actions, IPv4 and IPv6 |38 actual product PTYs; preferences/errors, confirmed Start/Stop and original Native proofs|
| Original TUI unknown outcomes, IPv4 and IPv6 |38 PTYs; same real broker pause/timeout/original query and current/stale/current exact job|
| Original basic TUI, IPv4 and IPv6 |20 PTYs; default Cancel, navigation, resize, signals/termios and original Native proofs|

The three existing PTY drivers now explicitly set a checked test
`XDG_STATE_HOME`, because PID1 supplies no ordinary user's HOME. Their original
assertions are preserved; no record is reset between product PTYs. This fixture
change is included in f174f434 and the full gate's source set.

Actual final native process accounting is60:51 cooperative exit0,8 deliberate
SIGKILL/reap137 and the original single expected unresolved exit1. Actual TUI
accounting is96 product PTYs, all wait0. These counters are separate from guest
exits, Cargo tests and USB operations.

## Actual process-crash sequence

Each new paired run executes four deliberate native process crashes:

1. Client HID Start: the real broker is SIGSTOPped; the actual GUI confirms Start,
   retains the original UUID and completes its private write. Capture the native
   pending view and checked file, SIGKILL/reap that actual GUI137, resume the same
   broker and observe exactly one original durable request. A distinct new GUI
   restores the UUID, confirms only RetryStart and writes its cleared local slot.
   The entire agent journal is unchanged by the original query. Normal native
   close/join occurs before original Native enumeration/I/O and cleanup checks.
2. Server disabled Create: preserve E55's true5-second timeout and renderer reload,
   then kill/reap the whole Rust GUI before late original commit. Relaunch a new
   native process and resolve its exact UUID/typed payload/protocol. No second
   Create appears; original rules/events/Start ownership remain unchanged.
3. Server stale Replace: preserve E56's observed revision2 versus actual service3,
   actual timeout/reload and durable refusal. Kill/reap its GUI, cooperatively
   stop/wait/restart the real agent, then relaunch and query the original refusal.
4. After that exact refusal has also been locally recorded, kill/reap the GUI
   again. A new native process restores the last local receipt and exact issued
   draft. Default-Cancel Restore keeps the original profile/revision2; the stale
   draft is still rejected before a later explicit latest edit.

All ten native processes per pair have actual UID1000/capability/ELF and real
WebKit-driver/session receipts. Six normal windows use actual WM_DELETE_WINDOW
and join their local workers; four137 exits are planned crashes, not cooperative
cleanup or forced failure teardown. Endpoint digests are additionally checked
against captured actual public manifests; the client broker's stopped state and
UID2000 are checked in its original proc files. This is same-author review of
original bytes, not an independent security audit.

The client also opens a second actual native GUI while the first owns its lock,
and another with an invalid private-directory mode. Both observe/read independently,
show disabled Start, and receive the real Rust bridge refusal before UUID/accepted
command creation. Their complete agent journals are unchanged. Actual Stop remains
covered by the unchanged action/TUI suites and independent bridge/worker paths;
no new native Stop-during-post-ACK-filesystem-failure scenario is claimed.

## Original failures, evidence and remaining scope

The first two new IPv4 invocations remain **FAIL**, each with two actual guest
exit0, no forced termination and all six guards true. Server crash/recovery steps
ran, but the client stopped inside the inherited `gui_open` helper: it required
Start enabled, contradicting the new read-only negative's required disabled Start.
The second run's preserved last actual HTTP body is false. Only the new fixture
adds an explicit read-only mode that requires disabled Start; every normal call
retains the original enabled predicate. A new clean run is required and passes.
No earlier verdict is overwritten or reclassified. Development TUI-fixture and
GUI-macro compile failures, patch/lookup mistakes and exact later results remain
in the work log and available original logs.

[Metadata](../evidence/frontend-intent-recovery-20261025/validation.json) and
[manifest](../evidence/frontend-intent-recovery-20261025/archive-manifest.json)
cover13,064 original members/155,701,511B, compressed36,255,107B, SHA-256
`b847ae55a46d0851004bb9f9b4b65915b671a22f6eb0ffb17b92abe642c5894c`.
Each member round-trips by bytes/SHA; source files map to each actual Git revision.
Private keys, executable/rootfs payloads and raw host USB inventory are excluded.
Methods and original summaries/logs accompany the archive.

The actual [later rule view](../evidence/frontend-intent-recovery-20261025/native-recovered-rules.png)
and [deleted-intent view](../evidence/frontend-intent-recovery-20261025/native-recovered-deleted.png)
are unedited1180×760 native screenshots inspected at their actual scroll positions.
They show later positive/deleted states and the new recovery status, not the
intermediate refusal or a complete accessibility/pixel matrix. Crash/refusal/
restoration claims use actual native DOM/IPC/process/file/confirmation evidence.

Unknown/expired/evicted results, including legacy Start refusals without durable
negative receipts, remain conservative and cannot be safely force-cleared here.
Unconfirmed renderer drafts, arbitrary power-loss/shared-home filesystems, hostile
same-UID tampering, all post-ACK file-failure/live-stop combinations and installed
reboot behavior remain unproven. Dynamic pools, physical remote safety receipts,
complete GUI disclosure/a11y/error states, `.deb`/systemd/install/upgrade/uninstall,
hardwareG1/G2/fullG3, storage/ISO/performance and Windows remain original required
work. No complete phase is declared. CI/Pages receipts follow only after actual
remote success and retain their own content revision.

## Documentation/showcase review

Static QA checks428 software inputs against f174f434 with exactly two fixture
files mapped to10117a0e, all three ELFs, eight site files (only index copy changed),
four unchanged global README-skill files and its Codex symlink. Local Markdown/
HTML targets, actual release CLI/TUI/GUI help and unchanged site JavaScript pass.
[Browser review](../evidence/frontend-intent-recovery-20261025/browser-review.json)
records1280/390 views without horizontal overflow, actual2px focus on the new
report link, mobile SVG selection and all images loaded after actual scrolling
to the native-GUI section. The initial below-fold image was correctly deferred.
The390px CSP no-script view retains all three static panels, new copy and build
commands. Three owned tabs close, viewport resets and the actual preview server
joins after SIGINT/exit130. No new clipboard/motion or full accessibility claim.

## Published CI and Pages receipt

Content head `1dde4f15d619a5bd42813cf0194207222b9ae5f7` is normally pushed to
`main`. [Core CI](https://github.com/tiansongyu/usbip-client/actions/runs/40179821668)
and [GUI CI](https://github.com/tiansongyu/usbip-client/actions/runs/40179821669)
each complete four native Ubuntu22.04/24.04 x86_64/aarch64 jobs successfully,
including the actual architecture/environment and full gate steps.
[Pages](https://github.com/tiansongyu/usbip-client/actions/runs/40179821682) check and
deploy succeed. [Original receipt](../evidence/frontend-intent-recovery-publication-1dde4f15-20261025/receipt.json)
retains29 API/HTTP/method files,371,538B, with exact member hashes.

All seven canonical HTTPS resources return200 and match Git bytes. The428 frozen
inputs map exactly to f174f434/10117a0e (only the two new fixture files advanced),
and the three ELFs, eight site files, four global skill files/symlink remain exact.
No CI logs, executed CI ELF sampling or per-platform test recount is claimed.
Local software belongs to f174f434, QEMU to10117a0e, CI/Pages to1dde4f15; the final
receipt-only commit is separate and does not change those assignments. Remaining
full-objective limitations above stay open.

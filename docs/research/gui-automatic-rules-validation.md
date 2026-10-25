# Native GUI automatic-rule validation

Local result: **PASS**,2026-10-25. Product and complete software gates are locked
to `7ef7f4923dc97bef98a971b1fc248e36cf670a67`; final QEMU fixtures are locked to
`e668c045` with byte-identical product sources, manifests, locks and three ELFs.
The intervening commits change test synchronization and a verifier assumption.
This is incremental Phase5 evidence, not complete GUI, installation or hardware
acceptance. Contract: [design](gui-automatic-rules-design.md).

## Delivered behavior

Settings now reads independently qualified Rules/RuleEvents pages through the
same ordinary-user Rust worker used by the TUI. Opening Settings requests these
reads; leaving pauses them. Existing TUI defaults issue no additional rule RPCs.
The GUI shows explicit policy/grant availability, the complete fixed device pool,
allowed conditions/rearm, quota, each rule's revision/future intent/manual pause,
last origin/job/errors and separately sequenced events/retention gaps.

Create is always disabled initially. Replace, Enable/rearm, Disable and Delete
use typed original RuleSpec/RuleMutation inputs, exact observed identities and
checked canonical decimal u64 revision strings. JavaScript never converts a
revision through Number. Confirmation defaults Cancel. Disable/Delete change
future triggering independently of the actual job and cleanup. Preferences still
control observation only and cannot delegate automation.

The Rust worker retains the complete original edit/profile/UUID before IPC and
holds action admission through its actual completion. Renderer reload cannot
return the slot or create a new mutation identity. Query uses only the original
RetryRuleMutation namespace, including after a profile change, refusal or expired
mapping. Only an exact original Applied reply clears it. Stop and Start retain
separate identities and behavior. After real local joins, an unresolved rule edit
makes frontend exit1 and prints its original safe query identity.

Drafts, original revisions, form focus and text selection survive ordinary
metadata refresh. Concurrently changed definitions are inspected explicitly;
refresh never silently rebases the draft. Names retain the service's UTF-8 byte
bounds; errors identify the field and focus an announced summary. Text uses the
existing bounded control/bidi sanitizer and textContent. The finite main-origin
bridge,512KiB complete projection,64KiB detail bound, one accepted action and real
observer/IPC shutdown ownership remain in place. No new external dependency,
helper mutation, credential access, arbitrary RPC or product testing API.

## Complete local software gates

The unmodified `scripts/check.sh` and separate `scripts/check-gui.sh` both pass at
clean7ef7f492, with404 frozen source/build/harness inputs and HEAD/clean guards.

| Gate | Passed executions | Failed / ignored |
|---|---:|---:|
| Root default/all-features, rustdoc and vendored nusb |1,027|0 /0|
| Separate GUI and original GLib regression |15|0 /0|

These are summed test executions, not distinct USB cases or a new hardware count.
Four shared-state tests execute in each root feature set; four bridge tests add
exact revisions above2^53, malformed/exhausted numbers, strict shapes, missing
policy/grant/stale identities, renderer loss and independent Start/Stop handling.
The prior50 schema cases/two generated guest syntax cases also pass. Dependencies
retain the original34 GLib warnings; no warning suppression/backport change.

| Actual release ELF | Bytes | SHA-256 |
|---|---:|---|
| CLI |21,933,280|`d6bd57c4daeabdcce23574f158e225fb7b2fb77deebe1bcf546bb9bc858ed034`|
| Helper |7,525,544|`475b2f97e8b4e31dd1af479193913c637d09dc8c3b1debd13442c0f88f6748f3`|
| GUI |17,410,456|`4c300afc5688bb4c8537cdf69af9f1fa93215bbc4218aa7c59799a99e98ff5a4`|

## Actual QEMU/native matrix

Ordinary x86_64 host, QEMU6.2.0/KVM, Linux6.8.0-106-generic guests, actual
UID2000 agents and UID1000 GUI/operators. Native WebKit2.50.4/wry0.57.1 runs under
Xvfb with the existing copied runtime, not a clean Ubuntu package installation.
All six final invocations use clean e668c045; every actual guest exits0, all
applicable source/HEAD/clean/ELF/driver guards pass and none is forcibly terminated.

| Invocation | Frozen inputs | Actual guest0 | Retained checks |
|---|---:|---:|---|
| New GUI rules IPv4 |391|2|Original264 +45 GUI checks;3 HID/FTDI I/O rounds|
| New GUI rules IPv6 |391|2|Same independent actual flow|
| Original GUI actions IPv4 |386|2|Original264/3 I/O;12 native processes|
| Original GUI actions IPv6 |386|2|Same unchanged verifier|
| Original GUI link IPv4 |398|2|Three exact15s flow faults,7 I/O, packet/Root proofs|
| Original single GUI |380|1|70 files/370,680B; original negative-only contract|

Total:11 actual guest0,34 actual native GUI children. The original single GUI
retains expected unresolved-Start exit1; the other33 GUI children exit0. Guest0
alone is not the verdict. The original Native/kernel/driver/VHCI/Root journals,
three real HID/FTDI rounds and v2 packet/lifecycle/sibling checks remain intact.
No host USB passthrough, host driver mutation or new device-class claim.

Each new pair uses three native processes: first the real no-policy agent shows
policy disabled and a closed bridge rejects ungranted Create without any agent
state mutation; then two policy-enabled epochs perform actual edits and original
agent-managed sharing. This is a bridge/no-policy negative, not a new claim of
native GUI peer-UID denial; earlier permission evidence keeps its own attribution.

The first disabled Create is deliberately sent while the same guest agent is
SIGSTOPped. Capture its actual stopped process identity, original pending UUID/
payload and real renderer reload before the five-second IPC deadline. The worker
then reports the real unknown outcome. Resume the same agent; its late original
request commits exactly one disabled rule and no Start. Native **Query original
rule edit** returns the actual original result and leaves the journal unchanged.
There is no proxy with substituted credentials or synthesized success reply.

Subsequent native edits reach revisions1(Create),2(Replace),4(Replace),5(Enable),
7(rearm),8(Disable),9(Delete); the actual concurrent CLI Disable is revision3 and
manual Stop pause is revision6. A stale revision2 draft is retained and rejected
before another GUI RPC. This does not claim a deterministic GUI-versus-service
CAS race. The original service's CAS tests remain separate.

Two automatic origins bind revisions5/7 to actual UID1000, exact rule/job/original
Start/audit identities. Manual pause survives a real agent restart and settle
period. At Disable/Delete the old job remains actually Running/not-stop-requested.
Three native WM_DELETE_WINDOW requests per pair use the original Xlib client,
actual timestamps and actual GUI wait0/local-worker join; subsequent original USB
I/O and full owner proofs establish service-job continuity. Final resolved deleted
records are retired by the existing journal while eight mutation receipts, two
origins and the exact eleven-event chain remain. No forever-tombstone requirement
or cleanup shortcut is added.

## Preserved failures and evidence

The first clean7ef IPv4 run is **FAIL** despite both guest0: the fixture waited for
a Rust snapshot but its DOM still showed Not sampled/disabled New. The script
then accessed a nonexistent rule-name field and WebDriver returned500. The
subsequent synchronization-only commit waits for actual DOM/button/revision state.

The second clean eedba0d9 run has both guest PASS/0 but remains host **FAIL**:
the new verifier indexed a deleted record after original actual owner cleanup had
correctly retired it. A saved traceback identifies this exact IndexError. A later
same-author review of those raw bytes passes original264+new45 using corrected
history/active-delete expectations; it is not another USB execution or a rewrite
of that failed summary. Fresh final IPv4/IPv6 invocations then pass at e668c045.
Both failed matrices stop at their first item; unexecuted combinations are not
invented. The original fmt test-literal syntax failure has tool transcript/work
log evidence and no nonexistent test log; corrected focused tests pass.

[Validation metadata](../evidence/gui-automatic-rules-20261025/validation.json),
[software/fixture originals](../evidence/gui-automatic-rules-20261025/archive-manifest.json)
and exact methods accompany5,691 archived files/71,156,884B, compressed17,341,848B:
SHA-256 `4c5dbe590b9c7098fd353df57378efe0b3d8c8f1f5a057115efdbc377798d331`.
Every archive member was checked by byte count/SHA, and every frozen source was
mapped to its actual Git commit. Private keys, ELF/rootfs payloads and raw host
USB inventory are excluded. This is same-author evidence validation, not an
independent security audit. Actual final [rule view](../evidence/gui-automatic-rules-20261025/native-rules.png)
and [deleted-intent view](../evidence/gui-automatic-rules-20261025/native-rules-deleted.png)
are unedited1180×760 WebKit screenshots inspected visually; they show their
recorded scroll positions, not a whole-application accessibility audit.

Reproduce with the [fixture guide](../../scripts/qemu_gui_rules/README.md) and full
gates before both address families; retain source/ELF freeze until actual terminal.

## Remaining work

Generic Error does not reliably distinguish every precommit CAS refusal from a
postcommit persistence/publication problem. Refused/Expired rule UUIDs therefore
remain blocked and query-only; settled negative receipts and an explicit safe
reconciliation flow remain unfinished. Inspect latest rules/events with the
administrator; do not clear the ID or reissue Create. UUID/draft retention does
not persist across whole frontend crashes/restarts. Rule disclosure focus and
full keyboard/accessibility/error-state coverage remain outside this narrow test.

Dynamic admitted device pools, physical remote all-configuration/generation safety
receipts, comprehensive GUI acceptance, `.deb`/systemd and clean install/upgrade/
uninstall/reboot, hardware G1/G2/fullG3, storage/ISO/performance and Windows still
require their original independent work. No entire phase is marked complete.
CI/Pages publication is recorded separately only after actual success.

Documentation QA checks21 files/249 local targets/44 site references; the eight
site files change only index copy, retaining the original E49 screenshot's own
attribution. The global readme-site skill's four files/Codex symlink are unchanged.
Current browser checks at1280/390 verify no horizontal overflow, the new report's
actual2px solid keyboard focus, loaded original images and390-wide script-blocked
fallback with all three backends/commands/new text visible. An initial lazy image
and a role query without JavaScript are recorded without inventing failures.
Temporary tabs/viewport and the owned preview server(actual SIGINT wait130) are
reclaimed. See [browser observations](../evidence/gui-automatic-rules-20261025/browser-review.json).

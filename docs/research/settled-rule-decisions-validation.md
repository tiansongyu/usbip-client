# Settled rule decisions: validation

Local result: **PASS**,2026-10-25, at clean
`650c53a8dbf14d8746006cb7a22f0dbfb3bcc19f`. The full software gates, actual CLI
adapter processes and all eight QEMU invocations use this source and the same
three release ELFs. [Implementation contract](settled-rule-decisions-design.md).
This closes the bounded settled-rejection gap identified in
[E55](gui-automatic-rules-validation.md); entire Phase5/Phase0–7 remains open.

## Implemented contract

`--automation-decisions-policy` loads a separate strict root schema2 document
with required `settled_rule_results:true`, using the original protected-file
loader and full fixed-pool/UID/backend/ACL checks. It conflicts with the existing
`--automation-policy` option. The original schema1 reader remains strict and
unchanged. Only explicit root2 opt-in initially upgrades application disk2/3 to
strict disk4; old policy1 alone still uses disk3. This is independent of TLS
schema/ALPN and the helper's private journal versions.

Disk4 requires both the unchanged positive automation journal and a nonnull
independent rejection journal. Complete disk2/3/4 shapes reject missing/null/
unknown/mismatched fields; no downgrade/reset occurs when policy is removed.
Older agents cannot open disk4. Rejections retain actual UID/profile/original
UUID/typed payload/fixed Failure and a separate contiguous sequence, bounded to256
entries within the original journal byte/admission reserve. Positive and negative
mutation IDs cannot overlap. Start retains its independent namespace.

New closed support/decide/query RPCs preserve old Rules/Job/Session/Metrics/audit/
log/diagnostic and USB/TLS wire shapes. Read-authorized support is independent of
WAL and is not delegation. Original positive/negative identities and payloads
are checked before another operation. Replays/queries never reapply an edit or
record another rejection; policy/grant changes do not rewrite an old decision.
Legacy edit/query operations also consult negative history without a second audit.

The real original mutation path still performs edits. After its actual return,
the actor checks committed positive history before considering rejection: a
registry failure after commit cannot turn Applied into Rejected. Without an
original positive, only a completed precommit failure with a healthy journal can
be durably recorded as Rejected by the same atomic Store writer before ACK.
Permission rejection includes one original Denied audit in that transaction.
Writer uncertainty has no settled rejection receipt. A committed negative also
survives a later registry publication error. No negative receipt grants USB
ownership, starts a job or proves cleanup.

The shared frontend retains original protocol/profile/UUID/payload before IPC.
Current capability/profile changes cannot redirect its query. The GUI clears an
intent only for an exact valid original protocol/result: Applied binds rule/
revision/deleted effect; Rejected requires a positive sequence and excludes
Persistence. Generic Error/Expired/mismatch stays blocked. A bounded last receipt
retains the original draft in Rust across renderer reload. **Restore rejected
draft** defaults Cancel, names the original request and restores its original
profile/revision without rebasing or submitting. Fresh state and another explicit
operator confirmation are required for a new edit. Known unhealthy journals or
unavailable support block new edits before minting UUIDs; original queries and
independent Stop remain available. A policy1-only disk3 instance explicitly reports legacy support;
an older agent unable to answer support is read-only for new GUI edits.

CLI `agent rule PROFILE --settled …` selects this protocol explicitly. It writes
a complete UUID/protocol intent/query frame before RPC, preserving existing
signal/backpressure cleanup. Legacy commands retain their old frames/behavior.

## Software and exact release artifacts

Unmodified root `scripts/check.sh` and separate `scripts/check-gui.sh` pass:

| Complete gate | Passed executions | Failed / ignored |
|---|---:|---:|
| Root default/all-features, rustdoc, vendored nusb |1,054|0 /0|
| Separate GUI and original GLib regression |17|0 /0|

These1,071 are summed executions, not unique USB cases. All410 frozen inputs,
HEAD/clean guards and three ELFs remain exact. The earlier focused service143/
frontend13, GUI15 and root all-feature Clippy checks pass; the later two bridge
tests are included in the final17. Original34 GLib dependency warnings remain;
no version, backport, external dependency or lock changes.

New tests cover strict root2 and disk4/real atomic reopen, legacy shapes,
permission/revision/quota refusals, UID/profile/payload isolation, grant changes,
old protocol interaction, bounded eviction, malformed receipts, exact counters
above2^53, retained query protocol/draft and support through real peer IPC while
the actual Store/actor is blocked. Actual file-identity replacement makes the
writer fail without a fake rejection. A retained real Store task plus intentional
fixture-only controls poisoning verifies both committed positive and committed
negative results after actual postcommit publication failure. Gate owners are
released/joined before assertions; no USB/helper success is inferred from units.
This does not fault-inject every fsync or prove crash durability on arbitrary disks.

| Actual release ELF | Bytes | SHA-256 |
|---|---:|---|
| CLI |22,149,112|`33406632ea3ef00e26f9ca504dcc0131ad51bafea428927542fe51e0aea10c2d`|
| Helper |7,525,544|`475b2f97e8b4e31dd1af479193913c637d09dc8c3b1debd13442c0f88f6748f3`|
| GUI |17,572,584|`0a492b38b679523775e3a0e052effa6aea465a6a8933669ffd6a383b04e835d4`|

## Actual native QEMU and CLI matrix

Ordinary x86_64 host, QEMU6.2.0/KVM, Linux6.8.0-106-generic guests, actual broker
UID2000 and GUI/operator UID1000, original WebKit2.50.4/wry0.57.1 under Xvfb.
Copied runtime is not a clean Ubuntu package installation. All first executions
in this slice pass; no new failed QEMU run is omitted or rewritten.

| Invocation | Frozen inputs | Guest0 | Evidence retained |
|---|---:|---:|---|
| Settled decisions IPv4 |397|2|Original264 +46 GUI +27 decision checks;3 HID/FTDI rounds|
| Settled decisions IPv6 |397|2|Same independently executed flow|
| Legacy policy1 GUI rules IPv4 |394|2|Original264+45/3 I/O; strict disk3|
| Legacy policy1 GUI rules IPv6 |394|2|Same unchanged default contract|
| Original GUI actions IPv4 |389|2|Original264/3 I/O/12 native application children|
| Original GUI actions IPv6 |389|2|Same original verifier|
| Original GUI link IPv4 |401|2|Three exact15s directional faults/7 I/O/packet/Root proof|
| Original single GUI |383|1|70 files/386,848B; original negative-only contract|

Eight invocations,15 actual guest0,40 native application children. Thirty-nine GUI
children exit0; the original single GUI retains its expected unresolved-Start
exit1. Every applicable source/HEAD/clean/ELF/driver guard passes; none is forcibly
terminated. Original kernel enumeration, three actual HID/FTDI rounds, exact
Root terminals/driver/VHCI baselines, and v2 packet/lifecycle/sibling checks stay
intact. Default Native verifier remains strict disk2; E55 defaults remain disk3;
only an explicit new fixture expectation permits the complete disk4 shape.

The new flow retains E55's real lost-positive Create ACK and positive CRUD. For
its additional service-side conflict, pause frontend polling at observed revision2
and use the real CLI to advance the actual rule to3. The native GUI's retained
revision2 therefore passes its observed bridge check and reaches actual service
CAS. Stop only the disposable agent, dispatch that original edit, capture its
real pending UUID/protocol/payload, reload the actual renderer and wait for the
real five-second IPC timeout. Resume the same agent. Exactly one rejection is
recorded for that original UID1000/UUID/Replace2 payload; the rule stays3 and no
job/session/Start is admitted by it.

Cooperatively stop/reap that agent0, capture its executed ELF/UID/process identity,
and start a distinct real broker before querying. The same GUI resolves the
original durable rejection with replayed=true. Its entire private journal is
identical before/after query. A real `--settled retry` CLI process then emits the
original query/protocol plus exact rejection response(two frames), returns expected
exit1, and again leaves the complete journal unchanged. Both new pairs exercise
this CLI path; the original five metadata-peer CLI cases separately pass actual
exits1/1/1/0/0 and keep the same CLI ELF. Those five do not prove real service/USB.

Native **Restore rejected draft** defaults Cancel and restores the exact original
profile/revision2/name. Fresh metadata3 then rejects that still-stale draft before
another RPC. The operator explicitly chooses latest3 and continues intended
Replace4/Enable5, manual Stop pause6 across restart, rearm7/Disable8/Delete9.
Final positive history remains exactly eight edits/two origins/eleven events;
rejection sequence1 is independently retained. Three real native closes per new
pair use WM_DELETE_WINDOW/actual wait/local joins; subsequent original I/O and
full Root proofs establish agent-job continuity. Future intent remains separate
from actual running-job/cleanup facts.

## Evidence and limits

[Validation metadata](../evidence/settled-rule-decisions-20261025/validation.json)
and [archive manifest](../evidence/settled-rule-decisions-20261025/archive-manifest.json)
cover5,966 files/73,866,742B, compressed18,151,179B with SHA-256
`a2e8586ec11abd01316cca26845fb05baf0b0a5f929fe131a153be5abbf6f1d7`.
Every member is checked by bytes/SHA and every frozen source maps to actual Git
650c53a8. Private keys, ELF/rootfs payloads and raw host USB inventory are excluded.
Methods and original logs accompany the evidence; this is same-author checking,
not an independent security audit. Earlier E55 failures retain their own original
records and verdicts. No new software/QEMU FAIL occurred in this slice; missing
file lookups/patch-context mistakes are recorded in the work log.

Actual final [settled-mode view](../evidence/settled-rule-decisions-20261025/native-settled-rules.png)
and [deleted-intent view](../evidence/settled-rule-decisions-20261025/native-settled-deleted.png)
are unedited1180×760 native screenshots inspected at their recorded scroll positions.
They capture later positive state, not the intermediate rejection. Rejection/
restoration evidence is actual native DOM/IPC/confirmation metadata, not a pixel
or full accessibility audit. [Reproduce](../../scripts/qemu_rule_decisions/README.md)
with both IP families and the complete gates while freezing source/ELFs until all
actual handles are terminal.

Expired/evicted identities and uncertain writes still do not prove no original
commit. Whole-frontend-process pending-ID/draft persistence, complete disclosure/
keyboard/error-state coverage, dynamic admitted pools, physical remote safety
receipts, `.deb`/systemd/clean install/upgrade/uninstall/reboot, real G1/G2/fullG3,
storage/ISO/performance and Windows remain unfinished. No full phase is declared
complete. CI/Pages publication is reported separately only after actual success.

## Documentation and showcase checks

English setup/migration instructions, CLI examples and GUI recovery guidance are
updated with the README and current showcase. Static QA verifies410 unchanged
frozen inputs against Git650c53a8, all three ELFs, eight website files (only index
copy changed), four unchanged global README-skill files and the Codex symlink.
Local Markdown/HTML targets and actual release CLI help pass; no product rebuild
or changed test source is attributed to this documentation pass.

[Browser observations](../evidence/settled-rule-decisions-20261025/browser-review.json)
record actual1280/390 views without horizontal overflow, the new report link's
keyboard focus (2px outline), loaded images and the desktop CSP no-script view
with all three static panels and build commands. No new showcase pixel, clipboard,
motion or complete accessibility claim. Both owned tabs close, viewport override
resets and the actual preview server joins after SIGINT/exit130.

## Published CI receipt

Content head `5b54c73a717106f90af0f417acefcf43599bfb0a` is pushed normally to
`main`. [Core CI](https://github.com/tiansongyu/usbip-client/actions/runs/40172001651)
and [GUI CI](https://github.com/tiansongyu/usbip-client/actions/runs/40172001639)
each complete all four Ubuntu22.04/24.04 x86_64/aarch64 native jobs successfully,
including the actual architecture/environment and full gate steps.
[Pages](https://github.com/tiansongyu/usbip-client/actions/runs/40172001636) check and
deploy also succeed. [Original receipt](../evidence/settled-rule-decisions-publication-5b54c73a-20261025/receipt.json) retains42 API/HTTP/method files,
553,155B, with exact member hashes.

All seven canonical HTTPS resources return200 and match Git bytes. The410 local
frozen inputs, three ELFs, eight site files and four global skill files/symlink
still match their recorded sources. CI job conclusions are not CI log analysis,
executed-ELF sampling or a per-platform test recount. Local software and QEMU
remain pinned to650c53a8; CI/Pages belong to5b54c73a. The final receipt-only commit
is a separate revision. The remaining full-objective limitations above stay open.

Later [E57 frontend-process validation](frontend-intent-recovery-validation.md) adds private original-request persistence across actual native process crashes. This E56 source, original failures and results remain assigned to their own revisions; it does not become power-loss or complete GUI acceptance.

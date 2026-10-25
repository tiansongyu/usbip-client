# Automatic sharing: persistent fixed-profile runtime

2026-10-25. Implementation and all final local/QEMU runs are locked to clean
`20a15a016f9174d8dee3e46cc093b92f0e1a782c`. This extends the
[E52 authorization/input foundation](automatic-sharing-foundation-validation.md)
with an actual agent-owned journal, rule RPCs/CLI and background execution.
It is **partial automatic sharing**, not completion of the overall
[design](automatic-sharing-design.md), GUI, installation or hardware gates.

## Implemented behavior

- Explicit root policy at agent startup; absent policy starts no observer or
  automatic job. Immutable complete-pool delegation and per-use ACL checks remain.
- Strict disk3 journal and independent mutation UUID/query namespace, revision
  CAS, bounded rule/tombstone/history retention and separately sequenced audit.
  Legacy disk2 remains strict and unchanged for agents never enabling policy;
  disk1 migration remains strict. No downgrade or reset of old ownership.
- One atomic Start transaction includes the job, original Start request/audit,
  rule/revision/delegated UID/observation origin and firing event, before actual
  runner entry. Retained origins continue deduplicating after Start FIFO eviction.
- Manual Stop suppresses future automatic starts before WAL waits and persistently
  pauses rules. Later automatic registration inherits suppression; an older Enable
  commit cannot clear a newer Stop. Actual safe siblings/cleanup remain original.
- One owned local monitor, monotonic source freshness and same-revision periodic
  refresh without new public watch frames. Complete safe stable observations are
  required; boot/generation primitives determine tokens, not an extensible DTO.
- Remote known virtual-profile conditions reuse the original authenticated
  two-slot catalog supervisor, keeping at most one automatic poll outstanding.
  Disable/lost waiters do not release an actual running network slot. Physical
  remote keys fail closed with SafetyEvidenceUnavailable.
- New CLI mutations emit the complete original UUID intent before dispatch and
  have dedicated INT/TERM handling; interrupted accepted requests remain uncertain.
  Existing Start/other agent output formats and frozen Job/Session/Metrics/
  legacy audit/log/diagnostic wire schemas remain unchanged.

The service adds one dependency edge to the already locked sha2 0.10.9. Root
inventory remains222 packages and external version/source/checksum values remain
unchanged. The independent GUI lock gets the same service edge. Hashes identify
observation metadata; they grant no USB access or hardware identity guarantee.

## Software and CLI evidence

Original full outputs, frozen396-input manifest and actual release hashes are
in [the evidence directory](../evidence/automatic-sharing-runtime-20261025/summary.json).

| Final gate at clean `20a15a01` | Passed | Failed / ignored |
| --- | ---: | ---: |
| `bash scripts/check.sh` | 1019 | 0 / 0 |
| `bash scripts/check-gui.sh` | 11 | 0 / 0 |

The root gate retains default/all-feature fmt, clippy, tests, release builds,
warning-denied rustdoc and vendored nusb tests. The41 extra executions over978 are
7 journal +12 runtime +1 discovery tests in each root feature run, plus1 gated
CLI parser test. GUI retains its original11 tests and separate build/rustdoc/JS
checks. The original34 GLib dependency warnings remain visible, unsuppressed.
All actual gate processes returned0 before source/documentation changes.

| Release ELF | Bytes | SHA-256 |
| --- | ---: | --- |
| CLI | 21,846,352 | `7b721c27470cbdb36c9762b61de76f6f3f705a1862568ca91293df7e3bc82607` |
| Helper | 7,525,544 | `475b2f97e8b4e31dd1af479193913c637d09dc8c3b1debd13442c0f88f6748f3` |
| GUI | 17,228,584 | `edfc9127e3a02b823cc58b31f29b924ed14e9255c80c51b2270d4ec1902a9c42` |

Software tests cover strict required/null/mixed-version shapes and actual atomic
disk3 reopen/corruption, CRUD/CAS/retry/quota/UID/revocation, same-transaction
origin, occurrence retention beyond FIFO pruning, local safety/freshness/storage/
ambiguity, overlapping physical scopes, remote shape/expiry and raw/framed TLS
observations. A real Store mutex gate verifies committed reads bypass WAL and
Stop reaches actual owners/late registration. A real read-only host monitor stops
and returns its finite worker permit while the WAL remains gated; it prints no
host inventory and opens no helper or USB node. Synthetic cleanup labels in
metadata fixtures are not physical USB release evidence.

[`test-rule-cli.py`](../../scripts/test-rule-cli.py) runs5 actual CLI children
against a disposable metadata-only Unix peer under a protected `/run` parent:
unknown reply preserves the generated UUID; Retry sends only that original query
and reports Expired; full stdout timeout and INT/TERM before complete intent send
no RPC. Actual exits are1/1/1/0/0. The peer records no real service commit. Four
original ready/blocked INT/TERM cases and two original output-backpressure cases
also pass against the same CLI ELF. Blocked-output socket closure is not a
received backend snapshot or a general hard-blocking guarantee.

## QEMU: actual Native rule firing and original regressions

All four final invocations use their original locked build, actual helper/CLI
execution hashes, isolated role credentials, dedicated broker UID2000/operator
UID1000, kernel and payload provenance. Eight guest processes actually return0,
without forced termination. Every source/ELF/HEAD/clean guard is true.

| Invocation | Inputs | Result and retained contract |
| --- | ---: | --- |
| New local Native rule, IPv4 | 237 | PASS; original264 +17 automatic assertions,3 HID/FTDI I/O rounds |
| New local Native rule, IPv6 | 237 | PASS; original264 +17 automatic assertions,3 HID/FTDI I/O rounds |
| Original no-policy Native agent, IPv4 | 235 | PASS; original264 assertions/3 I/O rounds, strict disk2 |
| Original Native framed-v2 faults, IPv4 | 244 | PASS; original3 directions/15-second valve windows,7 I/O rounds, packet/lifecycle/Root proofs |

The new [paired wrapper](../../scripts/qemu_automatic_rules/run.py) leaves every
original agent denial, kernel enumeration, raw-v1 health, two-epoch history,
targeted stop, SIGKILL/restart, helper capability identity, complete driver/config/
interface baseline and exact Root proof assertion intact. The old verifier still
defaults to strict disk2; only the new explicit server final/held snapshots require
the entire disk3 shape, with a separate strict automation-journal check.

The server first completes the original no-policy denials and actually joins its
idle agent. A root-owned policy then enables one fixed Native pool rule. Its
Create/Retry replies are actual RPC output. Background firing admits the two
devices; RetryStart only reads back that original automatic Start. Origin/job/
request/audit/Fired identities match the same saved committed snapshot. No fixture
manufactures a Start receipt. Real keyboard down/up and FTDI35-byte traffic in
each direction retain their original validation.

Targeted incoming HID Stop pauses the profile's rule at revision2 while preserving
the same live serial capability. The actual next service epoch remains paused for
more than its minimum settle interval and still has exactly one old origin/job.
Explicit CAS Enable revision3 rearms the same physical observation; a second
background Start completes the original final I/O and cleanup. Exactly two jobs/
origins and one rule remain, without duplicate firing. This is not a hotplug or
new physical-generation rule test. The independent v2 run never enables rules.

## Documentation and showcase checks

Static QA checked16 documentation files,232 local targets and43 site link/asset
references, actual CLI help, all396 frozen inputs and the same three ELFs. Only
site copy changed; its CSS, JS and original assets remain unchanged. The four
global `readme-site` files and Codex discovery symlink retain their original hashes.
The English README, showcase and global skill requested earlier remain maintained.

Current IAB checks use the real `/usbip-client/` prefix: desktop1280/scroll1265,
mobile390/scroll375, loaded original assets and actual keyboard focus on the new
rule-guide link with a2px solid outline. With page scripts blocked by preview
CSP, all three backend panels, commands and the new link remain visible. This
last check used a1280-wide tab. Screenshot capture was unavailable; reduced-motion
behavior has existing CSS review only, without new emulation or pixel acceptance.
Temporary tabs, viewport override and local preview server were reclaimed.
See [recorded observations](../evidence/automatic-sharing-runtime-20261025/browser-check.json).

## Failures and unfinished work

Development Runtime first6/7 and its diagnostic single-case run failed because
the fixture expected success after Stop had already preceded Begin. The actual
original result was Finished/Some(State)/zero sessions. The correction expects
that exact existing early-stop result, after joining the original network/worker;
no product assertion was relaxed to accept arbitrary errors or fake USB success.
Both original FAIL logs remain. Later focused12/12 and final full gates are separate.

Two file-name-filtered commands ran0 tests and count only as compilation. Correct
module filters subsequently ran journal7/7 and runtime12/12. One new parser-test
`expect` caused a clippy FAIL; propagating Result fixed it without suppressing
the lint. Earlier focused results and all original development logs are retained.
All new final CLI/QEMU invocations passed on their first execution; no failed
invocation was relabeled or overwritten.

The [archive manifest](../evidence/automatic-sharing-runtime-20261025/archive-manifest.json)
checks every archived original member. It excludes private keys, executable/rootfs
payloads and host raw inventory. Reports retain exact tested heads; later docs/CI
receipts do not acquire these runs retroactively.

Still required: automatic virtual/USBFS QEMU, automatic v2/failure/hotplug/revocation
expansion, authenticated complete safety/generation receipts for physical remote
devices, dynamic per-device pools, GUI rule CRUD/unknown-response flows, packaged
installation/systemd/upgrade/uninstall/reboot, real G1/G2 hardware, full G3,
performance/compatibility expansion and Windows support. Earlier independent
evidence remains scoped to its original revision and ELF. The full Phase0–7
objective remains active.

## Publication receipt

Content `d395c3964b6da7873571fe81805a94ade1fcdfdb` was pushed normally over SSH.
Core run40160168287 and GUI run40160168167 both completed successfully on all
four Ubuntu22.04/24.04 x86_64/aarch64 jobs; actual architecture and full-gate steps
were checked. Pages run40160168210 check/deploy also succeeded. Seven canonical
HTTPS resources return200 and exactly match that content head. All396 frozen
inputs, three ELFs, eight site files and four global skill files remain intact.

The [original publication receipt](../evidence/automatic-sharing-runtime-publication-d395c396-20261025/receipt.json)
retains42 API/HTTP/method files,437254 bytes, with per-file hashes. One attempt to
fetch an in-progress CI job log returned404; no CI logs or executable hashes were
downloaded or inferred. The first collector expected the wrong deployment step
label and failed before creating the archive directory. The original API showed
the successful `Deploy website` step; the corrected collector checks that exact
name. Neither collection issue changes any workflow's result. Local software and
QEMU evidence stays locked to20a15a01; the final receipt-only documentation commit
is not another tested code/CI revision.

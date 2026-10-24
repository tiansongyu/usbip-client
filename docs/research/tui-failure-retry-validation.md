# Terminal Start uncertainty and stale-page recovery

Status: scoped implementation verified at clean `4ee6be3e`, 2026-10-25. [Design](tui-failure-retry-design.md).
This is scoped Phase 4 evidence; G4 and the original Phase 0–7 goal remain open.

## Change and original failure

At clean `38477360178563640549b48ee6e2db0ede7cf8b7`, the new Native IPv4
experiment returned **FAIL**. Both guests actually exited zero, all four source,
ELF, head and clean guards held, and all nineteen product PTYs exited normally.
Both real terminal streams showed the five-second unknown Start outcome, the
exact original UUID in confirmation and `Resolved original`, without a normal
`Admitted job` acknowledgement. Actual RetryStart did not add a request/job.
The final verifier rejected the retained exact job's missing
current → stale → current transition: the old section still said Current.

A post-run diagnostic separately invoked only the original base verifier and
returned its 264 assertions and three HID/FTDI I/O rounds. It does not change the
original overall FAIL and is not another USB execution. The original summary,
raw streams, store snapshots, process observations and method remain archived.

The product fix at `4ee6be3ed5ff69b0a818aab1ba9f7b7bbb255e46` marks retained
job, session and audit pages stale when the initial Profiles read fails with a
transient error. It clears the previous journal-health indication and link
samples while retaining exact rows/cursors and independent local/remote sources.
Permission/protocol failures retain their prior clearing behavior. There is no
USB, helper, authorization, RPC/schema, start/retry or resource-lifecycle change.

## Software

The ten actual `scripts/check.sh` commands passed at that clean product commit:
formatting, default/all-feature Clippy with denied warnings, workspace tests,
release builds and rustdoc with denied warnings, plus vendored nusb tests.
Default tests: 418. All-feature tests: 514. Vendor tests: 20. Total: **952 test
executions**, zero failed/ignored. All 235 selected source/build inputs remained
stable and map to that Git revision. The new frontend test renders real
TestBackend buffers at 60 and 120 columns, retains exact job/session/audit
identity and cursors, clears health without inventing cleanup, and checks all
three stale page labels. The twelve frontend functions passed on their first
local run; C strict compilation and generated init dash preflight also passed.

The all-feature CLI is SHA-256
`0b4a3c40f5c968ba1122def71d569b939234ea09bec5c87d5c4784526e809ff0`
(20,262,112 bytes); helper is
`83c53d522d5c89a9b3384b23c17242aa3e197217fc785d62706b520c1e1473e5`
(7,525,376 bytes). These identify local/QEMU artifacts, not fetched CI binaries.

## Installed scenarios

All four final pairs ran at the same clean product commit as the software gate,
using their default locked all-feature build and the exact local release ELF.
The two new failure pairs and two original E46 normal-action pairs all passed:

| Evidence directory | Selected inputs | Actual PTYs | Server/client wait | Result |
| --- | ---: | ---: | --- | --- |
| rustusb-tui-failure-ipv4-20261025 | 237 | 19 | 0 / 0 | PASS |
| rustusb-tui-failure-ipv6-20261025 | 237 | 19 | 0 / 0 | PASS |
| rustusb-tui-failure-actions-regression-ipv4-20261025 | 233 | 19 | 0 / 0 | PASS |
| rustusb-tui-failure-actions-regression-ipv6-20261025 | 233 | 19 | 0 / 0 | PASS |

That is eight actual guest exits zero and **76 actual product PTYs**, each with
UID1000, zero recorded capabilities, live executed-ELF hash, real child wait0,
raw mode, resize and exact saved termios restoration. Each pair preserves all
four source/ELF/head/clean guards. The separate original red has two guest exits
zero and nineteen normal PTYs but remains an overall failed experiment.

Two of the nineteen actual UID1000 PTYs in each new pair use the controlled
failure driver. After initial sampling, the disposable root fixture pauses the
same agent with SIGSTOP, confirms its actual stopped process state, and lets
the real UI issue Start. The product IPC deadline expires while that process
remains stopped and the committed store remains unchanged. Resume the same
process and wait for its actual queued request to commit before confirming the
original retry through the UI. Require decoded unknown/UUID/resolved frames,
exactly one new durable request/job, and actual normal child wait.

The same PTYs then show the exact admitted job, pause the same agent again,
refresh and display that retained job as stale. After resume/refresh the same
job is current again. Phase timestamps, repeated stopped states and unchanged
PID/starttime distinguish the original process from a replacement. Timings
schedule the fixture; complete decoded ANSI frames and actual identities decide
its result. No synthetic response JSON or alternate IPC server is used. The
later real CLI RetryStart is only metadata readback after UI resolution.

Each original E46 regression still requires the normal Start acknowledgement;
the new optional verifier selection applies only to the two named failure PTYs.
All original 264 assertions, three HID/FTDI I/O rounds, kernel enumeration,
external TLS/plaintext-port checks, two epochs, exact root proofs and full
baseline cleanup remain mandatory. The old E45 invocation-only/INT/TERM matrix,
entire historical backend/v2/CRL matrix and separate host signal/backpressure
fixtures are not rerun by these four pairs.

## Evidence and boundaries

The [evidence manifest](../evidence/tui-failure-retry-4ee6be3-20261025/manifest.json)
closes 3685 original files/methods and 2015
redecoded guest Base64 blocks. Its raw archive is 7,569,167 bytes,
SHA-256 `d979e0c62ae650c079dc4a7a406ea10aeefe64530c3dbe92f166e6676ab9419c`. Source snapshots map to their actual Git
revisions; the red uses its older CLI and the four final pairs use the local
software-gate CLI. Actual wait/UID/PID/capability/ELF/termios, request/job identity,
original process identity and final journal baselines were checked again without
importing the run verifier. Original summaries and failures are unchanged.
No private keys, ELF, targets or private host discovery output are archived.

This is same-author source/raw/identity review, not an independent security audit
or new USB execution. SIGSTOP before admission/refresh is a reversible process
fault; it does not prove wire loss, a timed in-kernel active window, or cancellation
of permanently blocked kernel/filesystem work. No hardware passthrough is used.
Installed UI hotplug and authoritative action-denial scenarios, full G4,
hardware G1/G2, full G3, GUI/install, performance and Windows remain unfinished.
The existing English README, static showcase and global readme-site skill remain
separate documentation deliverables; the showcase is not the Phase 5 GUI.

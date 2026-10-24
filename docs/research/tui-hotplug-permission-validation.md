# Terminal hotplug, authoritative refusal and settings reload

Status: scoped implementation and documented G4 software/QEMU gate verified at
clean `2dc888ce`, 2026-10-25. [Design](tui-hotplug-permission-design.md).
Scope: the documented G4 terminal gate for ordinary operators of an already
configured agent, with software and disposable QEMU evidence. Hardware, full G3
and release readiness remain separate gates; GUI/install/performance/Windows
remain future phases.

## Defect and change

At clean `935bd4460d6221d885e83f93db5864c4b48af867`, the original product
returned an overall **FAIL** in the new all-feature single-guest experiment.
The guest actually exited 0, without forced termination; all four integrity guards
held. The four real TUI children returned their expected outcomes (three exit 0,
one unresolved-start exit 1) and restored the terminal. The original six-point
monitor verification returned successfully before the terminal assertion failed.

Actual open-modal frames showed address 2, then a blank panel on removal, then
address 3 after reinsertion. The old detail scroll offset hid `No selected
metadata`. The original verifier's empty-text candidates also included initial
wide sidebars; its ordering assertion still correctly rejected the run. Final
checking narrows those candidates to the actual open modal. The original FAIL
summary is retained unchanged; a stricter later predicate does not relabel it.

The fix at `617c0370` resets detail scroll when effective selected identity
changes or disappears, while preserving it when the same identity only reorders.
The final keyboard regression commit is
`2dc888ce498c8f373d288098691d80d927a7480a`. No USB, helper, authorization,
RPC/schema, resource ownership, dependency or profile-policy behavior changed.

The first shell composition template subcommand failed because `cleanup()` also
matched the inserted `frontend_cleanup()` suffix. Its outer shell returned 0;
the initial generator/stdout was not separately frozen. The recorded tool
observation and pre-fix runner snapshot are clearly labeled. A complete-line
anchor fixed composition; actual subsequent generation/dash and strict C
compilation passed. One premature summary read found no file: this runner writes
its summary in finally, and the same owned handle was re-polled, never restarted.

## Software and build selections

At clean `2dc888ce`, all ten required quality commands passed: fmt, default/all
Clippy with denied warnings, workspace tests, release builds, rustdoc with denied
warnings and vendor tests. Results are 420 default + 516 all-feature + 20 vendor
= **956 executions**, zero failed/ignored. All 235 selected source/build inputs
were stable and map to that Git revision. The two new frontend tests cover actual
TestBackend buffers at 60/120 columns, selected identity/reordering/removal, and
keyboard sort/filter/search/default-confirmation with the exact command channel.
Fourteen frontend functions passed on their first final development run.

The all-feature CLI is SHA-256
`6b426a3572ff1e4d2e95cd6e373d7c4d116fffdd7997490a994a517cdc4bbf8d`
(20,264,960 bytes); helper is
`83c53d522d5c89a9b3384b23c17242aa3e197217fc785d62706b520c1e1473e5`
(7,525,376 bytes). These are local/QEMU identities, not fetched CI binaries.

The separate minimal selection uses
`cargo build --workspace --release --locked --features rustusb-cli/tui`.
Its actual CLI is `06e1531d564dd371ecd89297f10b40ceb023b34aba340b55d6741eae4cb5ac71` (17,660,904 bytes);
its helper is `8dc4a579d4d82cb8bdd1815128cbbe0d41828248b413de13452eb057a9e90607` (6,726,344
bytes), copied but not executed by this standalone scenario. Its normal CLI
dependency graph excludes rustusb-rust-backend/nusb/rusb/libusb. It is distinct
from the default no-TUI CLI and from the all-feature selection. After its actual
VM completed, a recorded all-feature build restored the exact software-gate ELF;
restoration is not another software or USB execution.

## Installed scenarios

All five final invocations ran at the same clean `2dc888ce` source. Two single
guests used distinct feature selections; three managed pairs used the exact
all-feature software-gate artifacts. All eight guest exits were0, with no forced
single-guest termination and all applicable source/head/status/ELF guards true.

| Evidence directory | Inputs | Actual PTYs | Expected child exit 1 | Result |
| --- | ---: | ---: | ---: | --- |
| rustusb-tui-interactions-final-all-20261025 | 238 | 4 | 1 | PASS |
| rustusb-tui-interactions-final-tui-20261025 | 238 | 4 | 1 | PASS |
| rustusb-tui-interactions-fault-ipv4-20261025 | 237 | 19 | 0 | PASS |
| rustusb-tui-interactions-fault-ipv6-20261025 | 237 | 19 | 0 | PASS |
| rustusb-tui-interactions-legacy-ipv4-20261025 | 232 | 10 | 0 | PASS |

There are **56 actual product PTYs**: 54 normal exit 0 and two deliberately
expected unresolved-start exit 1, all with actual UID1000/ELF/zero recorded
capabilities/raw/resize/termios restoration. The separate red has four PTYs and
one guest exit 0 but remains overall FAIL. Six additional fast guard commands
return their expected1; they are not counted as PTYs.

The two standalone runs preserve the original independent observer's six current
points: empty, added, unbound, rebound, removed, reinserted. Actual kernel USB
hint counts increase, udevadm and sysfs/driver observations agree, and reinsertion
has the same candidate/port but a new observational generation/address. The TUI
has its own actual kernel group 1 socket, same PID/starttime/socket at both samples,
UID1000 and zero recorded capabilities. Its own complete frames display the
first address, visible empty-selection feedback in the still-open modal, and the
new address. The other observer is supporting evidence, not a substitute for UI
frames. No NIC, host passthrough, helper process or USB/IP driver is started in
these standalone tests. They do not prove a new USB/IP hot-unplug data path.

Three more real PTYs per standalone run exercise in-memory `d` defaults; `l`
loading a changed valid user file; a mode0644 Reload failure that preserves
options/bytes; and a real Read/Audit-only agent denying Start and the original
retry. Root changes only a known fixture preference file between phases: this
is not another product Save. The actual agent audits two UID1000 Permission
denials and admits no requests/jobs/sessions. The UI retains one original UUID,
shows its retry confirmation and reports it on exit. That child really exits1,
after terminal restoration and observer joins; the parent expects exactly this
result. The actual UID2000 agent later exits0 and unlinks its control socket.
The operator cannot read its actual present broker private key.

Three additional fast commands per standalone run reject root UI, `--json` and
redirected terminal I/O, each with actual exit 1, empty stdout and its exact guard
message. They have no separate live /proc receipt; the PTYs and agent do.
No response JSON is invented and no alternative IPC server stands in for the
agent. Bounded timing schedules events; actual frames, current facts, peer
identity, committed records, real waits and saved termios decide outcomes.

Each managed pair preserves the original 264 assertions, three HID/FTDI I/O
rounds, two epochs, external TLS/plaintext-port checks, exact root proofs and
full kernel baselines. The two E47 pairs also retain actual unknown-Start retry
and current/stale/current jobs; the E45 pair retains six-page/default-cancel,
quit/Ctrl-C/INT/TERM and held Connected terminal coverage. The entire historical
backend/v2/CRL matrix is not rerun, and neither are separate host-only signal and
backpressure fixtures by these five QEMU invocations.

## G4 requirement audit

The documented gate in [the roadmap](../roadmap.md#7-phase-4完整-tui) is checked
against code, rendered-model tests and installed product evidence at the revisions
above. This is a same-author audit of the gate's software/QEMU scope, not an
independent security review or a declaration of full project completion.

| G4 requirement | Authoritative evidence | Scoped conclusion |
| --- | --- | --- |
| All six pages share core interfaces | `worker.rs` uses public agent IPC and shared DeviceMonitor/CatalogMonitor; actual six-page PTYs, public-manifest-only startup and source hashes | Covered; no parallel USB business logic |
| No root TUI | Actual UID1000/zero-capability PTYs, root guard exit 1, checked OS peer authorizes Read/Audit but rejects Start | Covered; a confirmation grants no privilege |
| Keyboard reachability | Actual page/search/detail/confirm/quit and signal flows; real App key/channel tests for sort, attention filter, bounded UUID search and exact-target stop | Covered for documented bindings |
| Narrow terminal and resize | Actual120x30→60x18 in every PTY; TestBackend minimum/extreme/Unicode cases; viewport bounded 240x100 | Covered for tested layouts, not every emulator |
| Refresh preserves selection | Same identity survives reorder; changed/absent identity resets detail scroll; exact target IDs survive search/filter; actual new USB address | Covered without carrying old-generation authority |
| Unplug, outage, permission feedback | Actual empty modal/new address; E47 paused-agent stale/recovery and original retry; real service Permission result and retained UUID | Covered for these faults; not permanent OS blocking or every transient event |
| Confirmations cannot replace helper authorization | Default Cancel emits no work; actual Read/Audit-only policy denies both attempts and persists two denials with zero admission | Covered; accepted stop still is not release proof |
| Existing CLI behavior remains tested | Full 956 gate, current original monitor and managed regressions, real USB I/O/proofs, default CLI dependency isolation and minimal TUI selection | Covered for these selections; historical matrices retain their dates |

The documented terminal gate now has software/QEMU evidence. Administrator policy
and credential provisioning remain root-managed outside ordinary-user Settings;
Settings manage the three frontend preferences and readable fixed-profile choice.
This does not authorize arbitrary profile/device changes. Hardware G1/G2, full
G3, general recovery limits and Phases 5–7 remain open. No new storage, isochronous,
real-device, performance or Windows support is claimed.

## Evidence

The [evidence manifest](../evidence/tui-hotplug-permission-2dc888c-20261025/manifest.json)
closes 3286 original files/methods and 1398
redecoded guest artifact blocks. The raw archive is 7,320,626 bytes,
SHA-256 `754e2b1fa84f287cad2c937ab58372c70001ab525d878117e4ab91d076104728`. Original summaries, actual source/Git mapping,
ELF/wait/UID/capabilities/termios, admitted-state identities and final baselines
were checked again without importing run verifiers. This same-author review is
not new USB execution or an independent security audit. No private keys, ELF,
targets or private host inventory output are archived. Test-keyboard metadata
belongs only to the deliberately added disposable fixture.

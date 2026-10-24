# Local USB inventory validation

2026-10-24. The shared local observer and `devices --watch` are implemented.
The final software run records **872 executions at clean `c0346d6`**, with
**206 selected source/build inputs** stable before and after. At clean
`f66da6b`, two separate single-guest QEMU runs pass, one default and one
all-features build. The two binaries also pass six new local process cases.
These observations do not complete the full Phase 0–7 objective or hardware gates.

The [operation guide](../local-device-monitor.md) explains the quality fields,
complete batches and resynchronization. The [design](local-device-monitor-design.md)
retains choices and implementation corrections.

## Behavior and scope

The actual ordinary-user worker binds kernel Netlink group 1 before enumeration,
validates the returned source address, then treats USB device/interface/actions as
scan hints. The original bounded sysfs scanner remains authoritative. A periodic
scan compensates for lost notifications and delayed userspace initialization.

Candidate fingerprint, Bus ID and observational sysfs/USB stamp have distinct
roles. A generation or address change does not inherit authorization. Initializing
and stale inventories never become fabricated empty successful snapshots. Failed
or over-capacity scans retain the last successful inventory without inventing
removals. Slow subscriptions explicitly resynchronize from complete latest views.

The source owns no USB device FD, helper capability, connection lease or business
journal. Cooperative stop wakes its actual blocking worker; shutdown awaits it.
Cancellation cannot return that worker's quota while OS work remains active.
The CLI's own signal route must remain outside the generic helper-operation
cancellation branch; an actual old-product failure established this requirement.

## Revisions and executables

| Scope | Actual revision / selection | Result |
| --- | --- | --- |
| Initial implementation | `213ab92` | Complete gate failed on an initialization test expectation |
| Test correction | `bec0b51` | 872 software executions passed; actual CLI later exposed SIGINT ownership bug |
| Product signal correction | `5eb7f7f` | Local observer owns signal cleanup in the outer CLI dispatch |
| Final complete software and process fixture | clean `c0346d6` | 380 default + 472 all-features + 20 vendor; 51 result rows, zero failed/ignored |
| Final QEMU fixture | clean `f66da6b` | Default and all-features runs pass independently; 213 selected inputs include all 206 software inputs |

Both fmt/Clippy/release/rustdoc selections, vendor tests and Native dependency
isolation pass. The default CLI graph omits the optional service/transport and
Rust engine/nusb/rusb/libusb path. Only the new internal MIT package is added:
the dependency inventory changes 174→175, with external versions unchanged.

| Build selection | CLI SHA-256 | Helper SHA-256 (built/copied, not executed by monitor tests) |
| --- | --- | --- |
| Default | `e85fa916acc9379f58598922c469092aba53726830809c382cbdd9f62e945295` | `1b03da09e343f70b89fc3a3a85f05eeb3da338be1ba559800ee9431dc7de14dc` |
| All features | `d048e41c7dcdc71a2643a9e7ff2c454560838560fd174c9fb2f460333ca7b96d` | `0b68adf4a5d5e3339a05da41792956254fcdfb2e3df74749e7b463c0ae906296` |

The all-features executables match the final software artifacts. The default
executables have their own locked build/payload/actual CLI receipts. Current
source mapping does not relabel software execution at `c0346d6` as a run at
`f66da6b`.

## Actual guest events and current state

Each invocation boots one disposable x86_64 Linux guest with the Ubuntu
6.8.0-106-generic kernel, without a NIC. Its administrator controls only a QEMU
keyboard. The actual CLI runs as UID2000 with zero effective capabilities.
Two live `/proc` samples retain its original PID/start ticks, executable hash
and the same kernel group-1 socket. This is a metadata observer, not USB/IP I/O.

Both feature selections require these completed current snapshots in order:

| Point | Device count | Independent supporting observation |
| --- | --- | --- |
| Initial | 0 | Source already bound before the QMP add |
| Added | 1 | HID interface and usbhid driver; accepted kernel-hint count increased |
| Unbound | 1 | Same candidate/stamp, interface driver absent after guest admin unbind |
| Rebound | 1 | Same candidate/stamp and restored usbhid driver |
| Removed | 0 | Original device absent from sysfs; actual removal index retained |
| Reinserted | 1 | Same candidate and port, new sysfs inode/USB address, independent stat matches |

A separate guest `udevadm --kernel` capture contains add/remove/bind/unbind USB
actions. Point publications must advance actual hint counters and be triggered
by a USB hint or resynchronization, so a periodic-only implementation cannot
pass these predicates. This is not packet-by-packet causality attribution or a
latency benchmark. Complete JSON batches and a final worker-joined receipt are
retained. The actual CLI wait is zero, then QMP removes the final test device
and an independent directory listing verifies the empty non-root-hub baseline.
Both VMs exit zero without forced termination and with all four integrity guards
true. Those guards cover selected sources, Git head/status and binaries; they
are not a sample of host USB state.

## Actual local process cases

The same three cases pass for each build selection, separately from the Cargo
count and guest tests:

1. SIGINT after a complete successful inventory: actual ordinary UID/ELF/source
   sampled, actual exit0 and process gone.
2. Real 4096-byte stdout pipe filled before launch: the actual fd1 write syscall
   and pipe-write wchan are observed. SIGTERM exits0 without draining or rewriting
   the in-flight output; the reader later sees the original prefill and EOF.
3. The same blocked writer is actually stopped, the reader closed, and the
   original child continued: actual exit1 and a Broken-pipe error are observed.

The original four virtual-server signal and two backpressure cases also pass
against the same all-features CLI. The older backpressure fixture lacks per-child
executed-ELF and raw-wire samples; these are not reconstructed.

Raw host device output remains in private local `.private` directories. Public
archive inputs contain only the closed batch projection and own-process
receipts. No real host device is changed or exported. The six new local cases
are not six redirected USB devices or additional software test executions.

## Retained failures and corrections

- Development includes the original default-CLI optional-dependency compile
  error, style failures and two fixture expectation errors. A real regression
  first reproduced the present-device/missing-address false disappearance;
  the scanner now omits an entry only when its directory actually disappeared.
- Clean `213ab92` failed because the periodic test treated Initializing as its
  successful baseline. The corrected fixture waits for Current within the
  original total deadline; it does not sleep or weaken the stale assertions.
- The pre-signal-fix CLI `0c09d192…` returned the exact generic cancelled
  helper-operation error after SIGINT. The first fixture did not persist its
  child wait code; the fresh second old-binary run records actual exit1. The
  product route is corrected rather than changing that expectation to success.
- Clean `47e3f63` first default QEMU timed out and was forcibly recovered.
  The actual QEMU exit0 is not a guest PASS. A post-run jq compile check found
  reserved `$end`; no complete guest execution/point receipts were captured.
- Clean `4cb31f4` reached final removal but lacked the base64 utility during
  collection. PID1 actually exited127 and the kernel panicked. Its original
  host FAIL, forced recovery and exit0 remain. Required utilities are now
  preflighted, and collection failure cannot abort PID1 cleanup.
- Fresh `f66da6b` default and all runs pass with complete artifacts. Neither
  historical QEMU failure is reclassified.

## Evidence and limits

The [raw evidence archive](../evidence/local-device-monitor-f66da6b-20261024/manifest.json)
contains fixed primary-source research/licenses, probes, development and complete
software logs, original failures, public local receipts, both successful guest
runs and methods. A same-author read-only method imports no original
runner/parser/verifier, redecodes 34 blocks, maps all selected Git blobs and
checks 486 successful-run files byte-stable. This is not a new execution or an
independent security audit. The second old-process method was reconstructed
from preserved source changes and matched to its original selection SHA; no
missing process result was reconstructed.

Safe rustix poll/wake avoids a newly unsafe/deprecated Tokio AsyncFd route and
adds no libudev production dependency. Actual unprivileged source/wake probes
observed zero host event datagrams; those probes are not hotplug qualification.
Research-only Linux/systemd code retains its licenses and is not imported into
MIT product code.

No induced OS ENOBUFS, guaranteed transient history, permanent kernel/filesystem
blocking, remote auto-sharing, storage recovery, USB/IP forwarding, real-device
G1/G2, full G3, TUI/GUI, installer, performance or Windows completion is claimed
by this slice. Earlier redirected-I/O reports retain their original revisions.

## Documentation and site checks

The English guide, README and existing page describe the tested scopes separately.
Local checks observed the 872/2/6 metrics and report link at 1280×720 and 390×844,
with loaded images, no horizontal overflow and correct ArrowRight/Home tab, focus
and backend-query behavior. The initial mobile desktop SVG and later mobile SVG
observations are both retained. A CSP no-script preview kept all three panels and
commands readable at its observed 1280-pixel width. Console queries returned no
warnings/errors. Screenshot capture returned `Unable to capture screenshot`;
this is DOM/layout evidence, without pixel review. Clipboard and reduced-motion
checks were not repeated because runtime assets are unchanged.

All six local project-subpath HTTP resources returned 200 with exact source bytes;
the no-script response used the same HTML and explicit CSP header. Temporary
tabs closed, the viewport reset was observed, and the owned loopback preview
exited zero. These checks do not claim successful publication of the new content;
the actual workflows and public bytes receive a separate receipt after pushing.

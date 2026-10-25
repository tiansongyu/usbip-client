# Installed service USB data and live upgrades

2026-10-25. Four targeted paired runs pass on complete Ubuntu 22.04/24.04
amd64 systems. Each performs three real QEMU HID/FTDI I/O rounds, a client
package upgrade with live imports, a server upgrade with live exports and
connections, explicit service reuse, and original resource cleanup.
**Ten development runs remain failed.** An intermittent first-import failure
in one Native/IPv4 run remains unlocated; later success does not establish that
it is fixed. These are synthetic-device observations, not hardware acceptance.

[Design](installed-usb-data-design.md) ·
[Fixture](../../scripts/qemu_installed_usb/README.md) ·
[Exact public evidence](../evidence/installed-usb-data-20261025/summary.json) ·
[Additional archive audit](../evidence/installed-usb-data-20261025/audit.json)

## Actual passing matrix

| Backend / link | Server → client | Test source | I/O rounds | Client / server Root terminals |
| --- | --- | --- | --- | --- |
| Native / IPv4 | Jammy → Noble | clean `d6e68915b94789f8bfc354d21d25eaddfe48f46b` | 3 | 6 / 10 |
| Native / IPv6 | Noble → Jammy | clean `dd9390c460615c3489c24f265c33eb97bc1ee9bd` | 3 | 6 / 10 |
| USBFS / IPv4 | Jammy → Noble | clean `dd9390c460615c3489c24f265c33eb97bc1ee9bd` | 3 | 6 / 4 |
| USBFS / IPv6 | Noble → Jammy | clean `dd9390c460615c3489c24f265c33eb97bc1ee9bd` | 3 | 6 / 4 |

All eight original passing QEMU processes exit0; HEAD, clean-worktree and
actual ELF/package hash guards pass. Twelve passing I/O rounds verify an actual
evdev key press/release and 35 known FTDI bytes in each direction per round.
The 52 Root terminals are resource proofs, not 52 USB compatibility cases.
Both ends of each pair independently pass the unchanged strict PCAP classifier:
30 unidirectional TLS flows each, actual ALPN2/TLS1.3 framing, correct IP family,
real IPv4/IPv6 port3240 RST refusals and absence of the specified clear USB/IP,
URB and synthetic fixture signatures. This does not decrypt TLS.

The new observation-only change at d6e68915 adds pre-cleanup kernel/network and
application security/admission observations. The other three successful runs
remain attributed to dd9390c4; they are not relabeled as d6e68915 executions.

## Environment and delivered package identity

The original official cloud images and signature-verification receipts are
retained in the evidence archive. Jammy image SHA256 is
`2099e63da0b822ef8e05e3d596c1b402eaaae658fc08ab919cb8495bfcdc488b`.
Noble is `04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94`.
Two product-free OS preparation guests at clean bc2b8da8 install distribution
tools/modules and actually power off with exit0. Their private derived-disk
hashes are `58f452030b80cd1a7c9e3747789e2b7f5be042f4bc59c970bcfc8708310ef1a0`
and `ddbef3e9e3db79014961432c2f93d50ed5bfeead62c3a16d2a160145f5ceba2f`.
These prepared bases optimize OS dependencies; they do not replace the separate
[E58 fresh-install lifecycle acceptance](debian-installation-validation.md).

Jammy uses kernel5.15.0-198, systemd249.11 and broker UID112; Noble uses
kernel6.8.0-147, systemd255.4 and broker UID109. Full distribution versions,
actual usbip version, matching module preparation and administrator commands
are retained. Product services use the exact shipped units without overrides
or the earlier fsync gate. Both are initially inactive/disabled; the fixture
explicitly provisions and starts them as administrator. Each role receives only
its own TLS private key. Ordinary operator1000 uses authenticated application
IPC; operator1002 is refused without a device change. Live /proc observations
confirm the actual broker UID and zero agent effective/permitted/ambient/
inheritable capabilities; the helper alone runs as root.

The tested base package is v6
`0.1.0~preview20261025.6-1`, SHA256
`1dc950fc94b4a26877f803b16b6ec66113951c44021bef518ab99e5448a8142b`.
The upgrade is v8 `0.1.0~preview20261025.8-1`, built at clean90916bde;
its SHA256 is
`d36f460f47a6e07812a630a9dc6464ca70841175382ecfb8c12a0a63e847733e`;
the original build receipt is archived.
The intermediate v7
build used an untracked fixture and was not installed in these runs. Product
Rust, Cargo locks, vendor code and package controller/units are unchanged from
the preceding delivery. Package version/source, fixture source and actual
executed ELFs are recorded separately; this is not a claim that the binary was
rebuilt from each fixture commit.

## What each sequence verifies

The server publishes exactly the two administrator-approved stable device keys.
The client discovers that exact catalog, imports both devices through mTLS/v2,
and obtains complete Root VHCI receipts with exact application correlation,
resource ID, key, broker UID/PID and actual usbhid/ftdi_sio downstream drivers.
QMP and a private FTDI chardev inject only test data into emulated peripherals.

With both imports alive, real dpkg upgrades the client. Original agent and
helper owners cooperate, actually exit successfully in that order, and their
original PIDs/cgroups are gone/empty. Package gate output, original invocation
IDs and PID1 successful deactivation/stop journal events are preserved.
Daemon reload can clear inactive service metadata; an empty new field is never
the proof of an old exit. Both exact Root import releases and actual NULL VHCI/
removed old local buses are checked. The server stays running; the fixture waits
for both old incoming owners' terminal verified cleanup before reimporting.

The client explicitly restarts and completes the second actual I/O round.
Real dpkg then upgrades the server while both devices are in use. Exact old Root
Native export/stub or USBFS grant terminals match the application attempts and
restore original generation/configuration/interface/driver/node metadata.
Client VHCI ports actually become NULL. The fixture explicitly stops stale
client jobs, restarts the required services, completes the third I/O round, then
stops both ends and verifies their original baselines again.

Normal cooperative sessions may retain null application attempt proof fields;
the exact corresponding Root terminal is mandatory. Any nonnull echo must match.
A new process with zero resources, PID death alone or a missing journal is never
a substitute. Native final history contains ten server attempts; USBFS has four
grant attempts and no invented helper proofs for its six engine incoming sessions.

Operation errors remain independent from cleanup. Passing histories retain two
client I/O errors after server shutdown; Native incoming histories also retain
State errors, and USBFS histories retain InvalidInput/Cancelled outcomes on the
documented peer-stop paths. Every original terminal session is Disconnected with
verified cleanup. They are not reclassified as clean EOF or a traced reset cause.

## Retained failed development runs

The archive preserves fourteen paired runs: four PASS and ten FAIL, all 28 actual
QEMU exits0 and all source/ELF guards true. The two OS preparation exits are
additional and are not USB data runs.

- 90916bde Native/v4 attempt1: first actual I/O and client upgrade completed,
  but the new verifier wrongly required a nonnull application proof echo.
  Root proofs were present; the original FAIL remains.
- 66daec8b four cases: v4 reached actual I/O/client upgrade but incorrectly
  required inactive InvocationID retention after daemon reload. Native/v6
  listener preparation failed; USBFS/v6 discovery timed out. The private PCAP
  showed a NAT fec0 source while the intended data address was still tentative.
  Explicit DAD/carrier and exact source-route checks were added.
- 3a277055 four cases: Native dual-stack and USBFS/v6 completed three I/O rounds
  and both upgrades, but strict whole-boot PCAP verification rejected early
  zero-source MLD traffic outside the original initramfs contract. USBFS/v4
  failed its second import; the fixture had used published export availability
  without waiting for old incoming owner cleanup. The new barrier adds that
  actual condition without retrying IMPORT.
- dd9390c4 Native/v4 attempt4: HID failed with I/O before a helper attempt;
  serial remained active. Both devices never simultaneously became ready.
  This intermittent failure remains unlocated. d6e68915's independent run
  adds pre-cleanup observations and passes, but does not prove a product fix.

The complete boot PCAP is preserved. Successful runs use a separate unfiltered
capture started after both real DAD/source-route checks and before either product
unit starts, continuing through actual QEMU exit. No packet is removed or rewritten,
and the existing classifier is unchanged. All actual processes are awaited;
observation limits never become fabricated completed owners.

## Software and evidence verification

At clean d6e68915, the original complete `bash scripts/check.sh` and independent
`bash scripts/check-gui.sh` both exit0: root1076 plus GUI18 = **1094** test
executions, zero failed/ignored. Formatting, Clippy, default/all-feature release
builds, rustdoc and vendor tests run through those unchanged scripts.
All **717 tracked non-evidence inputs**, HEAD and clean-worktree guards match.

The resulting actual release ELF hashes also match those used by the packages:

| ELF | SHA256 |
| --- | --- |
| CLI | `54a67744f657fa79f4c184341db77f89affda88a35c470e62f5225ed92259bd8` |
| Helper | `475b2f97e8b4e31dd1af479193913c637d09dc8c3b1debd13442c0f88f6748f3` |
| GUI | `5c9ec587bf2bcb385da6af26824567572f094cbc000c9a472b7787ce6bef9ba9` |

The public archive contains **3322 exact members / 22,624,065 raw bytes**.
Archive SHA256 is
`35311dabe4610d4b1364d322a82319428e7cb1be19797562f4f58a1a0ef6007c`
(4,305,493 bytes). Every member is round-trip hash/size checked. An additional
read-only audit verifies the actual unit bytes, broker identity/capabilities,
I/O and package-stop observations from the archive. Collection checks 100 actual
generated private-key byte strings for accidental inclusion. Private keys,
disks, ELF/deb bytes and signed download capabilities stay outside Git.
The four global readme-site skill files and Codex symlink remain unchanged.

The English README and project page link this scope and its retained intermittent
failure. Local project-prefix checks pass254 asset/link/SVG checks; JavaScript
syntax passes. DOM checks observe desktop1280/document1265 and actual
mobile390/document375, a keyboard-focusable new report link with 2px outline,
and a private script-omitted fixture with all three backend headings, commands
and limitations visible. Initial stale viewport observations were1280 and are
not mobile evidence. Four owned tabs close, viewport resets, and the owned
loopback preview actually exits0 after INT. No new pixel screenshot review is
claimed. Publication is recorded separately after actual push/deployment.

Content commit `a028cb1bc20f6500db586b6f516c5263cceb040d` was normally pushed and
confirmed as remote main. Its [core CI](https://github.com/tiansongyu/usbip-client/actions/runs/40197552464),
[GUI CI](https://github.com/tiansongyu/usbip-client/actions/runs/40197552421)
and [Pages workflow](https://github.com/tiansongyu/usbip-client/actions/runs/40197552419)
all complete successfully: four native Ubuntu jobs per software workflow and
both Pages jobs. Four Debian artifact metadata records are retained; this
delivery does not download, inspect or run those CI binaries. The previous
artifact inspection remains at its original revision. Live HTML and six runtime
assets, plus pinned English README/report, all return200 and match source bytes.
Two initial generic asset traversals requested the hidden `.nojekyll` deployment
marker and got404; the corrected explicit runtime selection passes. The original
errors and discarded maintenance-README hypothesis are retained. See the
[publication receipt](../evidence/installed-usb-publication-a028cb1b-20261025/summary.json)
and its 31-member exact public manifest. This later receipt does not reattribute
local1094 tests or QEMU runs to a028cb1b or to the receipt commit.

These observations do not establish repeat-failure freedom, real G1/G2 devices,
arm64 installed runtime, arbitrary Composite/ISO support, storage safety,
permanent blocked-kernel/FS cancellation, power-loss recovery, full Phase1/2/3/5
acceptance or Windows USB. Prior results retain their own exact revisions.

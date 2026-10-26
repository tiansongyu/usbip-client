# ARM64 cold package installation and native GUI lifecycle

2026-10-26. Complete, fresh Ubuntu22.04/24.04 ARM64 QEMU cases pass the original
install → guarded upgrade → actual reboot → remove → purge sequence, including
an ordinary-user native GUI window and cooperative WM close0. All four actual
QEMU processes exit0, with no forced cleanup. **ARM USB data I/O and physical
hardware remain unvalidated.** This does not complete Phase5 or the full project.

[Design](arm64-installed-validation-design.md) ·
[Product-free environment evidence](arm64-environment-validation.md) ·
[Method](../../scripts/qemu_arm64/README.md) ·
[Exact public evidence](../evidence/arm64-cold-installation-20261026/summary.json) ·
[Archive consistency audit](../evidence/arm64-cold-installation-20261026/audit.json)

## Actual matrix and source identity

| Fresh system | Actual guest | Source | Check records / distinct labels | Actual QEMU exits | Broker UID |
| --- | --- | --- | --- | --- | --- |
| Ubuntu22.04 ARM64 | aarch64, kernel5.15.0-198, systemd249.11 | clean `76b4e7a35c5fae0308c24c538c66617745247137` | 49 / 42 | 0 / 0 | 112 |
| Ubuntu24.04 ARM64 | aarch64, kernel6.8.0-147, systemd255.4 | same clean76b4 | 50 / 42 | 0 / 0 | 108 |

All original guest assertions pass. Repeated GUI-alive polling creates different
record counts; these are not49/50 distinct tests or USB compatibility cases.
The actual dynamic broker UID is read in each guest, not assumed from amd64 or
from the separately prepared ARM disks. Each run's source/binary/head and the
additional clean/adapter/QEMU/firmware/package hash guards pass. Actual uname,
dpkg architecture and PID1 are checked before any package action.

The original complete root and separate GUI gates also pass at clean76b4:
1096+18=1114 test executions, zero failed/ignored,731 tracked non-evidence inputs
and HEAD/clean/input guards true. Host release CLI866b0751/helper475b2f97/GUI7bdb41f7
remain byte-identical to E60. This is host software evidence; ARM binaries are
the separately inspected native CI0316 packages, not those host ELFs. Product
Rust, Cargo locks, vendor, packaging and CI source are unchanged from CI0316 to
the fixture source. No new Rust dependency or permission is introduced.

## Actual packages, firmware and test fixture

Both cases start from the original official dated ARM64 images, with independently
verified signed checksums and actual image hashes in the environment checkpoint.
Prepared product-free disks do not replace these cold installs. The private
Ubuntu QEMU6.2/EDK2 tools select virt-6.2/cortex-a72/TCG with persistent private
firmware variables and four emulated CPUs. No host service, account or USB is
changed; this generic platform is not a physical ARM-board compatibility test.

First packages are the matching native CI0316 base+GUI pairs, version
`0.1.0~ci25+g0316de7638c6-1`. Original ZIP API digests, each package and all six
actual AArch64 payload ELFs are independently checked. Second packages are the
audited `+armtest1` revisions: only control Version and GUI exact base-version
Depends change. Every payload byte/mode/owner/link, maintainer script and other
control member compares equal. Separate derivation receipts record generator,
original and derived identities. This tests a same-source package upgrade; it is
not a native rebuild, old-code migration or mixed-preview protocol compatibility.

The original fsync-gate.c is compiled with GCC **inside each actual ARM guest**.
Its source SHA256 is
`56a50dc6aeca27b019774ae5d8808b395cbe1aba9d0aafafb49a90bab8280505`.
Actual AArch64 interposer hashes are Jammy
`22d1c29b7baf860dfc1ed8f589713ded6c8f5e2cc2f9bde984d6e40122546b40`
and Noble `0e273749e3554ec638010ea49090e079511c95fd796f1978197780f5232feb62`.
Guest runtime hashes match the compiled libraries; entered PID/UID matches the
original broker, and the same PID records actual fsync return0. This fixture is
guest-only and does not enter the product package.

## What the original assertions establish

Fresh installs leave both services disabled/inactive, no live policy/manifest
or payload conffiles. Explicit administrator provisioning creates the virtual
profile; the ordinary operator queries profiles and starts the virtual job.
Wrong mode/owner/symlink leaves are refused without repair or data loss. Actual
Xvfb/openbox and a packaged ordinary-user native GUI window run, receive
WM_DELETE_WINDOW and return0. These window tests do not repeat the separate
amd64 GUI action, layout, automatic-rule or USB fault matrices on ARM.

All three unsafe stop overrides are rejected by both old/new upgrade gates
before signaling the original broker; its PID, active state, version and CLI
bytes remain. The real upgrade then waits while the original broker is inside
its real journal fsync. The helper remains available, original CLI inode and
package version remain, and the controller releases the original syscall. Dpkg
actually returns0 and both units are cooperatively stopped. The original job
journal remains. This is not cancellation of an arbitrary permanent FS block.

Explicitly enabled services start after the actual QEMU reboot; the kernel boot
UUID changes and the old job identity remains. Remove/purge remove package
executables and enable links while retaining the original UID, configuration,
credential bytes, private state/journal, helper-state sentinel permissions and
administrator mask. Sentinels and virtual jobs are not recovery proofs for old
physical devices or unknown journals. A new process with zero resources never
stands in for old Root cleanup.

## Method, failure record and limits

The ARM adapter composes exact original host anchors and retains original guest.py,
C source and GUI budgets. Host platform launch, guest-native C compilation,
explicit architecture checks and actual-owner observation loops are adaptations;
the entire host runner is not byte-identical. Observation intervals do not kill,
restart or declare an actual owner complete. Original amd64 files stay unchanged.

Both first actual cold runs pass. A separate read-only progress observer initially
misreads a `.started.json` as a completed command and exits1; filtering those
metadata files fixes only the observer. Both original observations remain in the
archive and never change the product verdict. Earlier setup prototype failures
remain in the environment checkpoint.

The cold archive has673 exact members (1,658,023 raw bytes), gzip279186B, SHA256
`f76089cb926fa08c41b0b1a3830a6f372a183a1e8aa950bc8c13650cf9ab24e2`.
Original nested guest tar members are separately hashed and inspected. Fourteen
actual private-key file byte sequences, including private copies, are checked
absent. Images/disks/variables, ZIP/deb/ELF bytes and private directories are
excluded. The additional audit checks dynamic UID/PID, actual library/source,
syscall result and unchanged product source. It is by the same author, not an
independent implementation review or per-live-GUI /proc/ELF attestation.

The global README/site skill's four files and Codex symlink retain their delivered
hashes. ARM Native/USBFS I/O, general physical USB compatibility, power loss,
permanent kernel/FS blocks, storage/ISO, Windows USB and full Phase0–7 remain open.
Original E59 intermittent failure cause and E60 mixed-preview FAIL retain their
scope. Publication/CI results are recorded separately after actual push.

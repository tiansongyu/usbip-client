# Debian package lifecycle validation

2026-10-25. Two complete, fresh Ubuntu **amd64** QEMU installations pass the
install → upgrade → real reboot → remove → purge sequence. This verifies the
experimental base/GUI packages and systemd lifecycle described in the
[installation guide](../debian-installation.md), within the scope below.
Phase 5 and the full project remain incomplete.

## Current signal-guard follow-up

Final package/source and new complete cold validation are now pinned to
`0ad3a6ee14be720587962150d625e585f3f18909`, using preview **5 → 6**.
The guard also verifies loaded `KillSignal=15` and `SendSIGHUP=no` before sending
any signal. The earlier final-KILL check alone did not cover an abrupt initial
signal override; default shipped units were already correct.

Both Ubuntu amd64 guests repeat the complete fresh lifecycle successfully:
**44 records / 42 distinct assertions per guest**, four actual QEMU exit0,
no forced cleanup, all source/ELF/HEAD guards true. Three actual failed upgrades
per guest (`SendSIGKILL=yes`, `KillSignal=SIGKILL`, `SendSIGHUP=yes`) exercise both
old prerm/new failed-upgrade gates and preserve the original live broker PID and
old package before signalling. The journal wait, GUI WMclose0, real reboot,
remove/purge, retained configuration/UID/journals/sentinels and mask checks pass.
The original complete software gates at that same clean source again pass
1076 + 18, zero failed/ignored, with all 451 inputs held and original f174 ELFs.

[Follow-up summary](../evidence/debian-stop-signals-20261025/summary.json),
[member hashes](../evidence/debian-stop-signals-20261025/members.json) and
[raw evidence](../evidence/debian-stop-signals-20261025/raw-evidence.tar.gz) contain
666 reopened/verified members, 8,929,162 uncompressed bytes. The 1,768,564-byte
archive SHA256 is `78d8d4f794232dd515c0aef09b6bd6f263c7d5a0fbd6e36700a7681396a6e5e3`.
Only named cooperative unit settings are checked; arbitrary trusted-root changes,
administrator/kernel kill and permanent blocking retain their existing limits.

The original v3 → v4 evidence below remains attributed to its actual earlier
source. The new guard does not relabel those executions or expand USB/arm64 scope.

## Original source and artifacts

| Item | Actual source |
| --- | --- |
| Initial design | `5f352e99`, [design](debian-installation-design.md) |
| Final package code and local `.deb` builds | `22d3c485c70eaf5b036a2b632771f559b4c88ad0` |
| Final complete fresh QEMU and software gates | `fded558ad7183e55f40c36c2736243e971c1422f` |
| Rust core, CLI/helper/GUI release bytes | Unchanged from `f174f434f7e86659692226fcd9b7e43b87a57e15` |

Local tested versions are `0.1.0~preview20261025.3-1` and the higher
`0.1.0~preview20261025.4-1`, each with `rustusb` and `rustusb-gui`. Their actual
control/data archives were inspected: base 539 members, GUI 964; root ownership,
no setuid/setgid bits, no live `/etc` policy or persistent-state payload, actual
ELF identity, desktop `--manifest` argument, 64px icon and license bytes passed.
The conservative target-resolved license inventories contain 188/345 nodes,
including build/dev dependencies; they are not linked-only per-ELF SBOMs.
The GUI includes unmodified selectors MPL source and the original license text.
Native dependencies are calculated by `dpkg-shlibdeps`, not a guessed SONAME list.

CI is configured to build matching base/GUI preview artifacts on the four existing
Ubuntu distro/architecture cells after the original GUI gate. Those build outputs
are separate artifacts, not the local `.deb` files exercised here. The publication
receipt records their actual observed outcome when available. They do not imply
arm64 VM/USB acceptance or a signed release/APT repository.

## Actual full guest environments

Official dated cloud disks were downloaded with SHA256SUMS and detached signatures;
`gpgv` using the installed Ubuntu cloud-image keyring succeeded, followed by an
independent actual image SHA256 comparison. No host USB passthrough or host system
service/account/module change occurred. QEMU uses a writable private overlay,
NoCloud data and loopback-only SSH forwarding. Private keys, disks, `.deb` and
ELF bytes are excluded from the public evidence.

| Observed item | Ubuntu 22.04 | Ubuntu 24.04 |
| --- | --- | --- |
| Official image date | 20261020 | 20261017 |
| Image SHA256 | `2099e63da0b822ef8e05e3d596c1b402eaaae658fc08ab919cb8495bfcdc488b` | `04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94` |
| Kernel | 5.15.0-198-generic | 6.8.0-147-generic |
| systemd | 249.11-0ubuntu3.23 | 255.4-1ubuntu8.18 |
| dpkg | 1.21.1ubuntu2.6 | 1.22.6ubuntu6.6 |
| init-system-helpers | 1.62 | 1.66ubuntu1 |
| Actual GTK package/version | libgtk-3-0 3.24.33-1ubuntu2.2 | libgtk-3-0t64 3.24.41-4ubuntu1.3 |
| WebKitGTK 4.1 | 2.50.4-0ubuntu0.22.04.1 | 2.52.6-0ubuntu0.24.04.1 |
| Actual dedicated broker UID | 112 | 109 |

The Noble wildcard package query also lists an empty legacy `libgtk-3-0` entry;
that is not a second installed GTK version. Its actual runtime is the t64 package.
Both installations resolve and run the same Jammy-built tested preview packages.

## What the two final cases executed

Each final case contains 42 recorded assertions / 40 distinct labels, all passing;
polling may repeat labels. These are not additional Rust unit-test counts.

1. Fresh install leaves both services disabled/inactive, creates no live policy
   or manifest and ships no conffiles. `systemd-analyze verify` succeeds.
2. Actual wrong mode, wrong owner and symlink leaves are refused without recursive
   repair or loss of the existing sentinel. The fixture preserves/restores the
   runtime leaf created by an earlier failed preparation.
3. Explicit administrator configuration uses the actual dedicated UID in the
   helper policy, TLS profile and public manifest. An ordinary operator performs
   authenticated IPC. A real ordinary-user Tauri/WebKit window appears under
   Xvfb/openbox and exits **0** through WM_DELETE_WINDOW.
4. The ordinary operator starts a real loopback TLS virtual HID export job, and
   its original session reaches Available. No client imports it in this suite.
5. An unsafe SendSIGKILL override makes both the old prerm and new failed-upgrade
   fallback refuse replacement; the old package version and ELF remain.
6. An explicitly identified **test-only LD_PRELOAD library/drop-in**, outside the
   product package, holds an actual agent journal fsync. It records the original
   PID/UID and FD inode. During the hold, dpkg remains pending, the same broker
   remains deactivating, the helper stays active, and the old package version and
   actual CLI inode remain unchanged. Releasing one FIFO byte calls the real
   libc fsync and records its actual return0; the real dpkg child is then awaited
   and returns0. No synthetic successful IO or forced process termination is used.
7. Upgrade leaves both services cooperatively stopped. The old application job
   identity remains in the journal. The administrator explicitly enables boot.
8. A real reboot terminates the first QEMU process0, then the same disk boots in
   a second QEMU process. The kernel boot UUID changes, the explicitly enabled
   services become ready and the original job identity remains.
9. Actual remove and purge delete all package executables and enable links while
   retaining the broker UID, all administrator configuration/credential bytes,
   application journal, data sentinel and a root-owned 0600 helper-state sentinel.
   An administrator's `/dev/null` service mask survives. The helper sentinel is
   an ordinary synthetic retention file, **not** an unresolved USB grant/proof.

The two complete cases produce **four actual QEMU exit0**, no forced cleanup,
successful evidence collection and unchanged source/ELF/HEAD guards. The actual
native GUI processes both exit0. This is authored acceptance tooling, not an
independent implementation audit or a full accessibility/visual regression.

## Failures and development continuations retained

Five earlier invocations remain failed/uncompleted in their original summaries:

- The first Jammy dependency download was manually SIGINT-cancelled too early.
  Subsequent full logs showed slow continuing downloads, not a complete network
  outage. RustUSB had not been installed; the actual APT child was -2.
- The next two fresh attempts failed constructing the symlink test because the
  earlier directory refusal had already created the runtime leaf.
- Two explicitly scoped continuations failed the SIGSTOP wait assumption.
  systemd intentionally sends SIGCONT after the initial stop signal, as documented
  in the [Jammy kill procedure](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.kill.5.html).
  Their old finally block then encountered a missing PID before it could wait
  for dpkg; no dpkg exit0 is retroactively invented from the completed log text.

The next two development continuations pass their remaining lifecycle checks,
including real reboot/removal, but explicitly set `fresh_install_acceptance=false`:
they inherit the original initial guest state. They are not counted as the final
two complete fresh cases. Across all nine invocations, the original five failures,
two scoped development passes and two full fresh passes remain separate.

## Software and evidence

At clean `fded558a`, the complete original `scripts/check.sh` passes **1076** tests
and separate `scripts/check-gui.sh` passes **18**, with zero failed/ignored.
Formatting, Clippy, release builds and strict rustdoc pass. All **451** frozen
inputs, HEAD and clean-state guards hold. The three release ELF hashes remain
the original f174 bytes; new package/test tooling does not acquire those older
USB regression results as new execution.

[Evidence summary](../evidence/debian-installation-20261025/summary.json),
[member hashes](../evidence/debian-installation-20261025/members.json) and
[raw archive](../evidence/debian-installation-20261025/raw-evidence.tar.gz) preserve
883 original/source/method members, 10,939,952 uncompressed bytes. The 2,115,735-byte
archive SHA256 is `e014f027bc1d62215e5ab2d87964246eb010b24094a573c5c6f8088eb6334635`;
every member was reopened and verified. Primary Debian/systemd research captures
retain their upstream terms and are not imported into the product package.

The final cold tests validate the named Ubuntu amd64 environments and finite
controlled filesystem wait. They do not prove arbitrary permanent kernel/FS
blocks are cancellable, sudden power-loss durability, Windows USB, arm64 service
installation, Native/USBFS USB I/O under these new units or physical G1/G2.
Existing helper startup signal registration and legacy kernel-session ownership
limits remain. Process exit or new-epoch zero counters never retire old USB
records; uninstall preserves evidence and performs no root reconciliation.

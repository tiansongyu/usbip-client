# Debian installation lifecycle design

2026-10-25, proposed implementation and validation scope. The starting clean
commit is `412b40ff941d74ff80f1ca0cc4d86aade207a2d4`. Phase 5 remains incomplete.

## Problem and package boundary

The current CLI/helper and separate Tauri GUI have process and initramfs QEMU
evidence, but no Debian package, systemd unit or full installed Ubuntu reboot
acceptance. Add `rustusb` (CLI/TUI, restricted helper, administrative lifecycle
tool, units, examples) and optional `rustusb-gui` (ordinary-user desktop app).
Build native ELF files first; record their actual hashes and derive shared-library
dependencies with the build distribution's `dpkg-shlibdeps`. Do not relabel an
amd64 build as arm64, or claim newer-distribution binaries run on older Ubuntu.
The GUI package depends on the matching base package and its actual GTK/WebKit
libraries. No new Rust dependency or USB data path is proposed.

## Default installation and administrator state

Installation creates a dedicated non-login, nonzero `rustusb` system account.
An existing incompatible account or unexpected directory owner/mode is an error,
never an excuse for recursive chown. Its UID is a persistent authorization identity
and is retained on removal. Unit files and binaries are package-owned; examples
are under `/usr/share/doc/rustusb/`, not installed as live `/etc` conffiles.
Administrators explicitly provision `/etc/rustusb/policy.json`, the public
`agent.json`, TLS material and optional rule policy. No package script grants a
device, guesses an operator UID, generates credentials, starts usbipd, loads
modules or changes firewall/network policy. Installation and upgrades never
automatically enable, start or restart either service.

Remove/purge retain administrator configuration, credentials, the broker account,
helper Native/physical journals and application data, including unresolved old
epochs. Dpkg removes conffiles before `postrm purge`; therefore sensitive retained
configuration must never enter the package's conffile payload. No `rm -rf` or
automatic stale socket deletion is allowed. An administrator may inspect and
coordinate old records using the existing root proof interfaces; package removal
does not prove device release or authorize clearing them.

## Service lifecycle

Use systemd directives available in Ubuntu 22.04 systemd 249 as well as Ubuntu
24.04 systemd 255. The helper runs as root; the application agent runs as the
dedicated broker. Both invoke their real executable directly with `Type=exec`,
`Restart=no`, `KillMode=mixed`, `SendSIGKILL=no` and `TimeoutStopSec=infinity`.
The helper is ordered before the agent; BindsTo/After/PartOf on the agent stop new broker work if the helper exits and propagate explicit helper restart. This cannot restore an already-dead helper. Shutdown
stops the agent first, preserving the helper for actual cooperative cleanup.
`Type=exec` means execve succeeded, not that the socket/listener is ready.

A fixed-command root administrative tool checks/creates only the fixed package
runtime/data directories. It checks protected ancestors and exact final ownership
and modes and never rewrites an existing tree. Avoid systemd's recursive ownership
repair through RuntimeDirectory/StateDirectory. The agent's privileged prepare
step also verifies its root-managed manifest names the actual service UID and
package endpoint/data paths. Product configuration loaders remain authoritative.

Before upgrade unpack or removal, `prerm` waits for agent stop, verifies the unit's
successful main exit and empty actual cgroup, then does the same for the helper.
Any failure stops the package transaction before files change. `systemctl stop`
return 0 or `inactive` alone is insufficient: ExecStop failures can be hidden and
finite timeout stops can leave processes behind. No timeout sends SIGKILL. If
cooperative cleanup is blocked, the package operation remains blocked; an observer
deadline must not be reported as a completed stop. Retained old state is never
interpreted as recovered merely because these processes have exited.

Known source limit: the helper currently registers SIGTERM after journal opening
and listener setup. Unit directives cannot make a permanently blocked startup
cooperative. The initial package tests must distinguish ready-state cooperative
stop from startup readiness; no package or hardware claim erases that limitation.

## Validation plan and evidence

1. Preserve the existing complete root and GUI gates and original verifiers.
2. Inspect both package control/data archives, dependency metadata, permissions,
   scripts and actual ELF identity. Include malformed path/ownership refusals.
3. Boot official signed-checksum Ubuntu 22.04 and 24.04 amd64 cloud images with
   NoCloud data in disposable full-system QEMU disks. Save image provenance,
   package manager/systemd versions, raw command outputs and actual exits.
4. Exercise fresh install (no services or live authorization), explicit safe
   configuration and start, ordinary-user authorized IPC, cooperative stop,
   upgrade, reboot, remove and purge. Verify UID/config/state retention and no
   unintended start; test stop failure without deleting evidence. Synthetic USB
   work stays within disposable guests; do not pass through host USB.
5. Record failures as failures, source/ELF/package hashes and final clean commit
   attribution before publication. Full lifecycle/GUI desktop and real USB scope
   only pass when their actual assertions have executed.

Primary-source research is being saved separately for Debian maintainer-script
ordering and the two supported systemd versions. Official image provenance:
[Jammy release](https://cloud-images.ubuntu.com/releases/jammy/release/),
[Noble release](https://cloud-images.ubuntu.com/releases/noble/release/),
[signed checksum verification](https://ubuntu.com/docs/public-images/public-images-how-to/verify-image-checksum/),
[NoCloud datasource](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html).
No complete Phase 1/2/3/5/6/7 or physical G1/G2 acceptance follows from packaging.

Validation refinement: the original SIGSTOP holding assumption failed in the two
explicit development continuations. The official
[Jammy kill procedure](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.kill.5.html)
documents automatic SIGCONT after the initial stop signal. Use an explicitly
identified guest-only LD_PRELOAD fixture to hold an actual agent journal fsync
before calling the real syscall; a controller byte releases it and the real
return is recorded. Keep the test library/drop-in outside the product package,
retain the original failures and do not equate this finite controlled wait with
cancellation of permanent storage or kernel blocks.

Pre-publication guard refinement: default units already use initial SIGTERM and
no extra SIGHUP, but the package stop guard must inspect both loaded properties
before signalling. Reject an initial KillSignal other than 15 and SendSIGHUP=yes,
as well as the existing mode/final-KILL/timeout/restart checks. A final-KILL check
alone cannot prevent an administrator override from selecting an abrupt initial
signal. Add actual rejected upgrade attempts for initial SIGKILL and extra HUP;
each must preserve the live original broker and old package before any stop.
This is a check of named cooperative settings, not protection from arbitrary
trusted-root unit/executable changes or kernel/administrator kill.

Research digests: [Debian](debian-lifecycle-research.md), [systemd](systemd-lifecycle-research.md). The new preinst and failed-upgrade prerm must repeat the installed stop gate; a failed old prerm must never fall through to a successful no-op. Configure records potential WantedBy links using deb-systemd-helper update-state, without enabling. Remove disables those recorded links; purge drops only that package bookkeeping, preserving administrator masks. Reject nonempty DPKG_ROOT; upgrades/removals require the actual booted systemd manager, not a shared-run chroot.

Binary distribution also installs a conservative target-resolved Cargo license inventory (including build/dev nodes), original cached license/notice texts and fixed-revision supplemental texts where crates omit them. Ship unmodified selectors MPL source in the GUI package and identify its license steward/source URL; do not portray the inventory as an exact linked-only SBOM. Native GUI CI builds matching base packages after the original GUI gate and uploads preview artifacts for the four existing distro/architecture cells. Artifact builds are not full installed VM/USB acceptance.

# ARM64 installed Ubuntu validation

2026-10-26, started at clean `f9fdcdf91eca361c2286bc3a4c7931970f88eee9`.
The preceding E60 turn made verified progress. The full Phase0–7 objective stays
active; real-device G1/G2 and ARM installation are not completed by native CI.

## Gap and intended evidence

Ubuntu22.04/24.04 aarch64 binaries pass native CI, but the full installed-system
fixtures select x86_64/KVM. Extend test infrastructure to actual ARM64 full-system
guests without changing product Rust, package permissions or existing verifiers.
Native CI builds, emulated ARM kernel/runtime and physical ARM hardware remain
separate evidence classes. TCG timings are not product performance benchmarks.

First establish reproducible boot and product-free dependency preparation for
both official ARM64 Ubuntu cloud images. Verify their original signed SHA256SUMS
with the installed Ubuntu cloud-image keyring, independently hash image bytes,
record date/URL/size and preserve signatures. Copy-on-write disks, NoCloud seeds,
SSH keys, TLS keys and firmware variables remain private. No host USB passthrough,
module loading, account creation or service installation is authorized by this
test method.

Use the existing host's matching Ubuntu-packaged QEMU6.2 ARM executable and EDK2
firmware, downloaded and extracted into a private tool directory rather than
installed. Check package hashes against APT metadata and executable dependencies.
The versioned `virt-6.2` machine, explicit 64-bit cortex-a72 CPU, TCG accelerator,
PL011 console, virtio disk/network and PCI xHCI devices are proposed. Freeze the
actual binary/firmware hashes with each run. Validate actual supported command
options before boot; no `-cpu host`/KVM claim on this x86_64 host.

Reuse the original guest SSH runner, exact recorded commands, actual QEMU owner
and cooperative poweroff/join. Add an explicit architecture adapter with default
amd64 behavior preserved. Do not edit original application/Root proof checks or
weaken stop/PCAP/driver/I/O assertions. Guest uname, dpkg architecture, PID1 and
ELF machine/actual hash must demonstrate aarch64/arm64 execution. A timeout is
an observation, not a terminated VM; retain and inspect the same actual handle.

Then run matching native ARM CI artifacts from content0316de76. Download via the
GitHub artifact endpoint, verify API digest and each package receipt/ELF machine,
and retain temporary signed download capabilities only in private files. Do not
use a renamed amd64 package or newer-Ubuntu ELF to claim older-Ubuntu support.
Installation evidence must execute the original fresh install, administrator
provisioning, ordinary-user IPC/GUI lifecycle, guarded live upgrade, reboot,
remove/purge and retained-state assertions. USB evidence must separately execute
actual HID/FTDI I/O, live client/server upgrades, original Root correlations,
driver/config/interface/node/generation baselines and strict dual-ended PCAP.
Where two increasing same-source preview versions are needed, rebuild the same
source natively on the corresponding ARM environment or produce a separately
audited repack; do not alter package version fields while retaining an old build
receipt. The chosen method and actual package identities must be recorded before
accepting an upgrade result.

Keep source/HEAD/host ELF files frozen through actual local gates and QEMU owners.
Preserve every failure and distinguish environment setup, boot, package lifecycle,
device I/O and cleanup. Update design before any material scope change. Archive
only explicit public outputs, source and hashes; commit meaningful units and
normally push to the authorized origin. Existing software1114, QEMU1b59/393 and
remote0316 receipts keep their original identities. This plan is not acceptance.

## Primary sources examined

- [QEMU6.2 virt documentation](https://github.com/qemu/qemu/blob/v6.2.0/docs/system/arm/virt.rst):
  this generic emulated board supports PCI/flash/PL011 and requires an explicit
  64-bit CPU for AArch64; current master is not the executable's version contract.
- [Jammy dated official images](https://cloud-images.ubuntu.com/releases/jammy/release-20261020/)
  and [Noble dated official images](https://cloud-images.ubuntu.com/releases/noble/release-20261017/):
  ARM64 img and signed checksum files are published separately from amd64.
- Existing [package design](debian-installation-design.md),
  [installed USB method](installed-usb-data-design.md) and
  [framed-close migration contract](framed-management-race-validation.md) remain
  authoritative for unchanged product lifecycle and mixed-preview restrictions.

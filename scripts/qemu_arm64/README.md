# ARM64 installed-system validation

This adapter runs the original product-free Ubuntu preparation commands and VM
owner lifecycle under actual aarch64 emulation. It does not turn the prior
amd64 package/USB evidence into ARM evidence. See the
[scope and sources](../../docs/research/arm64-installed-validation-design.md).

Provide independently signed-checksum-verified dated ARM64 image directories
(`jammy` and `noble`, with actual image and receipt.json) and an extracted matching
Ubuntu QEMU ARM/EDK2 tool root. No host package installation is required:

```sh
python3 scripts/qemu_arm64/prepare.py \
  --tool-root /private/extracted-tools/usr-root \
  --images /private/verified-arm64-images --output /private/arm64-prepared
```

The tool root contains `usr/bin/qemu-system-aarch64`, AAVMF_CODE.fd and
AAVMF_VARS.fd under `usr/share/AAVMF`. Original amd64 scripts stay unchanged.
The adapter explicitly selects virt-6.2, cortex-a72 and TCG, creates a private
firmware-variable copy and verifies guest uname/dpkg/PID1 before the original
preparation. It preserves original live owner waits rather than treating an
observation deadline as termination. Inputs and generated-method hashes are saved.

Output contains private disks, SSH keys and NoCloud seeds. Never publish it
wholesale. Product-free boot/preparation, package lifecycle, actual USB I/O and
hardware acceptance are separate gates.

The cold-install adapter composes the original package runner with explicit ARM
launch, actual guest-native compilation of its unchanged fsync interposer and
actual-owner observation waits. The original guest assertions and GUI budget are
unchanged. Native CI packages are first; use independently audited higher-version
same-source pairs for the upgrade. Original signed images are required here:

```sh
python3 scripts/qemu_arm64/derive.py --source /private/ci-pairs \
  --output /private/higher-pairs
python3 scripts/qemu_arm64/package.py --tool-root /private/extracted-tools/usr-root \
  --image /private/ubuntu-22.04-server-cloudimg-arm64.img \
  --image-receipt /private/jammy/receipt.json \
  --first /private/ci-pairs/jammy --second /private/higher-pairs/jammy \
  --evidence /private/arm64-jammy-cold
```

Repeat separately with the matching Noble image and package pair. Preserve
generated source, interposer source/library/AArch64 proof, actual reboot exits,
original lifecycle evidence and independent ARM source/tool guards. A true
failure in the inherited method remains failed, including any explicitly recorded
forced cleanup; no observer expiry can count as a successful process stop.

`derive.py` expects matching `jammy`/`noble` directories with their original
base+GUI pair and clean ARM CI build receipt. Independently verify the API/ZIP
digest before using them. It compares every payload member and control file,
allows only Version/GUI exact Depends changes, and records its own generator hash
and a distinct derivation receipt. It neither executes nor recompiles a binary.

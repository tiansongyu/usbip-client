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
hardware acceptance are separate gates. This directory currently provides only
the first method; package/USB acceptance must retain all original assertions.

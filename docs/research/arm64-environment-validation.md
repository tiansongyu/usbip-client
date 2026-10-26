# ARM64 full-system environment checkpoint

2026-10-26. Product-free Ubuntu22.04/24.04 ARM64 guests both pass original OS
preparation at clean `dd3f93d8`. Both actual QEMU processes exit0; HEAD/clean
guards pass. This is boot/tool/module and input evidence, **not RustUSB package,
GUI, USB I/O or physical ARM acceptance**.

[Design](arm64-installed-validation-design.md) ·
[Methods](../../scripts/qemu_arm64/README.md) ·
[Exact public evidence](../evidence/arm64-environment-20261026/summary.json)

The QEMU6.2 ARM executable (`1:6.2+dfsg-2ubuntu6.31`) and EDK2 firmware package
(`2022.02-3ubuntu0.22.04.7`) are downloaded, checked against APT SHA256 metadata
and extracted privately. Host services, accounts and USB are unchanged. Actual
launch selects virt-6.2, cortex-a72, TCG, private persistent64MiB firmware
variables, virtio disks/seed/network and PL011. It does not emulate a specific
physical ARM board or establish native hardware performance.

Each guest executes `uname -m = aarch64`, `dpkg --print-architecture = arm64`
and systemd PID1 checks before the unchanged original preparation. Jammy uses
kernel5.15.0-198/systemd249.11; Noble6.8.0-147/systemd255.4. Matching USB/IP tools
and usbip_host/VHCI/HID/FTDI/xHCI modules are actually installed and loaded. There
is no RustUSB binary or live authorization in these prepared disks.

| Input / derived disk | SHA256 |
| --- | --- |
| Official Jammy ARM64, signed dated20261020 | `e720ae083f93baae47ac9b76a096243fe1027f60998cf1127a47a74c880f64ed` |
| Official Noble ARM64, signed dated20261017 | `50e24e1fb79e89116e939d90aa7738f7af48c442fa1bc2510131e0b39c50348e` |
| Jammy product-free prepared disk | `11afa405bfdf72300df216bf9754f468d242e1b517c92d28d33627d9611f3eb8` |
| Noble product-free prepared disk | `e56d1589f824ea51f70f9a9284dcafd360915d7e7c2be7d098d710db7022c12e` |

Original dated signed checksum files are independently reverified with the Ubuntu
cloud-image keyring for the ARM64 entries, and actual images are rehashed. Both
original live owners remain unchanged through slow package transfer/initramfs
work. Read-only TCP counters show Jammy receiving29MB then59MB before completing.
One diagnostic stat glob fails before privileged shell expansion; a later root
Python stat records the real partial-file size. A separate Noble observer returns
SSH255 around its successful cooperative shutdown. Neither observation changes
the original preparation verdict or starts a replacement VM.

Two matching native CI0316 ZIPs are downloaded through the GitHub artifact API;
API digest/size, all four package hashes and six actual AArch64 ELF hashes match
the original build receipts. Temporary signed capabilities stay private. These
ELFs are inspected, not executed by this checkpoint. A prototype and then formal
derivation create higher `+armtest1` pairs, proving every data member, permission,
owner/link and maintainer script identical, allowing only Version and GUI exact
base-version Depends changes. The four formal packages have distinct derivation
receipts and generator hashes; they are not native rebuilds or old-code migrations.

The first artifact download prototype exits1 because file_uri is an object rather
than a path. Its original method/log remain; inspecting its schema and using the
private download_url succeeds. An initial shell process cannot start in an absent
workdir and performs no download. All setup failures and observations are retained.
Cold-install adapter generated-source compilation and CLI-help preflights pass;
they are not product test passes. Future cold runs retain original guest/C/GUI
assertions and independently verify original owners, source/tools/packages and an
actual ARM-compiled fsync fixture.

The public archive has121 exact members,565237 raw bytes, gzip113352B, SHA256
`655629e0eaa93f188de2173133ced7630dc125bcbfe5e12c153c6c40a33bf517`.
Every member is read back against hash/size. Two actual SSH private keys and two
download capability strings are checked absent. Images/disks/firmware variables,
ZIP/deb/ELF bytes and private directories are excluded. Existing E60 software,
USB and publication results retain their own source identities. Installed ARM
runtime, USB I/O, hardware G1/G2 and full Phase0–7 remain unaccepted here.

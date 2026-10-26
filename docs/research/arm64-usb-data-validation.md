# ARM64 installed USB data investigation

2026-10-26. **The ARM64 Native/USBFS × IPv4/IPv6 data matrix has not passed.**
After a network adapter correction, both devices enumerate but the first I/O
round fails. A separate original-Linux USB/IP control reproduces an imported
keyboard runtime-suspend failure; per-device power control restores input.
No production power-management fix is included yet.

[Design](arm64-usb-data-design.md) · [Adapter](../../scripts/qemu_arm64/README.md) ·
[Original verdicts](../evidence/arm64-usb-data-20261026/summary.json) ·
[Exact archive members](../evidence/arm64-usb-data-20261026/originals-manifest.json)

## Actual matrix

| Execution | Clean source | Actual result | Actual QEMU exits |
| --- | --- | --- | --- |
| Initial Native/USBFS × IPv4/IPv6, four pairs | `37b5fc4ab9e41ebf3e712263b752b0e6f3e0709b` | All FAIL before import: data MAC mismatch | Eight exits0 |
| Same four cases after MAC correction | `bed49f4d1d46b7cb391407f6612a75fed643bcf7` | All FAIL at first I/O round: empty HID events | Eight exits0 |
| Native/IPv4 metadata-only input trace | same cleanbed49 | FAIL, same first-round symptom | Two exits0 |

IPv4 uses Ubuntu22.04 server/24.04 client; IPv6 reverses them. Actual guests are
aarch64, systemd PID1, kernels5.15.0-198/6.8.0-147. The original installed runner,
guest, concurrent imports, Root correlation/UID/key/resource/proof, complete
driver baseline, live-upgrade sequence and strict PCAP classifier are unchanged.
All original and additional ARM source/tool/firmware/parent/package guards pass.
Successful guest exits and guards never override a failed run.

Initially the guest expects data MAC52:54:00:59:00:21/22, while the ARM branch
supplies61. Only that data branch is corrected; management61, prepared parents
and original acceptance stay unchanged. The four original failures are retained.

Corrected cases complete authenticated Catalog and two actual VHCI imports with
`usbhid`/`ftdi_sio` enumeration. Client guest.py216 fails with `events=[]`.
Its preceding exact35-byte FTDI RX assertion has therefore passed; TX occurs
after the failed HID assertion and has not executed. The host25-second receive
timeout is secondary. **No complete I/O round, live-upgrade data matrix or final
PCAP acceptance passes.** Partial observations are not successful USB cases.

## Controlled local diagnosis

Five separate single-guest diagnostics use fresh COW children of the same
prepared Jammy disk, actual ARM platform/tool inputs and QMP key1/100ms.
They run no RustUSB, TLS or remote data network. All five actual QEMU processes
exit0; HEAD/clean/input guards pass.

| Diagnostic | Actual observation |
| --- | --- |
| Initial trace setup | Unknown trace-event name; fails before testing input |
| Correct trace names, local physical keyboard | Original `send-key` produces exact evdev press/release; later root-owned marker removal fails, second method unexecuted |
| Official `usbipd`/`usbip` localhost import | VHCI keyboard enumerates, both `send-key` and `input-send-event` produce no events |
| Same import with xHCI trace | Interrupt endpoint stopped before injection; kernel URB unlink; QMP events reach QEMU without HID queue consumption |
| Same import, per-device power control | `auto`/`suspended`: no events; only imported3-1 changed to `on`: `active`, exact press/release succeeds |

In the last control, physical1-1 stays `active`/`auto`. Imported3-1 initially has
`auto`, `suspended`, wakeup enabled and2000ms autosuspend delay. Writing `on` only
to this VHCI device resumes it; the same probe and injection then observe
`[1,2,1]` and `[1,2,0]`. No global USB parameter, kernel command line, host USB,
budget or assertion changes. The diagnostic host exit stays1: its original
all-positive predicate also includes the deliberately failing control.

This establishes the runtime-PM failure and manual control for this isolated
original-Linux import. It suggests a mechanism for the matrix symptom; the
failed matrix did not record client PM attributes, so this is not a retrospective
per-case PM trace or a passing production fix. The successful local keyboard
control does not justify adding a GPU. The original-Linux reproduction does not
establish broad ARM incompatibility or a Rust TLS defect.

The fixed [Linux5.15 USB PM documentation](https://www.kernel.org/doc/html/v5.15/driver-api/usb/power-management.html)
defines per-device `on` as resume/prevent autosuspend and explains that suspend
callbacks cancel URBs. System suspend is separate. Fixed QEMUv6.2.0 source
observations cover [routing](https://github.com/qemu/qemu/blob/v6.2.0/ui/input.c),
[QMP injection](https://github.com/qemu/qemu/blob/v6.2.0/ui/input-legacy.c),
[HID](https://github.com/qemu/qemu/blob/v6.2.0/hw/input/hid.c) and
[xHCI](https://github.com/qemu/qemu/blob/v6.2.0/hw/usb/hcd-xhci.c).
Source informs interpretation; actual observations determine verdicts.

## Software, inputs and custody

Both clean37b5 and cleanbed49 pass original `scripts/check.sh` and
`scripts/check-gui.sh`: core1096+GUI18=1114, zero failed/ignored,734 tracked
non-evidence inputs and HEAD/clean/input guards true. Host release CLI866b0751,
helper475b2f97 and GUI7bdb41f7 stay unchanged; this is host software evidence.

Actual ARM packages are original Jammy native CI0316 CLIff77d46b/helpercf3e28ca
on both Ubuntu kernels, plus the independently audited `+armtest1` control-only
revision. Payload, permissions, ownership, links and scripts are identical.
This is not a rebuild, separate Noble ELF USB acceptance or different-code
migration. [Cold installation](arm64-installed-validation.md) remains separate.

The public archive has1782 members/11,499,611 raw bytes; gzip2,383,972 bytes,
SHA256 `c2987ddde8c9a40f7acc9c5b9ac821b55833b193af2e28cbc4efb2cd626956be`.
It retains all verdicts, actual host-owner terminal receipts, generated methods,
source snapshots, software gates, complete boot/product PCAP and diagnostics.
Member hashes/sizes are verified by read-back. All68 actual private-key file
byte sequences and private-key/capability markers are excluded. Private disks,
firmware variables, keys, debs and ELFs are omitted. Eighteen paired and five
diagnostic guest exits are separate from host verdicts. All owners terminate
before source/docs change.

## Required next work

Design a scoped PM policy for the authorized pinned local VHCI generation,
covering legacy tool and anonymous secure import. Review privilege, cancellation
and partial-effect cleanup before implementation. Do not globally disable USB
autosuspend or modify an unrelated/replacement node. Then execute fresh software
gates, actual ARM packages and all four original data cases, preserving failures.
A manually resumed isolated device does not satisfy that acceptance.

ARM virtual CDC data, physical boards/devices G1/G2, full ARM GUI actions/faults,
storage/ISO, performance, Windows and the complete Phase0–7 objective remain
unfinished. TCG timing is not native hardware performance evidence.

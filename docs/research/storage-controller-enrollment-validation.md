# Storage boot policy and USBFS descriptor lifetimes

Ubuntu 22.04 and 24.04 pass the controller-policy and descriptor-lifetime experiments at clean commit `54a6b7a1d411ea063a81ee6468911a87bf057684`. Runtime authorization defaults are lost when the controller is recreated. An explicit boot default denies a storage device present from power-on, but interface defaults still need a separate gate. Registered pre-bind USBFS descriptors become disconnected during Native bind; descriptors opened after binding retain device-level control access in the tested transitions.

These are stock Linux mechanism results. **RustUSB storage export remains disabled.** The [product enrollment design](storage-controller-enrollment-design.md) remains proposed. No enrollment API, durable controller grant, general live-disk handoff or storage release protocol is implemented by this change.

The later [product controller-policy validation](controller-enrollment-validation.md) implements explicit durable enrollment and reconciliation. This E67 report retains its original mechanism-only scope.

## Environment and execution

Tests ran on 2026-10-26 using fresh COW descendants of the product-free [arrival experiment](storage-arrival-quarantine-validation.md) parents. Each policy guest has a dedicated xHCI at PCI `0000:00:06.0` and a new read-only 64 MiB ordinary-file disk. No filesystem is mounted or formatted. Host devices, modules, services, accounts and boot settings are untouched.

| Environment | Ubuntu 22.04 | Ubuntu 24.04 |
| --- | --- | --- |
| Kernel | `5.15.0-198-generic` | `6.8.0-147-generic` |
| Actual udev package | `249.11-0ubuntu3.23` | `255.4-1ubuntu8.18` |
| Policy host runner | `74678`, exit0 | `8002`, exit0 |
| Actual QEMU processes | `2955474`, `2956206`; both exit0 | `2955456`, `2956136`; both exit0 |
| Original normal I/O regression | Jammy → Noble, host `40285`, exit0 | Noble → Jammy, host `15948`, exit0 |

All four host runners pass. Eight QEMU processes exit normally, without forced disposal; guest evidence collection, kernel-integrity checks and source/parent/release-hash guards pass. Policy runs use two separate QEMU processes around a real guest reboot, with changed boot IDs and the same owned COW disk. The second process starts with the synthetic disk already attached. Both complete 64 MiB policy backing files remain zero after the actual QEMU exits.

The complete software gate on the same clean commit records 1,148 core and 18 GUI test executions, no failures or ignored tests, and stable hashes for 766 tracked non-evidence inputs. Formatting, strict Clippy, tests and release builds pass. Its runner-owned records retain the actual child exit codes; the original outer tool terminal response was unavailable after context rollover and is not reconstructed. The three product release hashes remain identical to the preceding storage-inspection/arrival builds.

## Controller policy: positive and negative controls

Both guests first expose the disk with the normal global default `-1`. A real `usb-storage` driver and read-only block child appear, proving the local storage path is available. After QMP removal, the fixture sets both dedicated root hubs' device and interface defaults to zero before adding a fresh device generation.

| Transition | Observed device/interface defaults on both hubs | Target result |
| --- | --- | --- |
| Existing hubs explicitly armed | 0 / 0 | Unauthorized; no local storage |
| Same PCI controller unbound/rebound under normal global default | 1 / 1 | New generation binds local storage |
| Actual reboot with `usbcore.authorized_default=0`; disk present at power-on | 0 / 1 | Unauthorized; no interfaces or block child |
| Controller recreated under boot default0 | 0 / 1 | New generation remains unauthorized |
| New hubs explicitly armed, then exact target authorized | 0 / 0 | Device authorized; interfaces remain unauthorized and locally unbound |

The original and replacement HCD inode identities are disjoint; the PCI object stays the same across each in-boot recreation. USB generation identities also change. Reused addresses therefore cannot preserve the old live receipt. The fixture's `old_receipt_invalid` field records these identity observations; it does not exercise a product receipt API.

The boot parameter is intentionally configured only in disposable guests. It affects all USB controllers and is not a silently installable desktop policy. The [fixed Linux 6.8 HCD implementation](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/hcd.c) explains why a new HCD copies the global device default but initializes interface authorization separately. Fixed Linux 5.15 sources show the equivalent boundary; these upstream originals are not asserted to be byte-identical to Ubuntu's downstream kernels.

## Two descriptor cohorts, four actual owners

Each guest uses two separate probe processes. Each process opens a writable and read-only USBFS descriptor as root, then drops to UID/GID 65534 with no supplementary groups and zero effective capabilities before probing. This is descriptor inheritance across a credential drop, not an SCM_RIGHTS experiment. Receipts capture the actual PID/start time, executable hash, FD numbers/flags, node identity and exact USB generation. Every original process closes its descriptors and is actually waited with exit0.

Each probe issues a real device-recipient GET_DESCRIPTOR control ioctl. Success means exactly 18 bytes with the expected descriptor type and VID/PID; it is not a cached `read()` or a storage request.

| Cohort and transition | Held writable FD | Held read-only FD | Fresh unprivileged writable open |
| --- | --- | --- | --- |
| Pre-bind, node temporarily 0666 | 18 bytes | `EPERM` (1) | Success |
| Same pre-bind FDs, node closed to 0600 | 18 bytes | `EPERM` (1) | `EACCES` (13) |
| Same pre-bind FDs after stock Native bind | `ENODEV` (19) | `EPERM` (1) | `EACCES` (13) |
| New FDs opened while Native-bound | 18 bytes | `EPERM` (1) | `EACCES` (13) |
| Same post-bind FDs after device authorization | 18 bytes | `EPERM` (1) | `EACCES` (13) |
| Same post-bind FDs after device deauthorization | 18 bytes | `EPERM` (1) | `EACCES` (13) |
| Same post-bind FDs after idle stock unbind | 18 bytes | `EPERM` (1) | `EACCES` (13) |

The exact results agree on both kernels. Interfaces stay unauthorized and locally unbound, with no source block child throughout these descriptor transitions. The final idle device is generic-bound and unauthorized. Idle stock unbind is a mechanism probe, not a proposed safe storage release operation.

This corrects the initial broad hypothesis that every old writable FD would survive Native bind. Generic driver disconnect calls the USBFS removal notifier, which invalidates already registered pre-bind state. Post-bind opens create independent state. See the [fixed USBFS implementation](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c) and [generic disconnect](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/generic.c). The original hypothesis and its correction are retained in the research report.

## What the permission experiment does not prove

Scoped guest rules set final root ownership/mode0600 and remove the `uaccess` tag before the stock seat rule. The evidence includes the actual guest package versions, fixture and relevant stock rule text, node mode/owner, POSIX ACL xattrs and real open results. Cold-state snapshots have no granting ACL. The experiment does **not** audit every effective rule or initramfs, exercise an active seat ACL grant, or prove uninterrupted raw-node exclusion from early boot. A final mode observation cannot establish that history.

The source review identifies two additional gaps, neither reproduced as a controlled race here:

- An open that passed VFS permissions before chmod can register its USBFS state after the disconnect notification cleared the old list.
- An already admitted synchronous control request releases the device lock around its private EP0 operation; it is outside the tracked asynchronous list drained by the notifier.

Canonical-node permissions also do not cover accessible character-device aliases, privileged FD delegation or every namespace. A deployment policy must account for trusted boot/rules/ACL history and privileged actors. No sleep, current FD census, chmod result or sequential ENODEV observation proves universal withdrawal of prior capabilities.

## Regression, evidence and next implementation

The common VM helper adds an empty-by-default boot-device extension. Both original normal arrival-transfer pairings are rerun unchanged on the same clean commit: real two-LUN I/O, direct readback, synchronization, daemon death with continued kernel-owned I/O, targeted reset and source quarantine pass. Their four guests exit normally. The earlier flush-error trials are not rerun or relabeled; their failures and emulator limitations remain in the original report.

The [evidence archive](../evidence/storage-policy-20261026/README.md) includes raw guest records, all four run receipts, software logs, methods, fixed research sources with licenses and source hashes. A same-author review checks the original records and Git inputs without importing the test runners. It is not another USB execution or an independent security audit. Research package binaries, disk images, executable payloads and private keys are excluded; source package identities remain recorded.

Product work still requires durable administrator enrollment intent, a fresh helper-epoch/boot/controller receipt, invalidation on unknown transitions, and separate exact-generation storage ownership and release. General locally used disk handoff, every-LUN/application cache drain, fault recovery, UAS, physical power-loss behavior, real hardware and the complete Phase 0–7 requirements remain unfinished.

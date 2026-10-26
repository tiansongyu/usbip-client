# Storage controller enrollment and USBFS capability boundary

Status: proposed product design, with guest-only prerequisites to validate. Baseline: `c6850e7d6e866f1b72fff7a29e370a290895013f`, 2026-10-26. This continues requirement 4.6 and the [arrival experiment](storage-arrival-quarantine-validation.md); it does not enable RustUSB storage sharing or replace the full storage handoff requirement.

## Durable intent is not a live controller grant

A future root-owned enrollment record selects an administrator-reserved controller and its deployment policy. It cannot itself authorize a device. A separate receipt must identify the current boot, PCI object, every associated root HCD, and verified device/interface defaults. Reboot, controller removal or HCD replacement invalidates that receipt and all dependent device grants, even if a bus number or PCI address is reused.

The audited Linux 5.15/6.8 `usb_add_hcd()` initializes device policy from the global `usbcore.authorized_default` parameter **before** root-hub registration, but initializes interface-default authorization to 1. Runtime writes to `usbX/authorized_default` and `interface_authorized_default` therefore do not survive HCD recreation. A later udev/systemd response cannot prove that no device was probed first. No audited per-controller boot authorization parameter provides the equivalent early boundary.

For a dedicated export deployment, explicitly administered `usbcore.authorized_default=0` at boot is a candidate prerequisite. It affects USB broadly and must never be silently installed on an ordinary host. A future helper may validate its presence and current value, but must not edit boot configuration or turn it off. Every newly observed HCD still requires interface-default denial before any target authorization. Previously authorized devices or a missing boot prerequisite must produce an unresolved state, not forced deauthorization or a fabricated history of isolation. The normal desktop/live-disk handoff remains separate work.

Proposed state layers:

| Layer | Meaning | Invalidated by |
| --- | --- | --- |
| Enrollment intent | Trusted administrator selection and deployment policy | Explicit administrator retirement or policy change |
| Armed controller receipt | Current boot + PCI/HCD identities + both defaults zero | Boot/HCD replacement, identity/default mismatch, unknown observation |
| Target arrival receipt | Exact new device generation beneath that armed controller | Replacement/removal, policy loss or any unverified transition |
| Storage lease/release | Actual authorized data owner and checked all-LUN/application release | Requires a separate protocol and recovery implementation |

These are contracts to implement, not new public Rust APIs or journal versions in this change. Current Native/USBFS admission, resource proofs and wire formats stay unchanged.

## USBFS permission and lifetime claims must be separate

Linux initially creates USB device nodes through devtmpfs; udev rules, ACLs, delegated descriptors and privileged callers can change access. Checking current mode or an empty process scan cannot prove no writable descriptor existed earlier. The candidate deployment must close ordinary-user writable opens from device creation, including udev/ACL paths, and treat other privileged administrators as trusted policy owners rather than adversaries it can defeat.

An important correction to the earlier broad descriptor warning: [generic USB driver disconnect](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/generic.c#L269) calls `usb_notify_remove_device()`. The [USBFS notifier](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c#L2858) destroys pending asynchronous work and removes registered descriptor state from the device file list. Stock Native bind passes through that generic unbind. A pre-bind descriptor may therefore become `ENODEV`; this is not revocation by chmod and must be tested separately from a descriptor newly opened while the stub is already bound.

There is also a source-level race: a previously permitted VFS open can register its USBFS state after the notifier has cleared the old list. One successful invalidation test cannot prove all in-flight opens were covered. No claim of general revocation follows from chmod followed by bind. Read-only descriptors fail the ioctl write-mode gate with `EPERM`; a still-connected writable descriptor can issue device-recipient control independently of interface claims. Safe GET_DESCRIPTOR is sufficient to demonstrate that capability without changing media or device configuration.

## Planned isolated verification

Use fresh COW descendants of the product-free E66 Ubuntu parents, a dedicated emulated xHCI at a fixed PCI slot, and a new **read-only** 64 MiB synthetic storage file. No filesystem is mounted or formatted. Host devices, modules, boot configuration, services and accounts remain untouched.

Each Ubuntu run will record:

1. A default-authorized positive control that actually binds local storage, followed by physical QMP removal. This verifies that an absent block child in later cases reflects a gate rather than a missing driver.
2. Pre-arrival runtime device/interface defaults zero. A first root-opened writable/read-only descriptor pair is passed to an actual non-root process with no supplementary groups/capabilities. Capture the same PID/FD/generation before and after chmod and stock Native bind; require the precise predicted errors rather than accepting any failure.
3. A second descriptor pair opened after Native binding. Capture fresh non-root open denial, connected writable GET_DESCRIPTOR, read-only ioctl refusal, and device deauthorization separately. Always close and wait the actual probe process before proceeding. No inference about unrelated or still-opening descriptors.
4. Runtime defaults followed by xHCI unbind/rebind. With the original global default, expect new HCD defaults to reset and a fresh local storage generation to appear. Preserve old/new identities; do not reuse an old receipt.
5. Guest-only GRUB configuration with the explicit boot default zero, then an actual guest reboot and original QEMU wait. Boot a second QEMU process from the same COW disk with storage physically present from power-on. Verify a different boot ID, real kernel command line, current parameter, and unauthorized target before any fixture authorization.
6. Under that boot prerequisite, recreate the HCD with storage still connected. New device default must be zero while interface default starts at one. Only after setting both new interface defaults to zero may the exact target be explicitly authorized; no local interface driver/block child may result.

Guest udev rule experiments may establish a final root-only node mode and removal of the access tag before the seat ACL rule. Their actual versions, rules, node ACLs and fresh-open results must be captured; they do not prove every deployment's rule set or historical absence of delegation. Existing writable descriptors are deliberately delegated by the test and are never presented as safe product behavior.

All methods are test-only. Source/HEAD/parent/release-file identities stay fixed while owners run. The original process must be waited; any hang, failed assertion or forced disposal remains a failure. Product implementation follows evidence, with durable records, unknown outcomes, actual blocking-owner lifetime, exact-generation checks and explicit release semantics still required.

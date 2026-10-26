# Storage arrival isolation: Native mechanism experiment

Status: proposed product design; isolated mechanism experiments completed with the scope and failures in the [validation report](storage-arrival-quarantine-validation.md). Baseline: `0865f65848d3cac986e5366ff55b19d3d1c0e352`, 2026-10-26. This continues requirement 4.6 and real device-level storage forwarding. It does not remove any RustUSB storage admission blocker or claim complete storage safety.

## Why the next boundary is before arrival

The [inspection implementation](storage-inspection-design.md) reports observed usage but cannot fence concurrent writers, hidden kernel references or later opens. A general live handoff from a locally used disk therefore remains unresolved. This experiment tests a different prerequisite for the eventual product: a dedicated, explicitly administered controller whose new devices have never been offered to local storage drivers in this boot. Existing mounted disks are outside this admission path; they cannot be relabelled as fresh arrivals.

Linux v5.15 `8bb7eca972ad531c9b149c0a51ab43a417385813` and v6.8 `e8f897f4afef0031fe618a8e94127a0934896aba` expose per-bus device and interface authorization defaults. The [authorization documentation](https://www.kernel.org/doc/html/v6.8/usb/authorization.html), [interface probe/claim implementation](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/driver.c) and [configuration implementation](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/message.c) support testing both defaults at zero before attachment. New interfaces inherit the HCD setting; ordinary interface drivers and USBFS interface claims reject unauthorized interfaces. This is a candidate Linux ownership boundary, not a complete raw-USB firewall or a hardware authentication mechanism.

The Native [stub device driver](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/usbip/stub_dev.c) owns the hub port at the device level. It is distinct from USBFS, whose interface claim is denied while interfaces remain unauthorized. Device authorization must eventually allow configuration; exact interaction with stock tools and the two Ubuntu kernels must be measured. We will not introduce kernel patches or copy GPL implementation into product Rust code.

## Candidate sequence and invariants

1. In a disposable guest, add an empty dedicated QEMU xHCI controller. Identify its new root hubs by actual before/after topology; verify they share one new PCI parent. Set and read back both authorization defaults as zero before adding any target USB device. Preserve all other controllers' settings.
2. Attach a synthetic BOT device with two fresh 64 MiB LUNs, one QEMU read-only and one expendable writable regular file. Require the exact synthetic descriptor identity, controller ancestry, device generation and unauthorized state. No source SCSI/block children or interface driver may exist.
3. Bind only that generation to `usbip-host`, then authorize only that pinned device. Both controller defaults remain zero. Any interfaces created by local or remote configuration must remain unauthorized and locally unbound. A replacement generation is never automatically authorized.
4. Expose stock `usbipd` only to the second authorized guest over the private socket-backed test network. Use stock `usbip attach` to exercise actual VHCI enumeration and native storage I/O. Every block write first checks exact synthetic USB ancestry, whole-disk capacity, LUN identity and writable backing; no host block device is opened.
5. Check explicit client cache-command results per LUN separately from readback, detach, transport closure and source isolation. A successful software flush cannot prove a physical device's power-loss durability. QEMU backing uses flush-enabled cache settings; do not use `-snapshot` or unsafe cache mode.
6. Keep the source stub bound after normal detach and faults. Close the listener to prevent new imports; do not run stock `usbip unbind`, unload the module or restore a local storage driver. Teardown powers off the expendable guests. Host backing files are inspected only after the original QEMU process has exited.

The controller setting is part of the invariant, not a one-time setup hint. HCD destruction/recreation or a reboot can reset it and requires a new pre-arrival policy. No future product may accept an empty proc scan as proof this policy existed earlier. Other privileged administrators, arbitrary kernel modules, already delegated writable USBFS capabilities, and a malicious USB device need separate threat-model treatment; descriptor/serial matching is only a test identity, not authentication.

## Experiments and failure evidence

Run with full product-free Ubuntu 22.04/24.04 parents, first one server/client direction and then the reverse. Pin HEAD, all tracked non-evidence fixture inputs and parent image hashes for each actual pair. Product source and release executables remain unchanged; this is an upstream mechanism experiment, not a RustUSB storage feature or a new product test count.

A separate tool-only preparation may add GCC and sg3-utils to new COW descendants of those parents. It must retain the original preparation receipt, source/parent hashes and actual guest exits, and produce a new derived-image hash before any mechanism run consumes it. It installs no RustUSB product or storage fixture. Final software checks, image preparation and actual mechanism results have separate records.

Required observations include:

- Initial unauthorized arrival, explicit generation-pinned bind/authorization, both LUNs on the client, successful writable-LUN data transfer and read-only-LUN write refusal.
- Source interface authorization zero, no source local storage driver/block device at each phase, and an explicit denied USBFS interface-claim probe where an interface exists.
- Normal client flush/detach followed by source isolation, with command status, exact original VHCI disappearance and actual child exits recorded separately.
- Actual `usbipd` SIGKILL/wait and continued existing-connection I/O if the kernel retains that connection; listener death must not be labelled USB release.
- An explicitly targeted TCP reset on the isolated data link, client disappearance and source isolation without automatic writable recovery. This is not a black-hole, packet-loss or physical hot-unplug test.
- Where supported by fixed QEMU source, explicit flush-failure injection and checked command failure. Never relabel an unsupported or unexecuted fault as covered.

Keep all failed trials, raw outputs, kernel logs, fixture versions, source guards and actual process exits. Host FAIL remains FAIL even if guests exit zero. Private keys, COW/backing disks, packages and executables are excluded from public evidence. The host's devices, mounts, modules, services and accounts are untouched.

The initial fixture at `d99201ca` reaches unauthorized arrival, stub binding and device-only authorization on Jammy, then fails before IMPORT: its ordinary-user readiness check cannot traverse the root-owned 0700 evidence directory. The actual daemon was ready and was subsequently killed and waited; both guests exit zero, but the host remains FAIL. The corrected readiness observer runs as root without changing directory permissions. Final fixtures also pin HCD/PCI inodes, check claim identity again after close, use fresh aligned `O_DIRECT` reads for both LUNs, and record actual QEMU identities.

The separate cache-error trial uses fixed QEMU 6.2 `raw -> blkdebug -> file`, a one-shot `flush_to_disk` EIO with `iotype=flush`, no sector restriction, `no-flush=false` and SCSI write-cache/report policy. The first explicit synchronizer follows a direct write without fsync. Acceptance requires the actual `sg_sync` categorized exit11, matching command 0x35 and sense 0xb/00/06 trace, and one QMP `BLOCK_IO_ERROR` with write/report action on the saved LUN graph. A second explicit write/sync is a once-consumption control, not automatic retry or a replacement verdict for the failed flush. See [QEMU block I/O](https://github.com/qemu/qemu/blob/44f28df24767cf9dca1ddc9b23157737c4cbb645/block/io.c), [blkdebug](https://github.com/qemu/qemu/blob/44f28df24767cf9dca1ddc9b23157737c4cbb645/block/blkdebug.c), and [sg_sync 1.46](https://github.com/doug-gilbert/sg3_utils/blob/b81d7816ca45f02bb3d50e4adde0222e8b66c2eb/src/sg_sync.c). Actual Ubuntu tool versions and bytes are recorded separately from these upstream references.

## Product work still required

### QEMU 6.2 nonzero-LUN sense limitation and failed teardown

The two initial cache-error trials at clean `3b9c5b2b` fail: the LUN1 SYNCHRONIZE CACHE trace contains the expected 0xb/00/06 sense and QMP reports the injected EIO, but the client receives 0x05/25/00 and `sg_sync` exits 5. QEMU's [fixed SCSI target-command path](https://github.com/qemu/qemu/blob/44f28df24767cf9dca1ddc9b23157737c4cbb645/hw/scsi/scsi-bus.c#L594) selects target emulation when a REQUEST SENSE has pending sense, then substitutes LUN_NOT_SUPPORTED whenever `req->lun != 0`. This explains the observed result; exit5 is not an acceptable substitute for the planned aborted-command check.

The next independent fault trial will put the writable fault target at LUN0 and the read-only control at LUN1. The normal scenario keeps its original layout. This explicitly narrows the fault test to LUN0 on the unmodified emulator; it does not repair or verify nonzero-LUN error fidelity. Both actual mappings must be recorded and checked. Cache-error acceptance remains exit11 plus the matching LUN0 trace and QMP event, followed by a separately recorded explicit control.

The failed runner also powered off the source before detaching the imported client. Both clients subsequently reported blocked LVM/kernel workers during shutdown. No kernel stack was captured, so the detailed blocking cause is unresolved. After repeated blocked-task reports and shutdown waits, each owned client received an externally recorded QMP `quit`; both original runners then actually returned exit1. Their QEMU exit0 values are forced process exits, not clean guest shutdown or release evidence. Original results remain unchanged. The next fixture must attempt explicit detach of a known held client before source teardown and reject blocked-task serial reports; a failed case must stay failed regardless of cleanup outcome.

A successful mechanism would justify designing a root-owned controller enrollment and arrival receipt, exact-generation authorization, durable quarantine and explicit recovery workflow. It would not finish live local-storage handoff, checked client release acknowledgement, all-LUN completeness, encrypted-volume policy, composite/UAS support, USBFS storage, reboot/power-loss recovery or hardware acceptance. The original Phase 0–7 scope remains active.

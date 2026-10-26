# Native storage arrival isolation: QEMU validation

Two Ubuntu guest pairings pass the final normal and cache-error mechanism tests. An empty dedicated controller is fenced before a synthetic storage device arrives; the client performs actual two-LUN I/O while the source keeps its interfaces unauthorized and locally unbound. These tests use stock Linux USB/IP, not a RustUSB product executable. **RustUSB storage export remains disabled.**

The [design](storage-arrival-quarantine-design.md) is still proposed for product integration. The [evidence archive](../evidence/storage-arrival-20261026/README.md) preserves successful runs, three failed trials, research originals, process-exit observations and software checks. Tests ran on 2026-10-26.

## Tested environment and source

| Item | Recorded value |
| --- | --- |
| Final fixture | `05f26ffbd11b9a1f959e523cac367cb4bdcc5343` |
| Initial corrected fixture and software gate | `3b9c5b2b93df7c058d0295e8c331709bff18d8c8` |
| First readiness failure | `d99201ca0625d0435b898814db6afaf4e4fa601f` |
| Guests | Full Ubuntu 22.04 / 24.04, x86_64, roles reversed in each scenario |
| Guest kernels | `5.15.0-198-generic`, `6.8.0-147-generic` |
| Emulated device | Dedicated xHCI, BOT, two fresh 64 MiB raw-file LUNs |
| Network | Private socket-backed guest link, stock unencrypted USB/IP |
| Normal LUN layout | LUN0 read-only, LUN1 writable |
| Cache-error LUN layout | LUN0 writable with one-shot flush EIO, LUN1 read-only |

Each run records the actual QEMU executable hash, guest kernel/tool versions and hashes, original daemon exit, all tracked non-evidence inputs, parent hashes and three unchanged product release hashes. The product-free parent preparation adds only GCC and sg3-utils to new COW guests and retains its original signed-image-derived receipts. Its two QEMU processes exit normally. No host USB device, block device, mount, module, service or account is changed.

The complete software gate at clean `3b9c5b2b` passes **1,148 core plus 18 GUI test executions**, with no failed or ignored tests. It includes the repository formatting, strict Clippy, workspace tests and release builds. Subsequent changes affect only fixtures and documentation; they do not create a new product build or transfer these results to a different product binary.

## Final matrix

All four final host runners exit0. All eight guests power off normally; collection and source/parent/binary guards pass.

| Source → client | Normal | LUN0 flush error |
| --- | --- | --- |
| Ubuntu 22.04 → 24.04 | PASS, host `66382` | PASS, host `27795` |
| Ubuntu 24.04 → 22.04 | PASS, host `30188` | PASS, host `41472` |

Two earlier normal runs at `3b9c5b2b` also pass. They are retained as separate executions, not added to the final matrix or described as new product tests.

### Arrival and source ownership

Before target attachment, both new root hubs have `authorized_default=0` and `interface_authorized_default=0`. The fixture saves the exact HCD/PCI identity and preserves other controllers' settings. The fresh device arrives unauthorized, without source block descendants. Binding selects its pinned generation; only that device is then authorized.

Client configuration creates source interfaces that remain unauthorized and have no local interface driver. A root USBFS interface-claim probe fails with `ENODEV` (19); generation, configuration and interfaces match before and after the probe. This is an interface-claim boundary. It is not proof that all device-level USBFS operations or existing writable descriptors are fenced.

### Normal I/O, daemon death and connection reset

Each final normal pair completes three 4 KiB writes at distinct offsets. Every cycle checks a fresh aligned `O_DIRECT` read, zero-filled read-only data, read-only write refusal (`EROFS`, 30), and explicit synchronization on both exact LUNs. QEMU acceptance of synchronization on its read-only LUN does not prove a physical cache operation.

The first cycle detaches and verifies original VHCI disappearance before a new import. After the second cycle, the original foreground `usbipd` is killed and actually waited with exit -9. The listener is absent, but the same kernel TCP tuple/inode and client generation still complete the third I/O cycle. Daemon death therefore does not release that existing data connection.

The fixture then resets only the actual private TCP tuple. The original client VHCI becomes NULL and its device disappears. The source remains bound to `usbip-host`, with denied interface authorization and no local storage driver or block children. It is not unbound or automatically restored to local writable use. Offline reads after actual QEMU exit verify the backing bytes.

### Explicit cache-error propagation

The fault run uses QEMU `raw → blkdebug → file`, flush-enabled caching, a single `flush_to_disk` EIO and report policy. It first performs an `O_DIRECT` write without fsync or O_SYNC. The first explicit SYNCHRONIZE CACHE(10) returns `sg_sync` exit11, matching the QEMU command 0x35, sense 0xb/00/06 and exactly one QMP write/report error on `arrival-lun0`.

A separately recorded second write and synchronization returns0, demonstrating consumption of the one-shot injection. It does not erase the first failed flush or approve clean storage recovery. The listener is closed, the client is explicitly detached, and the source remains quarantined. Physical power-loss durability is untested.

## Failures retained

| Trial | Observation | Result and follow-up |
| --- | --- | --- |
| Initial Jammy readiness, `d99201ca` | Ordinary-user readiness check could not traverse the root-owned evidence directory; daemon was actually listening | FAIL before import. Root read-only observer replaces the faulty check; directory permissions stay unchanged. |
| Jammy LUN1 cache error, `3b9c5b2b` | QEMU produces expected EIO/sense, but client receives illegal-request/LUN-not-supported, exit5 | FAIL. Later LUN0 trial has a different explicitly documented layout. |
| Noble LUN1 cache error, `3b9c5b2b` | Same mismatch | FAIL; not reclassified after later success. |

The fixed [QEMU 6.2 SCSI implementation](https://github.com/qemu/qemu/blob/44f28df24767cf9dca1ddc9b23157737c4cbb645/hw/scsi/scsi-bus.c#L594) substitutes LUN_NOT_SUPPORTED in its pending REQUEST SENSE path for a nonzero LUN. This explains why the initial injected sense differs from the client result. Testing LUN0 avoids that emulator limitation; it does not fix or validate nonzero-LUN error fidelity.

In both failed cache trials, teardown powered off the source before detaching the client. Each client later reported blocked LVM and kernel workers during shutdown. No kernel stack was captured, so the internal blocking cause is unresolved. After preserving repeated blocked-task reports and process identities, the two owned clients were disposed of with separately recorded QMP `quit`. Original runners actually returned exit1. Their QEMU exit0 values are **forced process exits, not clean guest shutdown**. The original narrow kernel-integrity field also missed blocked-task reports; its true value is not evidence against the observed hang. The final fixture rejects those reports and attempts detach of a known held client before source teardown.

## Evidence review

A later same-author, read-only audit checks all nine original trials against their Git inputs (759 files at the first fixture, 760 at each later fixture), 62 Linux originals and 142 USB research receipts. For every successful trial it also compares the complete 64 MiB writable backing against zeros plus the recorded write patterns, and the entire read-only backing against zeros. This is an offline file-byte check, not another USB run or an independent security audit. The first two audit attempts fail on record-format assumptions and are preserved alongside the corrected method.

## Limits and next product work

Runtime authorization defaults can reset when the HCD is recreated or the machine reboots. Existing writable USBFS descriptors and privileged external changes are additional boundaries; this matrix does not test their revocation or establish protection against them. The experiment uses a direct root port, no external hub, no filesystem mount, and disposable synthetic data.

Product integration still needs controller enrollment and durable pre-arrival policy, exact-generation authorization, administrator recovery, and a checked release protocol covering client applications and every LUN. General handoff of an already locally used disk, UAS, USBFS storage, encrypted-volume policy, crash/reboot and physical power-loss behavior remain unresolved. Secure RustUSB storage data transport, real devices, performance and the complete Phase 0–7 goals are not established by this mechanism experiment.

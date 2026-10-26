# Pre-arrival Native storage mechanism

This guest-only experiment evaluates the [proposed storage boundary](../../docs/research/storage-arrival-quarantine-design.md). It uses stock Linux USB/IP tools and never relaxes RustUSB storage admission. No product executable is run.

It needs the existing product-free full Ubuntu parents, QEMU 6.2/KVM, `qemu-img`, `genisoimage`, SSH and a clean checkout. The runner adds an empty dedicated source xHCI controller, sets both authorization defaults to zero before attaching a synthetic two-LUN BOT device, and pins its generation before Native binding. All writes target a verified disposable guest LUN backed by a new private regular file. The normal scenario uses RO LUN0/RW LUN1; the cache-error scenario uses RW LUN0/RO LUN1 because QEMU 6.2 does not preserve pending sense on nonzero LUNs. Both layouts are verified from actual enumeration.

`prepare.py --prepared <original-parent-directory> --output <fresh-directory>` can create tool-only parents with GCC and sg3-utils already installed. It uses fresh COW guests, retains the original signed-image-derived preparation receipt, verifies source/parent hashes, waits for actual QEMU exits, and saves new disk hashes. No RustUSB product or USB fixture is installed in those parents. Pass that new directory to `run.py` to avoid repeated package downloads.

```bash
python3 scripts/qemu_storage_arrival/run.py \
  --prepared /tmp/rustusb-installed-usb-os-preparation-20261025 \
  --server-release jammy \
  --output /tmp/rustusb-storage-arrival-jammy-20261026
```

Use `--server-release noble` and a fresh output directory for the reverse pairing. Do not edit, commit or rebuild while either original guest or its host runner remains active. The runner records source/parent/release-file guards, commands, original child exit, private-link traffic, source isolation, actual client I/O and backing-file readback only after QEMU exits.

The normal scenario uses fresh `O_DIRECT` reads with aligned buffers, and explicit `sg_sync` on each LUN (the read-only LUN through its exact SG node). QEMU's read-only flush acceptance does not establish a physical cache operation. The fault scenario kills and waits for the actual foreground `usbipd`, verifies the same kernel socket/client generation still transfers data with no listener, then resets only that actual private TCP tuple. Source defaults remain denied and the stub remains bound. There is no automatic writable reconnect or local-driver recovery.

Use `--scenario flush-error` with a new output directory for an independent fresh-media cache-error trial. It places `raw -> blkdebug -> file` under only RW LUN0, with one `flush_to_disk` EIO and report policy. The first explicit synchronizer follows an actual `O_DIRECT` write without fsync. The case requires `sg_sync` exit11, the matching SYNCHRONIZE CACHE(10) trace/sense 0xb/00/06 and one QMP write/report error. A second explicit write/sync control must succeed; it does not erase the first failure or approve clean storage recovery. Both scenarios retain offline backing-byte checks after the original QEMU exits. The original LUN1 fault trials remain failures; moving the independent fault target does not validate nonzero-LUN sense fidelity. On a failed case, a known held client is explicitly detached before source poweroff. Blocked-task serial reports fail the run. Any external forced disposal must be recorded separately; a QEMU process exit0 from `quit` is not clean guest shutdown.

Do not publish `.private`, disks, SSH keys or executable payloads. Failed host assertions remain failures even when both guests exit zero. The host's USB devices, storage, mounts, modules, services and accounts are never modified.

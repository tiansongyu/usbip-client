# Pre-arrival Native storage mechanism

This guest-only experiment evaluates the [proposed storage boundary](../../docs/research/storage-arrival-quarantine-design.md). It uses stock Linux USB/IP tools and never relaxes RustUSB storage admission. No product executable is run.

It needs the existing product-free full Ubuntu parents, QEMU 6.2/KVM, `qemu-img`, `genisoimage`, SSH and a clean checkout. The runner adds an empty dedicated source xHCI controller, sets both authorization defaults to zero before attaching a synthetic two-LUN BOT device, and pins its generation before Native binding. All writes target a verified disposable guest LUN backed by a new private regular file. The first LUN is QEMU read-only.

```bash
python3 scripts/qemu_storage_arrival/run.py \
  --prepared /tmp/rustusb-installed-usb-os-preparation-20261025 \
  --server-release jammy \
  --output /tmp/rustusb-storage-arrival-jammy-20261026
```

Use `--server-release noble` and a fresh output directory for the reverse pairing. Do not edit, commit or rebuild while either original guest or its host runner remains active. The runner records source/parent/release-file guards, commands, original child exit, private-link traffic, source isolation, actual client I/O and backing-file readback only after QEMU exits.

The normal scenario uses explicit `sg_sync` on each LUN (the read-only LUN through its exact SG node). QEMU's read-only flush acceptance does not establish a physical cache operation. The fault scenario kills and waits for the actual foreground `usbipd`, verifies the existing kernel connection still transfers data, then resets only that actual private TCP tuple. Source defaults remain denied and the stub remains bound. There is no automatic writable reconnect or local-driver recovery. Cache-error injection is a separate planned trial; these cases do not cover it.

Do not publish `.private`, disks, SSH keys or executable payloads. Failed host assertions remain failures even when both guests exit zero. The host's USB devices, storage, mounts, modules, services and accounts are never modified.

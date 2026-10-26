# Storage inspection in disposable Ubuntu guests

This fixture tests bounded root-only **observations**, with storage export still denied. It is not a USB storage forwarding or cache-persistence test. The design and limits are in [storage-inspection-design.md](../../docs/research/storage-inspection-design.md).

Use the existing product-free `qemu_installed_usb/prepare.py` images, QEMU 6.2, KVM, `qemu-img`, `genisoimage`, SSH and a clean checkout. Build the current all-feature release binaries with `scripts/check.sh` first. The runner freezes HEAD, all tracked non-evidence inputs, both executed host ELFs and the prepared parent image. Do not edit or rebuild while it owns a guest.

```bash
python3 scripts/qemu_storage/run.py \
  --prepared /tmp/rustusb-installed-usb-os-preparation-20261025 \
  --release jammy \
  --output /tmp/rustusb-storage-jammy-20261026
```

Run Noble separately with `--release noble` and a new output directory. Each actual QEMU process must finish; a timeout or guest exit does not override a failed host verdict. Private COW disks, backing files and SSH keys remain in `.private` and must not be published.

The runner adds a synthetic BOT device with a 64 MiB read-only first LUN and disposable writable second LUN. All partitioning, formatting, mounts, swap and dm-crypt operations occur **inside the guest** after checking the exact USB ancestry and capacity. It never uses a host block device, loop mount, tap, module or service. `hold.c` creates actual raw FD, bare-block mmap with its FD closed, and a thread with an unshared FD table; it is a guest-only fixture, not product code.

Assertions cover LUNs/partitions, kernel RO metadata, denial of an ordinary authorized UID, continued storage-export denial, raw/file handles and mappings, a separate mount namespace, swap, a crypt holder, unchanged USB driver/configuration/generation and actual child/helper exit. The plain dm-crypt mapping uses a disposable random key passed through stdin and never retained in evidence. An encryption hint does not identify closed encrypted filesystems. Reports may contain explicit coverage issues; no empty report grants export permission.

# Installed service USB validation

This opt-in test runs in disposable full Ubuntu amd64 QEMU guests using actual
base Debian packages, unchanged systemd units and dynamically allocated broker
UIDs. See the [design](../../docs/research/installed-usb-data-design.md). It does
not access host USB or install a host service.

Prepare verified Jammy/Noble image directories with signed-checksum receipts:

```sh
python3 scripts/qemu_installed_usb/prepare.py --images /private/images --output /private/prepared
python3 scripts/qemu_installed_usb/run.py --prepared /private/prepared --output /private/native-v4 \
  --base /private/rustusb_OLD_amd64.deb --upgrade /private/rustusb_NEW_amd64.deb \
  --backend native --family 4
```

Run `native`/`usbfs` and family `4`/`6` separately. IPv4 uses Jammy as server;
IPv6 uses Noble as server. OS preparation installs matching tools/modules and
actually powers off both original guests. Keep HEAD, worktree and host ELFs
fixed through all live processes. Output includes private disks and SSH/TLS
keys: never publish it wholesale. Explicitly select and inspect public evidence.

Command records preserve actual SSH exits. `summary.json` retains failures,
actual QEMU exits and source/ELF guards. Never restart a live test on observation
timeout. Operator1000 uses real application IPC; operator1002 must be rejected.
Three rounds use only synthetic QEMU keyboard/FTDI fixtures. Upgrade client and
server with live imports/exports respectively. Match Root kind, correlation,
resource, key and broker UID; zero new counters cannot prove an old release.

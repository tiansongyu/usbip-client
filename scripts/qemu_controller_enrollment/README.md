# Controller enrollment QEMU acceptance

This fixture executes the real `rustusb` CLI and `rustusb-helper` controller API in disposable full Ubuntu guests. It requires the prepared Jammy/Noble parents from `qemu_storage_arrival`, the current release CLI/helper, KVM and the existing host QEMU tooling. It never opens host USB devices or changes host policy.

```sh
python3 scripts/qemu_controller_enrollment/run.py \
  --prepared /tmp/rustusb-storage-arrival-preparation-20261026 \
  --server-release jammy \
  --output /tmp/rustusb-controller-enrollment-jammy-20261026
```

Repeat with `--server-release noble` and a new output directory. Sources must be committed and clean. Each pair uses three server boots and one client boot, preserving the same private server overlay across two actual reboots. Boot options, udev rules, synthetic read-only media and PCI controller mutations are confined to that overlay/guest.

The sequence checks missing boot provenance, wrong selector/revision/UID, exact retained replay, nonempty retirement refusal, helper restart with historical-only state, explicit reconciliation after HCD replacement and real reboot, then empty retirement without restoring allow defaults. Original helper/stock daemon workers retain their real children and record actual waits. The host records all QEMU exits, kernel-integrity checks, source/ELF/parent hashes and the unchanged backing image.

The separate final data check uses stock `usbip`/`usbipd` under defaults established by RustUSB: one read-only synthetic storage device enumerates in the other guest, a 1 MiB direct read matches zeros and SYNCHRONIZE CACHE completes. The fixture's explicit bind/device-authorization steps are upstream mechanism checks, **not RustUSB storage admission, secure transport or safe media release**. Hot-removing the disposable source before policy retirement does not prove a general storage cleanup path.

Results are valid only when the host `result.json` says `passed: true`, all four original QEMU processes exit zero, collection succeeds and the exact input guards pass. A child/QEMU exit alone cannot override a failed assertion. Private keys, overlays and ELF uploads remain under the output's `.private` or upload directories and are not publication artifacts.

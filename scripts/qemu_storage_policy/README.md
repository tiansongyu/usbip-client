# Storage controller and USBFS policy experiment

Read the [design](../../docs/research/storage-controller-enrollment-design.md). This fixture uses stock Linux USB/IP and a new read-only synthetic disk. It does not execute a RustUSB binary or grant product storage access.

```sh
python3 scripts/qemu_storage_policy/run.py \
  --prepared /tmp/rustusb-storage-arrival-preparation-20261026 \
  --release jammy \
  --output /tmp/rustusb-storage-policy-jammy-20261026
```

Use `noble` and a different output directory for Ubuntu 24.04. Prerequisites are the verified E66 tool-only parents, QEMU/KVM, SSH, genisoimage, GCC in the guests and a clean checkout. No host boot setting, USB device, block device, module, service or account is changed.

The guest tests real local-driver binding as a positive control, two actual non-root descriptor cohorts around Native binding, HCD recreation and a real reboot with `usbcore.authorized_default=0`. The second QEMU starts with storage physically present. The fixture preserves ordinary-user fresh-open results separately from held writable/read-only descriptors, and distinguishes old/new boot and HCD identities. Its test-only udev rules apply to the dedicated emulated PCI controller; they are not a general installer or an audit of arbitrary host rules.

Every descriptor owner closes and is actually waited. The original QEMU exits before the same private COW disk is used for the second boot. `Vm.boot_device_args()` is empty for existing fixtures; this subclass uses it only for cold-attached storage. Freeze source, HEAD, parents and product release bytes until both original processes and the host runner have finished. Preserve all failures and any forced intervention; process exit0 alone does not prove clean guest shutdown. Do not publish private directories, keys, images or executable payloads.

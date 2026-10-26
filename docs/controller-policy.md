# Manage controller denial policy

RustUSB can persist an administrator's intent to keep a dedicated PCI xHCI controller's device and interface defaults at zero. The Linux root helper verifies the current boot, exact controller selection and both root hubs before writing fixed denial values. It records the request before changing sysfs and records the result afterwards.

**This does not enable storage exports.** Controller enrollment does not authorize a USB target, revoke inherited USBFS descriptors, flush media or establish safe storage ownership. The existing export safety checks still apply.

## Prerequisites

Use an isolated, administrator-selected PCI xHCI controller with both logical root hubs. The helper requires a real boot with `usbcore.authorized_default=0`, the current global default at zero and device-default zero on both hubs. Merely changing a runtime default cannot establish the boot prerequisite. Boot configuration remains the administrator's responsibility; this kernel parameter affects other USB controllers too.

Existing authorized devices, downstream hubs, unexpected local interfaces, changed identities or unresolved owners prevent enrollment. Other controllers' known records are excluded precisely. A physical journal whose original device generation is gone cannot establish its old controller scope; reconcile that original record explicitly first.

Controller management is opt-in. Append the following option to the administrator-managed helper command, preserving its existing policy and physical/Native journal paths:

```text
--controller-state-dir /var/lib/rustusb/controllers
```

The helper creates its root-only state directory. Do not pre-create an empty directory or delete its files to reset history. An existing directory without its expected lock/history is rejected. A build without `physical-usb` refuses controller-enabled startup when the configured physical-state path already exists, because it cannot inspect those grants. The supplied systemd unit does not enable this option automatically.

Only root may use these controller commands, including read-only queries. Adding an ordinary user to the helper policy does not grant controller administration. The normal application remains unprivileged.

## Inspect and enroll

The examples use `jq` to extract the exact selector and boot UUID. Replace the example PCI address with the dedicated controller selected by the administrator. These commands use the default helper socket; pass the same explicit `--socket` to each command if your helper uses another path.

```sh
PCI=0000:00:06.0
INSPECTION=$(sudo rustusb controller inspect "$PCI")
printf '%s\n' "$INSPECTION" | jq .

BOOT_ID=$(printf '%s\n' "$INSPECTION" | jq -r .boot_id)
SELECTOR=$(printf '%s\n' "$INSPECTION" | jq -c .selector)
REVISION=$(sudo rustusb controller list | jq -r .historical.revision)
POLICY_ID=$(cat /proc/sys/kernel/random/uuid)
REQUEST_ID=$(cat /proc/sys/kernel/random/uuid)

sudo rustusb controller enroll \
  --policy-id "$POLICY_ID" \
  --request-id "$REQUEST_ID" \
  --expected-revision "$REVISION" \
  --expected-boot "$BOOT_ID" \
  --selector "$SELECTOR"
```

Retain the exact request ID and all arguments before submitting. A successful result has `record.outcome.state: "armed"`, and its verification identifies this helper epoch, boot and both original root hubs. Read back current values with `controller inspect`; a saved result is historical evidence, not continuing kernel authority.

The revision belongs to the whole controller store. Another policy mutation can make it stale. The store retains at most 16 policy slots and only the latest request per slot. Retired slots may be reused explicitly; old request IDs are not retained forever.

## Lost replies and explicit reconciliation

Query the original identity after a lost response:

```sh
sudo rustusb controller query --policy-id "$POLICY_ID" --request-id "$REQUEST_ID"
```

An exact replay of the retained original payload returns its historical result without repeating sysfs work. Reusing that request ID with changed arguments is rejected. An absent query result means unknown or expired, not proof of no effect. `in_flight` and `unconfirmed` writer states must not be interpreted as successful completion.

Helper restart clears live pins and creates a new helper epoch. It does not re-arm controllers automatically. HCD replacement and a new boot also require explicit verification. Refresh the inspection, global revision and request ID before reconciling existing intent:

```sh
INSPECTION=$(sudo rustusb controller inspect "$PCI")
BOOT_ID=$(printf '%s\n' "$INSPECTION" | jq -r .boot_id)
REVISION=$(sudo rustusb controller list | jq -r .historical.revision)
REQUEST_ID=$(cat /proc/sys/kernel/random/uuid)

sudo rustusb controller reconcile \
  --policy-id "$POLICY_ID" --request-id "$REQUEST_ID" \
  --expected-revision "$REVISION" --expected-boot "$BOOT_ID"
```

A short or failed sysfs write can leave partial denial. RustUSB never compensates by writing an allow value. A persistence error makes further mutation unavailable in that helper; preserve its history and resolve the reported uncertainty before issuing another operation. Cancellation of the CLI does not stop an accepted blocking helper operation or release its mutation lock early.

## Retire intent

Retirement requires an empty controller and no conflicting retained owner. It leaves kernel denial values unchanged and preserves the final request/revision as a tombstone. It does not release an attached storage device to local drivers.

After the controller is empty, refresh `BOOT_ID`, `REVISION` and `REQUEST_ID` as above, then run:

```sh
sudo rustusb controller retire \
  --policy-id "$POLICY_ID" --request-id "$REQUEST_ID" \
  --expected-revision "$REVISION" --expected-boot "$BOOT_ID"
```

Omitting `--controller-state-dir` on a later helper start disables management; it does not retire policies or restore defaults. Keep the original state for future reconciliation.

See the [implementation contract](research/controller-enrollment-implementation.md), [earlier boot-policy mechanism evidence](research/storage-controller-enrollment-validation.md) and [storage handoff design](research/storage-controller-enrollment-design.md) for the scope of this control and the remaining storage requirements.

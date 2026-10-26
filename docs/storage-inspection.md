# Inspect USB storage use

Administrators can inspect the currently observed users of a USB storage device without changing its driver or opening the storage media. Storage export remains disabled in both Native and USBFS backends. A report never grants permission to export.

## Run an inspection

Build the current CLI and helper, then configure the helper using the [Native setup guide](native-guide.md). This command is available in the default Linux build; no physical-backend feature is required. Restart an older helper cooperatively after installing the new binary. Identify the candidate fingerprint with an ordinary read-only inventory:

```sh
rustusb devices --json
```

An administrator can then request a report from the root helper:

```sh
sudo rustusb --json storage-inspect 'usb-<64-lowercase-hex-fingerprint>'
```

Replace the placeholder with the current fingerprint. For a source checkout, use `./target/release/rustusb`; `--socket` can select the already configured trusted helper socket. The request accepts no filesystem path, command or PID. Ordinary allowed users and the configured broker cannot invoke this administrator inspection.

## Read the result

| Field | Meaning |
| --- | --- |
| `device_id`, `bus_id`, `usb_generation`, `usb_address` | Candidate fingerprint and the USB generation observed during this scan. They are not hardware authentication. |
| `luns`, `blocks` | Observed SCSI LUNs, block devices, partitions and mapped consumers. Major/minor numbers identify current block objects. |
| `kernel_read_only` | The Linux block-object RO flag. It does not prove device-enforced or remote write protection. |
| `dm_hint` | `crypt`, `lvm` or `other`, derived from a DM UUID prefix. Closed LUKS containers and other encrypted filesystems are not detected by this hint. |
| `uses` | Positive holder, mount, raw/file FD, mapping, filesystem-reference or swap observations. PID/TID/start ticks identify visible tasks where applicable. |
| `busy_observed` | At least one positive use was found. `false` means only that this scan observed none. |
| `issues` | Unreadable, disappearing, malformed or bounded-out observations. Earlier busy findings are retained. |
| `limitations` | Gaps that apply even without an issue: non-atomic observation, hidden/taskless namespaces, non-FD kernel references, unprobed signatures and potentially unenumerated LUNs. |

The helper examines visible threads, including their independent FD tables, and resolves mapping metadata through identity-checked `/proc/TID/map_files`. It also follows block holders to mapped consumers. It does not include file paths, process command lines, full DM UUIDs, serial numbers or storage contents in the report. PID and device-number metadata is still administrative information; review a report before sharing it.

Exit code 0 means a report was produced. It does **not** mean a device is idle, coverage is complete, or export is safe. The tested idle/released reports still contain coverage issues. Missing access, disappearing tasks and limits are not treated as proof of absence. A service may be represented by its process references; this command does not resolve every PID to a systemd unit or enumerate every kernel-held reference.

## Boundaries and next actions

Only one actual inspection worker runs per helper. The report bounds retained nodes, usages, task/directory iteration and text. A ten-second cooperative budget stops starting more work; it cannot interrupt a kernel call that does not return. Disconnecting the CLI does not release the actual worker's quota, and helper shutdown waits for accepted work. An oversized result returns an error rather than a silently truncated success.

Use positive observations to locate the application, mount, swap area or mapped volume that needs administrator attention. The command does not stop services, unmount, flush, change read-only flags or modify device ownership. Even after users have cooperatively released the device, do not remove the existing storage blockers: a separate exclusion, cache-flush and recovery design is still required.

[Validation](research/storage-inspection-validation.md) records Ubuntu 22.04/24.04 x86_64 QEMU cases and the retained first mmap failure. [Design and upstream research](research/storage-inspection-design.md) explain why a proc scan, `O_EXCL`, local block RO or driver unbind alone cannot establish a safe writable USB handoff. Hardware storage acceptance, UAS, ARM64 inspection, hot-unplug inspection and full storage sharing remain unvalidated or unfinished.

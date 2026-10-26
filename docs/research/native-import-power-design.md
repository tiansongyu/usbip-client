---
status: proposed
date: 2026-10-26
---

# Keep an authorized imported USB device active

The E62 ARM matrix remains failed. Its isolated original-Linux USB/IP control
identifies imported HID runtime suspend; E63 adds a clean84045dfb control that
expects no events at auto/2000ms/suspended, then exact events after only imported
delay=-1, and again after explicit control=auto. Actual host/QEMU exits0 and
all frozen guards pass. This is diagnosis, not a passing RustUSB data matrix.

[Original investigation](arm64-usb-data-validation.md) ·
[Fixed-source research and original controls](../evidence/native-import-power-research-20261026/summary.json)

## Candidate policy and its limits

For each newly authorized, completely identified VHCI import, set only that
local generation's `power/autosuspend_delay_ms` to `-1`. Require readback `-1`,
actual `runtime_status=active`, and the same complete kernel/local identity
before returning success. Do not change global usbcore parameters, source-device
power policy, old journal entries, read-only observations or other USB devices.
Existing successful detach removes this ephemeral local node; never reopen an
old pathname to restore a power setting after detach or restart.

Fixed Linux v5.15/v6.8 `usb_new_device()` establishes use_autosuspend before
device_add. Negative delay has a separate usage-count hold that survives a later
`pm_runtime_allow()`/control=auto. Both delay and control sysfs stores discard
internal resume failures, so successful writes/readback alone do not prove
active. Missing attributes, EIO, error/unsupported/transitioning states must not
be silently interpreted as safe PM-disabled operation. This policy targets the
supported Ubuntu kernels; it does not implement USB/IP remote wakeup or system
sleep and cannot prevent an external administrator changing the policy again.

Primary fixed sources:

- [USB PM interface v5.15](https://www.kernel.org/doc/html/v5.15/driver-api/usb/power-management.html)
- [USB enumeration v6.8](https://github.com/torvalds/linux/blob/v6.8/drivers/usb/core/hub.c)
- [Runtime PM v6.8](https://github.com/torvalds/linux/blob/v6.8/drivers/base/power/runtime.c)
- [Runtime sysfs v6.8](https://github.com/torvalds/linux/blob/v6.8/drivers/base/power/sysfs.c)
- [Safe openat rustix1.1.5](https://docs.rs/rustix/1.1.5/rustix/fs/fn.openat.html)

Exact v5.15/v6.8 source, licenses, failed HTTP429 observations, necessary successful
primary-mirror reads, API references and two read-only ownership/kernel audits
are in the linked archive. These tags inform design; actual vendor-kernel
behavior needs QEMU validation. The E63 control samples one continuous import's
bus/PM state and exact events, not independent per-stage inode receipts.

## Common operation

Implement a private Linux-only helper in rustusb-native, conceptually:

```rust,ignore
fn prevent_import_autosuspend(
    devices: &Path,
    session: &NativeSession,
    expected_uid: u32,
) -> Result<()>;
```

Only existing authorized import paths call it after a matching USED row,
complete local address/VID/PID/filesystem-device/inode and original ownership
are known. It neither discovers nor authorizes a device. Production expected
UID is root; unprivileged fixture UID is confined to private test construction.
Secure import retains its existing selected-controller descendant checks.

Open the fixed local alias as a directory and fstat against the pinned receipt.
Follow only that legitimate sysfs alias; open fixed single-component children
relative to retained directory FDs with NOFOLLOW/CLOEXEC. Validate directory and
regular-attribute type, UID, no group/other write and filesystem identity. Use
no CREATE, TRUNC or APPEND. Revalidate local numeric identity through the pinned
directory and current alias before and after mutation. An alias replacement
cannot redirect an already opened attribute into a replacement generation.

Read tiny bounded decimal/state values. If a write is necessary, seek to0 and
perform one exact-length write of `-1\n`; a short write, EINTR or other error
is an operation failure with possible effects, not permission to retry a suffix.
Read back from the same opened attribute and require active from the pinned
device. Recheck the matching kernel row outside the helper before success.
All local descriptors close within the actual operation; no device FD is handed
off or added to a persistent owner/schema. Sysfs callbacks can block: NONBLOCK
does not make a driver callback cancellable or guarantee a deadline.

## Secure anonymous import

Use ImportChannel::commit's existing blocking owner after self.local is pinned
and the complete profile is available, before its final receipt. Profile
completion does not prove driver probe completion; negative delay is chosen
because its hold also survives a later control=auto. Check cancellation before
and after policy application, then recheck the same controller/row/generation.

The daemon already retains the actual blocking task and mutation guard, waits
through stop/EOF, records an available Observed receipt and always calls actual
K shutdown plus independent cleanup verification. Keep those paths on any PM
failure. Power state is neither a release proof nor a substitute for NULL and
old-generation disappearance. No new actor, capability or journal format.

## Legacy import, partial effects and cancellation

Keep the original attach method compatible. Add an explicit detailed in-process
failure type/method for the helper to retain a known partial session:

```rust,ignore
pub struct NativeAttachFailure {
    pub operation: Error,
    pub observed: Option<NativeSession>,
    pub cleanup: Option<Result<()>>,
}
// Existing attach_session keeps Result<NativeSession>.
// attach_session_detailed returns Result<NativeSession, NativeAttachFailure>.
```

This is not a serialized protocol/DTO change. Before an exact newly created
session and official endpoint record are verified, retain existing uncertain
tool outcomes without claiming or compensating an ambiguous connection.
After that point, preserve the observed receipt even if PM or its worker fails.

Acquire the backend mutex as an owned guard. Move it into the PM blocking worker
and return `(guard, result)`, keeping exclusion until the actual worker ends,
including when an external library waiter is dropped. On normal PM/postcheck
failure, retain that same guard and invoke a factored already-locked detach
body against only the exact new receipt. Preserve operation and cleanup results
separately. Successful compensation must still prove original NULL+gone; failed
or unknown compensation remains unresolved and must not discard that receipt.

The production helper uses the detailed method. Retain an unreleased observed
session in managed state before returning its failure, allowing existing exact
detach/reconciliation. Never overwrite an existing owner; an external collision
requires explicit reconciliation and must block new imports. A released receipt
does not become an active managed entry. The compatibility wrapper still reports
failure with context; direct callers needing partial ownership use the detailed
method. Implement Display/source without printing a raw session/USB payload.

Normal helper requests are owned by its JoinSet and awaited through shutdown;
they have no whole-operation EOF cancellation. The public async backend still
has its original broader drop/reconciliation limitation at OTHER await points
after kernel effects. The worker-guard change does not claim whole-operation
cancel rollback or cancellation of a permanent kernel block. Join failure retains
the known receipt and uncertainty; do not invent a release proof.

## Validation before acceptance

Meaningful filesystem/fault tests must cover exact-generation operation,
replacement and symlink rejection, no creation/truncation, bounded/invalid values,
wrong metadata, missing PM support, short/error single-write behavior, and active
state verification. An actual gated blocking worker must retain exclusion after
waiter cancellation until it ends. Legacy PM failure must exercise successful
and failed compensation, retained helper ownership and replacement refusal.
Preserve the existing ambiguous-tool-failure and secure initialization tests.

Regular files do not emulate kernfs store semantics: do not claim a fake-file
unit test proves a 2000→-1 kernel transition. The original-Linux control and new
actual product QEMU cases provide that evidence. Run original full core/GUI
gates, build and identify new native ARM packages, then execute all four original
ARM installed data cases with unchanged I/O, live-upgrade, Root/driver/PCAP checks.
Cover affected amd64 Native/USBFS, virtual and legacy import paths as appropriate.
Keep every old failure and executed-source identity. No hardware/complete-phase
acceptance is implied by this proposed design or its manual control.

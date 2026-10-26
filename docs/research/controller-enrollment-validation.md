# Persistent controller policy validation

The real RustUSB controller CLI and helper pass on Ubuntu 22.04 and 24.04 x86_64 in QEMU at clean commit `26faa7903fabb0720b5be778271f045a45181658`. Each pairing verifies explicit enrollment, helper restart, HCD replacement, a new kernel boot and empty retirement. The same source passes 1,193 core and 18 GUI test executions. Eight original QEMU processes exit normally.

This delivers the controller-policy part of the [implementation contract](controller-enrollment-implementation.md). **Product storage export remains disabled.** Enrollment grants no device access and does not prove USBFS revocation, media consistency or safe storage release. Use the [administrator guide](../controller-policy.md) for the actual commands.

## Product behavior

The optional root helper argument `--controller-state-dir` enables a separate, bounded policy store. RPC callers cannot choose that path. Root-only commands inspect, enroll, reconcile, query, list and retire explicit policy/request UUIDs under a global revision. The helper records Pending before fixed sysfs denial stores and commits the outcome before publishing a same-helper observation.

A restart reloads historical results, creates a new helper epoch and retains no live pins. Exact retained replays return history without repeating kernel operations. An absent old request is unknown or expired. Uncertain persistence blocks further mutations; query metadata is not a current kernel grant. The actual blocking closure holds both the common mutation guard and its worker quota until it returns, even if its waiter is cancelled.

The kernel component requires a complete PCI xHCI root pair, the exact selector and boot UUID, boot-time device default denial, current global zero and current root device-default zero. It retains directory ancestors, root hubs, their normal hub interfaces and the fixed policy attributes. Two complete matching observations and tail boot/global checks reject observed drift. Writes are single checked stores of `0\n`, with readback and no allow-value rollback. A failed inspection revokes a saved preparation. These observations are not an atomic kernel snapshot or protection against an external administrator's unobserved changes.

Existing Native export registrations, live export owners and legacy exports are checked by saved original scope; imports are excluded. A physical grant is scoped only through its exact same-boot sysfs generation/address. An absent or replaced generation remains unknown until its original grant is explicitly reconciled. Known other-controller records do not block the selected controller. A build without the physical adapter refuses controller-enabled startup when the configured physical-state path exists, rather than ignoring that history.

## Executions and provenance

Implementation began on 2026-10-26; verification and documentation continued into 2026-10-27. Fresh disposable overlays derive from the earlier product-free Ubuntu preparation images. Only test guests receive boot settings, udev rules, product executables or controller mutations. No host USB device, block device, module, service, account or boot configuration is changed.

| Item | Jammy server → Noble client | Noble server → Jammy client |
| --- | --- | --- |
| Server kernel | `5.15.0-198-generic` | `6.8.0-147-generic` |
| Host runner | session `21193`, exit0 | session `85791`, exit0 |
| Server QEMU processes, in boot order | `3067516`, `3067894`, `3068205` | `3067514`, `3067820`, `3068038` |
| Client QEMU process | `3068430` | `3068177` |
| Actual exits | All four exit0 | All four exit0 |

Each server overlay spans two actual guest reboots and three distinct boot UUIDs. A read-only 64 MiB synthetic disk is present from power-on in the last two boots. Evidence collection, kernel-integrity pattern checks and all source/parent/release-hash guards pass. Both entire backing files remain zero after the actual QEMU exits.

The final software runner observes both `scripts/check.sh` and `scripts/check-gui.sh` exit0, with 778 tracked non-evidence input hashes unchanged. Formatting, default and all-feature Clippy/tests/release builds, rustdoc and vendored adapter tests pass. The 1,211 total is test executions, not that many distinct USB scenarios. An earlier complete gate at `b7ff08f2` records 1,206 executions; it predates three additional tests and the audit fixes and is not relabeled as the final gate.

## Actual product scenarios

| Scenario | Observed result on both servers |
| --- | --- |
| Normal boot without the required parameter | Inspection succeeds; enrollment refuses without consuming revision0 or changing defaults |
| Wrong selector or global revision | Refused; revision remains0 |
| Policy-allowed UID65534 requests controller list | Root-only check refuses the actual CLI request |
| Initial enrollment | Revision1, Armed result, both hubs at device/interface defaults0; target remains unauthorized without local storage |
| Exact retained request replay and query | Same historical result; a changed payload with that request UUID is refused |
| Nonempty retirement | Refused without consuming another revision |
| Helper restart in the same boot | New helper epoch, no verified pins, old Armed history retained; explicit reconciliation produces revision2 |
| xHCI unbind/rebind in the guest | New root and target generations, defaults0/1; explicit reconciliation records revision3 with the new root identities and defaults0/0 |
| Real reboot with storage attached | New boot UUID, historical previous-boot receipt and no live pins; explicit reconciliation records revision4 |
| Empty retirement after test-device removal | Revision5 tombstone, no pins, denial defaults unchanged |

Each helper is an actual child of a retained guest observer, with PID/start time, live executable hash, command line, credentials and descriptor observations. Four helper children per pairing are stopped cooperatively and actually waited with exit0. Each pairing records 37 actual CLI invocations, including six expected refusals. Fast CLI records retain argv, PID, exit, stdout/stderr and executable-file hashes; they do not claim a live `/proc/PID/exe` sample for every short invocation.

## Separate stock Native data regression

After product reconciliation, the fixture explicitly uses stock `usbip bind` and authorizes the synthetic target under those controller defaults. The source remains bound to `usbip-host`, with interfaces unauthorized and no local block child. Stock `usbipd` and `usbip attach` establish the data connection to the other guest.

The client enumerates one read-only 64 MiB LUN, performs a 1 MiB direct read matching zero bytes and completes `sg_sync` with exit0. Client detach removes that original imported device. The original stock daemon is then stopped and actually waited. The disposable source is hot-removed before empty controller retirement; this is not a product media-release operation.

This checks compatibility of product-established defaults with actual upstream USB/IP I/O. It does not implement secure RustUSB storage admission, repeat E66's two-LUN/fault matrix, validate writable media or substitute for the earlier encrypted HID/FTDI tests.

## Failures, reviews and retained evidence

Development failures remain in the [evidence archive](../evidence/controller-enrollment-20261027/README.md): the first Native fixtures inherited group-writable permissions; the first helper compilation used a missing path import and the wrong existing topology type; the first store fixtures left their anchor group-writable; the first strict Clippy run rejected manual prefix stripping. Their original logs and available affected-source copies are preserved. Corrected fixtures use explicit permissions; production permission checks were not weakened.

Two separate read-only source reviews identified preparation reuse, incomplete observation checks, ancestor continuity and root hub interface omissions before the first product commit. Follow-up reviews found the default-build physical-history gap, failed-inspection preparation reuse and malformed historical port chains. The fixes and meaningful negative tests precede the final source. Reviews are source analysis, not additional USB executions or a general security certification.

The archive retains both full software gates, targeted development logs, two raw QEMU runs, guest archives, original methods and fixed source reviews. A separate same-author audit checks original records and Git inputs without importing the runners. The first audit misclassified an installation argument as a CLI call; its original failure is retained. The first archive scan matched its own key-marker literal; an anchored check corrected the method. Neither changes a test verdict. Private keys, disk overlays, uploaded executable payloads and host-private data are excluded. The manifest records exact member sizes and hashes.

The [documentation QA archive](../evidence/controller-enrollment-docs-20261027/manifest.json) retains CLI help, local targets, subpath HTTP checks and desktop/mobile/no-script browser observations. Screenshot capture was unavailable; clipboard, reduced-motion emulation and all existing tab interactions were not rerun.

## Remaining work

No public enrollment receipt is accepted as a USB target grant. Exact-generation storage admission, early-boot permission/ACL continuity, old USBFS open/control races, application and every-LUN cache handling, safe release and fault recovery remain separate work. Permanent kernel/filesystem blocking is not claimed cancellable. This increment does not add ARM64 controller-policy, real-hardware, UAS or Windows acceptance, nor complete the original Phase 0–7 goal.

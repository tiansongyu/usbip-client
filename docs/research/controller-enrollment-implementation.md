# Persistent controller enrollment implementation

Status: implemented and [validated](controller-enrollment-validation.md) at clean `26faa790`; contract started 2026-10-26. Baseline `0c3dd04c`. This implements the controller-policy layer of the [storage design](storage-controller-enrollment-design.md), using the [E67 mechanism evidence](storage-controller-enrollment-validation.md). The full storage handoff requirement remains separate: a controller record does not authorize a USB target, revoke old USBFS operations, drain media caches or release a storage session.

## Administrator workflow and scope

Add an explicit helper `--controller-state-dir` option. Without it the controller management service is unavailable; existing helper startup and journal formats retain their behavior. The path is chosen only by the root administrator starting the helper. RPC callers cannot choose a filesystem path. A new root-only CLI command group uses the existing trusted Unix socket and OS peer credentials.

The administrator first inspects a canonical PCI address and obtains its exact observed selector, current boot UUID and controller/root-hub identities. Enrollment supplies that selection, an explicit policy UUID, an explicit request UUID and an expected revision. Reconcile and retire also require explicit request UUIDs and revisions. A query for the original policy/request identity never retries a mutation or generates a new identity.

A helper built without `physical-usb` cannot inspect the physical journal. With controller management enabled, it must refuse startup if the configured physical-state path exists or cannot be checked; use a capable helper to reconcile that history first. This check precedes creation of controller state. Omitted controller management preserves the existing startup behavior.

The first implementation supports a dedicated PCI xHCI controller with both logical root hubs, selected by canonical BDF plus PCI vendor/device/subsystem identifiers. The actual driver must be `xhci_hcd` and class must be xHCI. It accepts no driver name, sysfs path, attribute value, boot option or shell command from RPC. Production sysfs/procfs roots are fixed. Tests alone use private filesystem roots.

Arming requires the actual boot command line to select device default denial, the current global parameter to remain zero, and a complete current topology. The parser accepts a documented strict ASCII command-line subset, rejects ambiguous quoting/escaping and duplicate/aliased target options, and does not mistake tokens after `--` for kernel parameters. A current global zero without boot provenance is insufficient. Already authorized non-root USB devices, active interfaces, unexpected hubs, missing topology or identity drift refuse arming without forced deauthorization.

Only the fixed `authorized_default` and `interface_authorized_default` attributes may receive a single checked write of `0\n`. A short/error store is an uncertain mutation; it is never completed by writing a suffix. Every root/attribute is opened through trusted pinned directories, with fixed basenames, NOFOLLOW, ownership/type/filesystem checks, and before/after identity and value verification. The complete root set is re-observed. No method writes device authorization, defaults1, PCI unbind/reset or boot configuration.

Original scope is checked under the same mutation guard. Durable and live Native exports use their saved canonical paths, including registrations that have not bound yet; VHCI imports are excluded. Legacy exports use their saved topology. Physical grants lack a historical controller path, so only the original same-boot sysfs generation and address can establish scope. Missing/replaced generations are unknown and require the existing explicit grant reconciliation; known other-controller records do not block this controller. Enrollment never retires these owners implicitly or substitutes a current Bus ID for an old device.

The kernel observer retains every directory ancestor as well as each root interface. Two complete matching observations, with boot/global checks at both ends and an unchanged hub driver, are required. A failed public inspection also consumes any previous preparation; a later arm requires a new explicit prepare. Saved device paths must contain a consistent USB bus and nested port chain before they can exclude an unrelated owner. This detects observed changes; it is not an atomic kernel snapshot or exclusion of an external administrator.

## Durable state and request outcomes

Use a separate schema and directory, not the Native channel or USBFS resource journal. Reuse their protected-file operations: bounded bytes before strict decoding, root0700 directory, root0600 regular files with one link, lifetime flock, identity/bytes checks, fixed temporary file, atomic rename and directory fsync. An uncertain commit poisons further mutation. Missing/corrupt existing state does not initialize a fresh history.

The bounded store retains at most 16 controller records, including retired records, and uses monotonically increasing revisions. Each record retains its last request's complete typed payload and outcome. Replaying that retained request returns its original historical outcome only when the whole payload matches; it does not perform sysfs work again. Reuse of a retained request ID with different input fails. An older request outside this retained window is unknown/expired; the original expected revision prevents it from being applied again. The service does not promise unbounded UUID retention. Retirement preserves its revision/tombstone, and a later explicit enrollment may reuse that policy slot with a new revision.

Order for an accepted mutation:

1. Authenticate root, validate bounded typed input and acquire the actual helper mutation owner.
2. Check store health, retained request identity and revision. Inspect/pin the intended current controller before recording a new plan.
3. Persist the new pending request and intended denial before any sysfs write. This consumes the old revision even if a later step fails.
4. Apply and verify fixed denial on the same pinned generation. Retain achieved denial on failure; never restore allow values automatically.
5. Persist the operation outcome. Only then publish the successful current helper-epoch receipt. A final persistence failure leaves the operation unconfirmed.

Reopening the store creates a new helper epoch and no live receipts. Persisted success is historical evidence, not current authorization. A pending operation requires explicit reconciliation under the current boot/policy/selector; it is not silently replayed. Reconcile creates a new receipt and does not adopt an old target/session grant.

Retire withdraws the intent to re-arm and drops that policy's current receipt. It retains denial and durable history. It must reject a nonempty or uncertain controller topology instead of implicitly releasing a device to local drivers. A live future storage owner must also block retirement when target admission is integrated. At this increment no controller receipt is accepted by product export/import, and existing storage blockers remain enforced.

## Real owners and observation

Move the global helper mutation guard into the actual blocking closure covering persistence, sysfs work and final recording. A dropped RPC, elapsed response deadline or stop signal cannot release that guard or worker quota while the operation still runs. The helper joins accepted connection tasks and their original workers during shutdown. Permanent filesystem or kernel blocking is not claimed to be cancellable.

Queries use a separate short-held, immutable committed view and explicit writer state. Never hold that view lock across filesystem/sysfs I/O. Historical committed results are labeled as such; an in-flight or poisoned writer remains visible as pending/unconfirmed. Query metadata is not a live kernel receipt or USB cleanup proof. Current inspection is a separate bounded real observation. No background loop automatically authorizes or re-arms targets.

## Verification required before delivery

Unit/integration checks must cover strict BDF/selector/boot parsing, ancestor/leaf symlinks, wrong owner/mode/type/filesystem, missing and extra root hubs, controller/attribute replacement, authorized targets, global drift, exact zero stores/short-write behavior, and retained pins. Store tests cover malformed/oversized state, lock exclusion, missing history, CAS/replay conflicts, retirement/re-enrollment, pending restart and failures before/after rename. An actual gated blocking-worker test must prove cancellation does not release the mutation owner early.

Use both product-free Ubuntu parents in QEMU with fresh disposable read-only synthetic media. Exercise real product enrollment, helper restart, HCD replacement, real guest reboot with storage present at boot, explicit reconciliation, wrong-identity/revision/UID refusal, retirement while nonempty and empty retirement. Observe original PID/FD/boot identities, real child exits and durable bytes independently. Run a stock Native data-path regression under product-established controller denial, labeled separately from secure RustUSB storage support. Preserve failures and unknown outcomes.

Run the full repository software gates and applicable existing data-path regressions on fixed clean sources before updating README/site capability claims. Early-boot ACL continuity, adversarial privileged policy changes, prior USBFS open/control races, existing-media consistency, full storage export/release, real hardware and remaining Phase 0–7 work stay explicit.

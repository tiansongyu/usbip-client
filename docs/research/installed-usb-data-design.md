# Installed service USB data validation design

2026-10-25. Starting clean source `5e867949fe0752a463eba344c51d155e9c57a91d`.
This is a proposed test extension, not a claim of completed installation or USB
acceptance. The preceding [package lifecycle validation](debian-installation-validation.md)
is verified progress, but its virtual export job had no actual client import.

## Scope and ownership

Exercise the shipped base package and unchanged systemd helper/agent units in
full Ubuntu 22.04 and 24.04 amd64 guests. Ordinary UID1000 uses the existing
authenticated application API; the actual nonzero package broker UID is derived
per guest and applied to the helper policy, TLS profiles and public manifest.
No production test RPC, permission bypass, host USB, host root service/module or
host network mutation is allowed. Use only disposable emulated USB keyboard and
FTDI devices. No storage device or filesystem redirection is introduced.

Keep the existing production Rust core, transport, helper, backend and frontend
unchanged unless a measured failure requires a separately documented fix.
Do not weaken or relabel the existing initramfs/Rootproof verifiers. New full-OS
fixtures must preserve their own complete original observations and correlate
application IDs with helper resource/proof IDs and actual kernel/driver state.

## Full-system test environment

Use the previously verified, dated official cloud disks and signed-checksum
provenance. A preparatory guest may install matching kernel tools/modules and
test prerequisites, then shut down actually before becoming a private derived
base. Record the official image hash, preparation commands/exits, installed
versions and derived disk hash. Derived bases contain no active RustUSB policy,
grant, job, TLS service key or running product. They are an optimization for OS
dependencies, not fresh-install acceptance; the separate E58 cold tests retain
that scope. Keep all disks, SSH/TLS private keys and ELF/package bytes private.

Each pair has a NAT NIC only for SSH/official APT and a separate socketpair NIC
for the isolated USB TLS link. Set only guest addresses/routes. Capture only the
private data NIC. Record actual systemd, kernel, usbip tools, units, executable
hashes and package control metadata. Load approved usbip_host/vhci_hcd/usbhid/
ftdi_sio modules explicitly as guest administrator; package scripts must not
start usbipd or load modules. Native and USBFS own disjoint device pools and run
in separate cases, never stacked on one device.

Retain the entire boot data-NIC capture separately. Start a new unfiltered capture
after both actual DAD/source-route readiness checks and before either product unit
starts; end only at real QEMU exit. The original strict TLS classifier applies to
this complete product window unchanged. Full-OS early boot can contain zero-source
MLD traffic outside its older initramfs contract; do not rewrite/remove packets or
relax that verifier to reclassify a failed whole-boot run. After client upgrade,
wait for both original incoming owners to become terminal with verified cleanup
before reimport; published export availability alone is not an engine release.

## Planned matrix

| Backend / link | Server | Client |
| --- | --- | --- |
| Native / IPv4 | Ubuntu 22.04 | Ubuntu 24.04 |
| Native / IPv6 | Ubuntu 24.04 | Ubuntu 22.04 |
| USBFS / IPv4 | Ubuntu 22.04 | Ubuntu 24.04 |
| USBFS / IPv6 | Ubuntu 24.04 | Ubuntu 22.04 |

Use explicit schema2/ALPN2 configuration for the real TLS device stream, retaining
the existing framing/health contract. A management catalog alone is insufficient.
Do not call any row passed before both actual guests, kernel enumeration, typed
I/O and cleanup/proof observations have completed.

## Per-pair acceptance sequence

1. Install the reviewed base package, explicitly provision exact synthetic-device
   pools, TLS identities, operator ACLs and fixed package paths. Capture original
   sysfs generations/configuration/interfaces/drivers/node permissions and empty
   VHCI baseline. Confirm service readiness using real authenticated IPC, not
   Type=exec alone. Check unauthorized operator refusal without a device change.
2. Start the server profile through the ordinary operator, discover its exact
   catalog, import HID and FTDI through two client profiles and verify actual VHCI
   receipts/generations and Connected sessions. Inject a QMP key and known FTDI
   bytes; retain actual evdev and bidirectional serial results. Reuse the existing
   audited probes where compatible, preserving their assertions and real exits.
3. Upgrade the client package while both imports are active. Require actual
   agent-then-helper cooperative exit0, empty old cgroups, exact terminal Root
   proofs, NULL/removed original VHCI generations, and no automatic restart.
   Root proof matching is mandatory. The optional application attempt proof is
   populated during recovery; ordinary cooperative `released` sessions may keep
   it null. Any nonnull application echo must equal the exact Root criterion,
   as in the existing lifecycle verifier. A null echo never substitutes for a
   missing or mismatched Root terminal.
   Server exports must remain available. Inspect actual package replacement and
   retained application/helper records; PID death or zero new counters is not
   proof that the old devices were released.
4. Explicitly restart the client services, import again and repeat real I/O.
   Upgrade the server while its exports/grants and incoming sessions are active.
   Verify cooperative ordering, all old resource proofs, original drivers/config/
   interface/node baseline restoration and remote client kernel teardown.
   Keep operation errors separate from cleanup outcomes.
5. Explicitly restart both ends as needed and complete a third actual I/O round,
   then stop the owned jobs/services and verify their final original baselines.
   Retain every actual command/child return and both QEMU exits. A deadline is an
   observation limit, never a completed owner; no hidden forced cleanup counts
   as success. Preserve all failed development attempts.

## Attribution and limits

Freeze source/HEAD/host ELFs through all live local gates/QEMU processes. Record
actual package/ELF hashes independently of source identity. Run the complete root
and separate GUI gates for delivery and keep prior software/USB results at their
actual revisions. Archive only explicit public evidence; inspect nested members
and verify hashes/tracking before normal SSH push.

This matrix cannot establish real physical G1/G2, arbitrary Composite/ISO support,
arm64 installed runtime, permanent kernel/FS block cancellation, power-loss
recovery or Windows USB. It does not complete Phase 1's real-hardware gate or the
full Phase 2/3/5 scope. README/site/global readme-site delivery already exists;
update only verified capability links after this work has actual evidence.

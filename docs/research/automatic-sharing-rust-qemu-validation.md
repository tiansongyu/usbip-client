# Automatic rules: virtual import and USBFS under v2 faults

2026-10-25. All final software/QEMU evidence is locked to clean
`42e98ab36cb974022b49588055c3f0407a5f1b18`. This validates the already implemented
[E53 fixed-profile runtime](automatic-sharing-runtime-validation.md) against the
Rust engines, following the [fixture contract](automatic-sharing-rust-qemu-design.md).
No product Rust, Cargo manifest/lock or GUI implementation changed from
`20a15a01`; the three actual release ELFs are also byte-identical. New validation
does not replace dynamic pools, GUI editing, installation or hardware acceptance.

## Actual automatic admission

The [new paired fixture](../../scripts/qemu_automatic_rust/run.py) reuses the
original framed-v2 runner, exact-flow valve, packet auditor and Rust lifecycle
checker. It replaces selected manual Starts with real CLI rule operations and
actual RetryStart responses from their committed origins. It never manufactures
a mutation acknowledgement, Start receipt, kernel observation or cleanup proof.

For virtual devices, the client automatically admits one persistent CDC job and
four successive HID jobs. After each exact-flow fault, the original job must be
Finished and its session Verified before an explicit CAS Enable rearms the same
rule. The saved pre-Enable snapshots retain the old consumed occurrence and no
extra origin. One unchanged advertised token per profile is explicitly rearmed;
this is not evidence of physical hotplug or uninterrupted presence between polls.
The server retains its original manual job. Virtual HID coverage is real kernel
enumeration and idle-link failure; CDC coverage includes actual35-byte echo
traffic. No injected virtual HID key is claimed by these runs.

For USBFS, a local complete-pool rule automatically starts the server's real
two-device job. Its original physical grant identities remain through all target
faults. Clients still issue the original manual Starts: unattended physical remote
import remains blocked pending authenticated complete configuration/alternate
safety evidence. Actual keyboard events, FTDI traffic, both exact physical Finish
proofs and complete original driver/configuration/interface baseline remain required.

The participating agent first completes its original no-policy denials, then its
actual idle process is stopped and reaped0. A root-owned0644/nlink1 policy is
captured before the new agent enables strict disk3. The original UID1002 denials
remain. New UID1001 has every manual Read/Start/Stop/Discover/Audit profile ACL but
no automation grant. Its real rule Create exits1/Permission; saved before/after
snapshots retain empty jobs/sessions/requests and an identical empty automation
journal, with exactly one added OS-peer UID1001 Denied audit. Native/physical root
file presence and any actual bytes remain identical. This isolates missing
automation delegation from missing manual ACLs. The fast negative CLI has no
separate live `/proc/exe` sample; actual agent/helper ELF and kernel-derived audit
identity evidence retain their stated scope.

Every successful automatic Start is checked in its original saved snapshot for
matching rule/revision/delegated UID/profile/origin request/job, exactly one legacy
Start audit and the same-sequence Fired event. Actual intent/query frames retain
the original mutation UUID and outcome. Five origins/jobs in each virtual pair
and one in each USBFS pair establish no duplicate firing in this bounded run.

## Final matrix

QEMU6.2.0 boots the actual Linux6.8.0-106-generic kernel in isolated x86_64 guests,
using role-separated ephemeral credentials, UID2000 agents and UID1000 operators.
All seven host verifiers return0/PASS; all fourteen actual guest processes return0,
without forced termination. Every source/ELF/HEAD/clean guard is true.

| Invocation at `42e98ab3` | Inputs | Result |
| --- | ---: | --- |
| Virtual remote rules, IPv4 | 247 | PASS;43 additional rule assertions,5 automatic jobs |
| Virtual remote rules, IPv6 | 247 | PASS;43 additional rule assertions,5 automatic jobs |
| USBFS local rule, IPv4 | 247 | PASS;17 additional rule assertions,1 automatic job |
| USBFS local rule, IPv6 | 247 | PASS;17 additional rule assertions,1 automatic job |
| Original no-policy virtual v2, IPv4 | 244 | PASS; original disk2/authorization/packet/lifecycle checks |
| Original no-policy USBFS v2, IPv4 | 244 | PASS; original disk2/grants/baseline/packet/lifecycle checks |
| E53 Native local rule, IPv4 | 237 | PASS; original264 +17 rule assertions and3 HID/FTDI I/O rounds |

The six v2 invocations each retain all original three directional15-second valve
windows, seven actual I/O rounds, sustained serial sibling, independent Catalog
during the fault, original owner/socket identity, exact packet classification,
kernel USED/NULL/Gone observations and full Root proof bindings. Live pre-disarm
proofs must still exactly match final raw artifacts. No assertion about an
unobserved pending URB, physical hardware or a new process's empty resources is
substituted for actual release.

USBFS IPv6 c2s preserves the original client `end=io`/operation Io and server
`probe_timeout`/operation Timeout. Both have Verified cleanup under the original
checker. The other new directional outcomes report probe timeout on both ends.
The client Io result is not reclassified as timeout, clean EOF or a traced reset.

## Software, schema and provenance

The [full outputs and manifest](../evidence/automatic-sharing-rust-qemu-20261025/summary.json)
record399 frozen source/build/fixture inputs and exact release hashes.

| Gate at clean `42e98ab3` | Passed | Failed / ignored |
| --- | ---: | ---: |
| Complete root `scripts/check.sh` | 1019 | 0 / 0 |
| Independent GUI `scripts/check-gui.sh` | 11 | 0 / 0 |
| Python schema preflight | 50 | 0 / 0 |
| Complete generated guest `dash -n` | 2 | 0 / 0 |

Root/GUI gates keep their original fmt/clippy/tests/release/rustdoc/vendor/JS
checks; the34 GLib dependency warnings remain unsuppressed. Product sources and
the CLI21,846,352B (`7b721c27…`), helper7,525,544B (`475b2f97…`) and GUI17,228,584B
(`edfc9127…`) match the E53 implementation; full SHA-256 values are in the manifest.
This is a new execution at its recorded head, not a reassignment of old evidence.
The E53 local signal/backpressure/CLI tests are not rerun or recounted here.

The original Rust v2 checker continues to require disk2 by default, now with a
closed whole field set. Only the new explicit virtual-client or USBFS-server
expectation requires disk3 and its nonnull complete automation object. Other roles
retain disk2. Extra UID1001 operator metadata is also an explicit new-fixture
expectation; default configuration assertions remain exact. The50 synthetic
positive/negative cases reject missing/null/mixed/unknown shapes, wrong roles/
versions and malformed journal collections/sequences. They test the verifier,
not the product's independent Rust journal decoder. Original no-policy QEMU runs
validate the stricter defaults against actual saved product bytes.

The first generated-guest preflight failed before any VM started because the Rust
fragment intervened between `network_ready` and the role workflow. The corrected
unique insertion anchor is the actual `rust_server_setup` branch. Both complete
generated scripts then passed. All seven new final QEMU invocations passed on
their first execution. Previous E53 failures remain in their own report and archive.

The [original archive manifest](../evidence/automatic-sharing-rust-qemu-20261025/archive-manifest.json)
checks4421 original members,52,966,244 bytes. The archive is11,150,104 bytes,
SHA-256 `95b4678c0d0d02526c675594391c166a116a87553f1b15ef9c8f4e99c28cadd4`.
It excludes private keys, ELF/rootfs payloads and host raw inventory. Later docs
and publication receipts do not become the tested code revision.

Documentation QA checks18 files, local targets, site references, actual CLI help,
all399 frozen inputs and the same three ELFs; see the
[QA record](../evidence/automatic-sharing-rust-qemu-20261025/qa.json).
The four global README skill files and Codex discovery symlink remain intact.
Current IAB DOM checks at the project prefix show desktop1280/scroll1265 and
mobile390/scroll375, with the updated scope visible and actual Tab focus2px solid
on the rules link. At390px with page scripts blocked by preview CSP, all three
backend panels, commands and updated copy remain visible. Original image assets
load; no new pixel or reduced-motion emulation is claimed. Temporary tab,
viewport and server are reclaimed; [observations](../evidence/automatic-sharing-rust-qemu-20261025/browser-check.json)
record the actual values. No new native GUI window is part of this validation.

Still unfinished: GUI rule CRUD/unknown-response flows, dynamic admitted pools,
authenticated physical remote safety/generation evidence, automatic raw-v1 and
hotplug/revocation/prepare/WAL QEMU expansion, packaged installation/systemd/
upgrade/uninstall/reboot, G1/G2 hardware, full G3, performance/compatibility work
and Windows support. The full Phase0–7 goal remains active.

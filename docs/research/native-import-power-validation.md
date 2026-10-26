# Authorized import runtime power validation

2026-10-26. The per-import runtime-power fix at clean
`ed1e8fef84c096feaa337484a4b75f145d6db278` passes the original software gates
and eight installed Native/USBFS data categories across amd64/arm64 and IPv4/IPv6.
One ARM64 category passes in an explicitly instrumented xHCI diagnostic trial;
its preceding pre-product kernel boot failure remains failed. This is simulated
device evidence, not real-hardware acceptance or a boot-stability fix.

[Design](native-import-power-design.md) ·
[Research controls](../evidence/native-import-power-research-20261026/summary.json) ·
[Execution summaries](../evidence/native-import-power-20261026/summary.json) ·
[Exact original members](../evidence/native-import-power-20261026/originals-manifest.json)

## Implemented behavior

After authorization and complete identification of a newly imported VHCI
generation, both Native import paths set only its local
`power/autosuspend_delay_ms` to `-1`. Success requires readback, active runtime
state, and the same complete row and local identity. The operation pins directory
and attribute FDs, rejects replacement/symlink/unsafe metadata, bounds reads and
uses one exact store. It does not change global USB parameters or source-device
power policy. Detach removes the ephemeral generation; no old path is reopened
to restore a setting after removal or restart.

Secure import keeps the operation inside its existing blocking owner and
Observed/shutdown/proof path. Legacy `attach_session_detailed` preserves a known
partial receipt and separate operation/compensation results, while the existing
method remains compatible. Its owned mutex stays with the actual worker through
waiter cancellation. The helper retains unreleased partial owners and one bounded
conflicting-generation receipt, without overwriting an older owner. Release still
requires the original NULL port and old-generation disappearance.

No dependency, wire format, persistent schema, permission or ALPN change. A missing
or unsupported PM attribute is an operation error, not assumed safe. Successful
sysfs stores alone cannot prove resume; fixed Linux sources show their callbacks
discard resume errors. Negative delay retains a separate usage hold after later
`control=auto`. The isolated original-Linux control verifies this transition;
regular-file unit fixtures do not emulate kernfs store semantics.

## Actual installed matrix

All executions below use clean `ed1e8fef`. IPv4 places the server on Ubuntu 22.04
and client on 24.04; IPv6 reverses them. Actual vendor kernels are
5.15.0-198/6.8.0-147. Each positive pair keeps the original concurrent HID/FTDI
imports, three complete I/O rounds, client then server live package upgrade,
exact Root correlation/kind/UID/key/resource/release criterion, driver/config/alt
and node/generation baseline, and strict boot/product-window PCAP checks.

| Architecture | Backend | Network | Verdict | Actual guest exits | Root proofs |
| --- | --- | --- | --- | --- | --- |
| amd64 | Native | IPv4 | PASS | Two0 | 16 |
| amd64 | Native | IPv6 | PASS | Two0 | 16 |
| amd64 | USBFS | IPv4 | PASS | Two0 | 10 |
| amd64 | USBFS | IPv6 | PASS | Two0 | 10 |
| arm64 | Native | IPv4 | PASS | Two0 | 16 |
| arm64 | Native | IPv6 | PASS | Two0 | 16 |
| arm64 | USBFS | IPv4 | PASS | Two0 | 10 |
| arm64 | USBFS | IPv6, original attempt | **FAIL before product installation; forced abort** | Two0 | Not executed |
| arm64 | USBFS | IPv6, independent boot trace | PASS, diagnostic instrumentation explicit | Two0 | 10 |

Eight positive pairs provide 24 complete HID press/release and FTDI rounds,
840 exact serial bytes in each direction and 104 Root release proofs. They are
not nine positive pairs. The original ARM adapter, guest and verifiers are
unchanged. The independent diagnostic adds only selected metadata xHCI trace
events from QEMU boot; its generated boot method, trace names, original-source
hashes and separate outer verdict are retained. It does not change CPU count,
TCG, firmware, peripherals, timeout, acceptance or cleanup predicates.

The failed Noble server boot reports xHCI controller death, soft lockup and RCU
stalls before SSH and before uploading/installing any product package. A verified
QMP `system_powerdown` is accepted but does not stop the stalled guest. A later
explicit QMP `quit` forcibly ends that exact saved command/PID. The original
runner reports FAIL and collects both actual exits with code 0; neither those exits nor
the subsequent traced PASS changes its verdict. The underlying boot instability
has not been diagnosed or fixed. No repeated uninstrumented retry is presented
as a fix.

Three additional original paired regressions pass at the same source and frozen
host ELFs: legacy Linux `usbipd` → RustUSB Native client HID, plus secure virtual
HID/CDC on IPv4 and IPv6. Each has two actual exits with code 0. They reuse the complete
locked all-features release build with `--skip-build`, explicitly recorded by an
outer source/HEAD/clean/three-ELF guard. They are separate from the installed
matrix and from Cargo counts. In total the main archive contains eleven positive
pairs and one failed pair, with 24 actual QEMU exits with code 0. The earlier single-guest
original-Linux control belongs to the separate research archive.

## Software and package provenance

Original `scripts/check.sh` and `scripts/check-gui.sh` pass at clean `ed1e8fef`:
core 1120 + GUI 18 = **1138 test executions**, zero failed/ignored, with 738 tracked
non-evidence inputs and all HEAD/clean/input guards true. Host release SHA256:

| ELF | SHA256 |
| --- | --- |
| CLI | `1a22d6159d624db41eaa0cf81b41683208e603117e450f7de99fc92a75fb7c2f` |
| Helper | `aebc1fbffd62cd029fa7f94c7df423c42f3be29c9c5206471a07f62c5f86647d` |
| GUI | `2551ed1afcc84098b94052ed58b0ff5f835468323de58815b154ceba6b388690` |

All local owners finish before documentation or repository changes. Development
failures remain: initial Errno conversion compile error, strict metadata fixture
permissions, and a test-only Clippy clone warning. Their actual logs and selected
source snapshots are included without labeling dirty runs as clean full gates.

amd64 uses the actual Jammy host-built base
`0.1.0~pm1+ged1e8fef84c0-1`, plus a strict control-only `+pmtest1` derivative.
An earlier independent base/upgrade GUI archive comparison **fails** because
selectors 0.38.0's bundled source gzip timestamps differ; decompressed source
bytes match. That broad audit remains failed. The installed matrix uses the
separately audited formal derivative, which preserves every payload member,
mode, owner, link and maintainer script. No packaging code is changed to hide
the difference.

ARM64 uses actual native CI Jammy packages from source `ed1e8fef`,
`0.1.0~ci31+ged1e8fef84c0-1`, on both kernels, with the strict `+armtest1`
derivative. ZIP/API digest, architecture, package and actual AArch64 payloads
are checked. Its executed CLI SHA is
`fcd4b2d35c3ed4c88d1ee284302d44c117e555b995248f9254c63136d5795993`;
helper is
`a3b8f928c9e23e40b3d149a93ef419bb7a09ae5fb1d5d1b71ad9487a9b00e4f0`.
The separately downloaded Noble build is not executed in this matrix. These
upgrades exercise lifecycle with the same code payload, not old-code migration
or GUI operation. Product-free prepared disks and E61 cold-installation evidence
keep their earlier source provenance.

[Core CI 40284815248](https://github.com/tiansongyu/usbip-client/actions/runs/40284815248)
and [GUI CI 40284815259](https://github.com/tiansongyu/usbip-client/actions/runs/40284815259)
complete successfully on all four native Ubuntu/architecture jobs. Primary
run/job/step metadata is retained; full CI logs and per-platform test totals
are not reconstructed from metadata. Pages 40284815276 passes for `ed1e8fef`'s
earlier site content, not this later documentation update.

## Retained limits and evidence

The E62 MAC/HID/trace failures retain their original verdicts and sources. Its
matrix did not sample PM attributes, so the new control and product fix do not
retroactively produce per-case PM traces. The older E59 intermittent encrypted
import failure's cause remains unproven. Runtime PM policy does not implement
USB/IP remote wakeup, system suspend, resistance to later administrator policy
changes, or cancellation of a permanently blocked sysfs/kernel callback. Public
legacy async callers retain the previously documented broader reconciliation
limitation at other effectful await points.

The main archive contains 4193 exact members/46,982,969 raw bytes;
gzip 9,525,575 bytes, SHA256
`12cfd2940d1844b1d2b14bf82db3f85e1a1a3a2ee2b7a4668ba0083ae3095edb`.
Every member is read back against original bytes/length/SHA. All 63 actual private-key
files and signed capability values are checked for exclusion. No disks, firmware,
private credentials, packages or executables are published. This integrity check
is not a separate security audit or another USB execution.

ARM virtual CDC data, full ARM GUI actions/faults, real devices/boards G1/G2,
storage/ISO, performance, Windows and the complete Phase0–7 objective remain
unfinished. Emulated timings are not native hardware performance measurements.

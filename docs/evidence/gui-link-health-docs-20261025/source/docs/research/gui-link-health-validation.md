# Native GUI same-connection RTT and fault presentation

2026-10-25. The native GUI now keeps expanded health details, keyboard focus and
metadata scroll position while real link samples update. It labels ended and
previous-epoch observations as retained history, and a stored RTT as recorded
rather than current responsiveness. This extends [E50 actions](gui-native-actions-validation.md)
and the [GUI preview](native-gui-validation.md). Contract:
[design](gui-link-health-design.md). Full Phase5/Phase0–7 remains incomplete.

## Actual defect and fix

Clean `80b189badcc901eae12489c214dfdac572f7af5a` ran the original GUI ELF
`a8b897c0…` in a real UID1000 WebKit process. For the same actual HID session,
the rendered reply sequence advanced1→3 and RTT1423→1903µs, while both disclosure
open and focused changed true→false. The run correctly failed before fault arm.
Both guest exits were0, no forced termination occurred and all six guards were
true; these facts do not change its FAIL into a pass. Its failure cleanup used
cooperative process stopping, not successful WM_DELETE_WINDOW evidence.

Product fix `a29b4e2c619b7d7a1466886a84d2ae87a963f5cc` captures only the current
DOM's disclosure/scroll state before replacement, keyed by profile, instance,
session and connection. Existing finite focus keys restore the same summary or
metadata focus. No accumulating map or retained health cache is introduced.
Raw-v1 remains RTT unavailable. Typed availability, phase, probe and end fields
stay distinct from USB operation/cleanup. An intermediate IPv4 run at this source
passes; final tests below add scroll/profile isolation and historical-text checks.

## Final source, software and binaries

Every final software/QEMU check uses clean
`c4f72f6e02120487077733a4c6eb046094db3248`. The
[public originals](../evidence/gui-link-health-20261025/README.md) retain the
negative, intermediate PASS, final gates, original regressions and methods.

| Check | Actual result |
|---|---|
| Full root `scripts/check.sh` |956 passed:420 default +516 all-features +20 vendor;0 failed/ignored; formatting, Clippy, release and rustdoc passed|
| Independent `scripts/check-gui.sh` |11 passed:5 bridge +6 GLib;0 failed/ignored; all GUI gate steps passed|
| New GUI v2 IPv4 and IPv6 pairs |Both PASS;each390 source inputs,6 guards true,2 actual guest exits0 and no forced termination|
| Original Native v2 IPv4 pair |PASS;238 source inputs,4 guards true,2 actual guest exits0, original768MiB default, no forced termination|
| Original GUI action IPv4 and IPv6 pairs |Both PASS;each378 inputs/6 guards true/2 guest exits0;each retains264 original checks,3 HID/FTDI rounds,12 native GUI processes and full root proofs|
| Original single-guest GUI |PASS;372 inputs,4 guards true,guest0/no forced;70 decoded guest files/385543 bytes;original denial/retry/hotplug/preferences/geometry preserved|

These are six final QEMU invocations,11 actual guest exits0. They include31 real
GUI processes:30 normal child exits0 and the old single-guest case's correctly
unresolved child exit1 after local joins. Original regression executions are not
new USB types or independent compatibility cases. Earlier development gate output
and the intermediate run remain separate from the final gate attribution.

| Actual release ELF | Bytes | SHA256 |
|---|---:|---|
|all-features rustusb|20284624|`5ed8d381c21958cf29269a38dde528c867963420fe0a7903f20784168619cc6a`|
|all-features helper|7525376|`83c53d522d5c89a9b3384b23c17242aa3e197217fc785d62706b520c1e1473e5`|
|native GUI|16996192|`c0b141c647b0d9b79d5126ec007fe0ecff73575a7bdf829975b22af62f70e95f`|

CLI/helper bytes remain those of E50. GUI bytes include the new embedded local
JavaScript; no Rust core, USB engine, privilege or dependency change is introduced.
Root and GUI source/head/worktree and selected binaries remained frozen until all
actual handles ended. Actual native PID/start/UID/capabilities/ELF and driver
receipts are retained. The six new GUI processes have UID1000, zero inherited/
permitted/effective/ambient capabilities and no observed `/dev/bus/usb` handle;
the operator cannot read the service private key. Both private guests use explicit
4096MiB, Linux6.8.0-106 x86_64, no host USB passthrough or host network bridge.

## What the final GUI pairs prove

Each pair composes the original Native framed-v2 experiment, retaining its exact
three directional flow blackholes,1000/3000ms probe policy, actual tuple binding,
15-second terminal-observation window, byte-exact packet audit and lifecycle
verifier. Neither verifier nor deadline is weakened. Seven actual HID/FTDI rounds,
the same persistent serial sibling, fresh Catalog during faults, fresh HID reuse
and exact original root/driver/VHCI proof remain authoritative. Each pair's packet
audit returns `PACKET_AUDIT_PASS`.

For each direction, one real client GUI opens the exact original HID session:

- Actual rendered metadata matches the agent's instance/session/data-connection
  identity. Displayed RTT equals that same rendered metadata's real sample; the
  GUI's string counter representation is explicitly matched to original integers.
  This is enqueue-to-library-reply time, including scheduling/queueing, not wire
  RTT, USB completion latency or a performance benchmark.
- Actual reply sequences advance while disclosure open, summary keyboard focus
  and metadata scrollTop40 remain intact. The first phase switches to the actual
  serial profile and back: both new pages have closed disclosures and the exact
  respective session sets; old expansion does not migrate across profiles.
- The same GUI process stays alive while the original private valve discards only
  the target real flow. The later view has owner-ended history, ended phase and
  actual typed cause. Recorded RTT explicitly says it is not a current
  responsiveness result. Independent session state shows verified cleanup.
- Five final client samples record `probe_timeout`/operation `timeout`; IPv6 c2s
  actually records `io`/operation `io`, while its server records the timeout.
  These distinct results are preserved. The original assertion requires at least
  one actual endpoint timeout per phase, plus exact two-sided cleanup. No I/O
  result is relabeled as timeout or clean EOF.
- All six GUI windows receive actual advertised `WM_DELETE_WINDOW` with a real
  X-server timestamp, then actually exit0 and join local work. Original sibling
  I/O and fresh reuse continue afterward. GUI close does not stop the service job.

![Actual native GUI after a targeted QEMU data-link timeout, with retained RTT history and independently verified cleanup](../assets/gui-link-qemu.png)

Recorded final IPv4 c2s native capture,1180×760, exact original WebDriver PNG.
It was visually inspected alongside the IPv6 capture. The shown microseconds are
one retained sample, not a benchmark; the synthetic keyboard is not hardware
compatibility evidence.

## Remaining scope

The GUI does not inject snapshots, RTT/PONGs, Start requests or USB capabilities
in this scenario; its profile/read commands use the existing authenticated IPC.
Read-only GUI evidence does not replace E50's separately confirmed Start/Stop.
Copied GTK/WebKit/Mesa runtime is not clean Ubuntu package/install qualification.

Automatic sharing rules, full GUI/accessibility acceptance, `.deb`/systemd,
configuration/install/upgrade/uninstall/reinstall/reboot lifecycle, real G1/G2,
complete G3, broader performance/device compatibility and Windows USB remain
required. Earlier failed runs retain their original verdict/source. Publication
and four-platform CI receipts will be recorded separately from these local/QEMU
facts; this report does not declare the full project complete.

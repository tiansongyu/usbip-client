# GUI-driven Native actions and real window-close evidence

2026-10-25. Confirmed GUI actions now redirect the fixed QEMU keyboard and FTDI
devices through the existing Native application service on both IPv4 and IPv6.
This extends the [native preview](native-gui-validation.md); full Phase5 and the
original Phase0–7 objective remain incomplete. Contract:
[test design](gui-native-actions-design.md). Reproduce with the
[paired runner](../../scripts/qemu_gui_actions/README.md).

## Final source and checks

Every final run used clean `8e8d50d9b37dfd5beae9b14f925fa8dfdcb60678`.
These are test/fixture changes; the product sources and actual release ELFs remain
the same as the final E49 preview. Original source snapshots, commands, raw
results, failed attempts and archive method are in the
[public evidence](../evidence/gui-native-actions-20261025/README.md).

| Actual check | Result |
|---|---|
| Root `scripts/check.sh` |956 passed:420 default,516 all-features,20 vendored nusb;0 failed/ignored; full formatting, Clippy, release and rustdoc gates passed|
| Independent `scripts/check-gui.sh` |11 passed:5 bridge and6 GLib;0 failed/ignored; full GUI gate passed|
| GUI action pair, IPv4 |PASS;2 actual guest exits0, no forced termination;378 source inputs and all6 guards true|
| GUI action pair, IPv6 |PASS;2 actual guest exits0, no forced termination;378 source inputs and all6 guards true|
| Original Native agent pair, IPv4 |PASS;2 actual guest exits0, no forced termination;229 source inputs,4 guards true; original768MiB default retained|
| Original single-guest GUI |PASS;guest0, no forced termination;372 source inputs,4 guards true;70 decoded guest files/385784 bytes|

The two new pairs each preserve all264 original Native checks, two agent epochs,
three HID/FTDI I/O rounds, root terminal identity proofs, exact driver/VHCI
restoration and encrypted-only external transport checks. The original agent
regression also preserves its264 checks and three I/O rounds. These are repeated
scenario executions, not additional distinct USB-device compatibility claims.
The old single-guest test retains actual denial/original retry, hotplug, saved
preferences and geometry checks; its unresolved GUI child correctly exits1 while
the independent agent exits0.

| Actual release ELF | Bytes | SHA256 |
|---|---:|---|
|all-features rustusb|20284624|`5ed8d381c21958cf29269a38dde528c867963420fe0a7903f20784168619cc6a`|
|all-features helper|7525376|`83c53d522d5c89a9b3384b23c17242aa3e197217fc785d62706b520c1e1473e5`|
|native GUI|16994912|`a8b897c0151c9ebfe96a1d3be9e8f42fc5942ff42f879608165c5ead7a1db8dd`|

The source/head/worktree/ELFs stayed frozen until every actual check process ended.
The GUI pairs additionally guard the WebKit driver ELF. Copied runtime ELF hashes,
actual GUI/driver PID/start identity/UID/capabilities, generated guest scripts and
actual waits are retained. The paired guests use Linux6.8.0-106 x86_64 and4096MiB
each, with private QEMU NIC sockets, no host USB passthrough or host bridge.

## What the actual GUI did

Each new pair launches twelve separate native WebKit/Tauri processes under
UID1000 with zero inheritable/permitted/effective/ambient capabilities. The actual
service runs under UID2000. The operator cannot read either service private key;
GUI process FD observations contain no USB device handle.

- Six starts use the real configured profile selector, Start button and
  default-cancel confirmation. Normal GUI acknowledgement contains the admitted
  job ID. Protected state before/after shows exactly one new UID1000 request for
  that profile. CLI RetryStart reads back that exact request, reports replayed,
  and leaves requests unchanged; it never substitutes for a GUI Start.
- One incoming HID session and four jobs stop through the actual Connections
  controls and default-cancel confirmation. The verifier independently matches
  the exact session/job identities to original service receipts. The GUI reports
  owner-present Stop acceptance; original terminal/root proofs and restored
  driver/VHCI baselines establish subsequent release.
- The client opens a current authenticated remote catalog containing both exact
  device keys. A previously selected local eligibility filter stays hidden and
  does not remove remote rows. Real no-match search, clearing, selection and
  descriptor detail pass. The raw remote screenshot was visually inspected.
- Raw-v1 link health has no RTT sample. The actual expanded Connections detail
  displays **RTT unavailable**. No heartbeat RTT or framed-v2 GUI acceptance is
  inferred from this run.

The test-only original Xlib client sends the advertised ICCCM `WM_DELETE_WINDOW`
protocol to the sole mapped window of the recorded GUI PID, using an actual X
server timestamp. It does not destroy the window or replace close with a process
signal. All24 native processes across the two final pairs actually exit0 and
report their local worker joined. After every Start window closes, the same
agent/job identity remains Running without a stop request. The original subsequent
kernel enumeration and actual HID/serial I/O prove continued service work beyond
the GUI's lifetime. GUI metadata alone is not that proof.

## Failed attempts and corrections

Original results remain unchanged:

1. Clean `3f683f2d` failed before GUI launch: jq treats `$label` as a reserved
   keyword in the fixture's session JSON command. Use `session_label`; a host
   preflight executes the actual extracted jq command.
2. Clean `86eac9ca` and `901a6dac` failed after GUI session creation but before
   Start. A fixture result predicate applied `.error?` to boolean JavaScript
   results, producing an empty jq stream/exit4. Typed result validation and actual
   true/false/null/string/object/error-object preflights fix this.
3. The initial diagnosis wrongly attributed exit4 to native close and claimed a
   Start acknowledgement. Raw artifacts contain no admitted/close receipt and
   show zero jobs/requests. That inference is explicitly retracted in the work
   log. Added C close diagnostics had not run and are not product-fix evidence.
4. Clean `ae1e3fb5` then passed the first complete IPv4 action pair. Final
   `8e8d50d9` independently reruns both families with stricter Stop identity,
   driver identity, raw-link health and failed-infrastructure wait checks.

All three failed pairs have actual guest exits0, no forced termination and all
six guards true; the guest failure markers still make their overall result FAIL.
An archive preflight also rejected its own source's literal private-key marker.
The method and observation are retained; final checking rejects actual PEM header
lines and key filenames, then verifies every archived member's bytes/SHA.

## Scope still open

No new product USB engine, RPC privilege, device type or hardware evidence is
introduced. The GUI uses the existing shared frontend/service and fixed Native
profiles; selecting a row does not change the profile's device set. This copied
GTK/WebKit runtime is not a clean Ubuntu installation or package test.

Framed-v2 GUI live RTT, automatic sharing rules, complete GUI/accessibility
acceptance, `.deb`/systemd deployment, configuration/install/upgrade/uninstall/
reinstall/reboot lifecycle, real hardware G1/G2, complete G3, broader performance
and Windows USB/driver work remain required. CI/Pages publication receipts are
recorded separately; local/QEMU success is not deployment evidence.

## Publication

Content `8fd0094b31ace722d15a32841200df47036bcb45` was pushed through the authorized
SSH remote. [Core CI](https://github.com/tiansongyu/usbip-client/actions/runs/40145511838)
and [independent GUI CI](https://github.com/tiansongyu/usbip-client/actions/runs/40145511737)
each completed all four native Ubuntu22.04/24.04 × x86_64/aarch64 jobs successfully,
including their actual architecture/full-gate steps.
[Pages](https://github.com/tiansongyu/usbip-client/actions/runs/40145511774) check
and deployment succeeded. Seven canonical HTTPS resources returned200 with exact
Git bytes; source/ELF/global skill integrity checks passed. Original API/step/HTTP
receipts and methods are [preserved](../evidence/gui-native-actions-publication-8fd0094b-20261025/README.md).
These status receipts do not supply CI logs/ELFs or a new per-platform test total.
QEMU evidence remains locked to8e8d50d9; subsequent receipt edits are documentation.

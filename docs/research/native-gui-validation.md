# Native GUI preview: software and QEMU evidence

2026-10-25. A real Linux Tauri/WebKit preview now exists; Phase5 and the original
Phase0–7 objective remain incomplete. Contract: [design](native-gui-design.md).
Operator instructions: [GUI guide](../gui.md). Public originals:
[evidence manifest](../evidence/native-gui-20261025/manifest.json),
[archive receipt](../evidence/native-gui-20261025/receipt.json).

## Implementation

`rustusb-frontend` extracts the existing state/worker/checked preferences without
USB logic changes. The TUI retains its run/Options/Settings API through reexports.
The independent `apps/rustusb-gui` workspace uses the same worker and the original
service/discovery libraries. It has six real pages, explicit fixed-profile and
exact-stop confirmations, checked preferences and original-UUID retry. It owns
no USB resources. The Rust admission slot persists through actual completion;
renderer Promise loss/reload does not return it. A pending sequence is projected
separately from the worker's acknowledged sequence.

Three commands are enumerated with AppManifest and restricted to the packaged
local main WebView, with Rust origin/label checks, CSP and denied popup/remote
navigation/frames. No shell/fs/http/process plugins are installed. Metadata is
rendered as text; controls/bidi text is filtered and numeric values become strings.
One nonwaiting projection permit limits application serialization; a complete
512KiB view or 64KiB detail is returned or explicitly refused. This does not bound
Tauri's earlier input parsing or later buffering. One latest shared watch supplies
observations; the bridge creates no new network/USB/observer owner.

The runtime/signal source exists before blocking startup reads. Accepted reads
and mutations retain actual task ownership. Close/INT/TERM stop and join local
work before exit; unknown Start is reported separately. SIGKILL, framework crashes
and permanent kernel blocking are outside cooperative cleanup guarantees.

## Exact checks and build identities

| Check | Clean revision | Actual outcome |
|---|---|---|
| Root ten-command `scripts/check.sh` | b15dd113 |420 default +516 all-features +20 vendored nusb =956 passed,0 failed/ignored; fmt/Clippy/release/rustdoc passed|
| Initial GUI full gate | b15dd113 |10 passed; preserved earlier developer failures|
| Pending-state GUI full gate |7c4eb3cd|11 passed:5 bridge +6 GLib; all GUI gate commands0|
| Viewport GUI full gate |08ac67a1|11 passed,0 failed/ignored; fmt/Clippy/release/rustdoc/JS syntax passed|
| Shared-worker original TUI QEMU |72fe888c|guest0, no forced termination, four guards true; four actual PTYs (three child0, one expected unresolved child1), original six observer points and three fast refusals preserved|
| First functional native GUI QEMU |f37d95d9|parent/guest PASS,66 guest files/378653B; subsequent visual QA found a separate long-detail layout bug|
| Native GUI QEMU with geometry |08ac67a1|parent/guest PASS,68 guest files/386369B; actual guest0, no forced termination, all four guards true|
| Final GUI full gate, local-only eligibility |03621a06|11 passed,0 failed/ignored; all GUI gate commands0|
| Final native GUI QEMU, filter and geometry |03621a06|parent/guest PASS,70 guest files/364143B; guest0, no forced termination, all four guards true|

Root core inputs from b15 remain unchanged through the GUI/fixture-only commits;
root tests are not falsely counted as rerun on each later revision. Repeated GUI
executions are not additional test coverage. All test runners preserve source,
head, worktree and binary guards. Final actual release ELFs:

| Binary | Bytes | SHA256 |
|---|---:|---|
|root all-features rustusb|20284624|`5ed8d381c21958cf29269a38dde528c867963420fe0a7903f20784168619cc6a`|
|root all-features helper|7525376|`83c53d522d5c89a9b3384b23c17242aa3e197217fc785d62706b520c1e1473e5`|
|native GUI|16994912|`a8b897c0151c9ebfe96a1d3be9e8f42fc5942ff42f879608165c5ead7a1db8dd`|

The helper is copied but never started in these native GUI cases. Final QEMU
uses a disposable x86_64 Linux6.8.0-106 guest, no NIC/host USB passthrough and no
USB/IP kernel modules. Actual UID1000/zero effective/permitted/inheritable/ambient
capabilities and process/ELF identities are captured. WebKitWebDriver reports
Wry0.57.1, alongside the locked Tauri2.12.5/runtime-wry2.12.3 graph. The host's
GTK/WebKit/Mesa/font runtime is copied with ELF hashes into the ephemeral rootfs;
this is not a clean Ubuntu package-installation test.

A real root-provisioned agent runs under UID2000, authorizing UID1000 Read/Audit
only. Strict extra-field input is rejected before an agent operation; default
Cancel leaves no state file. QMP keyboard add/remove/readd changes the actual GUI
inventory/generation and selection detail. Start and original RetryStart across
renderer reload cause exactly two real permission-denied audit records, zero
admitted requests/jobs/sessions and the identical retained UUID. Checked preferences
are actually saved1000:600:1. GUI SIGTERM actually waits child1 for unresolved Start,
prints the identity and local-worker join; the independent agent still answers
Profiles, then its own cooperative stop waits0. This is not active-job continuity.

Final wide1180×760 geometry has workspace/footer bottom760 and main bottom724;
narrow800×600 has workspace/footer bottom600 and main bottom564. Native details
switching and keyboard-focusable metadata are checked. Screenshots were visually
inspected; [wide capture](../assets/gui-qemu.png) is a recorded synthetic-device
native window, not a mock, live web app, USB import or hardware compatibility proof.

## Failures retained

The archive keeps original failures and the exact source/commands used:

- Developer native startup evaluated spawn_blocking outside a runtime and panicked;
  fixed to runtime.spawn_blocking. A Serde unit-variant accepted extra fields;
  fixed with empty struct variants. First GUI gate found missing test crate docs.
- Host fixture errors: an overbroad date replacement changed a driver path; `/tmp`
  preferences correctly failed trust checks; an incorrect MiniBrowser capability
  failed matching. Correct official driver mapping omits browserName. Host raw
  USB inventory/screenshots remain private and are excluded from public evidence.
- Native QEMU b15 failed on BusyBox setpriv; its old parent did not serialize an
  already-ended actual wait (None), so no guest0 is reconstructed.7c failed because
  a substring replacement renamed snapshot. b1eb failed on missing dlopen GLES;
  its failed-session cleanup did not yet observe the GUI child wait. All remain FAIL.
-9b guest completed but parent incorrectly required a trailing slash on the valid
  `tauri://localhost` URL. Candidate post-failure diagnosis is labeled separately;
  the original FAIL is unchanged.72 failed when the narrow test navigated before
  real Save acknowledgement; it then queried an absent pane. The fixture now waits
  actual DOM completion. Later original runs provide the PASS evidence.
- First TUI parent lacked pyte although the guest completed. A nonexistent venv
  command then exited127 without launching a VM. Correct PYTHONPATH to the existing
  test-only pyte0.8.2/wcwidth0.2.13 install produced the final actual PASS.
- Long metadata made the workspace/footer937px in a760px native viewport. The
  genuine host geometry negative is retained without host USB metadata. The
  min-height fix and new QEMU geometry assertions prove the correction.

## GLib and dependency boundary

The active [RUSTSEC-2024-0429](https://rustsec.org/advisories/RUSTSEC-2024-0429.html)
is a mutable C out-pointer bug, not the initially suspected lifetime escape.
The two-line glib0.18.5 backport equals upstream
[05dff0ee](https://github.com/gtk-rs/gtk-rs-core/commit/05dff0ee696f9bcd8617cd48c4b812d046d440cb).
All121 published source files/license are retained; only variant_iter.rs differs.
Its full SHA is recorded in [vendor provenance](../../apps/rustusb-gui/vendor/README.md).
No version spoofing, advisory suppression or execution of affected old methods.

The component archive has500 original source/probe/method/log files,6900945B;
its own [manifest](../evidence/native-gui-20261025/glib-manifest.json) and
[receipt](../evidence/native-gui-20261025/glib-receipt.json) verify them. Both versions
reject seven lifetime escapes each, disproving the initial hypothesis; normal use
compiles. Patched custom6 and upstream iterator11 tests pass in debug/release;
caller fmt/Clippy pass after preserved fixture lint/format failures. Final GUI
integration executes the six tests against the actual patched dependency graph.
Unchanged upstream GLib emits34 newer-Rust lifetime-syntax dependency warnings;
they were not suppressed. Version scanners may still flag0.18.5. This is not a
whole-GTK vulnerability or release-license audit. Root inventory222 and separate
GUI Cargo inventory475 include all-platform metadata, not475 Linux runtime libs.

## Final local-filter correction

Post-publication code review found that a selected local eligibility filter could
hide remote entries. The predicate is now explicitly local-only; Remote hides
that control while preserving its local choice. Final clean03621a06 repeats the
full GUI gate and native QEMU flow, with a real page-navigation assertion for the
hidden remote control/local choice plus all existing geometry/denial/join checks.
This still does not validate a nonempty live remote client catalog.

[Supplemental originals](../evidence/native-gui-filter-03621a06-20261025/manifest.json)
and [receipt](../evidence/native-gui-filter-03621a06-20261025/receipt.json) retain the
new source, commands, logs and summaries separately. The README/site image now
uses this final run's actual unedited native capture. Previous08 screenshots,
CEC2 ELF and original results remain in the historical archive, not reassigned.

## Open acceptance

GUI-driven successful Start/Stop, live remote client catalog/RTT, active-job
continuity through window close, signal duplication/framework failure paths and
full GUI accessibility/keyboard coverage still need dedicated cases. Automatic
sharing rules, `.deb`, systemd, configuration/install/upgrade/uninstall/reboot and
four-platform native GUI qualification remain Phase5 requirements. GUI CI is a
separate four-platform software gate; compilation is not native I/O evidence.
Hardware G1/G2, full G3, broader compatibility/performance and Windows remain open.


## Publication receipt

Final content3aafa2c0 was pushed by normal SSH with exact remote main and a clean
worktree. Primary API confirms root software
[40142928056](https://github.com/tiansongyu/usbip-client/actions/runs/40142928056)
and native GUI
[40142928072](https://github.com/tiansongyu/usbip-client/actions/runs/40142928072):
four Ubuntu22.04/24.04 × x86_64/aarch64 jobs each completed/success, including
actual architecture and full gate steps. Pages
[40142928094](https://github.com/tiansongyu/usbip-client/actions/runs/40142928094)
check121018248988/deploy121018320543 completed/success. Seven canonical HTTPS
bodies, including the native capture, returned200 and exactly matched the Git
content.222 core inputs,138 GUI non-README inputs, all release ELFs, eight site
files and four global readme-site files/discovery symlink were preserved.

[Publication originals and methods](../evidence/native-gui-publication-3aafa2c0-20261025/manifest.json)
and [receipt](../evidence/native-gui-publication-3aafa2c0-20261025/receipt.json)
retain the earlier776 and final content observations independently. API step
success is not a per-platform test recount or fetched CI ELF/log archive. The
native QEMU and open acceptance boundaries above remain unchanged. This receipt
only adds documentation; it is not a new product build or hardware result.


## Subsequent confirmed-action evidence

The later [E50 report](gui-native-actions-validation.md) independently validates
IPv4/IPv6 GUI Start/Stop, a nonempty remote catalog and continued original Native
HID/FTDI I/O after actual WM_DELETE_WINDOW closes. It reruns both software gates
and the original agent/single-GUI fixtures on clean8e8d50d9. Earlier E49 results,
negative-only scope and original failures above remain attributed to their own
runs; the E49 screenshot is not relabeled as a positive import capture.


Later [E51 same-link evidence](gui-link-health-validation.md) fixes actual refresh disclosure/focus loss and validates real v2 RTT/history in both families. E49 originals and their negative-only scope stay unchanged.

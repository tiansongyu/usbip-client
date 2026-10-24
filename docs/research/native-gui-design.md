# Native GUI implementation contract

Status: implementation draft, 2026-10-25. Continues the full Phase 5 objective;
not an accepted release architecture or completed installation/hardware gate.
Research and source/version evidence: [GUI research](gui-installation-research.md).

Use a separate `apps/rustusb-gui` workspace with Tauri2.12.5/tauri-build2.7.4,
static local HTML/CSS/JavaScript, existing core/service/discovery path crates and
no shell/fs/http/process plugins. Root Native workspace gates remain intact;
the GUI has its own fmt/Clippy/test/release/rustdoc gate and dependency inventory.
Pin an upstream-compatible GLib0.18.5 mutable-out-pointer fix (PR1343/05dff0ee),
preserving licenses/version/API, with original source checksum, exact patch and
actual tests. Do not execute affected unpatched methods or call it a full GTK
security audit. A separate component task researches/tests this fix as required
by the technical-design skill; project conventions keep its evidence here.

The ordinary-user process reads only a protected public Manifest passed at
startup. It never starts the agent, loads private TLS keys, calls helper
mutations or owns USB sessions. All actions use original authenticated IPC;
local/remote observation uses the existing shared libraries. Fixed-profile Start
means the entire configured profile; browsing a device is not a new selection
of exported/imported keys. Confirmations default Cancel. A lost/denied Start
keeps its original UUID in Rust, independent of renderer reload; retries use
only RetryStart. Accepted stop is never a release proof. Existing storage and
generation restrictions remain authoritative.

Extract the existing tested TUI worker/state/checked preferences into a headless
`rustusb-frontend` crate; TUI and GUI reuse that same implementation. Terminal
rendering stays in rustusb-tui. Preserve existing public TUI run/Options/Settings
API through reexports. The GUI bridge validates the finite command shape and
refresh range before dispatch. This also brings persistent checked preferences
into the first GUI implementation, without granting administrator policy.

One Rust worker retains actual local/remote observer owners and accepted work.
Action admission is nonwaiting, one accepted action through actual completion;
renderer Promise loss cannot return its slot. Keep the existing single latest metadata watch; project one capped response under
a nonwaiting serialization permit, without another observer task. Keep
independent freshness and string-form counters/identities. GUI polling
returns a complete capped JSON projection or explicit unavailable/oversize/Busy,
and creates no observer or network owner. Detail reads are bounded metadata;
no renderer-supplied path, endpoint, credential or arbitrary RPC is accepted.
Tauri parses input before application checks: these bounds cover bridge work and
state, not arbitrary framework/renderer allocations. Use settlement-driven
frontend polling, not unbounded push channels.

Enumerate custom commands with AppManifest, allow only the local `main` WebView,
check its actual label/origin in Rust, use restrictive CSP, deny navigation,
popups and frames. Render device text with textContent, never HTML injection.
On close/INT/TERM, prevent native exit, signal sticky stop, join GUI-owned work
and monitors, retain/report unresolved identity, then allow exit. Do not stop
agent jobs or fake cancellation of blocking OS work.

## Visual and interaction plan

Audience: operators managing explicitly provisioned USB profiles. Six pages:
Local devices, Remote devices, Connections, Activity, System, Settings. The first
screen is the actual device inventory. Keep one persistent profile control and
clear action/status area, a compact device table and a selection detail panel.
Eligibility filtering applies only to local export candidates; hide its control
on Remote and retain its local choice for return navigation.
Use native window chrome, real empty/error states, visible keyboard focus and
text state labels. Profile starts and exact-session/job stops require explicit
default-cancel dialogs. Selection follows stable identity plus generation.

Use the existing slate/cyan RustUSB direction: canvas #0f172a, panel #18243a,
line #334155, text #f1f5f9, muted #b1bfd1, focus #67e8f9; danger #fda4af and
success #86efac are accompanied by text. Use local system sans (Noto Sans,
DejaVu Sans), 14px body/20px page title, 8px spacing rhythm and restrained 6px
controls. A 190px navigation rail, flexible main list and 300px details pane
collapse to a list/details toggle on narrow windows. The grid workspace must
allow its main track to shrink (`min-height:0`); long metadata scrolls inside the
viewport with the section status footer still visible. Metadata preformatted
areas are keyboard-focusable. Verify actual native element geometry, not only
screenshot dimensions. No marketing hero, fake
live metrics, web fonts, card grid or decorative motion. The skill search's
generic landing-page suggestions are inapplicable; retain its keyboard/contrast
and dense-console guidance. This is an application, not the showcase website.

## Verification and remaining scope

Run real native WebView/IPC checks under Xvfb and disposable QEMU as available;
mocked Chrome alone is insufficient. Test actual UID/capabilities/ELF, source
and build provenance, read-only real-agent refusal, original-UUID retry, actual
close joins and active service-job continuity. Preserve original failures.
Use software tests for queue bounds, projection/text bounds and lifecycle.

The first implementation delivers shared-service lists, manual actions,
search/details, state/available link health and feedback. Authorized agent-owned automatic-sharing rules, .deb/systemd,
install/upgrade/uninstall/reboot and four-platform native GUI acceptance remain
required Phase 5 work; this draft does not redefine them away. Hardware/full G3,
performance/expanded compatibility and Windows remain original open stages.

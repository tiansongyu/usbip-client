# GUI and installation: research and draft boundaries

Status: **draft research**, 2026-10-25. No GUI, new dependency, accepted architecture,
package build or installation has been implemented by this work. The full user
Phase0–7 goal remains active. The [G4 software/QEMU audit](tui-hotplug-permission-validation.md#g4-requirement-audit)
is the current terminal baseline; hardware/full G3 and release gates remain open.

Two independent read-only component tasks were delegated as explicitly required
by the writing-technical-design skill. The project convention keeps their
research here and in evidence, without creating unrelated global/component
skills or automatically accepting an ADR. [Integrity review](../evidence/gui-phase5-research-20261025/review.json)
verified all 314 original payload files; selected digests/API maps, public metadata,
source indexes, hashes and Tauri license texts are committed. Full public source
snapshots remain at the original directories enumerated by that review and are
not included in this repository evidence folder. Original retrieval failures
are retained, including two wrong license-path 404s and Ubuntu-site 500/timeouts.
No repository implementation, system installation/update, host USB access or
upstream script execution occurred during research.

Subsequent implementation is recorded separately in the [native GUI contract](native-gui-design.md) and [actual validation](native-gui-validation.md); this historical research statement is not the current implementation status.

## Verified candidate facts

- Tauri **2.12.5**, released 2026-10-21 at
  [`993038c49185307dfc895ab7657d77ca0d5ed7c7`](https://github.com/tauri-apps/tauri/releases/tag/tauri-v2.12.5),
  uses Rust 1.90/edition 2024, Apache-2.0 OR MIT. Published source checksum/VCS and
  three critical source files matched that revision. Supporting versions are
  tauri-build 2.7.4, runtime-wry 2.12.3 and JS API 2.12.4. The release lock contains
  wry 0.57.0/tao 0.37.0; independently current 0.57.1/0.37.2 do not establish what a
  future RustUSB lock will resolve. [Tauri digest](../evidence/gui-phase5-research-20261025/tauri/DIGEST.md).
- Linux Tauri directly depends on GTK even with default features disabled. Its
  runtime requests GTK>=3.24/WebKitGTK4.1>=2.40. Current official Launchpad
  Published records cover amd64/arm64 updated Jammy and Noble; Jammy's original
  WebKit 2.36 does not satisfy this minimum. Noble's GTK runtime package has a t64
  name. These are package/source facts, not a native build or installation test.
  [Runtime digest and source index](../evidence/gui-phase5-research-20261025/linux/digest.md).
- App commands are not automatically capability-protected without an app ACL
  manifest. Explicitly enumerate commands with AppManifest::commands, select a
  local capability and fixed WebView label, validate arguments and retain the
  real agent UID/profile/action authorization. Window/WebView matches are
  alternatives. CSP, navigation, popup and frame restrictions remain necessary;
  Linux iframe origin distinctions are limited.
- Async command dispatch discards its spawned JoinHandle. Promise timeout or
  renderer loss cannot stand for actual work cancellation. ExitRequested's
  synchronous callback must prevent exit while an independently retained local
  supervisor stops and joins GUI-owned observers/accepted work. Agent jobs stay
  owned by the existing service. [Exact API/source map](../evidence/gui-phase5-research-20261025/tauri/API.md).
- Channel ordering does not supply bounded capacity or consumer acknowledgements;
  pending bodies/messages use uncapped collections. Prefer one capped latest
  Rust projection and settlement-driven pull polling. Tauri parses JSON before
  application admission, so bridge limits cannot be advertised as a hard bound
  on arbitrary framework/renderer allocations.
- The inspected upstream lock contains glib 0.18.5. The active
  [RUSTSEC-2024-0429](https://rustsec.org/advisories/RUSTSEC-2024-0429.html)
  concerns VariantStrIter methods in 0.15..<0.20, patched>=0.20. Actual reachability
  and compatible remediation remain unresolved. Investigate the upstream fix,
  backport/API compatibility and relevant call paths before introducing a GUI
  dependency lock. The separate GTK unmaintained advisory 0415 is withdrawn;
  advisory 0413 concerns ATK, also withdrawn. Neither implies a blanket current
  unsafe-GTK conclusion. No vulnerable GUI dependency was added to RustUSB.

## Proposed implementation boundaries

Keep the ordinary-user GUI on existing public Manifest/IPC, DeviceMonitor and
CatalogMonitor APIs; it loads no private TLS keys and calls no helper mutations.
Rust retains pending Start UUIDs independently of renderer reloads and permits
only exact RetryStart until resolved. Stop acceptance is not release proof.
Local device identity includes observational generation; a browsed row is not
an authorization grant. Agent/profile, local/remote and health samples remain
independent and visibly stale on failure.

An isolated opt-in GUI workspace outside existing `crates/*` is a candidate for
keeping GTK out of default Native builds. Changing default-members alone does
not exclude a package from `--workspace`. Any separate GUI workspace needs its
own mandatory fmt/Clippy/test/release/docs and native CI; isolation must not
silently remove GUI verification. No package layout has been accepted yet.

The GUI must still deliver the original dual-end lists, connect/disconnect,
search, latency/state/details, automatic-sharing rules, errors and access
control. Automatic rules need an authorized agent-owned lifecycle and the
existing storage/generation restrictions; a renderer timer or saved preference
cannot become device authority. Detailed rule/schema/API design is outstanding.

## Packaging and next verification

The repository currently has no packaging directory or GUI implementation.
Prepare separate broker/helper/operator deployment and configuration ownership.
Tauri's Debian bundler alone does not prove safe install/upgrade/remove/purge,
restart or rollback. Native dependencies and glibc require building each
architecture on the oldest supported updated base, inspecting generated ELF and
package metadata, then testing both Ubuntu releases. [Official Debian guidance](https://v2.tauri.app/distribute/debian/).

Headless native testing can use the documented external tauri-driver 2.1.0,
WebKitWebDriver and Xvfb, without shipping a test plugin. Current docs also offer
embedded WDIO testing. Xvfb/native IPC differs from a mocked Chrome renderer and
does not prove Wayland, physical input or USB/hardware authorization.
[Official WebDriver CI](https://v2.tauri.app/develop/tests/webdriver/ci/).

Next work is to resolve the GLib compatibility finding, probe an isolated minimal
native application and define bounded DTO/owner/exit contracts, then implement
and exercise real shared-service actions. Add reproducible Ubuntu systemd VM
installation/upgrade/uninstall tests. Do not enable broad PrivateDevices/sysfs
restrictions or SIGKILL stop escalation without verifying actual helper/broker
cleanup. Unresolved durable resource records and credentials need explicit
retention behavior. None of these Phase 5 acceptance checks has run yet.

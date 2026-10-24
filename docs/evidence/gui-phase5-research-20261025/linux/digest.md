# Linux GUI runtime and packaging digest

As of 2026-10-25; candidate research, no accepted design or product change.

## Versions and contracts

Tauri **2.12.5** (2026-10-21, commit `993038c49185307dfc895ab7657d77ca0d5ed7c7`) requires Rust **1.90**, edition2024, Apache-2.0 OR MIT. Its runtime-wry2.12.3 requests wry0.57 and tao0.37; the upstream release lock actually resolves wry0.57.0/tao0.37.0. Latest independent releases are wry**0.57.1** (`2446feadae623d6f2ac8d2136e3ccef3d7197e0e`, Apache-2.0 OR MIT) and tao**0.37.2** (`2d9b2ba15247879787878ae565e350bcc1b9f622`, Apache-2.0), both Rust1.85, released2026-10-23. No new project dependency resolution was run.

The Linux stable path is **GTK3 >=3.24**, **WebKitGTK4.1 >=2.40**, libsoup3; native minima follow enabled Cargo features and sys-crate pkg-config metadata. GTK4/WebKit6 is not the inspected default. Native GTK/WebKit have LGPL and additional component licenses; retain distro copyright/notice inventories rather than labeling the full stack MIT.

## Ubuntu and prerequisites

Official Launchpad Published records independently cover **amd64 and arm64**:

| Distribution | WebKit dev/runtime/driver updates | GTK runtime updates | glibc series |
|---|---|---|---|
|22.04|2.50.4-0ubuntu0.22.04.1|libgtk-3-0 3.24.33-1ubuntu2.2|2.35|
|24.04|2.52.6-0ubuntu0.24.04.1|libgtk-3-0t64 3.24.41-4ubuntu1.3|2.39|

Jammy Release-pocket WebKit2.36 **does not meet** current Tauri's2.40 minimum: document updated repositories. Upstream WebKit latest2.54.2 is different from Ubuntu-maintained package versions; no CVE-free claim follows.

Official development recipe: libwebkit2gtk-4.1-dev, build-essential, curl, wget, file, libxdo-dev, libssl-dev, libayatana-appindicator3-dev, librsvg2-dev; Rust plus the chosen frontend toolchain. Some are optional-feature/tooling dependencies, not all minimal runtime dependencies. Tray and linux-libxdo are optional features. Bundler emits libwebkit2gtk-4.1-0/libgtk-3-0 and optional chosen indicator dependency; generated package must be inspected for version floors, ELF transitive dependencies and Noble's t64 provider. Build each architecture on the **oldest supported updated base**, then test both releases; newer glibc linkage is not backward compatible.

## Testing and deployment boundary

External tauri-driver**2.1.0** (Rust1.90) starts WebKitWebDriver, ports4444/4445, automation environment enabled; native host defaults127.0.0.1. Official CI adds webkit2gtk-driver/Xvfb and uses xvfb-run. Current docs also offer WDIO's embedded test plugin; plain-Chrome mocked renderer mode does not exercise native IPC. Xvfb exercises a real X11 WebView, not Wayland/compositor/physical input, USB I/O or hardware authorization. Keep test-server/plugin exposure out of release configuration.

Package installation owns root-only helper/policy/credential provisioning; GUI remains operator UID and uses existing agent IPC, without keys or helper mutations. Stable broker identity and existing0711-control/0700-data contracts remain authoritative. systemd249/255 directives need separate helper/broker evaluation: PrivateDevices hides USB, ProtectKernelTunables restricts sysfs, and stop timeout can escalate to SIGKILL. Tauri packaging alone supplies no safe service lifecycle. Debian maintainer scripts must be idempotent; install/upgrade/remove/purge and rollback require disposable Ubuntu systemd tests preserving unresolved records and explicit configuration retention.

## Risks, confidence and evidence

Upstream lock includes glib0.18.5: **RUSTSEC-2024-0429** affects0.15..<0.20 VariantStrIter::{last,next,next_back,nth,nth_back}; patched>=0.20. Reachability is **unknown**, not a blanket unsafe-GTK conclusion. Before implementation, investigate upstream/backport compatibility or establish controlled reachability; no remediation is claimed. GTK's older unmaintained advisory0415 is withdrawn.

High confidence: captured source/version/package facts. Unverified: actual native build, minimum packaged ELF/dependencies, desktop/Wayland, aarch64 execution, installation lifecycle, sandbox behavior and hardware. Host inventory: Jammy x86_64, Rust1.97.1, GTK3.24.33/WebKit2.50.4 and Xvfb present; WebKitWebDriver/tauri-driver/libxdo-dev/debhelper absent. No install/update, host USB access or application execution occurred. packages.ubuntu.com500/timeouts and guessed WebKit/docs404s are preserved in receipts; Launchpad and corrected pinned Tauri paths succeeded.

Sources/URLs and original fetch failures: `source-index.json`; local command receipts: `inventory/`; closed payload hashes: `manifest.json` and `SHA256SUMS`.

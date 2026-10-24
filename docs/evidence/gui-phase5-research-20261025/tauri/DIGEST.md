# Tauri v2 component research

As of 2026-10-25. Read-only research; proposed integration, not an approved design.

## Identity

Stable Rust crate **tauri 2.12.5**, released **2026-10-21**, upstream commit **993038c49185307dfc895ab7657d77ca0d5ed7c7**; MSRV **1.90**, edition **2024**, **Apache-2.0 OR MIT**. Supporting versions at that revision: tauri-build2.7.4, runtime-wry2.12.3, JS API2.12.4. crates.io reports2.12.5 as max_stable_version;3.0.0-alpha.5 is prerelease. The published crate checksum matches registry metadata, its VCS SHA matches this commit, and selected IPC/runtime files equal upstream bytes. [Release](https://github.com/tauri-apps/tauri/releases/tag/tauri-v2.12.5), [manifest](https://github.com/tauri-apps/tauri/blob/993038c49185307dfc895ab7657d77ca0d5ed7c7/Cargo.toml).

## APIs and ownership

Use Builder::manage/setup/invoke_handler/build, State, typed async commands returning serializable Result, and App::run_return. CloseRequested::prevent_close and ExitRequested::prevent_exit permit cooperative local shutdown before AppHandle::exit. The event callback is synchronous. Async command dispatch spawns and discards its JoinHandle: a JS Promise timeout or vanished WebView does not establish cancellation. Store actual task owners and original pending Start UUIDs in Rust, outside renderer state. Join GUI observers/accepted local work before exiting; do not stop service jobs. Tokio integration uses async_runtime::set(handle) once before runtime use; keep the underlying runtime alive. Started spawn_blocking work cannot be aborted. [Pinned IPC](https://github.com/tauri-apps/tauri/blob/993038c49185307dfc895ab7657d77ca0d5ed7c7/crates/tauri/src/ipc/mod.rs), [Tokio](https://docs.rs/tokio/1.54.1/tokio/task/fn.spawn_blocking.html).

## Authorization boundary

App commands are **not automatically capability protected**: registered local commands are allowed absent an app ACL manifest. Enumerate every bridge command with AppManifest::commands, generate permissions, explicitly select one local capability and exact WebView label. Window patterns grant all their WebViews; window/webview matches are alternatives, not an intersection. Keep remote capabilities absent, block external navigation/popups and frames, configure restrictive CSP, and omit shell/fs/http/process/helper/credential commands. Capabilities do not replace Rust argument validation or agent UID/profile authorization. Linux iframe origins cannot reliably be distinguished from their containing window. [Capabilities](https://v2.tauri.app/security/capabilities/), [CSP](https://v2.tauri.app/security/csp/).

## Bounded bridge option

Prefer a pull command `gui_snapshot(last_seq)` returning `NotModified | CompleteSnapshot | ResyncRequired | Busy`. One shared local/remote observer owner updates a capped latest Rust projection; polling creates no observers. One immediate Rust admission permit, bounded DTO fields/rows/encoded bytes, and frontend polling only after the previous call settles prevent bridge work accumulation. Oversize produces explicit unavailable/resync feedback, never a partial authoritative snapshot. Keep exact operation receipts/unknown UUIDs separately. Channels offer ordering but no capacity or consumer ACK; pending large bodies and JS out-of-order messages use uncapped collections. IPC parsing precedes command validation, so this bounds bridge work/state, not arbitrary renderer/framework allocations. [Pinned Channel](https://github.com/tauri-apps/tauri/blob/993038c49185307dfc895ab7657d77ca0d5ed7c7/crates/tauri/src/ipc/channel.rs).

## Dependency isolation and limits

Linux tauri depends on GTK even with default-features=false; wry adds WebKitGTK. Keep the GUI in an opt-in separate package/workspace, outside existing `crates/*` workspace builds; only the GUI selects wry and platform features. No GUI dependency in Native CLI/core. Website release index and prerequisites lag current release/MSRV/Windows7 policy. Advisory channel-ID randomness also differs from the current per-WebView sequential-ID source; do not claim randomness. Source/API confidence high; no build, install, runtime, performance, full dependency audit, hardware or Phase5 acceptance evidence. Details and exact source map: API.md; retrievals: method/*fetch.json; integrity: manifest.json and SHA256SUMS.

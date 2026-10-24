# Pinned API and integration notes

Research only; all suggestions remain draft. No repository changes, installation, upstream script execution, GUI construction, or runtime tests occurred.

## Exact version provenance

- `public/release-tauri-2.12.5.json`: GitHub tag release, published2026-10-21T11:54:49Z, non-prerelease/non-draft, target commit993038c49185307dfc895ab7657d77ca0d5ed7c7.
- `public/gitref-tauri-2.12.5.json` and `public/github-core-commit.json`: tag→commit object; commit tree metadata. GitHub signature metadata is preserved, not independently cryptographically verified here.
- `public/crate-tauri.json`: registry max_stable_version2.12.5, max_version3.0.0-alpha.5.
- `public/crate-tauri-2.12.5.json`: release time, not-yanked, MSRV1.90, license and package checksum.
- `public/tauri-2.12.5.crate` is a downloaded **source** archive, never installed or executed. `method/published-crate-fetch.json` records the actual SHA-256 and registry-checksum match. `published-crate/.cargo_vcs_info.json` identifies the same993038c commit; extracted channel.rs/async_runtime.rs/webview/mod.rs equal pinned upstream bytes. Source archives contain upstream code/scripts but none were executed.
- Root and crate manifests inherit edition2024/MSRV1.90/Apache-2.0 OR MIT. License texts are saved with their actual hyphenated filenames; two earlier underscore-name404 responses remain in public-fetch.json.
- `upstream/packages/api/package.json` is2.12.4; `public/npm-api-current.json` agrees; `public/npm-api-all-metadata.json` records2.12.4 published2026-10-14T18:47:44.921Z. `npm-api-2.12.3.json` is a deliberately retained earlier lookup, not the selected version. No NPM package installation or distribution integrity verification occurred.

## APIs actually considered

All Rust source paths below are under `upstream/` at993038c. Their public URLs are `https://github.com/tauri-apps/tauri/blob/993038c49185307dfc895ab7657d77ca0d5ed7c7/<path>`.

| API | Actual signature/semantics | Pinned source |
|---|---|---|
| Builder state | `manage<T>(self,state:T)->Self where T:Send+Sync+'static`; duplicate same-type state asserts | crates/tauri/src/app.rs:2055 |
| Fallible state lookup | `Manager::try_state<T>(&self)->Option<State<'_,T>>`; `state` panics when missing, while the command State extractor returns InvokeError | crates/tauri/src/lib.rs:751–775; src/state.rs:59–69 |
| Setup | `setup<F>(self,F)->Self`, F is FnOnce(&mut App<R>)->Result<(),Box<dyn Error>>+Send+'static | crates/tauri/src/app.rs:1838 |
| Invoke dispatch | `invoke_handler<F>(self,F)->Self where F:Fn(Invoke<R>)->bool+Send+Sync+'static`; register once via generate_handler! | crates/tauri/src/app.rs:1723 |
| Build | `build(self,Context<R>)->tauri::Result<App<R>>`; event-loop/display/runtime/plugin initialization can fail | crates/tauri/src/app.rs:2377–2408 |
| Main loop | `App::run_return<F:FnMut(&AppHandle<R>,RunEvent)+'static>(self,F)->i32`; run_iteration deprecated/busy-loop warning | crates/tauri/src/app.rs:1394–1429,1495–1527 |
| Exit hooks | `ExitRequestApi::prevent_exit(&self)` and `CloseRequestApi::prevent_close(&self)`; restart ignores exit prevention | crates/tauri/src/app.rs:79–106 |
| Exit | `AppHandle::exit(&self,i32)` requests ExitRequested/Exit; if runtime request fails it cleans up Tauri internals then calls process::exit | crates/tauri/src/app.rs:574–586 |
| Main thread dispatch | `run_on_main_thread<F:FnOnce()+Send+'static>(&self,F)->tauri::Result<()>` | crates/tauri/src/app.rs:496 |
| Async runtime | `set(tokio::runtime::Handle)` only once, before lazy initialization; runtime must stay alive; spawn requires Send+'static; JoinHandle resolves tauri::Result<T> | crates/tauri/src/async_runtime.rs:139–169,252–318 |
| Commands | `#[tauri::command] async fn...(State<'_,Bridge>, owned DTO)->Result<Reply,BridgeError>`; inputs Deserialize, outputs/errors Serialize; borrowed async arguments require Result workaround | crates/tauri-macros/src/command/wrapper.rs; public/docs-calling-rust.txt |
| JS call | `invoke<T>(cmd:string,args:InvokeArgs={},options?:InvokeOptions):Promise<T>`; TS generic provides no runtime DTO validation | packages/api/src/core.ts:443–449 |
| Channels | `Channel<TSend>::send(&self,data:TSend)->tauri::Result<()> where TSend:IpcResponse`; direct eval or pending-body fetch; no consumption ACK | crates/tauri/src/ipc/channel.rs:273–345,453–464 |
| Events | Emitter::emit_to<I,S>(target,event,payload)->Result<()> where I:Into<EventTarget>,S:Serialize+Clone; events have no fine-grained payload capability policy | crates/tauri/src/lib.rs:1009; public/docs-calling-frontend.txt |
| Navigation | `WebviewWindowBuilder::on_navigation<F:Fn(&Url)->bool+Send+'static>` and `on_new_window<F:Fn(Url,NewWindowFeatures)->NewWindowResponse<R>+Send+'static>`; respond Deny for unsolicited popup | crates/tauri/src/webview/webview_window.rs:266,315; webview/mod.rs:243 |
| App ACL | `AppManifest::commands(self,&'static[&'static str])->Self`; Attributes::app_manifest sets it, try_build returns Result<()> | crates/tauri-build/src/acl.rs:93–130; src/lib.rs:551,652 |

## Proposed integration, explicitly unimplemented

Use a synchronous GUI main function so the OS event loop stays on the main thread. Either use Tauri's global Tokio executor or create one multithread Tokio runtime, set its handle before Tauri runtime access and retain its owner through local shutdown. Do not create a nested block_on inside async commands or wait for shutdown on the event-loop thread.

Conceptual command surface:

```text
gui_snapshot(last_seq) -> NotModified | CompleteSnapshot | ResyncRequired | Busy
gui_start(fixed_profile_id) -> existing agent outcome, with Rust-owned original UUID
gui_retry_start(original_pending_uuid) -> only that existing UUID
gui_stop(exact_job_or_session_id) -> operation receipt, not cleanup proof
gui_preferences(three frontend preferences) -> existing bounded user file operation
```

These names are proposed, not existing product APIs. Enumerate all of them in **both** AppManifest::commands and generate_handler!, and grant their generated allow permissions only to one explicitly selected local capability. No generic service-method proxy, path reader, shell command, profile editor, credential argument or helper mutation endpoint. Preserve exact pending UUID in Rust across WebView remount/reload; frontend never substitutes a new UUID after an unknown result. Service authorization remains authoritative even after a UI confirmation.

Concrete bounded pull option: one Rust supervisor owns one local and one remote observer and coalesces complete observation state. `gui_snapshot` uses a try-acquired single permit rather than a waiting queue. One full serializable projection is retained with fixed row/string/log-tail limits and an encoded-byte ceiling; declare exact values in the later design and verify worst-case encoded data. Caller supplies only last sequence, cannot allocate a new observer. JavaScript schedules the next poll after the previous one settles; Rust independently enforces admission so a compromised renderer cannot bypass the normal schedule. A sequence change returns a whole capped model; identical sequence returns NotModified. Any overrun or replacement mismatch returns explicit ResyncRequired/unavailable while preserving current/stale semantics, never truncates a complete snapshot into Current. Mutation receipts/unknown UUID state have separate bounded exact-identity retention and cannot be coalesced away. A framework-level arbitrary-input allocation bound is **not** established: protocol.rs parses supplied JSON before application command checks.

Close/exit handling uses an atomic/sticky Closing state. Initially prevent_close/prevent_exit and begin cooperative local stop; wake and join the actual local observers and accepted frontend work under a Rust owner. A second quit request observes the same owner. Once joins finish, mark readiness and request AppHandle::exit, permitting the resulting ExitRequested. Do not use AppHandle::restart, process-plugin exit, panic, abort, runtime-drop or a timeout as a substitute for actual joins. The synchronous RunEvent::Exit hook is too late to initiate reliable async cleanup. The agent's persistent service jobs keep running. Permanent FS/kernel blocking remains an existing explicit limitation; this research adds no bounded-completion guarantee.

## Security and feature cautions

- Source webview/mod.rs:1959–2084 checks the invoke key and classifies local URLs, then checks plugin ACL, app ACL when present, and remote origins. Local custom commands absent an app manifest bypass ACL rejection. A capability JSON alone does not make unenumerated app commands protected.
- Capability windows and webviews are OR matches. `windows:["main"]` also grants every webview in that window, irrespective of `webviews`. Prefer a single fixed webview label and do not expose window creation permissions. No remote capability; no external iframe. Linux/Android cannot distinguish embedded iframe requests from parent-origin requests according to official docs.
- Navigation should use parsed scheme/host/port and intended local route, never string starts_with. Production bundled local origin and development devUrl differ; developer-origin allowances must not leak into packaged config. Configure navigation/popup handlers before creating the webview, e.g. from_config with config creation disabled.
- CSP is off if not configured; preserve Tauri nonce/hash injection. Start from self-only bundled resources, required IPC connect targets, object-src none/base-uri none/frame-src none; validate the final generated policy against selected Linux engine and any debug dev server. No claimed XSS immunity.
- Per-webview Channel pending payload HashMap and JS pending array have no application capacity/ACK. Closing a registered webview purges pending large bodies and stops further queueing, but Channel::send can return Ok when an old webview is no longer registered. This is not proof of frontend delivery, acceptance, ownership cleanup or agent admission.
- Events can originate in JS; event callbacks must not authorize Start/Stop. `listen` returns Promise<unlisten>; await it and unregister on SPA unmount. Prefer commands for mutation and complete pull projections for state.
- Linux `tauri` has nonoptional GTK0.18/v3_24 even with default-features=false; `wry` adds webkit2gtk and runtime-wry. Default features also include compression/common-controls-v6/dynamic-acl/x11/dbus. A draft minimal Linux GUI selection is explicit wry+x11 with optional compression; actual X11/Wayland/platform selection needs later build/runtime validation. Release custom-protocol selection remains required for bundled assets. No shell/fs/http/process/updater plugins are needed for the scoped ordinary-user bridge.
- Current root RustUSB workspace has members `crates/*` and no default-members, while delivery uses --workspace and --all-features. Merely adding a GUI crate inside crates or changing default-members would still pull GUI into --workspace builds. A separate opt-in GUI workspace/package outside that glob preserves existing gates; there must be no reverse dependency from core/CLI/service to Tauri. No cargo metadata/build executed to prove a future graph.
- Official advisory GHSA-7gmj-67g7-phm9 (CVE-2026-42184) lists patched>=2.11.1; GHSA-w28w-mhc8-qvjv lists patched>=2.11.6. Their JSON/full HTML are retained. Advisory describes random channel data IDs;2.12.5 source and changelog use per-webview sequential IDs. Treat the source as authoritative for2.12.5 and do not derive a random-token security claim. Full dependency and WebView vulnerability status was not audited.
- Release index lags at2.12.3; prerequisites page still says Windows7 while2.12 blog explicitly drops it and sets MSRV1.90. Do not reuse the old prerequisites Windows claim. RustUSB workspace1.97 satisfies declared Tauri MSRV, but compilation/platform support remains untested here.

## Evidence limits

This is source/API research and a same-author reading, not an independent security audit. No GUI, privileged code, USB action, package install, benchmark, browser run, QEMU or hardware test. No performance/latency/memory claims. Proposed limits need product design and meaningful tests. Existing E48 G4 software/QEMU evidence is not Phase5 GUI evidence or broader hardware/G3 acceptance. Source/SHA manifests do not prove that any compiled GUI uses this dependency set.

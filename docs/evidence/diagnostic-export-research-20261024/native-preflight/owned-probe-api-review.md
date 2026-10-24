# Owned Native probe review — additive API proposal

Read-only review of `docs/research/diagnostic-export-design.md` and current `crates/rustusb-native/src/diagnostics.rs`, 2026-10-24. No implementation, build, test or product invocation. The earlier preflight report/manifest are preserved unchanged; this supplement has its own manifest.

The proposed design is implementable without changing the old doctor or any USB operation. Keep the public old `diagnose_native() -> NativeDiagnostics` and its three-second tool deadline plus two-second bounded reap observation. Add an explicit export-only entry point, preferably:

```rust,ignore
pub async fn diagnose_native_with_child_reaping() -> rustusb_core::Result<NativeDiagnostics>;
```

Use private `CleanupMode::{BoundedObserve, ActualReap}` below a shared collector. The new entry point returns `Err` only for actual cleanup/reap uncertainty (or collector setup); ordinary missing/untrusted/nonzero/timeout observations remain report data when their owned child was actually reaped. A different small closed error type is also acceptable if root prefers a diagnostics-specific public API; it must not expose error text in the artifact. The existing old API need not acquire a new fallible signature.

Implementation constraints:

1. Thread cleanup mode through `probe_fixed_tool`, `run_version`, and **every** `failed_child` invocation, including impossible pipe-taking branches, output overflow, capture/wait failure and command deadline. Change no trust/argv/environment/output bound or probe deadline.
2. `ActualReap` calls `start_kill()` then `child.wait().await` without wrapping the wait in the old two-second timeout. A kill error is not a reason to skip wait: the process may have exited concurrently. A successful wait is the receipt. A wait error must remain explicit, cause a partial capture, and must never produce `child reaped` or a complete success claim. Actual waiting is not a promise that OS/kernel termination cannot remain blocked.
3. Finish both tools even if one cleanup reports an error. Use `tokio::join!` for the pair and inspect both results afterward; no early-return `try_join!` that drops another owned child. The existing inner capture `try_join!(child.wait(), stdout, stderr)` is safe only because the outer scope keeps `child` and routes errors through actual cleanup.
4. Keep `kill_on_drop(true)` as a defensive fallback, not the supported cancellation/reap guarantee. The export's actual owner must retain the whole new future after SIGINT/SIGTERM or downstream cancellation. Cancellation stops later stages, not the accepted probe owner.
5. A dedicated current-thread Tokio runtime in the exporter-owned blocking task must enable timer/I/O/process support, `block_on` both Native and sanitized helper observations, then actually drop the runtime before the task returns. This waits for internal Tokio filesystem work that survived local timeouts; it cannot substitute for the explicit child wait. Do not use `shutdown_background` or `shutdown_timeout` to declare the owner done.
6. Only the direct `tokio::process::Child` is owned/reaped. Do not claim a process-group/descendant kill guarantee, and do not add new privileged process-group signaling to this slice.
7. `diagnose_helper` is already a read-only authenticated Status observation and has no child. Preserve its existing eight-second bound and policy denial semantics; no fallback device operation or privileged bypass. A helper log line is not a USB mutation.

Suggested new crate-private bridge (exact crate setup remains root-owned):

```rust,ignore
// No Serialize or Debug derivation; never leaves the projection boundary.
pub(crate) struct NativeProbeSnapshot {
    pub(crate) native: rustusb_native::diagnostics::NativeDiagnostics,
    pub(crate) helper: rustusb_daemon::diagnostics::HelperDiagnostics,
}
pub(crate) fn collect_native_blocking(
    helper_socket: std::path::PathBuf,
) -> Result<NativeProbeSnapshot, ProbeFailure>;
```

`collect_native_blocking` itself runs only inside a retained outer `spawn_blocking` owner. It owns its dedicated runtime; `tokio::join!` completes Native and helper before runtime destruction, including on Native error. `ProbeFailure` is an internal closed classification, not Display/Debug. If retaining a successful helper section when Native reap fails matters, return a raw snapshot containing two typed section outcomes plus a collector completion field instead of one top-level Result. That is a useful representation choice, not permission to hide cleanup uncertainty. Existing raw DTOs remain Serialize for old doctor compatibility; it is the bridge/wrapper and private collection API that must not expose generic serialization. The final collector serializes only the root-owned closed projection DTO.

Meaningful implementation tests to add when authorized:

- Real private child `exec sleep` under a short fixture-only deadline and actual output-overflow child: assert timeout/overflow remains the cause, actual direct-child wait completes, and its process no longer exists before new API returns. Reuse the existing test-only process-fixture lock and paths; no production environment/path override.
- Two concurrently owned private children: one fails/exceeds output while the other remains active, then cancellation/completion releases both through real waits. A simulated caller/receiver cancellation must not abort the retained owner; assert owner completion after direct-child receipts.
- Exercise every error branch's cleanup result through a narrow controlled internal seam only where an actual OS error is not portable; label it simulated, and assert wait uncertainty becomes partial rather than parse the free-text detail. Do not add dozens of mirrored mode-predicate tests.
- Existing old doctor tests must still pass unchanged and preserve old bounded observation semantics. New API tests do not establish USB I/O, helper root authentication, or permanently blocked OS termination.

The only blocking design issue would be returning a normal complete Native report after `wait()` fails while asserting every child was reaped. An explicit Result/typed uncertainty closes that gap. Otherwise the proposed ownership split has no source-level blocker.

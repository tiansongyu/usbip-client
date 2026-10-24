# Report console log write failures without recursive diagnostics

2026-10-24. Corrective contract after the first actual diagnostic-export process run. Original product `dff36a3`; clean harness `2ca5f5b`; all-feature CLI `67fff5700d3282f711659c8b3fae363d5ee4f80d9a8bfdd42476dba388dfc419`. Implemented in `8351577`; the [validation report](diagnostic-export-validation.md) records the preserved failure, 797-execution gate and actual corrected process/CI results. The verification section below is the original plan, not a replacement for those receipts. The complete Phase0–7 goal remains active.

## Observed failure

The first three process scenarios passed: existing/symlink output refusal, local diagnostic publication, and valid/corrupt local log capture. The fourth deliberately ignored SIGXFSZ and set its own RLIMIT_FSIZE to16. The actual receipt correctly retained errno27/EFBIG, a16-byte private partial and its digest; the final filename was absent. Actual child wait returned101, failing the strict exit1 assertion. Its stderr evidence file was also limited to16 bytes. All seven children were reaped without forced cleanup; source and ELF guards remained true. Later signal/backpressure/default/regression scenarios were not executed. Preserve that FAIL.

Locked tracing-subscriber0.3.23 has an explicit `log_internal_errors(true)` default in `fmt/mod.rs`. After the selected stderr writer's `write_all` fails, `fmt/fmt_layer.rs:1051–1063` calls `eprintln!` on that same failed stream. This explains a credible secondary panic route; the truncated original stderr is not a captured panic backtrace. The Rust source and new same-fault rerun must substantiate the correction, without rewriting the original verdict.

## Corrective boundary

Keep the successful default human/JSON formatting, ANSI policy, `RUST_LOG`, stdout protocols and file-only logger behavior. Keep synchronous default stderr semantics; this change introduces no queue, detached worker, extra file, USB capability or new external dependency. It does not claim that an unread/permanently blocked stderr is cancellable.

Use a private checked console MakeWriter around `std::io::Stderr`. Delegate actual `Write`/`flush` calls and return their real results, while latching only the first returned error's `ErrorKind` and optional errno. Do not format/store arbitrary errors or acknowledge a failed write as successful. The latch contains metadata only and holds no lock across I/O. Disable the subscriber's recursive internal-error printing. This fixes returned stderr I/O errors such as the observed EFBIG; it is not a claim that arbitrary third-party field formatters cannot panic. Rust's special handling of a closed stderr FD remains a separate compatibility boundary and is not described as observed EBADF.

The default LogOwner keeps the latch and reports its error from explicit shutdown **after the existing actual operation/cleanup tree**. File monitors remain absent in console mode and the healthy default ShutdownReport remains `status:None`. File-mode queues, counters, real workers and shutdown ordering remain unchanged. A console failure must not poison the application WAL or turn an unproven USB outcome into cleanup success.

Replace the CLI/helper's own error-path `eprintln!` calls with fallible `writeln!` whose delivery result is best effort. Preserve their failure exit status and message text; never fall back to machine stdout or recursively trace the logging error. This includes the last logger-shutdown failure message, which could otherwise recreate101 after the original failure was correctly returned. Parser/library formatting paths outside these owned diagnostics are not assigned a broader guarantee.

## Verification

Add a focused unit check for first-error latching and real failed writes, using private test-owned files without replacing process-global stderr. Run the original actual RLIMIT_FSIZE16 scenario with regular stderr unchanged, requiring the original partial/file receipt plus actual exit1. Exercise human and JSON console modes and a successful read-only operation whose logging fails, so logging loss cannot become exit0. Preserve the actual stderr bytes and no-cleanup-before-owner facts where the scenario owns a service.

Then run the complete Rust gates and relevant process regressions on the changed sources/ELFs, and continue the three observer signal gates, stdout backpressure, default-feature checks, focused installed WAL QEMU and four native CI. Earlier793 results retain their old source scope. No new QEMU or hardware result is implied by this corrective design.

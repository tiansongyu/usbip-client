# Private diagnostic exports: validation and limits

2026-10-24. Private diagnostic export, an independently published committed service view and checked console error reporting are implemented. Software gates, actual local subprocess checks, one installed Native QEMU scenario and four native CI combinations passed. Their source revisions and evidence scopes are separate. This completes the diagnostic-export increment of requirement 4.11, not Phase 3 or the full Phase 0–7 project.

The [operator guide](../diagnostics.md) covers commands and result handling. The [design](diagnostic-export-design.md) specifies publication, authorization, quotas and retained workers; the [console correction](stderr-write-failure-design.md) explains the original returned-write failure and its limits.

## Behavior and ownership

`rustusb diagnostics --output /absolute/private/result.json` collects fixed Native/helper observations, optional local log history or one authorized agent profile. It does not scan devices, change drivers, stop jobs or read broker credentials. Agent mode requires both Read and Audit on the exact profile. Each request rechecks the real peer UID; even root sees only the selected profile and its own unscoped denials.

The service publishes immutable metadata only after successful business commit, state replacement and the existing audit mirror. Authorized Begin/Page/End and their errors bypass the WAL actor. Up to four actual snapshot objects, one per UID, have a 30-second access expiry. Arc ownership retains the quota while a page copies metadata; End cannot prematurely free a still-owned slot. Pages contain at most 16 rows and 60,000 encoded payload bytes under the existing 65,536-byte frame.

The closed schema-1 JSON is at most 4 MiB. Separate per-capture alias namespaces preserve relationships without a reverse map. Raw UIDs/PIDs, endpoints, paths, identifiers, serials, credentials, USB contents, tool output and arbitrary messages are omitted. Original operation errors, cleanup results and transport-end categories remain distinct. Health and logs are independent samples, not an atomic extension of the pinned business view.

The output uses a fresh checked 0600 partial in an existing caller-owned 0700 directory with protected ancestors. Real accepted writes and their digest precede file sync, NOREPLACE publication and directory sync. Existing files are never overwritten. A failure records partial or uncertain publication side effects. Signals stop new stages and await actual retained work. A signal after publication was accepted preserves its real result. The collection budget cannot cancel a permanently blocked filesystem call; Native child termination is followed by actual reaping, and private runtimes retain internal filesystem work until completion.

## Software gates and executable identity

The final complete local gate ran on **modified `2ca5f5b`**, with all **192 selected source/build inputs** unchanged before and after. Those bytes match product `8351577` and the later QEMU revision. This mapping does not relabel the original run as clean. The selected list includes manifests and platform-specific sources; it is not a rustc dep-info trace or a count of unique tests.

| Gate | Actual result |
| --- | --- |
| Default workspace tests | 343 executions |
| All-features workspace tests | 434 executions |
| Vendored nusb library tests | 20 executions |
| Total | **797 executions**, 47 result rows, zero failed or ignored |
| `scripts/check.sh` | Formatting, both Clippy configurations, tests, release builds and strict rustdoc passed |
| Native-only isolation | Secure-only normal dependency tree excludes the optional engine, nusb, rusb and libusb |

The lock adds one internal crate, moving the inventory from 159 to 160 packages. Every existing external package block, version and checksum is unchanged. The [software archive](../evidence/diagnostic-export-stderr-correction-software-20261024/manifest.json) retains actual commands, source copies, environment, outputs and executable receipts.

| Local release artifact | SHA-256 |
| --- | --- |
| All-features CLI | `9708a50ee836378cd64f8726dae36caded96d4fca39faae215aa30658ef49b48` |
| All-features helper | `7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322` |
| Separately built default CLI | `88c00373baab4e25b6df149dbe74d4009fbe1b2932ffe7039b1fcf1ff55ffdb0` |

The earlier **793 = 341 + 432 + 20** gate belongs to modified `88179f2`, with 191 selected inputs matching `dff36a3`. Its [original software archive](../evidence/diagnostic-export-software-initial-20261024/manifest.json) retains a preceding service test-fixture failure: reading a weak monitor after consuming its owner did not observe the final shutdown status. The fixture was corrected to check the actual shutdown report and committed disk audit. The original failure is preserved.

## Actual local processes

At clean **`8351577`**, a separate checkout selected the above unchanged executables without rebuilding. All 192 selected inputs, executable, HEAD and clean-worktree guards remained true; all children were reaped without forced or secondary cleanup errors.

| Executable | Actual diagnostic groups | Actual children |
| --- | --- | --- |
| All-features CLI | 9 | 13 |
| Default CLI | 4 | 6 |

The all-features groups cover output refusals, local capture, local logs, real file-size failure, successful operations with failed stderr, SIGINT, SIGTERM, accepted-publication signal race and stdout backpressure. The default groups cover refusals, local capture, unavailable agent mode and failed stderr after successful operations. Human and JSON console subchecks account for additional children. The old four server-signal and two backpressure scenarios also passed with the selected all-features CLI, under their own observation limits.

Two real `fchmod` syscall-entry holds receive SIGINT/SIGTERM while the original process and partial inode remain live. After the actual original syscall returns 0, cancellation leaves a valid Cancelled partial and exit 1. A real `fsync` entry hold receives SIGTERM after write acceptance; the original syscall returns 0 and the result remains Complete/Published with `cancel_observed: true`, exit 0. These removable entry holds do not establish cancellation inside a permanently blocked kernel handler.

The stdout test fills a real 4096-byte pipe without draining until actual wait, while file publication completes. Its drained padding was not saved as an independent raw artifact. Controlled loopback DEVLIST checks produce successful empty output while actual stderr failure causes exit 1; the small wire exchange was asserted by the frozen runner but not separately saved. Of the 19 diagnostic children, five have complete proc/executed-ELF receipts, nine identity-only receipts and five wrapper-before-exec receipts. Missing samples are not reconstructed. Legacy blocked-output EOF was sampled after wait, and that mode has no saved stopped-resource snapshot.

The [local runtime archive](../evidence/diagnostic-export-local-runtime-8351577-20261024/manifest.json) retains these scopes, actual signal/syscall/wait receipts, original outputs and two method reviews. Their predicates are not extra USB cases.

## Installed Native path in QEMU

The [focused runner](../../scripts/qemu_diagnostic_export/README.md) passed at clean **`d9ebe2eadf8490081035199c91d17bbba41bc57e`**: actual parent exit 0, both guests exit 0, final success markers and all four source/executable/HEAD/clean guards true. Its default locked all-features build produced the CLI/helper above. All 209 selected harness inputs include the same 192 software inputs.

This is **one Native IPv4/framed-v2 paired run, one epoch, two actual I/O rounds**. UID0 helpers, UID2000 agents and a UID1000 exporter use the installed production paths. Both rounds deliver real emulated keyboard events and 35 bytes in each FTDI direction, totaling 70 bytes each way. The second round occurs before the original WAL syscall is released.

The original server `state.next` candidate 1 is held at the actual same `fsync` entry: PID 440/start 370, TID 447, FD 27, device 2/inode 413. Pre-export, post-export and post-second-I/O receipts bind that same live call. The observed hold is **1.037464451 seconds**, an artificial interval rather than a performance or hard-block exit guarantee. Explicit release resumes the same syscall, which actually returns 0.

The diagnostic file pins **committed sequence 46**, excluding draft 47. It contains one job, four sessions, all 44 authorized retained audit rows and three log pages with 44 records. The two other callers' unscoped denials are excluded. The complete private artifact is **26,173 bytes**, SHA-256 `f433247df9d17cf9f6653efa5b3b5f2c54b37709f9f945e59d9c389904e05013`. Closed-field, alias and source-to-projection checks use the retained raw artifact and original committed view.

Read-only and audit-only callers are denied. Their UID execution is bound to fixed `setpriv` invocations, root manifests and protected per-UID output directories; unlike the successful exporter they have no individual proc/executed-ELF samples. Existing-output refusal preserves the original inode and bytes, including an actual guest `cmp`; this scenario does not trace a concurrent rename collision. Original-token End is supported by the collector's owned cleanup path and a subsequent same-UID Begin/End before releasing the WAL gate, not a raw trace of the first token's End request.

Client-first cooperative stop yields all six exact original Root terminal proofs, full driver/configuration/interface baseline, all 16 VHCI ports NULL and original generations Gone. Two original incoming sessions retain State with Protocol transport end alongside Verified cleanup. The strict correlation uses their exact IDs, connection identities, reversed tuples and paired client `local_stop`; it does not turn those outcomes into clean EOF or identify the precise reset branch. Raw packet captures are retained without claiming a new TLS packet-classifier verdict.

The [final QEMU archive](../evidence/diagnostic-export-qemu-final-d9ebe2e-20261024/manifest.json) includes raw files, generated source, actual executable/UID receipts and two separately written raw-method reviews. Reviewers participated in implementation or harness work; method independence is not an independent implementation audit. Their checks and the host's 1300 predicates are not additional USB test cases. A review-script variable-shadow failure is retained separately from the actual run verdict.

## Four native CI combinations

Exact **`d9ebe2e` [run 40067501829](https://github.com/tiansongyu/usbip-client/actions/runs/40067501829)** completed successfully on Ubuntu 22.04/24.04 × x86_64/aarch64. Jobs are `120793716138`, `120793716286`, `120793716316` and `120793716335`. Full official decoded logs are saved as their original UTF-8 bytes, with metadata and a separate count/source-method review in the [CI archive](../evidence/diagnostic-export-ci-d9ebe2e-20261024/manifest.json).

Each platform runs 797 software executions, the existing five TLS combinations, four signal, two backpressure and six logging scenarios, plus nine diagnostic groups with 13 children. Those children have four full proc/executed-ELF, six identity-only and three wrapper-before-exec receipts per platform. The four default-only diagnostic groups were run locally on x86_64 and are not claimed across this CI matrix. The method review's 617 predicates are not extra test cases. Runner inventories include historical evidence and are not compiler input counts. CI hashes do not provide missing executable bodies or runner temporary files.

The earlier `dff36a3` [run 40061061065](https://github.com/tiansongyu/usbip-client/actions/runs/40061061065) passed four platforms with 793 executions and the then-existing process checks. Its [separate archive](../evidence/diagnostic-export-ci-dff36a3-20261024/manifest.json) retains five delegated connector failures and an initial ARM log-parser failure; those were evidence-collection/method failures, not failed CI jobs. It does not verify the later correction or new diagnostic runner.

The English README/site content at `94e0d07` has a later [publication archive](../evidence/diagnostic-export-publication-94e0d07-20261024/manifest.json): software run `40069172719` completed successfully on all four native platforms, and Pages run `40069172720` passed check/deploy. Six canonical HTTPS resources returned 200 and match that exact content commit. Live desktop/mobile layout and keyboard behavior were checked separately. These status receipts do not recount 797 executions or move the QEMU results from clean `d9ebe2e`.

## Preserved failures and remaining work

The first actual process matrix at clean `2ca5f5b` failed its fourth group: the correct errno-27/EFBIG receipt, 16-byte partial and digest accompanied actual exit **101**, because regular stderr had the same file-size limit. Seven children were reaped and guards stayed true. Two earlier sparse-checkout preflights ran no children. The [original failure archive](../evidence/diagnostic-export-local-first-failure-20261024/manifest.json) preserves all three attempts. A same-old-executable debug/off differential is diagnostic evidence, not a recommendation to disable logging. Checked console writes now retain real returned errors, disable recursive subscriber error printing and report failure after actual operation cleanup. Default synchronous stderr, third-party formatter panics, closed-FD compatibility and permanent stderr blocking retain their stated limits.

The first QEMU run at clean `bde3aa9` remains **FAIL**, although both guests exited 0. Its original strict tracer expected 15 arguments; the fixed logging launch supplied 21 and was rejected before server startup. No successful export or USB I/O is inferred. The [original run and correction archive](../evidence/diagnostic-export-qemu-first-failure-bde3aa9-20261024/manifest.json) retains it. A generated test-only variant changes only the count and six exact fixed logging arguments. Original C, syscall selectors, FD identity, actual return, cleanup and deadlines remain unchanged. Three actual C compilations and 26 isolated argument-predicate executions verify that narrow change; they are not product or USB tests. Only the later clean run is PASS.

The earlier [logging scenario at clean `48c9794`](rotating-logs-validation.md) and [23 paired runs at clean `1582f06`](prepare-recording-shutdown-validation.md) retain their original executables and scopes. They are not rerun or combined into a new single-revision matrix. Real hardware G1/G2, complete G3, permanent kernel/filesystem blocking, broader Rust crash recovery, age-based log retention, TUI/GUI, packaging and Windows USB remain unfinished.

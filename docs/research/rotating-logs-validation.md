# Rotating process logs: validation and limits

2026-10-24. The bounded file sink, authorized log reader and real-process checks are implemented. Software, local subprocess validation, the installed QEMU scenario and corrected four-platform CI passed. Their actual revisions and evidence scopes remain separate. This is a requirement 4.11 increment, not completion of Phase 3 or the full Phase 0–7 project.

The [operator guide](../logging.md) describes startup options, private-directory setup, pages and failure handling. The [design and fixed-source comparison](rotating-logs-design.md) records the storage and ownership contract. Default stderr behavior remains available; opting into file logging selects the bounded file sink.

## Behavior under test

- CLI, helper and agent share a nonempty `rustusb-logging` crate. No external dependency versions or checksums changed; the dependency inventory adds one internal package, from 158 to 159.
- An existing private 0700 directory contains checked, singly linked 0600 files. Descriptor-relative opens, identity validation and independently opened access-lock descriptors protect the finite store. A writer retains its actual ownership lock until shutdown.
- A record is at most 2 KiB. The queue holds 256 records. Defaults are 4 MiB per segment and eight segments; configuration permits 4096 bytes to 8 MiB and two to 16 segments, including active. The maximum segment payload is 128 MiB per store.
- Typed NDJSON excludes raw USB data, serials, descriptors, credentials, paths, arbitrary error strings and unapproved Debug formatting. Profile readers see exact typed audit projections, not generic diagnostics carrying profile-like text. Successful audit commits are mirrored only after the authoritative transaction; the mirror can lose records and has no admission or cleanup authority.
- Every agent page requires the existing profile `audit` action and actual Unix peer identity. Authorized pages, Busy and errors bypass the business WAL actor. Two actual readers and two queued requests remain bounded even when clients cancel. RPC callers cannot choose a path or change logging configuration.
- File logging is initialized before Tokio/device work. Existing actual owners finish their cleanup before the logger drains and joins its real workers. Full queues or I/O errors remain explicit. A final logging failure can produce exit 1 after a USB operation completed; it is not evidence that the operation was rolled back.

## Complete software gate

The actual run used modified `48a4b013fceb4493cf07c10cac999b8c41f76dae`. All **177 selected compilation inputs** were copied before execution, remained unchanged and subsequently matched product commit `f3cf8d4f13077cbcbb48c9e3e27918b87ca31a0d`. This correspondence does not relabel the original run as clean.

| Actual check | Result |
| --- | --- |
| `bash -x scripts/check.sh` | PASS; 308 default + 395 all-features + 20 vendor = **723 test executions**, 43 result rows, zero failed or ignored |
| Formatting, both Clippy configurations, both release builds, both warnings-as-errors rustdocs | PASS; ten actual Cargo invocations are retained |
| Native-only `secure-transport` check and normal dependency tree | PASS; no Rust backend, nusb, rusb or libusb1-sys in that normal dependency tree |
| Independent statistical/source method | PASS; 415 qualified observed function names; these are distinct from test executions and USB cases |

Filesystem tests exercise actual rotation, restart, torn tails, cursor gaps, safety checks, replacement, concurrent locks and I/O failures. Tracing tests include secret sentinels and unapproved Debug values that must not be formatted. Four service logging tests use actual temporary stores and Unix sockets for peer authorization, committed audit projection, unavailable/Busy responses and bounded cancellation.

The complete [software archive](../evidence/rotating-logs-software-20261024/manifest.json) preserves commands, xtrace, toolchain, all selected source bytes, Git correspondence and release receipts. The final all-features CLI SHA-256 is `6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474`; helper SHA-256 is `bb0ea543bf6cc1af840c4e793b26906a6f9062b534c00b0c6549dce3fce834a1`.

Two earlier gates failed on separate `collapsible_if` warnings, first in the storage reader and then in the CLI reader. Neither reached test execution. Both original failures and exact source versions remain archived; the fixes changed style without weakening warnings or tests. The earlier development subset, 22 logger and four service executions, belongs to its earlier source snapshot.

## Actual local processes

A separate clean checkout at `f3cf8d4` selected that external all-features CLI by its exact byte hash, without rebuilding. The actual parent coordinator completed with exit 0; compilation-input, HEAD, clean-tree and binary guards stayed true.

| New process scenario | Actual observations |
| --- | --- |
| Startup refusals | Seven steps within one grouped case; invalid flags, unsafe stores and a second writer are rejected, with machine stdout separate |
| Rotation, restart and cursors | Actual 4096-byte/two-segment bounds, four process epochs and 18 one-record pages correlated with retained raw bytes |
| Follow SIGINT | Observer exits; independent virtual owner retains both active imports and pending requests, then shuts down normally |
| Follow SIGTERM | Same ownership separation with actual SIGTERM |
| Observer stdout backpressure | Actual 4096-byte pipe fill, blocked writer and no draining; observer fails after its output deadline while the USB owner continues |
| Runtime file-size failure | Actual parent `prlimit`, same active FD/inode, 329 to 345 bytes with a real 16-byte incomplete tail and errno 27; virtual owner still active before requested stop, real stopped resource snapshot, then wait exit 1 and subsequent data EOF observations |

The EFBIG case does **not** contain a successful disk logger `Stopped` record. The data EOF reads occurred after the actual wait; no earlier parent-side EOF timestamp is claimed. The cleanup-before-logger contract is supported by source ordering and the genuine operation snapshot, not a moved observation time.

All six new cases passed. The original four signal and two output-backpressure scenarios also passed with the same selected CLI, under their existing evidence contracts. Those older blocked-output cases do not gain unread backend snapshots or missing per-child ELF receipts.

The [local runtime archive](../evidence/rotating-logs-local-runtime-20261024/manifest.json) preserves raw protocol/cursor/output bytes, process identities, signals, waits, failed-file tails and parser attempts. Of 38 new child records, 11 have an actual `/proc/exe` sample; 27 fast children retain selected-command/global binary guards without that sample. The new runner inventory has 190 entries. The older signal inventory has 14,149 entries including 13,951 historical documentation snapshots; these are not current compilation inputs.

These tests use ordinary-user loopback memory virtual profiles. They do not invoke the privileged helper, enumerate kernel USB devices or operate host USB hardware.

## Installed path and native CI

The focused [QEMU runner](../../scripts/qemu_process_logging/README.md) passed at clean **`48c9794d4a5f1e8116e504b7396937e808e5b32f`**. It performed its default locked all-features release build; both actual guest executable hashes equal the software-gate CLI/helper above. All 189 selected harness inputs, including the 177 compilation inputs, and source/binary/HEAD/clean guards stayed unchanged. The actual parent exited 0, both guests exited 0 and both guest success markers were present.

This is **one paired Native IPv4/framed-v2 scenario with three agent epochs**, not three new runs and not a rerun of the earlier 23-run matrix:

1. Installed UID0 helpers, UID2000 agents and UID1000 operators perform actual keyboard input and 35 bytes in each FTDI direction. Authorized profile/self-denial pages, other-UID/profile refusals and byte/count rotation are observed with the original capabilities unchanged. A path-shaped cursor is rejected by the CLI before IPC; it is not a QEMU wire-injection test.
2. A distinct root process holds the actual agent access-lock inode with a fresh OFD. Actual writer EX and two reader SH syscalls block; five observer processes produce an actual Busy response without an assumed arrival order. All 512 denial requests have distinct already-committed audit receipts. After real server SIGTERM, the same agent and lock owner remain alive while exact original Root terminals, complete driver/configuration baseline and independent client VHCI NULL/generation disappearance are saved. Only then is the original nonce released. The actual logger drains 281 accepted/written records and reports 290 dropped records, with a genuine unhealthy `Stopped` status and final agent exit **1** for LostRecords. This expected logging failure is separate from verified USB cleanup; the guest can correctly exit 0 after checking both results.
3. Fresh agent instances and capabilities perform actual I/O and cooperate in complete cleanup. The original two helpers then actually stop. Across the three epochs, each FTDI direction carries 105 bytes; all six client capabilities and generations are distinct. There are six actual agent and two helper UID/executable receipts.

Seven authorized pages return 76 filtered records, each matched to a committed audit sequence and committing application instance. Private directory/file permissions and 4096-byte/four-segment bounds are checked in both roles. The other seven logger owners drain with no losses. The [final raw archive](../evidence/rotating-logs-qemu-final-48c9794-20261024/manifest.json) records exact files, generated init, actual waits and version/ELF/input receipts. The removable gate's observed duration is not a cleanup SLA or proof that permanent kernel/filesystem blocking is cancellable.

The first actual run at clean `44b1ef9` remains **FAIL**, with both guests exiting 0. Its verifier incorrectly required every normal incoming operation outcome to be error-free. Four original server incoming sessions retained State with a Protocol link end after their paired clients stopped, while exact cleanup was independently Verified. Fixed-source review found the same operation/cleanup distinction in the earlier clean1582 framed-v2 baseline. The corrected verifier permits this recorded outcome only for exact normal/fresh original incoming IDs, same preclose/terminal connection identities, reversed peer TCP tuples, paired client `local_stop`, actual I/O and host client-stop-before-server-stop ordering. Complete Root proofs and cleanup assertions remain strict. Neither product behavior nor timeouts changed; the revised verifier was not used to relabel the first run. Its [complete original failure](../evidence/rotating-logs-qemu-first-failure-44b1ef9-20261024/manifest.json), source reviews and author/parser mistakes are preserved.

State/Protocol is still an observed transport error, **not clean EOF**. Fault-epoch client imports can also retain Io after the server stops. These separate operation results remain visible alongside Verified cleanup; the captures do not identify the precise on-wire reset/error branch. Probe samples awaiting their first reply are not rewritten as healthy PONG observations.

The earlier **23 paired runs / 46 guest exits at clean `1582f06`** remain solely the [preparation-stop baseline](prepare-recording-shutdown-validation.md), with that report's original artifacts. No combined 24-run single-source result is claimed.

The corrected four-platform [CI at `89ef420`](https://github.com/tiansongyu/usbip-client/actions/runs/40053603593) completed successfully on actual native Ubuntu22.04/24.04 × x86_64/aarch64. Every job passed 723 executions/43 result rows with zero failed or ignored, existing four signals/two backpressure checks and six new process-logging scenarios. Its [official logs and method review](../evidence/rotating-logs-ci-89ef420-20261024/manifest.json) distinguish 11 actual `/proc/exe` samples and 27 fast children, native pipe-write observations and available raw contents from file hashes whose runner originals are unavailable. Source inventory paths include historical evidence and are not a compilation-input count.

The first [CI at `41fce5f`](https://github.com/tiansongyu/usbip-client/actions/runs/40052402641) retains four failures before any new logging case: the runner assumed `~/.cache` already existed. The narrow fix validates protected ancestors and creates only a missing cache with mode0700, retaining existing modes and product checks. Twelve ordinary-user filesystem function fixtures passed. Original [CI failures and the fix evidence](../evidence/rotating-logs-ci-first-failure-41fce5f-20261024/manifest.json) remain separate from the replacement success.

## Evidence discipline and remaining work

The [research archive](../evidence/rotating-logs-research-20261024/manifest.json) retains fixed upstream source/license/provenance, design reviews, author handoffs, lock comparison and separately identified crash/security gaps. Each archive records original paths, exact bytes and SHA-256; copied staged Git blobs were checked. Reviewer diagnostic totals are not extra software tests or USB cases. Reviewers also authored parts of this implementation, so independent parsing methods are not represented as an independent implementation audit. Initial review-parser mistakes remain separate from corrected reports and do not change the original product verdicts.

File logging is private operational history, not immutable audit, power-loss-atomic rotation or an unlimited archive. SIGKILL can lose queued records. Permanent filesystem or kernel blocking remains unbounded; an observation timeout cannot cancel an actual blocking worker. The verified removable QEMU obstruction establishes only its particular ordering window. Age-based retention, compression, diagnostic export, GUI/TUI log views, complete secret/output auditing, real-device G1/G2 and full security/Phase 3 acceptance remain unfinished. Credential replacement currently requires cooperative whole-agent restart; CRL/OCSP and hot revocation are not introduced by this change.

## README and website publication

The English README and paired project website were updated at `7640efb`. That exact content commit's four native software jobs and Pages check/deploy all completed successfully. Six canonical HTTPS resources match its Git blobs byte-for-byte; live desktop/mobile and keyboard observations are recorded separately in the [publication receipts](../evidence/rotating-logs-publication-7640efb-20261024/manifest.json). These later workflow status checks do not recount the detailed CI at `89ef420` or move the clean `48c9794` QEMU results to another revision. The globally installed `readme-site` skill and Codex discovery link retain their original four source files and fixed rstop reference.

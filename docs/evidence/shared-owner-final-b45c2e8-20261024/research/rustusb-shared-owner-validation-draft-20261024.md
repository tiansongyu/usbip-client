# Shared foreground and managed owner validation

2026-10-24. **Review draft:** implementation and final software validation are complete; all nine existing QEMU regressions have reached PASS. Independent review of the final QEMU raw evidence and its publication archive are still separate, pending delivery steps. This report covers shared task ownership, not completion of Phase 3 or hardware acceptance.

The CLI and application agent now call the same in-process service owners for secure Native imports and Native, virtual, and USBFS servers. Legacy virtual/physical foreground commands also use those owners. The CLI retains configuration loading, signals, bounded stdin controls, and JSON output; it no longer implements another device-owning server or launches a CLI subprocess to manage jobs. The contract is recorded in the [shared-owner design](shared-owner-design.md).

## Preserved execution contracts

| Boundary | Foreground | Managed agent |
| --- | --- | --- |
| Resource authorization | Existing untracked helper operations; no fabricated application session or correlation | Real durable session/attempt admission and tracked helper operations |
| Rust IMPORT | Existing authenticated, atomically authorized nonmanaged engine entry | The same device-key/Bus ID checks plus durable admission before actual claim |
| Local control | Bounded, local test stdin; existing HID/CDC controls only where supported | Production RPC remains profile/action based; no injected HID/CDC test input |
| Presentation | Existing schema-1 receipts, ready/snapshot/ack/error/stopped events | Persisted application facts and independently authorized session control |
| Completion | Actual resource shutdown/close/Finish, then optional final output | Actual cleanup plus independent durable recording results |

An output delivery ACK means the CLI completed its write/flush. It does **not** acknowledge a durable application admission. Managed persistence failures never fall back to untracked operations. Fresh untracked Native storage remains schema 1; USBFS retains existing schema 1 or explicit recovery-authorized schema 2. Previously upgraded Native2/physical3 stores remain sticky; foreground execution does not downgrade them or silently add tracking.

Several observable differences remain intentional. Foreground client relay retains bidirectional half-close/drain behavior; the managed client retains its existing first-EOF policy. Native foreground IMPORT failures retain status 2/4 replies, while the managed path keeps its existing EOF policy. Virtual ready events still expose `devices`; physical ready events expose `snapshot`; Native published metadata remains a pre-export registration snapshot, not live configuration. Malformed Native stdin still emits an error and then ends the enabled control stream.

## Ownership and cleanup changes

The actual job task is retained independently of the frontend's output and signal waiters. INT/TERM requests cooperative stop; repeated signals cannot discard the task or starve collection of its completed result. The stdout thread owns only bounded bytes and a write handle. Its existing five-second send/write/flush budget remains one budget. A cancelled in-flight output prevents a second blocked final event after cleanup.

`JobOutcome` preserves operation, cleanup, and recording separately. USB/network/output failures are operation failures; durable Begin/Attempt/Resource/Available receipts and later record failures are recording failures. Successful output or an empty fresh-process snapshot cannot replace a cleanup receipt.

Rust physical teardown closes actual owners and attempts every received grant's Finish before waiting for application recording acknowledgements. Native stub teardown releases its export-use lock only after the actual helper close result, marking any cleanup failure before unlocking. A record waiter interrupted by stop retains its exact future; it is completed after resource cleanup and before subsequent Closing/Finish records. Batch export cleanup starts every bounded close task before joining them, so one pending record receipt cannot prevent a sibling's actual close from starting.

Read-only helper Status remains cancellable before capability delegation. Once an actual grant request or USBFS initializer has started, the owner is retained and awaited; a subsequent stop prevents listener bind and enters actual cleanup. The existing startup Status and live-lease TERM paths have QEMU evidence below. Stop **inside an already accepted initializer**, and kernel hard-block behavior, remain source constraints without a dedicated initializer-gate QEMU result.

## Final software evidence

The final [software archive](../evidence/shared-owner-final-software-20261024/manifest.json) records `scripts/check.sh` exit 0, unchanged inputs, formatting, default/all-feature Clippy, tests, release builds, rustdoc, and vendor tests. It contains **530 passing test executions**: 212 default + 298 all-features + 20 vendor, with zero failures or ignored tests across 39 result rows. These are executions across configurations, not 530 distinct USB cases.

The gate's recorded Git HEAD is `4d700c2`, with a modified worktree containing the foreground compatibility repair. Its 152 frozen software inputs match the Git blobs of final commit `b45c2e8c67a06396360fee0af6c0a092a950a6ff` individually by size and SHA256. The successful software result belongs to that exact input set; it is not a clean-`4d700c2` result. The software archive's contemporaneous “fixed matrix not yet executed” note predates the completed QEMU sequence below.

Additional retained evidence includes the Native-only feature check/dependency tree, the original four local signal cases, and two local output-backpressure cases. Those original local scripts exercise the plaintext virtual command and are not substitutes for the secure adapter checks.

Five real mTLS foreground-adapter cases cover ready INT/TERM, blocked-output INT/TERM, and the blocked-output deadline. Each establishes two authenticated virtual IMPORT connections with pending IN requests. The ready cases observe the final backend snapshot; the three blocked cases observe process exit and actual data-connection closure, **without** a final backend snapshot. The deadline exits with an error at approximately five seconds; the signal cases exit normally. These tests use in-memory DER and directly supplied configuration, so they do not prove the trusted file loader, privileged helper, or USB kernel path. Their earlier independent local review retains its original source/run attribution and is not relabelled as a fresh review of the final rerun.

Earlier development Clippy failures and the first full-gate failure remain preserved in the [original software archive](../evidence/shared-owner-software-20261024/manifest.json). The first full gate stopped before tests; the subsequently corrected invalid test fingerprints are not reported as executed failing tests.

## Preserved first QEMU failure

The first clean run at `4d700c22754fc5e4efb37fee391e00ff340d5829` remains **FAIL** in the [compatibility-failure archive](../evidence/shared-owner-compat-failure-4d700c2-20261024/manifest.json). Only secure virtual IPv4 ran; the other eight planned groups were not executed in that sequence.

That run established two actual Connected VHCI receipts and schema-1 import records. It then failed the original contender-denial assertion before the first USB I/O cycle: extraction reused the managed diagnostic without the word `is`, changing the foreground message from `configured remote device is unavailable or unauthorized`. Both guest process exit codes were zero, but the runner correctly returned failure. This was an observable foreground compatibility regression, not an environment failure or a successful USB cycle.

The repair restores the exact foreground message while retaining the managed message. It changes no device authorization, transport behavior, or original QEMU assertion. The original logs, source/binary guards, failed summary, and independent failure review remain intact.

## Completed QEMU regression sequence

The final sequence is recorded in `/tmp/rustusb-shared-owner-qemu-fixed-20261024/result.json` and each case's `summary.json`. All nine runs have terminal exit code 0/PASS at clean `b45c2e8`, with all **18 guest exit codes zero**. The sequence's 192 selected inputs remain unchanged. Secure foreground summaries each record 154 inputs; agent summaries each record 156. The older SIGTERM runner retains its own source fingerprint and guard format rather than receiving invented new summary fields.

| Run | Result | What the existing scenario exercises |
| --- | --- | --- |
| Secure virtual / IPv4 | PASS | HID/CDC, two actual I/O and cleanup/reuse cycles |
| Secure virtual / IPv6 | PASS | The same flow over actual IPv6 TLS |
| Secure USBFS / IPv4 | PASS | HID/FTDI, two actual I/O and cleanup/reuse cycles |
| Secure USBFS / IPv6 | PASS | The same flow over actual IPv6 TLS |
| Secure Native / IPv4 | PASS | HID/FTDI, real stub/import channels, export-baseline restoration |
| Secure Native / IPv6 | PASS | The same Native path over actual IPv6 TLS |
| Native agent / IPv4 | PASS | Operator exit, per-session stop, agent restart/root proof, subsequent I/O |
| Native agent / IPv6 | PASS | The same managed ownership scenario over actual IPv6 TLS |
| Legacy USBFS SIGTERM | PASS | Read-only startup helper wait and two-live-lease cooperative termination |

The six foreground summaries report two cycles each, HID key codes 2 and 3, and 35 serial bytes in each direction per cycle: 12 cycles and 420 bytes per direction across those six runs. Each Native-agent run reports three serial rounds, 105 bytes per direction, and HID codes 2 and 4. The SIGTERM summary reports the original 35-byte serial observations and actual cleanup checks. These small deterministic transfers verify behavior; they are not throughput measurements.

All nine summaries identify the same all-feature release CLI (`ee66f1c9…2528df01`) and helper (`cc6525a4…fccb3c2a`), with full hashes retained in the source report. The guests use Linux `6.8.0-106-generic`, kernel image SHA256 `ac8edf63…118f8`, and isolated QEMU networking. Secure/agent runs retain per-role credential payloads, actual executable receipts, external-link captures, and the original plaintext-port refusal checks. Packet summaries show encrypted application records; encrypted handshake details and certificate authorization must be correlated with the configuration/source and actual successful path, not claimed visible in ciphertext.

The native-agent cases preserve the distinction between client Connected after real VHCI enumeration and server Connected after IMPORT reply flush. Root proof is matched to the original resource/correlation; neither process restart nor new-epoch resource counts are themselves proof of recovery. Managed Rust crash-session recovery is not added by this extraction.

At this draft's cut-off, the matrix terminal results and source/ELF guards have been read, and the final software inputs have been independently compared with the final Git blobs. **This document is not yet an independent raw-QEMU audit.** Raw lifecycle/security reviews and the final QEMU archive should be attached under their own exact source attribution when delivered. No CI result is inferred from the local software or QEMU results.

## Remaining boundaries

- An indefinitely stalled preparation-stage Resource ACK after a Native export or USBFS grant has been received still leaves its owner retained and unresolved. This slice does not guarantee bounded cleanup under arbitrary WAL stalls. Returned durable errors still enter actual cleanup; a stalled ACK is not a successful ACK or terminal proof.
- No dedicated run stops inside an accepted USBFS initializer or demonstrates forced completion of a kernel hard block. The startup Status test cannot establish that result.
- Fresh untracked foreground records do not gain managed crash recovery. Existing conservative unresolved Rust sessions and bounded historical-proof retention remain unchanged.
- Certificate/ACL negative software tests are not newly performed certificate-negative QEMU scenarios. Successful configured principals do not prove every denial path.
- Hardware G1/G2, general USBFS recovery across configuration/alternate changes, ISO, same-data-channel heartbeat/RTT, log rotation, full Phase 3, Windows, TUI/GUI, performance and later phases remain unfinished. The project website is not a GUI implementation.

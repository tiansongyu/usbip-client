# Framed data-link health validation

The opt-in `rustusb-usbip/2` transport now has software and dual-guest QEMU evidence for same-connection probes, RTT observations and cooperative cleanup after selective network loss. This is a Phase 3 increment. It does not complete Phase 3, real-device G1/G2, ISO support, performance qualification or Windows USB support.

Use the [operator guide](../data-link-health.md) for configuration and `agent health`. The [design](data-link-health-design.md) records the ownership, flow-control, flush and privacy constraints.

## Verified versions

| Item | Actual version and scope |
| --- | --- |
| Management lifetime fix | `6d933d27d46c184764f339701e0f03a0610a18a4`; full replies use explicit FIN/EOF before natural driver join |
| Final QEMU source | Clean `531e0c32a4b0e7252ca771ab4f10042c71c8c195`; includes the observed network-readiness barrier |
| Software run | Actual dirty `3ca4947`; all 165 frozen Rust/TOML/lock/check inputs match the committed source individually. This mapping does not relabel the original run as clean |
| CLI | SHA-256 `bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a` |
| Helper | SHA-256 `b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5` |
| Toolchain | Rust/Cargo 1.97.1, Edition 2024; actual tool and ELF receipts are in the software archive |
| Transport dependencies | Fixed h2 0.4.20, rustls 0.23.46 and tokio-rustls 0.26.6; resolved versions and licenses are in the [inventory](../dependency-inventory.json) and [third-party register](../third-party.md) |

The final 15 QEMU runs all use the same CLI/helper bytes above. The global 213 selected inputs, each runner's selected inputs, commit and clean-tree guards remain stable during execution. All guests boot the selected Linux 6.8.0-106-generic kernel. Each run records its actual QEMU/tool versions; it does not inherit an earlier tool receipt.

## Software acceptance

`bash -x scripts/check.sh` passes its ten Cargo steps: formatting; default and all-features Clippy, tests, release builds and warnings-as-errors rustdoc; then the separate vendored nusb suite.

| Suite | Executions |
| --- | ---: |
| Default workspace | 260 |
| All-features workspace | 346 |
| Vendored nusb | 20 |
| Total | **626** |

There are 39 result rows, zero failures and zero ignored tests. The independent parser observes 366 qualified test names; 626 is an execution count across configurations, not 626 distinct cases. Zero-test documentation rows do not add executions.

The [software archive](../evidence/data-link-management-software-20261024/manifest.json) preserves actual command traces, input manifests, tool/ELF receipts and independent review. Native-only `secure-transport` check/tree also passes: it includes the application service and h2 transport, without the Rust engine, nusb or rusb. Four local signal scenarios and the original two output-backpressure scenarios pass with the same all-features CLI. These ordinary-user tests do not perform host USB operations.

The new tests cover actual TLS writes and flushes, complete DATA/FIN fences, receive-window credit, probes while application reads are blocked, half-close/drain behavior, missing replies, metadata poison, checked overflow, strict configuration/history fields, and quiet third-party diagnostics. The privacy test first captures a real unquiet unknown-ACK payload, then checks that the quiet driver suppresses its ASCII/decimal/hex representations while typed application audit remains visible.

Management regressions exercise public Rust server APIs, the actual Native handler and production discovery/client code over real loopback TCP and memory-DER mTLS. They cover Catalog, complete status-2/status-4 refusals, no helper attempt after refusal, and stopping while a peer withholds FIN. They do not stand in for trusted-file loading or physical USB; those boundaries have separate QEMU evidence.

## QEMU acceptance

All **15 runs pass**, with **30 guest exit codes equal to zero**, at the single clean final source above. Guest exit codes alone are insufficient: the runners also require their explicit success markers and actual lifecycle/network evidence.

| New framed-v2 runs | IPv4 | IPv6 | Workload |
| --- | --- | --- | --- |
| Native | PASS | PASS | QEMU HID key events and FTDI traffic |
| USBFS | PASS | PASS | QEMU HID key events and FTDI traffic |
| Virtual | PASS | PASS | HID enumeration/idle connection and CDC echo |

Each of these six runs performs three fresh targeted flows: client-to-server loss, server-to-client loss and bidirectional loss. That is **18 directional fault phases**, not 18 additional runs.

The ordinary-user valve retains its two private socketpair endpoints and drops only complete Ethernet frames matching the target connection's actual TCP 5-tuple. Each arm is bound to original health response bytes, instance/session IDs and both endpoints' process-local connection identities. There is no injected PONG, TLS proxy, process kill, TCP abort or interface-down operation to create the fault.

Before arming, both endpoints have actual matched replies on that data connection. A phase requires at least one real `probe_timeout`; an asymmetric peer can end differently after the first endpoint closes. The 1000/3000 ms configuration is unchanged. The verifier checks the same-attempt local monotonic enqueue-to-timeout observation, with the documented scheduling allowance, and separately waits for actual cleanup evidence. A deadline never substitutes for release proof.

The original serial/CDC connection survives all three faults and completes seven 35-byte rounds, 245 bytes in each direction. Native/USBFS use actual FTDI traffic; Virtual uses actual CDC echo and does not claim injected keyboard events. New Catalog requests still succeed. Each targeted HID import is actually retired before a fresh attempt is accepted; the last attempt also reaches a matched-reply barrier.

Native import/stub proofs, client VHCI NULL state and old-node disappearance are checked against the original attempt identities. USBFS per-lease cleanup is distinct from its two retained export grants: those grants remain through the faults and retire only at final cooperative Finish and baseline restoration. Virtual has no physical server-device proof. Both agents finally receive actual SIGTERM and are reaped with exit zero. Final resource proof precedes Ethernet teardown; kernel TIME_WAIT/FIN_WAIT bookkeeping is not required to vanish.

The nine existing raw-v1 regressions also pass on this final source: virtual/USBFS/Native × both IP families, two Native agent runs, and the existing USBFS SIGTERM run. Their scopes remain separate from the six new runs. Agent snapshots now strictly verify private disk2 and one-to-one `link_history`; raw-v1 observations have explicit unavailable RTT/statistics. Public IPC1 and existing Session/Metrics bodies retain their format.

The [final QEMU archive](../evidence/data-link-final-531e0c3-20261024/manifest.json) contains raw role artifacts, PCAPs, valve journals, source/build receipts and supplementary reviews. Network and lifecycle review counts are diagnostic predicates, not USB test-case counts. Reviewers include authors of portions of the harness; independent reconciliation methods are not described as a wholly independent implementation.

## Supplementary raw reconciliation

Both supplementary methods pass without importing the original lifecycle/packet verifiers. Their authors also contributed parts of the harness/product, which remains explicit.

- Network reconciliation compares 12,361 PCAP records: 6,298 source ingress packets, 6,063 byte-identical peer arrivals and 235 direction/tuple-specific drops. All 168 observed TLS directions reassemble completely. Actual process TCP tuple/UID/inode/FD/PID/start-time/ELF observations link the data flow and persistent sibling. Process snapshots are sequential, not atomic attestations; server ALPN and PING contents are encrypted.
- Lifecycle reconciliation checks all 18 fault windows. Thirty-five endpoint timeouts are observed at 3000–3002 ms; that is a timeout observation, not a USB latency benchmark. Original attempts, seven sibling rounds, exact release/reuse and final baselines match. The new and old run groups retain separate counts.
- Predicate totals include byte-by-byte overlap checks and repeated records. They are diagnostic work, not extra USB cases. Parser-development failures remain attached as parser failures, without changing original run verdicts or bytes.

## Failures retained before the final pass

1. **Clean `e1529ea`, initial Native IPv4: FAIL.** The initial Catalog returned typed I/O failure, before IMPORT or fault arming. All 44 observed packets were forwarded; no rule was armed. Two zero guest exit codes did not turn its FAIL markers into success. Source inspection and an actual public-server mTLS regression exposed the short-management lifetime problem. No decrypted RST was captured, and the original raw records do not identify the exact query/join substage. The failed test function remains byte-identical and passes after the production fix. See the [original archive](../evidence/data-link-first-failure-e1529ea-20261024/manifest.json).
2. **Clean `93428ce`, partial matrix: FAIL.** Four new Native/USBFS runs passed; Virtual IPv4 then timed out during the original port-3240 refusal probe, before Catalog. The remaining ten runs were not executed. Initial snapshots showed NO-CARRIER and later kernel LinkUp; no TCP SYN appeared. Nine socket-forward receipts are not nine proven peer-PCAP arrivals. `531e0c3` adds actual carrier/operstate/uptime evidence and a two-sided host acknowledgement before START, with another client check before the original probe. Neither `nc -w 2`, its refusal assertion nor the data-link probe policy was widened. See the [partial matrix and failure](../evidence/data-link-matrix-93428ce-20261024/manifest.json).

The earlier [608-execution software run](../evidence/data-link-software-20261024/manifest.json) and [clean e29cc11 raw-v1 nine-run pass](../evidence/data-link-compat-e29cc11-20261024/manifest.json) also retain their own source versions. They are not relabeled as final acceptance.

## Boundaries

- A h2 reply proves responsiveness of the authenticated transport endpoint on this connection. It does not prove USB/kernel progress, enumeration, driver restoration or malicious-peer honesty. The library PING payload is not a business nonce; local sequence numbers are metadata.
- RTT is local enqueue-to-library-response time, including queueing and scheduling. It is not a measured wire-transmit interval or USB request latency. Link byte counters include management traffic; existing session metrics retain their post-IMPORT contract.
- The receive pump bounds payload and returns credit only for actual application consumption. A live full window can still respond to probes. `local_data_stall` is a separate bounded drain failure after connection completion, not a peer probe timeout.
- Live observations use bounded in-memory state, not per-probe WAL writes. Private disk2 strictly migrates valid disk1, preserves unavailable raw-v1 history and qualifies old observations by epoch. A crash can leave an older checkpoint; restart does not refresh it.
- Native and managed USBFS idle/connected observations are not pending-URB proof. This interface does not expose actual USBFS pending/inflight counters. The earlier SIGTERM startup gate still does not prove helper acceptance, and its original hard-block/active-window limits remain.
- Real-device G1/G2, ISO, Windows USB and performance qualification remain uncompleted. The existing SIGKILL/recovery negatives remain. Cooperative authorized retirement does not provide FD revocation or repair an old process's lost recovery baseline.
- Some final server-side peer shutdowns are recorded as `protocol` while resource cleanup is independently `verified`. The report does not reclassify those observations as universally clean transport exits or evidence of malicious peer bytes.
- No TUI or GUI implementation is introduced by this work. The website is a project showcase, not a live management dashboard.

## CI and publication

The normal SSH push of `531e0c3` is confirmed against remote `main`. [Software CI run 40029976284](https://github.com/tiansongyu/usbip-client/actions/runs/40029976284), attempt 1, has four actual native successful jobs: Ubuntu 22.04/24.04 × x86_64/aarch64. Each full official log contains 260 default + 346 all-features + 20 vendor executions, 39 rows, zero failures/ignored tests, five memory-DER TLS scenarios and the original four signal/two backpressure scenarios.

The [CI archive](../evidence/data-link-ci-531e0c3-20261024/manifest.json) retains the full connector responses and matching decoded logs. All four 6,597-entry source inventories match the actual Git blobs and one fingerprint; 5,939 entries are archived `.rs` files, not additional compiled sources or USB cases. Actual Ubuntu 24 runner kernels expose `anon_pipe_write`, and Ubuntu 22 kernels expose `pipe_write`.

There are no uploaded workflow artifacts. Missing runner temporary files are not reconstructed: the five TLS scenarios lack per-child ELF/full before/stopped originals; the four signals contain actual ELF inode/SHA receipts; the original two backpressure checks have workflow-build provenance. These evidence strengths remain distinct. Historical CI retains its own revision and counts.

The updated English README/site is published at `f095ff51b35134c3209d49d21b77ce695c9554b0`. Its [software CI 40032354147](https://github.com/tiansongyu/usbip-client/actions/runs/40032354147) has four successful native jobs, and [Pages 40032354233](https://github.com/tiansongyu/usbip-client/actions/runs/40032354233) has successful check/deploy jobs. This later status check does not recount executions or move the detailed `531e0c3` CI/QEMU evidence to a different revision; the same 165 software inputs individually match the content commit.

All six live HTTPS resources return 200 and exactly match that content commit's Git blobs. Actual browser checks observe the new 626/15/18 text, loaded diagrams, keyboard/URL tab state and desktop/mobile layouts without horizontal page overflow or console warnings/errors. The local no-script and clipboard-policy checks remain separate; screenshot capture is unavailable and reduced-motion preference was not emulated. The [publication receipts](../evidence/data-link-publication-f095ff5-20261024/manifest.json) preserve workflow status, live bytes, browser observations and preview cleanup. The reusable global `readme-site` skill and its Codex discovery remain installed; their four-source snapshot is in the [documentation/site QA archive](../evidence/data-link-docs-site-20261024/manifest.json).

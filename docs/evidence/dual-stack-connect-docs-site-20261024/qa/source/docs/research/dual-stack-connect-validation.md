# Hostname and dual-stack connection validation

2026-10-24. Product and clean software:
`f3bf579c79d361c96c9381c74a5a151557a5756a`. Final installed checkpoint:
`67d0bb002d910d75bcb77a51abb85ade2c5d51c5`. The
[design](dual-stack-connect-design.md) records decisions, fixed primary sources
and initial failures. This advances Phase 3; the original Phase 0–7 goal remains
active.

The shared Rust connector now starts another TCP candidate while an earlier
address remains pending. It preserves OS family preference, stably deduplicates
and interleaves families, and owns all candidate futures inline. Normal launch
spacing is 250ms, shortened to at least 100ms after a definitive failure. At most
64 raw answers, 16 unique addresses and four actual queued/running resolver
closures are accepted. The real blocking closure retains its permit even after
the async waiter is cancelled. IP literals bypass resolution.

Only the TCP winner proceeds to existing TLS/ALPN/pin checks and USB operations.
The independently configured TLS service name, original total operation deadline,
stop source and root authorization remain. There is no TLS-failure retry,
protocol downgrade, duplicate IMPORT or live-stream migration. Legacy Rust
DEVLIST uses the helper too; external distribution usbip keeps its own behavior.

## Clean software and local processes

At clean f3bf579, complete `bash -x scripts/check.sh` passed:

| Execution scope | Passed | Failed / ignored |
| --- | ---: | ---: |
| Default workspace | 365 | 0 / 0 |
| All-features workspace | 457 | 0 / 0 |
| Vendored nusb library | 20 | 0 / 0 |
| Total executions | 842 | 0 / 0 |

All 47 result rows, both Clippy/release/warning-strict rustdoc configurations,
formatting and Native-only feature isolation pass. All 198 selected source/build
inputs were copied before execution and remain unchanged. The dependency
inventory remains 174 packages; Cargo.lock is unchanged. Tokio test-util is a
protocol dev feature for deterministic clocks, without a new runtime package.
Older 824 software evidence remains assigned to clean `47fcb75`.

Eight new connector tests cover answer order/dedup/scope/bounds, real literal
and system-hostname byte transfer, an actual full loopback accept queue hiding
a healthy second address, deterministic 250/100ms pacing, simultaneous-ready
owned resources, real candidate sockets before/after cancellation, actual
blocked resolver closures after waiter cancellation, and preserved OS errors.
Synthetic ready-future observations are not installed double-connection tests.
The actual-socket test observes WouldBlock before cancel and EOF afterwards.

A separate real service test exercises successful catalog and wrong service-name
refusal for raw and framed TLS using a hostname endpoint and independent
`server.test` identity. It preserves the original management close/driver join.
The local localhost answer set is OS-dependent; this is not a claim that local
NSS returned two families. Installed tests below supply that stronger evidence.

Four existing ordinary-user signal cases and two original output-backpressure
cases pass using the same prebuilt all-features CLI. The latter retains its
lack of individual executed-ELF/raw-wire receipts. Two additional real CLI
process cases at clean f4f0dc pass hostname DEVLIST and original withheld-response
deadline. They save actual proc UID/ELF receipts, request/reply/error output,
wait codes, peer EOF and joined server threads; their loopback empty catalog is
synthetic management input, without USB or multi-address-set qualification.
These two cases are separate from 842 Cargo executions and now have a native-CI
step.

The first local CLI fixture passed catalog but failed its timeout assertion by
expecting empty stdout. The existing `--json` failure contract returns a typed
error object. Its actual stdout/stderr and method remain; its negative child
wait code was not independently saved before that assertion. The corrected fresh
run requires exactly the existing error DTO and saves wait/cleanup before
judgment. The initial Clippy redundant-closure correction and source-review
unbounded-test-accept finding are also retained as development observations.

## Actual hostname fallback and regressions

All final runs use an ordinary x86_64 host, isolated QEMU guests, Ubuntu
`6.8.0-106-generic`, fresh evidence directories and the default locked
all-features release build:

```sh
python3 -B scripts/qemu_dual_stack/run.py --backend native --family ipv4 \
  --kernel 6.8.0-106-generic --evidence /tmp/NEW-EMPTY-DIRECTORY
```

`--family` identifies the healthy family. Guest-only protected hosts/gai/NSS/
host.conf settings put a stale opposite-family address first. Its permanent
nonexistent private-link MAC leaves TCP pending. No host network policy, DNS
server, TAP or physical host USB is changed. A separately built, guarded
std-only probe runs as actual UID2000 before product work, reporting its live
executable SHA, four proc UID values, absent resolver overrides and both raw
SocketAddr answers. This uses OS hosts-file resolution, not wire DNS or VPN.

| Installed scenario at clean67d0bb0 | Paired runs | Guest exits zero |
| --- | ---: | ---: |
| Raw hostname fallback: Native/virtual/USBFS × healthy IPv4/IPv6 | 6 | 12 |
| Explicit framed-v2 hostname fallback: Native, healthy IPv4 | 1 | 2 |
| Original raw default-policy secure regressions: three backends × two families | 6 | 12 |
| Independent unchanged Native/IPv4 framed-v2 diagnostic regression | 1 | 2 |
| Total successful runs | 14 | 28 |

All source/ELF/HEAD/clean guards are true. Every hostname run preserves the
original two real emulated HID/serial I/O cycles, helper refusal, kernel import
receipts, actual process waits, complete root journals and full original driver
baselines. Native/USBFS use emulated HID/FTDI; virtual uses its fixed HID/CDC ACM
profiles. The separate diagnostic run retains its actual WAL gate, complete
private export and second I/O round before release; it is not another hostname
or installed admission-RPC test.

Raw PCAP files remain unchanged. A named healthy projection excludes only exact
empty SYN packets from the private client to the stale address/nonexistent MAC.
Every retained packet record is byte-identical. The original complete TLS
classifier checks that projection, with an enumerated expected ALPN (raw1 is its
unchanged default; the separate v2 case requires2). Exact healthy IP/MAC tuples
and thirteen first-SYN pairs are observed per hostname run. Equal-count sorted
chronology establishes aggregate ordering within the original deadline, without
per-process/per-operation packet attribution or latency benchmarking.

## Failures, NSS diagnosis and valid old-product comparison

Six original host FAILs remain separate from the fourteen successful runs:

| Original checkpoint / experiment | Observation and retained boundary |
| --- | --- |
| 5b26bba old-product first | BusyBox ip rejects neigh replace before hostname dial; setup failure |
| ca63bc4 old-product second | Discovery timeout/stale SYNs, but actual candidate list was not captured |
| b5d3e92 new-product raw and separate v2 | Two discovery timeouts/stale SYNs, without a captured candidate list |
| 636efa root diagnostic | Probe returns exactly one stale address; host.conf absent; root-only scope |
| Detached 0d116f0 corrected old product | UID2000 probe returns both candidates; old connector sends six stale SYNs and no healthy SYN, then times out |

Each retains host FAIL despite both guest exits zero and all four guards true.
The first five do not establish a valid two-candidate old/new comparison. Fixed
Rust 1.97.1 source and glibc 2.35 NSS source explain why missing host.conf can stop
at the first hosts entry. The actual root probe supports that diagnosis, without
proving the product process's own answer list. The exact Ubuntu patched-source
fetch returned 404; its patch set was not audited. New guest-only `multi on` is
installed before fresh lookup processes, with protected readable-file receipts
and absent environment overrides. It must not be transplanted into host policy.

The valid old comparison uses a clean detached sparse checkout at
`0d116f0c85db40b24b8c1c103b9b6ec4b82c3ee2`: corrected fixture `67d0bb0` is retained,
six product paths are restored to ca63, and all 196 old source/build inputs map
exactly to clean `47fcb75`. Default locked build and actual executable receipts yield
the same old CLI/helper hashes as that earlier build. The direct raw capture
and actual UID2000 two-answer probe establish the sequential timeout here.
This is a real old-product negative, without a new old-software execution or
successful USB-case claim. Its patch, commit metadata and small Git bundle
(parent 67d prerequisite) are archived; the temporary checkpoint is not a
published product branch. Initial empty sparse-index preparation is documented
separately from QEMU outcomes.

## Provenance and preserved scope

New all-features CLI SHA-256:
`29d0d476e7b7ab21ae57fea2e2a049301ebbc2453469321d1ce3a0c5dc60e8ef`.
New helper SHA-256:
`75e3de91607fe6cb47b00762839b50a3ce839c0cf415939356633ad58653b338`.
Old counterfactual CLI/helper remain
`877946fd141c6acee5e30b930cd49bdf77ba9f7573333cd4f19f87b1431ef9fd` /
`7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322`.
Probe executables have their own source/compiler/build/live-SHA receipts;
executable bodies and private keys are not uploaded.

The seven hostname runs select 207 inputs, including all 198 software inputs;
the independent diagnostic run selects 215. The six original default runners
select 201, including 196 software inputs, omitting rust-toolchain.toml and
scripts/check.sh. These omissions are explicit rather than invented runtime
selection. All 198 are separately mapped to the installed fixed Git checkpoint.
A same-author read-only method imports no original runner/parser/verifier:
4,949 original files remain byte-identical, 1,695 base64 blocks are redecoded,
selected Git blobs and actual probe receipts are checked, and both packet
projections are independently reconstructed. This is not another execution,
an independent security audit or another TLS classifier verdict.

Research, component reviews, software, local processes, all six failed runs,
fourteen successful runs, raw methods and the old-checkpoint bundle are retained
in [the evidence archive](../evidence/dual-stack-connect-67d0bb0-20261024/manifest.json).
The published README/site summarize current evidence; earlier admission,
diagnostic and recovery reports retain their own revisions.

This is RFC-inspired TCP fallback after one completed system lookup, without
independent asynchronous AAAA/A queries, NAT64 policy or full RFC 8305 compliance.
Actual resolver work may outlive its waiter and delay runtime shutdown; retained
limits do not bound std/libc/OS allocation or lookup duration. No wire-DNS, VPN,
performance, general outage recovery, hot revocation, full G3, real hardware G1/G2,
TUI/GUI, packaging or Windows completion is claimed. The full goal stays active.

## Documentation and site checks

The English README and existing project page now summarize 842 software
executions, 14 paired runs and 28 successful guest exits, with direct links to
this report and the earlier capability reports. The global `readme-site` skill
and five CSS/JavaScript/SVG runtime assets remain unchanged.

Local browser checks at 1280×720 and 390×844 observed the new metrics and report
link, loaded images and no horizontal overflow. ArrowRight and Home retained
selected-tab/focus behavior; the URL hash remained empty. The first mobile
observation still used the desktop SVG, then the mobile SVG loaded. A separate
`script-src 'none'` CSP preview kept all three backend panels and their commands
visible at its observed 1280-pixel width. Console queries returned no warnings
or errors. The screenshot API returned `Unable to capture screenshot`; this is
a DOM/layout check, without a pixel review. Clipboard and reduced-motion tests
were not repeated. Temporary tabs were closed, the viewport override reset, and
the owned loopback preview exited with code zero. Local HTTP resource bytes were
not separately fetched in this pass; publication bytes will be checked against
the content commit.

[Documentation and browser records](../evidence/dual-stack-connect-docs-site-20261024/manifest.json)
preserve the checks, maintained sources and the explicit observation limits.

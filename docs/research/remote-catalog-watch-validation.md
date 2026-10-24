# Authenticated remote catalog observation: validation

The shared service now owns continuous remote catalog observation, used by
`agent discover PROFILE --watch` and `secure-discover --watch`. It exposes complete
latest views, typed stale/terminal conditions, stable-key changes and actual local
shutdown. The [guide](../remote-catalog-watch.md) explains caller roles and machine
output; the [design](remote-catalog-watch-design.md) records the contract.

This is progress toward the original remote-list requirement, not full Phase 3,
automatic sharing, physical-generation detection or TUI/GUI completion. Runs use
UTC 2026-10-24 (continuing into 2026-10-25 in Asia/Shanghai); filenames retain the
UTC date. Prior software/QEMU claims retain their original revisions.

## Exact software

Clean `ec91c4a678f9d81b002a8df44220277bb8115511` passed all ten normal default and
all-feature formatting, Clippy, workspace tests, release and rustdoc commands,
plus the locked vendor library tests. The
[software summary](../evidence/remote-catalog-watch-633b2a3-20261024/software-summary.json)
retains command arguments, exits and before/after hashes of 223 selected source,
manifest, toolchain, vendor and check-script inputs.

| Test execution scope | Actual passes |
| --- | --- |
| Default workspace | 394 |
| All-feature workspace | 489 |
| Locked nusb vendor library | 20 |
| Total | 903; zero failed or ignored |

Nine new library test functions cover stale retention and terminal permission
clearing, real empty success, key/address/metadata changes, duplicate/malformed
bounds, retry/revision limits, skipped subscriptions and terminal publication.
Actual loopback peers exercise raw-v1 and framed-v2 multiple responses, a refused
TCP connection and recovery, reverse-FIN-held shutdown, real socket EOF and owner
quota release after Drop while metadata subscriptions remain. Three CLI tests
cover bounded complete frames and argument/one-shot compatibility. These functions
and their subscenarios are included in the Cargo counts, not extra USB cases.

Existing USB/IP, agent RPC and private disk schemas are unchanged; the new CLI
stream has its own versioned batches. No dependency was added. `RemoteDevice`, `RemoteInterface`
and `CatalogDevice` gain equality for comparisons; existing serialization is
unchanged. One-shot output remains unchanged. The direct-watch configuration-load
signal path was corrected by source review, while the old one-shot cancellation
error stays intact; no new deterministic startup syscall gate was executed.

## Exact installed QEMU

Both final paired runs use clean
`633b2a3eb4d73601fde320e92056e5eb670bf5b2`, normal locked all-feature builds,
221 frozen inputs and all four source/binary/head/clean guards:

| Focused Native fixture | Result | Actual QEMU exits |
| --- | --- | --- |
| IPv4 | Passed | Server 0, client 0 |
| IPv6 | Passed | Server 0, client 0 |

The [IPv4](../evidence/remote-catalog-watch-633b2a3-20261024/qemu-v4-final-summary.json)
and [IPv6](../evidence/remote-catalog-watch-633b2a3-20261024/qemu-v6-final-summary.json)
summaries preserve original guest artifacts, provenance and observations. The
installed release bytes equal the successful software build:

```text
all-feature rustusb        46b2a0fbb940f40ff0666bd25df99ee587c73d919366bb7a8d770d5bea88f80d
all-feature rustusb-helper 10add0aad11b0563f908b21294c9e50da1e3b8fb970cd88b340db1cdc3dfb351
default rustusb            4e0e443a8955c50a3e5227757d1594e3496329c106d7fcca620303297855a14d
default rustusb-helper     fbb62c21ef1cc7395bc7532e971abe04e8a3e299ae33efd46b9f7a5c62f8d75e
```

Each pair retains the original managed Native fixture's 264 assertions, two
agent epochs, three HID/FTDI I/O rounds, 105 serial bytes in each direction,
exact root proofs, unchanged full driver baselines and ciphertext classifier.
The extension adds 69 assertions per pair, not 69 USB cases. It changes no
original verifier requirement.

An actual UID1000 watcher, with no process capabilities, observes eight complete
batches. Saved points independently match their original raw begin/device/change/
end rows. One observer UUID, PID and kernel starttime survive this sequence:

1. Current two-entry inventory before imports.
2. Fresh one-entry HID inventory while serial remains exclusively reserved.
3. Actual guest-only SIGSTOP around the original client-agent SIGKILL; after
   SIGCONT and before restart, stale data retains that same HID entry without
   removal changes.
4. The new agent's successful read sees both exports and requests resynchronization.
5. Original remote export sessions are actually stopped individually: serial-only,
   then a successful empty catalog through the still-running listener.
6. TERM and real wait 0, with the local observer-joined receipt. The original
   server restart and final I/O/cleanup then proceed.

Busy entries are intentionally hidden by the existing Native catalog. The
one-entry pre-kill view is not a physical unplug; stale data does not become
current merely because the old serial owner later ends. New-process resource
counts are never used to retire old sessions; the original root-proof checks
remain mandatory.

Each pair also runs four installed CLI cases, separately from the long-lived
watcher: protected direct TLS loading as UID2000 plus TERM; an actually full
write-only FIFO/blocked write syscall and INT; closing the only output reader and
actual exit 1/Broken pipe; and UID1002 terminal denial with absent inventory.
The full-output case records a completed remote discovery while stdout remains
blocked, and never drains it before wait. Direct/full/closed children and the
long-lived observer have actual live ELF/UID/capability receipts. The fast denied
child has its real exit/body and kernel-UID denial audit, **without** a separate
live ELF sample. No private key bytes are archived; UID1000 cannot read the
broker's protected key.

The original four server signal and two backpressure process regressions pass
with the same all-feature CLI. Three actual old local-device-observer cases also
pass; their host raw `.private` output is excluded from the public archive. The
older backpressure script lacks per-child live ELF receipts; its selected binary
is recorded before/after without inventing those missing samples.

## Failures retained

- Clean `6139056` first full gate passed default checks but failed all-feature
  Clippy with exit 101 because a real `run` item followed the new test module.
  Moving the module, without suppressing the lint, fixed it. The original
  223-input snapshot and logs remain a failed gate.
- Clean `8daf8ecc` first IPv4 QEMU failed overall. Empty jq output exited 0 and
  the fixture incorrectly accepted it as a point, then an empty revision caused
  `--argjson` failure. The original empty saved file and raw stream remain; real
  stale-retained product frames do not turn this run into PASS.
- Clean `febd5e99` second IPv4 QEMU failed overall after the strict empty check
  correctly rejected missing points. The fixture incorrectly demanded two
  visible entries while serial was reserved. Actual repeated successful catalogs
  and the fixed Native `Export::accepting` code established the correct HID-only
  expectation. Tests now require that exact key/count; production filtering was
  not changed to satisfy the test.

Both failed QEMU runs record guest exits 0 and true guards; those observations do
not override scenario failure or prove all original cleanup assertions ran.
The nine-case rootless preflight executes the actual guest point function with
temporary paths and one poll, covering empty/partial/mixed/count/revision cases.
Earlier seven-case and four-filter checks stay separately recorded. These are
fixture checks, not Cargo or USB executions. The initial missing test-module fmt
failure has a tool receipt but no separately saved raw output; no file is invented.

## Scope and evidence

The [manifest](../evidence/remote-catalog-watch-633b2a3-20261024/manifest.json)
indexes a compressed closed set of original sources, logs, methods and failures.
The [same-author review](../evidence/remote-catalog-watch-633b2a3-20261024/review.json)
checks source Git blobs and original bytes and redecodes guest Base64 blocks
without importing the original runners/verifiers. It is not a new USB execution
or an independent security audit. Extract the archive into a fresh directory;
its manifest provides per-member hashes and lengths.

Two local observers per process, at most 16 records, bounded frames and backoff
bound this implementation's work. They do not guarantee fairness, indefinite
audit retention, all transient event delivery or a shutdown deadline under a
permanently blocked OS/filesystem/libc call. Agent polling still commits each
successful discovery and can consume audit history. A join does not prove an
accepted remote read, a detached resolver or any USB device has been retired.

Installed QEMU evidence is Native/raw-v1; framed-v2 observation is tested through
real local loopback peers. These results do not establish a new virtual/USBFS
forwarding matrix, hardware generation tracking, physical G1/G2, complete G3,
automatic sharing, TUI/GUI, packaging, performance or Windows support. Publication
and any later CI are recorded separately from these local runs.

## Documentation and site check

The [documentation receipt](../evidence/remote-catalog-watch-docs-20261024/manifest.json)
retains 15 source snapshots, 442 local/repository-mapped links and the actual
194-check site validation, JavaScript syntax and whitespace exits. An initial QA
method used the wrong summary filename and exited 1; the corrected method passed.
All 223 software and 221 QEMU selected inputs still match their tested bytes.
The four global `readme-site` files and its Codex discovery symlink remain unchanged.

Actual CUA browser checks at the repository subpath verified updated remote links
and copy, loaded SVGs, desktop 1280×720 and mobile 390×844 layout, no horizontal
text/card overflow, and ArrowRight focus/selection for backend tabs. A separate
HTTP-CSP script-disabled preview kept all three panels, build commands and new
remote evidence readable. Console warning/error lists were empty. Screenshots
were unavailable, so no pixel review is claimed. CSS, JavaScript and SVGs are
unchanged; earlier clipboard/reduced-motion checks were not repeated. Temporary
preview tabs and the loopback server were closed and viewport settings reset.

## Published content and CI

Content `9e65763705aa48f4a8085ecb6f8b938fea4b7305` was normally pushed over SSH,
with a clean tree and matching remote main.
[Software run 40114160923](https://github.com/tiansongyu/usbip-client/actions/runs/40114160923)
completed successfully on all four native Ubuntu 22.04/24.04 x86_64/aarch64 jobs
(120931897631, 120931897761, 120931897823, 120931897834).
[Pages run 40114161007](https://github.com/tiansongyu/usbip-client/actions/runs/40114161007)
completed check 120931897786 and deploy 120931982014 successfully.

The [publication receipt](../evidence/remote-catalog-watch-publication-9e65763-20261024/manifest.json)
retains primary run/job API responses, polling, exact HTTP bodies/headers, the
content push and verification methods. Software step details were null/empty in
these responses; an attempted direct job-detail endpoint was rejected by the
connector. Neither missing steps nor full CI logs/per-platform ELF/test recounts
are invented. The four successful job conclusions are verified separately from
the local 903 executions at `ec91c4a` and paired QEMU runs at `633b2a3`.

Six canonical HTTPS resources returned 200 and exactly matched content Git blobs.
Actual live CUA reload/AX/DOM checks confirmed the new guide links and 903-scope
copy, loaded local SVGs and an empty warning/error list at 1280×720. There is no
pixel review. The 223 software, each 221 QEMU and four global skill source files
still match; the later receipt-only commit does not relabel their tested revisions.

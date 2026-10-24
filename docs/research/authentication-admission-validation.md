# Authentication admission: validation

2026-10-24. Product `47fcb752607dfd25d022b9d2860099efd51c2b76`;
installed checkpoint `d619b22bb1db0983e7dad197e2bfbc79d7dd924a`.
The [design](authentication-admission-design.md) defines the quota, insertion
point and ownership. This advances Phase 3; the complete Phase 0–7 goal remains
active.

Every loaded secure server configuration now shares a bounded, nonwaiting GCRA
authentication gate across its clones and successive jobs. The default admits
64 initial connections and replenishes one cell per 125ms. Root-trusted server
schemas 1 and 2 accept a strict optional `authentication_limit` object; clients
cannot override it. The existing sixteen connection permits remain separate.
Failed, cancelled or closed handshakes do not refund admission. Established
streams and USB operations do not revisit the gate.

A separate metadata-only `AdmissionMonitor` reports exact policy, admitted and
limited totals, saturation and consistency. Foreground `server_admission` and
`agent --config FILE server-admission PROFILE` expose it. Actual peer/profile
Read authorization precedes sampling; authorized results bypass WAL and denied
reads remain durable. Existing TLS/framing/outer-limit counters, Session,
Metrics, disk and diagnostic-bundle schemas are unchanged. This temporal gate
is distinct from durable USB admission before a device claim.

## Software, dependencies and local processes

The complete clean `47fcb75` `bash -x scripts/check.sh` passed:

| Scope | Passed | Failed / ignored |
| --- | ---: | ---: |
| Default workspace | 356 | 0 / 0 |
| All-features workspace | 448 | 0 / 0 |
| Vendored nusb library | 20 | 0 / 0 |
| Total executions | 824 | 0 / 0 |

Both formatting/Clippy/release/rustdoc configurations and the Native-only
feature-isolation check passed. All 196 selected Rust/build inputs were copied
before execution, remained unchanged and match the installed checkpoint. Later
harness changes do not relabel these software checks as a new execution.

New deterministic tests check exact burst/refill boundaries, noncharging
refusals, concurrent clones, policy bounds, saturation, poisoned metadata and
monitor lifetime. Real raw TLS checks zero transport I/O on rate refusal,
returned permits, no refund after failure/cancellation, later authentication
and continued bidirectional I/O on the original stream. A separate real framed
connection retains its existing data flow after a new connection is refused.
Trusted configuration tests cover both schemas and invalid/client overrides.

Existing real Unix-IPC/foreground tests now also observe admission. Read-only
metadata completes while an actual Store commit is held; denied and unknown
profiles preserve their existing authorization paths. Eighty failed local TLS
connections reconcile admitted/refused decisions with unchanged TLS counters.
The original sixteen stalled connections, outer refusal, two job starts and
raw/framed shutdown behavior remain covered. These software fixtures do not
claim installed managed-agent admission RPC or kernel USB validation.

The same all-features CLI passed four local signal and two original output-
backpressure scenarios at clean `9561133`. The backpressure fixture retains its
lack of independent executed-ELF/raw-wire receipts for each child. A development
Store test initially failed on its obsolete zero-TLS-attempt expectation after
adding eighty connections; that assertion was corrected to the actual equations.
The design/work log preserve this dirty fixture failure.

Governor is pinned to MIT `0.10.4`, std-only, with explicit `MonotonicClock`.
Fourteen packages were added to the lockfile; the generated inventory contains
174 package/license records with no missing license metadata. The Governor
subtree excludes quanta, dashmap, rand and getrandom; TLS/ring's random source is
unrelated. Fixed-source research, actual isolated Rust 1.97.1 probes and a
read-only component review are archived separately from product acceptance.
The upstream u64-nanosecond clock horizon and broken-clock limits remain explicit.

## Paired QEMU exhaustion and regressions

Each actual run starts from a clean checkout, performs the default locked
all-features release build, uses an ordinary x86_64 host and the Ubuntu
`6.8.0-106-generic` guest kernel, and writes a fresh directory:

```sh
python3 -B scripts/qemu_authentication_admission/run.py --backend native \
  --family ipv4 --kernel 6.8.0-106-generic --evidence /tmp/NEW-EMPTY-DIRECTORY
```

At clean `d619b22`, all six `native|virtual|usbfs` × `ipv4|ipv6`
raw-v1 exhaustion combinations passed, with twelve guest exits zero. The
six original `scripts/qemu/run-secure-qemu.py` default-policy combinations also
passed, with twelve guest exits zero. One separate unchanged Native/IPv4
`scripts/qemu_diagnostic_export/run.py` framed-v2 regression passed with two
guest exits zero. It retains the real WAL gate, complete private export, second
actual HID/FTDI round before release and full original cleanup; it is not a
framed-specific exhaustion or installed admission-RPC test. Total: **13 paired
runs / 26 successful guest exits**, with all four source/ELF/HEAD/clean guards true.

Actual probe totals across the two epochs of each exhaustion run:

| Backend / family | Client wait rows | Admitted probes | Rate refusals |
| --- | ---: | ---: | ---: |
| native / ipv4 | 2416 | 47 | 2369 |
| native / ipv6 | 2414 | 48 | 2366 |
| virtual / ipv4 | 2416 | 46 | 2370 |
| virtual / ipv6 | 2378 | 46 | 2332 |
| usbfs / ipv4 | 2409 | 47 | 2362 |
| usbfs / ipv6 | 2372 | 47 | 2325 |

TLS-budget and outer-budget probe deltas are zero in these six runs. These
counts describe the bounded fixture, not a throughput or latency benchmark.

The new fixture preserves the original authentication suite's two credential
epochs, four free/held refusal barriers, direct unauthorized IMPORT status 4,
authorized duplicate status 2, startup retirement, actual I/O and complete root
terminal proofs/driver baselines. It adds quota 32/500ms and eight actual UID2000
BusyBox workers per epoch, repeatedly running the copied OpenSSL client with a
valid but unmapped leaf for eight guest uptime seconds. Every attempt has a
bounded real wait result; every worker is joined. Proc UID/start/BusyBox ELF
samples establish the long workers. Fast OpenSSL leaves lack individual
executed-ELF samples; copied executable/library hashes are retained.

The host permits original HID/serial input only after observed rate refusals.
Original I/O must finish while probe workers are alive, and refusal totals must
progress afterwards. The same original process generations, import journal and
VHCI identity remain. After every worker exits and TLS work drains, a real
authorized discovery after replenishment succeeds on that same configuration:
exactly one admission and one TLS authentication, no extra rate refusal. Its
empty catalog means the two devices remain busy; it does not substitute for I/O.
Original close, wait, root proof and full baseline checks remain mandatory.

Actual child wait rows reconcile independently with new admissions, rate
refusals, unchanged TLS-budget refusals and outer-listener refusals. Admitted
probe connections reach the unmapped-leaf check. Rate refusals do not inflate
the unchanged TLS-attempt count. Wait rows and predicate counts are not software
or USB case counts. Mixed PCAP files are preserved without a new ciphertext-
classifier verdict.

The first Native/IPv4 run at clean `9561133` remains **FAIL**. Both guest exits
were zero and all four guards true, but the new host checker tried to read
`payload_sha256` before the base runner persisted its final in-memory summary.
The original authentication checker had completed; new admission checks had
not. `d619b22` writes a separate provenance receipt during actual payload
construction, before verification. The fresh second run uses that receipt; old
evidence is neither filled in nor reclassified. Product Rust code is unchanged.

## Provenance and limits

The all-features CLI SHA-256 is
`877946fd141c6acee5e30b930cd49bdf77ba9f7573333cd4f19f87b1431ef9fd`;
helper remains
`7c690c62dbc02b7e08b224e2f3cfe773c77bebc6a8e6c10ebd716b2696806322`.
A separate same-author read-only method imports no original runner/verifier:
8,133 original files remain byte-identical and 4,971 artifact blocks
are redecoded against saved bytes. All selected run inputs match exact Git
blobs. The exhaustion runs select 210 inputs and the diagnostic regression 213,
each containing all 196 software inputs. The original default-policy runner
selects 199 inputs, containing 194 software inputs; it omits
`rust-toolchain.toml` and `scripts/check.sh`. A separate post-execution Git-byte
mapping covers all 196, without inventing runtime selection of those two files.
Actual probe/TLS deltas are recalculated. The first review helper assumed the
missing fields; the second shadowed an inventory variable. Both failed methods
and observed-result notes are retained; the corrected final method passes.
This is provenance checking, not an independent security audit or new execution.

Raw research, clean software, local processes, static fixture checks, the first
host FAIL, final paired runs and the read-only method are retained in
[the evidence archive](../evidence/authentication-admission-d619b22-20261024/manifest.json).
Private keys and executable bodies are excluded.

This is an aggregate per-configuration work bound, without per-source fairness
or a sustained-attack/performance qualification. One peer can exhaust the shared
quota. Installed managed-agent admission RPC, framed-specific exhaustion,
hardware G1/G2, complete G3, hot revocation, persistent security history and
general crash recovery remain open. Local real-IPC/framed tests and a separate
framed-v2 regression retain their own scope. No TUI, GUI, packaging, performance
or Windows completion is implied.

## README and site verification

The English/Chinese README and existing static showcase link the new admission
scope beside its earlier evidence. Maintained-source links, tables, site assets,
Node syntax and whitespace checks pass. Six project-prefix local HTTP resources
match source bytes. Actual CUA checks at 1280×720 and 390×844 observe the new
link, loaded images and no horizontal overflow; ArrowRight/Home preserve tab
selection, focus and URL. A temporary identical response with CSP
`script-src 'none'` leaves all three backend instructions and evidence readable.
This blocks page scripts rather than disabling JavaScript browser-wide.

Normal and CSP-page console warning/error queries are empty. Screenshot capture
again returned `Unable to capture screenshot`; no pixel review is claimed.
Reduced motion and clipboard behavior were not rerun. Five runtime assets are
byte-identical to the preceding publication, and the four existing global
`readme-site` skill sources are hashed without modification. Both temporary tabs
were closed, the viewport reset and both owned preview server processes exited
zero. Initial pending-manifest and mistyped HTTP-helper-path checks are retained
as intermediate QA observations. CI/deployment/live verification is recorded
after an actual push; local checks alone do not prove publication.

## Published content and CI

Normal SSH push published `5845b14ba41b6d112cf264845e5beecbb7533ef3`.
Its [four native CI jobs](https://github.com/tiansongyu/usbip-client/actions/runs/40084212856)
all completed successfully, including each actual native-architecture and full
software-gate step. The [Pages check and deployment](https://github.com/tiansongyu/usbip-client/actions/runs/40084212885)
also succeeded. These are final primary API run/job/step statuses; full CI logs
and CI executable bodies were not fetched or independently recounted. The 824
execution count remains the actual local clean-47f run, and the thirteen paired
QEMU runs remain clean d619. All 196 selected software inputs match published
5845 bytes without being relabeled as another execution.

All six canonical HTTPS resources return 200 and exactly match that content
commit's Git blobs. Actual live desktop/mobile DOM checks find the new evidence
link, loaded responsive images and no horizontal overflow. The first immediate
mobile resize sampled an image before completion; the settled observation
retains both complete images. Viewport overrides were reset and the public tab
kept as the deliverable. The [publication archive](../evidence/authentication-admission-publication-5845b14-20261024/manifest.json)
preserves the original push, API, source mapping, HTTP bodies/headers and browser
observations. A subsequent documentation receipt does not change the tested
product or published site bytes.

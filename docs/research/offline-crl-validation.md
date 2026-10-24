# Offline CRLs: exact validation

Both TLS roles can now require bounded, complete offline certificate revocation
lists. Standard WebPki still authenticates chains, CRL signatures and revoked
serials; a small metadata admission/checked-clock boundary rejects ambiguous or
out-of-window lists. The [guide](../offline-crl.md) explains configuration and
renewal; the [design](offline-crl-design.md) records decisions and limits.

This advances Phase 3 revocation, not hot revocation, all PKIX distribution modes,
hardware acceptance or the full Phase 0–7 goal. Evidence names use UTC 2026-10-24;
work continued into 2026-10-25 Asia/Shanghai. Earlier reports retain their tested
versions and verdicts.

## Exact software and dependency scope

Clean `fcb487cf242d968ef5a75f375b91f8e2ad82897c` passed the ten normal default and
all-feature formatting, Clippy, workspace test, release and warning-strict rustdoc
commands plus locked vendor-library tests. The
[software summary](../evidence/offline-crl-e441bb8-20261024/software-summary.json)
records arguments, actual exits and 226 selected before/after input hashes.

| Scope | Actual passes | Failed / ignored |
| --- | ---: | --- |
| Default workspace | 406 | 0 / 0 |
| All-feature workspace | 501 | 0 / 0 |
| Locked nusb library | 20 | 0 / 0 |
| Total executions | 927 | 0 / 0 |

Twelve new transport test functions cover bounded metadata/extensions, actual
raw-v1 and framed-v2 TLS, loaded-file policy and clock behavior. Real handshakes
check revoked clients, servers and intermediates despite correct pins, missing
issuer coverage, wrong issuer/key and modified signatures. Two issuer lists
permit a valid intermediate chain; omission of either rejects it. Client-local
TLS1.3 completion is never substituted for server acceptance.

A test-only inner clock crosses the actual parsed common CRL window on both
TLS roles. New handshakes fail at expiry and after rollback, including when an
unused issuer's list controls the boundary. The original real stream continues
bidirectional I/O. Repeated valid connections remain full handshakes. Actual
loopback TCP/framing drivers also transfer data and join after stop.

Both trusted JSON schemas reject explicit null/empty, duplicate fields, bad
paths, malformed/overlapping lists, permissions, symlinks, hardlinks and oversized
reads. Loaded schema-1/schema-2 endpoints perform actual TLS before/after CRL file
replacement: old snapshots still accept, newly loaded snapshots reject. Ordinary
filesystem fixtures substitute a test trust root; they do not authorize production
/tmp. Subscenarios are included in the Cargo counts, not additional USB cases.

Pinned x509-parser 0.18.1 is now a direct normal transport dependency, with default
features disabled. It was already locked but inactive before this feature. No
external package version/checksum changed; the regenerated full-feature inventory
remains 175 packages with no missing license metadata. Actual release rustc
fingerprints show parser features []; its 30-package normal subtree is not 30 new
locked packages or a claim that the whole product has no crypto dependency.
The default Native CLI's actual normal tree excludes transport/x509-parser/rustls.

## Research and ownership

Three independent read-only component tasks examined pinned rustls 0.23.46 /
webpki 0.103.16, rcgen 0.14.10 and x509-parser 0.18.1. The closed research subarchive
retains 116 reviewed files, source/license hashes, primary responses and actual
isolated probes. Bare WebPki accepts future thisUpdate, and an older empty CRL
can shadow a later same-issuer revocation. The implemented checks address both;
metadata never replaces WebPki cryptography. Research observations are not product
or USB executions, nor an independent security audit.

CRLs are 1–16 complete DER lists, each at most 64 KiB and 256 KiB total, one per raw
issuer DN. Standard full-chain/unknown-deny/expiration checks stay enabled. Both
configs use a checked actual SystemTime provider constrained to the common
thisUpdate/nextUpdate window; no insecure/custom certificate verifier is installed.
Client/server resumption and early data remain disabled. PKI, server name,
principal pins, device ACL, authentication admission and helper policy remain.

The agent loads all profiles at startup into shared immutable credential Arcs.
Restarting a job alone does not reload CRLs; the whole owning agent must stop and
restart. Existing streams are not revalidated or terminated by replacement or
expiry. Signatures/issuer relevance are checked when needed during real peer
verification, not authenticated for every unused list at file load.

## Installed paired QEMU

All final runs use clean `e441bb89086b13a95ffb0964ee46d35c7ed2ee32`, the normal
locked all-feature release build and four true source/binary/HEAD/clean guards.
The selected release bytes equal the successful software build:

    all-feature rustusb        d941b677260f6c431148fbe73e568c165e3a4d1543582db7b4e79519b6a4ff63
    all-feature rustusb-helper 594cda8d3effef0a6b1b121f3dab84beaf54c8f430a71e9aec5b60c3355afbda
    default rustusb            76352db4c5f1f7d311f1033a7cba0805e2d843558146e754138f326f2f778240
    default rustusb-helper     20b6c9a7ab5058bcddb7a102069176ab963aa001589076f9630d9947671c7824

The [fixture](../../scripts/qemu_crl/README.md) composes the original authentication
suite, retaining its two actual server generations, four hostile barriers,
HID/serial I/O, original resource identities, real waits and full driver/VHCI
baselines. An ordinary x86_64 host never passes through or mutates host USB.

| CRL combination | Original assertions | Added assertions | New CLI waits | Guest exits server / client |
| --- | ---: | ---: | ---: | --- |
| native-ipv6 | 337 | 225 | 44 | 0 / 0 |
| native-ipv4 | 337 | 225 | 44 | 0 / 0 |
| virtual-ipv4 | 320 | 221 | 44 | 0 / 0 |
| virtual-ipv6 | 320 | 221 | 44 | 0 / 0 |
| usbfs-ipv4 | 330 | 225 | 44 | 0 / 0 |
| usbfs-ipv6 | 330 | 225 | 44 | 0 / 0 |

Each of six CRL pairs freezes 226 selected inputs. The original 337/320/330 and
added 225/221/225 predicates are assertions, not USB cases. Every pair adds four
separate CRL barriers after the original ones, so original counter equations and
actual I/O requirements remain intact. Each new barrier has eleven actual
UID2000 CLI waits: six fail before connecting and five reach TLS. Before/after
server snapshots resolve to zero in-flight, with exact five-attempt deltas:
epoch one two authenticated/three failed; epoch two one authenticated/four failed.
No unmapped, timeout, cancellation or quota result substitutes for CRL rejection.

An always-revoked leaf remains pinned in both policies. A second leaf also stays
pinned/authorized, succeeds in epoch one, then fails only after the real server
restart loads its new CRL. The file is already replaced during held epoch one;
the old PID/starttime still accepts it, proving replacement is not hot reload.
Actual server UID/capability/ELF receipts, original helper state and live imported
identities are checked. New fast negative CLI children lack individual live ELF
samples; fixed actual launch/configs, real waits and long server/helper receipts
remain, without inventing missing samples.

Client cases separately reject a revoked server and bad CRL signature; expired,
future, malformed, overlapping, empty and null inputs fail loading. Independent
host OpenSSL checks establish signed fixture serial membership. Only public
certificate/CRL DER and inspection are archived; private keys stay in disposable
role-separated temporary payloads.

Six unchanged default-policy secure backend/family regressions also pass at the
same clean head (216 selected inputs each), retaining the original positive
ciphertext classifier, IPv4/IPv6 plaintext-port refusal and actual I/O/cleanup.
One unchanged Native/IPv4 framed-v2 diagnostic regression passes (230 selected
inputs), including its actual WAL gate, complete export, second I/O before gate
release and original root proofs. That regression does **not** load CRLs and is
not an installed framed CRL-refusal matrix.

Thus final evidence is **13 paired runs / 26 actual guest exits 0**: six new CRL,
six default-policy and one separate v2 regression. Reproduce with fresh evidence
paths and the commands in the archived matrix method; no --skip-build is used.

## Failures and source scope retained

Development compile failures include Oid lacking Ord, a tuple-returning async
fixture using ?, an invalid repeated-array JSON macro and mistaken test-only Oid
imports/extension constructor arguments. Final code fixes them without suppressing
lints or deleting tests. The initial check has only the tool receipt and saved
failing source, not a separately captured raw log; later development output exists.
Independent research compilation/tool failures also keep their original records.

At clean `47c0021`, an earlier Native/IPv4 pair passed separately. The first IPv6
pair failed overall: its inherited absent-ALPN OpenSSL probe returned 104 with
read:errno=104 while stdin contained an application request. Both guest exits 0 and
true guards do not complete its missing barrier or cleanup assertions. It remains
FAIL. No traced TCP-reset cause is claimed.

The corrected CRL extension gives only the absent-ALPN negotiation probe a saved
empty stdin, retaining strict exit 1/no reply and all original refusal equations,
plus exact protocol-mismatch delta 1. It adds no 104/timeout exception or permissive
IO classification. All six final CRL pairs satisfy those stronger receipts. The
old failed run is not reclassified from their success.

The [manifest](../evidence/offline-crl-e441bb8-20261024/manifest.json) indexes the
closed compressed original logs, sources, methods, failed/prior runs and research.
The [same-author review](../evidence/offline-crl-e441bb8-20261024/review.json)
performs 66,101 read-only checks, redecodes 6,039 original guest Base64 blocks and
checks 10,491 original files byte-stable, without importing original verifiers.
These are evidence checks, not new tests or an independent security audit.

There is no online CRL/OCSP fetching, active-session hot revocation, persistent
CRL-number anti-rollback or guarantee after a permanently blocked OS/filesystem
call. Installed managed-agent/v2 CRL refusal, broader PKIX scope, real hardware,
full G3, TUI/GUI, packaging, storage, performance and Windows remain incomplete.
Publication and later CI retain their own content revision.

## Documentation check

The [documentation receipt](../evidence/offline-crl-docs-20261024/manifest.json)
retains 17 source snapshots, 485 local/repository-mapped links and actual
199-check site, JavaScript syntax and whitespace results. All 226 software and
13 final QEMU input sets still match their tested bytes. The seven site files
remain identical to `e6177063`, and the global readme-site skill's four files and
Codex discovery symlink remain unchanged. No new browser/pixel execution or
repeated global skill quick-validation is claimed for unchanged assets.

## Published content and CI

Content `91ecc44fe458de40ff806b80d7a73b7e24440a64` was normally pushed over SSH,
with a clean tree and matching remote main.
[Software run 40121403621](https://github.com/tiansongyu/usbip-client/actions/runs/40121403621)
completed successfully on all four native Ubuntu 22.04/24.04 x86_64/aarch64 jobs
(120953526607, 120953526693, 120953526731, 120953526778). The primary API's actual
architecture/full-gate step conclusions are also successful. This does not
recount each platform's tests or invent executed-ELF/full-log samples.
[Pages run 40121403729](https://github.com/tiansongyu/usbip-client/actions/runs/40121403729)
completed check 120953526988 and deploy 120953618317 successfully.

The [publication receipt](../evidence/offline-crl-publication-91ecc44-20261024/manifest.json)
retains 41 original files/methods: primary run/job responses, exact HTTP bodies
and headers, source/step checks and the content push. Six canonical HTTPS bodies
returned 200 and match content Git blobs. The seven website sources and four
global skill files remain unchanged; no new browser/pixel execution is claimed.
The local 927 executions remain at `fcb487cf` and final paired QEMU at `e441bb89`.
The final receipt-only commit does not relabel those runs as its own verification.

# Framed management close and coordinated preview upgrades

2026-10-26. The selected framed-v2 transport fix retains the original connection
through actual local write shutdown and peer TLS EOF. Full software gates and
four installed Native/USBFS dual-stack regressions pass. A separate coordinated
old-preview upgrade also passes. **A mixed old/new preview run remains failed.**
The cause of E59's original intermittent encrypted Native/IPv4 import failure
has not been established; these results do not retrospectively fix or rejudge it.

[Design and pinned upstream sources](framed-management-race-design.md) ·
[Installed fixture](../../scripts/qemu_installed_usb/README.md) ·
[Exact evidence and all six paired verdicts](../evidence/framed-management-race-20261026/summary.json) ·
[Archive audit](../evidence/framed-management-race-20261026/audit.json)

## Implemented boundary

The server starts graceful h2 admission shutdown while its only accepted CONNECT
stream1 remains active. Existing Catalog, IMPORT and USB traffic retain their
original application EOS, consumption, flow-credit and actual FIN-flush checks.
The original connection is polled through real shutdown under the existing stop
and response-timeout policy. ClosingIo sits outside TrackingIo, closes the same
underlying writer, then reads that transport to peer EOF. It discards at most
65536 tail bytes with a 1KiB buffer and 32 reads per poll. This does not validate
post-connection h2 tail semantics or accept those bytes as application traffic.

Real TLS close_notify validation, UnexpectedEof/reset errors, typed I/O/protocol
outcomes and quiet h2 boundaries remain. There is no new FD/owner, dependency,
schema, helper permission or raw-v1 downgrade. Observer metadata cannot keep the
transport alive. Device/root cleanup remains independently verified even when
the operation records an I/O error during peer-initiated teardown.

The stronger real TCP/mTLS regression gates the client's actual final network
read, observes actual server write shutdown and requires the original server
owner still alive. Only then does it allow peer reads and join both owners.
Old production `530c93ab` plus the exact new test overlay fails (exit101), also
with a fresh isolated target. It is a dirty test-only control, not a clean build.
The earlier assumption that the server should finish before peer progress is
superseded; its original source and failures are retained. The original half-close
and no-FIN TLS-error contracts are retained. A new real-TLS missing-close_notify
negative verifies sticky UnexpectedEof after local write shutdown.

## Verified source, binaries and packages

The unchanged `scripts/check.sh` and separate `scripts/check-gui.sh` pass at clean
`473fb3c7f6ae5c47e23f11def1d2ad2d14e77eb7`: **1096 + 18 = 1114 test
executions**, zero failed/ignored. All 722 tracked non-evidence input hashes and
HEAD/clean/input guards pass. The root gate includes formatting, strict Clippy,
workspace/default/all-feature tests, release build and the original extra checks.
GUI uses its original independent gate. These counts are not USB device cases.

| Final release artifact | SHA256 |
| --- | --- |
| CLI | `866b07513577f1cc35f86242ac5bcd76290691ba57256b8d66baaa4ad2fe68ec` |
| Helper | `475b2f97e8b4e31dd1af479193913c637d09dc8c3b1debd13442c0f88f6748f3` |
| GUI | `7bdb41f76f384d8c1ffd3c5ca327968de599abf520eec7c3953eccaf97a1c356` |

Base `0.1.0~preview20261026.9-1` and upgrade
`0.1.0~preview20261026.10-1` are built from those CLI/helper ELFs at the same
clean473 source. Their package hashes are respectively
`b442e76e9bab9c88b3b026a70c22db9b201ac60d627fe0f89888edb3c81e04bd`
and `90c61d4340d23a5a51f8d45dde0716e78a65f573ce190b995c993753a5343390`.
These are unsigned local amd64 previews; GUI was checked but is not installed by
this base-only USB suite. Old v6 retains its separate original receipt and ELF.

At clean `c89dec61`, the focused transport suite passes85 and the production
concurrent Catalog test passes256 real TCP/mTLS exchanges. A predefined32-round
audit of half-close, final-read retention and concurrent Catalog passes96 actual
test processes, each selecting exactly one test, including8192 Catalog exchanges.
HEAD/clean/debug-binary guards pass. This bounded sample is not race freedom.

## Actual installed matrix

| Sequence | Fixture source | Actual result | I/O rounds | Client / server Root proofs |
| --- | --- | --- | --- | --- |
| Native / IPv4, v9→v10 | clean `1b59ee74` | PASS | 3 | 6 / 10 |
| Native / IPv6, v9→v10 | clean `1b59ee74` | PASS | 3 | 6 / 10 |
| USBFS / IPv4, v9→v10 | clean `1b59ee74` | PASS | 3 | 6 / 4 |
| USBFS / IPv6, v9→v10 | clean `1b59ee74` | PASS | 3 | 6 / 4 |
| Native / IPv4, sequential v6→v10, new client/old server | clean `1b59ee74` | **FAIL**, second Catalog I/O | 1 completed | cleanup audited separately |
| Native / IPv4, coordinated v6→v10, both ends before restart | clean `393ce5ce` | PASS | 3 | 6 / 12 |

All twelve actual QEMU processes exit0, including the failed operation's guests.
Every pair's HEAD/clean/ELF/package guards pass. Five passing pairs contain fifteen
real evdev HID press/release and FTDI bidirectional35B rounds, seventy exact
original Root terminals, original driver/interface/config/node/generation
baseline restoration and the unchanged strict dual-ended ALPN2/TLS1.3 PCAP
classifier. Boot captures remain separate from complete product-window captures;
encrypted bytes are not decrypted. Guest exit0 and cleanup do not override FAIL.

Ubuntu22.04/24.04 amd64 cross roles, official image provenance, product-free
prepared bases, dynamic broker UIDs109/112 and original shipped units are reused
from [E59](installed-usb-data-validation.md). Live process hashes, zero agent
capabilities, original invocation stop/deactivate events and actual PID/cgroup
retirement are independently checked in the archive. No host USB/service/root
configuration is changed. Prepared bases do not replace E58 cold-install tests.

The four v9→v10 runs use original run.py/guest.py assertions. The coordinated
adapter composes exact validated source anchors; it saves its generated source
and original/generator/generated hashes. After old-v6 I/O, the client upgrade
stops actual imports; the old server is then cooperatively upgraded and verified
before new client imports restart. Both endpoints explicitly stop and restore
their baselines before the third reuse round. The extra server epoch explains
12 server proofs. It does not rewrite the failed sequential migration.

## Failures, corrections and evidence integrity

The archive retains every candidate log, historical source snapshot, method,
fixed-round audit and both complete gate results. Early focused successes at
41540391/d329fe32 do not override their broader failures. The 7c45dadf fixed
96-process audit remains80 PASS/16 FAIL. The c59c58d1 root gate remains exit101:
its historical wrapper counted only `ok` segments (260); counting the preserved
`FAILED` service segment gives393 passed/1 failed. GUI separately passes18.
The raw historical summary is unchanged; audit.json records the count correction.

The first old-control build shared the main target and contaminated attribution
of two following current-source runs (82 tests listed where84 were required).
Both are excluded from acceptance, including the subsequent Catalog PASS.
Only scoped transport/service build artifacts were cleaned; a fresh verbose
current-source rebuild passes84, and the final missing-close_notify case brings
that to85. The control was rerun in its own fresh target and fails as expected.
No test failures are suppressed, retried into a changed verdict, or ignored.

The public archive has2250 exact members (17,844,276 raw bytes), gzip3,271,453B,
SHA256 `74685363f6d729f2f2a569a20539a30d3db70cd0c1d436bc8c8af21102f22d19`.
Every member is read back against its hash and size;42 actual generated private
key byte sequences are checked absent. Keys, VM disks, debs and ELF binaries
stay outside Git. The four global README skill files and Codex symlink retain
their prior delivered hashes. Source, fixture, package and later publication
commits are recorded separately rather than relabeling these runs.

## Upgrade requirement and remaining scope

For affected old framed-v2 previews, stop new imports, let original owners finish
and inspect their cleanup, upgrade both endpoints, then explicitly restart and
verify discovery/I/O. See the [package upgrade guide](../debian-installation.md).
The failed new-client/old-server test prevents a transparent rolling-compatibility
claim despite unchanged ALPN. Do not bypass TLS EOF errors or silently downgrade.

Real-device G1/G2, installed ARM runtime, general device compatibility, storage
and ISO, Windows USB, permanent kernel/FS blocking, power-loss durability and
complete Phase0–7 acceptance remain open. E59's original encrypted failure cause
remains unproven. Remote CI/Pages publication is recorded separately after push.

## Publication receipt

Content `0316de7638c68b9360ec719194e9e0c4ae0fe454` is normally pushed to the
authorized SSH remote and independently verified as remote main. Core run
[40260772607](https://github.com/tiansongyu/usbip-client/actions/runs/40260772607)
passes on Ubuntu22.04/24.04 × x86_64/aarch64, each complete job log recording
1096 passed/0 failed/0 ignored. GUI run
[40260772624](https://github.com/tiansongyu/usbip-client/actions/runs/40260772624)
passes its four native jobs, each18/0/0, including the original package build and
artifact upload. Pages run
[40260772646](https://github.com/tiansongyu/usbip-client/actions/runs/40260772646)
passes check/deploy. Four Debian artifact metadata entries are retained; their
binaries are not downloaded, installed or executed by this receipt.

Seven deployed runtime files and three pinned source documents return HTTP200
and exactly match content0316. Live DOM confirms the new validation link,
two-endpoint upgrade requirement and retained mixed failure. Local desktop1280,
actual390, keyboard2px and script-omitted390 checks are in the local evidence;
no new screenshot/pixel claim is made. The
[publication receipt](../evidence/framed-close-publication-0316de76-20261026/receipt.json)
retains25 exact members (102,477,620 raw bytes), including eight decoded job logs,
all statuses, artifact metadata and HTTP observations, with round-trip hashes.
Its archive SHA256 is
`5b61636caf8f0e32cde808d2ff5de58b4ee5b329a32f62155fd9e59b407e45a3`.
The subsequent receipt commit does not relabel local software473, USB1b59/393,
or this remote content0316 execution.

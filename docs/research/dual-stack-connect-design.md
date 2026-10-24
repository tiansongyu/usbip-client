# Bounded dual-stack TCP fallback

2026-10-24. Baseline `9c9506fad514d016e5353fd4711a44f1c06a97e2`.
Status: implemented; targeted8 connector tests and one raw/framed hostname/TLS
service-name test pass. Complete clean software and installed validation pending.

The original goal explicitly requires hostname, IPv4/IPv6, timeout/cancellation
and real encrypted device data connections. The current shared secure connector
and legacy DEVLIST use Tokio's sequential multi-address connect. A pending first
address can exhaust the existing operation deadline before a healthy second
address is attempted. Fixed-source audit and an isolated actual loopback probe
confirm this behavior; those probes are not RustUSB or USB acceptance.

## Sources and decision

Tokio 1.54.1 is already locked: MIT, VCS
`b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8`, registry checksum
`b52fd3255258d26fac7264ab5a76b53c13367136dc011d3d6ad4f8ed070396a3`.
Its [TCP connect implementation](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/net/tcp/stream.rs)
awaits candidates sequentially. Its [address adapter](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/net/addr.rs)
offloads hostname resolution to blocking std lookup; literals bypass it.
[Blocking tasks](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/task/blocking.rs)
cannot generally be stopped once running. These fixed sources and an actual
four-closure permit/cancellation probe justify bounding real resolver work.

[RFC 8305 sections 2, 4 and 5](https://www.rfc-editor.org/rfc/rfc8305.html)
inform the policy: preserve host preference, interleave families, retain earlier
pending attempts, normally stagger launches by 250ms. Section 5 has no
immediate-failure exception: use a 100ms minimum after definitive failure,
remaining above its normative 10ms floor. Do not describe this as complete RFC
8305: one completed OS lookup does not implement independently arriving AAAA/A,
50ms DNS resolution delay, updates, address synthesis or NAT64/DNS64 policy.

Reuse Tokio TCP and the platform resolver without adding a dependency. A small
bounded policy scheduler is appropriate in the protocol crate, which already
owns network discovery. libcurl's maintained racing implementation is a useful
comparison, but importing C/HTTP infrastructure for this TCP-only policy is not
needed. Keep research in the existing report/evidence layout; the repository
instructions override the design skill's generic component-skill output layout.

## Interface and ownership

Add `rustusb_protocol::connect::connect(&Peer) -> Result<TcpStream>`:

- Numeric IP literals create one address directly, without taking a DNS permit.
- Four process-wide permits bound queued/running actual std resolver closures.
  Nonwaiting exhaustion reports a fixed State error. Move the owned permit into
  the real `spawn_blocking` closure, retaining it until that closure finishes,
  even if its async waiter is dropped. Do not claim the OS resolver is cancelled.
- Inspect at most 65 raw resolver entries, rejecting more than 64. Stably
  deduplicate, reject more than 16 unique addresses or an empty result, preserve
  full SocketAddr values and each family's order. These bounds cover retained
  RustUSB results, not std/libc/OS internal allocation or lookup duration.
- Start with the platform's first family and alternate while both remain.
  Start the first candidate immediately; normal later starts are at least250ms
  apart. A definitive TCP failure may shorten the next interval to100ms.
- Own at most16 single-address TCP futures inline; poll a bounded collection
  with one Tokio timer. No spawned dial task, per-address timeout, retry loop,
  parallel TLS handshake or duplicate USB operation is introduced. Poll existing
  candidates before launching another; return a TCP winner only after every
  losing future/socket is dropped. Cancellation drops the whole owned set.
- Callers retain their original total DNS/TCP/TLS/catalog/IMPORT deadline and
  stop source. No stage refreshes that deadline. DNS work can outlive its
  waiter and can delay runtime shutdown; the four-work quota remains occupied
  until actual completion. Literal endpoints still work at DNS saturation.

Both legacy Rust DEVLIST and the shared secure client adapter use this helper.
The external Linux usbip tool retains its own network behavior. TLS starts on
only the winner, using the original independent `server_name`, PKI, ALPN and
pin rules. A TLS failure is returned without trying another endpoint or an
insecure protocol. Server authentication admission/permits and USB/root
ownership are unchanged. Requests gain no endpoint override or new privilege.

## Verification contract

1. Before product changes, a clean paired QEMU fixture must demonstrate the
   original sequential failure. Test a protected hostname resolving first to a
   stale address with a permanent nonexistent private-link MAC, then the actual
   server's opposite family. Only the disposable guest's hosts/gai/neighbor
   setup changes. The isolated NIC has no host forwarding, DNS service or TAP.
2. Preserve all original secure fixture predicates: two real HID/serial I/O
   cycles, real wait/cleanup, original root journals and full driver baselines.
   A new strict capture method accepts only empty SYN attempts to the exact
   stale candidate; it records a derived healthy projection for the original
   ciphertext classifier without changing raw captures. Exact healthy client/server tuples must
   follow earlier stale-candidate SYNs with equal-count sorted chronology; this
   is aggregate evidence without per-process/per-operation attribution. This is OS hostname resolution
   via guest hosts files, not wire-DNS or VPN qualification.
3. Meaningful software tests cover ordering/duplicates/limits, actual loopback
   fallback, launch timing with deterministic clocks, failure acceleration,
   multiple ready sockets, immediate loser drop and caller cancellation.
   Actual blocked resolver closures prove cancellation cannot reuse quota;
   literals bypass exhausted resolver capacity. Real TLS keeps service-name
   verification and fails closed after selection.
4. Execute complete fmt/Clippy/default+all-feature tests/release/rustdoc and
   Native-only isolation, with actual source/build/ELF provenance. New QEMU
   runs cover three backends and both preferred-failure families; applicable
   old secure/admission/framed regressions remain separate scopes. Retain all
   original failures and do not relabel older824 software evidence.

This is Phase3 connectivity work, not complete G3, a performance benchmark,
hardware G1/G2, hot credential revocation, TUI/GUI, packaging or Windows support.

## Initial installed fixture correction

The first clean5b26bba Native/IPv4 fixture fails during hostname setup:
BusyBox standalone ash selects its `ip` applet, which rejects `neigh replace`.
No DUAL_STACK_READY or product hostname dial occurs. Both guest exits zero and
all four provenance guards true do not turn that host FAIL into a sequential-
connector reproduction. Use the already copied `/usr/bin/ip` explicitly for the
new neighbor mutation and observation, then rerun clean/fresh. Product unchanged.

The fresh cleanca63bc4 Native/IPv4 run reaches DUAL_STACK_READY and then
FAILS at the first real secure discovery with its original connection timeout.
Raw client PCAP shows stale IPv6 SYNs and zero healthy IPv4/3241 SYN connections.
Both guest exits zero/all four guards true are preserved separately from the
host FAIL. A post-run named derived classification does not change that verdict.
This is the installed original-product reproduction, distinct from the earlier
BusyBox setup failure; no device I/O or full successful cleanup suite is claimed.

## Implementation review checkpoint

Two read-only component reviews find no remaining must-fix after corrections.
Final selected sets of11/12 files remain stable, and the fixed Tokio timer
source confirms its upward millisecond rounding. The reviews ran no product
tests. The first Clippy redundant closure correction and test unbounded-accept
correction are preserved separately. Actual localhost results are platform-
dependent; installed QEMU, not a local dual-listener assumption, must prove
two-family behavior. Synthetic ready-resource timing and socket WouldBlock/EOF
observations retain their exact scope. No external dependency or lockfile changes;
174-package inventory remains unchanged.

## Clean software and framed fixture scope

At clean f3bf579 the complete gate passes365 default/457 all-features/20 vendor
executions (842 total), with198 copied source/build inputs unchanged and the
Native-only isolation check passing. CLI/helper release digests are separately
recorded; neither is assumed equal to the older824 build. No new package or
lockfile change is introduced. These are software results, not installed PASS.

Before installed product runs, give the new fixture an explicit `--framed-v2`
option for a separate Native/IPv4 scenario. Both trusted TLS JSONs must then use
strict schema2/data_link defaults, without downgrade. The original classifier
gets only an enumerated expected-ALPN parameter (default raw1 unchanged), so it
checks the exact v2 ClientHello offer on the same strict healthy projection.
No raw packet/log substitution is allowed. This supplements six raw hostname
cases and six original raw default-policy regressions; it does not label all
backends' installed v2 fallback or wire-DNS/VPN as qualified.

## First installed product observations

Both clean b5d3e92 Native/IPv4 raw and separate framed-v2 product runs FAIL at
secure discovery timeout, with both guest exits zero and all four guards true.
The raw client capture has one stale connection's SYNs and no healthy-family
SYN. No installed success is inferred from the842 software results. These files
remain unchanged; the failure is not yet assigned to NSS or the race scheduler.

The original stale-name fixture had not captured the actual std resolver answer
list. Instrument the same guest configuration with an independent guarded
std-only Rust probe, recording actual executed probe SHA and raw SocketAddr
answers before product work. Do not change hosts/NSS policy yet or silently
reclassify the earlier old-product failure as a proven two-candidate experiment.
The probe's compiler/source/copied-executable provenance remains separate from
product ELF receipts. Any eventual NSS correction requires a fresh clean run
and a valid old-product counterfactual with the corrected candidate set.

The fresh clean636efa guarded root std probe returns exactly one address, the
stale IPv6 candidate; its actual executable SHA matches its build receipt and
host.conf is observed absent. This supports the NSS explanation: the captured
fixed glibc2.35 source stops after the first matching hosts entry with multi off.
The exact Ubuntu patched source fetch was unavailable, so that attribution is
source-backed inference plus actual probe behavior, not a claim of exact Ubuntu
source identity. The earlier ca63/b5 experiments did not establish two eligible
candidates and cannot validate the two-address race counterfactual. Their FAIL
verdicts remain; the original real-loopback sequential probe remains separate.

Install explicit guest-only `host.conf` multi on before any fresh lookup, with
root0644 policy receipts and no RESOLV_HOST_CONF/RESOLV_MULTI overrides. Run the
new guarded probe as actual UID2000 and capture its proc UID/executable before
product work. It must report the stale and healthy addresses in order. A valid
old-product comparison must use this corrected environment; no host NSS file is
changed. The first root-only probe's narrower scope is retained.

## Local legacy CLI integration

The new Rust connector also serves legacy read-only DEVLIST. Add a reproducible
ordinary-user process fixture using only loopback listeners, an empty synthetic
DEVLIST reply and the prebuilt CLI. Check actual `discover localhost` output and
a withheld-response timeout, with real proc executable/UID receipts and final
child reap/server-thread joins. This is separate from842 Cargo executions and
from installed USB qualification. Add the same two-case fixture to native CI;
no host NSS/USB mutation, public listener, new permission or package is needed.

The first local legacy fixture passes catalog but fails its withheld-response
assertion: existing `--json` failures emit the structured `{ok:false,error:...}`
DTO on stdout, whereas the new fixture expected empty stdout. Actual stdout/
stderr and method remain; its child wait code was not separately persisted
before that assertion. Correct the exact existing DTO expectation and save
wait/cleanup observations before judging a fresh run. Product code unchanged.

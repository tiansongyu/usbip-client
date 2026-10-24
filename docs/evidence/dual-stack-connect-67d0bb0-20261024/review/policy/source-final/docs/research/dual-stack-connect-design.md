# Bounded dual-stack TCP fallback

2026-10-24. Baseline `9c9506fad514d016e5353fd4711a44f1c06a97e2`.
Status: implementation contract; product validation pending.

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

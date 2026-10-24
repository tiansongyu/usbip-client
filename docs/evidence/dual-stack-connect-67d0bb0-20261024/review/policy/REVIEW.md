# Dual-stack connector and projection: final source review

2026-10-24. Read-only selected-source review. No repository edit, Cargo/Python
fixture execution, test run, network fault or USB operation is performed here.
Parent runtime results remain independently scoped. The first snapshot and
PRELIMINARY.md precede the parent's style/fixture corrections; source-final is
the final review baseline, with its own before/after hashes.

## Result

No product or fixture must-fix remains in the final selected snapshot. This is
source assurance, not a complete security audit or installed acceptance.

## Connector contract

- connect.rs enforces 64 raw/16 unique resolver results before TCP starts;
  stable deduplication preserves full SocketAddr values. Partition plus paired
  pop_front operations retain each family's order and start with the first
  OS-returned family. Literal addresses bypass DNS acquisition.
- The four-work owned permit moves into the actual blocking closure before
  lookup and validation. Async waiter cancellation does not release it early.
  The stated limit excludes std/libc/OS allocation and duration.
- At most16 boxed single-address futures are owned inline. Every poll pass first
  polls existing candidates; failure removes only completed futures and advances
  the next launch no earlier than last_started+100ms. Normal launches use250ms.
  Each launch updates last_started. The timer is polled when work remains;
  exhausting the finite list either leaves actual candidate wakers or returns
  the final OS error. No unbounded immediate retry loop or detached dial exists.
- Success drops the complete pending collection before returning the winner;
  cancellation destroys that same collection. The shared secure adapter starts
  one raw/framed TLS handshake only after connect returns, preserving the
  independent configured service name. No authentication-failure retry is added.
- Existing legacy DEVLIST and secure discovery/IMPORT wrappers retain their
  original whole-operation timeout/timeout_at boundaries. This does not modify
  the external Linux usbip tool's connection policy.

## Corrected tests and evidence scope

The final literal/hostname exchange, socket-pair setup, healthy accept and close
observations have bounded outer waits. The earlier unbounded accept under join!
is removed. Cancellation now samples WouldBlock before timeout and EOF after;
these are actual socket API observations, not syscall-entry tracing. Paused-clock
fixtures exercise250/100ms starts and owned future destruction. The simultaneous
ready fixture uses DropReceipts, not two installed USB/TLS sessions.

The service fixture performs actual hostname Catalog positive/negative cases
for both raw/framed transports using a separate TLS service name. Its localhost
answer set is explicitly platform-dependent; it cannot prove a second DNS answer
or per-address authentication retry behavior by itself. Winner-only TLS is also
reviewed directly in the call path.

## QEMU projection

Only validated empty client SYNs to the exact opposite-family stale IP, TCP3241,
nonexistent MAC and source MAC are excluded. Responses/payloads are rejected;
unclassified records remain for the unchanged classifier. The healthy SYN index
now checks exact fixture client/server IP and MAC tuples. Output uses a distinct
healthy-projection path and retained PCAP record slices; raw captures are read,
not overwritten. Original USB/root/baseline verification still runs first.

Equal distinct-flow counts and sorted SYN timestamps prove aggregate chronology
within the existing ten-second bound, without per-process/per-operation
attribution. README/design/result limits now state this explicitly. This is
hosts-file OS resolution, without wire-DNS, VPN, benchmark, complete RFC8305,
hardware or fullG3 acceptance. Old setup failure and old sequential timeout are
not reclassified by this review.

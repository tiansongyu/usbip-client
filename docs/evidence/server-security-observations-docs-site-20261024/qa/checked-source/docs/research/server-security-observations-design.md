# Bounded server authentication observations

2026-10-24. Baseline `39004dda3172d2945650b36023a45065bcc19a64`.
Status: implemented and validated at clean `c097cf8`: 808 software executions,
six installed authentication-observation pairs and one separate framed-v2
diagnostic regression passed. See the [validation report](server-security-observations-validation.md)
for exact sources, ELFs, counters and remaining boundaries.

The installed authentication negatives exposed missing operational information:
Native silently skips outer connection-budget refusals, and both engines project
TLS errors to fixed Permission errors without retaining categories. This increment
adds bounded read-only counters. It does not complete durable security auditing,
G3, or hardware G1/G2.

## Scope and ownership

Each constructed `TlsServer` owns one metadata-only `SecurityMonitor`. Clones
share observations and the existing TLS connection budget. The scope is **loaded
TLS server configuration**, not job, session, process-wide traffic, USB transfers
or permanent history. An agent profile retains it across job stop/start. Restart
loads a new configuration; agent replies include the actual service instance ID.
No historical zero is invented for a previous epoch.

TLS counters distinguish attempts, in-flight attempts, authenticated results,
generic handshake failure, deadline expiry, verified-but-unmapped leaf, negotiated
protocol mismatch, TLS permit refusal, and cancelled/dropped attempts. Framed-v2
setup after TLS has separate attempts, in-flight, ready, failed, timed-out and
cancelled counts. Native and both secure Rust modes separately count outer
listener-budget refusals before TLS. Legacy plaintext policy is outside scope.

An attempt starts when its future first enters the observed stage. An owned guard
records one outcome, including Drop on cancellation. In-flight is an attempt gauge,
not active streams or USB leases. Authenticated means local PKI, ALPN and leaf
mapping passed; it does not prove peer acceptance, IMPORT success or VHCI
enumeration. Generic handshake failures do not guess certificate/ALPN/close causes
from rustls or IO strings. Wrong local raw/framed API use is outside the stage.

Snapshots copy coherent metadata under a short mutex. Counters saturate with
`saturated=true`; unexpected underflow or recovered poison sets `consistent=false`.
Neither guard nor monitor owns acceptor, credentials, policy, stream, permit, USB
FD, helper handle, listener or task. No filesystem, logging, async wait or user
callback runs under this mutex. Counts never gate authentication, admission or
cleanup. Preserve actual owner/stop/join and all existing permit semantics.

## Interfaces and authorization

Foreground secure servers accept `{"op":"server_security","id":"check-1"}`
on the existing enabled bounded control input. A new schema-1 `server_security`
event carries the safe ID and separate snapshot. Legacy servers return the fixed
control rejection. Existing Ready/Snapshot/Stopped events do not change. There is
no unsolicited per-failure stdout or synchronous stderr logging.

`rustusb agent --config FILE server-security PROFILE` reads the fixed profile.
Check actual kernel UID and existing `Action::Read` before inspecting configuration
or counters. Client profiles return fixed Unsupported. All authorized outcomes
bypass the WAL actor; denied/unknown profiles retain durable denial handling.
Report current `journal_healthy` independently. No input selects a path, endpoint,
key, listener or target outside the profile.

Add separate tagged request/response/event variants. Keep Session/Metrics, health,
diagnostic bundle schema-1, IPC envelope-1, manifest and private disk schemas.
Old clients do not implicitly support new variants. Diagnostic export does not
include these live counters. Snapshots contain only fixed enums, numbers and
flags, without peer addresses, serials, device keys, pins/principals, certificates,
raw errors, messages or USB data. This is volatile observation, not durable audit,
credential revocation evidence, or USB release proof.

## Required validation

1. Real TLS success, rejection, unmapped leaf, timeout, cancellation and exhausted
   permits; coherent accounting and returned permits. Separate framed setup
   success/failure/cancel accounting. Explicit saturation and poison recovery.
2. Actual foreground process/control and service profile reads: Read-only access,
   denied/unknown/client profiles, scope across job restarts, bounded projection,
   and authorized reads while the WAL actor is held. Preserve cleanup/stop tests.
3. Full `scripts/check.sh` and Native feature isolation; record actual source,
   selected inputs, default/all-feature counts and ELFs separately.
4. Extend installed paired-QEMU authentication snapshots at existing free/held
   barriers. Use a clean revision and new result directories. Refusals must change
   stage counts while original owners survive and later actual HID/serial I/O
   succeeds. Preserve root proofs, guest exits, device baselines, old credential
   retirement and the four original FAILs. Mixed PCAP is not exact-cause evidence.

Retain the fixed Rust/rustls/Tokio dependencies and source-backed architecture;
there is no new external dependency, crypto mechanism or privileged API.

## Initial implementation checks

Actual targeted transport tests passed: local outcome aggregation, cancellation
and returned permits, 16 authenticated-stream budget refusal, metadata lifetime,
saturation/poison recovery, framed success/cancellation, malformed framing and
the real five-second setup deadline. Service checks passed for real loopback
foreground controls/outer quota, configuration scope across two job starts,
actual UID permission denials through durable IPC, and authorized success plus
Unsupported while a real Store lock blocks an already-polled commit.

The first foreground test used a three-second shutdown expectation and FAILED.
Source inspection confirmed existing cooperative framed-v2 joins pre-auth TLS
through its five-second deadline; raw-v1 aborts those network tasks. The revised
test preserves those product paths and separately expects timeout versus
cancellation. The first compile also found a removed helper still used by three
callers and a mutex guard inferred across await; a test borrow later failed to
compile. These were corrected before the targeted checks passed. These dirty
development executions are not clean full-gate or installed USB acceptance.

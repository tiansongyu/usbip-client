# Framed management connection-close contract

2026-10-25. Started clean `1e67e70ff35734531ae4b71006bd821564221fad`.
E59 is verified progress, not a live-owner wait. This work addresses a concrete
transport close defect; it does not decrypt or identify the cause of E59's
intermittent Native/IPv4 failure. The complete Phase0–7 objective remains active.

## Problem and selected correction

Production discovery finishes TLS/Catalog/FIN/EOF/owner join before IMPORT
requests a helper capability. E59's absent HID attempt and null link history
narrow that path without proving its failing substage.

The original driver drops its h2/TLS connection after authenticated stream EOS,
trailers, application consumption/flow credit and local FIN flush. Those stream
facts do not execute TLS shutdown. At clean fededbe2, the controlled real-TCP/mTLS
`completed_framed_peer_close_survives_delayed_final_tls_read` fails with
ConnectionAborted after actual server completion. Both owners are joined before
its assertion. The original test function remains verbatim. The earlier256
concurrent production Catalogs at b3cdd482 passed and did not reproduce it.

Keep original send/receive/ping references alive through actual connection close.
Retain the unique h2/TLS connection in a bounded close phase before publishing
its outcome. A private adapter accepts a close flag: client keeps polling its
actual connection; server first preserves an observed extra stream/error, then
requests NO_ERROR close for the already-drained fixed single stream and polls
real codec flush/TLS shutdown. The h2 method is named `abrupt_shutdown`; here
no application half/byte remains outstanding and no new stream is admitted.
Its generic graceful new-stream grace exchange instead adds a MAX-ID GOAWAY/PING
round that produced actual idle-close BrokenPipe candidate failures.

Only after actual close are connection/stream/ping owners dropped and terminal
metadata published. Stop and the original response-timeout policy bound this
pure asynchronous network stage; actual stop/timeout/protocol/I/O failures stay
typed failures. Never mask BrokenPipe/reset/EOF or treat a deadline as a healthy
peer completion. Preserve server h2 I/O classification separately from extra
streams. Public LinkHealth/TLS/ALPN/helper/WAL schemas and device ownership stay
unchanged. Every h2 operation/error/drop remains quiet; temporary typed test-only
diagnostics are removed. No unsafe access, dependency update or copied upstream
implementation is introduced.

## Fixed source evidence

h2 0.4.20 source is
[`b0e890842ac16d3c76836fd4fcc06421acb15fa7`](https://github.com/hyperium/h2/blob/b0e890842ac16d3c76836fd4fcc06421acb15fa7/src/server.rs).
Close requires continued polling; client stream-reference loss auto-closes its
connection, while generic server graceful close uses a PING/one RTT before final
GOAWAY. These details come from actual source, not a README.
Tokio-rustls0.26.6 source
[`8aebb7c9556ea8024c1b974b84a4a6f2e306889a`](https://github.com/rustls/tokio-rustls/blob/8aebb7c9556ea8024c1b974b84a4a6f2e306889a/src/client.rs)
polls close_notify and underlying shutdown; its
[buffer contract](https://docs.rs/tokio-rustls/0.26.6/tokio_rustls/)
also requires actual flush.

## Verification and limits

At clean d329fe32 the complete transport suite passes82 tests. A predefined
32 rounds of the original half-close and original delayed-read tests pass all64
actual processes with HEAD/clean guards. Original feded test bytes remain a
prefix of the final file; only an independent no-FIN abort negative is appended.
A real underlying AsyncWrite.shutdown gate verifies release, stop, timeout and
I/O error, actual owner joins and actual transport drop. An aborted TLS peer
without stream FIN still produces I/O failure.

Keep every original candidate FAIL/source/result. Early reference release broke
the original half-close; a generic graceful-PING candidate also broke normal
close. Its provisional PONG-withholding timeout assumption is superseded by the
restored original stronger regression and actual shutdown gate. No original
half-close/malformed-data assertion is weakened, ignored or reclassified.
Software success does not establish the old encrypted QEMU cause or race freedom.

Run the unchanged full root and separate GUI gates, package actual final ELFs,
and run original installed Native/USBFS dual-stack I/O/upgrade tests with all
Root/kernel/driver/node/PCAP checks intact. Freeze HEAD/source/ELFs during every
actual live local gate/QEMU owner; do not restart on observation expiry. Archive
explicit public evidence without keys/disks, commit and normally push. Global
README/site skill remains shared. Hardware G1/G2, installed ARM runtime, Windows
USB, storage/ISO, permanent blocked-kernel/FS cancellation, power loss and full
phases remain unverified.

## Complete-gate finding and refined lifecycle

The c59c58d1 full root gate fails the actual concurrent production Catalog test
with I/O (group0/wave0); GUI18 passes. This invalidates delivery of that candidate
despite its focused82/64 passes. Keep the failed full gate and mixed post-gate
ELF identities; do not build acceptance packages from them.

Refine the fixed-one-stream lifecycle using h2's documented active-stream graceful
shutdown: immediately after accepting CONNECT stream1 and issuing its non-EOS
response, announce that no further streams will be accepted. Both halves of that
existing stream are still open, so the MAX-ID GOAWAY/PING progression occurs
while it is active, not after idle EOF. Existing stream1 continues Catalog or
IMPORT/USB traffic unchanged. Keep polling the original connection through actual
TLS shutdown after its normal complete EOS/credit/FIN boundary. This removes the
late-idle close adapter and NO_ERROR-abrupt candidate. No added stream, ALPN
version, retry, sleep, ownership relaxation or I/O suppression is introduced.
All original regressions and the production concurrent Catalog remain mandatory.

The active-stream candidate b9fc7a6c passes the original close/half-close cases and
the production concurrent Catalog. Its no-FIN TLS abort negative still fails its
exact error-category assertion: actual Protocol instead of required Io, never
healthy completion. h2 can deliver a transport error through the receive-stream
after GOAWAY; the old pump mapped every such error to generic protocol failure.
Preserve its typed IO kind (without formatting raw h2 errors) through DATA/trailer
reads and driver classification. Invalid framing/trailers remain Protocol;
explicit counter/metadata fatal reasons retain precedence. Do not weaken the
abort test or accept arbitrary failures as success.

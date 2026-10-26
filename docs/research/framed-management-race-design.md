# Framed management connection-close contract

Started 2026-10-25 at clean `1e67e70ff35734531ae4b71006bd821564221fad`;
verification continued 2026-10-26 after explicit resume. E59 is verified progress.
This work addresses reproduced transport close defects. It does not decrypt or
identify the cause of E59's original intermittent Native/IPv4 failure, or complete
the full Phase0–7 objective.

## Problem and authoritative regressions

Production discovery completes TLS/Catalog/FIN/EOF/owner join before IMPORT asks
the helper. A missing helper attempt/null data-link history narrows that path
without identifying its failed substage.

At clean fededbe2 a controlled real-TCP/mTLS final-read test fails with
ConnectionAborted after server completion. Several early candidates pass focused
tests but fail the original half-close or concurrent production Catalog; those
failures remain authoritative. The c59c58d1 complete gate fails Catalog; the
7c45dadf fixed96-process audit is80 PASS/16 FAIL. Typed stage evidence at530c93ab
shows FIN/EOF was not complete, so a parsed reply cannot be accepted with a
suppressed close warning.

The final ownership regression blocks the client's real network read, observes
the server's actual TCP write-shutdown receipt, requires its original owner still
alive, then permits peer progress and joins both owners. It fails against
pre-drain530c93ab plus the exact test overlay, including a fresh isolated-target
control (exit101). That control is explicitly a dirty test-only overlay, not a
clean product build. The former owner-before-peer-progress test/source/results
remain historical; the stronger receipt/live-owner test replaces that assumption.
The original half-close assertion and no-FIN TLS-error assertion are unchanged.

## Selected lifecycle

Each v2 connection accepts exactly one CONNECT stream1. Immediately after its
non-EOS response, while both halves remain open, the server starts h2 graceful
shutdown of further stream admission. Existing stream1 continues Catalog or
IMPORT/USB traffic unchanged. Its grace GOAWAY/PING exchange occurs while the
stream is active; starting an idle close afterwards caused candidate races.

Keep original send/receive/ping references and the unique original connection
through the original EOS/trailers, actual application consumption/flow credit
and local FIN-flush boundary. Then continue polling actual h2/TLS shutdown under
the existing stop and response-timeout policy before publishing completion.

An internal ClosingIo wraps TrackingIo externally. When h2 selects connection
shutdown it first polls the original underlying write shutdown, then retains
that same transport while reading to actual peer EOF. TLS still verifies records
and close_notify. UnexpectedEof, reset, other I/O, protocol and timeout remain
errors; there is no blanket EOF acceptance or fallback. No FD duplicate, extra
owner or monitor capability is introduced.

h2 has already selected logical connection close. At most65536 tail bytes are
discarded with a1KiB scratch buffer and32 reads per poll; they are neither adopted
as USB/application data nor logged. This bounded transport drain does not claim
semantic validation of post-connection h2 tail frames. Outer handshake/driver
stop/deadlines remain in force. The wrapper sits outside the write-fence observer
so waiting for peer reads does not fake write backpressure or DATA/FIN flush.
Actual transport drop still precedes terminal metadata.

Preserve h2 I/O kinds through server accept, DATA/trailer and driver classification;
invalid framing/trailers remain Protocol and explicit metadata/counter fatal
reasons retain precedence. All h2 operations/errors/drops remain quiet. Temporary
test-only diagnostics are removed. Public TLS/ALPN/LinkHealth/helper/WAL schemas,
device ownership, dependencies and core unsafe prohibition remain unchanged.
Raw-v1 does not use this optional framed connection path.

## Source evidence and verification

Pinned h2 0.4.20 source:
[`b0e890842ac16d3c76836fd4fcc06421acb15fa7`](https://github.com/hyperium/h2/blob/b0e890842ac16d3c76836fd4fcc06421acb15fa7/src/server.rs).
Continued polling is required for close; stream-reference loss can auto-close
the client, and server graceful shutdown uses a control PING/RTT while preserving
active streams. Tokio-rustls0.26.6 source:
[`8aebb7c9556ea8024c1b974b84a4a6f2e306889a`](https://github.com/rustls/tokio-rustls/blob/8aebb7c9556ea8024c1b974b84a4a6f2e306889a/src/client.rs);
its [buffer contract](https://docs.rs/tokio-rustls/0.26.6/tokio_rustls/) requires
actual flush/shutdown. No upstream implementation is copied.

After scoped current-source rebuild, transport84 passes. At clean c89dec61 the
additional real TLS peer-without-close_notify negative passes with sticky
UnexpectedEof, bringing transport to85. A predefined32 rounds of original
half-close, final-read owner retention and concurrent production Catalog pass96
actual processes (each exactly one selected test), including8192 Catalog exchanges,
with HEAD/clean/binary guards true. These are software cases, not USB cases or a
general race-freedom claim.

A shared target used for the first old-control build contaminated subsequent
current-source attribution (82 listed instead of expected84). Those two subsequent
runs are excluded. The old control was rerun in a fresh dedicated target; current
transport/service build artifacts were scoped-cleaned and actual source paths/
counts checked. Never reuse cross-checkout target output as source proof.

Full unchanged root/GUI gates and final-ELF installed Native/USBFS dual-stack
I/O/upgrade regressions remain required before delivery. Freeze HEAD/source/ELFs
through every actual live local gate/QEMU handle and preserve all failures.
Archive explicit public evidence without keys/disks, commit and normally push.
Global README/site skill remains shared. Real G1/G2, installed ARM, Windows USB,
storage/ISO, permanent kernel/FS blocking, power loss and complete phases remain
unverified.

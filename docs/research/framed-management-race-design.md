# Framed management race investigation

2026-10-25, starting clean `1e67e70ff35734531ae4b71006bd821564221fad`.
E59 is verified progress, not an unresolved-owner wait. Its ten original failed
runs stay failed. In Native/IPv4 attempt4 the first HID import failed with I/O
before any helper attempt, while the serial import remained active. A later
independent run passed; that does not identify or fix the intermittent failure.

## Evidence and initial hypothesis

The client job first runs production discovery (TLS connect, Catalog parse,
framed FIN/EOF and exact driver join), then creates the IMPORT connection and
publishes its monitor, then asks the helper. A missing attempt and null link
history narrow the path but do not prove which discovery/connect stage failed.
Do not infer USB driver failure from the generic I/O category alone.

The current framed driver returns PeerEof after received END_STREAM/application
drain and local FIN flush, then drops the owned h2 connection. Investigate whether
this can race the peer's remaining driver/transport work. This is a hypothesis,
not a claim about the failed encrypted capture, a TLS reset, or its root cause.

## Investigation contract

1. Add bounded real TCP/mTLS regressions using the current production discovery,
   protocol and ownership paths. Exercise concurrent independent Catalogs without
   device/helper operations, and retain actual failures and join outcomes. Use
   fresh credentials/server limits in bounded groups to avoid accidentally testing
   authentication quota exhaustion. No sleep/retry or sequential-import workaround.
2. If needed add a controlled transport scheduling gate to make the suspected
   close ordering deterministic. Keep real original owner handles and await them;
   an observation deadline cannot drop or replace a live driver. A negative test
   must fail on actual current code before its production fix is credited.
3. Change product code only after evidence identifies a defect. Preserve strict
   malformed/truncated-frame, partial-DATA/FIN, flow-control, typed end/error,
   no-extra-application-data, quiet logging and actual total owner-join contracts.
   Do not accept arbitrary IO/EOF/reset as clean completion or widen deadlines.
4. Verify the unchanged installed Native/USBFS I/O/upgrade baseline and add
   repeated concurrent installed imports with pre-cleanup observations as needed.
   Original Root correlation/kind/UID/key/resource/proof and kernel/driver/node
   baselines stay mandatory. Keep failed and successful runs at actual source,
   package and ELF identities. Host USB/services/modules remain untouched.
5. Run full root and separate GUI gates, archive explicit public evidence without
   private keys/disks, update claims only to the measured scope, commit and normally
   push. Global README/site skill remains the existing shared installation.

This investigation does not replace missing hardware G1/G2, installed ARM runtime,
Windows USB, storage/ISO, power-loss recovery or the full Phase0–7 objective.

## Reproduced defect and selected correction

At clean `fededbe2`, the real TCP/mTLS delayed-final-read test actually fails
with client ConnectionAborted after both original owners have been joined. The
preceding 256 concurrent production Catalogs at clean b3cdd482 passed and did not
reproduce it. Retain that original test source and failing verdict. This establishes a transport
defect; it does not identify the contents/cause of the old encrypted E59 failure.

The current driver drops its h2/TLS connection immediately after authenticated
stream EOS, consumed receive bytes and local FIN flush. Application END_STREAM
is not an executed TLS shutdown. The fixed h2 0.4.20 source is
[`b0e890842ac16d3c76836fd4fcc06421acb15fa7`](https://github.com/hyperium/h2/blob/b0e890842ac16d3c76836fd4fcc06421acb15fa7/src/proto/connection.rs):
the client polls shutdown when stream references are gone, and the server's
explicit no-error close continues polling its codec through actual shutdown.
Tokio-rustls 0.26.6 polls close_notify and underlying shutdown; its
[buffered-write contract](https://docs.rs/tokio-rustls/0.26.6/tokio_rustls/)
also requires actual flush. No dependency update or copied upstream code.

After the original local FIN is flushed, release only its completed h2 send
handle (subsequent shutdown stays idempotent). After the original receive EOS,
trailers, consumed bytes and returned flow credit, drop the completed receive
and ping handles, retain the unique connection, and poll actual h2/TLS close.
The client can then close naturally without retained stream references. The
server requests NO_ERROR GOAWAY only after this complete single-stream boundary;
it still rejects an observed extra stream/error. Keep all h2 operations/drops
inside existing quiet wrappers. The close phase is interruptible using the
original stop and bounded by the existing response-timeout policy, with actual
typed failure on I/O/protocol/timeout. Do not turn any such failure into PeerEof,
skip partial DATA/FIN, discard unconsumed receive bytes, or lose the outer owner.

The first candidate 41540391 passes the unchanged delayed TLS-read regression
but fails the original half-close/reverse-drain test (Io instead of PeerEof).
Retain that failure. Fully consumed DATA can still leave h2 control work such as
returned window credit; a one-step server abrupt_shutdown is therefore not a
complete orderly-close strategy. Use h2's normal graceful_shutdown progression
under the same bounded original owner, preserving its control-frame drain. Do
not weaken the original half-close assertion or mask its I/O error.

At 800f94c9 the original half-close passes; the delayed-network-read test's
client now succeeds, but the deliberately stalled server correctly reports
LocalDataStall instead of its test's assumed healthy PeerEof. h2's documented
graceful close waits for a control PONG/one RTT, which the gate intentionally
withholds until that owner ends. Correct this **test specification**, preserving
the original source/101 result: add one case releasing the gate after actual FIN
flush and requiring both actual owners PeerEof, and one withholding reads until
the real close deadline and requiring server LocalDataStall and client clean EOF.
Verify the revised negative against the original pre-fix production link source;
it must still fail there. Do not accept arbitrary errors, remove the original
half-close test, add ignores or call a timeout a successful peer completion.

The 980445be/da111ead normal-close regressions still fail (original half-close
and/or resumed TLS peer), with actual server BrokenPipe observation. The fixed
h2 client automatically initiates close when its stream references disappear.
Dropping completed receive/ping and send references **before** orderly connection
close introduced that additional race against the server's graceful control
exchange. Revise the correction: retain the original LinkIo send and driver
receive/ping handles through actual connection close, and drop them only before
publishing the final outcome. No application buffer or peer control work is
discarded; do not mask BrokenPipe. Keep the normal graceful server progression,
existing stop/timeout and strict owner outcomes. Temporary test-only diagnostics
print typed IO kind/reason flags only and will be removed before delivery.

805c267a still fails both normal cases. Source inspection further identifies
that h2 graceful_shutdown queues an idle MAX-ID GOAWAY plus shutdown PING; its
client closes once GOAWAY has no remaining active streams, potentially before
the server's PING progression. Test the complete-stream NO_ERROR close again
while retaining all original stream references (unlike 41540391). This is a
bounded candidate experiment, not a passed fix, and must satisfy both original
half-close and new real-TLS cases before it can be selected.

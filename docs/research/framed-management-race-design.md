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

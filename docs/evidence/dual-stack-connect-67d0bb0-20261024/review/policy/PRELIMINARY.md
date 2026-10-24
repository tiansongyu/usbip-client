# Initial selected-source review (not final runtime acceptance)

The initial 12-file snapshot precedes the parent's style correction and later
fixture changes. It remains preserved with inputs-before.json. The observed
style-only connect.rs hash changed from
3bbbcc2404d9226c52415b69851e4324131bf661790eda99f1fbff9e92e7bc19 to
fc1183ecfe3109c2862211e4241ecfa00eb824141f89adaa0ba729f17fc22538:
`race(addresses, |address| TcpStream::connect(address))` becomes the direct
function argument. This is not falsely reported as a stable before/after pair.

Product source inspection found no must-fix in the policy scheduler, bounded
address ordering, actual-work resolver permit ownership, caller deadline or
winner-only TLS path. Source review is not evidence of a test run.

The parent was notified about these fixture/proof issues before final capture:

1. The literal/hostname test bounded only connect under join!, leaving accept
   able to wait indefinitely after a connect failure. Bound the whole exchange;
   the socket-pair helper has the same containment pattern.
2. The healthy SYN index accepted any same-family destination-port3241 SYN.
   Require the exact fixture client/server IP and MAC tuples when claiming
   exact healthy-endpoint observations.
3. Sorted equal-count stale/healthy SYN timestamps establish aggregate
   chronological pairing, not per-process or per-operation causal attribution.
   Preserve that distinction explicitly rather than silently strengthening it.
4. Real cancellation EOF checks benefit from confirming sockets are still open
   before the cancellation point; synthetic simultaneous-ready DropReceipts
   prove owned-future destruction rather than two installed TLS/USB cases.

Parent message confirms these changes are being implemented, with new actual
hostname catalog positive/negative service-name coverage. Final capture/review
will inspect their actual source after parent notification. No repo edits or
product/fixture/test execution are performed by this review agent.

# Bounded dual-stack TCP racing: protocol research

2026-10-24. Read-only research for RustUSB's existing Rust 1.97.1 / Tokio 1.54.1
stack. This report defines a proposed contract; it is not product, network,
USB, performance, interoperability or full RFC-conformance acceptance.

## Standards findings

[RFC 8305](https://www.rfc-editor.org/rfc/rfc8305.html), December 2017,
recommends 250ms between connection launches and family interleaving after
destination sorting. Host-configured IPv4 preference can be retained; IPv6 need
not be forced ahead of an administrator's policy. Earlier attempts remain
active while later ones start; a successful TCP handshake selects the winner
and remaining attempts should be cancelled. Section 5 requires at least 10ms
between launches and recommends a 100ms minimum. It gives no immediate-failure
exception. Faster-than-250ms replacement therefore still needs a floor.

A completed OS lookup omits section 3's independently arriving AAAA/A queries
and 50ms resolution delay. DNS updates and NAT64 synthesis are also omitted.
Capping results adds a local limit. Describe this as RFC-inspired dual-stack
fallback, without full RFC 8305 conformance. TCP success does not prove working
TLS/application behavior (section 9.2); DNS mapping is not an identity guarantee
(section 10).

[RFC 6724 section 2](https://www.rfc-editor.org/rfc/rfc6724.html#section-2)
intends getaddrinfo to return destination-sorted results. Preserving that order
delegates policy to the platform; it does not certify every platform's sorting.

## Proposed RustUSB contract

- Accept the original validated endpoint. IP literals bypass resolution. Bound
  retained input explicitly: for example at most 32 raw entries and 16 unique
  SocketAddr values, rejecting overflow before connecting instead of silently
  discarding a potentially reachable tail. Preserve full IPv6 scope/port data.
- Deduplicate stably, retain each family's relative order, start with the first
  returned family, and alternate when both families remain.
- Start one attempt immediately; pace pending attempts at 250ms. Following a
  definitive failure, use a documented minimum (100ms recommended here; never
  below 10ms if retaining the section-5 timing constraint). Keep previous pending
  sockets rather than expiring them merely because another family starts.
- Poll a bounded collection of owned TCP futures inline. Success, timeout and
  cancellation drop every loser; no detached dial task or unowned connected
  socket survives the caller. Test simultaneous successes and cancellation.
- Keep one caller deadline across resolution, TCP, TLS and operation setup.
  Resolver permits belong to actual blocking work until it ends, including
  after waiter cancellation; this bounds job count, not OS DNS duration or
  libc allocation. Report a saturated budget separately from remote failure.
- Authenticate only the selected TCP stream using the original configured TLS
  service name. No insecure fallback, credential override or TLS-failure retry
  is introduced by this TCP connector. This is a RustUSB security policy.

## Maintained alternative examined

[libcurl's official option documentation](https://curl.se/libcurl/c/CURLOPT_HAPPY_EYEBALLS_TIMEOUT_MS.html)
currently describes a 200ms default and six-socket rolling cap. Its policies
are a useful comparison, but its broader C/HTTP transport is not needed merely
to race Tokio TCP futures. No dependency is recommended or imported here.

## Evidence and confidence

`sources.json` records exact URLs, final URLs, HTTP status/headers, UTC time,
size and SHA-256 for the saved primary documents. Published text retains its
original IETF Trust notices; no upstream code is copied into product sources.
The errata search GET echoes RFC 8305 and reports no matching records; the web
reader first could not access it, then a terminal GET succeeded. This is the
observed query result, not a future guarantee. High confidence in the cited
standard constraints; implementation ownership/timing and actual blackhole
recovery still require product source review and tests.

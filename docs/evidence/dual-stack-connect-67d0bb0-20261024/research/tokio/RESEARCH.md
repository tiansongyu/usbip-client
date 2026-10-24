# Fixed Tokio DNS and TCP connection audit

2026-10-24. Read-only component research for RustUSB's bounded dual-stack
connector. No repository files, host USB state or host network policy changed.

## Identity and sources

Tokio **1.54.1**, published **2026-10-21T21:05:43.950213Z**, MIT, declared
MSRV1.85. Registry checksum and actual cached published `.crate` SHA-256 both
`b52fd3255258d26fac7264ab5a76b53c13367136dc011d3d6ad4f8ed070396a3`.
Registry `.cargo_vcs_info.json` identifies
`b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8`, path `tokio`.
The fetched fixed upstream `addr.rs` matches the installed source byte-for-byte.
The source files, license, manifests, primary registry response, fixed-version
HTML pages and HTTP/SHA provenance are retained here.

Primary references:

- [Fixed TCP source](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/net/tcp/stream.rs)
- [Fixed DNS adapter](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/net/addr.rs)
- [Fixed blocking semantics](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/task/blocking.rs)
- [Fixed semaphore ownership](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/sync/semaphore.rs)
- [Fixed task-handle semantics](https://github.com/tokio-rs/tokio/blob/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/runtime/task/join.rs)

The web tool could not open the versioned docs URLs; direct HTTPS fetches of
those exact URLs subsequently returned200 and their bytes are preserved.
Moving std docs currently identify1.99.1; they are not evidence of Rust1.97.1
internals. The actual probe compiler is1.97.1.

## Verified behavior and API subset

`TcpStream::connect<A: ToSocketAddrs>(addr:A) -> io::Result<TcpStream>` first
awaits resolution and then sequentially awaits `connect_addr` for each result.
It returns the first success, otherwise the last attempt error. It has no
internal stagger, family race or per-address timeout. A pending first address
can therefore consume the caller's entire outer deadline before later addresses
are attempted. That conclusion follows directly from fixed source and is also
reproduced by the probe below.

The hostname `(&str,u16)` adapter uses a blocking task around std
`ToSocketAddrs`; numeric IPv4/IPv6 inputs instead return an immediately ready
single address. The hostname result is already a vector-backed iterator. A cap
applied after std resolution bounds RustUSB's retained results, not the OS/libc
resolver's internal memory, sockets or duration.

`spawn_blocking<F,R>(F) -> JoinHandle<R>` requires `F:FnOnce()->R+Send+'static`
and `R:Send+'static`. Dropping its handle detaches work. A started blocking
closure cannot be aborted, and normal runtime shutdown waits for it. Queued
work may be prevented from starting by abort, without a general guarantee.

`Arc<Semaphore>::try_acquire_owned()` returns an owned permit or
`TryAcquireError`. Permit Drop restores capacity. Acquiring before submission
and moving a named `_actual_work_permit` binding into the closure correctly
bounds queued/running resolver work even when its async waiter is cancelled.
Do not use `let _ = permit`, which would discard it immediately.

## Actual isolated probes

The offline probe used fixed Tokio1.54.1 and Rust1.97.1, with target/binary bodies
outside this archive. It passed:

- A real loopback listener with a full accept queue leaves candidate one pending.
  Sequential multi-address connect times out after200ms without attempting the
  healthy second listener; a direct connection to that same second listener
  succeeds. No firewall, routing or interface changes were used.
- Four actual blocking closures retain four real permits after all four async
  waiters are actually aborted. A fifth acquisition remains refused. Capacity
  returns only after all four controlled closures finish.
- An actual std lookup of localhost succeeds on the blocking pool.

These are library/runtime probes, not RustUSB product, dual-stack wire, DNS
blackhole, QEMU, USB or performance acceptance.

## Integration constraints and assessment

No must-fix objection to the proposed architecture, provided the implementation
keeps the existing total DNS/TCP/TLS deadline and cancellation boundary, owns
all losing TCP futures until dropped, and selects only TCP before the unchanged
TLS/ALPN/pin/service-name checks. A failed TLS authentication must not trigger an
insecure fallback. Keep the four-work quota inside real resolver closures;
report exhaustion explicitly rather than silently starting unbounded work.
IP literals should bypass DNS work. Preserve scope IDs supplied by OS addresses.
Do not claim resolver cancellation, bounded libc allocation, strict shutdown
under a permanently blocked resolver, or full RFC8305 compliance from this
research. Stagger/interleave policy is a separate protocol decision.

Confidence: high for fixed source/API/identity and observed probe behavior;
no independent security/advisory audit or production latency qualification.

# governor admission-rate library research

2026-10-24. Read-only research; no repository source/commit/USB changes. Current registry max stable is 0.10.4 (released2025-12-16), MIT. Official .crate SHA2569efcab3c1958580ff1f25a2a41be1668f7603d849bb63af523b208a3cc1223b8 verified against saved registry JSON. VCS9f3a79dd47dd32acd589c562b8d4fefe99b93372; nine relevant published files match fixed Git source bytes. There is no declared rust-version/MSRV in package metadata/Cargo.toml. The isolated probe actually compiled/run with project rustc1.97.1, not an MSRV or other-platform qualification.

## Recommendation

Use governor0.10.4 with default-features=false,features=["std"] for the proposed bounded GCRA admission gate. It is a mature algorithm implementation, avoids inventing another bucket, shares one fixed state via caller Arc, and its immediate check() returns without an admission wait. Keep the existing16 connection permit bound independently; that bound alone cannot cap repeated fast failures over time. A checked local bucket remains a feasible smaller-dependency alternative, but adds arithmetic/concurrency logic and tests that this crate already provides. This recommendation assumes documented uptime/clock horizon, validated quota bounds, no arbitrary caller Clock, no awaited until_ready, and no production use of its extreme fake-clock API.

Exact dependency:
```toml
governor = { version = "=0.10.4", default-features = false, features = ["std"] }
```

For production pin the explicit clock rather than relying on a feature-sensitive alias:
```rust
use governor::{RateLimiter, Quota, clock::MonotonicClock, state::{direct::NotKeyed, InMemoryState}};
type Limiter = RateLimiter<NotKeyed, InMemoryState, MonotonicClock>;
let quota = Quota::with_period(Duration::from_millis(interval_millis))
    .ok_or(FIXED_ERROR)?
    .allow_burst(nonzero_burst);
let limiter: Limiter = RateLimiter::direct_with_clock(quota, MonotonicClock);
let admitted = limiter.check().is_ok();
```
For tests C=FakeRelativeClock, use direct_with_clock(quota,clock.clone()) and clock.advance(Duration). Generic alias with default middleware spelled out is RateLimiter<NotKeyed,InMemoryState,C,NoOpMiddleware<<C as Clock>::Instant>>. C needs Clock, not Clone except where the test wants another clock handle. Production DefaultDirectRateLimiter is also valid in the current std-only graph, but its DefaultClock changes to QuantaClock if any other dependency enables governor/quanta through Cargo feature unification.

The requested1..60000ms period and1..256 burst bound keep quota tau multiplication<=60s*255 and exact integer periods. Quota::with_period rejectszero; allow_burst takesNonZeroU32. A successful check irrevocably consumes admission state; dropping its unit success or any later network future does not refund it. Failed check does not updateTAT or postpone future eligibility. Caller must keep one Arc gate per loaded config, never construct a new limiter per handshake/clone, and keep metadata observer separate so it retains no limiter/credentials/network permit.

## Clock/arithmetic and scheduling boundaries

std-only DefaultClock is MonotonicClock, using web_time::Instant (native std Instant wrapper). Reference::duration_since saturates backward-before-start time tozero; it does not report clock reversal. GCRA storedTAT does not decrease, and probe reversal does not mintcells, but a rollback may prolong denial. Do not describe metadata consistent as proof of a healthy clock.

Nanos(u64) represents limiter age from creation, not wall/boot epoch. Duration conversion has expect, add/mul use ordinary integer arithmetic. A deliberately near-u64MAX custom-clock check panics in the debug probe (caught; probe exits0). Therefore not arbitrary-clock/forever arithmetic safe: >~584years lifetime/huge forward clock jumps or fake advance overflow are outside its horizon. Bounded ordinary quotas avoid construction tau overflow but not this documented age limit. FakeRelativeClock::advance itself can panic on a >u64 nanosecond argument or debug overflow on cumulative advancement. Restrict fake tests to small explicit durations. NotUntil::earliest_possible/wait_time_from/Display add Instant+duration; production does not need them, so map .is_err() to the fixed local error rather than formatting this internal error.

Source chain direct_with_clock -> state::RateLimiter::new and check -> Gcra::test_and_update -> InMemoryState::measure_and_replace contains clock sampling andAtomicU64 CAS, no timer construction, background thread, spawned future or admission wait. It is not formally wait-free: CAS can retry under contention. A caller metadata mutex around the synchronous check+record removes ordinary same-gate CAS contention, but the mutex is still a short synchronous wait. No std-futures-timer code runs merely because it is a compiled dependency.

## Dependencies and actual probe scope

The saved normal tree has noquanta,dashmap,rand,getrandom. std unconditionally enables futures-timer/futures-util/futures-sink/parking_lot; cfg-if,hashbrown,nonzero_ext,portable-atomic,smallvec,spinning_top,web-time are unconditional. Metadata lists platform dependencies too; see graph-summary.json. Isolated resolved versions may differ from compatible pins reused in final workspace lock. Do not count this as the final product graph or four-platform build.

Actual isolated debug probe passed64 initial permits with125ms refill,101 immediate noncharging refusals, exact period-1ns/period recovery,100 concurrent Arc clones with exactly64 successes, and a rollback-before-start with no mintedcell. The deliberate near-u64 overflow was caught as expected; this is not a product failure or DoS/hardware test. Probe source/lock, raw log, compiler, metadata/tree, ELF hash and build-product relocation metadata are preserved. ELF/build bodies remain in /tmp/rustusb-auth-rate-governor-probe-target-20261024 outside this evidence directory. No throughput/memory/latency benchmark, USB run or security qualification was done.

One evidence-summary Python helper initially failed because Python3 has no tomllib. Its import ran before writes; corrected helper parses only Cargo-generated name/version pairs by an anchored regex against fixed Git30481f0 lock. This does not change the Rust probe.

## Primary sources

- Registry: https://crates.io/api/v1/crates/governor
- Fixed release docs: https://docs.rs/governor/0.10.4/governor/struct.RateLimiter.html
- Exact quota: https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/quota.rs
- Features/MSRV declaration absence: https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/Cargo.toml
- Clock selection: https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/clock/default.rs
- Clock reference: https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/clock/with_std.rs
- Nanoseconds: https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/nanos.rs
- GCRA decisions: https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/gcra.rs
- Fixed direct state: https://github.com/boinkor-net/governor/blob/9f3a79dd47dd32acd589c562b8d4fefe99b93372/governor/src/state/in_memory.rs
- Fake clock: https://docs.rs/governor/0.10.4/governor/clock/struct.FakeRelativeClock.html

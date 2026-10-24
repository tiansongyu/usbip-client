use governor::{Quota,RateLimiter,clock::{FakeRelativeClock,MonotonicClock,Clock},nanos::Nanos,state::{InMemoryState,NotKeyed}};
use std::{num::NonZeroU32,time::Duration,sync::{Arc,atomic::{AtomicU64,Ordering}}};
fn quota()->Result<Quota,Box<dyn std::error::Error>>{Ok(Quota::with_period(Duration::from_millis(125)).ok_or("zero period")?.allow_burst(NonZeroU32::new(64).ok_or("zero burst")?))}
#[derive(Clone,Default)]struct RawClock(Arc<AtomicU64>);
impl Clock for RawClock {type Instant=Nanos;fn now(&self)->Nanos{Nanos::new(self.0.load(Ordering::SeqCst))}}
fn main()->Result<(),Box<dyn std::error::Error>>{
 let q=quota()?;assert_eq!(q.replenish_interval(),Duration::from_millis(125));assert_eq!(q.burst_size().get(),64);
 let _:RateLimiter<NotKeyed,InMemoryState,MonotonicClock>=RateLimiter::direct(q);
 let clock=FakeRelativeClock::default();let lim=RateLimiter::direct_with_clock(q,clock.clone());
 for _ in 0..64 {assert!(lim.check().is_ok());}assert!(lim.check().is_err());for _ in 0..100{assert!(lim.check().is_err());}
 clock.advance(Duration::from_millis(125)-Duration::from_nanos(1));assert!(lim.check().is_err());clock.advance(Duration::from_nanos(1));assert!(lim.check().is_ok());assert!(lim.check().is_err());
 println!("quota exact;64 immediate checks;101 noncharging refusals;125ms boundary recovery: PASS");
 let clock=FakeRelativeClock::default();let shared=Arc::new(RateLimiter::direct_with_clock(q,clock));let gate=Arc::new(std::sync::Barrier::new(100));let mut tasks=Vec::new();
 for _ in 0..100{let lim=shared.clone();let gate=gate.clone();tasks.push(std::thread::spawn(move||{gate.wait();lim.check().is_ok()}));}
 let mut accepted=0;for task in tasks{if task.join().map_err(|_|"thread panicked")?{accepted+=1;}}assert_eq!(accepted,64);assert!(shared.check().is_err());println!("100 concurrent Arc clones share exactly64 initial decisions: PASS");
 let clock=RawClock::default();clock.0.store(100_000_000,Ordering::SeqCst);let lim=RateLimiter::direct_with_clock(q,clock.clone());for _ in 0..64 {assert!(lim.check().is_ok());}clock.0.store(50_000_000,Ordering::SeqCst);for _ in 0..10 {assert!(lim.check().is_err());}clock.0.store(225_000_000,Ordering::SeqCst);assert!(lim.check().is_ok());assert!(lim.check().is_err());println!("clock reversal before limiter start does not mint cells: PASS");
 // Deliberate arithmetic-edge probe only; no product/USB operation.
 let clock=RawClock::default();let lim=RateLimiter::direct_with_clock(q,clock.clone());clock.0.store(u64::MAX-1,Ordering::SeqCst);let result=std::panic::catch_unwind(||lim.check());
 println!("near_u64_nanos_limit_panicked={}",result.is_err());
 if cfg!(debug_assertions){assert!(result.is_err());}
 Ok(())
}

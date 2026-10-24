//! Nonwaiting authentication admission, separate from connection ownership.

use crate::{Error, Result, SecurityScope};
use governor::{
    Quota, RateLimiter,
    clock::{Clock, MonotonicClock},
    middleware::NoOpMiddleware,
    state::{InMemoryState, direct::NotKeyed},
};
use serde::{Deserialize, Serialize};
use std::{
    num::NonZeroU32,
    sync::{Arc, Mutex},
    time::Duration,
};

/// Immutable, bounded quota: initial burst and replenishment period of one cell.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "PolicyFile", into = "PolicyFile")]
pub struct AdmissionPolicy {
    burst: u16,
    interval_millis: u32,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyFile {
    burst: u16,
    interval_millis: u32,
}

impl AdmissionPolicy {
    /// Require a burst of 1..=256 and one replenished cell every 1..=60,000ms.
    pub fn new(burst: u16, interval_millis: u32) -> Result<Self> {
        if !(1..=256).contains(&burst) {
            return Err(Error::InvalidInput("authentication admission burst"));
        }
        if !(1..=60_000).contains(&interval_millis) {
            return Err(Error::InvalidInput("authentication admission interval"));
        }
        Ok(Self {
            burst,
            interval_millis,
        })
    }

    /// Maximum initial or fully replenished burst, shared across server clones.
    pub fn burst(self) -> u16 {
        self.burst
    }

    /// Time to replenish one admission, not the complete burst.
    pub fn interval_millis(self) -> u32 {
        self.interval_millis
    }

    fn quota(self) -> Result<Quota> {
        let burst = NonZeroU32::new(u32::from(self.burst))
            .ok_or(Error::InvalidInput("authentication admission burst"))?;
        Quota::with_period(Duration::from_millis(u64::from(self.interval_millis)))
            .map(|quota| quota.allow_burst(burst))
            .ok_or(Error::InvalidInput("authentication admission interval"))
    }
}

impl Default for AdmissionPolicy {
    fn default() -> Self {
        Self {
            burst: 64,
            interval_millis: 125,
        }
    }
}

impl TryFrom<PolicyFile> for AdmissionPolicy {
    type Error = Error;
    fn try_from(value: PolicyFile) -> Result<Self> {
        Self::new(value.burst, value.interval_millis)
    }
}

impl From<AdmissionPolicy> for PolicyFile {
    fn from(value: AdmissionPolicy) -> Self {
        Self {
            burst: value.burst,
            interval_millis: value.interval_millis,
        }
    }
}

/// Coherent volatile admission decisions, not TLS results or USB cleanup proof.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AdmissionSnapshot {
    /// One loaded TLS configuration, including its clones and successive jobs.
    pub scope: SecurityScope,
    /// Exact administrator-selected quota, independent of the stream budget.
    pub policy: AdmissionPolicy,
    /// Rate decisions allowing TLS work, including subsequently failed attempts.
    pub admitted: u64,
    /// Rate decisions refusing TLS work before any transport I/O.
    pub limited: u64,
    /// A decision counter saturated; no wraparound is hidden.
    pub saturated: bool,
    /// False after recovered metadata poison; new admissions then fail closed.
    pub consistent: bool,
}

/// Metadata-only observer; retaining it cannot keep a limiter or stream alive.
#[derive(Debug, Clone)]
pub struct AdmissionMonitor(Arc<Mutex<AdmissionSnapshot>>);

impl AdmissionMonitor {
    /// Read a coherent bounded snapshot without changing quota or owners.
    pub fn snapshot(&self) -> AdmissionSnapshot {
        match self.0.lock() {
            Ok(value) => *value,
            Err(poisoned) => {
                let mut value = poisoned.into_inner();
                value.consistent = false;
                *value
            }
        }
    }
}

// Explicit MonotonicClock prevents a future Cargo feature union from changing
// the production clock to quanta. Generic clocks remain internal test seams.
pub(crate) struct Admission<C: Clock = MonotonicClock> {
    limiter: RateLimiter<NotKeyed, InMemoryState, C, NoOpMiddleware<C::Instant>>,
    monitor: AdmissionMonitor,
}

impl Admission<MonotonicClock> {
    pub(crate) fn new(policy: AdmissionPolicy) -> Result<Self> {
        Self::with_clock(policy, MonotonicClock)
    }
}

impl<C: Clock> Admission<C> {
    fn with_clock(policy: AdmissionPolicy, clock: C) -> Result<Self> {
        Ok(Self {
            limiter: RateLimiter::direct_with_clock(policy.quota()?, clock),
            monitor: AdmissionMonitor(Arc::new(Mutex::new(AdmissionSnapshot {
                scope: SecurityScope::LoadedTlsServerConfiguration,
                policy,
                admitted: 0,
                limited: 0,
                saturated: false,
                consistent: true,
            }))),
        })
    }

    pub(crate) fn monitor(&self) -> AdmissionMonitor {
        self.monitor.clone()
    }

    pub(crate) fn check(&self) -> Result<()> {
        let mut snapshot = self
            .monitor
            .0
            .lock()
            .map_err(|_| Error::AdmissionUnavailable)?;
        // Synchronous checked subset only: no readiness future, timer, keyed
        // map, callback, logging, filesystem, device operation or async wait.
        let admitted = self.limiter.check().is_ok();
        let count = if admitted {
            &mut snapshot.admitted
        } else {
            &mut snapshot.limited
        };
        match count.checked_add(1) {
            Some(next) => *count = next,
            None => snapshot.saturated = true,
        }
        if admitted {
            Ok(())
        } else {
            Err(Error::AdmissionRateLimited)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use governor::clock::FakeRelativeClock;

    type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn actual_gate_has_exact_refill_and_noncharging_refusals() -> TestResult {
        let clock = FakeRelativeClock::default();
        let gate = Admission::with_clock(AdmissionPolicy::default(), clock.clone())?;
        for _ in 0..64 {
            gate.check()?;
        }
        for _ in 0..100 {
            assert_eq!(gate.check(), Err(Error::AdmissionRateLimited));
        }
        clock.advance(Duration::from_nanos(124_999_999));
        assert_eq!(gate.check(), Err(Error::AdmissionRateLimited));
        clock.advance(Duration::from_nanos(1));
        gate.check()?;
        assert_eq!(gate.check(), Err(Error::AdmissionRateLimited));
        clock.advance(Duration::from_secs(8));
        for _ in 0..64 {
            gate.check()?;
        }
        assert_eq!(gate.check(), Err(Error::AdmissionRateLimited));
        let snapshot = gate.monitor().snapshot();
        assert_eq!(snapshot.admitted, 129);
        assert_eq!(snapshot.limited, 103);
        assert!(snapshot.consistent && !snapshot.saturated);
        Ok(())
    }

    #[test]
    fn concurrent_clones_share_one_gate_and_observer_cannot_retain_it() -> TestResult {
        let gate = Arc::new(Admission::with_clock(
            AdmissionPolicy::new(8, 60_000)?,
            FakeRelativeClock::default(),
        )?);
        let weak = Arc::downgrade(&gate);
        let observer = gate.monitor();
        let barrier = Arc::new(std::sync::Barrier::new(32));
        let mut tasks = Vec::new();
        for _ in 0..32 {
            let gate = gate.clone();
            let barrier = barrier.clone();
            tasks.push(std::thread::spawn(move || {
                barrier.wait();
                gate.check()
            }));
        }
        let mut admitted = 0;
        for task in tasks {
            match task.join().map_err(|_| "admission thread failed")? {
                Ok(()) => admitted += 1,
                Err(Error::AdmissionRateLimited) => {}
                result => return Err(format!("unexpected admission result {result:?}").into()),
            }
        }
        assert_eq!(admitted, 8);
        drop(gate);
        assert!(weak.upgrade().is_none());
        let snapshot = observer.snapshot();
        assert_eq!((snapshot.admitted, snapshot.limited), (8, 24));
        Ok(())
    }

    #[test]
    fn saturated_counters_remain_visible_and_poison_blocks_new_admission() -> TestResult {
        let gate = Admission::with_clock(
            AdmissionPolicy::new(2, 60_000)?,
            FakeRelativeClock::default(),
        )?;
        gate.monitor
            .0
            .lock()
            .map_err(|_| "metadata poisoned")?
            .admitted = u64::MAX;
        gate.check()?;
        assert!(gate.monitor().snapshot().saturated);
        assert_eq!(gate.monitor().snapshot().admitted, u64::MAX);
        let monitor = gate.monitor();
        assert!(
            std::panic::catch_unwind(move || {
                let _guard = match monitor.0.lock() {
                    Ok(value) => value,
                    Err(value) => value.into_inner(),
                };
                std::panic::resume_unwind(Box::new("test admission metadata poison"));
            })
            .is_err()
        );
        assert_eq!(gate.check(), Err(Error::AdmissionUnavailable));
        assert!(!gate.monitor().snapshot().consistent);
        Ok(())
    }

    #[test]
    fn strict_policy_rejects_disable_aliases_and_unbounded_values() -> TestResult {
        for raw in [
            "null",
            "{}",
            r#"{"burst":0,"interval_millis":1}"#,
            r#"{"burst":257,"interval_millis":1}"#,
            r#"{"burst":1,"interval_millis":0}"#,
            r#"{"burst":1,"interval_millis":60001}"#,
            r#"{"burst":1,"burst":2,"interval_millis":125}"#,
            r#"{"burst":1,"interval_millis":125,"enabled":false}"#,
        ] {
            assert!(serde_json::from_str::<AdmissionPolicy>(raw).is_err());
        }
        for policy in [
            AdmissionPolicy::default(),
            AdmissionPolicy::new(1, 1)?,
            AdmissionPolicy::new(256, 60_000)?,
        ] {
            assert_eq!(
                serde_json::from_slice::<AdmissionPolicy>(&serde_json::to_vec(&policy)?)?,
                policy
            );
            assert_eq!(
                policy.quota()?.replenish_interval(),
                Duration::from_millis(u64::from(policy.interval_millis()))
            );
            assert_eq!(
                policy.quota()?.burst_size().get(),
                u32::from(policy.burst())
            );
        }
        Ok(())
    }
}

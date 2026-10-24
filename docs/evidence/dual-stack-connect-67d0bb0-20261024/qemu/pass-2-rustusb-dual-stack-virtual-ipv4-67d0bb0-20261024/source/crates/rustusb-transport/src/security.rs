//! Volatile, metadata-only observations shared by one loaded TLS configuration.

use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex, MutexGuard};

/// Results of local TLS authentication, not IMPORT or peer acceptance evidence.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TlsCounters {
    /// Attempts entering the TLS stage, including immediate permit refusals.
    pub attempts: u64,
    /// Currently executing attempts; excludes already authenticated streams.
    pub in_flight: u64,
    /// Standard PKI, fixed ALPN and leaf mapping all passed locally.
    pub authenticated: u64,
    /// Rustls handshake or missing peer certificate failed; exact cause unknown.
    pub handshake_failed: u64,
    /// The TLS handshake deadline expired.
    pub timed_out: u64,
    /// PKI passed, but the leaf was absent from the immutable policy.
    pub unmapped_certificate: u64,
    /// A completed handshake did not negotiate the required protocol.
    pub protocol_mismatch: u64,
    /// The shared TLS stream budget was exhausted.
    pub connection_limit: u64,
    /// The attempt future was dropped before a result was recorded.
    pub cancelled: u64,
}

/// Framed-v2 setup, separately counted after successful TLS authentication.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FramingCounters {
    /// Attempts entering framed setup after successful TLS.
    pub attempts: u64,
    /// Currently executing framed setup attempts.
    pub in_flight: u64,
    /// The single-stream driver was successfully constructed.
    pub ready: u64,
    /// Framed setup returned an error, without guessing a remote cause.
    pub failed: u64,
    /// The framed setup deadline expired.
    pub timed_out: u64,
    /// Setup was dropped before recording a result.
    pub cancelled: u64,
}

/// Coherent counters scoped to one constructed TLS server and all its clones.
/// They are neither persistent audit records nor USB release proofs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SecuritySnapshot {
    /// Explicit lifetime boundary; job restarts do not reset a loaded configuration.
    pub scope: SecurityScope,
    /// Local TLS attempt observations.
    pub tls: TlsCounters,
    /// Post-authentication framed-v2 setup observations.
    pub framing: FramingCounters,
    /// Outer listener refusals before entering TLS; distinct from TLS permits.
    pub listener_connection_limit: u64,
    /// At least one counter has saturated; arithmetic identities may no longer hold.
    pub saturated: bool,
    /// False after recovered metadata poison or an inconsistent gauge update.
    pub consistent: bool,
}

impl Default for SecuritySnapshot {
    fn default() -> Self {
        Self {
            scope: SecurityScope::LoadedTlsServerConfiguration,
            tls: TlsCounters::default(),
            framing: FramingCounters::default(),
            listener_connection_limit: 0,
            saturated: false,
            consistent: true,
        }
    }
}

/// Closed observation scope; there is no implicit process or durable history.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SecurityScope {
    /// One constructed TLS server, including its clones until configuration reload.
    LoadedTlsServerConfiguration,
}

/// A nonowning metadata handle: no credentials, streams, permits or device owners.
#[derive(Debug, Default, Clone)]
pub struct SecurityMonitor(Arc<Mutex<SecuritySnapshot>>);

fn increment(value: &mut u64, saturated: &mut bool) {
    match value.checked_add(1) {
        Some(next) => *value = next,
        None => *saturated = true,
    }
}

impl SecurityMonitor {
    fn lock(&self) -> MutexGuard<'_, SecuritySnapshot> {
        match self.0.lock() {
            Ok(value) => value,
            Err(poisoned) => {
                let mut value = poisoned.into_inner();
                value.consistent = false;
                value
            }
        }
    }

    /// Copy a coherent, fixed-size snapshot without filesystem or owner access.
    pub fn snapshot(&self) -> SecuritySnapshot {
        *self.lock()
    }

    /// Record a real outer listener-budget refusal before TLS is attempted.
    /// This observation never authorizes or rejects a connection itself.
    pub fn listener_connection_limit(&self) {
        let mut snapshot = self.lock();
        let SecuritySnapshot {
            listener_connection_limit,
            saturated,
            ..
        } = &mut *snapshot;
        increment(listener_connection_limit, saturated);
    }

    pub(crate) fn begin(&self, stage: Stage) -> Attempt {
        let mut snapshot = self.lock();
        let SecuritySnapshot {
            tls,
            framing,
            saturated,
            ..
        } = &mut *snapshot;
        let (attempts, in_flight) = match stage {
            Stage::Tls => (&mut tls.attempts, &mut tls.in_flight),
            Stage::Framing => (&mut framing.attempts, &mut framing.in_flight),
        };
        increment(attempts, saturated);
        increment(in_flight, saturated);
        Attempt {
            monitor: self.clone(),
            stage,
            outcome: Outcome::Cancelled,
        }
    }
}

#[derive(Clone, Copy)]
pub(crate) enum Stage {
    Tls,
    Framing,
}

pub(crate) enum Outcome {
    Ready,
    Failed,
    TimedOut,
    Unmapped,
    ProtocolMismatch,
    ConnectionLimit,
    Cancelled,
}

/// Retains only metadata. Drop records actual future cancellation, never cleanup.
pub(crate) struct Attempt {
    monitor: SecurityMonitor,
    stage: Stage,
    outcome: Outcome,
}

impl Attempt {
    pub(crate) fn finish(mut self, outcome: Outcome) {
        self.outcome = outcome;
    }
}

impl Drop for Attempt {
    fn drop(&mut self) {
        let mut snapshot = self.monitor.lock();
        let SecuritySnapshot {
            tls,
            framing,
            saturated,
            consistent,
            ..
        } = &mut *snapshot;
        let (in_flight, outcome) = match self.stage {
            Stage::Tls => (
                &mut tls.in_flight,
                match self.outcome {
                    Outcome::Ready => &mut tls.authenticated,
                    Outcome::Failed => &mut tls.handshake_failed,
                    Outcome::TimedOut => &mut tls.timed_out,
                    Outcome::Unmapped => &mut tls.unmapped_certificate,
                    Outcome::ProtocolMismatch => &mut tls.protocol_mismatch,
                    Outcome::ConnectionLimit => &mut tls.connection_limit,
                    Outcome::Cancelled => &mut tls.cancelled,
                },
            ),
            Stage::Framing => (
                &mut framing.in_flight,
                match self.outcome {
                    Outcome::Ready => &mut framing.ready,
                    Outcome::TimedOut => &mut framing.timed_out,
                    Outcome::Cancelled => &mut framing.cancelled,
                    _ => &mut framing.failed,
                },
            ),
        };
        match in_flight.checked_sub(1) {
            Some(next) => *in_flight = next,
            None => *consistent = false,
        }
        increment(outcome, saturated);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn overflow_and_metadata_poison_remain_visible_without_changing_owner_state() {
        let monitor = SecurityMonitor::default();
        monitor.lock().listener_connection_limit = u64::MAX;
        monitor.listener_connection_limit();
        assert_eq!(monitor.snapshot().listener_connection_limit, u64::MAX);
        assert!(monitor.snapshot().saturated);
        assert!(monitor.snapshot().consistent);
        let poisoned = monitor.clone();
        let result = std::panic::catch_unwind(move || {
            let _guard = poisoned.lock();
            std::panic::resume_unwind(Box::new("test metadata poison"));
        });
        assert!(result.is_err());
        assert!(!monitor.snapshot().consistent);
        monitor.begin(Stage::Tls).finish(Outcome::Ready);
        let snapshot = monitor.snapshot();
        assert_eq!(snapshot.tls.authenticated, 1);
        assert_eq!(snapshot.tls.in_flight, 0);
        assert!(!snapshot.consistent);
    }
}

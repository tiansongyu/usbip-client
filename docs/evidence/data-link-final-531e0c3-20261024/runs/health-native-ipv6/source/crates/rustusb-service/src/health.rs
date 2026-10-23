//! Nonowning, bounded data-link observations, separate from device lifecycle proof.

use crate::model::SessionRole;
use rustusb_core::{
    Error, Result,
    application::{InstanceId, SessionId},
    link_health::LinkHealth,
};
use rustusb_transport::LinkMonitor;
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};

/// Whether a link observation belongs to a currently retained owner.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum HealthAvailability {
    /// A current owner's nonowning link monitor supplied this sample.
    Live,
    /// The previous process epoch cannot supply live monotonic observations.
    PreviousEpoch,
    /// The actual owner has ended; the last sample is historical.
    OwnerEnded,
    /// No negotiated data-link observation has been received.
    NotObserved,
    /// A published export is not one particular incoming data connection.
    NotDataSession,
}

/// A payload-free link observation; it never changes a device's lifecycle state.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionHealth {
    /// Original application session identity.
    pub session_id: SessionId,
    /// Epoch qualifying process-local transport identities and elapsed times.
    pub instance_id: InstanceId,
    /// Distinguishes a local import from remote acceptance or an export.
    pub role: SessionRole,
    /// Current owner versus retained historical observation.
    pub availability: HealthAvailability,
    /// None means not observed/not applicable, never a healthy zero RTT.
    pub link: Option<LinkHealth>,
}

#[derive(Clone)]
enum Source {
    Raw,
    Direct(LinkMonitor),
    #[cfg(feature = "rust-backend")]
    Server(
        rustusb_rust_backend::ServerControl,
        rustusb_core::server_session::ServerSessionId,
    ),
}
#[derive(Default)]
struct ReporterState {
    source: Option<Source>,
    final_sample: Option<Option<LinkHealth>>,
}

/// A nonowning observation cell returned with a durably admitted session.
/// It contains no USB handle, TLS stream, permit or durable acknowledgement.
#[derive(Clone, Default)]
pub struct HealthReporter(Arc<Mutex<ReporterState>>);
impl HealthReporter {
    fn set(&self, source: Source) -> Result<()> {
        let mut state = self.0.lock().map_err(|_| poisoned())?;
        if state.source.is_some() || state.final_sample.is_some() {
            return Err(Error::State(
                "session link observation already registered".into(),
            ));
        }
        state.source = Some(source);
        Ok(())
    }
    pub(crate) fn direct(&self, monitor: Option<LinkMonitor>) -> Result<()> {
        self.set(monitor.map_or(Source::Raw, Source::Direct))
    }
    #[cfg(feature = "rust-backend")]
    pub(crate) fn server(
        &self,
        control: rustusb_rust_backend::ServerControl,
        id: rustusb_core::server_session::ServerSessionId,
    ) -> Result<()> {
        self.set(Source::Server(control, id))
    }
    pub(crate) fn snapshot(&self) -> Result<Option<LinkHealth>> {
        let (source, final_sample) = {
            let state = self.0.lock().map_err(|_| poisoned())?;
            (state.source.clone(), state.final_sample)
        };
        if let Some(sample) = final_sample {
            return Ok(sample);
        }
        let value = match source {
            None => None,
            Some(Source::Raw) => Some(LinkHealth::raw_v1()),
            Some(Source::Direct(monitor)) => Some(
                monitor
                    .snapshot()
                    .map_err(|_| Error::State("data-link observation unavailable".into()))?,
            ),
            #[cfg(feature = "rust-backend")]
            Some(Source::Server(control, id)) => control.link_health(id)?,
        };
        if let Some(value) = value {
            value.validate()?;
        }
        Ok(value)
    }
    pub(crate) fn freeze(&self) -> Result<()> {
        let sample = self.snapshot()?;
        let mut state = self.0.lock().map_err(|_| poisoned())?;
        if state.final_sample.is_none() {
            state.final_sample = Some(sample);
            state.source = None;
        }
        Ok(())
    }
}
fn poisoned() -> Error {
    Error::State("session health registry poisoned".into())
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct LinkHistory {
    pub session_id: SessionId,
    pub instance_id: InstanceId,
    #[serde(deserialize_with = "required_link")]
    pub link: Option<LinkHealth>,
}

fn required_link<'de, D: serde::Deserializer<'de>>(
    decoder: D,
) -> std::result::Result<Option<LinkHealth>, D::Error> {
    Option::<LinkHealth>::deserialize(decoder)
}

pub(crate) fn check_progress(before: Option<LinkHealth>, after: Option<LinkHealth>) -> Result<()> {
    let Some(after) = after else {
        return if before.is_none() {
            Ok(())
        } else {
            Err(Error::State("link observation disappeared".into()))
        };
    };
    after.validate()?;
    if let Some(before) = before {
        if before.protocol != after.protocol
            || before.identity != after.identity
            || after.sample_sequence < before.sample_sequence
            || after.elapsed_millis < before.elapsed_millis
            || before.end.is_some() && before != after
        {
            return Err(Error::State(
                "link identity or observation moved backwards".into(),
            ));
        }
        if let (Some(a), Some(b)) = (before.stats, after.stats)
            && (b.app_read_bytes < a.app_read_bytes || b.app_write_bytes < a.app_write_bytes)
        {
            return Err(Error::State(
                "link application counters moved backwards".into(),
            ));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::link_health::*;

    fn sample() -> Result<LinkHealth> {
        let address = "[ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff]:65535"
            .parse()
            .map_err(|_| Error::State("fixture address".into()))?;
        Ok(LinkHealth {
            protocol: LinkProtocol::FramedV2,
            identity: Some(LinkIdentity {
                connection: u64::MAX,
                tcp: TcpTuple {
                    local: address,
                    peer: address,
                },
            }),
            sample_sequence: u64::MAX - 1,
            elapsed_millis: u64::MAX,
            phase: LinkPhase::Open,
            probe: ProbeState::ReplyObserved,
            last_probe_sequence: Some(u64::MAX),
            last_probe_enqueued_millis: Some(u64::MAX),
            last_reply_sequence: Some(u64::MAX),
            last_reply_millis: Some(u64::MAX),
            last_round_trip_micros: Some(u64::MAX),
            stats: Some(LinkStats {
                app_read_bytes: u64::MAX,
                app_write_bytes: u64::MAX,
                last_app_read_millis: Some(u64::MAX),
                last_app_write_millis: Some(u64::MAX),
                receive_pending_bytes: 65_535,
                send_capacity_blocked: false,
                transport_write_blocked: false,
            }),
            end: None,
        })
    }

    #[test]
    fn maximum_fixed_sample_fits_reserved_space_and_rtt_can_decrease() -> Result<()> {
        let before = sample()?;
        before.validate()?;
        let mut after = before;
        after.sample_sequence += 1;
        after.last_round_trip_micros = Some(1);
        check_progress(Some(before), Some(after))?;
        let entry = LinkHistory {
            session_id: SessionId::new("ffffffff-ffff-ffff-ffff-ffffffffffff")?,
            instance_id: InstanceId::new("ffffffff-ffff-ffff-ffff-ffffffffffff")?,
            link: Some(after),
        };
        let size = serde_json::to_vec(&entry)
            .map_err(|_| Error::State("fixture JSON".into()))?
            .len();
        assert!(size <= 2048);
        let mut backwards = after;
        if let Some(stats) = &mut backwards.stats {
            stats.app_read_bytes -= 1;
        }
        assert!(check_progress(Some(after), Some(backwards)).is_err());
        let mut changed_identity = after;
        if let Some(identity) = &mut changed_identity.identity {
            identity.connection -= 1;
        }
        assert!(check_progress(Some(after), Some(changed_identity)).is_err());
        after.phase = LinkPhase::Ended;
        after.probe = ProbeState::TimedOut;
        after.end = Some(LinkEnd::ProbeTimeout);
        check_progress(Some(before), Some(after))?;
        let mut changed_terminal = after;
        changed_terminal.last_round_trip_micros = Some(2);
        assert!(check_progress(Some(after), Some(changed_terminal)).is_err());
        assert!(check_progress(Some(after), None).is_err());
        Ok(())
    }

    #[test]
    fn frozen_raw_observation_never_gains_live_rtt_or_changes_source() -> Result<()> {
        let reporter = HealthReporter::default();
        assert_eq!(reporter.snapshot()?, None);
        reporter.direct(None)?;
        reporter.freeze()?;
        assert_eq!(reporter.snapshot()?, Some(LinkHealth::raw_v1()));
        assert!(reporter.direct(None).is_err());
        assert!(check_progress(Some(LinkHealth::raw_v1()), Some(sample()?)).is_err());
        Ok(())
    }
}

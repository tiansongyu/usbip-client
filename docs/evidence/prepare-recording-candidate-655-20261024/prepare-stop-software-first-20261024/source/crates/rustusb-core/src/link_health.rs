//! Bounded facts about the transport carrying an actual USB/IP byte stream.
//!
//! A responding transport endpoint does not prove USB completion or consumption.

use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;

// Nullable fields remain required in strict persisted/IPC samples. Using a
// direct field deserializer preserves serde's duplicate-field rejection.
fn required_option<'de, D, T>(deserializer: D) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

/// Explicitly configured transport version; failed v2 never falls back to v1.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkProtocol {
    /// Original unframed stream, without application heartbeat evidence.
    RawV1,
    /// One bounded h2 stream and its connection-level probes over mutual TLS.
    FramedV2,
}

/// Byte-stream lifetime, separate from device and probe state.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkPhase {
    /// Network/framing preparation has not completed.
    Starting,
    /// Both application directions are open.
    Open,
    /// A direction ended; buffered data or the other direction can remain.
    Draining,
    /// The actual driver finished.
    Ended,
}

/// Latest local probe fact. A reply is not a USB-health claim.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProbeState {
    /// Raw-v1 cannot supply this evidence.
    UnavailableRawV1,
    /// No probe reply has yet been observed.
    AwaitingFirstReply,
    /// One library probe is outstanding.
    Pending,
    /// The library matched the most recently completed probe.
    ReplyObserved,
    /// A pending probe exceeded its absolute response deadline.
    TimedOut,
    /// Driver stop or a non-timeout terminal condition ended probing.
    Stopped,
}

/// Redacted terminal cause; never an arbitrary peer error string.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkEnd {
    /// The owning application requested cooperation.
    LocalStop,
    /// Clean remote EOF and remaining received bytes have been consumed.
    PeerEof,
    /// The actual network I/O failed.
    Io,
    /// Framing or the fixed single-stream contract failed.
    Protocol,
    /// The one outstanding probe timed out.
    ProbeTimeout,
    /// A bounded monotonic counter could not be incremented.
    CounterOverflow,
    /// Connection ended but buffered data exceeded its local drain grace.
    LocalDataStall,
}

/// Addresses obtained from the actual connected TCP socket before TLS.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TcpTuple {
    /// This socket's local address and port.
    pub local: SocketAddr,
    /// This socket's connected peer address and port.
    pub peer: SocketAddr,
}

/// Process-local connection identity, qualified by the application's epoch.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinkIdentity {
    /// Nonzero checked process-local sequence; not a credential or wire nonce.
    pub connection: u64,
    /// Actual socket tuple, never a caller-supplied authorization assertion.
    pub tcp: TcpTuple,
}

/// Actual application byte progress and observed backpressure.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinkStats {
    /// Bytes delivered to the application, including management and USB/IP headers.
    pub app_read_bytes: u64,
    /// Bytes accepted from the application, excluding h2 and TLS overhead.
    pub app_write_bytes: u64,
    /// Connection-relative monotonic time of the last nonempty application read.
    #[serde(deserialize_with = "required_option")]
    pub last_app_read_millis: Option<u64>,
    /// Connection-relative monotonic time of the last nonempty application write.
    #[serde(deserialize_with = "required_option")]
    pub last_app_write_millis: Option<u64>,
    /// Received but unconsumed payload, including pump frame and pipe, not RSS.
    pub receive_pending_bytes: u32,
    /// An application write currently waits for stream flow-control capacity.
    pub send_capacity_blocked: bool,
    /// Actual underlying TLS write/flush was observed pending.
    pub transport_write_blocked: bool,
}

/// One bounded latest sample, or an explicitly unavailable raw-v1 projection.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinkHealth {
    /// Actual configured and negotiated mode.
    pub protocol: LinkProtocol,
    /// Present for live or historical v2, absent for raw-v1.
    #[serde(deserialize_with = "required_option")]
    pub identity: Option<LinkIdentity>,
    /// Checked local publication sequence within this connection.
    pub sample_sequence: u64,
    /// Monotonic elapsed time from this connection's creation.
    pub elapsed_millis: u64,
    /// Actual transport phase, never a device-state substitute.
    pub phase: LinkPhase,
    /// Latest probe fact.
    pub probe: ProbeState,
    /// Local probe sequence, not the library's fixed on-wire opaque payload.
    #[serde(deserialize_with = "required_option")]
    pub last_probe_sequence: Option<u64>,
    /// Time the last probe was enqueued, not a wire-transmit timestamp.
    #[serde(deserialize_with = "required_option")]
    pub last_probe_enqueued_millis: Option<u64>,
    /// Local sequence of the most recently matched library reply.
    #[serde(deserialize_with = "required_option")]
    pub last_reply_sequence: Option<u64>,
    /// Time the latest matching library reply was observed.
    #[serde(deserialize_with = "required_option")]
    pub last_reply_millis: Option<u64>,
    /// Enqueue-to-library-reply elapsed time, including queueing and scheduling.
    #[serde(deserialize_with = "required_option")]
    pub last_round_trip_micros: Option<u64>,
    /// Absent for raw-v1; no fabricated zero-valued evidence.
    #[serde(deserialize_with = "required_option")]
    pub stats: Option<LinkStats>,
    /// Actual driver terminal cause; absent while still running.
    #[serde(deserialize_with = "required_option")]
    pub end: Option<LinkEnd>,
}

impl LinkHealth {
    /// Construct an explicit unavailable projection without inventing evidence.
    pub fn raw_v1() -> Self {
        Self {
            protocol: LinkProtocol::RawV1,
            identity: None,
            sample_sequence: 0,
            elapsed_millis: 0,
            phase: LinkPhase::Ended,
            probe: ProbeState::UnavailableRawV1,
            last_probe_sequence: None,
            last_probe_enqueued_millis: None,
            last_reply_sequence: None,
            last_reply_millis: None,
            last_round_trip_micros: None,
            stats: None,
            end: None,
        }
    }

    /// Validate persisted or IPC facts without trusting absent/contradictory fields.
    /// This cannot prove their provenance; only a live transport creates evidence.
    pub fn validate(&self) -> Result<()> {
        let invalid = || Error::InvalidInput("inconsistent link-health sample".into());
        if self.protocol == LinkProtocol::RawV1 {
            return if *self == Self::raw_v1() {
                Ok(())
            } else {
                Err(invalid())
            };
        }
        let identity = self.identity.ok_or_else(invalid)?;
        let stats = self.stats.ok_or_else(invalid)?;
        if identity.connection == 0
            || identity.tcp.local.port() == 0
            || identity.tcp.peer.port() == 0
            || stats.receive_pending_bytes > 65_535
            || self.probe == ProbeState::UnavailableRawV1
            || (self.phase == LinkPhase::Ended) != self.end.is_some()
            || (self.probe == ProbeState::TimedOut) != (self.end == Some(LinkEnd::ProbeTimeout))
            || self.last_probe_sequence.is_some() != self.last_probe_enqueued_millis.is_some()
            || self.last_reply_sequence.is_some() != self.last_reply_millis.is_some()
            || self.last_reply_sequence.is_some() != self.last_round_trip_micros.is_some()
            || self.last_probe_sequence == Some(0)
            || self.last_reply_sequence == Some(0)
            || self.last_reply_sequence > self.last_probe_sequence
            || (self.probe == ProbeState::Pending && self.last_probe_sequence.is_none())
            || (self.probe == ProbeState::ReplyObserved && self.last_reply_sequence.is_none())
            || [
                self.last_probe_enqueued_millis,
                self.last_reply_millis,
                stats.last_app_read_millis,
                stats.last_app_write_millis,
            ]
            .into_iter()
            .flatten()
            .any(|time| time > self.elapsed_millis)
        {
            return Err(invalid());
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn nullable_health_fields_are_required_and_duplicates_are_rejected()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let sample = LinkHealth::raw_v1();
        let encoded = serde_json::to_string(&sample)?;
        assert_eq!(serde_json::from_str::<LinkHealth>(&encoded)?, sample);
        for field in [
            "identity",
            "last_probe_sequence",
            "last_probe_enqueued_millis",
            "last_reply_sequence",
            "last_reply_millis",
            "last_round_trip_micros",
            "stats",
            "end",
        ] {
            let mut missing = serde_json::to_value(sample)?;
            missing
                .as_object_mut()
                .ok_or("fixture object")?
                .remove(field);
            assert!(
                serde_json::from_value::<LinkHealth>(missing).is_err(),
                "missing {field}"
            );
            let duplicate = format!(
                "{{\"{field}\":null,{}",
                encoded.get(1..).ok_or("fixture JSON")?
            );
            assert!(
                serde_json::from_str::<LinkHealth>(&duplicate).is_err(),
                "duplicate {field}"
            );
        }
        Ok(())
    }

    #[test]
    fn nullable_progress_times_are_explicit_even_when_null()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let stats = LinkStats {
            app_read_bytes: 0,
            app_write_bytes: 0,
            last_app_read_millis: None,
            last_app_write_millis: None,
            receive_pending_bytes: 0,
            send_capacity_blocked: false,
            transport_write_blocked: false,
        };
        let encoded = serde_json::to_string(&stats)?;
        assert_eq!(serde_json::from_str::<LinkStats>(&encoded)?, stats);
        for field in ["last_app_read_millis", "last_app_write_millis"] {
            let mut missing = serde_json::to_value(stats)?;
            missing
                .as_object_mut()
                .ok_or("fixture object")?
                .remove(field);
            assert!(
                serde_json::from_value::<LinkStats>(missing).is_err(),
                "missing {field}"
            );
            let duplicate = format!(
                "{{\"{field}\":null,{}",
                encoded.get(1..).ok_or("fixture JSON")?
            );
            assert!(
                serde_json::from_str::<LinkStats>(&duplicate).is_err(),
                "duplicate {field}"
            );
        }
        Ok(())
    }

    #[test]
    fn legacy_projection_cannot_claim_a_probe_or_zero_stats() {
        let mut sample = LinkHealth::raw_v1();
        assert!(sample.validate().is_ok());
        sample.last_round_trip_micros = Some(0);
        assert!(sample.validate().is_err());
    }

    #[test]
    fn v2_requires_real_identity_stats_and_consistent_probe_relationships() -> Result<()> {
        let mut sample = LinkHealth::raw_v1();
        sample.protocol = LinkProtocol::FramedV2;
        assert!(sample.validate().is_err());
        sample.identity = Some(LinkIdentity {
            connection: 1,
            tcp: TcpTuple {
                local: "127.0.0.1:1234"
                    .parse()
                    .map_err(|_| Error::State("fixture".into()))?,
                peer: "127.0.0.1:5678"
                    .parse()
                    .map_err(|_| Error::State("fixture".into()))?,
            },
        });
        sample.stats = Some(LinkStats {
            app_read_bytes: 0,
            app_write_bytes: 0,
            last_app_read_millis: None,
            last_app_write_millis: None,
            receive_pending_bytes: 0,
            send_capacity_blocked: false,
            transport_write_blocked: false,
        });
        sample.phase = LinkPhase::Open;
        sample.probe = ProbeState::AwaitingFirstReply;
        sample.validate()?;
        sample.last_probe_sequence = Some(1);
        sample.last_probe_enqueued_millis = Some(0);
        sample.probe = ProbeState::Pending;
        sample.validate()?;
        sample.last_reply_sequence = Some(2);
        sample.last_reply_millis = Some(0);
        sample.last_round_trip_micros = Some(0);
        assert!(sample.validate().is_err());
        Ok(())
    }
}

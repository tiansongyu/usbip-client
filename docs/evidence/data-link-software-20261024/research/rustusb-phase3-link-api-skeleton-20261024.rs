//! API declaration sheet for root/N/I/H freeze, not an implementation or build result.
//! Intended concrete inherent methods are written as signature comments, avoiding
//! placeholder bodies. Only this /tmp document exists; no repository crate was added.

// ---- rustusb-core::link_health: pure, bounded serde DTOs ----------------------
use serde::{Deserialize, Serialize};
use std::net::SocketAddr;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkProtocol { RawV1, FramedV2 }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkPhase { Starting, Open, Draining, Ended }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProbeState { UnavailableRawV1, AwaitingFirstReply, Pending, ReplyObserved, TimedOut, Stopped }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LinkEnd { LocalStop, PeerEof, Io, Protocol, ProbeTimeout, CounterOverflow }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TcpTuple {
    pub local: SocketAddr,
    pub peer: SocketAddr,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinkIdentity {
    /// Nonzero process-local counter, checked not to wrap. Not a bearer token.
    /// Service qualifies it with existing instance/session IDs; never reused in process.
    pub connection: u64,
    /// Captured from the actual connected TcpStream before moving it into TLS.
    pub tcp: TcpTuple,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinkStats {
    /// Bytes actually delivered to/accepted from this byte-stream application;
    /// includes management/USB-IP headers; excludes h2 control/TLS overhead.
    /// Existing IMPORT-only Session::Metrics remains unchanged and separate.
    pub app_read_bytes: u64,
    pub app_write_bytes: u64,
    pub last_app_read_millis: Option<u64>,
    pub last_app_write_millis: Option<u64>,
    /// Received payload not consumed by the app, including pump frame and pipe; not RSS.
    pub receive_pending_bytes: u32,
    /// Observations, not guesses about device health.
    pub send_capacity_blocked: bool,
    pub transport_write_blocked: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LinkHealth {
    pub protocol: LinkProtocol,
    /// None only for legacy/historical unavailable projection, never a live v2.
    pub identity: Option<LinkIdentity>,
    pub sample_sequence: u64,
    pub elapsed_millis: u64,
    pub phase: LinkPhase,
    pub probe: ProbeState,
    /// Local pending-probe correlation, NOT a wire nonce (h2 USER payload is fixed).
    pub last_probe_sequence: Option<u64>,
    pub last_probe_enqueued_millis: Option<u64>,
    pub last_reply_sequence: Option<u64>,
    pub last_reply_millis: Option<u64>,
    /// send_ping enqueue -> library pong, includes scheduling/queues.
    pub last_round_trip_micros: Option<u64>,
    pub stats: Option<LinkStats>,
    pub end: Option<LinkEnd>,
}

// Required validators: v1 all probe/RTT values None; live v2 identity required;
// checked monotonic counters/seq; reply<=probe; times<=elapsed; RTT may decrease;
// ended/timeout sticky; final samples freeze; no wall-clock/new-epoch Instant reuse.

// ---- rustusb-transport public surface --------------------------------------
// pub const ALPN_V1: &[u8] = b"rustusb-usbip/1";
// pub const ALPN_V2: &[u8] = b"rustusb-usbip/2";
// Existing ALPN alias and old new/connect/accept signatures keep raw-v1 behavior.
//
// pub struct ProbePolicy { interval: Duration, response_timeout: Duration }
// impl ProbePolicy {
//   pub fn new(interval: Duration, response_timeout: Duration) -> Result<Self>;
//   pub fn interval(&self) -> Duration;
//   pub fn response_timeout(&self) -> Duration;
// }
// Defaults5s/15s; interval1..=60s, timeout>=2*interval and <=300s; no RPC override.
// Wire memory/stream/header limits are fixed v2 profile constants, not arbitrary input.
//
// pub struct Link<I = LinkIo> {
//   pub io: I,
//   pub owner: LinkOwner,
//   pub monitor: LinkMonitor,
// }
// pub type ClientLink = Link<ClientLinkIo>;
// pub type ServerLink = Link<AuthenticatedLinkIo>;
// ClientLinkIo/AuthenticatedLinkIo delegate AsyncRead+AsyncWrite to LinkIo;
// their verified server pin / peer are private and obtained only by TLS code.
// impl ClientLinkIo { pub fn server_certificate_pin(&self)->CertificatePin; }
// impl AuthenticatedLinkIo { pub fn peer(&self)->&VerifiedPeer; }
//
// pub struct LinkOutcome { pub reason: LinkEnd, pub final_health: LinkHealth }
// impl LinkOwner {
//   pub fn request_stop(&self);
//   pub async fn join(self) -> Result<LinkOutcome>;
// }
// impl LinkMonitor {
//   pub fn snapshot(&self) -> Result<LinkHealth>;
//   pub fn changes(&self) -> watch::Receiver<u64>;
//   /// Sticky actual driver ended, not USB cleanup proof. No stop capability.
//   pub fn ended(&self) -> watch::Receiver<bool>;
// }
// LinkIo: AsyncRead+AsyncWrite+Unpin+Send; no Clone/into_inner/h2 public handle.
// LinkIo Drop requests driver stop; owner retained+join on all production paths.
// A timeout/error makes read/write/flush immediately fail (discard stale cached data);
// normal END_STREAM instead drains buffered bytes then returns0, preserving reverse half.
//
// impl TlsClient {
//   pub fn new_with_link(roots:TrustRoots,id:DerIdentity,policy:ProbePolicy)->Result<Self>;
//   pub fn protocol(&self)->LinkProtocol;
//   pub async fn connect_link(&self,name:&str,tcp:TcpStream)->Result<ClientLink>;
// }
// impl TlsServer {
//   pub fn new_with_link(roots:TrustRoots,id:DerIdentity,acl:AccessPolicy,policy:ProbePolicy)->Result<Self>;
//   pub fn protocol(&self)->LinkProtocol;
//   pub async fn accept_link(&self,tcp:TcpStream)->Result<ServerLink>;
// }
// Recommended first slice: new *_link methods are explicit v2-only, reject raw config;
// v1 uses existing raw methods and service projects unavailable (no fake raw driver).
// If root chooses a unified factory enum instead, freeze that alternative explicitly.
// Real TCP factories read local_addr/peer_addr themselves; public caller cannot claim a tuple.
// Private generic IO constructors exist only inside transport for tests/implementation.

// ---- trusted config2 (root-owned, proposed names) ---------------------------
// Old strict schema1 -> RawV1; existing Loaded{Client,Server}Config shape retained.
// Strict schema2 keeps all existing role/DER/ACL/pool fields and additionally requires:
//   "data_link": {
//     "protocol": "framed_v2",
//     "interval_millis": 5000,
//     "response_timeout_millis": 15000
//   }
// Required, deny_unknown_fields; old DTO frozen; no serde default manufactures v2.
// No endpoint/path/ALPN/policy override through agent control RPC.
// Config2 constructs new_with_link; TLS config holds protocol/policy. No fallback on errors.

// ---- crate-private transport construction and quiet/flush boundaries --------
// pub(crate) async fn link::client<S>(
//   verified:S, actual:TcpTuple, policy:ProbePolicy
// )->Result<Link<LinkIo>> where S:AsyncRead+AsyncWrite+Unpin+Send+'static;
// pub(crate) async fn link::server<S>(...same...)->Result<Link<LinkIo>>;
// Only tls.rs passes authenticated streams; tests identify their synthetic IO honestly.
// link.rs owns h2 setup/stream1/RX pump/probe/Connection driver and all queue limits.
// private quiet.rs:
//   fn call<T>(f:impl FnOnce()->T)->T;              // scoped NoSubscriber Dispatch
//   Quiet<T> / QuietFuture<F>: calls,poll,Ready mapping and Drop all quiet;
//   no raw h2 error/handle/Future/FlowControl escapes; map to typed static reasons.
// Entire h2 src fixed crate has no production spawn; caller owns every future.
//
// Root's flush.rs (no health DTO dependency):
//   TrackingIo::new(inner, Side::{Client,Server})->(Self,FlushMonitor)
//   FlushMonitor::poll_flushed(&mut self,cx,target_data:u64,require_fin:bool)
//       ->Poll<io::Result<()>>
// Optional necessary stats hook, owned by root not link.rs:
//   FlushMonitor::snapshot()->FlushSnapshot
//   FlushSnapshot{completed_data:u64,flushed_data:u64,fin_seen:bool,
//                 fin_flushed:bool,write_pending:bool,closed:bool}
// LinkIo increments data_enqueued checked only after successful send_data;
// poll_flush waits target=data_enqueued; poll_shutdown emits one empty FIN then
// waits same target+require_fin. Connection/reset/fatal/end wakes every waiter.
// No PING/SETTINGS advances DATA fence. No header/payload logging or retained USB bytes.

// ---- RX pump credit invariant, independent of WAL/UI -----------------------
// Driver owns RecvStream and a 65535B duplex writer; LinkIo owns only its reader
// plus quiet SendStream. Receive window65535 means total outstanding payload<=65535.
// Pump polls data promptly (returns tiny-frame metadata budget), writes/coalesces
// bytes into pipe; credit is NOT released on poll_data or pipe write.
// LinkIo successful poll_read adds exactly filled_delta to AtomicConsumed(total),
// then Notify. Driver keeps credited_total and releases only checked difference.
// received_total is published before exposing bytes to pipe (avoid cross-thread
// consumed>delivered false race); consumed<=received and no credit duplication.
// EOS closes pipe writer after buffered payload; failure makes IO sticky-error.
// Notify is a wakeup, counters authoritative; register/check/wait avoids lost wakes.
// Physical allocations include in-copy Bytes at most1frame, TLS/codec/metadata;
// logical65535 bound is not exact RSS. No app/observer await pauses Connection/probe.

// ---- caller contracts (I/H) -----------------------------------------------
// I owns live registry/strict disk2/new SessionHealth; old Session/Metrics/IPC1 unchanged.
// Raw relay task itself selects monitor.ended/stop vs copying, so kernelR write
// blockage cannot suppress link failure. Parent retains/joins relay+LinkOwner,
// then actual helper/lease cleanup; link ended/RTT is never cleanup proof.
// Pending helper mutation/claim remains owned until unique real result.
// Foreground drain and managed first-EOF remain explicit; zeroFIN fence supports both.
// H valve binds actual tuple+session/attempt+connection identity. Event name is
// PROBE_ENQUEUED, not WIRE_SENT; PONG payload not decrypted from PCAP. No wire nonce claim.

// Root freeze amendment: snapshot loads consumed before received; checked pending
// includes in-pump frame+pipe, stats None for RawV1. Poison/driver panic propagate
// Result errors. Normal EOS/ConnectionOk drains cached data; ended=true is fatal,
// timeout or stop, and cannot discard clean residual bytes prematurely.

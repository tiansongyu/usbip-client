//! Bounded mutual TLS and certificate-to-device authorization primitives.
//!
//! This crate neither opens listeners nor accesses devices. Its optional Linux
//! file-loading API validates administrator-controlled configuration and DER
//! files; the memory-DER API remains available independently. Callers must apply
//! the authenticated peer's device predicate to both DEVLIST and IMPORT before
//! acquiring a device. TLS alone does not implement that USB/IP integration.
//!
//! Both stream types follow `AsyncWrite` buffering semantics: callers must flush
//! each completed protocol response and bound their application reads/writes.

mod acl;
mod admission;
mod close;
pub mod config;
mod flush;
mod link;
mod quiet;
mod revocation;
mod security;
mod tls;

pub use acl::{
    AccessPolicy, CertificatePin, DeviceKey, PrincipalRule, VerifiedPeer, VirtualProfile,
};
pub use admission::{AdmissionMonitor, AdmissionPolicy, AdmissionSnapshot};
pub use config::{LoadedClientConfig, LoadedServerConfig, load_client, load_server};
pub use link::{Link, LinkIo, LinkMonitor, LinkOutcome, LinkOwner, ProbePolicy};
pub use security::{
    FramingCounters, SecurityMonitor, SecurityScope, SecuritySnapshot, TlsCounters,
};
pub use tls::{
    AuthenticatedLinkIo, AuthenticatedStream, ClientLink, ClientLinkIo, ClientStream, DerIdentity,
    ServerLink, TlsClient, TlsServer, TrustRoots,
};

/// Legacy raw USB/IP protocol; retained as the default for existing constructors.
pub const ALPN: &[u8] = ALPN_V1;
/// Raw USB/IP protocol without same-connection heartbeat measurements.
pub const ALPN_V1: &[u8] = b"rustusb-usbip/1";
/// Single-stream framed USB/IP with independent same-TLS-connection probes.
pub const ALPN_V2: &[u8] = b"rustusb-usbip/2";
/// Maximum simultaneous handshaking or authenticated streams per server instance.
pub const MAX_CONNECTIONS: usize = 16;
/// Deadline for completing either side of a TLS handshake.
pub const HANDSHAKE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(5);
/// Rustls plaintext/write-buffer limit, not a bound on total process memory.
pub const TLS_BUFFER_LIMIT: usize = 64 * 1024;

/// Redacted transport/configuration failures; certificate and key bytes are never included.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum Error {
    /// An identifier, policy, or input length failed a fixed validation rule.
    #[error("invalid transport input: {0}")]
    InvalidInput(&'static str),
    /// A trusted configuration or credential file operation failed. Only a
    /// fixed field label and error kind are retained, never its path or bytes.
    #[error("secure configuration I/O failure in {field}: {kind:?}")]
    ConfigurationIo {
        /// Fixed configuration or credential field being read.
        field: &'static str,
        /// Operating-system error category without private path information.
        kind: std::io::ErrorKind,
    },
    /// Trusted credential-file loading is currently implemented only on Linux.
    #[error("secure credential-file loading requires Linux")]
    UnsupportedConfigurationPlatform,
    /// A certificate, private key, or key/certificate pair could not be loaded.
    #[error("invalid TLS credentials")]
    InvalidCredentials,
    /// Standard TLS authentication or transport negotiation failed.
    #[error("TLS handshake failed")]
    HandshakeFailed,
    /// The handshake did not finish within its deadline.
    #[error("TLS handshake timed out")]
    Timeout,
    /// The server already owns its bounded number of connections.
    #[error("TLS connection limit reached")]
    ConnectionLimit,
    /// New authentication work exceeded the loaded configuration's temporal quota.
    #[error("TLS authentication admission rate limit reached")]
    AdmissionRateLimited,
    /// Admission metadata cannot be trusted; new authentication fails closed.
    #[error("TLS authentication admission unavailable")]
    AdmissionUnavailable,
    /// A PKI-authenticated certificate is absent from the configured mapping.
    #[error("TLS certificate has no configured principal")]
    UnmappedCertificate,
    /// The authenticated connection did not negotiate the required TLS/ALPN protocol.
    #[error("TLS application protocol mismatch")]
    ProtocolMismatch,
    /// A framed connection could not complete its bounded setup.
    #[error("framed data-link setup timed out")]
    LinkSetupTimeout,
    /// An owned driver failed or its metadata could not be read consistently.
    #[error("framed data-link driver failed")]
    LinkDriverFailed,
}

/// Result for credential loading, policy validation, and TLS connection setup.
pub type Result<T> = std::result::Result<T, Error>;

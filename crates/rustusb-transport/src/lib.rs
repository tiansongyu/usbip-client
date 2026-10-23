//! Bounded mutual TLS and certificate-to-device authorization primitives.
//!
//! This crate neither opens listeners nor accesses devices or credential files.
//! Callers must load administrator-controlled credentials and policy, then apply
//! the authenticated peer's device predicate to both DEVLIST and IMPORT before
//! acquiring a device. TLS alone does not implement that USB/IP integration.
//!
//! Both stream types follow `AsyncWrite` buffering semantics: callers must flush
//! each completed protocol response and bound their application reads/writes.

mod acl;
mod tls;

pub use acl::{
    AccessPolicy, CertificatePin, DeviceKey, PrincipalRule, VerifiedPeer, VirtualProfile,
};
pub use tls::{AuthenticatedStream, ClientStream, DerIdentity, TlsClient, TlsServer, TrustRoots};

/// The only application protocol negotiated by these TLS endpoints.
pub const ALPN: &[u8] = b"rustusb-usbip/1";
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
    /// A PKI-authenticated certificate is absent from the configured mapping.
    #[error("TLS certificate has no configured principal")]
    UnmappedCertificate,
    /// The authenticated connection did not negotiate the required TLS/ALPN protocol.
    #[error("TLS application protocol mismatch")]
    ProtocolMismatch,
}

/// Result for credential loading, policy validation, and TLS connection setup.
pub type Result<T> = std::result::Result<T, Error>;

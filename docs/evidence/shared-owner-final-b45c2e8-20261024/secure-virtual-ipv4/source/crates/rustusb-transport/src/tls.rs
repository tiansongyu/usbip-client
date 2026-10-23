use crate::{
    ALPN, AccessPolicy, CertificatePin, Error, HANDSHAKE_TIMEOUT, MAX_CONNECTIONS, Result,
    TLS_BUFFER_LIMIT, VerifiedPeer, acl::MAX_CERT_BYTES,
};
use rustls::{
    ClientConfig, RootCertStore, ServerConfig,
    pki_types::{CertificateDer, PrivateKeyDer, ServerName},
};
use std::{
    collections::BTreeSet,
    fmt, io,
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
    time::Duration,
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    sync::{OwnedSemaphorePermit, Semaphore},
};
use tokio_rustls::{TlsAcceptor, TlsConnector};

const MAX_CHAIN: usize = 4;
const MAX_ROOTS: usize = 32;
const MAX_DER_TOTAL: usize = 64 * 1024;
const MAX_KEY_BYTES: usize = 16 * 1024;

fn certificates(bytes: Vec<Vec<u8>>, max_count: usize) -> Result<Vec<CertificateDer<'static>>> {
    if bytes.is_empty() || bytes.len() > max_count {
        return Err(Error::InvalidInput("certificate count"));
    }
    if bytes
        .iter()
        .any(|cert| cert.is_empty() || cert.len() > MAX_CERT_BYTES)
        || bytes.iter().map(Vec::len).sum::<usize>() > MAX_DER_TOTAL
    {
        return Err(Error::InvalidInput("certificate size"));
    }
    let mut seen = BTreeSet::new();
    let mut parsed = Vec::with_capacity(bytes.len());
    for bytes in bytes {
        if !seen.insert(CertificatePin::from_der(&bytes)?) {
            return Err(Error::InvalidInput("duplicate certificate"));
        }
        let cert = CertificateDer::from(bytes);
        // Parse every supplied certificate, including intermediates, without
        // pretending this structural check validates its chain or validity time.
        RootCertStore::empty()
            .add(cert.clone())
            .map_err(|_| Error::InvalidCredentials)?;
        parsed.push(cert);
    }
    Ok(parsed)
}

/// An owned DER chain and matching private key, with redacted Debug output.
/// Reading files and checking their ownership/permissions belongs to the caller.
pub struct DerIdentity {
    chain: Vec<CertificateDer<'static>>,
    key: PrivateKeyDer<'static>,
}

impl DerIdentity {
    /// Load 1..4 certificates (16 KiB each, 64 KiB total) and a DER private key
    /// of at most 16 KiB. Reject malformed certificates, keys, and mismatched pairs.
    /// This does not validate the identity's chain, expiry, or intended peer trust.
    pub fn new(chain: Vec<Vec<u8>>, key: Vec<u8>) -> Result<Self> {
        let chain = certificates(chain, MAX_CHAIN)?;
        if key.is_empty() || key.len() > MAX_KEY_BYTES {
            return Err(Error::InvalidInput("private key size"));
        }
        let key = PrivateKeyDer::try_from(key).map_err(|_| Error::InvalidCredentials)?;
        let certified = rustls::sign::CertifiedKey::from_der(
            chain.clone(),
            key.clone_key(),
            &rustls::crypto::ring::default_provider(),
        )
        .map_err(|_| Error::InvalidCredentials)?;
        certified
            .keys_match()
            .map_err(|_| Error::InvalidCredentials)?;
        Ok(Self { chain, key })
    }
}

impl fmt::Debug for DerIdentity {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("DerIdentity([redacted])")
    }
}

/// A bounded set of explicitly supplied trust anchors; no operating-system roots are added.
pub struct TrustRoots(RootCertStore);

impl TrustRoots {
    /// Load 1..32 distinct DER certificates, each at most 16 KiB and at most 64 KiB total.
    /// The caller must establish that these are the administrator's intended trust anchors.
    pub fn new(roots: Vec<Vec<u8>>) -> Result<Self> {
        let mut store = RootCertStore::empty();
        for cert in certificates(roots, MAX_ROOTS)? {
            store.add(cert).map_err(|_| Error::InvalidCredentials)?;
        }
        Ok(Self(store))
    }
}

impl fmt::Debug for TrustRoots {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("TrustRoots([redacted])")
    }
}

/// Immutable mutual-TLS server configuration and its shared connection budget.
/// Clones share the same 16 permits; each returned stream retains one until Drop.
#[derive(Clone)]
pub struct TlsServer {
    acceptor: TlsAcceptor,
    policy: Arc<AccessPolicy>,
    limit: Arc<Semaphore>,
}

impl TlsServer {
    /// Configure TLS 1.3, mandatory client certificates, fixed ALPN, and no tickets/0-RTT.
    /// Only PKI-verified leaf certificates present in `policy` can produce a stream.
    pub fn new(roots: TrustRoots, identity: DerIdentity, policy: AccessPolicy) -> Result<Self> {
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let verifier = rustls::server::WebPkiClientVerifier::builder_with_provider(
            Arc::new(roots.0),
            provider.clone(),
        )
        .build()
        .map_err(|_| Error::InvalidCredentials)?;
        let mut config = ServerConfig::builder_with_provider(provider)
            .with_protocol_versions(&[&rustls::version::TLS13])
            .map_err(|_| Error::InvalidCredentials)?
            .with_client_cert_verifier(verifier)
            .with_single_cert(identity.chain, identity.key)
            .map_err(|_| Error::InvalidCredentials)?;
        config.alpn_protocols = vec![ALPN.to_vec()];
        config.session_storage = Arc::new(rustls::server::NoServerSessionStorage {});
        config.send_tls13_tickets = 0;
        config.max_early_data_size = 0;
        config.send_half_rtt_data = false;
        config.cert_compressors.clear();
        config.cert_decompressors.clear();
        Ok(Self {
            acceptor: TlsAcceptor::from(Arc::new(config)),
            policy: Arc::new(policy),
            limit: Arc::new(Semaphore::new(MAX_CONNECTIONS)),
        })
    }

    /// Authenticate an already accepted duplex stream within five seconds.
    /// Acquire a permit before TLS work; overload is rejected without waiting.
    /// Cancellation or any failure drops the input and returns its permit.
    /// The caller owns spawning and joining this future, and must bound accepts/tasks.
    pub async fn accept<S>(&self, stream: S) -> Result<AuthenticatedStream<S>>
    where
        S: AsyncRead + AsyncWrite + Unpin,
    {
        self.accept_with_deadline(stream, HANDSHAKE_TIMEOUT).await
    }

    async fn accept_with_deadline<S>(
        &self,
        stream: S,
        deadline: Duration,
    ) -> Result<AuthenticatedStream<S>>
    where
        S: AsyncRead + AsyncWrite + Unpin,
    {
        let permit = self
            .limit
            .clone()
            .try_acquire_owned()
            .map_err(|_| Error::ConnectionLimit)?;
        let handshake = self.acceptor.accept_with(stream, |connection| {
            connection.set_buffer_limit(Some(TLS_BUFFER_LIMIT));
        });
        let inner = tokio::time::timeout(deadline, handshake)
            .await
            .map_err(|_| Error::Timeout)?
            .map_err(|_| Error::HandshakeFailed)?;
        let connection = inner.get_ref().1;
        check_protocol(connection.protocol_version(), connection.alpn_protocol())?;
        let cert = connection
            .peer_certificates()
            .and_then(|chain| chain.first())
            .ok_or(Error::HandshakeFailed)?;
        let peer = self
            .policy
            .verified_peer(CertificatePin::from_der(cert.as_ref())?)?;
        Ok(AuthenticatedStream {
            inner,
            peer,
            _permit: permit,
        })
    }
}

impl fmt::Debug for TlsServer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("TlsServer")
            .field("available_permits", &self.limit.available_permits())
            .finish()
    }
}

/// Client credentials and explicit server trust with resumption and early data disabled.
#[derive(Clone)]
pub struct TlsClient {
    connector: TlsConnector,
}

impl TlsClient {
    /// Configure standard chain/time/usage/server-name verification and mandatory local identity.
    /// This never installs an insecure verifier or adopts system trust roots.
    pub fn new(roots: TrustRoots, identity: DerIdentity) -> Result<Self> {
        let mut config =
            ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
                .with_protocol_versions(&[&rustls::version::TLS13])
                .map_err(|_| Error::InvalidCredentials)?
                .with_root_certificates(roots.0)
                .with_client_auth_cert(identity.chain, identity.key)
                .map_err(|_| Error::InvalidCredentials)?;
        config.alpn_protocols = vec![ALPN.to_vec()];
        config.resumption = rustls::client::Resumption::disabled();
        config.enable_early_data = false;
        config.cert_compressors.clear();
        config.cert_decompressors.clear();
        Ok(Self {
            connector: TlsConnector::from(Arc::new(config)),
        })
    }

    /// Complete TLS within five seconds, verifying the configured DNS name or IP SAN.
    /// Success authenticates the server, not the server application's acceptance of
    /// this client's principal or device request; await the actual management response.
    pub async fn connect<S>(&self, server_name: &str, stream: S) -> Result<ClientStream<S>>
    where
        S: AsyncRead + AsyncWrite + Unpin,
    {
        if server_name.is_empty() || server_name.len() > 253 {
            return Err(Error::InvalidInput("server name"));
        }
        let name = ServerName::try_from(server_name.to_owned())
            .map_err(|_| Error::InvalidInput("server name"))?;
        let handshake = self.connector.connect_with(name, stream, |connection| {
            connection.set_buffer_limit(Some(TLS_BUFFER_LIMIT));
        });
        let inner = tokio::time::timeout(HANDSHAKE_TIMEOUT, handshake)
            .await
            .map_err(|_| Error::Timeout)?
            .map_err(|_| Error::HandshakeFailed)?;
        let connection = inner.get_ref().1;
        check_protocol(connection.protocol_version(), connection.alpn_protocol())?;
        let cert = connection
            .peer_certificates()
            .and_then(|chain| chain.first())
            .ok_or(Error::HandshakeFailed)?;
        let server_pin = CertificatePin::from_der(cert.as_ref())?;
        Ok(ClientStream { inner, server_pin })
    }
}

impl fmt::Debug for TlsClient {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("TlsClient([redacted])")
    }
}

fn check_protocol(version: Option<rustls::ProtocolVersion>, alpn: Option<&[u8]>) -> Result<()> {
    if version != Some(rustls::ProtocolVersion::TLSv1_3) || alpn != Some(ALPN) {
        return Err(Error::ProtocolMismatch);
    }
    Ok(())
}

/// Authenticated duplex TLS stream retaining its server connection permit until Drop.
/// No raw stream access is exposed: extracting an inner transport cannot bypass the budget.
/// Flush responses explicitly; application operation deadlines are the caller's responsibility.
pub struct AuthenticatedStream<S> {
    inner: tokio_rustls::server::TlsStream<S>,
    peer: VerifiedPeer,
    _permit: OwnedSemaphorePermit,
}

impl<S> AuthenticatedStream<S> {
    /// Borrow the PKI-authenticated, policy-mapped peer and its device allowlist.
    pub fn peer(&self) -> &VerifiedPeer {
        &self.peer
    }
}

impl<S> fmt::Debug for AuthenticatedStream<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AuthenticatedStream")
            .field("peer", &self.peer)
            .finish_non_exhaustive()
    }
}

/// TLS client stream whose server certificate and configured name have been verified.
/// This does not certify server-side device authorization; read the management response.
pub struct ClientStream<S> {
    inner: tokio_rustls::client::TlsStream<S>,
    server_pin: CertificatePin,
}

impl<S> ClientStream<S> {
    /// Return the verified server leaf digest for audit correlation, not device authorization.
    pub fn server_certificate_pin(&self) -> CertificatePin {
        self.server_pin
    }
}

impl<S> fmt::Debug for ClientStream<S> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("ClientStream([redacted])")
    }
}

macro_rules! stream_io {
    ($name:ident) => {
        impl<S: AsyncRead + AsyncWrite + Unpin> AsyncRead for $name<S> {
            fn poll_read(
                self: Pin<&mut Self>,
                cx: &mut Context<'_>,
                buf: &mut ReadBuf<'_>,
            ) -> Poll<io::Result<()>> {
                Pin::new(&mut self.get_mut().inner).poll_read(cx, buf)
            }
        }
        impl<S: AsyncRead + AsyncWrite + Unpin> AsyncWrite for $name<S> {
            fn poll_write(
                self: Pin<&mut Self>,
                cx: &mut Context<'_>,
                bytes: &[u8],
            ) -> Poll<io::Result<usize>> {
                Pin::new(&mut self.get_mut().inner).poll_write(cx, bytes)
            }
            fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
                Pin::new(&mut self.get_mut().inner).poll_flush(cx)
            }
            fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
                Pin::new(&mut self.get_mut().inner).poll_shutdown(cx)
            }
        }
    };
}
stream_io!(AuthenticatedStream);
stream_io!(ClientStream);

#[cfg(test)]
mod tests;

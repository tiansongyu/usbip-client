//! Startup-only trusted JSON/DER loading for dedicated Linux broker processes.
//!
//! Loading is synchronous and may block on filesystem or cryptographic work;
//! asynchronous callers must use an owned blocking task. A loaded configuration
//! does not confer a helper role or open a listener. The helper must separately
//! verify the process's OS identity and its administrator-managed device pool.

use crate::{Result, TlsClient, TlsServer};
use rustusb_core::{DeviceKey, Peer};
use std::{fmt, net::SocketAddr, path::Path};

/// Immutable server startup inputs loaded for the current dedicated broker UID.
/// The CLI must reject devices outside its selected backend before device access.
pub struct LoadedServerConfig {
    /// Exact nonzero service UID specified by the administrator.
    pub broker_uid: u32,
    /// Explicit literal listen address. Loading does not bind this address.
    pub listen: SocketAddr,
    /// Finite device pool; each principal's allowlist is a subset of this pool.
    pub devices: Vec<DeviceKey>,
    /// PKI/ALPN configuration and immutable certificate-to-device policy.
    pub tls: TlsServer,
}

impl fmt::Debug for LoadedServerConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LoadedServerConfig")
            .field("broker_uid", &self.broker_uid)
            .field("listen", &self.listen)
            .field("device_count", &self.devices.len())
            .finish_non_exhaustive()
    }
}

/// Immutable client startup inputs, distinct from a transient USB bus address.
/// Discovery may show the authenticated catalog; attach must select only `device`.
pub struct LoadedClientConfig {
    /// Exact nonzero service UID specified by the administrator.
    pub broker_uid: u32,
    /// Administrator's bounded server alias, also checked by helper import policy.
    pub server_id: String,
    /// Fixed connection endpoint, supporting a validated DNS name, IPv4 or IPv6.
    pub endpoint: Peer,
    /// Fixed DNS/IP name used for standard server-certificate verification.
    pub server_name: String,
    /// Exact stable remote device key permitted for this client's attach operation.
    pub device: DeviceKey,
    /// Mutual-TLS credentials and explicit server trust anchors.
    pub tls: TlsClient,
}

impl fmt::Debug for LoadedClientConfig {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("LoadedClientConfig")
            .field("broker_uid", &self.broker_uid)
            .field("server_id", &self.server_id)
            .finish_non_exhaustive()
    }
}

/// Load strict schema-1 (raw) or schema-2 (framed) server JSON and bounded DER files for the actual nonroot UID.
///
/// Config/CA/chain files and every parent directory must be root-owned and not
/// group/other writable. Keys require root or broker ownership and mode 0600.
/// Symlinks, hardlinks, nonregular files, oversized or changing files fail closed.
/// Multiple files are not an atomic reload; use cooperative stop/restart to rotate.
/// This synchronous function must run off an asynchronous executor's worker.
pub fn load_server(path: &Path) -> Result<LoadedServerConfig> {
    #[cfg(target_os = "linux")]
    {
        platform::load_server(path)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = path;
        Err(crate::Error::UnsupportedConfigurationPlatform)
    }
}

/// Load strict schema-1 (raw) or schema-2 (framed) client JSON and bounded DER files for the actual nonroot UID.
///
/// This enforces the same trusted-file rules as [`load_server`]. It neither
/// connects to the endpoint nor grants the caller a privileged helper role.
/// The endpoint, PKI server name and device key cannot be replaced by CLI input.
/// This synchronous function must run off an asynchronous executor's worker.
pub fn load_client(path: &Path) -> Result<LoadedClientConfig> {
    #[cfg(target_os = "linux")]
    {
        platform::load_client(path)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = path;
        Err(crate::Error::UnsupportedConfigurationPlatform)
    }
}

/// Read a bounded root-owned public JSON document under protected ancestors.
///
/// This reads no private credentials and does not require the caller to be the
/// broker UID. It reuses the trusted regular-file, hardlink, symlink and changing
/// file checks used for TLS configuration. Callers must validate their own schema.
/// Run this synchronous filesystem operation off an asynchronous worker.
pub fn read_root_document(path: &Path) -> Result<Vec<u8>> {
    #[cfg(target_os = "linux")]
    {
        platform::read_root_document(path)
    }
    #[cfg(not(target_os = "linux"))]
    {
        let _ = path;
        Err(crate::Error::UnsupportedConfigurationPlatform)
    }
}

#[cfg(target_os = "linux")]
mod platform {
    use super::*;
    use crate::{
        AccessPolicy, CertificatePin, DerIdentity, Error, PrincipalRule, ProbePolicy, TrustRoots,
    };
    use serde::Deserialize;
    use std::{
        collections::BTreeSet,
        fs::{self, File, Metadata},
        io::{self, Read},
        os::unix::fs::MetadataExt,
        path::{Component, PathBuf},
    };

    const MAX_CONFIG: usize = 64 * 1024;
    const MAX_DER: usize = 16 * 1024;
    const MAX_DER_TOTAL: usize = 64 * 1024;
    const MAX_PATH: usize = 4096;
    const MAX_COMPONENTS: usize = 64;

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ServerFile {
        schema_version: u32,
        broker_uid: u32,
        listen: SocketAddr,
        devices: Vec<DeviceKey>,
        principals: Vec<PrincipalFile>,
        ca_certificates: Vec<PathBuf>,
        certificate_chain: Vec<PathBuf>,
        private_key: PathBuf,
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ClientFile {
        schema_version: u32,
        broker_uid: u32,
        server_id: String,
        endpoint: Peer,
        server_name: String,
        device: DeviceKey,
        ca_certificates: Vec<PathBuf>,
        certificate_chain: Vec<PathBuf>,
        private_key: PathBuf,
    }

    // Decode the version first without converting through Value: each complete
    // document is then decoded by its own strict shape, including duplicate fields.
    #[derive(Deserialize)]
    struct FileVersion {
        schema_version: u32,
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct DataLinkFile {
        protocol: String,
        interval_millis: u64,
        response_timeout_millis: u64,
    }

    impl DataLinkFile {
        fn policy(self) -> Result<ProbePolicy> {
            if self.protocol != "framed_v2" {
                return Err(Error::InvalidInput("data-link protocol"));
            }
            ProbePolicy::new(
                std::time::Duration::from_millis(self.interval_millis),
                std::time::Duration::from_millis(self.response_timeout_millis),
            )
        }
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ServerFileV2 {
        schema_version: u32,
        broker_uid: u32,
        listen: SocketAddr,
        devices: Vec<DeviceKey>,
        principals: Vec<PrincipalFile>,
        ca_certificates: Vec<PathBuf>,
        certificate_chain: Vec<PathBuf>,
        private_key: PathBuf,
        data_link: DataLinkFile,
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct ClientFileV2 {
        schema_version: u32,
        broker_uid: u32,
        server_id: String,
        endpoint: Peer,
        server_name: String,
        device: DeviceKey,
        ca_certificates: Vec<PathBuf>,
        certificate_chain: Vec<PathBuf>,
        private_key: PathBuf,
        data_link: DataLinkFile,
    }

    fn version(bytes: &[u8]) -> Result<u32> {
        serde_json::from_slice::<FileVersion>(bytes)
            .map(|v| v.schema_version)
            .map_err(|_| Error::InvalidInput("configuration schema version"))
    }

    fn server_document(bytes: &[u8]) -> Result<(ServerFile, Option<ProbePolicy>)> {
        match version(bytes).map_err(|_| Error::InvalidInput("server configuration schema"))? {
            1 => serde_json::from_slice(bytes)
                .map(|file| (file, None))
                .map_err(|_| Error::InvalidInput("server configuration schema")),
            2 => {
                let file: ServerFileV2 = serde_json::from_slice(bytes)
                    .map_err(|_| Error::InvalidInput("server configuration schema"))?;
                let policy = file.data_link.policy()?;
                Ok((
                    ServerFile {
                        schema_version: file.schema_version,
                        broker_uid: file.broker_uid,
                        listen: file.listen,
                        devices: file.devices,
                        principals: file.principals,
                        ca_certificates: file.ca_certificates,
                        certificate_chain: file.certificate_chain,
                        private_key: file.private_key,
                    },
                    Some(policy),
                ))
            }
            _ => Err(Error::InvalidInput("configuration schema version")),
        }
    }

    fn client_document(bytes: &[u8]) -> Result<(ClientFile, Option<ProbePolicy>)> {
        match version(bytes).map_err(|_| Error::InvalidInput("client configuration schema"))? {
            1 => serde_json::from_slice(bytes)
                .map(|file| (file, None))
                .map_err(|_| Error::InvalidInput("client configuration schema")),
            2 => {
                let file: ClientFileV2 = serde_json::from_slice(bytes)
                    .map_err(|_| Error::InvalidInput("client configuration schema"))?;
                let policy = file.data_link.policy()?;
                Ok((
                    ClientFile {
                        schema_version: file.schema_version,
                        broker_uid: file.broker_uid,
                        server_id: file.server_id,
                        endpoint: file.endpoint,
                        server_name: file.server_name,
                        device: file.device,
                        ca_certificates: file.ca_certificates,
                        certificate_chain: file.certificate_chain,
                        private_key: file.private_key,
                    },
                    Some(policy),
                ))
            }
            _ => Err(Error::InvalidInput("configuration schema version")),
        }
    }

    #[derive(Deserialize)]
    #[serde(deny_unknown_fields)]
    struct PrincipalFile {
        id: String,
        pins: Vec<String>,
        devices: Vec<DeviceKey>,
    }

    struct Context {
        uid: u32,
        administrator_uid: u32,
        // Test fixtures are genuinely owned by the test UID. This boundary
        // substitutes a fixture trust root, never production /tmp ownership.
        #[cfg(test)]
        fixture_root: Option<PathBuf>,
    }

    impl Context {
        fn system() -> Self {
            Self {
                uid: rustix::process::geteuid().as_raw(),
                administrator_uid: 0,
                #[cfg(test)]
                fixture_root: None,
            }
        }

        fn broker(&self, uid: u32) -> Result<()> {
            if uid == 0 || uid != self.uid {
                return Err(Error::InvalidInput("dedicated broker UID"));
            }
            Ok(())
        }
    }

    #[derive(Clone, Copy)]
    enum Kind {
        Public,
        Secret(u32),
    }

    fn io_error(field: &'static str, error: io::Error) -> Error {
        Error::ConfigurationIo {
            field,
            kind: error.kind(),
        }
    }

    fn valid_path(path: &Path) -> Result<()> {
        let bytes = path.as_os_str().as_encoded_bytes();
        if !path.is_absolute()
            || bytes.len() > MAX_PATH
            || bytes.iter().any(|b| b.is_ascii_control())
            || path.components().count() > MAX_COMPONENTS
            || path
                .components()
                .any(|c| !matches!(c, Component::RootDir | Component::Normal(_)))
        {
            return Err(Error::InvalidInput("absolute protected file path"));
        }
        Ok(())
    }

    fn protected_directory(path: &Path, context: &Context) -> Result<()> {
        valid_path(path)?;
        #[cfg(test)]
        if let Some(root) = &context.fixture_root {
            // Only private test entry points can replace the root-owner check.
            let suffix = path
                .strip_prefix(root)
                .map_err(|_| Error::InvalidInput("fixture path escaped trust root"))?;
            directory_metadata(root, context)?;
            let mut cursor = root.clone();
            for component in suffix.components() {
                cursor.push(component);
                directory_metadata(&cursor, context)?;
            }
            return Ok(());
        }
        let mut cursor = PathBuf::new();
        for component in path.components() {
            cursor.push(component);
            directory_metadata(&cursor, context)?;
        }
        Ok(())
    }

    fn directory_metadata(path: &Path, context: &Context) -> Result<()> {
        let metadata = fs::symlink_metadata(path)
            .map_err(|error| io_error("protected parent directory", error))?;
        if !metadata.is_dir()
            || metadata.uid() != context.administrator_uid
            || metadata.mode() & 0o022 != 0
        {
            return Err(Error::InvalidInput("protected parent directory"));
        }
        Ok(())
    }

    fn validate_metadata(
        metadata: &Metadata,
        kind: Kind,
        max: usize,
        context: &Context,
    ) -> Result<()> {
        if !metadata.is_file() || metadata.nlink() != 1 || metadata.len() > max as u64 {
            return Err(Error::InvalidInput("bounded regular single-link file"));
        }
        let valid = match kind {
            Kind::Public => {
                metadata.uid() == context.administrator_uid && metadata.mode() & 0o022 == 0
            }
            Kind::Secret(broker) => {
                (metadata.uid() == context.administrator_uid || metadata.uid() == broker)
                    && metadata.mode() & 0o7777 == 0o600
            }
        };
        if !valid {
            return Err(Error::InvalidInput("trusted file ownership or permissions"));
        }
        Ok(())
    }

    fn same_file(before: &Metadata, after: &Metadata) -> bool {
        before.dev() == after.dev()
            && before.ino() == after.ino()
            && before.mode() == after.mode()
            && before.uid() == after.uid()
            && before.gid() == after.gid()
            && before.nlink() == after.nlink()
            && before.len() == after.len()
            && before.mtime() == after.mtime()
            && before.mtime_nsec() == after.mtime_nsec()
            && before.ctime() == after.ctime()
            && before.ctime_nsec() == after.ctime_nsec()
    }

    fn read_file(
        path: &Path,
        kind: Kind,
        max: usize,
        field: &'static str,
        context: &Context,
    ) -> Result<Vec<u8>> {
        valid_path(path)?;
        let parent = path
            .parent()
            .ok_or(Error::InvalidInput("protected file parent"))?;
        protected_directory(parent, context)?;
        let before = fs::symlink_metadata(path).map_err(|error| io_error(field, error))?;
        validate_metadata(&before, kind, max, context)?;
        let fd = rustix::fs::open(
            path,
            rustix::fs::OFlags::RDONLY
                | rustix::fs::OFlags::NOFOLLOW
                | rustix::fs::OFlags::CLOEXEC
                | rustix::fs::OFlags::NONBLOCK,
            rustix::fs::Mode::empty(),
        )
        .map_err(|error| io_error(field, error.into()))?;
        let file = File::from(fd);
        let opened = file.metadata().map_err(|error| io_error(field, error))?;
        validate_metadata(&opened, kind, max, context)?;
        if !same_file(&before, &opened) {
            return Err(Error::InvalidInput("file changed during open"));
        }
        let mut bytes = Vec::with_capacity(opened.len() as usize);
        (&file)
            .take((max + 1) as u64)
            .read_to_end(&mut bytes)
            .map_err(|error| io_error(field, error))?;
        protected_directory(parent, context)?;
        let after = file.metadata().map_err(|error| io_error(field, error))?;
        let current = fs::symlink_metadata(path).map_err(|error| io_error(field, error))?;
        if bytes.len() > max
            || bytes.len() as u64 != opened.len()
            || !same_file(&opened, &after)
            || !same_file(&opened, &current)
        {
            return Err(Error::InvalidInput("file changed during bounded read"));
        }
        Ok(bytes)
    }

    fn certificates(
        paths: &[PathBuf],
        max_count: usize,
        field: &'static str,
        context: &Context,
    ) -> Result<Vec<Vec<u8>>> {
        if paths.is_empty() || paths.len() > max_count {
            return Err(Error::InvalidInput("certificate path count"));
        }
        let mut total = 0;
        let mut bytes = Vec::with_capacity(paths.len());
        for path in paths {
            let certificate = read_file(path, Kind::Public, MAX_DER, field, context)?;
            total += certificate.len();
            if total > MAX_DER_TOTAL {
                return Err(Error::InvalidInput("certificate total size"));
            }
            bytes.push(certificate);
        }
        Ok(bytes)
    }

    fn credentials(
        roots: &[PathBuf],
        chain: &[PathBuf],
        key: &Path,
        broker_uid: u32,
        context: &Context,
    ) -> Result<(TrustRoots, DerIdentity)> {
        let roots = TrustRoots::new(certificates(roots, 32, "CA certificate", context)?)?;
        let chain = certificates(chain, 4, "certificate chain", context)?;
        let key = read_file(
            key,
            Kind::Secret(broker_uid),
            MAX_DER,
            "private key",
            context,
        )?;
        Ok((roots, DerIdentity::new(chain, key)?))
    }

    pub(super) fn read_root_document(path: &Path) -> Result<Vec<u8>> {
        read_file(
            path,
            Kind::Public,
            MAX_CONFIG,
            "public document",
            &Context::system(),
        )
    }

    fn load_server_inner(path: &Path, context: &Context) -> Result<LoadedServerConfig> {
        let bytes = read_file(
            path,
            Kind::Public,
            MAX_CONFIG,
            "server configuration",
            context,
        )?;
        let (file, link_policy) = server_document(&bytes)?;
        if file.schema_version != if link_policy.is_some() { 2 } else { 1 } {
            return Err(Error::InvalidInput("configuration schema version"));
        }
        context.broker(file.broker_uid)?;
        let pool: BTreeSet<_> = file.devices.iter().collect();
        if file.devices.is_empty() || file.devices.len() > 16 || pool.len() != file.devices.len() {
            return Err(Error::InvalidInput("server device pool"));
        }
        if file.principals.len() > 64 {
            return Err(Error::InvalidInput("principal count"));
        }
        let mut rules = Vec::with_capacity(file.principals.len());
        for principal in file.principals {
            if principal
                .devices
                .iter()
                .any(|device| !pool.contains(device))
            {
                return Err(Error::InvalidInput("principal device outside server pool"));
            }
            if principal.pins.len() > 2 {
                return Err(Error::InvalidInput("principal certificate list"));
            }
            let pins = principal
                .pins
                .iter()
                .map(|pin| CertificatePin::from_hex(pin))
                .collect::<Result<Vec<_>>>()?;
            rules.push(PrincipalRule::new(&principal.id, pins, principal.devices)?);
        }
        let policy = AccessPolicy::new(rules)?;
        let (roots, identity) = credentials(
            &file.ca_certificates,
            &file.certificate_chain,
            &file.private_key,
            file.broker_uid,
            context,
        )?;
        Ok(LoadedServerConfig {
            broker_uid: file.broker_uid,
            listen: file.listen,
            devices: file.devices,
            tls: match link_policy {
                Some(probes) => TlsServer::new_with_link(roots, identity, policy, probes)?,
                None => TlsServer::new(roots, identity, policy)?,
            },
        })
    }

    fn load_client_inner(path: &Path, context: &Context) -> Result<LoadedClientConfig> {
        let bytes = read_file(
            path,
            Kind::Public,
            MAX_CONFIG,
            "client configuration",
            context,
        )?;
        let (file, link_policy) = client_document(&bytes)?;
        if file.schema_version != if link_policy.is_some() { 2 } else { 1 } {
            return Err(Error::InvalidInput("configuration schema version"));
        }
        context.broker(file.broker_uid)?;
        if file.server_id.is_empty()
            || file.server_id.len() > 64
            || !file
                .server_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        {
            return Err(Error::InvalidInput("server identifier"));
        }
        if file.server_name.is_empty()
            || file.server_name.len() > 253
            || rustls::pki_types::ServerName::try_from(file.server_name.as_str()).is_err()
        {
            return Err(Error::InvalidInput("TLS server name"));
        }
        let (roots, identity) = credentials(
            &file.ca_certificates,
            &file.certificate_chain,
            &file.private_key,
            file.broker_uid,
            context,
        )?;
        Ok(LoadedClientConfig {
            broker_uid: file.broker_uid,
            server_id: file.server_id,
            endpoint: file.endpoint,
            server_name: file.server_name,
            device: file.device,
            tls: match link_policy {
                Some(probes) => TlsClient::new_with_link(roots, identity, probes)?,
                None => TlsClient::new(roots, identity)?,
            },
        })
    }

    pub(super) fn load_server(path: &Path) -> Result<LoadedServerConfig> {
        load_server_inner(path, &Context::system())
    }

    pub(super) fn load_client(path: &Path) -> Result<LoadedClientConfig> {
        load_client_inner(path, &Context::system())
    }

    #[cfg(test)]
    mod tests;
}

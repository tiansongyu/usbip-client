//! Explicit choice between existing trusted-network and authenticated listeners.

use rustusb_core::Result;
use rustusb_rust_backend::{SecureServerConfig, ServerConfig, ServerHandle, VirtualBackend};
use rustusb_transport::TlsServer;

pub(super) enum Binding {
    Legacy(ServerConfig),
    Tls {
        config: SecureServerConfig,
        tls: TlsServer,
    },
}

impl Binding {
    pub(super) fn validate(&self) -> Result<()> {
        match self {
            Self::Legacy(config) => config.validate(),
            // TLS settings were loaded from administrator-managed config.
            // They do not borrow the legacy trusted_network declaration.
            Self::Tls { .. } => Ok(()),
        }
    }

    pub(super) fn is_secure(&self) -> bool {
        matches!(self, Self::Tls { .. })
    }

    pub(super) async fn bind_virtual(self, backend: VirtualBackend) -> Result<ServerHandle> {
        match self {
            Self::Legacy(config) => ServerHandle::bind(config, backend).await,
            Self::Tls { config, tls } => ServerHandle::bind_tls(config, backend, tls).await,
        }
    }

    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    pub(super) async fn bind_physical(
        self,
        backend: rustusb_rust_backend::PhysicalBackend,
    ) -> Result<ServerHandle> {
        match self {
            Self::Legacy(config) => ServerHandle::bind_physical(config, backend).await,
            Self::Tls { config, tls } => {
                ServerHandle::bind_physical_tls(config, backend, tls).await
            }
        }
    }
}

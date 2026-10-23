//! Real in-process job owners. No runner reads global stdin or installs signals.
//!
//! Callers retain and join the runner task after requesting cooperative stop.
//! A cancelled UI waiter must never cancel an accepted job's owner task.

#[cfg(target_os = "linux")]
mod client;
#[cfg(target_os = "linux")]
mod io;
#[cfg(target_os = "linux")]
mod native;
#[cfg(feature = "rust-backend")]
mod rust_server;

use crate::context::JobContext;
use crate::{foreground::JobOutcome, run_context::RunContext};
use rustusb_core::{DeviceBackend, Error, Result};
use rustusb_protocol::secure::CatalogDevice;
use rustusb_transport::config::{LoadedClientConfig, LoadedServerConfig};
use std::{sync::Arc, time::Duration};

/// Administrator-loaded immutable inputs; no endpoint or credential is supplied by control RPC.
pub enum LoadedJob {
    /// Authenticated remote device imported into the local Linux VHCI controller.
    Client {
        /// Trusted startup configuration for exactly one remote device key.
        config: Arc<LoadedClientConfig>,
        /// Bounded DNS/TCP/TLS/catalog and remote IMPORT deadline.
        timeout: Duration,
    },
    /// Authenticated Linux usbip-host service using anonymous helper-owned channels.
    NativeServer(Arc<LoadedServerConfig>),
    /// Authenticated Rust virtual HID/CDC service.
    #[cfg(feature = "rust-backend")]
    VirtualServer(Arc<LoadedServerConfig>),
    /// Authenticated explicitly granted USBFS generations.
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    PhysicalServer(Arc<LoadedServerConfig>),
    /// Explicit legacy virtual listener for foreground callers only.
    #[cfg(feature = "rust-backend")]
    VirtualLegacy(rustusb_rust_backend::ServerConfig),
    /// Explicit legacy physical listener for foreground callers only.
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    PhysicalLegacy {
        /// Existing trusted-network/peer validation is still mandatory.
        config: rustusb_rust_backend::ServerConfig,
        /// Exact helper-authorized legacy device fingerprints.
        device_ids: Vec<String>,
    },
}

/// Run the actual resource owner through operation and verified/unconfirmed cleanup.
///
/// This future must be retained and joined after setting `context.stop`; dropping
/// it is not a cleanup receipt. Persistence errors never bypass owned cleanup.
pub async fn run_job(job: LoadedJob, context: JobContext) -> Result<()> {
    run_owned(job, RunContext::managed(context))
        .await
        .into_result()
}

pub(crate) async fn run_owned(job: LoadedJob, context: RunContext) -> JobOutcome {
    match job {
        LoadedJob::Client { config, timeout } => {
            #[cfg(target_os = "linux")]
            {
                client::run(config, timeout, context).await
            }
            #[cfg(not(target_os = "linux"))]
            {
                let _ = (config, timeout, context);
                JobOutcome::before_start(Error::Unsupported(
                    "Native secure import requires Linux".into(),
                ))
            }
        }
        LoadedJob::NativeServer(config) => {
            if let Err(error) = validate_pool(&config, DeviceBackend::Native) {
                return JobOutcome::before_start(error);
            }
            #[cfg(target_os = "linux")]
            {
                native::run(config, context).await
            }
            #[cfg(not(target_os = "linux"))]
            {
                let _ = context;
                JobOutcome::before_start(Error::Unsupported(
                    "Native secure serving requires Linux".into(),
                ))
            }
        }
        #[cfg(feature = "rust-backend")]
        LoadedJob::VirtualServer(config) => {
            if let Err(error) = validate_pool(&config, DeviceBackend::Virtual) {
                return JobOutcome::before_start(error);
            }
            rust_server::virtual_server(config, context).await
        }
        #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
        LoadedJob::PhysicalServer(config) => {
            if let Err(error) = validate_pool(&config, DeviceBackend::Physical) {
                return JobOutcome::before_start(error);
            }
            rust_server::physical_server(config, context).await
        }
        #[cfg(feature = "rust-backend")]
        LoadedJob::VirtualLegacy(config) => {
            if context.is_managed() {
                return JobOutcome::before_start(Error::Permission(
                    "managed jobs require an authenticated fixed profile".into(),
                ));
            }
            rust_server::virtual_legacy(config, context).await
        }
        #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
        LoadedJob::PhysicalLegacy { config, device_ids } => {
            if context.is_managed() {
                return JobOutcome::before_start(Error::Permission(
                    "managed jobs require an authenticated fixed profile".into(),
                ));
            }
            rust_server::physical_legacy(config, device_ids, context).await
        }
    }
}

fn validate_pool(config: &LoadedServerConfig, backend: DeviceBackend) -> Result<()> {
    if config.devices.is_empty()
        || config.devices.len() > 16
        || config.devices.iter().any(|key| key.backend() != backend)
    {
        return Err(Error::InvalidInput(
            "all configured devices must match the selected service backend".into(),
        ));
    }
    Ok(())
}

/// Read the authenticated visible catalog, without importing or acquiring helper capabilities.
pub async fn discover(
    config: &LoadedClientConfig,
    timeout: Duration,
) -> Result<Vec<CatalogDevice>> {
    tokio::time::timeout(timeout, async {
        rustusb_protocol::secure::discover(connect(config).await?).await
    })
    .await
    .map_err(|_| Error::Timeout("secure discovery including DNS/TCP/TLS".into()))?
}

async fn connect(
    config: &LoadedClientConfig,
) -> Result<rustusb_transport::ClientStream<tokio::net::TcpStream>> {
    let socket =
        tokio::net::TcpStream::connect((config.endpoint.host.as_str(), config.endpoint.port.get()))
            .await?;
    socket.set_nodelay(true)?;
    config
        .tls
        .connect(&config.server_name, socket)
        .await
        .map_err(|_| Error::Permission("secure server authentication failed".into()))
}

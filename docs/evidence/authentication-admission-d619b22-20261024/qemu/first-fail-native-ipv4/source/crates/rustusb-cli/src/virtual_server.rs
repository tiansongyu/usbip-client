//! Legacy virtual CLI parameters; the service retains every real owner.

use super::shutdown_signals::ShutdownSignals;
use rustusb_core::Result;
use rustusb_rust_backend::ServerConfig;
use rustusb_service::jobs::LoadedJob;
use std::{
    net::{IpAddr, SocketAddr},
    path::PathBuf,
};

pub(super) async fn run(
    listen: SocketAddr,
    trusted_network: bool,
    allowed_peers: Vec<IpAddr>,
    control_stdin: bool,
) -> Result<()> {
    super::foreground::run(
        LoadedJob::VirtualLegacy(ServerConfig {
            listen,
            trusted_network,
            allowed_peers,
        }),
        // The memory-only virtual owner never contacts a privileged helper.
        PathBuf::new(),
        control_stdin,
        ShutdownSignals::new()?,
    )
    .await
}

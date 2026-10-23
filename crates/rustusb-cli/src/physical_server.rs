//! Legacy physical CLI parameters; the service owns grants and actual cleanup.

use super::shutdown_signals::ShutdownSignals;
use rustusb_core::Result;
use rustusb_rust_backend::ServerConfig;
use rustusb_service::jobs::LoadedJob;
use std::{
    net::{IpAddr, SocketAddr},
    path::Path,
};

pub(super) async fn run(
    socket: &Path,
    device_ids: Vec<String>,
    listen: SocketAddr,
    trusted_network: bool,
    allowed_peers: Vec<IpAddr>,
    control_stdin: bool,
) -> Result<()> {
    super::foreground::run(
        LoadedJob::PhysicalLegacy {
            config: ServerConfig {
                listen,
                trusted_network,
                allowed_peers,
            },
            device_ids,
        },
        socket.to_owned(),
        control_stdin,
        ShutdownSignals::new()?,
    )
    .await
}

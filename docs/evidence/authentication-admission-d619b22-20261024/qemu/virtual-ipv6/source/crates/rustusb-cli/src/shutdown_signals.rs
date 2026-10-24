//! Cooperative server termination, registered before acquiring service resources.

use rustusb_core::{Error, Result};
#[cfg(unix)]
use tokio::signal::unix::{Signal, SignalKind, signal};

pub(super) struct ShutdownSignals {
    #[cfg(unix)]
    interrupt: Signal,
    #[cfg(unix)]
    terminate: Signal,
}

impl ShutdownSignals {
    /// Register both Unix signals inside the running Tokio runtime.
    pub(super) fn new() -> Result<Self> {
        Ok(Self {
            #[cfg(unix)]
            interrupt: signal(SignalKind::interrupt())?,
            #[cfg(unix)]
            terminate: signal(SignalKind::terminate())?,
        })
    }

    /// Wait for at least one shutdown request; Unix notifications may coalesce.
    pub(super) async fn recv(&mut self) -> Result<()> {
        #[cfg(unix)]
        {
            let received = tokio::select! {
                received = self.interrupt.recv() => received,
                received = self.terminate.recv() => received,
            };
            received.ok_or_else(|| {
                Error::Io(std::io::Error::new(
                    std::io::ErrorKind::BrokenPipe,
                    "server shutdown signal stream ended",
                ))
            })
        }
        #[cfg(not(unix))]
        {
            tokio::signal::ctrl_c().await.map_err(Error::Io)
        }
    }
}

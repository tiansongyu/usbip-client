//! Shared, read-only local discovery for ordinary-user frontends.
//!
//! Monitoring is metadata only. Neither snapshots nor notification payloads
//! authorize device use, establish compatibility, or prove USB cleanup.

use rustusb_core::{Device, DeviceDiscovery, Error, Result};
use std::sync::{Arc, LazyLock};
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

static WORKERS: LazyLock<Arc<Semaphore>> = LazyLock::new(|| Arc::new(Semaphore::new(2)));

fn permit(pool: Arc<Semaphore>) -> Result<OwnedSemaphorePermit> {
    pool.try_acquire_owned()
        .map_err(|_| Error::State("local discovery workers are busy".into()))
}

/// Scan using the common bounded application entry point, without device writes.
///
/// Cancelling the awaiter does not terminate a started OS read or return its
/// actual worker quota. Linux is required; this does not invoke the root helper.
pub async fn scan() -> Result<Vec<Device>> {
    #[cfg(target_os = "linux")]
    {
        let permit = permit(WORKERS.clone())?;
        tokio::task::spawn_blocking(move || {
            let _permit = permit;
            rustusb_linux::SysfsDiscovery::default().scan()
        })
        .await
        .map_err(|error| Error::State(format!("local discovery worker: {error}")))?
    }
    #[cfg(not(target_os = "linux"))]
    {
        Err(Error::Unsupported(
            "local USB discovery requires Linux".into(),
        ))
    }
}

#[cfg(target_os = "linux")]
mod monitor;
#[cfg(target_os = "linux")]
pub use monitor::*;

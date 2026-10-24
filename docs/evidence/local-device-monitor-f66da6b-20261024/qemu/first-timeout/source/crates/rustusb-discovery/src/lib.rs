//! Shared, read-only local discovery for ordinary-user frontends.
//!
//! Monitoring is metadata only. Neither snapshots nor notification payloads
//! authorize device use, establish compatibility, or prove USB cleanup.

#[cfg(target_os = "linux")]
use rustusb_core::DeviceDiscovery;
use rustusb_core::{Device, Error, Result};
#[cfg(target_os = "linux")]
use std::sync::{Arc, LazyLock};
#[cfg(target_os = "linux")]
use tokio::sync::{OwnedSemaphorePermit, Semaphore};

#[cfg(target_os = "linux")]
static WORKERS: LazyLock<Arc<Semaphore>> = LazyLock::new(|| Arc::new(Semaphore::new(2)));

#[cfg(target_os = "linux")]
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
        scan_work(WORKERS.clone(), || {
            rustusb_linux::SysfsDiscovery::default().scan()
        })
        .await
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

#[cfg(target_os = "linux")]
async fn scan_work(
    pool: Arc<Semaphore>,
    work: impl FnOnce() -> Result<Vec<Device>> + Send + 'static,
) -> Result<Vec<Device>> {
    let permit = permit(pool)?;
    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        work()
    })
    .await
    .map_err(|error| Error::State(format!("local discovery worker: {error}")))?
}

/// Read-only observed metadata and stamps, with no device capabilities.
#[cfg(target_os = "linux")]
pub use rustusb_linux::observation::{ObservationStamp, ObservedDevice};

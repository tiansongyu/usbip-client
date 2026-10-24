//! Retained local observation work, with a private runtime and no USB owner.

use rustusb_core::{Error, Result};
use rustusb_daemon::diagnostics::{HelperDiagnostics, diagnose_helper};
use rustusb_native::diagnostics::{NativeDiagnostics, diagnose_native_with_child_reaping};
use std::path::PathBuf;

// Raw observations deliberately have no serialization or Debug implementation.
// Only the closed artifact projection may cross the collection boundary.
pub(crate) struct NativeObservations {
    pub(crate) native: Result<NativeDiagnostics>,
    pub(crate) helper: HelperDiagnostics,
}

/// Observe local prerequisites, retaining all accepted work until it completes.
///
/// The caller must keep polling this future after a stop request. Dropping a
/// `spawn_blocking` handle cannot cancel the running closure. Its private runtime
/// joins timed-out internal filesystem work before returning; explicit Native
/// child waits, rather than runtime destruction, provide child reap receipts.
pub(crate) async fn collect(socket: PathBuf) -> Result<NativeObservations> {
    tokio::task::spawn_blocking(move || {
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .max_blocking_threads(4)
            .build()?;
        let (native, helper) = runtime.block_on(async {
            // A Native cleanup error must not abandon the helper observation.
            tokio::join!(
                diagnose_native_with_child_reaping(),
                diagnose_helper(&socket)
            )
        });
        // Do not replace this with shutdown_background/shutdown_timeout: local
        // probe deadlines do not terminate filesystem tasks already in flight.
        drop(runtime);
        Ok(NativeObservations { native, helper })
    })
    .await
    .map_err(|_| Error::State("diagnostic local observer worker failed".into()))?
}

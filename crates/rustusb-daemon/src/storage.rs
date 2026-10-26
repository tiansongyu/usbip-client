//! Root-only storage observations, outside all mutation/journal locks.
use crate::{Error, Result};
use std::sync::Arc;
use tokio::sync::Semaphore;

async fn observe<T, F>(uid: u32, device_id: &str, slots: Arc<Semaphore>, work: F) -> Result<T>
where
    T: Send + 'static,
    F: FnOnce() -> Result<T> + Send + 'static,
{
    if uid != 0 {
        return Err(Error::Permission("storage inspection requires root".into()));
    }
    crate::validate_device_id(device_id)?;
    let permit = slots
        .try_acquire_owned()
        .map_err(|_| Error::State("a storage inspection is already running".into()))?;
    tokio::task::spawn_blocking(move || {
        // Lost RPC/awaiter does not release the actual worker quota.
        let _permit = permit;
        work()
    })
    .await
    .map_err(|_| Error::State("storage inspection worker failed".into()))?
}

pub(crate) async fn serve(
    mut stream: tokio::net::UnixStream,
    app: Arc<crate::linux::App>,
    uid: u32,
    device_id: String,
) -> Result<()> {
    let target = device_id.clone();
    let result = observe(uid, &device_id, app.storage_slots.clone(), move || {
        let report = rustusb_linux::storage::inspect(&target)?;
        let value = serde_json::to_value(report)
            .map_err(|_| Error::State("storage report serialization failed".into()))?;
        let bytes = serde_json::to_vec(&value)
            .map_err(|_| Error::State("storage report serialization failed".into()))?;
        if bytes.len() > 60 * 1024 {
            return Err(Error::State(
                "storage report exceeds IPC limit; no export permission was granted".into(),
            ));
        }
        Ok(value)
    })
    .await;
    let response = match result {
        Ok(value) => crate::Response::success(value),
        Err(error) => crate::Response::failure(error),
    };
    crate::linux::write_frame(&mut stream, &response).await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn unauthorized_and_invalid_inputs_never_start_observation() -> Result<()> {
        let count = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let slots = Arc::new(Semaphore::new(1));
        for (uid, id) in [
            (1000, format!("usb-{}", "a".repeat(64))),
            (0, "../../proc".into()),
        ] {
            let count = count.clone();
            let result = observe(uid, &id, slots.clone(), move || {
                count.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                Ok(())
            })
            .await;
            assert!(result.is_err());
        }
        assert_eq!(count.load(std::sync::atomic::Ordering::SeqCst), 0);
        assert_eq!(slots.available_permits(), 1);
        Ok(())
    }
    #[tokio::test]
    async fn lost_waiter_does_not_release_actual_worker_slot() -> Result<()> {
        let slots = Arc::new(Semaphore::new(1));
        let task_slots = slots.clone();
        let (started, ready) = tokio::sync::oneshot::channel();
        let (release, wait) = std::sync::mpsc::channel();
        let (finished, done) = tokio::sync::oneshot::channel();
        let job = tokio::spawn(async move {
            observe(
                0,
                &format!("usb-{}", "a".repeat(64)),
                task_slots,
                move || {
                    let _ = started.send(());
                    wait.recv()
                        .map_err(|_| Error::State("test gate disconnected".into()))?;
                    let _ = finished.send(());
                    Ok(())
                },
            )
            .await
        });
        ready
            .await
            .map_err(|_| Error::State("test worker not started".into()))?;
        job.abort();
        let _ = job.await;
        assert_eq!(slots.available_permits(), 0);
        let denied = observe(0, &format!("usb-{}", "b".repeat(64)), slots.clone(), || {
            Ok(())
        })
        .await;
        assert!(denied.is_err());
        release
            .send(())
            .map_err(|_| Error::State("test gate release failed".into()))?;
        done.await
            .map_err(|_| Error::State("test worker did not finish".into()))?;
        // Acquiring waits for the closure's actual permit drop, not its notification.
        let permit = slots
            .acquire()
            .await
            .map_err(|_| Error::State("test semaphore closed".into()))?;
        drop(permit);
        Ok(())
    }
    #[test]
    fn storage_request_is_strict() {
        assert!(
            serde_json::from_str::<crate::Request>(
                r#"{"op":"inspect_storage","device_id":"x","path":"/proc"}"#
            )
            .is_err()
        );
        assert!(serde_json::from_str::<crate::Request>(r#"{"op":"inspect_storage"}"#).is_err());
    }
}

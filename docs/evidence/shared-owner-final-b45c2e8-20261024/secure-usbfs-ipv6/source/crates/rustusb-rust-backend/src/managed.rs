//! Managed TLS sessions: admission precedes claim, while cleanup never awaits the manager.
use crate::{
    counted::Counted,
    monitor::{self, Counters, MonitorWriter, Tracked},
    server::{imported_connection, write_frame},
    session::{ConnectionAccess, ServingBackend, SessionLease},
    wire::{self, Management},
};
use rustusb_core::{
    Error, Result,
    server_session::{ServerCleanup, ServerSessionError, ServerSessionPhase},
};
use rustusb_protocol::secure::{self, DeviceMapping, SecureOperation};
use rustusb_transport::TlsServer;
use std::{
    future::poll_fn,
    ops::{Deref, DerefMut},
    time::Duration,
};
use tokio::{io::AsyncWrite, net::TcpStream, sync::watch};

const CLEANUP_NOTICE: Duration = Duration::from_secs(10);
struct Owned<L: SessionLease>(L);
impl<L: SessionLease> Deref for Owned<L> {
    type Target = L;
    fn deref(&self) -> &L {
        &self.0
    }
}
impl<L: SessionLease> DerefMut for Owned<L> {
    fn deref_mut(&mut self) -> &mut L {
        &mut self.0
    }
}
impl<L: SessionLease> Drop for Owned<L> {
    fn drop(&mut self) {
        self.0.begin_close();
    }
}

async fn completion(receiver: &mut watch::Receiver<Option<ServerCleanup>>) -> ServerCleanup {
    loop {
        if let Some(result) = *receiver.borrow_and_update() {
            return result;
        }
        if receiver.changed().await.is_err() {
            return ServerCleanup::Unconfirmed;
        }
    }
}
async fn claim_cleanup(
    receiver: &mut watch::Receiver<Option<ServerCleanup>>,
    tracked: &mut Tracked,
) -> ServerCleanup {
    let result = tokio::time::timeout(CLEANUP_NOTICE, completion(receiver)).await;
    match result {
        Ok(result) => result,
        Err(_) => {
            // This is a soft observation deadline, not permission to drop the
            // worker's ownership or release the outer connection permit.
            let _ = tracked.phase(ServerSessionPhase::CleanupUnconfirmed);
            completion(receiver).await
        }
    }
}
async fn close<L: SessionLease>(lease: &mut L, tracked: &mut Tracked) -> Result<()> {
    lease.begin_close();
    match tokio::time::timeout(CLEANUP_NOTICE, poll_fn(|cx| lease.poll_cleanup(cx))).await {
        Ok(result) => result,
        Err(_) => {
            let _ = tracked.phase(ServerSessionPhase::CleanupUnconfirmed);
            poll_fn(|cx| lease.poll_cleanup(cx)).await
        }
    }
}
fn combined(operation: Result<()>, cleanup: Result<()>) -> Result<()> {
    match (operation, cleanup) {
        (Err(_), Err(_)) => Err(Error::State("managed USB/IP operation failed and cleanup remains unconfirmed; inspect both monitor error categories".into())),
        (Err(error), _) | (_, Err(error)) => Err(error),
        (Ok(()), Ok(())) => Ok(()),
    }
}
async fn interruptible_write(
    writer: &mut (impl AsyncWrite + Unpin),
    frame: &[u8],
    stop: &mut watch::Receiver<monitor::Signal>,
) -> Result<()> {
    tokio::select! {
        biased;
        _ = monitor::stopped(stop) => Err(Error::State("managed USB/IP stop requested".into())),
        result = write_frame(writer, frame) => result,
    }
}

pub(crate) async fn accepted<B: ServingBackend>(
    socket: TcpStream,
    backend: B,
    tls: TlsServer,
    monitor: MonitorWriter,
) -> Result<()> {
    let mut server_stop = monitor.stopping();
    let stream = tokio::select! {
        biased;
        _ = monitor::server_stopped(&mut server_stop) => return Ok(()),
        result = tls.accept(socket) => result.map_err(|_| Error::Permission("managed mutual TLS authentication failed".into()))?,
    };
    let peer = stream.peer().clone();
    let access = ConnectionAccess::Authenticated(peer.clone());
    let counters = Counters::new();
    let stream = Counted::new(stream, counters.clone());
    let (mut reader, mut writer) = tokio::io::split(stream);
    let (prelude, management) = tokio::select! {
        biased;
        _ = monitor::server_stopped(&mut server_stop) => return Ok(()),
        result = async { Ok::<_, Error>((secure::read_prelude(&mut reader).await?, wire::read_management(&mut reader).await?)) } => result?,
    };
    let (key, bus_id) = match (prelude, management) {
        (SecureOperation::Catalog, Management::Devlist) => {
            let snapshot = backend.catalog(true, &access)?;
            let mappings: Vec<_> = snapshot
                .iter()
                .map(|entry| DeviceMapping {
                    device_key: entry.key.clone(),
                    bus_id: entry.metadata.bus_id.clone(),
                })
                .collect();
            let devices: Vec<_> = snapshot.into_iter().map(|entry| entry.metadata).collect();
            let reply = wire::devlist_reply(&devices)?;
            return tokio::select! {
                biased;
                _ = monitor::server_stopped(&mut server_stop) => Ok(()),
                result = async { secure::write_catalog_mapping(&mut writer, &mappings).await?; write_frame(&mut writer, &reply).await } => result,
            };
        }
        (SecureOperation::Import { device_key }, Management::Import(bus_id)) => {
            (device_key, bus_id)
        }
        _ => {
            return Err(Error::Protocol(
                "secure prelude and standard operation differ".into(),
            ));
        }
    };
    if !peer.allows(&key) {
        return write_frame(&mut writer, &wire::import_reply(4, None)?).await;
    }
    let mut tracked =
        match monitor.begin(key.clone(), peer.principal().to_owned(), counters.clone()) {
            Ok(tracked) => tracked,
            Err(_) => return write_frame(&mut writer, &wire::import_reply(2, None)?).await,
        };
    if let Err(error) = tracked.admission().await {
        let category = if tracked.signal.borrow().stop {
            ServerSessionError::Cancelled
        } else if matches!(error, Error::Permission(_)) {
            ServerSessionError::AdmissionRejected
        } else {
            monitor::category(&error)
        };
        if !tracked.signal.borrow().stop {
            let status = if matches!(error, Error::Permission(_)) {
                4
            } else {
                2
            };
            let _ = interruptible_write(
                &mut writer,
                &wire::import_reply(status, None)?,
                &mut tracked.signal,
            )
            .await;
        }
        tracked.finish(ServerCleanup::NotStarted, Some(category), None)?;
        return Err(error);
    }
    // Recheck the sticky stop immediately before any backend mutation. The
    // atomic ACL/key/bus-id check remains inside backend.claim_managed.
    if tracked.signal.borrow().stop {
        tracked.finish(
            ServerCleanup::NotStarted,
            Some(ServerSessionError::Cancelled),
            None,
        )?;
        return Ok(());
    }
    tracked.phase(ServerSessionPhase::Claiming)?;
    let (terminal, mut completed) = watch::channel(None);
    let mut stopping = tracked.signal.clone();
    let claimed = {
        let claim = backend.claim_managed(&bus_id, Some(&key), &access, terminal);
        tokio::pin!(claim);
        tokio::select! {
            biased;
            _ = monitor::stopped(&mut stopping) => Err(Error::State("managed claim stopped; awaiting its supervisor".into())),
            result = &mut claim => result,
            _ = tokio::time::sleep(wire::FRAME_DEADLINE) => Err(Error::Timeout("managed device claim; actual worker remains supervised".into())),
        }
        // Dropping this ready waiter requests cleanup; completed is the unique
        // worker's terminal witness and remains owned below.
    };
    let mut lease = match claimed {
        Ok(lease) => Owned(lease),
        Err(error) => {
            if !stopping.borrow().stop {
                let status = if matches!(error, Error::Permission(_) | Error::InvalidInput(_)) {
                    4
                } else {
                    2
                };
                let _ = interruptible_write(
                    &mut writer,
                    &wire::import_reply(status, None)?,
                    &mut stopping,
                )
                .await;
            }
            drop(reader);
            drop(writer);
            let _ = tracked.phase(ServerSessionPhase::Closing);
            let cleanup = claim_cleanup(&mut completed, &mut tracked).await;
            let operation = if stopping.borrow().stop {
                ServerSessionError::Cancelled
            } else {
                monitor::category(&error)
            };
            tracked.finish(
                cleanup,
                Some(operation),
                (cleanup == ServerCleanup::Unconfirmed).then_some(ServerSessionError::State),
            )?;
            return Err(error);
        }
    };
    // Lease ownership exists before any observation, encoding, write or flush.
    let operation = async {
        tracked.phase(ServerSessionPhase::Started)?;
        let reply = wire::import_reply(0, Some(&lease.device()))?;
        interruptible_write(&mut writer, &reply, &mut stopping).await?;
        tracked.phase(ServerSessionPhase::Connected)?;
        counters.enable();
        tokio::select! {
            biased;
            _ = monitor::stopped(&mut stopping) => Ok(()),
            result = imported_connection(&mut *lease, reader, &mut writer) => result,
        }
    }
    .await;
    // The nested actor owned reader; returning/dropping it ends partial frames.
    drop(writer);
    let _ = tracked.phase(ServerSessionPhase::Closing);
    let cleanup = close(&mut *lease, &mut tracked).await;
    let operation_error = operation.as_ref().err().map(monitor::category).or_else(|| {
        stopping
            .borrow()
            .stop
            .then_some(ServerSessionError::Cancelled)
    });
    let cleanup_error = cleanup.as_ref().err().map(monitor::category);
    tracked.finish(
        if cleanup.is_ok() {
            ServerCleanup::Verified
        } else {
            ServerCleanup::Unconfirmed
        },
        operation_error,
        cleanup_error,
    )?;
    combined(operation, cleanup)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test(start_paused = true)]
    async fn delayed_attempt_completion_keeps_ownership_after_soft_cleanup_deadline() -> Result<()>
    {
        let observer = crate::ServerMonitor::new();
        let writer = observer.writer();
        writer.attach()?;
        let mut tracked = writer.begin(
            rustusb_core::DeviceKey::virtual_profile(rustusb_core::VirtualProfile::Hid),
            "test-principal".into(),
            Counters::new(),
        )?;
        tracked.phase(ServerSessionPhase::Claiming)?;
        tracked.phase(ServerSessionPhase::Closing)?;
        let id = tracked.id;
        let (sender, mut receiver) = watch::channel(None);
        let waiting = tokio::spawn(async move {
            let cleanup = claim_cleanup(&mut receiver, &mut tracked).await;
            tracked.finish(cleanup, Some(ServerSessionError::Cancelled), None)
        });
        tokio::task::yield_now().await;
        tokio::time::advance(CLEANUP_NOTICE + Duration::from_secs(1)).await;
        tokio::task::yield_now().await;
        let snapshot = observer.snapshot()?;
        assert_eq!(
            snapshot.sessions[0].phase,
            ServerSessionPhase::CleanupUnconfirmed
        );
        assert_eq!(snapshot.sessions[0].cleanup, None);
        assert!(!waiting.is_finished());
        assert!(
            observer
                .acknowledge(id, snapshot.sessions[0].revision)
                .is_err()
        );
        // Only this attempt's terminal witness ends the wait; elapsed time cannot.
        sender.send_replace(Some(ServerCleanup::Verified));
        waiting
            .await
            .map_err(|_| Error::State("test task failed".into()))??;
        assert_eq!(
            observer.snapshot()?.sessions[0].cleanup,
            Some(ServerCleanup::Verified)
        );
        Ok(())
    }
    #[tokio::test]
    async fn lost_attempt_witness_is_unconfirmed_not_verified() {
        let (sender, mut receiver) = watch::channel(None);
        drop(sender);
        assert_eq!(completion(&mut receiver).await, ServerCleanup::Unconfirmed);
    }
}

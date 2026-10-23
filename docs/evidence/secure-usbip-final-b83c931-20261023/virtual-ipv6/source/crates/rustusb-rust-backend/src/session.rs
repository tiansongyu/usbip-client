//! Device-independent transfer and cleanup boundary for the shared wire actor.

use crate::{
    device::{DeviceLease, VirtualBackend},
    wire::{Completion, Submit},
};
use rustusb_core::{DeviceKey, Error, Result};
use rustusb_protocol::RemoteDevice;
use rustusb_transport::VerifiedPeer;
use std::{
    collections::VecDeque,
    future::Future,
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
    time::Duration,
};
use tokio::time::{Instant, Sleep};

pub(crate) type TransferId = u64;

pub(crate) enum Started {
    Ready(Completion),
    Queued,
}

pub(crate) enum TransferEvent {
    Completed {
        id: TransferId,
        completion: Completion,
    },
    Cancelled {
        id: TransferId,
        completion: Completion,
    },
}

/// The authenticated stream is the only network source of this private context.
#[derive(Clone)]
pub(crate) enum ConnectionAccess {
    Legacy,
    Authenticated(VerifiedPeer),
}

impl ConnectionAccess {
    pub(crate) fn allows(&self, key: &DeviceKey) -> bool {
        match self {
            Self::Legacy => true,
            Self::Authenticated(peer) => peer.allows(key),
        }
    }

    /// The requested stable key, current registry key and ACL must all agree.
    /// Legacy requests never carry a secure key and cannot authorize TLS fallback.
    pub(crate) fn require(&self, key: &DeviceKey, expected: Option<&DeviceKey>) -> Result<()> {
        let permitted = match self {
            Self::Legacy => expected.is_none(),
            Self::Authenticated(peer) => expected == Some(key) && peer.allows(key),
        };
        if permitted {
            Ok(())
        } else {
            Err(Error::Permission(
                "device is not permitted for this connection".into(),
            ))
        }
    }
}

pub(crate) struct RegisteredDevice {
    pub(crate) key: DeviceKey,
    pub(crate) metadata: RemoteDevice,
}

pub(crate) trait ServingBackend: Clone + Send + Sync + 'static {
    type Lease: SessionLease;
    fn catalog(
        &self,
        available_only: bool,
        access: &ConnectionAccess,
    ) -> Result<Vec<RegisteredDevice>>;
    #[cfg(test)]
    fn remote_devices(&self, available_only: bool) -> Result<Vec<RemoteDevice>> {
        Ok(self
            .catalog(available_only, &ConnectionAccess::Legacy)?
            .into_iter()
            .map(|entry| entry.metadata)
            .collect())
    }
    fn claim(
        &self,
        bus_id: &str,
        expected: Option<&DeviceKey>,
        access: &ConnectionAccess,
    ) -> impl Future<Output = Result<Self::Lease>> + Send;
    /// Await all sessions already closing, never treating a deadline as release.
    fn settle_closed(&self) -> impl Future<Output = Result<()>> + Send;
}

pub(crate) trait SessionLease: Send {
    fn device(&self) -> RemoteDevice;
    /// Own the request without cloning its OUT buffer. This must not block.
    fn start(&mut self, id: TransferId, request: Submit) -> Result<Started>;
    /// Cancel-safe observation; hardware ownership survives a dropped poll future.
    fn poll_event(&mut self, cx: &mut Context<'_>) -> Poll<Result<TransferEvent>>;
    /// Independent lifetime signal, including when no URBs are active. Ready
    /// means the device/session is terminal, never merely idle or available.
    fn poll_disconnect(&mut self, _cx: &mut Context<'_>) -> Poll<Result<()>> {
        Poll::Pending
    }
    /// Request only. A later terminal event decides which side won the race.
    fn request_cancel(&mut self, id: TransferId) -> Result<()>;
    /// Idempotently release response-held budget after its final write completes.
    fn retire(&mut self, id: TransferId) -> Result<()>;
    fn record_unlink(&mut self, cancelled: bool) -> Result<()>;
    /// Idempotent; Drop must also initiate closing without claiming completion.
    fn begin_close(&mut self);
    fn poll_cleanup(&mut self, cx: &mut Context<'_>) -> Poll<Result<()>>;
}

pub(crate) struct VirtualLease {
    metadata: RemoteDevice,
    lease: Option<Arc<DeviceLease>>,
    pending: VecDeque<(TransferId, Submit)>,
    events: VecDeque<TransferEvent>,
    wake: Option<Pin<Box<dyn Future<Output = ()> + Send>>>,
    tick: Pin<Box<Sleep>>,
}

impl ServingBackend for VirtualBackend {
    type Lease = VirtualLease;
    fn catalog(
        &self,
        available_only: bool,
        access: &ConnectionAccess,
    ) -> Result<Vec<RegisteredDevice>> {
        self.catalog_with_access(available_only, access)
    }
    async fn claim(
        &self,
        bus_id: &str,
        expected: Option<&DeviceKey>,
        access: &ConnectionAccess,
    ) -> Result<Self::Lease> {
        let lease = Arc::new(self.claim_with_access(bus_id, expected, access)?);
        Ok(VirtualLease {
            metadata: lease.device(),
            lease: Some(lease),
            pending: VecDeque::new(),
            events: VecDeque::new(),
            wake: None,
            tick: Box::pin(tokio::time::sleep(Duration::from_millis(10))),
        })
    }
    async fn settle_closed(&self) -> Result<()> {
        Ok(())
    }
}

impl VirtualLease {
    fn lease(&self) -> Result<&DeviceLease> {
        self.lease
            .as_deref()
            .ok_or_else(|| Error::State("virtual session is closing".into()))
    }
}

impl SessionLease for VirtualLease {
    fn device(&self) -> RemoteDevice {
        self.metadata.clone()
    }
    fn start(&mut self, id: TransferId, request: Submit) -> Result<Started> {
        match self.lease()?.submit(&request)? {
            Some(completion) => Ok(Started::Ready(completion)),
            None => {
                if request.direction != 1 {
                    return Err(Error::State(
                        "virtual backend left a non-IN request pending".into(),
                    ));
                }
                self.pending.push_back((id, request));
                self.lease()?.set_pending_count(self.pending.len())?;
                Ok(Started::Queued)
            }
        }
    }
    fn poll_event(&mut self, cx: &mut Context<'_>) -> Poll<Result<TransferEvent>> {
        if let Some(event) = self.events.pop_front() {
            return Poll::Ready(Ok(event));
        }
        if self.wake.is_none() {
            let Some(lease) = self.lease.clone() else {
                return Poll::Ready(Err(Error::State("virtual session is closing".into())));
            };
            self.wake = Some(Box::pin(async move {
                let notified = lease.notify().notified();
                tokio::pin!(notified);
                notified.as_mut().enable();
                notified.await;
            }));
        }
        // Register input notification before checking queues, avoiding lost wakes.
        if let Some(wake) = self.wake.as_mut()
            && wake.as_mut().poll(cx).is_ready()
        {
            self.wake = None;
        }
        for index in 0..self.pending.len() {
            let (id, request) = &self.pending[index];
            let id = *id;
            match self.lease().and_then(|lease| lease.poll_pending(request)) {
                Ok(Some(completion)) => {
                    self.pending.remove(index);
                    if let Err(error) = self
                        .lease()
                        .and_then(|lease| lease.set_pending_count(self.pending.len()))
                    {
                        return Poll::Ready(Err(error));
                    }
                    return Poll::Ready(Ok(TransferEvent::Completed { id, completion }));
                }
                Ok(None) => {}
                Err(error) => return Poll::Ready(Err(error)),
            }
        }
        // Only the virtual HID adapter needs a timer for SET_IDLE repetition.
        if self.tick.as_mut().poll(cx).is_ready() {
            self.tick
                .as_mut()
                .reset(Instant::now() + Duration::from_millis(10));
            let _ = self.tick.as_mut().poll(cx);
        }
        Poll::Pending
    }
    fn request_cancel(&mut self, id: TransferId) -> Result<()> {
        let index = self
            .pending
            .iter()
            .position(|(pending, _)| *pending == id)
            .ok_or_else(|| Error::State("virtual cancellation target is not active".into()))?;
        self.pending.remove(index);
        self.lease()?.set_pending_count(self.pending.len())?;
        self.events.push_back(TransferEvent::Cancelled {
            id,
            completion: Completion {
                status: -104,
                actual_length: 0,
                data: Vec::new(),
            },
        });
        Ok(())
    }
    fn retire(&mut self, _id: TransferId) -> Result<()> {
        Ok(())
    }
    fn record_unlink(&mut self, cancelled: bool) -> Result<()> {
        self.lease()?.record_unlink(cancelled)
    }
    fn begin_close(&mut self) {
        self.pending.clear();
        self.events.clear();
        self.wake = None;
        self.lease = None;
    }
    fn poll_cleanup(&mut self, _cx: &mut Context<'_>) -> Poll<Result<()>> {
        if self.lease.is_none() {
            Poll::Ready(Ok(()))
        } else {
            Poll::Pending
        }
    }
}

impl Drop for VirtualLease {
    fn drop(&mut self) {
        self.begin_close();
    }
}

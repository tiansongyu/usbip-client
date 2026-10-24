//! Concrete bounded worker-to-supervisor messages; no UI or global signal ownership.

use crate::model::{Cleanup, Evidence, Failure, Metrics, SessionRole};
use rustusb_core::{DeviceKey, Error, Result, SessionState, application::*};
use std::path::PathBuf;
use tokio::sync::{mpsc, oneshot, watch};

/// One durable request from a real owner. Reply only after the transaction commits.
pub enum JobMessage {
    /// Register a device session before admission or helper prepare.
    Begin {
        /// Owning job.
        job_id: JobId,
        /// Device role.
        role: SessionRole,
        /// Stable device key.
        device_key: DeviceKey,
        /// Verified certificate principal, never an operator claim.
        principal: Option<String>,
        /// Returns a concrete session reporter and independent stop receiver.
        reply: oneshot::Sender<Result<SessionContext>>,
    },
    /// Reserve an exact resource attempt before a possibly mutating request.
    Attempt {
        /// Existing session identity.
        session_id: SessionId,
        /// Resource kind.
        kind: BrokerResourceKind,
        /// Persisted fresh correlation.
        reply: oneshot::Sender<Result<BrokerCorrelation>>,
    },
    /// Register a received handle's actual ID.
    Resource {
        /// Exact pre-reserved attempt.
        correlation: BrokerCorrelation,
        /// Actual helper ID.
        resource_id: ResourceId,
        /// Durable acknowledgement.
        reply: oneshot::Sender<Result<()>>,
    },
    /// A real lifecycle fact, not a requested future state.
    Observe {
        /// Existing session.
        session_id: SessionId,
        /// Actual state.
        state: SessionState,
        /// Actual supporting evidence.
        evidence: Evidence,
        /// Optional current metrics.
        metrics: Option<Metrics>,
        /// Durable acknowledgement.
        reply: oneshot::Sender<Result<()>>,
    },
    /// The actual owner completed its cleanup path.
    Finish {
        /// Existing session.
        session_id: SessionId,
        /// Operation error retained independently of cleanup.
        operation_error: Option<Failure>,
        /// Actual cleanup result.
        cleanup: Cleanup,
        /// Final observed counters.
        metrics: Metrics,
        /// Durable acknowledgement; its failure must not undo or skip cleanup.
        reply: oneshot::Sender<Result<()>>,
    },
}

/// Concrete job owner context. Dropping a frontend never drops this receiver.
#[derive(Clone)]
pub struct JobContext {
    /// Original service instance.
    pub instance_id: InstanceId,
    /// Owning job identity.
    pub job_id: JobId,
    /// Administrator-selected helper endpoint.
    pub helper_socket: PathBuf,
    /// Cooperative stop; a sender disappearing also requests stop.
    pub stop: watch::Receiver<bool>,
    /// Bounded worker fact channel, handled by the owning supervisor.
    pub facts: mpsc::Sender<JobMessage>,
}

impl JobContext {
    /// Persist one session before attempting any resource acquisition.
    pub async fn begin_session(
        &self,
        role: SessionRole,
        device_key: DeviceKey,
        principal: Option<String>,
    ) -> Result<SessionContext> {
        let (reply, receive) = oneshot::channel();
        self.facts
            .send(JobMessage::Begin {
                job_id: self.job_id.clone(),
                role,
                device_key,
                principal,
                reply,
            })
            .await
            .map_err(|_| unavailable())?;
        receive.await.map_err(|_| unavailable())?
    }
}

/// A nonowning session reporter and stop receiver. The runner retains all handles.
#[derive(Clone)]
pub struct SessionContext {
    /// Nonowning live data-link observations; never a durable ACK.
    pub health: crate::health::HealthReporter,
    /// Durable session identity.
    pub id: SessionId,
    /// Cooperative per-session stop, distinct from proof of completion.
    pub stop: watch::Receiver<bool>,
    /// Bounded fact channel to the actual owner supervisor.
    pub facts: mpsc::Sender<JobMessage>,
}

impl SessionContext {
    /// Reserve and persist a fresh attempt before contacting the helper.
    pub async fn attempt(&self, kind: BrokerResourceKind) -> Result<BrokerCorrelation> {
        let (reply, receive) = oneshot::channel();
        self.facts
            .send(JobMessage::Attempt {
                session_id: self.id.clone(),
                kind,
                reply,
            })
            .await
            .map_err(|_| unavailable())?;
        receive.await.map_err(|_| unavailable())?
    }
    /// Persist a received capability ID; the caller still owns cleanup on error.
    pub async fn resource(&self, correlation: BrokerCorrelation, resource_id: &str) -> Result<()> {
        let resource_id = ResourceId::new(resource_id)?;
        let (reply, receive) = oneshot::channel();
        self.facts
            .send(JobMessage::Resource {
                correlation,
                resource_id,
                reply,
            })
            .await
            .map_err(|_| unavailable())?;
        receive.await.map_err(|_| unavailable())?
    }
    /// Persist an actual lifecycle observation. Never call Connected before its receipt.
    pub async fn observe(
        &self,
        state: SessionState,
        evidence: Evidence,
        metrics: Option<Metrics>,
    ) -> Result<()> {
        let (reply, receive) = oneshot::channel();
        self.facts
            .send(JobMessage::Observe {
                session_id: self.id.clone(),
                state,
                evidence,
                metrics,
                reply,
            })
            .await
            .map_err(|_| unavailable())?;
        receive.await.map_err(|_| unavailable())?
    }
    /// Record the completed real cleanup path, even when the operation failed.
    pub async fn finish(
        &self,
        operation_error: Option<Failure>,
        cleanup: Cleanup,
        metrics: Metrics,
    ) -> Result<()> {
        let (reply, receive) = oneshot::channel();
        self.facts
            .send(JobMessage::Finish {
                session_id: self.id.clone(),
                operation_error,
                cleanup,
                metrics,
                reply,
            })
            .await
            .map_err(|_| unavailable())?;
        receive.await.map_err(|_| unavailable())?
    }
}

/// Wait for a sticky cooperative stop or owner disappearance.
pub async fn stopped(receiver: &mut watch::Receiver<bool>) {
    loop {
        if *receiver.borrow() || receiver.changed().await.is_err() {
            return;
        }
    }
}

fn unavailable() -> Error {
    Error::State("application supervisor unavailable".into())
}

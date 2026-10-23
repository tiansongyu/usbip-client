//! Private mode boundary; a foreground delivery is never a durable admission.

use crate::{
    context::{JobContext, SessionContext, stopped},
    foreground::{Control, Delivery, Event, ForegroundOptions},
    model::{Cleanup, Evidence, Failure, Metrics, SessionRole},
};
use rustusb_core::{
    DeviceKey, Error, Result, SessionState,
    application::{BrokerCorrelation, BrokerResourceKind},
};
use std::{path::PathBuf, sync::Arc};
use tokio::sync::{Mutex, mpsc, oneshot, watch};

enum Mode {
    Managed(JobContext),
    Foreground(Arc<ForegroundState>),
}
struct ForegroundState {
    events: mpsc::Sender<Delivery>,
    controls: Mutex<mpsc::Receiver<Control>>,
}
/// Internal concrete reporter shared by actual resource owner pipelines.
pub(crate) struct RunContext {
    mode: Mode,
    pub helper_socket: PathBuf,
    pub stop: watch::Receiver<bool>,
}
impl Clone for RunContext {
    fn clone(&self) -> Self {
        Self {
            mode: match &self.mode {
                Mode::Managed(c) => Mode::Managed(c.clone()),
                Mode::Foreground(c) => Mode::Foreground(c.clone()),
            },
            helper_socket: self.helper_socket.clone(),
            stop: self.stop.clone(),
        }
    }
}
impl RunContext {
    pub fn managed(context: JobContext) -> Self {
        Self {
            helper_socket: context.helper_socket.clone(),
            stop: context.stop.clone(),
            mode: Mode::Managed(context),
        }
    }
    pub fn foreground(options: ForegroundOptions) -> Self {
        Self {
            helper_socket: options.helper_socket,
            stop: options.stop,
            mode: Mode::Foreground(Arc::new(ForegroundState {
                events: options.connection.events,
                controls: Mutex::new(options.connection.controls),
            })),
        }
    }
    pub fn is_foreground(&self) -> bool {
        matches!(self.mode, Mode::Foreground(_))
    }
    #[cfg(feature = "rust-backend")]
    pub fn is_managed(&self) -> bool {
        !self.is_foreground()
    }
    pub async fn begin_session(
        &self,
        role: SessionRole,
        key: DeviceKey,
        principal: Option<String>,
    ) -> Result<RunSession> {
        match &self.mode {
            Mode::Managed(context) => Ok(context.begin_session(role, key, principal).await?.into()),
            Mode::Foreground(_) => Ok(RunSession {
                managed: None,
                stop: self.stop.clone(),
            }),
        }
    }
    // No stdout or other OS stream is owned here. Stop can interrupt a bounded
    // delivery at any point without cancelling the actual owner's cleanup.
    pub async fn emit(&self, event: Event) -> Result<()> {
        let Mode::Foreground(state) = &self.mode else {
            return Ok(());
        };
        let mut stop = self.stop.clone();
        let (complete, receive) = oneshot::channel();
        tokio::select! {
            biased;
            _=stopped(&mut stop)=>Ok(()),
            result=async{state.events.send(Delivery{event,complete}).await.map_err(|_|Error::State("foreground output observer ended".into()))?;receive.await.map_err(|_|Error::State("foreground output observer ended".into()))?}=>result,
        }
    }
    // Only the top-level owner consumes controls. Cloned connection/session
    // contexts do not install an additional consumer or expose this over RPC.
    pub async fn next_control(&self) -> Control {
        match &self.mode {
            Mode::Foreground(state) => state
                .controls
                .lock()
                .await
                .recv()
                .await
                .unwrap_or(Control::Eof),
            Mode::Managed(_) => std::future::pending().await,
        }
    }
}

pub(crate) struct RunSession {
    managed: Option<SessionContext>,
    pub stop: watch::Receiver<bool>,
}
impl Clone for RunSession {
    fn clone(&self) -> Self {
        Self {
            managed: self.managed.clone(),
            stop: self.stop.clone(),
        }
    }
}
impl From<SessionContext> for RunSession {
    fn from(value: SessionContext) -> Self {
        Self {
            stop: value.stop.clone(),
            managed: Some(value),
        }
    }
}
impl RunSession {
    pub fn link(&self, monitor: Option<rustusb_transport::LinkMonitor>) -> Result<()> {
        self.managed
            .as_ref()
            .map_or(Ok(()), |session| session.health.direct(monitor))
    }
    #[cfg(feature = "rust-backend")]
    pub fn server_link(
        &self,
        control: rustusb_rust_backend::ServerControl,
        id: rustusb_core::server_session::ServerSessionId,
    ) -> Result<()> {
        self.managed
            .as_ref()
            .map_or(Ok(()), |session| session.health.server(control, id))
    }
    /// None means the explicit foreground/untracked path, never a failed WAL.
    pub async fn attempt(&self, kind: BrokerResourceKind) -> Result<Option<BrokerCorrelation>> {
        match &self.managed {
            Some(session) => Ok(Some(session.attempt(kind).await?)),
            None => Ok(None),
        }
    }
    pub async fn resource(&self, correlation: Option<BrokerCorrelation>, id: &str) -> Result<()> {
        match (&self.managed, correlation) {
            (Some(session), Some(correlation)) => session.resource(correlation, id).await,
            (None, None) => Ok(()),
            _ => Err(Error::State("owner tracking mode mismatch".into())),
        }
    }
    pub async fn observe(
        &self,
        state: SessionState,
        evidence: Evidence,
        metrics: Option<Metrics>,
    ) -> Result<()> {
        match &self.managed {
            Some(session) => session.observe(state, evidence, metrics).await,
            None => Ok(()),
        }
    }
    pub async fn finish(
        &self,
        error: Option<Failure>,
        cleanup: Cleanup,
        metrics: Metrics,
    ) -> Result<()> {
        match &self.managed {
            Some(session) => {
                // Freeze only after the caller's real cleanup. Keep recording errors explicit.
                let frozen = session.health.freeze();
                let finished = session.finish(error, cleanup, metrics).await;
                match (frozen, finished) {
                    (Err(first), Err(second)) => Err(Error::State(format!(
                        "link checkpoint: {first}; terminal recording: {second}"
                    ))),
                    (Err(error), _) | (_, Err(error)) => Err(error),
                    (Ok(()), Ok(())) => Ok(()),
                }
            }
            None => Ok(()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::foreground;
    use rustusb_core::{
        VirtualProfile,
        application::{AttemptId, InstanceId, JobId, SessionId},
    };
    fn correlation() -> Result<BrokerCorrelation> {
        Ok(BrokerCorrelation {
            instance_id: InstanceId::new("00000000-0000-0000-0000-000000000001")?,
            session_id: SessionId::new("00000000-0000-0000-0000-000000000002")?,
            attempt_id: AttemptId::new("00000000-0000-0000-0000-000000000003")?,
        })
    }
    #[tokio::test]
    async fn explicit_foreground_has_no_correlation_or_durable_admission() -> Result<()> {
        let (_front, connection) = foreground::channel();
        let (_stop, receiver) = watch::channel(false);
        let context = RunContext::foreground(ForegroundOptions {
            helper_socket: PathBuf::from("/unused-test-helper"),
            stop: receiver,
            connection,
        });
        let session = context
            .begin_session(
                SessionRole::Import,
                DeviceKey::virtual_profile(VirtualProfile::Hid),
                None,
            )
            .await?;
        assert!(
            session
                .attempt(BrokerResourceKind::NativeImport)
                .await?
                .is_none()
        );
        assert!(
            session
                .resource(Some(correlation()?), "not-a-resource")
                .await
                .is_err()
        );
        session
            .observe(SessionState::Connecting, Evidence::Intent, None)
            .await?;
        session
            .finish(None, Cleanup::NotStarted, Metrics::default())
            .await?;
        Ok(())
    }
    #[tokio::test]
    async fn managed_admission_really_waits_and_propagates_rejection() -> Result<()> {
        let (sender, mut facts) = mpsc::channel(2);
        let (_stop, receiver) = watch::channel(false);
        let context = RunContext::managed(JobContext {
            instance_id: correlation()?.instance_id,
            job_id: JobId::new("00000000-0000-0000-0000-000000000004")?,
            helper_socket: "/unused-test-helper".into(),
            stop: receiver,
            facts: sender,
        });
        let task = tokio::spawn(async move {
            context
                .begin_session(
                    SessionRole::Incoming,
                    DeviceKey::virtual_profile(VirtualProfile::Hid),
                    None,
                )
                .await
        });
        let message = facts
            .recv()
            .await
            .ok_or_else(|| Error::State("missing actual admission request".into()))?;
        tokio::task::yield_now().await;
        assert!(!task.is_finished());
        let crate::context::JobMessage::Begin { reply, .. } = message else {
            return Err(Error::State("unexpected admission request".into()));
        };
        let _ = reply.send(Err(Error::Permission("test admission denied".into())));
        assert!(matches!(
            task.await
                .map_err(|_| Error::State("admission task failed".into()))?,
            Err(Error::Permission(_))
        ));
        Ok(())
    }
    #[tokio::test]
    async fn sticky_stop_interrupts_unacknowledged_output_delivery() -> Result<()> {
        let (mut front, connection) = foreground::channel();
        let (stop, receiver) = watch::channel(false);
        let context = RunContext::foreground(ForegroundOptions {
            helper_socket: "/unused-test-helper".into(),
            stop: receiver,
            connection,
        });
        let task = tokio::spawn(async move {
            context
                .emit(Event::Ack {
                    id: foreground::ControlId::new("actual-write")?,
                })
                .await
        });
        let delivery = front
            .events
            .recv()
            .await
            .ok_or_else(|| Error::State("missing actual output delivery".into()))?;
        assert!(!task.is_finished());
        stop.send_replace(true);
        tokio::time::timeout(std::time::Duration::from_secs(1), task)
            .await
            .map_err(|_| Error::Timeout("output stop".into()))?
            .map_err(|_| Error::State("output task failed".into()))??;
        // There was no stdout ACK; stop is independent of frontend progress.
        drop(delivery);
        Ok(())
    }
    #[tokio::test]
    async fn vanished_foreground_reader_reports_an_error_instead_of_fake_ack() -> Result<()> {
        let (front, connection) = foreground::channel();
        let (_stop, receiver) = watch::channel(false);
        drop(front);
        let context = RunContext::foreground(ForegroundOptions {
            helper_socket: "/unused-test-helper".into(),
            stop: receiver,
            connection,
        });
        assert!(
            context
                .emit(Event::Ack {
                    id: foreground::ControlId::new("observer-gone")?
                })
                .await
                .is_err()
        );
        Ok(())
    }
}

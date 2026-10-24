//! Retained metadata-only acknowledgements, never resource ownership.

use rustusb_core::Result;
use std::{future::Future, pin::Pin};

/// An original recording future. Drain only after all actual cleanup/Finish.
pub(super) type Pending = Pin<Box<dyn Future<Output = Result<()>> + Send + 'static>>;

pub(super) enum Wait<T> {
    Ready(Result<T>),
    Interrupted {
        pending: Pending,
        reason: Result<()>,
    },
}

/// Keep the exact future across stop, including a request not yet enqueued.
///
/// Only reporter/identity metadata belongs in `recording`. Do not use this for
/// Begin (whose returned session must be retained), a handle, or an initializer.
/// An interrupted correlation is never permission to start a later helper RPC.
pub(super) async fn wait<T, F, S>(recording: F, stop: S) -> Wait<T>
where
    T: Send + 'static,
    F: Future<Output = Result<T>> + Send + 'static,
    S: Future<Output = Result<()>>,
{
    let mut original = Box::pin(recording);
    tokio::select! {
        biased;
        reason = stop => Wait::Interrupted {
            pending: Box::pin(async move { original.await.map(|_| ()) }),
            reason,
        },
        result = &mut original => Wait::Ready(result),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        context::{JobMessage, SessionContext, stopped},
        health::HealthReporter,
    };
    use rustusb_core::{
        Error,
        application::{AttemptId, BrokerCorrelation, InstanceId, SessionId},
    };
    use tokio::sync::{mpsc, watch};

    #[tokio::test]
    async fn interrupted_real_resource_message_keeps_the_original_rejection() -> Result<()> {
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            let (facts, mut receive) = mpsc::channel(1);
            let (stop, mut stopping) = watch::channel(false);
            let session = SessionContext {
                health: HealthReporter::default(),
                id: SessionId::new("00000000-0000-0000-0000-000000000002")?,
                stop: stopping.clone(),
                facts,
            };
            let correlation = BrokerCorrelation {
                instance_id: InstanceId::new("00000000-0000-0000-0000-000000000001")?,
                session_id: session.id.clone(),
                attempt_id: AttemptId::new("00000000-0000-0000-0000-000000000003")?,
            };
            let task = tokio::spawn(wait(
                async move {
                    session
                        .resource(correlation, "00000000-0000-0000-0000-000000000004")
                        .await
                },
                async move {
                    stopped(&mut stopping).await;
                    Ok(())
                },
            ));
            let Some(JobMessage::Resource { reply, .. }) = receive.recv().await else {
                return Err(Error::State("missing actual Resource message".into()));
            };
            stop.send_replace(true);
            let Wait::Interrupted { pending, reason } = task
                .await
                .map_err(|_| Error::State("record wait task failed".into()))?
            else {
                return Err(Error::State("recording completed without its ACK".into()));
            };
            reason?;
            assert!(!reply.is_closed());
            reply
                .send(Err(Error::Permission(
                    "original recording rejection".into(),
                )))
                .map_err(|_| Error::State("original ACK receiver was dropped".into()))?;
            assert!(matches!(pending.await, Err(Error::Permission(_))));
            Ok(())
        })
        .await
        .map_err(|_| Error::Timeout("retained Resource ACK fixture".into()))?
    }

    #[tokio::test]
    async fn simultaneous_stop_does_not_poll_or_discard_the_original_record() -> Result<()> {
        let (polled, mut observe) = mpsc::channel(1);
        let result = wait(
            async move {
                polled
                    .send(())
                    .await
                    .map_err(|_| Error::State("fixture observer ended".into()))?;
                Ok(7_u8)
            },
            async { Ok(()) },
        )
        .await;
        let Wait::Interrupted { pending, reason } = result else {
            return Err(Error::State("ready record outran sticky stop".into()));
        };
        assert!(observe.try_recv().is_err());
        reason?;
        pending.await?;
        assert_eq!(observe.recv().await, Some(()));
        Ok(())
    }
}

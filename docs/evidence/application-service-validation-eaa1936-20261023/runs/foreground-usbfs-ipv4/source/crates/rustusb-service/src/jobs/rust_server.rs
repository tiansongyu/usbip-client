//! Managed Rust engine adapter. Admission is acknowledged before atomic claim.

use super::combined;
use crate::{
    context::{JobContext, SessionContext, stopped},
    model::{Cleanup, Evidence, Failure, Metrics, SessionRole},
};
use rustusb_core::{Error, Result, SessionState, server_session::*};
use rustusb_rust_backend::{SecureServerConfig, ServerHandle, ServerMonitor, VirtualBackend};
use rustusb_transport::config::LoadedServerConfig;
use std::{collections::BTreeSet, sync::Arc, time::Duration};
use tokio::{sync::watch, task::JoinSet};

fn metrics(value: ServerSessionMetrics) -> Metrics {
    Metrics {
        app_rx_bytes: value.app_rx_bytes,
        app_tx_bytes: value.app_tx_bytes,
        saturated: value.saturated,
        elapsed_millis: value.elapsed_millis,
    }
}
fn failure(value: ServerSessionError) -> Failure {
    match value {
        ServerSessionError::Permission => Failure::Permission,
        ServerSessionError::Protocol => Failure::InvalidInput,
        ServerSessionError::Io => Failure::Io,
        ServerSessionError::Timeout => Failure::Timeout,
        ServerSessionError::Unsupported => Failure::Unsupported,
        ServerSessionError::Cancelled => Failure::Cancelled,
        _ => Failure::State,
    }
}

async fn record(session: &SessionContext, value: &ServerSessionSnapshot) -> Result<bool> {
    let sampled = Some(metrics(value.metrics));
    match value.phase {
        ServerSessionPhase::Preparing | ServerSessionPhase::Claiming => {
            session
                .observe(SessionState::Connecting, Evidence::Intent, sampled)
                .await?
        }
        ServerSessionPhase::Started => {
            session
                .observe(SessionState::Connecting, Evidence::Prepared, sampled)
                .await?
        }
        ServerSessionPhase::Connected => {
            session
                .observe(
                    SessionState::Connected,
                    Evidence::ImportReplyFlushed,
                    sampled,
                )
                .await?
        }
        ServerSessionPhase::Closing => {
            session
                .observe(SessionState::Disconnecting, Evidence::Closing, sampled)
                .await?
        }
        ServerSessionPhase::Closed | ServerSessionPhase::CleanupUnconfirmed => {
            if value.cleanup.is_none() {
                // A soft cleanup deadline is an observation while the real
                // blocking owner remains alive. It is not an acknowledgeable
                // terminal and cannot retire this application session.
                session
                    .observe(SessionState::Error, Evidence::Unconfirmed, sampled)
                    .await?;
                return Ok(false);
            }
            let cleanup = match value.cleanup {
                Some(ServerCleanup::NotStarted) => Cleanup::NotStarted,
                Some(ServerCleanup::Verified) => Cleanup::Verified,
                Some(ServerCleanup::Unconfirmed) | None => Cleanup::Unconfirmed,
            };
            session
                .finish(
                    value.operation_error.map(failure),
                    cleanup,
                    metrics(value.metrics),
                )
                .await?;
            return Ok(true);
        }
    }
    Ok(false)
}

async fn incoming(monitor: ServerMonitor, id: ServerSessionId, context: JobContext) -> Result<()> {
    let initial = monitor
        .snapshot()?
        .sessions
        .into_iter()
        .find(|entry| entry.id == id)
        .ok_or_else(|| Error::State("managed attempt disappeared before admission".into()))?;
    let session = match context
        .begin_session(SessionRole::Incoming, initial.device_key, initial.principal)
        .await
    {
        Ok(session) => session,
        Err(error) => {
            let _ = monitor.reject(id);
            let _ = monitor.request_disconnect(id);
            return Err(error);
        }
    };
    let mut job_stop = context.stop.clone();
    let mut session_stop = session.stop.clone();
    let mut first_error = None;
    let mut changed = monitor.changes();
    let mut after = initial.revision.saturating_sub(1);
    let result = {
        let observing = async {
            if *context.stop.borrow() || *session.stop.borrow() {
                monitor.reject(id)?;
                monitor.request_disconnect(id)?;
            } else if let Err(error) = monitor.admit(id) {
                first_error = Some(error);
                let _ = monitor.request_disconnect(id);
            }
            loop {
                let page = monitor.events_after(after, 64)?;
                let full_page = page.events.len() == 64;
                // Other sessions acknowledge their own events concurrently. A
                // global gap does not discard this unacknowledged session's events.
                for event in page.events {
                    after = event.sequence;
                    if event.session.id != id {
                        continue;
                    }
                    if first_error.is_none() {
                        match record(&session, &event.session).await {
                            Ok(true) => {
                                monitor.acknowledge(id, event.session.revision)?;
                                return Ok(());
                            }
                            Ok(false) => {}
                            Err(error) => {
                                first_error = Some(error);
                                let _ = monitor.request_disconnect(id);
                            }
                        }
                    }
                    // A recording failure still waits for this attempt's real
                    // terminal owner. Never acknowledge a terminal not persisted.
                    if event.session.cleanup.is_some() {
                        return Ok(());
                    }
                }
                if full_page {
                    continue;
                }
                let current = monitor
                    .snapshot()?
                    .sessions
                    .into_iter()
                    .find(|entry| entry.id == id);
                if let Some(current) = current {
                    // Never jump to a newer phase with a metric sample and then
                    // replay an older lifecycle event on the next page.
                    if current.revision > after {
                        continue;
                    }
                    if current.cleanup.is_none() && first_error.is_none() {
                        // Periodic byte samples preserve the actual phase/evidence.
                        if let Err(error) = record(&session, &current).await {
                            first_error = Some(error);
                            let _ = monitor.request_disconnect(id);
                        }
                    }
                } else {
                    return Err(Error::State(
                        "managed owner vanished without a terminal observation".into(),
                    ));
                }
                tokio::select! {
                    result = changed.changed() => if result.is_err() { return Err(Error::State("managed monitor ended before cleanup".into())); },
                    _ = tokio::time::sleep(Duration::from_secs(1)) => {},
                }
            }
        };
        tokio::pin!(observing);
        // This select covers even an fsync acknowledgement. After a stop, retain
        // and join the same observer until this attempt's real terminal arrives.
        tokio::select! {
            biased;
            _ = stopped(&mut job_stop) => { let _ = monitor.request_disconnect(id); (&mut observing).await },
            _ = stopped(&mut session_stop) => { let _ = monitor.request_disconnect(id); (&mut observing).await },
            result = &mut observing => result,
        }
    };
    result?;
    first_error.map_or(Ok(()), Err)
}

async fn supervise(
    monitor: ServerMonitor,
    context: JobContext,
    mut finished: watch::Receiver<bool>,
    failed: watch::Sender<bool>,
) -> Result<()> {
    let mut changed = monitor.changes();
    let mut seen = BTreeSet::new();
    let mut tasks = JoinSet::new();
    let mut first = None;
    loop {
        let snapshot = monitor.snapshot()?;
        seen.retain(|id| snapshot.sessions.iter().any(|entry| entry.id == *id));
        for entry in &snapshot.sessions {
            if seen.insert(entry.id) {
                let monitor = monitor.clone();
                let context = context.clone();
                let id = entry.id;
                tasks.spawn(incoming(monitor, id, context));
            }
        }
        if *finished.borrow() && tasks.is_empty() {
            break;
        }
        tokio::select! {
            result = tasks.join_next(), if !tasks.is_empty() => {
                let result = match result { Some(Ok(result)) => result, Some(Err(_)) => Err(Error::State("managed session reporter failed".into())), None => Ok(()) };
                if let Err(error) = result { failed.send_replace(true); if first.is_none() { first = Some(error); } }
            },
            result = changed.changed() => if result.is_err() { failed.send_replace(true); if first.is_none() { first = Some(Error::State("managed monitor ended".into())); } },
            _ = finished.changed(), if !*finished.borrow() => {},
        }
    }
    first.map_or(Ok(()), Err)
}

struct Served {
    operation: Result<()>,
    cleanup: Result<()>,
    recorded: Result<()>,
}

async fn serving(
    server: ServerHandle,
    monitor: ServerMonitor,
    mut context: JobContext,
    exports: &[SessionContext],
) -> Served {
    let (done, finished) = watch::channel(false);
    let (failed, mut failure) = watch::channel(false);
    let observing = tokio::spawn(supervise(monitor, context.clone(), finished, failed));
    let ready = async {
        for export in exports {
            export
                .observe(SessionState::Available, Evidence::ExportReady, None)
                .await?;
        }
        Ok::<_, Error>(())
    }
    .await;
    let operation = match ready {
        Err(error) => Err(error),
        Ok(()) => {
            tokio::select! { biased; _ = stopped(&mut context.stop) => Ok(()), _ = stopped(&mut failure) => Err(Error::State("managed session persistence failed".into())) }
        }
    };
    // Keep the monitor consumer running while shutdown cooperatively joins all
    // actual claims/leases. A quiet final event still receives its durable ack.
    let shutdown = server.shutdown().await;
    done.send_replace(true);
    let observed = match observing.await {
        Ok(result) => result,
        Err(_) => Err(Error::State("managed monitor supervisor failed".into())),
    };
    Served {
        operation,
        cleanup: shutdown,
        recorded: observed,
    }
}

async fn exports(config: &LoadedServerConfig, context: &JobContext) -> Result<Vec<SessionContext>> {
    let mut sessions: Vec<SessionContext> = Vec::new();
    for key in &config.devices {
        let registered = context
            .begin_session(SessionRole::Export, key.clone(), None)
            .await;
        match registered {
            Ok(session) => sessions.push(session),
            Err(error) => {
                for session in sessions {
                    let _ = session
                        .finish(
                            Some(Failure::Persistence),
                            Cleanup::NotStarted,
                            Metrics::default(),
                        )
                        .await;
                }
                return Err(error);
            }
        }
    }
    Ok(sessions)
}

async fn finish_exports(
    exports: &[SessionContext],
    operation: &Result<()>,
    cleanup: Cleanup,
) -> Result<()> {
    let mut first = None;
    for session in exports {
        let observed = session
            .observe(SessionState::Disconnecting, Evidence::Closing, None)
            .await;
        let recorded = session
            .finish(
                operation.as_ref().err().map(Failure::from_error),
                cleanup,
                Metrics::default(),
            )
            .await;
        if first.is_none() {
            first = observed.err().or(recorded.err());
        }
    }
    first.map_or(Ok(()), Err)
}

pub(super) async fn virtual_server(
    config: Arc<LoadedServerConfig>,
    context: JobContext,
) -> Result<()> {
    let exports = exports(&config, &context).await?;
    let monitor = ServerMonitor::new();
    let serving = async {
        let backend = VirtualBackend::new()?;
        let server = ServerHandle::bind_managed_tls(
            SecureServerConfig {
                listen: config.listen,
            },
            backend,
            config.tls.clone(),
            monitor.clone(),
        )
        .await?;
        Ok::<_, Error>(serving(server, monitor, context, &exports).await)
    }
    .await;
    // All resources are memory-only, and serving joins the entire real task
    // tree before returning; binding failure acquired no connection leases.
    let (operation, cleanup) = match serving {
        Ok(served) => {
            let cleanup = if served.cleanup.is_ok() {
                Cleanup::Verified
            } else {
                Cleanup::Unconfirmed
            };
            (
                combined(served.operation, served.cleanup, served.recorded),
                cleanup,
            )
        }
        Err(error) => (Err(error), Cleanup::Verified),
    };
    let recorded = finish_exports(&exports, &operation, cleanup).await;
    combined(operation, Ok(()), recorded)
}

#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
pub(super) async fn physical_server(
    config: Arc<LoadedServerConfig>,
    context: JobContext,
) -> Result<()> {
    use rustusb_core::application::BrokerResourceKind;
    use rustusb_daemon::{Request, open_secure_physical_tracked, request};
    use rustusb_rust_backend::{PhysicalBackend, PhysicalSource};
    let exports = exports(&config, &context).await?;
    let mut sources = Vec::new();
    let mut leases: Vec<Option<String>> = vec![None; exports.len()];
    let mut attempted = vec![false; exports.len()];
    let preparation = async {
        for (index, (key, session)) in config.devices.iter().zip(&exports).enumerate() {
            if *context.stop.borrow() || *session.stop.borrow() {
                return Err(Error::State("physical preparation stopped".into()));
            }
            let correlation = session.attempt(BrokerResourceKind::UsbfsGrant).await?;
            attempted[index] = true;
            let id = key
                .fingerprint()
                .ok_or_else(|| Error::InvalidInput("physical fingerprint required".into()))?;
            let grant = open_secure_physical_tracked(
                &context.helper_socket,
                id.to_owned(),
                correlation.clone(),
            )
            .await?;
            leases[index] = Some(grant.lease_id.clone());
            sources.push(PhysicalSource {
                fd: grant.fd,
                device: grant.device,
                receipt: grant.receipt,
            });
            session.resource(correlation, &grant.lease_id).await?;
        }
        Ok::<_, Error>(())
    }
    .await;
    let mut backend = None;
    let operation = match preparation {
        Err(error) => {
            drop(sources);
            Err(error)
        }
        Ok(()) => match PhysicalBackend::from_sources(sources).await {
            Err(error) => Err(error),
            Ok(owner) => {
                backend = Some(owner.clone());
                let monitor = ServerMonitor::new();
                match ServerHandle::bind_physical_managed_tls(
                    SecureServerConfig {
                        listen: config.listen,
                    },
                    owner,
                    config.tls.clone(),
                    monitor.clone(),
                )
                .await
                {
                    Ok(server) => {
                        let served = serving(server, monitor, context.clone(), &exports).await;
                        combined(served.operation, served.cleanup, served.recorded)
                    }
                    Err(error) => Err(error),
                }
            }
        },
    };
    // Initializer is always awaited rather than cancelled. Once it has ended,
    // close every worker before independently finishing all received grants.
    let closed = match backend {
        Some(owner) => owner.close().await.map(|_| ()),
        None => Ok(()),
    };
    let mut first_error = None;
    for (index, session) in exports.iter().enumerate() {
        let cleanup = match &leases[index] {
            Some(lease_id) if closed.is_ok() => {
                match request(
                    &context.helper_socket,
                    Request::FinishPhysical {
                        lease_id: lease_id.clone(),
                    },
                )
                .await
                {
                    Ok(response) if response.ok => Ok(()),
                    Ok(_) => Err(Error::State("helper physical finish unconfirmed".into())),
                    Err(error) => Err(error),
                }
            }
            Some(_) => Err(Error::State("physical worker close unconfirmed".into())),
            None if attempted[index] => {
                Err(Error::State("physical grant outcome unconfirmed".into()))
            }
            None => Ok(()),
        };
        let evidence = if cleanup.is_err() {
            Cleanup::Unconfirmed
        } else if attempted[index] {
            Cleanup::Verified
        } else {
            Cleanup::NotStarted
        };
        let observed = session
            .observe(SessionState::Disconnecting, Evidence::Closing, None)
            .await;
        let recorded = session
            .finish(
                operation.as_ref().err().map(Failure::from_error),
                evidence,
                Metrics::default(),
            )
            .await;
        if first_error.is_none() {
            first_error = cleanup.err().or(observed.err()).or(recorded.err());
        }
    }
    combined(operation, closed, first_error.map_or(Ok(()), Err))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context::JobMessage;
    use rustusb_core::{DeviceKey, VirtualProfile, application::SessionId};
    use tokio::sync::mpsc;

    // These exercise the service's fact conversion and durable acknowledgement
    // boundary, not a real USB claim or a blocking kernel completion.
    #[tokio::test]
    async fn soft_cleanup_deadline_is_not_finished_until_real_terminal() -> Result<()> {
        let (facts, mut receiver) = mpsc::channel(2);
        let (_stop, stop) = watch::channel(false);
        let session = SessionContext {
            id: SessionId::new("00000000-0000-0000-0000-000000000001")?,
            stop,
            facts,
        };
        let mut value = ServerSessionSnapshot {
            id: ServerSessionId::new(1)?,
            device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
            principal: Some("client".into()),
            phase: ServerSessionPhase::CleanupUnconfirmed,
            stop_requested: true,
            revision: 5,
            metrics: ServerSessionMetrics::default(),
            operation_error: Some(ServerSessionError::Cancelled),
            cleanup_error: Some(ServerSessionError::Timeout),
            cleanup: None,
        };
        let owner = session.clone();
        let observed = value.clone();
        let task = tokio::spawn(async move { record(&owner, &observed).await });
        match tokio::time::timeout(Duration::from_secs(2), receiver.recv())
            .await
            .map_err(|_| Error::Timeout("soft deadline fixture fact".into()))?
        {
            Some(JobMessage::Observe {
                state: SessionState::Error,
                evidence: Evidence::Unconfirmed,
                reply,
                ..
            }) => {
                let _ = reply.send(Ok(()));
            }
            _ => {
                return Err(Error::State(
                    "soft deadline incorrectly emitted a terminal fact".into(),
                ));
            }
        }
        assert!(
            !task
                .await
                .map_err(|_| Error::State("soft deadline fixture task".into()))??
        );
        value.phase = ServerSessionPhase::Closed;
        value.cleanup = Some(ServerCleanup::Verified);
        value.cleanup_error = None;
        value.revision = 6;
        let task = tokio::spawn(async move { record(&session, &value).await });
        match tokio::time::timeout(Duration::from_secs(2), receiver.recv())
            .await
            .map_err(|_| Error::Timeout("terminal fixture fact".into()))?
        {
            Some(JobMessage::Finish {
                cleanup: Cleanup::Verified,
                operation_error: Some(Failure::Cancelled),
                reply,
                ..
            }) => {
                let _ = reply.send(Ok(()));
            }
            _ => {
                return Err(Error::State(
                    "real terminal did not emit its separate cleanup result".into(),
                ));
            }
        }
        assert!(
            task.await
                .map_err(|_| Error::State("terminal fixture task".into()))??
        );
        Ok(())
    }
}

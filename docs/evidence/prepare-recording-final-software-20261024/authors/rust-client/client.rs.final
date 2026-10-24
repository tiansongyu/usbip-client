use super::{
    discover_until,
    io::{self, Counters},
    link, recording,
};
use crate::{
    context::stopped,
    foreground::{Event, JobOutcome},
    model::{Cleanup, Evidence, Failure, SessionRole},
    run_context::RunContext,
};
use rustusb_core::{Error, SessionState, application::BrokerResourceKind};
use rustusb_daemon::{SecureImportTarget, prepare_native_import, prepare_native_import_tracked};
use rustusb_transport::config::LoadedClientConfig;
use std::{future::Future, pin::Pin, sync::Arc, time::Duration};
use tokio::sync::watch;

enum StageError {
    Operation(Error),
    Recording(Error),
}
impl From<Error> for StageError {
    fn from(error: Error) -> Self {
        Self::Operation(error)
    }
}

type Recording = Pin<Box<dyn Future<Output = rustusb_core::Result<()>> + Send>>;
struct Stops {
    job: watch::Receiver<bool>,
    session: watch::Receiver<bool>,
    invalidated: watch::Receiver<bool>,
    link: Option<rustusb_transport::LinkMonitor>,
    ended: Option<watch::Receiver<bool>>,
}
impl Stops {
    async fn wait<T>(
        &mut self,
        future: impl Future<Output = T>,
    ) -> std::result::Result<Option<T>, StageError> {
        tokio::select! {
            biased;
            _=stopped(&mut self.job)=>Ok(None),
            _=stopped(&mut self.session)=>Ok(None),
            _=stopped(&mut self.invalidated)=>Err(Error::State("secure helper channel invalidated".into()).into()),
            error=link::ended(&mut self.ended,self.link.as_ref())=>Err(StageError::Operation(error)),
            result=future=>Ok(Some(result)),
        }
    }
}
async fn record_or_stop(
    mut recording: Recording,
    pending: &mut Option<Recording>,
    stops: &mut Stops,
) -> std::result::Result<bool, StageError> {
    match stops.wait(&mut recording).await {
        Ok(Some(result)) => result.map(|()| true).map_err(StageError::Recording),
        stopped => {
            *pending = Some(recording);
            stopped.map(|_| false)
        }
    }
}

async fn cleanup_then_record(
    cleanup: impl Future<Output = rustusb_core::Result<()>>,
    pending: Option<Recording>,
) -> (rustusb_core::Result<()>, rustusb_core::Result<()>) {
    let cleaned = cleanup.await;
    let recorded = match pending {
        Some(recording) => recording.await,
        None => Ok(()),
    };
    (cleaned, recorded)
}

pub(super) async fn run(
    config: Arc<LoadedClientConfig>,
    timeout: Duration,
    context: RunContext,
) -> JobOutcome {
    let session = match context
        .begin_session(SessionRole::Import, config.device.clone(), None)
        .await
    {
        Ok(session) => session,
        Err(error) => {
            return JobOutcome {
                operation: Ok(()),
                cleanup: Ok(()),
                recording: Err(error),
                final_event: None,
            };
        }
    };
    let mut session_stop = session.stop.clone();
    let mut job_stop = context.stop.clone();
    let counters = Counters::new();
    let mut attempted = false;
    let mut handle = None;
    let mut copying = None;
    let mut copy_finished = false;
    let mut link_owner = None;
    let mut link_monitor = None;
    let mut postponed_recording = None;
    let operation = async {
        session.observe(SessionState::Connecting, Evidence::Intent, None).await.map_err(StageError::Recording)?;
        let deadline = tokio::time::Instant::now() + timeout;
        let selection = discover_until(&config, deadline, async {
            tokio::select! { _=stopped(&mut job_stop)=>{}, _=stopped(&mut session_stop)=>{} }
        }).await?;
        let Some(catalog) = selection else { return Ok(()); };
        let selected = catalog.into_iter().find(|entry| entry.device_key == config.device)
            .ok_or_else(|| Error::Permission(if context.is_foreground() {
                "configured remote device is unavailable or unauthorized"
            } else { "configured remote device unavailable or unauthorized" }.into()))?;
        let connection = tokio::select! {
            biased;
            _=stopped(&mut job_stop)=>return Ok(()),
            _=stopped(&mut session_stop)=>return Ok(()),
            result=tokio::time::timeout_at(deadline, link::connect(&config))=>result.map_err(|_|Error::Timeout("secure import connection".into()))??,
        };
        link_owner = connection.owner;
        link_monitor = connection.monitor;
        session.link(link_monitor.clone()).map_err(StageError::Recording)?;
        let mut network = connection.io;
        let imported = tokio::select! {
            biased;
            _=stopped(&mut job_stop)=>return Ok(()),
            _=stopped(&mut session_stop)=>return Ok(()),
            result=tokio::time::timeout_at(deadline, rustusb_protocol::secure::import_device(&mut network, &selected))=>result.map_err(|_|Error::Timeout("secure selection/import including DNS/TCP/TLS".into())).and_then(|result|result).map(|(_, remote)| remote),
        };
        let remote = match imported {
            Ok(remote) => remote,
            Err(error) => {
                // A complete IMPORT refusal is a short management exchange.
                // Keep I/O alive for bounded FIN/EOF; malformed or incomplete
                // replies still retain the original operation failure.
                let closed = link::complete_management(&mut network, &mut link_owner, async {
                    tokio::select! {
                        biased;
                        _=stopped(&mut job_stop)=>Ok(()),
                        _=stopped(&mut session_stop)=>Ok(()),
                        _=tokio::time::sleep_until(deadline)=>Err(Error::Timeout("secure rejected import close".into())),
                    }
                }).await;
                return Err(match closed {
                    Ok(_) => error,
                    Err(closed) => Error::State(format!("secure IMPORT failed ({error}); framed refusal close failed ({closed})")),
                }.into());
            }
        };
        if *context.stop.borrow() || context.stop.has_changed().is_err()
            || *session.stop.borrow() || session.stop.has_changed().is_err() { return Ok(()); }
        link::check(link_monitor.as_ref())?;
        let reporter = session.clone();
        let mut import_ended = link_monitor.as_ref().map(rustusb_transport::LinkMonitor::ended);
        let correlation = match recording::wait(
            async move { reporter.attempt(BrokerResourceKind::NativeImport).await },
            async {
                tokio::select! {
                    biased;
                    _=stopped(&mut job_stop)=>Ok(()),
                    _=stopped(&mut session_stop)=>Ok(()),
                    error=link::ended(&mut import_ended,link_monitor.as_ref())=>Err(error),
                }
            },
        ).await {
            recording::Wait::Ready(result) => result.map_err(StageError::Recording)?,
            recording::Wait::Interrupted { pending, reason } => {
                // The original managed reservation may still commit. Keep the
                // existing conservative no-handle outcome, but never send a
                // helper request after its late ACK. Network cleanup comes first.
                attempted = !context.is_foreground();
                postponed_recording = Some(pending);
                return reason.map_err(StageError::Operation);
            }
        };
        // A managed reservation is already durable even before its RPC. A
        // foreground None has no WAL and remains NotStarted until we send.
        attempted = correlation.is_some();
        if *context.stop.borrow() || *session.stop.borrow() { return Ok(()); }
        link::check(link_monitor.as_ref())?;
        // Never cancel an accepted helper prepare. Receive the actual handle,
        // record its ID, and then close it when a stop arrived meanwhile.
        let target = SecureImportTarget {
            server_id: config.server_id.clone(), device_key: config.device.clone(), remote,
        };
        attempted = true;
        handle = Some(match correlation.clone() {
            Some(correlation) => prepare_native_import_tracked(&context.helper_socket, target, correlation).await?,
            None => prepare_native_import(&context.helper_socket, target).await?,
        });
        let owner = handle.as_mut().ok_or_else(|| Error::State("import owner absent".into()))?;
        let mut stops = Stops {
            job:context.stop.clone(),session:session.stop.clone(),invalidated:owner.invalidated(),
            link:link_monitor.clone(),ended:link_monitor.as_ref().map(rustusb_transport::LinkMonitor::ended),
        };
        let reporter=session.clone(); let channel_id=owner.channel_id().to_owned();
        if !record_or_stop(Box::pin(async move { reporter.resource(correlation,&channel_id).await }),&mut postponed_recording,&mut stops).await? { return Ok(()); }
        let reporter=session.clone();
        if !record_or_stop(Box::pin(async move { reporter.observe(SessionState::Connecting,Evidence::Prepared,None).await }),&mut postponed_recording,&mut stops).await? { return Ok(()); }
        if *context.stop.borrow() || *session.stop.borrow() { return Ok(()); }
        link::check(link_monitor.as_ref())?;
        let kernel = owner.take_relay()?;
        copying = Some(tokio::spawn(relay_link(network, kernel, context.is_foreground(), counters.clone(), link_monitor.clone())));
        // An accepted helper mutation remains owned until its actual response.
        // No timeout or WAL waiter drops this exchange or fabricates its result.
        let receipt = owner.commit().await?;
        let reporter=session.clone(); let sampled=counters.snapshot();
        if !record_or_stop(Box::pin(async move { reporter.observe(SessionState::Connected,Evidence::LocalEnumeration,Some(sampled)).await }),&mut postponed_recording,&mut stops).await? { return Ok(()); }
        let event=Event::Connected {server_id:config.server_id.clone(),device_key:config.device.clone(),receipt:Box::new(receipt)};
        match stops.wait(context.emit(event)).await? {Some(result)=>result?,None=>return Ok(())}
        let mut sample = tokio::time::interval(Duration::from_secs(1));
        sample.tick().await;
        loop {
            let task = copying.as_mut().ok_or_else(|| Error::State("secure relay owner absent".into()))?;
            let event=stops.wait(async {
                tokio::select! {
                    result=&mut *task=>Some(result),
                    _=sample.tick()=>None,
                }
            }).await?;
            match event {
                None=>return Ok(()),
                Some(Some(result))=>{
                    copy_finished=true;
                    return result.map_err(|_|Error::State("secure relay task failed".into()))?.map_err(StageError::Operation);
                },
                Some(None)=>{
                    let reporter=session.clone();let sampled=counters.snapshot();
                    if !record_or_stop(Box::pin(async move {reporter.observe(SessionState::Connected,Evidence::LocalEnumeration,Some(sampled)).await}),&mut postponed_recording,&mut stops).await? {return Ok(());}
                }
            }
        }
    }.await;
    // Stop and join all network owners before asking K's actual helper owner
    // for cleanup. Recording errors do not bypass this sequence.
    if let Some(task) = copying.filter(|_| !copy_finished) {
        if !task.is_finished() {
            task.abort();
        }
        let _ = task.await;
    }
    let (link_result, _) = link::finish(&mut link_owner).await;
    let (mut operation, earlier_recording) = match operation {
        Ok(()) => (Ok(()), Ok(())),
        Err(StageError::Operation(error)) => (Err(error), Ok(())),
        Err(StageError::Recording(error)) => (Ok(()), Err(error)),
    };
    if let Err(error) = link_result {
        operation = Err(error);
    }
    // Keep the accepted recording future, but let it own no K/R/network
    // capability while disk is delayed. Original ACK precedes Closing/Finish.
    let (cleanup, pending_recording) = cleanup_then_record(
        async {
            match handle {
                Some(owner) => owner.close().await,
                None if attempted => Err(Error::State("import prepare outcome unconfirmed".into())),
                None => Ok(()),
            }
        },
        postponed_recording,
    )
    .await;
    let closing = session
        .observe(
            SessionState::Disconnecting,
            Evidence::Closing,
            Some(counters.snapshot()),
        )
        .await;
    let outcome = if cleanup.is_err() {
        Cleanup::Unconfirmed
    } else if attempted {
        Cleanup::Verified
    } else {
        Cleanup::NotStarted
    };
    let failure = operation
        .as_ref()
        .err()
        .or(earlier_recording.as_ref().err())
        .or(pending_recording.as_ref().err())
        .or(closing.as_ref().err())
        .map(Failure::from_error);
    let recorded = session.finish(failure, outcome, counters.snapshot()).await;
    let recording = recording_results([earlier_recording, pending_recording, closing, recorded]);
    let final_event = (attempted && operation.is_ok() && cleanup.is_ok() && recording.is_ok())
        .then_some(Event::Disconnected);
    JobOutcome {
        operation,
        cleanup,
        recording,
        final_event,
    }
}

fn recording_results<const N: usize>(
    results: [rustusb_core::Result<()>; N],
) -> rustusb_core::Result<()> {
    let mut errors = results.into_iter().filter_map(std::result::Result::err);
    let Some(first) = errors.next() else {
        return Ok(());
    };
    let remaining: Vec<_> = errors.collect();
    if remaining.is_empty() {
        return Err(first);
    }
    Err(Error::State(
        std::iter::once(first)
            .chain(remaining)
            .map(|error| format!("recording: {error}"))
            .collect::<Vec<_>>()
            .join("; "),
    ))
}

#[cfg(test)]
async fn relay<A, B>(
    network: A,
    kernel: B,
    foreground: bool,
    counters: Arc<Counters>,
) -> rustusb_core::Result<()>
where
    A: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
    B: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    relay_link(network, kernel, foreground, counters, None).await
}

async fn relay_link<A, B>(
    network: A,
    kernel: B,
    foreground: bool,
    counters: Arc<Counters>,
    monitor: Option<rustusb_transport::LinkMonitor>,
) -> rustusb_core::Result<()>
where
    A: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
    B: tokio::io::AsyncRead + tokio::io::AsyncWrite + Unpin,
{
    let mut ended = monitor.as_ref().map(rustusb_transport::LinkMonitor::ended);
    tokio::select! {
        biased;
        error = link::ended(&mut ended, monitor.as_ref()) => Err(error),
        result = async {
    if foreground {
        // Keep draining the opposite half after the first EOF, just as the
        // original foreground command did. No managed metric is fabricated.
        let (mut network, mut kernel) = (network, kernel);
        tokio::io::copy_bidirectional(&mut network, &mut kernel).await?;
        Ok(())
    } else {
        io::relay(network, kernel, counters).await
    }
        } => result,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    #[tokio::test]
    async fn stopped_import_attempt_closes_actual_tls_before_late_ack_and_never_calls_helper()
    -> crate::jobs::management_tests::TestResult {
        use crate::context::{JobContext, JobMessage, SessionContext};
        use crate::jobs::management_tests::{credentials, device, loaded};
        use rustusb_core::{
            DeviceKey, VirtualProfile,
            application::{AttemptId, BrokerCorrelation, InstanceId, JobId, SessionId},
        };
        use rustusb_protocol::{secure, server};
        use tokio::{
            net::TcpListener,
            sync::{mpsc, oneshot},
        };

        tokio::time::timeout(Duration::from_secs(15), async {
            for framed in [false, true] {
                let (tls, client) = credentials(framed)?;
                let listener = TcpListener::bind("127.0.0.1:0").await?;
                let config = Arc::new(loaded(client, listener.local_addr()?)?);
                let (closed_tx, closed_rx) = oneshot::channel();
                let peer =
                    tokio::spawn(async move {
                        let (tcp, _) = listener.accept().await?;
                        let (mut connection, _) = link::accept(&tls, tcp).await?;
                        assert!(matches!(
                            secure::read_prelude(&mut connection.io).await?,
                            secure::SecureOperation::Catalog
                        ));
                        assert!(matches!(
                            server::read_management(&mut connection.io).await?,
                            server::Management::Devlist
                        ));
                        let remote = device()?;
                        secure::write_catalog_mapping(
                            &mut connection.io,
                            &[secure::DeviceMapping {
                                device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
                                bus_id: remote.bus_id.clone(),
                            }],
                        )
                        .await?;
                        connection
                            .io
                            .write_all(&server::devlist_reply(std::slice::from_ref(&remote))?)
                            .await?;
                        connection.io.flush().await?;
                        assert!(
                            link::complete_management(
                                &mut connection.io,
                                &mut connection.owner,
                                std::future::pending()
                            )
                            .await?
                        );
                        drop(connection);
                        let (tcp, _) = listener.accept().await?;
                        let (mut connection, _) = link::accept(&tls, tcp).await?;
                        assert!(matches!(
                            secure::read_prelude(&mut connection.io).await?,
                            secure::SecureOperation::Import { .. }
                        ));
                        assert!(matches!(
                            server::read_management(&mut connection.io).await?,
                            server::Management::Import(_)
                        ));
                        connection
                            .io
                            .write_all(&server::import_reply(0, Some(&remote))?)
                            .await?;
                        connection.io.flush().await?;
                        let mut byte = [0];
                        let read = connection.io.read(&mut byte).await;
                        assert!(matches!(read, Ok(0) | Err(_)));
                        let (_, sample) = link::finish(&mut connection.owner).await;
                        if framed {
                            assert!(sample.is_some_and(|sample| sample.phase
                                == rustusb_core::link_health::LinkPhase::Ended));
                        }
                        closed_tx
                            .send(())
                            .map_err(|_| Error::State("TLS close observer ended".into()))?;
                        Ok::<_, Error>(())
                    });
                let (facts, mut receiver) = mpsc::channel(2);
                let (stop, stopping) = watch::channel(false);
                let instance = InstanceId::new("00000000-0000-0000-0000-000000000001")?;
                let id = SessionId::new("00000000-0000-0000-0000-000000000003")?;
                let directory = tempfile::tempdir()?;
                let helper = directory.path().join("must-not-be-contacted.sock");
                let context = RunContext::managed(JobContext {
                    instance_id: instance.clone(),
                    job_id: JobId::new("00000000-0000-0000-0000-000000000002")?,
                    helper_socket: helper.clone(),
                    stop: stopping.clone(),
                    facts: facts.clone(),
                });
                let task = tokio::spawn(run(config, Duration::from_secs(5), context));
                let Some(JobMessage::Begin { reply, .. }) = receiver.recv().await else {
                    return Err(Error::State("missing real client Begin".into()).into());
                };
                let _ = reply.send(Ok(SessionContext {
                    health: crate::health::HealthReporter::default(),
                    id: id.clone(),
                    stop: stopping,
                    facts,
                }));
                let Some(JobMessage::Observe {
                    evidence: Evidence::Intent,
                    reply,
                    ..
                }) = receiver.recv().await
                else {
                    return Err(Error::State("missing client Intent".into()).into());
                };
                let _ = reply.send(Ok(()));
                let Some(JobMessage::Attempt {
                    kind: BrokerResourceKind::NativeImport,
                    reply,
                    ..
                }) = receiver.recv().await
                else {
                    return Err(Error::State("missing held actual Attempt".into()).into());
                };
                stop.send_replace(true);
                closed_rx.await?;
                assert!(!task.is_finished() && !reply.is_closed());
                assert!(receiver.try_recv().is_err());
                assert!(!helper.exists());
                let late = if framed {
                    Ok(BrokerCorrelation {
                        instance_id: instance,
                        session_id: id,
                        attempt_id: AttemptId::new("00000000-0000-0000-0000-000000000004")?,
                    })
                } else {
                    Err(Error::Permission("late original Attempt rejection".into()))
                };
                reply
                    .send(late)
                    .map_err(|_| Error::State("original Attempt future dropped".into()))?;
                let Some(JobMessage::Observe {
                    evidence: Evidence::Closing,
                    reply,
                    ..
                }) = receiver.recv().await
                else {
                    return Err(Error::State(
                        "stopped Attempt sent unexpected helper preparation fact".into(),
                    )
                    .into());
                };
                let _ = reply.send(Ok(()));
                let Some(JobMessage::Finish {
                    cleanup: Cleanup::Unconfirmed,
                    reply,
                    ..
                }) = receiver.recv().await
                else {
                    return Err(Error::State(
                        "reserved unsent Attempt lost conservative outcome".into(),
                    )
                    .into());
                };
                let _ = reply.send(Ok(()));
                let outcome = task.await?;
                // A late Attempt ACK cannot cause the deliberately nonexistent
                // helper to be contacted: that would add a real operation error.
                assert!(outcome.operation.is_ok());
                assert!(outcome.cleanup.is_err());
                assert_eq!(outcome.recording.is_ok(), framed);
                assert!(!helper.exists());
                peer.await??;
            }
            Ok::<_, Box<dyn std::error::Error + Send + Sync>>(())
        })
        .await??;
        Ok(())
    }

    #[tokio::test]
    async fn link_failure_during_an_accepted_ack_cleans_before_waiting_for_that_same_ack()
    -> rustusb_core::Result<()> {
        // Actual in-memory stream lifetime and production ownership helpers;
        // the cleanup closure is not a claim of real helper/kernel validation.
        let (live, receiver) = watch::channel(false);
        let (link_end, ended) = watch::channel(false);
        let (relay, mut kernel) = tokio::io::duplex(1);
        let (recording, began) = tokio::sync::oneshot::channel();
        let (ack, receipt) = tokio::sync::oneshot::channel::<rustusb_core::Result<()>>();
        let (cleaned, closed) = tokio::sync::oneshot::channel();
        let task = tokio::spawn(async move {
            let mut stops = Stops {
                job: receiver.clone(),
                session: receiver.clone(),
                invalidated: receiver,
                link: None,
                ended: Some(ended),
            };
            let mut pending = None;
            let operation = record_or_stop(
                Box::pin(async move {
                    recording
                        .send(())
                        .map_err(|_| Error::State("fixture recording observer".into()))?;
                    receipt
                        .await
                        .map_err(|_| Error::State("accepted ACK future dropped".into()))?
                }),
                &mut pending,
                &mut stops,
            )
            .await;
            assert!(matches!(operation, Err(StageError::Operation(_))));
            cleanup_then_record(
                async move {
                    drop(relay);
                    cleaned
                        .send(())
                        .map_err(|_| Error::State("fixture cleanup observer".into()))?;
                    Err(Error::State("actual fixture close failure".into()))
                },
                pending,
            )
            .await
        });
        tokio::time::timeout(Duration::from_secs(2), async {
            began
                .await
                .map_err(|_| Error::State("fixture ACK not polled".into()))?;
            link_end.send_replace(true);
            closed
                .await
                .map_err(|_| Error::State("link-ended did not reach cleanup".into()))?;
            assert!(!*live.borrow());
            assert!(!task.is_finished());
            let mut byte = [0];
            assert_eq!(kernel.read(&mut byte).await?, 0);
            ack.send(Err(Error::State("original WAL error".into())))
                .map_err(|_| Error::State("accepted original ACK was cancelled".into()))?;
            let (cleanup, recording) = task
                .await
                .map_err(|_| Error::State("fixture client owner".into()))?;
            assert!(matches!(cleanup, Err(Error::State(_))));
            assert!(matches!(recording, Err(Error::State(_))));
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("client link-ended ACK fixture".into()))?
    }

    #[tokio::test]
    async fn foreground_half_close_still_drains_the_actual_reverse_stream()
    -> rustusb_core::Result<()> {
        let (network, mut peer) = tokio::io::duplex(16);
        let (kernel, mut device) = tokio::io::duplex(16);
        let task = tokio::spawn(relay(network, kernel, true, Counters::new()));
        tokio::time::timeout(Duration::from_secs(2), async {
            peer.write_all(b"request").await?;
            peer.shutdown().await?;
            let mut received = Vec::new();
            device.read_to_end(&mut received).await?;
            assert_eq!(received, b"request");
            assert!(!task.is_finished());
            device.write_all(b"reply-after-eof").await?;
            device.shutdown().await?;
            let mut returned = Vec::new();
            peer.read_to_end(&mut returned).await?;
            assert_eq!(returned, b"reply-after-eof");
            task.await
                .map_err(|_| Error::State("foreground relay fixture task".into()))??;
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("foreground half-close fixture".into()))?
    }

    #[tokio::test]
    async fn managed_mode_retires_both_halves_at_the_first_eof() -> rustusb_core::Result<()> {
        let (network, mut peer) = tokio::io::duplex(16);
        let (kernel, mut device) = tokio::io::duplex(16);
        let task = tokio::spawn(relay(network, kernel, false, Counters::new()));
        tokio::time::timeout(Duration::from_secs(2), async {
            peer.shutdown().await?;
            task.await
                .map_err(|_| Error::State("managed relay fixture task".into()))??;
            let mut byte = [0u8; 1];
            assert_eq!(device.read(&mut byte).await?, 0);
            assert_eq!(peer.read(&mut byte).await?, 0);
            Ok::<_, Error>(())
        })
        .await
        .map_err(|_| Error::Timeout("managed first-EOF fixture".into()))?
    }

    #[test]
    fn all_recording_failures_are_retained() {
        let result = recording_results([
            Err(Error::State("original admission".into())),
            Err(Error::State("closing receipt".into())),
            Err(Error::State("terminal receipt".into())),
        ]);
        let text = match result {
            Err(error) => error.to_string(),
            Ok(()) => String::new(),
        };
        for message in ["original admission", "closing receipt", "terminal receipt"] {
            assert!(text.contains(message));
        }
    }
}

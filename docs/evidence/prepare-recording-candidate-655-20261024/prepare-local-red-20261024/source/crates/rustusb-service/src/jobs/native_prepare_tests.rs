//! Ordinary-user production-owner fixtures: no root validation bypass, USB or helper proof.
use super::*;
use crate::{
    context::{JobContext, JobMessage, SessionContext},
    health::HealthReporter,
};
use rustusb_core::application::{AttemptId, BrokerCorrelation, InstanceId, JobId, SessionId};
use tokio::{
    io::AsyncReadExt,
    net::UnixStream,
    sync::{mpsc, oneshot},
};

fn correlation(number: u8) -> Result<BrokerCorrelation> {
    Ok(BrokerCorrelation {
        instance_id: InstanceId::new("00000000-0000-0000-0000-000000000001")?,
        session_id: SessionId::new(format!("00000000-0000-0000-0000-{number:012x}"))?,
        attempt_id: AttemptId::new(format!("00000000-0000-0000-0001-{number:012x}"))?,
    })
}
fn session(
    number: u8,
    facts: mpsc::Sender<JobMessage>,
    stop: watch::Receiver<bool>,
) -> Result<RunSession> {
    Ok(RunSession::from(SessionContext {
        health: HealthReporter::default(),
        id: correlation(number)?.session_id,
        stop,
        facts,
    }))
}
fn fixture_export(
    number: u8,
    facts: mpsc::Sender<JobMessage>,
    stop: watch::Receiver<bool>,
    fail: bool,
) -> Result<(Arc<Export>, UnixStream, watch::Sender<bool>)> {
    let key = DeviceKey::native(&format!("usb-{number:064x}"))?;
    let (invalidated, invalidation) = watch::channel(false);
    let (requested, _) = watch::channel(false);
    let (mut control, peer) = UnixStream::pair()?;
    let export = Arc::new(Export {
        key,
        published: super::super::management_tests::device()?,
        handle: Mutex::new(None),
        session: session(number, facts, stop)?,
        invalidated: invalidation,
        stop: requested,
        use_lock: Mutex::new(()),
        reserved: AtomicBool::new(false),
        failed: AtomicBool::new(false),
        pending_record: Mutex::new(None),
        prior_failure: Mutex::new(None),
        fixture_close: Mutex::new(Some(Box::pin(async move {
            control.write_all(b"close").await?;
            let mut receipt = [0; 2];
            control.read_exact(&mut receipt).await?;
            if receipt != *b"ok" {
                return Err(Error::Protocol("fixture close receipt".into()));
            }
            if fail {
                Err(Error::State("fixture actual close failed".into()))
            } else {
                Ok(())
            }
        }))),
    });
    Ok((export, peer, invalidated))
}
async fn recorded(
    export: Arc<Export>,
    number: u8,
    stop: &watch::Receiver<bool>,
) -> std::result::Result<Option<Arc<Export>>, JobOutcome> {
    let key = export.key.clone();
    let lease = format!("00000000-0000-0000-0002-{number:012x}");
    let correlation = match correlation(number) {
        Ok(value) => value,
        Err(error) => return Err(JobOutcome::before_start(error)),
    };
    finish_received_export(export, key, lease, Some(correlation), stop).await
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Stage {
    Begin,
    Attempt,
    Resource,
    Available,
}
enum Held {
    Unit(oneshot::Sender<Result<()>>),
    Begin(oneshot::Sender<Result<SessionContext>>),
    Attempt(oneshot::Sender<Result<BrokerCorrelation>>),
}
impl Held {
    fn closed(&self) -> bool {
        match self {
            Self::Unit(x) => x.is_closed(),
            Self::Begin(x) => x.is_closed(),
            Self::Attempt(x) => x.is_closed(),
        }
    }
    fn reject(self) -> Result<()> {
        fn rejected<T>() -> Result<T> {
            Err(Error::State(
                "fixture original preparation ACK rejected".into(),
            ))
        }
        let delivered = match self {
            Self::Unit(x) => x.send(rejected()).is_ok(),
            Self::Begin(x) => x.send(rejected()).is_ok(),
            Self::Attempt(x) => x.send(rejected()).is_ok(),
        };
        if delivered {
            Ok(())
        } else {
            Err(Error::State(
                "original preparation ACK was cancelled".into(),
            ))
        }
    }
}
async fn read_closes(peers: &mut [UnixStream], observed: &mut [Vec<u8>]) -> Result<()> {
    for (peer, bytes) in peers.iter_mut().zip(observed) {
        // One-byte reads preserve fixture progress even if the observation
        // deadline fires; no cancelled read_exact loses a partial message.
        while bytes.len() < 5 {
            bytes.push(peer.read_u8().await?);
        }
        if bytes != b"close" {
            return Err(Error::Protocol("fixture Close command".into()));
        }
    }
    Ok(())
}
async fn finalize_messages(
    messages: &mut mpsc::Receiver<JobMessage>,
    count: usize,
    failed: bool,
    mut prefix: std::collections::VecDeque<JobMessage>,
) -> Result<()> {
    let mut closing = std::collections::BTreeSet::new();
    let mut finished = std::collections::BTreeSet::new();
    while finished.len() < count {
        let message = match prefix.pop_front() {
            Some(message) => message,
            None => messages
                .recv()
                .await
                .ok_or_else(|| Error::State("final records absent".into()))?,
        };
        match message {
            JobMessage::Observe {
                session_id,
                state,
                evidence,
                reply,
                ..
            } => {
                assert_eq!(state, SessionState::Disconnecting);
                assert_eq!(evidence, Evidence::Closing);
                assert!(closing.insert(session_id));
                let _ = reply.send(Ok(()));
            }
            JobMessage::Finish {
                session_id,
                cleanup,
                reply,
                ..
            } => {
                assert!(closing.contains(&session_id));
                let expected_failure = failed && session_id == correlation(2)?.session_id;
                assert!(matches!(
                    (cleanup, expected_failure),
                    (Cleanup::Verified, false) | (Cleanup::Unconfirmed, true)
                ));
                assert!(finished.insert(session_id));
                let _ = reply.send(Ok(()));
            }
            _ => {
                return Err(Error::State(
                    "unexpected record after original preparation ACK".into(),
                ));
            }
        }
    }
    Ok(())
}

async fn matrix(stage: Stage, fail_close: bool) -> Result<()> {
    tokio::time::timeout(Duration::from_secs(10), async {
        let (facts, mut messages) = mpsc::channel(16);
        let (stop, stopping) = watch::channel(false);
        let (first, peer, first_invalidated) = fixture_export(1, facts.clone(), stopping.clone(), false)?;
        let mut peers = vec![peer]; let mut exports = vec![first.clone()];
        let mut invalidations = vec![first_invalidated];
        let second = if matches!(stage, Stage::Resource | Stage::Available) {
            let (export, peer, invalidated) = fixture_export(2, facts.clone(), stopping.clone(), fail_close)?;
            peers.push(peer); exports.push(export.clone()); invalidations.push(invalidated); Some(export)
        } else { None };
        let context = RunContext::managed(JobContext {
            instance_id: correlation(1)?.instance_id,
            job_id: JobId::new("00000000-0000-0000-0003-000000000001")?,
            helper_socket: "/no-ordinary-user-helper-bypass.sock".into(), stop: stopping.clone(), facts: facts.clone(),
        });
        let (address, listener_address) = oneshot::channel();
        let (dropped, listener_dropped) = oneshot::channel();
        let task = tokio::spawn(async move {
            let mut preparation = Preparation::default();
            preparation.register(first.clone(), stopping.clone());
            let mut outcome = success();
            if let Err(result) = recorded(first, 1, &stopping).await { merge(&mut outcome, result, true); }
            let mut listener = None;
            if let Some(second) = second {
                preparation.register(second.clone(), stopping.clone());
                if let Err(result) = recorded(second.clone(), 2, &stopping).await { merge(&mut outcome, result, true); }
                if stage == Stage::Available {
                    match TcpListener::bind("127.0.0.1:0").await {
                        Ok(bound) => {
                            if let Ok(local) = bound.local_addr() { let _ = address.send(local); }
                            listener = Some(bound);
                            if let Err(result) = record_available(&second, stopping.clone()).await { merge(&mut outcome, result, true); }
                        },
                        Err(error) => outcome.operation = Err(error.into()),
                    }
                }
            } else {
                // This calls the real preparation path. The held Begin or
                // Attempt is rejected before any root-trusted helper request.
                let key = DeviceKey::native(&format!("usb-{}", "2".repeat(64)))?;
                if let Err(result) = prepare(&key, &context, &mut preparation).await { merge(&mut outcome, result, true); }
            }
            drop(listener); let _ = dropped.send(());
            merge(&mut outcome, close_all(&mut preparation).await, true);
            Ok::<_, Error>(outcome)
        });
        let held = loop {
            match messages.recv().await.ok_or_else(|| Error::State("preparation gate absent".into()))? {
                JobMessage::Resource { correlation: c, reply, .. } => {
                    if stage == Stage::Resource && c.session_id == correlation(2)?.session_id { break Held::Unit(reply); }
                    let _ = reply.send(Ok(()));
                },
                JobMessage::Begin { reply, .. } => {
                    if stage == Stage::Begin { break Held::Begin(reply); }
                    let value = SessionContext { health: HealthReporter::default(), id: correlation(2)?.session_id, stop: stop.subscribe(), facts: facts.clone() };
                    let _ = reply.send(Ok(value));
                },
                JobMessage::Attempt { reply, .. } => {
                    assert!(stage == Stage::Attempt); break Held::Attempt(reply);
                },
                JobMessage::Observe { evidence, reply, .. } => {
                    if stage == Stage::Available && evidence == Evidence::ExportReady { break Held::Unit(reply); }
                    assert_eq!(evidence, Evidence::Intent); let _ = reply.send(Ok(()));
                },
                _ => return Err(Error::State("unexpected preparation prefix".into())),
            }
        };
        let mut held = Some(held);
        stop.send_replace(true);
        let mut observed = vec![Vec::new(); peers.len()];
        let released_while_held = matches!(tokio::time::timeout(Duration::from_secs(2), read_closes(&mut peers, &mut observed)).await, Ok(Ok(())));
        // On a failing implementation still open the original record gate and
        // collect every software owner, so the negative does not hang the suite.
        if !released_while_held { held.take().ok_or_else(|| Error::State("record gate absent".into()))?.reject()?; read_closes(&mut peers, &mut observed).await?; }
        for peer in &mut peers { peer.write_all(b"ok").await?; }
        for export in &exports {
            let guard = export.use_lock.lock().await;
            assert_eq!(export.failed.load(Ordering::Acquire), fail_close && export.key == exports.last().ok_or_else(|| Error::State("fixture export absent".into()))?.key);
            drop(guard);
        }
        let mut prefix = std::collections::VecDeque::new();
        if released_while_held {
            assert!(!task.is_finished()); assert!(!held.as_ref().ok_or_else(|| Error::State("record gate absent".into()))?.closed());
            while let Ok(message) = messages.try_recv() {
                match &message {
                    JobMessage::Observe { session_id, .. } | JobMessage::Finish { session_id, .. } => {
                        assert_ne!(*session_id, correlation(2)?.session_id, "Closing/Finish overtook the original same-session preparation ACK");
                    },
                    _ => return Err(Error::State("unexpected record before original ACK release".into())),
                }
                prefix.push_back(message);
            }
            if stage == Stage::Available {
                listener_dropped.await.map_err(|_| Error::State("listener not dropped while Available gate held".into()))?;
                let address = listener_address.await.map_err(|_| Error::State("actual bound address absent".into()))?;
                assert!(TcpStream::connect(address).await.is_err());
            }
            held.take().ok_or_else(|| Error::State("record gate absent".into()))?.reject()?;
        }
        // The rejected later Begin never created a new session; rejected
        // Attempt did, and its no-helper Finish precedes the old export close.
        if stage == Stage::Attempt {
            let message = messages.recv().await.ok_or_else(|| Error::State("unacquired session Finish absent".into()))?;
            let JobMessage::Finish { session_id, cleanup, reply, .. } = message else { return Err(Error::State("later Attempt terminal order".into())); };
            assert_eq!(session_id, correlation(2)?.session_id); assert!(matches!(cleanup, Cleanup::NotStarted)); let _ = reply.send(Ok(()));
        }
        finalize_messages(&mut messages, exports.len(), fail_close, prefix).await?;
        let outcome = task.await.map_err(|_| Error::State("preparation owner failed".into()))??;
        assert!(outcome.operation.is_ok()); assert_eq!(outcome.cleanup.is_err(), fail_close);
        assert!(matches!(outcome.recording, Err(Error::State(ref text)) if text.contains("fixture original preparation ACK rejected")));
        drop(invalidations);
        assert!(released_while_held, "received sibling owners waited for a preparation ACK before actual close");
        Ok::<_, Error>(())
    }).await.map_err(|_| Error::Timeout("Native preparation record/owner fixture".into()))?
}

#[tokio::test]
async fn received_collection_stops_two_exports_during_resource_ack() -> Result<()> {
    matrix(Stage::Resource, false).await
}
#[tokio::test]
async fn later_begin_and_attempt_ack_stalls_release_the_prepared_sibling() -> Result<()> {
    matrix(Stage::Begin, false).await?;
    matrix(Stage::Attempt, false).await
}
#[tokio::test]
async fn available_ack_stall_drops_listener_and_closes_both_exports() -> Result<()> {
    matrix(Stage::Available, false).await
}
#[tokio::test]
async fn cleanup_and_late_resource_errors_remain_distinct_and_ordered() -> Result<()> {
    matrix(Stage::Resource, true).await
}

#[tokio::test]
async fn simultaneous_stop_retains_unpolled_resource_until_actual_close() -> Result<()> {
    tokio::time::timeout(Duration::from_secs(5), async {
        let (facts, mut messages) = mpsc::channel(8);
        let (_stop, receiver) = watch::channel(true);
        let (export, mut peer, _invalidation) = fixture_export(1, facts, receiver.clone(), false)?;
        let observed_export = export.clone();
        let task = tokio::spawn(async move {
            let mut preparation = Preparation::default();
            preparation.register(export.clone(), receiver.clone());
            let mut outcome = match recorded(export, 1, &receiver).await {
                Err(outcome) => outcome,
                Ok(_) => {
                    return Err(Error::State(
                        "sticky stop published a received export".into(),
                    ));
                }
            };
            merge(&mut outcome, close_all(&mut preparation).await, true);
            Ok::<_, Error>(outcome)
        });
        let mut command = [0; 5];
        peer.read_exact(&mut command).await?;
        assert_eq!(&command, b"close");
        assert!(
            messages.try_recv().is_err(),
            "a later record overtook the stopped unpolled Resource"
        );
        peer.write_all(b"ok").await?;
        let guard = observed_export.use_lock.lock().await;
        drop(guard);
        let resource = messages
            .recv()
            .await
            .ok_or_else(|| Error::State("retained Resource never polled".into()))?;
        let JobMessage::Resource { reply, .. } = resource else {
            return Err(Error::State(
                "Resource was replaced by a later phase".into(),
            ));
        };
        reply
            .send(Err(Error::State(
                "fixture original preparation ACK rejected".into(),
            )))
            .map_err(|_| Error::State("original stopped Resource receiver lost".into()))?;
        finalize_messages(&mut messages, 1, false, std::collections::VecDeque::new()).await?;
        let outcome = task
            .await
            .map_err(|_| Error::State("sticky received owner failed".into()))??;
        assert!(outcome.operation.is_ok());
        assert!(outcome.cleanup.is_ok());
        assert!(matches!(outcome.recording, Err(Error::State(_))));
        Ok::<_, Error>(())
    })
    .await
    .map_err(|_| Error::Timeout("Native simultaneous preparation stop fixture".into()))?
}

#[tokio::test]
async fn received_identity_failure_survives_verified_close_in_session_finish() -> Result<()> {
    tokio::time::timeout(Duration::from_secs(5), async {
        let (facts, mut messages) = mpsc::channel(8);
        let (_stop, receiver) = watch::channel(false);
        let (export, mut peer, _invalidation) = fixture_export(1, facts, receiver.clone(), false)?;
        let wrong_key = DeviceKey::native(&format!("usb-{}", "b".repeat(64)))?;
        let task = tokio::spawn(async move {
            let mut preparation = Preparation::default();
            preparation.register(export.clone(), receiver.clone());
            let mut outcome = match finish_received_export(
                export, wrong_key, "00000000-0000-0000-0002-000000000001".into(),
                Some(correlation(1)?), &receiver,
            ).await {
                Err(outcome) => outcome,
                Ok(_) => return Err(Error::State("mismatched received identity was published".into())),
            };
            merge(&mut outcome, close_all(&mut preparation).await, true);
            Ok::<_, Error>(outcome)
        });
        let resource = messages.recv().await.ok_or_else(|| Error::State("identity fixture Resource absent".into()))?;
        let JobMessage::Resource { reply, .. } = resource else { return Err(Error::State("identity fixture Resource order".into())); };
        let _ = reply.send(Ok(()));
        let mut close = [0; 5]; peer.read_exact(&mut close).await?;
        assert_eq!(&close, b"close"); peer.write_all(b"ok").await?;
        let closing = messages.recv().await.ok_or_else(|| Error::State("identity fixture Closing absent".into()))?;
        let JobMessage::Observe { state, evidence, reply, .. } = closing else { return Err(Error::State("identity fixture Closing order".into())); };
        assert_eq!(state, SessionState::Disconnecting); assert_eq!(evidence, Evidence::Closing);
        let _ = reply.send(Ok(()));
        let finish = messages.recv().await.ok_or_else(|| Error::State("identity fixture Finish absent".into()))?;
        let JobMessage::Finish { operation_error, cleanup, reply, .. } = finish else { return Err(Error::State("identity fixture Finish order".into())); };
        assert_eq!(operation_error, Some(Failure::State));
        assert_eq!(cleanup, Cleanup::Verified);
        let _ = reply.send(Ok(()));
        let outcome = task.await.map_err(|_| Error::State("identity fixture owner failed".into()))??;
        assert!(matches!(outcome.operation, Err(Error::Protocol(ref text)) if text == "Native export identity differs"));
        assert!(outcome.cleanup.is_ok()); assert!(outcome.recording.is_ok());
        Ok::<_, Error>(())
    }).await.map_err(|_| Error::Timeout("Native received identity/finalize fixture".into()))?
}

#[tokio::test]
async fn known_identity_failure_is_not_erased_by_simultaneous_stop() -> Result<()> {
    tokio::time::timeout(Duration::from_secs(5), async {
        let (facts, mut messages) = mpsc::channel(8);
        let (_stop, receiver) = watch::channel(true);
        let (export, mut peer, _invalidation) = fixture_export(1, facts, receiver.clone(), false)?;
        let task = tokio::spawn(async move {
            let mut preparation = Preparation::default(); preparation.register(export.clone(), receiver.clone());
            let wrong = DeviceKey::native(&format!("usb-{}", "b".repeat(64)))?;
            let mut outcome = match finish_received_export(export, wrong,
                "00000000-0000-0000-0002-000000000001".into(), Some(correlation(1)?), &receiver).await {
                Err(outcome) => outcome, Ok(_) => return Err(Error::State("stopped mismatched owner was published".into())),
            };
            merge(&mut outcome, close_all(&mut preparation).await, true); Ok::<_, Error>(outcome)
        });
        let mut close = [0; 5]; peer.read_exact(&mut close).await?; assert_eq!(&close, b"close"); peer.write_all(b"ok").await?;
        let resource = messages.recv().await.ok_or_else(|| Error::State("stopped identity Resource absent".into()))?;
        let JobMessage::Resource { reply, .. } = resource else { return Err(Error::State("stopped identity Resource order".into())); }; let _ = reply.send(Ok(()));
        let closing = messages.recv().await.ok_or_else(|| Error::State("stopped identity Closing absent".into()))?;
        let JobMessage::Observe { evidence, reply, .. } = closing else { return Err(Error::State("stopped identity Closing order".into())); }; assert_eq!(evidence, Evidence::Closing); let _ = reply.send(Ok(()));
        let finish = messages.recv().await.ok_or_else(|| Error::State("stopped identity Finish absent".into()))?;
        let JobMessage::Finish { operation_error, cleanup, reply, .. } = finish else { return Err(Error::State("stopped identity Finish order".into())); }; assert_eq!(cleanup, Cleanup::Verified); let _ = reply.send(Ok(()));
        let outcome = task.await.map_err(|_| Error::State("stopped identity task failed".into()))??;
        assert!(outcome.cleanup.is_ok()); assert!(outcome.recording.is_ok());
        assert!(matches!(outcome.operation, Err(Error::Protocol(ref text)) if text == "Native export identity differs"), "known identity operation failure was erased by stop");
        assert_eq!(operation_error, Some(Failure::State), "known identity operation failure was erased from Finish");
        Ok::<_, Error>(())
    }).await.map_err(|_| Error::Timeout("Native stopped known identity fixture".into()))?
}

async fn local_cancelled_received(stage: Stage) -> Result<()> {
    tokio::time::timeout(Duration::from_secs(10), async {
        let (facts, mut messages) = mpsc::channel(16);
        let (job_stop, job) = watch::channel(false);
        let (_sibling_stop, sibling_local) = watch::channel(false);
        let (target_stop, target_local) = watch::channel(false);
        let (sibling, mut sibling_peer, _first_invalidation) = fixture_export(1, facts.clone(), sibling_local, false)?;
        let (target, mut target_peer, _second_invalidation) = fixture_export(2, facts, target_local, false)?;
        let observed_sibling = sibling.clone(); let observed_target = target.clone();
        let (decision, decided) = oneshot::channel(); let (resume, resumed) = oneshot::channel(); let (ready, prepared) = oneshot::channel();
        let task = tokio::spawn(async move {
            let mut preparation = Preparation::default(); preparation.register(sibling.clone(), job.clone());
            let mut outcome = success();
            if let Err(result) = recorded(sibling.clone(), 1, &job).await { merge(&mut outcome, result, true); }
            preparation.register(target.clone(), job.clone());
            let mut listener = None;
            let local = if stage == Stage::Resource {
                match recorded(target, 2, &job).await { Ok(None) => true, Ok(Some(_)) => false, Err(result) => { merge(&mut outcome, result, true); false } }
            } else {
                if let Err(result) = recorded(target.clone(), 2, &job).await { merge(&mut outcome, result, true); }
                let bound = TcpListener::bind("127.0.0.1:0").await?; listener = Some(bound);
                match record_available(&target, job.clone()).await { Ok(()) => true, Err(result) => { merge(&mut outcome, result, true); false } }
            };
            if !local { preparation.request_stop(); }
            let _ = decision.send(local);
            if local {
                resumed.await.map_err(|_| Error::State("local fixture resume lost".into()))?;
                if let Err(result) = record_available(&sibling, job.clone()).await { merge(&mut outcome, result, true); }
                if listener.is_none() { listener = Some(TcpListener::bind("127.0.0.1:0").await?); }
                let bound = listener.as_ref().ok_or_else(|| Error::State("safe sibling listener absent".into()))?.local_addr()?;
                let _ = ready.send(bound);
                let mut stopped_job = job; stopped(&mut stopped_job).await;
            }
            drop(listener); merge(&mut outcome, close_all(&mut preparation).await, true); Ok::<_, Error>(outcome)
        });
        let held = loop {
            match messages.recv().await.ok_or_else(|| Error::State("local target gate absent".into()))? {
                JobMessage::Resource { correlation: c, reply, .. } => {
                    if stage == Stage::Resource && c.session_id == correlation(2)?.session_id { break reply; }
                    let _ = reply.send(Ok(()));
                },
                JobMessage::Observe { evidence, reply, .. } if stage == Stage::Available && evidence == Evidence::ExportReady => break reply,
                _ => return Err(Error::State("local cancellation prefix order".into())),
            }
        };
        target_stop.send_replace(true);
        let mut close = [0; 5]; target_peer.read_exact(&mut close).await?; assert_eq!(&close, b"close"); target_peer.write_all(b"ok").await?;
        let actual = observed_target.use_lock.lock().await; drop(actual);
        let local = decided.await.map_err(|_| Error::State("local cancellation decision absent".into()))?;
        let sibling_retained = !stop_requested(&observed_sibling.stop.subscribe()) && observed_sibling.accepting();
        let mut probe = [0; 1]; let sibling_had_no_close = matches!(sibling_peer.try_read(&mut probe), Err(error) if error.kind() == std::io::ErrorKind::WouldBlock);
        assert!(!held.is_closed()); let _ = held.send(Ok(()));
        if local {
            let _ = resume.send(());
            let available = messages.recv().await.ok_or_else(|| Error::State("safe sibling Available absent".into()))?;
            let JobMessage::Observe { session_id, evidence, reply, .. } = available else { return Err(Error::State("safe sibling ready record order".into())); };
            assert_eq!(session_id, correlation(1)?.session_id); assert_eq!(evidence, Evidence::ExportReady); let _ = reply.send(Ok(()));
            let bound = prepared.await.map_err(|_| Error::State("safe sibling listener did not continue".into()))?;
            assert!(TcpStream::connect(bound).await.is_ok());
            assert!(observed_sibling.accepting()); assert!(!observed_target.accepting());
            job_stop.send_replace(true);
            sibling_peer.read_exact(&mut close).await?; assert_eq!(&close, b"close");
        } else {
            // Still drain the old failing branch's global sibling close.
            let already_read = if sibling_had_no_close { 0 } else { 1 };
            if already_read == 1 { close[0] = probe[0]; }
            sibling_peer.read_exact(&mut close[already_read..]).await?; assert_eq!(&close, b"close");
        }
        sibling_peer.write_all(b"ok").await?;
        finalize_messages(&mut messages, 2, false, std::collections::VecDeque::new()).await?;
        let outcome = task.await.map_err(|_| Error::State("local cancellation owner failed".into()))??;
        assert!(outcome.operation.is_ok()); assert!(outcome.cleanup.is_ok()); assert!(outcome.recording.is_ok());
        assert!(local && sibling_retained && sibling_had_no_close, "per-export stop escalated to a safe sibling/global preparation stop");
        Ok::<_, Error>(())
    }).await.map_err(|_| Error::Timeout("Native local received cancellation fixture".into()))?
}
#[tokio::test]
async fn local_resource_stop_keeps_the_safe_sibling_owned_and_available() -> Result<()> {
    local_cancelled_received(Stage::Resource).await
}
#[tokio::test]
async fn local_available_stop_keeps_the_safe_sibling_owned_and_available() -> Result<()> {
    local_cancelled_received(Stage::Available).await
}

#[tokio::test]
async fn local_stop_before_helper_is_a_local_skip_without_new_attempt() -> Result<()> {
    tokio::time::timeout(Duration::from_secs(5), async {
        let (facts, mut messages) = mpsc::channel(8);
        let (_job_stop, job) = watch::channel(false);
        let (_local_stop, local) = watch::channel(true);
        let context = RunContext::managed(JobContext {
            instance_id: correlation(1)?.instance_id,
            job_id: JobId::new("00000000-0000-0000-0003-000000000001")?,
            helper_socket: "/no-root-helper-in-local-skip-fixture.sock".into(),
            stop: job,
            facts: facts.clone(),
        });
        let task = tokio::spawn(async move {
            let mut preparation = Preparation::default();
            let key = DeviceKey::native(&format!("usb-{}", "2".repeat(64)))?;
            Ok::<_, Error>(prepare(&key, &context, &mut preparation).await)
        });
        let begin = messages
            .recv()
            .await
            .ok_or_else(|| Error::State("local Begin absent".into()))?;
        let JobMessage::Begin { reply, .. } = begin else {
            return Err(Error::State("local Begin order".into()));
        };
        let _ = reply.send(Ok(SessionContext {
            health: HealthReporter::default(),
            id: correlation(2)?.session_id,
            stop: local,
            facts,
        }));
        let finish = messages
            .recv()
            .await
            .ok_or_else(|| Error::State("no-helper local Finish absent".into()))?;
        let JobMessage::Finish { cleanup, reply, .. } = finish else {
            return Err(Error::State(
                "local cancellation requested new Intent/Attempt/helper work".into(),
            ));
        };
        assert_eq!(cleanup, Cleanup::NotStarted);
        let _ = reply.send(Ok(()));
        let result = task
            .await
            .map_err(|_| Error::State("no-helper local task failed".into()))??;
        assert!(
            matches!(result, Ok(None)),
            "no-helper per-export cancellation was returned as a global interruption"
        );
        assert!(messages.try_recv().is_err());
        Ok::<_, Error>(())
    })
    .await
    .map_err(|_| Error::Timeout("Native pre-helper local cancellation fixture".into()))?
}

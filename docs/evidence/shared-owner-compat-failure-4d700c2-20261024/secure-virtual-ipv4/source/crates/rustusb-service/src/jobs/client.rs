use super::{
    connect, discover,
    io::{self, Counters},
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
use std::{sync::Arc, time::Duration};

enum StageError {
    Operation(Error),
    Recording(Error),
}
impl From<Error> for StageError {
    fn from(error: Error) -> Self {
        Self::Operation(error)
    }
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
    let operation = async {
        session.observe(SessionState::Connecting, Evidence::Intent, None).await.map_err(StageError::Recording)?;
        let selection = tokio::time::timeout(timeout, async {
            let selected = discover(&config, timeout).await?.into_iter()
                .find(|entry| entry.device_key == config.device)
                .ok_or_else(|| Error::Permission("configured remote device unavailable or unauthorized".into()))?;
            rustusb_protocol::secure::import_device(connect(&config).await?, &selected).await
        });
        let (network, remote) = tokio::select! {
            biased;
            _ = stopped(&mut job_stop) => return Ok(()),
            _ = stopped(&mut session_stop) => return Ok(()),
            result = selection => result.map_err(|_| Error::Timeout("secure selection/import including DNS/TCP/TLS".into()))??,
        };
        let correlation = session.attempt(BrokerResourceKind::NativeImport).await.map_err(StageError::Recording)?;
        // A managed reservation is already durable even before its RPC. A
        // foreground None has no WAL and remains NotStarted until we send.
        attempted = correlation.is_some();
        if *context.stop.borrow() || *session.stop.borrow() { return Ok(()); }
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
        session.resource(correlation, owner.channel_id()).await.map_err(StageError::Recording)?;
        session.observe(SessionState::Connecting, Evidence::Prepared, None).await.map_err(StageError::Recording)?;
        if *context.stop.borrow() || *session.stop.borrow() { return Ok(()); }
        let kernel = owner.take_relay()?;
        let mut invalidated = owner.invalidated();
        copying = Some(tokio::spawn(relay(network, kernel, context.is_foreground(), counters.clone())));
        let operations = async {
            let receipt = owner.commit().await?;
            session.observe(SessionState::Connected, Evidence::LocalEnumeration, Some(counters.snapshot())).await.map_err(StageError::Recording)?;
            context.emit(Event::Connected { server_id: config.server_id.clone(), device_key: config.device.clone(), receipt: Box::new(receipt) }).await?;
            let task = copying.as_mut().ok_or_else(|| Error::State("secure relay owner absent".into()))?;
            let mut sample = tokio::time::interval(Duration::from_secs(1));
            sample.tick().await;
            loop {
                tokio::select! {
                    result = &mut *task => {
                        copy_finished = true;
                        return result.map_err(|_| Error::State("secure relay task failed".into()))?.map_err(StageError::Operation);
                    },
                    _ = sample.tick() => session.observe(SessionState::Connected, Evidence::LocalEnumeration, Some(counters.snapshot())).await.map_err(StageError::Recording)?,
                }
            }
        };
        tokio::pin!(operations);
        tokio::select! {
            biased;
            _ = stopped(&mut job_stop) => Ok(()),
            result = &mut operations => result,
            _ = stopped(&mut session_stop) => Ok(()),
            _ = stopped(&mut invalidated) => Err(Error::State("secure helper channel invalidated".into()).into()),
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
    let (operation, earlier_recording) = match operation {
        Ok(()) => (Ok(()), Ok(())),
        Err(StageError::Operation(error)) => (Err(error), Ok(())),
        Err(StageError::Recording(error)) => (Ok(()), Err(error)),
    };
    let closing = session.observe(
        SessionState::Disconnecting,
        Evidence::Closing,
        Some(counters.snapshot()),
    );
    let cleanup = async {
        match handle {
            Some(owner) => owner.close().await,
            None if attempted => Err(Error::State("import prepare outcome unconfirmed".into())),
            None => Ok(()),
        }
    };
    let (closing, cleanup) = tokio::join!(closing, cleanup);
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
        .or(closing.as_ref().err())
        .map(Failure::from_error);
    let recorded = session.finish(failure, outcome, counters.snapshot()).await;
    let recording = recording_results([earlier_recording, closing, recorded]);
    let final_event = (attempted && operation.is_ok() && cleanup.is_ok() && recording.is_ok())
        .then_some(Event::Disconnected);
    JobOutcome {
        operation,
        cleanup,
        recording,
        final_event,
    }
}

fn recording_results(results: [rustusb_core::Result<()>; 3]) -> rustusb_core::Result<()> {
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
    if foreground {
        // Keep draining the opposite half after the first EOF, just as the
        // original foreground command did. No managed metric is fabricated.
        let (mut network, mut kernel) = (network, kernel);
        tokio::io::copy_bidirectional(&mut network, &mut kernel).await?;
        Ok(())
    } else {
        io::relay(network, kernel, counters).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

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

use super::{
    combined, connect, discover,
    io::{self, Counters},
};
use crate::{
    context::{JobContext, stopped},
    model::{Cleanup, Evidence, Failure, SessionRole},
};
use rustusb_core::{Error, Result, SessionState, application::BrokerResourceKind};
use rustusb_daemon::{SecureImportTarget, prepare_native_import_tracked};
use rustusb_transport::config::LoadedClientConfig;
use std::{sync::Arc, time::Duration};

pub(super) async fn run(
    config: Arc<LoadedClientConfig>,
    timeout: Duration,
    mut context: JobContext,
) -> Result<()> {
    let session = context
        .begin_session(SessionRole::Import, config.device.clone(), None)
        .await?;
    let mut session_stop = session.stop.clone();
    let counters = Counters::new();
    let mut attempted = false;
    let mut handle = None;
    let mut copying = None;
    let mut copy_finished = false;
    let operation = async {
        session.observe(SessionState::Connecting, Evidence::Intent, None).await?;
        let selection = tokio::time::timeout(timeout, async {
            let selected = discover(&config, timeout).await?.into_iter()
                .find(|entry| entry.device_key == config.device)
                .ok_or_else(|| Error::Permission("configured remote device unavailable or unauthorized".into()))?;
            rustusb_protocol::secure::import_device(connect(&config).await?, &selected).await
        });
        let (network, remote) = tokio::select! {
            biased;
            _ = stopped(&mut context.stop) => return Ok(()),
            _ = stopped(&mut session_stop) => return Ok(()),
            result = selection => result.map_err(|_| Error::Timeout("secure selection/import including DNS/TCP/TLS".into()))??,
        };
        let correlation = session.attempt(BrokerResourceKind::NativeImport).await?;
        attempted = true;
        if *context.stop.borrow() || *session.stop.borrow() { return Ok(()); }
        // Never cancel an accepted helper prepare. Receive the actual handle,
        // record its ID, and then close it when a stop arrived meanwhile.
        handle = Some(prepare_native_import_tracked(&context.helper_socket, SecureImportTarget {
            server_id: config.server_id.clone(), device_key: config.device.clone(), remote,
        }, correlation.clone()).await?);
        let owner = handle.as_mut().ok_or_else(|| Error::State("import owner absent".into()))?;
        session.resource(correlation, owner.channel_id()).await?;
        session.observe(SessionState::Connecting, Evidence::Prepared, None).await?;
        if *context.stop.borrow() || *session.stop.borrow() { return Ok(()); }
        let kernel = owner.take_relay()?;
        let mut invalidated = owner.invalidated();
        copying = Some(tokio::spawn(io::relay(network, kernel, counters.clone())));
        let operations = async {
            owner.commit().await?;
            session.observe(SessionState::Connected, Evidence::LocalEnumeration, Some(counters.snapshot())).await?;
            let task = copying.as_mut().ok_or_else(|| Error::State("secure relay owner absent".into()))?;
            let mut sample = tokio::time::interval(Duration::from_secs(1));
            sample.tick().await;
            loop {
                tokio::select! {
                    result = &mut *task => {
                        copy_finished = true;
                        return result.map_err(|_| Error::State("secure relay task failed".into()))?;
                    },
                    _ = sample.tick() => session.observe(SessionState::Connected, Evidence::LocalEnumeration, Some(counters.snapshot())).await?,
                }
            }
        };
        tokio::pin!(operations);
        tokio::select! {
            biased;
            _ = stopped(&mut context.stop) => Ok(()),
            result = &mut operations => result,
            _ = stopped(&mut session_stop) => Ok(()),
            _ = stopped(&mut invalidated) => Err(Error::State("secure helper channel invalidated".into())),
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
        .or(closing.as_ref().err())
        .map(Failure::from_error);
    let recorded = session.finish(failure, outcome, counters.snapshot()).await;
    combined(operation.and(closing), cleanup, recorded)
}

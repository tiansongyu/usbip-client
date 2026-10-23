//! Native TLS owners use only helper-held kernel endpoints; no plain listener.

use super::{
    combined,
    io::{self, Counters},
};
use crate::{
    context::{JobContext, SessionContext, stopped},
    model::{Cleanup, Evidence, Failure, Metrics, SessionRole},
};
use rustusb_core::{DeviceKey, Error, Result, SessionState, application::BrokerResourceKind};
use rustusb_daemon::{NativeExportHandle, prepare_native_export_tracked};
use rustusb_protocol::{
    RemoteDevice,
    secure::{self, DeviceMapping, SecureOperation},
    server::{self, Management},
};
use rustusb_transport::{AuthenticatedStream, TlsServer, VerifiedPeer, config::LoadedServerConfig};
use std::{
    collections::HashSet,
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    time::Duration,
};
use tokio::{
    io::{AsyncWrite, AsyncWriteExt},
    net::{TcpListener, TcpStream},
    sync::{Mutex, Semaphore, watch},
    task::JoinSet,
};

const FRAME_DEADLINE: Duration = Duration::from_secs(5);

struct Export {
    key: DeviceKey,
    published: RemoteDevice,
    handle: Mutex<Option<NativeExportHandle>>,
    session: SessionContext,
    invalidated: watch::Receiver<bool>,
    stop: watch::Sender<bool>,
    use_lock: Mutex<()>,
    reserved: AtomicBool,
    failed: AtomicBool,
}
impl Export {
    fn accepting(&self) -> bool {
        !*self.stop.borrow()
            && !*self.invalidated.borrow()
            && !self.reserved.load(Ordering::Acquire)
            && !self.failed.load(Ordering::Acquire)
    }
    async fn close(&self) -> Result<()> {
        // Disable catalog/IMPORT before stopping this export's sole stub owner.
        self.stop.send_replace(true);
        let _use = self.use_lock.lock().await;
        let handle = self.handle.lock().await.take();
        let closing = self
            .session
            .observe(SessionState::Disconnecting, Evidence::Closing, None);
        let cleanup = async {
            match handle {
                Some(handle) => handle.close().await,
                None => Err(Error::State("Native export close repeated".into())),
            }
        };
        let (observed, cleanup) = tokio::join!(closing, cleanup);
        let failure = observed.as_ref().err().map(Failure::from_error);
        let recorded = self
            .session
            .finish(
                failure,
                if cleanup.is_ok() {
                    Cleanup::Verified
                } else {
                    Cleanup::Unconfirmed
                },
                Metrics::default(),
            )
            .await;
        combined(observed, cleanup, recorded)
    }
}

struct Reservation(Arc<Export>);
impl Drop for Reservation {
    fn drop(&mut self) {
        self.0.reserved.store(false, Ordering::Release);
    }
}

async fn prepare(key: &DeviceKey, context: &JobContext) -> Result<Arc<Export>> {
    let session = context
        .begin_session(SessionRole::Export, key.clone(), None)
        .await?;
    let mut attempted = false;
    let mut owner = None;
    let operation = async {
        session
            .observe(SessionState::Exporting, Evidence::Intent, None)
            .await?;
        let correlation = session.attempt(BrokerResourceKind::NativeExport).await?;
        attempted = true;
        if *context.stop.borrow() || *session.stop.borrow() {
            return Err(Error::State("Native export stopped before prepare".into()));
        }
        let id = key
            .fingerprint()
            .ok_or_else(|| Error::InvalidInput("Native device fingerprint required".into()))?;
        owner = Some(
            prepare_native_export_tracked(
                &context.helper_socket,
                id.to_owned(),
                correlation.clone(),
            )
            .await?,
        );
        let handle = owner
            .as_ref()
            .ok_or_else(|| Error::State("Native export owner absent".into()))?;
        session.resource(correlation, handle.lease_id()).await?;
        if handle.device_key() != key {
            return Err(Error::Protocol("Native export identity differs".into()));
        }
        Ok(())
    }
    .await;
    if let Err(error) = operation {
        let cleanup = match owner {
            Some(handle) => handle.close().await,
            None if attempted => Err(Error::State("Native export prepare unconfirmed".into())),
            None => Ok(()),
        };
        let recorded = session
            .finish(
                Some(Failure::from_error(&error)),
                if cleanup.is_err() {
                    Cleanup::Unconfirmed
                } else if attempted {
                    Cleanup::Verified
                } else {
                    Cleanup::NotStarted
                },
                Metrics::default(),
            )
            .await;
        return combined(Err(error), cleanup, recorded)
            .and(Err(Error::State("Native export preparation failed".into())));
    }
    let handle = owner.ok_or_else(|| Error::State("Native export owner absent".into()))?;
    let published = handle.published_device().clone();
    let invalidated = handle.invalidated();
    let (stop, _) = watch::channel(false);
    Ok(Arc::new(Export {
        key: key.clone(),
        published,
        handle: Mutex::new(Some(handle)),
        session,
        invalidated,
        stop,
        use_lock: Mutex::new(()),
        reserved: AtomicBool::new(false),
        failed: AtomicBool::new(false),
    }))
}

enum Task {
    Export(Result<()>),
    Connection(Result<()>),
}

pub(super) async fn run(config: Arc<LoadedServerConfig>, mut context: JobContext) -> Result<()> {
    let mut exports = Vec::new();
    let prepared = async {
        let mut buses = HashSet::new();
        for key in &config.devices {
            if *context.stop.borrow() {
                break;
            }
            let export = prepare(key, &context).await?;
            let distinct = buses.insert(export.published.bus_id.clone());
            exports.push(export);
            if !distinct {
                return Err(Error::Protocol("duplicate Native publication bus".into()));
            }
        }
        server::devlist_reply(
            &exports
                .iter()
                .map(|export| export.published.clone())
                .collect::<Vec<_>>(),
        )?;
        Ok(())
    }
    .await;
    if prepared.is_err() || *context.stop.borrow() {
        let cleanup = close_all(&exports).await;
        return combined(prepared, cleanup, Ok(()));
    }
    // Binding may acquire a listener, so receive it before processing stop.
    let listener = match TcpListener::bind(config.listen).await {
        Ok(listener) => listener,
        Err(error) => return combined(Err(error.into()), close_all(&exports).await, Ok(())),
    };
    let ready = async {
        for export in &exports {
            export
                .session
                .observe(SessionState::Available, Evidence::ExportReady, None)
                .await?;
        }
        Ok::<_, Error>(())
    }
    .await;
    if let Err(error) = ready {
        drop(listener);
        return combined(Err(error), close_all(&exports).await, Ok(()));
    }
    let mut tasks = JoinSet::new();
    let (stop_all, stop) = watch::channel(false);
    for export in &exports {
        let export = export.clone();
        let mut job_stop = stop.clone();
        tasks.spawn(async move {
            let mut local_stop = export.session.stop.clone();
            let mut loss = export.invalidated.clone();
            let lost = tokio::select! {
                biased;
                _ = stopped(&mut job_stop) => false,
                _ = stopped(&mut local_stop) => false,
                _ = stopped(&mut loss) => true,
            };
            let result = export.close().await;
            Task::Export(if lost && result.is_ok() {
                Err(Error::State("Native export invalidated".into()))
            } else {
                result
            })
        });
    }
    let budget = Arc::new(Semaphore::new(16));
    let operation = {
        loop {
            tokio::select! {
                biased;
                _ = stopped(&mut context.stop) => break Ok(()),
                result = tasks.join_next(), if !tasks.is_empty() => match result {
                    Some(Ok(Task::Export(Err(error)))) => break Err(error),
                    Some(Ok(Task::Connection(result))) => { if result.is_err() { tracing::debug!("managed Native connection ended with an operation error"); } },
                    Some(Ok(Task::Export(Ok(())))) => {},
                    Some(Err(_)) => break Err(Error::State("Native owned task failed".into())),
                    None => {},
                },
                accepted = listener.accept() => {
                    let (network, _) = match accepted { Ok(value) => value, Err(error) => break Err(error.into()) };
                    let Ok(permit) = budget.clone().try_acquire_owned() else { continue };
                    let config = config.clone();
                    let registry = exports.clone();
                    let context = context.clone();
                    let stop = stop.clone();
                    tasks.spawn(async move { let _permit = permit; Task::Connection(connection(network, config.tls.clone(), registry, context, stop).await) });
                }
            }
        }
    };
    drop(listener);
    stop_all.send_replace(true);
    let mut cleanup_error = None;
    while let Some(task) = tasks.join_next().await {
        match task {
            Ok(Task::Export(Err(error))) => {
                if cleanup_error.is_none() {
                    cleanup_error = Some(error);
                }
            }
            Ok(Task::Connection(_)) | Ok(Task::Export(Ok(()))) => {}
            Err(_) => {
                if cleanup_error.is_none() {
                    cleanup_error = Some(Error::State("Native task cleanup unconfirmed".into()));
                }
            }
        }
    }
    if exports
        .iter()
        .any(|export| export.failed.load(Ordering::Acquire))
        && cleanup_error.is_none()
    {
        cleanup_error = Some(Error::State("Native stub cleanup unconfirmed".into()));
    }
    combined(operation, cleanup_error.map_or(Ok(()), Err), Ok(()))
}

async fn close_all(exports: &[Arc<Export>]) -> Result<()> {
    let mut first = None;
    for export in exports {
        if let Err(error) = export.close().await
            && first.is_none()
        {
            first = Some(error);
        }
    }
    first.map_or(Ok(()), Err)
}

async fn write_frame(writer: &mut (impl AsyncWrite + Unpin), bytes: &[u8]) -> Result<()> {
    tokio::time::timeout(FRAME_DEADLINE, async {
        writer.write_all(bytes).await?;
        writer.flush().await
    })
    .await
    .map_err(|_| Error::Timeout("Native TLS management response".into()))??;
    Ok(())
}

async fn management(
    stream: &mut AuthenticatedStream<TcpStream>,
    exports: &[Arc<Export>],
    peer: &VerifiedPeer,
) -> Result<Option<Reservation>> {
    match (
        secure::read_prelude(stream).await?,
        server::read_management(stream).await?,
    ) {
        (SecureOperation::Catalog, Management::Devlist) => {
            let catalog: Vec<_> = exports
                .iter()
                .filter(|export| export.accepting() && peer.allows(&export.key))
                .collect();
            let mapping: Vec<_> = catalog
                .iter()
                .map(|export| DeviceMapping {
                    device_key: export.key.clone(),
                    bus_id: export.published.bus_id.clone(),
                })
                .collect();
            let devices: Vec<_> = catalog
                .iter()
                .map(|export| export.published.clone())
                .collect();
            secure::write_catalog_mapping(stream, &mapping).await?;
            write_frame(stream, &server::devlist_reply(&devices)?).await?;
            Ok(None)
        }
        (SecureOperation::Import { device_key }, Management::Import(bus_id)) => {
            let Some(export) = exports.iter().find(|export| {
                export.key == device_key
                    && export.published.bus_id == bus_id
                    && peer.allows(&export.key)
                    && !*export.stop.borrow()
                    && !*export.invalidated.borrow()
                    && !export.failed.load(Ordering::Acquire)
            }) else {
                write_frame(stream, &server::import_reply(4, None)?).await?;
                return Ok(None);
            };
            if export
                .reserved
                .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                .is_err()
            {
                write_frame(stream, &server::import_reply(2, None)?).await?;
                return Ok(None);
            }
            Ok(Some(Reservation(export.clone())))
        }
        _ => Err(Error::Protocol(
            "secure prelude and operation differ".into(),
        )),
    }
}

async fn connection(
    network: TcpStream,
    tls: TlsServer,
    exports: Vec<Arc<Export>>,
    context: JobContext,
    mut job_stop: watch::Receiver<bool>,
) -> Result<()> {
    network.set_nodelay(true)?;
    let mut stream = tokio::select! {
        biased;
        _ = stopped(&mut job_stop) => return Ok(()),
        result = tls.accept(network) => result.map_err(|_| Error::Permission("Native mutual TLS authentication failed".into()))?,
    };
    let peer = stream.peer().clone();
    let reservation = tokio::select! {
        biased;
        _ = stopped(&mut job_stop) => return Ok(()),
        result = management(&mut stream, &exports, &peer) => result?,
    };
    let Some(reservation) = reservation else {
        return Ok(());
    };
    let export = &reservation.0;
    let _use = export.use_lock.lock().await;
    if *job_stop.borrow() || *export.stop.borrow() {
        return Ok(());
    }
    // The actual TLS principal and exact authorized stable key are recorded
    // before a helper stub operation; no UI-supplied principal is accepted.
    let session = context
        .begin_session(
            SessionRole::Incoming,
            export.key.clone(),
            Some(peer.principal().to_owned()),
        )
        .await?;
    let mut local_stop = session.stop.clone();
    let mut export_stop = export.stop.subscribe();
    let counters = Counters::new();
    let mut attempted = false;
    let mut stub = None;
    let mut network = Some(stream);
    let operation = async {
        session.observe(SessionState::Connecting, Evidence::Intent, None).await?;
        let correlation = session.attempt(BrokerResourceKind::NativeStub).await?;
        attempted = true;
        if *job_stop.borrow() || *local_stop.borrow() || *export_stop.borrow() { return Ok(()); }
        let owner = export.handle.lock().await;
        let owner = owner.as_ref().ok_or_else(|| Error::State("Native export closed before stub".into()))?;
        stub = Some(owner.open_stub_tracked(correlation.clone()).await?);
        let stub = stub.as_mut().ok_or_else(|| Error::State("Native stub owner absent".into()))?;
        session.resource(correlation, stub.channel_id()).await?;
        session.observe(SessionState::Connecting, Evidence::Prepared, None).await?;
        if *job_stop.borrow() || *local_stop.borrow() || *export_stop.borrow() { return Ok(()); }
        let relay = stub.take_relay()?;
        // commit is received before a stop is processed; the helper actor
        // retains any real blocking operation even if its caller disappears.
        stub.commit().await?;
        if *job_stop.borrow() || *local_stop.borrow() || *export_stop.borrow() { return Ok(()); }
        let stream = network.as_mut().ok_or_else(|| Error::State("Native network owner absent".into()))?;
        write_frame(stream, &server::import_reply(0, Some(&export.published))?).await?;
        session.observe(SessionState::Connected, Evidence::ImportReplyFlushed, Some(counters.snapshot())).await?;
        let stream = network.take().ok_or_else(|| Error::State("Native network owner absent".into()))?;
        let mut loss = stub.invalidated();
        let mut export_loss = export.invalidated.clone();
        let forwarding = async {
            let copying = io::relay(stream, relay, counters.clone());
            tokio::pin!(copying);
            let mut sample = tokio::time::interval(Duration::from_secs(1));
            sample.tick().await;
            loop {
                tokio::select! {
                    result = &mut copying => return result,
                    _ = sample.tick() => session.observe(SessionState::Connected, Evidence::ImportReplyFlushed, Some(counters.snapshot())).await?,
                }
            }
        };
        tokio::select! {
            biased;
            _ = stopped(&mut job_stop) => Ok(()),
            _ = stopped(&mut local_stop) => Ok(()),
            _ = stopped(&mut export_stop) => Ok(()),
            _ = stopped(&mut loss) => Ok(()),
            _ = stopped(&mut export_loss) => Err(Error::State("Native export invalidated during relay".into())),
            result = forwarding => result,
        }
    }.await;
    drop(network);
    let closing = session.observe(
        SessionState::Disconnecting,
        Evidence::Closing,
        Some(counters.snapshot()),
    );
    let cleanup = async {
        match stub {
            Some(stub) => stub.close().await,
            None if attempted => Err(Error::State("Native stub prepare unconfirmed".into())),
            None => Ok(()),
        }
    };
    let (closing, cleanup) = tokio::join!(closing, cleanup);
    if cleanup.is_err() {
        export.failed.store(true, Ordering::Release);
    }
    let failure = operation
        .as_ref()
        .err()
        .or(closing.as_ref().err())
        .map(Failure::from_error);
    let recorded = session
        .finish(
            failure,
            if cleanup.is_err() {
                Cleanup::Unconfirmed
            } else if attempted {
                Cleanup::Verified
            } else {
                Cleanup::NotStarted
            },
            counters.snapshot(),
        )
        .await;
    combined(operation.and(closing), cleanup, recorded)
}

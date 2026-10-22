use super::{MAX_CONNECTIONS, MAX_FRAME_BYTES, Policy, Request, Response};
use rustusb_core::{Device, DeviceDiscovery, Error, ExportBackend, Result, VhciPort};
use rustusb_linux::SysfsDiscovery;
use rustusb_native::{NativeBackend, NativeSession};
use serde::Serialize;
use std::{
    collections::{HashMap, HashSet},
    fs::{self, File},
    io::Read,
    os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt},
    path::{Component, Path, PathBuf},
    sync::Arc,
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt, BufReader},
    net::{UnixListener, UnixStream},
    sync::{Mutex, Semaphore},
    task::JoinSet,
    time::timeout,
};

const READ_TIMEOUT: Duration = Duration::from_secs(5);
const WRITE_TIMEOUT: Duration = Duration::from_secs(5);
const QUEUE_TIMEOUT: Duration = Duration::from_secs(30);
const RESPONSE_TIMEOUT: Duration = Duration::from_secs(120);

#[derive(Default)]
struct ManagedState {
    ports: HashMap<VhciPort, NativeSession>,
    released_ports: HashSet<VhciPort>,
}

struct App {
    policy: Policy,
    backend: NativeBackend,
    // Serializes all mutations from this helper. It does not lock out an
    // administrator running usbip independently; identity checks still matter.
    state: Mutex<ManagedState>,
}

impl App {
    async fn handle(&self, uid: u32, req: Request) -> Result<serde_json::Value> {
        self.policy.authorize_uid(uid)?;
        if let Request::Status = req {
            let kernel = match self.backend.sessions().await {
                Ok(sessions) => serde_json::json!({ "available": true, "sessions": sessions }),
                Err(error) => {
                    serde_json::json!({ "available": false, "reason": Response::failure(error).error })
                }
            };
            return Ok(serde_json::json!({
                "helper": "running",
                "backend": "linux-native",
                "native_data_encryption": false,
                "external_trusted_network_confirmed": self.policy.trusted_network,
                "kernel": kernel,
                "port_ownership": "only ports created by this helper instance; unknown ports after restart require administrator reconciliation",
                "policy_reload": "restart required"
            }));
        }
        let mut state = timeout(QUEUE_TIMEOUT, self.state.lock())
            .await
            .map_err(|_| {
                Error::Timeout("helper operation queue; no new mutation started".into())
            })?;
        let export = matches!(&req, Request::Export { .. });
        match req {
            Request::Export { device_id } | Request::Unexport { device_id } => {
                self.policy.authorize_device(&device_id)?;
                let before = current_device(&device_id).await?;
                if before.occupied != Some(false) && before.exported {
                    return Err(Error::UnsafeDevice("exported device is occupied or its occupancy is unknown; release the client first".into()));
                }
                if export && !before.export_blockers.is_empty() {
                    return Err(Error::UnsafeDevice("current device is not eligible for safe export; inspect devices and the safety policy".into()));
                }
                // A second fresh scan rejects obvious removal/replacement. This
                // is not an atomic storage-exclusion mechanism; storage remains
                // blocked by discovery for Phase 1.
                let immediate = current_device(&device_id).await?;
                if !same_device_generation(&before, &immediate) {
                    return Err(Error::State(
                        "device changed during authorization; rescan and retry explicitly".into(),
                    ));
                }
                if export && !immediate.export_blockers.is_empty() {
                    return Err(Error::UnsafeDevice(
                        "device safety changed before export".into(),
                    ));
                }
                if immediate.exported && immediate.occupied != Some(false) {
                    return Err(Error::UnsafeDevice(
                        "device became occupied or its occupancy is unknown before the operation"
                            .into(),
                    ));
                }
                let operation = if export {
                    self.backend.export(immediate.bus_id.clone()).await
                } else {
                    self.backend.unexport(immediate.bus_id.clone()).await
                };
                // Reconcile even when the native command failed. Never report
                // a successful rollback or retry an uncertain operation.
                let after = current_device(&device_id).await;
                operation?;
                let after = after?;
                if !same_device_generation(&immediate, &after) || after.exported != export {
                    return Err(Error::State("native operation postcondition is uncertain; inspect kernel state before another change".into()));
                }
                Ok(
                    serde_json::json!({ "device_id": device_id, "bus_id": after.bus_id, "exported": after.exported }),
                )
            }
            Request::Attach { peer, bus_id } => {
                self.policy.authorize_peer(&peer)?;
                // The backend must verify a unique before/after port, remote
                // device ID, current USB identity and native endpoint record.
                let session = self.backend.attach_session(peer, bus_id).await?;
                let port = session.port;
                if state.ports.contains_key(&port) {
                    return Err(Error::State("new import used an already recorded port; administrator reconciliation required".into()));
                }
                state.released_ports.remove(&port);
                let data = serde_json::to_value(&session).map_err(json_error)?;
                state.ports.insert(port, session);
                Ok(data)
            }
            Request::Detach { port } => {
                if !state.ports.contains_key(&port) && !state.released_ports.contains(&port) {
                    return Err(Error::Permission("port is not owned by this helper instance; unknown or restart-surviving ports require administrator reconciliation".into()));
                }
                let current = self.backend.sessions().await?;
                let at_port = current.iter().find(|session| session.port == port);
                let Some(owned) = state.ports.get(&port) else {
                    if state.released_ports.contains(&port) && at_port.is_none() {
                        return Ok(serde_json::json!({ "port": port, "detached": true }));
                    }
                    return Err(Error::Permission("port is not owned by this helper instance; unknown or restart-surviving ports require administrator reconciliation".into()));
                };
                if let Some(actual) = at_port {
                    if !same_session(owned, actual) {
                        return Err(Error::State("port identity changed since import; refusing to detach a possibly unrelated session".into()));
                    }
                    self.backend.detach_session(owned).await?;
                }
                state.ports.remove(&port);
                state.released_ports.insert(port);
                Ok(serde_json::json!({ "port": port, "detached": true }))
            }
            Request::Sessions => {
                let current = self.backend.sessions().await?;
                let sessions = current
                    .into_iter()
                    .map(|session| {
                        let managed = state
                            .ports
                            .get(&session.port)
                            .is_some_and(|owned| same_session(owned, &session));
                        serde_json::json!({ "session": session, "managed": managed })
                    })
                    .collect::<Vec<_>>();
                Ok(serde_json::json!(sessions))
            }
            Request::Status => Err(Error::State("status dispatch invariant".into())),
        }
    }
}

fn same_device_generation(before: &Device, after: &Device) -> bool {
    before.id == after.id
        && before.bus_id == after.bus_id
        && before.device_address == after.device_address
        && before.topology == after.topology
        && before.vendor_id == after.vendor_id
        && before.product_id == after.product_id
}

fn same_session(before: &NativeSession, after: &NativeSession) -> bool {
    // Status may move to ERROR without transferring ownership. Stable kernel
    // identity must remain known; otherwise fail closed for administrator help.
    before.port == after.port
        && before.local_bus_id == after.local_bus_id
        && before.remote_device_id == after.remote_device_id
        && before.vendor_id == after.vendor_id
        && before.product_id == after.product_id
}

async fn current_device(id: &str) -> Result<Device> {
    let id = id.to_owned();
    tokio::task::spawn_blocking(move || {
        let devices = SysfsDiscovery::default().scan()?;
        let mut matching = devices.into_iter().filter(|device| device.id == id);
        let device = matching.next().ok_or_else(|| {
            Error::State("approved device is absent; rediscover its current identity".into())
        })?;
        if matching.next().is_some() {
            return Err(Error::UnsafeDevice(
                "device fingerprint is ambiguous".into(),
            ));
        }
        Ok(device)
    })
    .await
    .map_err(|_| Error::State("device scan task failed".into()))?
}

fn json_error(_: serde_json::Error) -> Error {
    Error::Protocol("invalid local JSON message".into())
}

fn validate_metadata(metadata: &fs::Metadata, expected_uid: u32) -> Result<()> {
    if metadata.uid() != expected_uid || metadata.mode() & 0o022 != 0 {
        return Err(Error::Permission(
            "policy and socket directories must be root-owned and not group/other writable".into(),
        ));
    }
    Ok(())
}

fn protected_directory(path: &Path, expected_uid: u32) -> Result<()> {
    if !path.is_absolute() {
        return Err(Error::InvalidInput("helper paths must be absolute".into()));
    }
    let mut cursor = PathBuf::new();
    for component in path.components() {
        match component {
            Component::RootDir | Component::Normal(_) => cursor.push(component),
            _ => {
                return Err(Error::InvalidInput(
                    "helper paths cannot contain parent/current directory components".into(),
                ));
            }
        }
        let metadata = fs::symlink_metadata(&cursor)?;
        if !metadata.is_dir() || metadata.file_type().is_symlink() {
            return Err(Error::Permission(
                "helper paths cannot traverse symlinks or non-directories".into(),
            ));
        }
        validate_metadata(&metadata, expected_uid)?;
    }
    Ok(())
}

fn read_policy(config: &Path) -> Result<Policy> {
    let parent = config
        .parent()
        .ok_or_else(|| Error::InvalidInput("policy parent directory required".into()))?;
    protected_directory(parent, 0)?;
    let descriptor = rustix::fs::open(
        config,
        rustix::fs::OFlags::RDONLY
            | rustix::fs::OFlags::NOFOLLOW
            | rustix::fs::OFlags::CLOEXEC
            | rustix::fs::OFlags::NONBLOCK,
        rustix::fs::Mode::empty(),
    )
    .map_err(std::io::Error::from)?;
    let file = File::from(descriptor);
    let metadata = file.metadata()?;
    validate_metadata(&metadata, 0)?;
    if !metadata.is_file() || metadata.len() > MAX_FRAME_BYTES as u64 {
        return Err(Error::Permission(
            "policy must be a regular file no larger than 64 KiB".into(),
        ));
    }
    let mut bytes = Vec::new();
    file.take((MAX_FRAME_BYTES + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > MAX_FRAME_BYTES {
        return Err(Error::InvalidInput("policy size limit exceeded".into()));
    }
    let policy: Policy = serde_json::from_slice(&bytes).map_err(json_error)?;
    policy.validate()?;
    Ok(policy)
}

async fn read_frame<R: AsyncRead + Unpin>(reader: &mut R, deadline: Duration) -> Result<Vec<u8>> {
    let limited = reader.take((MAX_FRAME_BYTES + 1) as u64);
    let mut buffered = BufReader::new(limited);
    let mut frame = Vec::new();
    timeout(deadline, buffered.read_until(b'\n', &mut frame))
        .await
        .map_err(|_| Error::Timeout("local IPC frame".into()))??;
    if frame.len() > MAX_FRAME_BYTES || frame.last() != Some(&b'\n') {
        return Err(Error::Protocol(
            "IPC requires one newline-terminated JSON frame no larger than 64 KiB".into(),
        ));
    }
    Ok(frame)
}

async fn write_frame<W: AsyncWrite + Unpin, T: Serialize>(writer: &mut W, value: &T) -> Result<()> {
    let mut frame = serde_json::to_vec(value).map_err(json_error)?;
    frame.push(b'\n');
    if frame.len() > MAX_FRAME_BYTES {
        return Err(Error::Protocol(
            "local response exceeds IPC size limit".into(),
        ));
    }
    timeout(WRITE_TIMEOUT, writer.write_all(&frame))
        .await
        .map_err(|_| Error::Timeout("local IPC write".into()))??;
    Ok(())
}

async fn serve_connection(mut stream: UnixStream, app: Arc<App>) -> Result<()> {
    let uid = stream.peer_cred()?.uid();
    let mut operation = "unauthorized_or_invalid";
    let response = match app.policy.authorize_uid(uid) {
        Err(error) => Response::failure(error),
        Ok(()) => match read_frame(&mut stream, READ_TIMEOUT).await {
            Ok(frame) => match serde_json::from_slice::<Request>(&frame).map_err(json_error) {
                Ok(req) => {
                    operation = match &req {
                        Request::Export { .. } => "export",
                        Request::Unexport { .. } => "unexport",
                        Request::Attach { .. } => "attach",
                        Request::Detach { .. } => "detach",
                        Request::Sessions => "sessions",
                        Request::Status => "status",
                    };
                    match app.handle(uid, req).await {
                        Ok(data) => Response::success(data),
                        Err(error) => Response::failure(error),
                    }
                }
                Err(error) => Response::failure(error),
            },
            Err(error) => Response::failure(error),
        },
    };
    tracing::info!(
        caller_uid = uid,
        operation,
        success = response.ok,
        "local helper operation finished"
    );
    if let Err(error) = write_frame(&mut stream, &response).await {
        if matches!(error, Error::Protocol(_)) {
            write_frame(&mut stream, &Response::failure(error)).await?;
        } else {
            return Err(error);
        }
    }
    Ok(())
}

pub(super) async fn request(socket: &Path, req: Request) -> Result<Response> {
    let parent = socket
        .parent()
        .ok_or_else(|| Error::InvalidInput("socket parent directory required".into()))?;
    protected_directory(parent, 0)?;
    let metadata = fs::symlink_metadata(socket)?;
    if metadata.uid() != 0 || !metadata.file_type().is_socket() {
        return Err(Error::Permission(
            "helper socket must be root-owned and not a symlink".into(),
        ));
    }
    let mut stream = timeout(READ_TIMEOUT, UnixStream::connect(socket))
        .await
        .map_err(|_| Error::Timeout("connecting to local helper".into()))??;
    if stream.peer_cred()?.uid() != 0 {
        return Err(Error::Permission("helper peer is not root".into()));
    }
    write_frame(&mut stream, &req).await?;
    let frame = read_frame(&mut stream, RESPONSE_TIMEOUT).await?;
    serde_json::from_slice(&frame).map_err(json_error)
}

pub(super) async fn serve(config: &Path, socket: &Path) -> Result<()> {
    if !rustix::process::geteuid().is_root() {
        return Err(Error::Permission(
            "only the restricted helper, installed by an administrator, may run as root".into(),
        ));
    }
    let policy = read_policy(config)?;
    let parent = socket
        .parent()
        .ok_or_else(|| Error::InvalidInput("socket parent directory required".into()))?;
    if !parent.exists() {
        let ancestor = parent
            .parent()
            .ok_or_else(|| Error::InvalidInput("socket directory parent required".into()))?;
        protected_directory(ancestor, 0)?;
        fs::create_dir(parent)?;
        fs::set_permissions(parent, fs::Permissions::from_mode(0o755))?;
    }
    protected_directory(parent, 0)?;
    if fs::symlink_metadata(socket).is_ok() {
        return Err(Error::State("socket path already exists; verify the previous helper stopped and reconcile its state before removing it".into()));
    }
    let listener = UnixListener::bind(socket)?;
    fs::set_permissions(socket, fs::Permissions::from_mode(0o666))?;
    let bound_metadata = fs::symlink_metadata(socket)?;
    let app = Arc::new(App {
        policy,
        backend: NativeBackend::default(),
        state: Mutex::new(ManagedState::default()),
    });
    let limit = Arc::new(Semaphore::new(MAX_CONNECTIONS));
    let mut jobs = JoinSet::new();
    let mut termination =
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())?;
    tracing::info!(
        "root helper listening; unknown kernel ports are not owned; no built-in data encryption"
    );
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (stream, _) = accepted?;
                match limit.clone().try_acquire_owned() {
                    Ok(permit) => {
                        let app = app.clone();
                        jobs.spawn(async move {
                            let _permit = permit;
                            if serve_connection(stream, app).await.is_err() {
                                tracing::warn!("local helper connection failed or caller disconnected; inspect kernel state after any mutation");
                            }
                        });
                    }
                    Err(_) => { tracing::warn!("local helper concurrent connection limit reached"); }
                }
            }
            _ = termination.recv() => break,
            interrupt = tokio::signal::ctrl_c() => { interrupt?; break; }
            Some(result) = jobs.join_next(), if !jobs.is_empty() => {
                if result.is_err() { tracing::error!("helper request task failed; administrator reconciliation may be needed"); }
            }
        }
    }
    drop(listener);
    // Stop accepting work, but allow bounded in-flight native operations to
    // finish. Existing native kernel sessions intentionally survive shutdown.
    while let Some(result) = jobs.join_next().await {
        if result.is_err() {
            tracing::error!("helper request failed during shutdown");
        }
    }
    let current = fs::symlink_metadata(socket)?;
    if current.dev() == bound_metadata.dev() && current.ino() == bound_metadata.ino() {
        fs::remove_file(socket)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn app(allowed_uids: Vec<u32>) -> Arc<App> {
        Arc::new(App {
            policy: Policy {
                allowed_uids,
                export_device_ids: vec![],
                peers: vec![],
                trusted_network: false,
            },
            backend: NativeBackend::default(),
            state: Mutex::new(ManagedState::default()),
        })
    }

    #[tokio::test]
    async fn real_unix_peer_credentials_allow_status_and_deny_unknown_uid() -> Result<()> {
        for allowed in [true, false] {
            let (mut client, server) = UnixStream::pair()?;
            let uid = server.peer_cred()?.uid();
            let service = app(if allowed { vec![uid] } else { vec![] });
            let task = tokio::spawn(serve_connection(server, service));
            if allowed {
                write_frame(&mut client, &Request::Status).await?;
            }
            let frame = read_frame(&mut client, READ_TIMEOUT).await?;
            let response: Response = serde_json::from_slice(&frame).map_err(json_error)?;
            assert_eq!(response.ok, allowed);
            task.await
                .map_err(|_| Error::State("test server task failed".into()))??;
        }
        Ok(())
    }

    #[tokio::test]
    async fn bounded_frames_reject_overflow_missing_newline_and_timeout() -> Result<()> {
        for bytes in [
            vec![b'x'; MAX_FRAME_BYTES + 1],
            b"{\"op\":\"status\"}".to_vec(),
        ] {
            let (mut sender, mut receiver) = tokio::io::duplex(MAX_FRAME_BYTES + 2);
            sender.write_all(&bytes).await?;
            sender.shutdown().await?;
            assert!(read_frame(&mut receiver, READ_TIMEOUT).await.is_err());
        }
        let (_sender, mut receiver) = tokio::io::duplex(64);
        assert!(matches!(
            read_frame(&mut receiver, Duration::from_millis(20)).await,
            Err(Error::Timeout(_))
        ));
        Ok(())
    }

    #[tokio::test]
    async fn unix_unknown_fields_are_rejected_and_unapproved_actions_do_no_native_io() -> Result<()>
    {
        let (mut client, server) = UnixStream::pair()?;
        let uid = server.peer_cred()?.uid();
        let service = app(vec![uid]);
        let task = tokio::spawn(serve_connection(server, service.clone()));
        client
            .write_all(b"{\"op\":\"status\",\"command\":\"id\"}\n")
            .await?;
        let response: Response =
            serde_json::from_slice(&read_frame(&mut client, READ_TIMEOUT).await?)
                .map_err(json_error)?;
        assert!(!response.ok);
        task.await
            .map_err(|_| Error::State("test server task failed".into()))??;
        assert!(matches!(
            service
                .handle(
                    uid,
                    Request::Export {
                        device_id: format!("usb-{}", "a".repeat(64))
                    }
                )
                .await,
            Err(Error::Permission(_))
        ));
        assert!(matches!(
            service
                .handle(uid, Request::Detach { port: VhciPort(1) })
                .await,
            Err(Error::Permission(_))
        ));
        let peer = rustusb_core::Peer {
            host: rustusb_core::Host::new("192.0.2.1")?,
            port: std::num::NonZeroU16::new(3240)
                .ok_or_else(|| Error::State("test port".into()))?,
        };
        assert!(matches!(
            service
                .handle(
                    uid,
                    Request::Attach {
                        peer,
                        bus_id: rustusb_core::BusId::new("1-1")?
                    }
                )
                .await,
            Err(Error::Permission(_))
        ));
        Ok(())
    }

    #[test]
    fn policy_metadata_rejects_writable_or_wrong_owner() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let file_path = directory.path().join("policy.json");
        fs::write(&file_path, b"{}")?;
        fs::set_permissions(&file_path, fs::Permissions::from_mode(0o644))?;
        let metadata = fs::metadata(&file_path)?;
        validate_metadata(&metadata, metadata.uid())?;
        assert!(validate_metadata(&metadata, metadata.uid().wrapping_add(1)).is_err());
        fs::set_permissions(&file_path, fs::Permissions::from_mode(0o666))?;
        assert!(validate_metadata(&fs::metadata(&file_path)?, metadata.uid()).is_err());
        Ok(())
    }

    #[test]
    fn root_socket_validation_rejects_user_writable_directory() {
        assert!(protected_directory(Path::new("/tmp"), 0).is_err());
        assert!(protected_directory(Path::new("relative"), 0).is_err());
    }
}

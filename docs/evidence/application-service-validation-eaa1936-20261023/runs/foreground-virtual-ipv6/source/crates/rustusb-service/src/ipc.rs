//! Bounded local transport authenticated with kernel Unix peer credentials.

use crate::{
    config::{LoadedConfig, Manifest},
    fs::Directory,
    rpc::*,
    runtime::{Command, Controls, Runtime},
};
use rustix::fs::{AtFlags, FileType, Mode, OFlags};
use rustusb_core::{Error, Result};
use std::{
    fs::File,
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::{Component, Path},
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{UnixListener, UnixStream},
    sync::{Semaphore, mpsc, oneshot, watch},
    task::JoinSet,
};

const MAX_FRAME: usize = 65536;
const DEADLINE: Duration = Duration::from_secs(5);

struct Endpoint {
    directory: Directory,
    _lock: File,
    listener: UnixListener,
    identity: (u64, u64),
    uid: u32,
}
impl Endpoint {
    async fn bind(manifest: &Manifest) -> Result<Self> {
        let directory = Directory::open(&manifest.control_directory, manifest.broker_uid, 0o711)?;
        let lock = directory.lock()?;
        let path = manifest.socket_path();
        if let Some(identity) = socket_identity(&directory.file, manifest.broker_uid)? {
            let connected =
                tokio::time::timeout(Duration::from_secs(1), UnixStream::connect(&path)).await;
            match connected {
                Ok(Err(error)) if error.kind() == std::io::ErrorKind::ConnectionRefused => {}
                _ => {
                    return Err(Error::State(
                        "existing agent socket is active or cannot safely be classified".into(),
                    ));
                }
            }
            directory.verify()?;
            if socket_identity(&directory.file, manifest.broker_uid)? != Some(identity) {
                return Err(Error::State("stale agent socket changed".into()));
            }
            rustix::fs::unlinkat(&directory.file, "control.sock", AtFlags::empty())
                .map_err(std::io::Error::from)?;
        }
        directory.verify()?;
        let listener = UnixListener::bind(&path)?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o666))?;
        directory.verify()?;
        let identity = socket_identity(&directory.file, manifest.broker_uid)?
            .ok_or_else(|| Error::State("new agent socket disappeared".into()))?;
        directory.file.sync_all()?;
        Ok(Self {
            directory,
            _lock: lock,
            listener,
            identity,
            uid: manifest.broker_uid,
        })
    }
    fn unlink(&self) -> Result<()> {
        self.directory.verify()?;
        if socket_identity(&self.directory.file, self.uid)? != Some(self.identity) {
            return Err(Error::State(
                "agent socket identity changed before close".into(),
            ));
        }
        rustix::fs::unlinkat(&self.directory.file, "control.sock", AtFlags::empty())
            .map_err(std::io::Error::from)?;
        self.directory.file.sync_all()?;
        Ok(())
    }
}

fn socket_identity(directory: &File, uid: u32) -> Result<Option<(u64, u64)>> {
    let stat = match rustix::fs::statat(directory, "control.sock", AtFlags::SYMLINK_NOFOLLOW) {
        Ok(s) => s,
        Err(rustix::io::Errno::NOENT) => return Ok(None),
        Err(e) => return Err(std::io::Error::from(e).into()),
    };
    if FileType::from_raw_mode(stat.st_mode) != FileType::Socket
        || stat.st_uid != uid
        || stat.st_mode & 0o7777 != 0o666
    {
        return Err(Error::Permission("agent socket owner/type/mode".into()));
    }
    Ok(Some((stat.st_dev, stat.st_ino)))
}

/// Run the nonroot application service until cooperative stop and actual job cleanup.
/// The caller must await this future; cancelling its owning task is not cleanup.
pub async fn serve(config: LoadedConfig, mut stop: watch::Receiver<bool>) -> Result<()> {
    if rustix::process::geteuid().as_raw() != config.manifest.broker_uid
        || config.manifest.broker_uid == 0
    {
        return Err(Error::Permission(
            "agent must run as configured nonroot broker".into(),
        ));
    }
    let config = Arc::new(config);
    let controls = Arc::new(Mutex::new(Controls::default()));
    let runtime = Runtime::open(config.clone(), controls.clone()).await?;
    let endpoint = Endpoint::bind(&config.manifest).await?;
    let (sender, receiver) = mpsc::channel(16);
    let (shutdown, shutdown_receiver) = watch::channel(*stop.borrow());
    let mut owner = tokio::spawn(runtime.run(receiver, shutdown_receiver));
    let permits = Arc::new(Semaphore::new(16));
    let mut requests = JoinSet::new();
    let mut accept_error = None;
    if !*stop.borrow() {
        loop {
            tokio::select! {
                changed=stop.changed()=>{let _=changed;break;},
                result=&mut owner=>{
                    // No job can remain when the supervisor returns normally.
                    while let Some(result)=requests.join_next().await{let _=result;}
                    endpoint.unlink()?;
                    return result.map_err(|_|Error::State("agent supervisor task ended abnormally".into()))?;
                },
                result=requests.join_next(),if !requests.is_empty()=>{let _=result;},
                accepted=endpoint.listener.accept()=>{
                    let (stream,_)=match accepted{Ok(pair)=>pair,Err(error)=>{accept_error=Some(error);break;}};
                    let Ok(permit)=permits.clone().try_acquire_owned()else{drop(stream);continue;};
                    let sender=sender.clone();let config=config.clone();let controls=controls.clone();
                    requests.spawn(async move{let _permit=permit;let _=tokio::time::timeout(DEADLINE,request(stream,sender,config,controls)).await;});
                },
            }
        }
    }
    shutdown.send_replace(true);
    // Request tasks own no device capability. Their individual bounded deadline
    // completes while the real supervisor continues handling cleanup facts.
    while let Some(result) = requests.join_next().await {
        let _ = result;
    }
    drop(sender);
    let result = owner
        .await
        .map_err(|_| Error::State("agent supervisor task ended abnormally".into()))?;
    let endpoint_result = endpoint.unlink();
    result?;
    endpoint_result?;
    if let Some(error) = accept_error {
        return Err(error.into());
    }
    Ok(())
}

async fn request(
    mut stream: UnixStream,
    sender: mpsc::Sender<Command>,
    config: Arc<LoadedConfig>,
    controls: Arc<Mutex<Controls>>,
) -> Result<()> {
    let uid = stream.peer_cred()?.uid();
    let envelope: RequestEnvelope = serde_json::from_slice(&read_frame(&mut stream).await?)
        .map_err(|_| Error::InvalidInput("agent request schema".into()))?;
    if envelope.schema_version != 1 {
        return Err(Error::InvalidInput("agent protocol version".into()));
    }
    // A valid stop reaches its independent watch even if a durable transaction
    // is slow. The response still waits for persistence; it never reports release.
    if matches!(
        envelope.request,
        Request::StopJob { .. } | Request::StopSession { .. }
    ) {
        let _ = controls
            .lock()
            .map_err(|_| Error::State("agent controls lock poisoned".into()))?
            .request(&config, uid, &envelope.request);
    }
    let (reply, receive) = oneshot::channel();
    sender
        .send(Command {
            uid,
            request: envelope.request,
            reply,
        })
        .await
        .map_err(|_| Error::State("agent supervisor unavailable".into()))?;
    let response = receive
        .await
        .map_err(|_| Error::State("agent supervisor unavailable".into()))?;
    let bytes = serde_json::to_vec(&response)
        .map_err(|_| Error::State("agent response encoding".into()))?;
    write_frame(&mut stream, &bytes).await
}

async fn read_frame(stream: &mut UnixStream) -> Result<Vec<u8>> {
    let length = stream.read_u32().await? as usize;
    if length == 0 || length > MAX_FRAME {
        return Err(Error::InvalidInput("agent frame length".into()));
    }
    let mut bytes = vec![0; length];
    stream.read_exact(&mut bytes).await?;
    Ok(bytes)
}
async fn write_frame(stream: &mut UnixStream, bytes: &[u8]) -> Result<()> {
    if bytes.is_empty() || bytes.len() > MAX_FRAME {
        return Err(Error::State("agent response exceeds frame budget".into()));
    }
    stream.write_u32(bytes.len() as u32).await?;
    stream.write_all(bytes).await?;
    stream.flush().await?;
    Ok(())
}

fn control_directory(manifest: &Manifest) -> Result<File> {
    // O_PATH permits traversal by an operator with execute-only access to the
    // broker's 0711 leaf. Each component is anchored to its checked parent FD.
    let flags = OFlags::PATH | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
    let mut directory =
        File::from(rustix::fs::open("/", flags, Mode::empty()).map_err(std::io::Error::from)?);
    let components: Vec<_> = manifest
        .control_directory
        .components()
        .filter(|c| !matches!(c, Component::RootDir))
        .collect();
    for (index, component) in components.iter().enumerate() {
        let metadata = directory.metadata()?;
        if metadata.uid() != 0 || metadata.mode() & 0o022 != 0 {
            return Err(Error::Permission("agent client ancestor trust".into()));
        }
        let Component::Normal(name) = component else {
            return Err(Error::InvalidInput("agent client directory".into()));
        };
        directory = File::from(
            rustix::fs::openat(&directory, *name, flags, Mode::empty())
                .map_err(std::io::Error::from)?,
        );
        if index + 1 == components.len() {
            let metadata = directory.metadata()?;
            if metadata.uid() != manifest.broker_uid || metadata.mode() & 0o7777 != 0o711 {
                return Err(Error::Permission("agent client leaf owner/mode".into()));
            }
        }
    }
    Ok(directory)
}

/// Send one typed operation to the fixed trusted endpoint and verify its OS UID.
/// This reads public manifest metadata only; it never loads a TLS private key.
pub async fn call(manifest: &Manifest, request: Request) -> Result<ResponseEnvelope> {
    manifest.validate()?;
    tokio::time::timeout(DEADLINE, async {
        let directory = control_directory(manifest)?;
        let identity = socket_identity(&directory, manifest.broker_uid)?
            .ok_or_else(|| Error::State("agent is not listening".into()))?;
        let mut stream = UnixStream::connect(manifest.socket_path()).await?;
        if stream.peer_cred()?.uid() != manifest.broker_uid
            || socket_identity(&directory, manifest.broker_uid)? != Some(identity)
        {
            return Err(Error::Permission("agent server identity changed".into()));
        }
        let current = control_directory(manifest)?;
        let before = directory.metadata()?;
        let after = current.metadata()?;
        if (before.dev(), before.ino()) != (after.dev(), after.ino()) {
            return Err(Error::Permission("agent directory identity changed".into()));
        }
        let bytes = serde_json::to_vec(&RequestEnvelope {
            schema_version: 1,
            request,
        })
        .map_err(|_| Error::InvalidInput("agent request encoding".into()))?;
        write_frame(&mut stream, &bytes).await?;
        let response: ResponseEnvelope = serde_json::from_slice(&read_frame(&mut stream).await?)
            .map_err(|_| Error::Protocol("agent response schema".into()))?;
        if response.schema_version != 1 {
            return Err(Error::Protocol("agent response version".into()));
        }
        Ok(response)
    })
    .await
    .map_err(|_| Error::Timeout("agent local request".into()))?
}

/// Load a public trusted manifest in an owned blocking task and make one call.
pub async fn call_path(path: &Path, request: Request) -> Result<ResponseEnvelope> {
    let path = path.to_owned();
    let manifest = tokio::task::spawn_blocking(move || Manifest::read(&path))
        .await
        .map_err(|_| Error::State("agent manifest task failed".into()))??;
    call(&manifest, request).await
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn oversized_empty_and_unknown_requests_fail_without_allocating_unboundedly() -> Result<()>
    {
        for length in [0, 65537, u32::MAX] {
            let (mut left, mut right) = UnixStream::pair()?;
            left.write_u32(length).await?;
            assert!(read_frame(&mut right).await.is_err());
        }
        assert!(serde_json::from_str::<RequestEnvelope>(r#"{"schema_version":1,"request":{"op":"start","profile":"x","request_id":"00000000-0000-0000-0000-000000000001","endpoint":"attacker"}}"#).is_err());
        Ok(())
    }
    #[tokio::test]
    async fn unix_credentials_and_framing_come_from_the_actual_connection() -> Result<()> {
        let (mut left, mut right) = UnixStream::pair()?;
        assert_eq!(
            right.peer_cred()?.uid(),
            rustix::process::geteuid().as_raw()
        );
        write_frame(&mut left, b"bounded").await?;
        assert_eq!(read_frame(&mut right).await?, b"bounded");
        Ok(())
    }
}

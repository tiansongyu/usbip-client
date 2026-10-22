//! Linux USB/IP tool adapter with bounded subprocesses and authoritative sysfs checks.
//!
//! This crate does not authorize callers or determine whether storage is safe to export.
//! The privileged helper must perform those checks before calling mutation methods.
//! Dropping an operation future kills an active child, but cannot undo a kernel write;
//! callers must reconcile kernel state after cancellation. No method guesses a rollback.

use rustusb_core::{BusId, Error, ExportBackend, ImportBackend, Peer, Result, VhciPort};
use rustusb_protocol::RemoteDevice;
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, HashSet},
    io,
    path::{Path, PathBuf},
    process::Stdio,
    sync::Arc,
    time::Duration,
};
use tokio::{
    fs,
    io::{AsyncRead, AsyncReadExt},
    process::Command,
    sync::Mutex,
    time::{Instant, sleep},
};

const MAX_ATTRIBUTE: usize = 65_536;
const MAX_OUTPUT: usize = 65_536;
const NULL: u32 = 4;
const USED: u32 = 6;
const HEADER: &str = "hub port sta spd dev sockfd local_busid";

/// One authoritative VHCI port observation, not a durable session or caller identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NativeSession {
    /// Global kernel port, bounded to the native utility's unsigned-byte API.
    pub port: VhciPort,
    /// Kernel status: 4 unused, 5 enumerating, 6 used, 7 error.
    pub status: u32,
    /// Local enumerated USB bus ID, available only for status 6.
    pub local_bus_id: Option<BusId>,
    /// Remote `(busnum << 16) | devnum`, available only for status 6.
    pub remote_device_id: Option<u32>,
    /// Enumerated vendor ID; missing devices or unreadable attributes are errors.
    pub vendor_id: Option<u16>,
    /// Enumerated product ID; this is metadata, not a cryptographic identity.
    pub product_id: Option<u16>,
}

#[derive(Debug)]
struct Configuration {
    usbip: PathBuf,
    devices: PathBuf,
    platform: PathBuf,
    records: PathBuf,
    command_timeout: Duration,
    settle_timeout: Duration,
    poll_interval: Duration,
    require_root_tool: bool,
}

/// Fixed-tool native backend. Clones serialize mutations through the same lock.
#[derive(Debug, Clone)]
pub struct NativeBackend {
    configuration: Arc<Configuration>,
    mutation: Arc<Mutex<()>>,
}

impl Default for NativeBackend {
    fn default() -> Self {
        Self {
            configuration: Arc::new(Configuration {
                usbip: PathBuf::from("/usr/bin/usbip"),
                devices: PathBuf::from("/sys/bus/usb/devices"),
                platform: PathBuf::from("/sys/devices/platform"),
                records: PathBuf::from("/var/run/vhci_hcd"),
                command_timeout: Duration::from_secs(15),
                settle_timeout: Duration::from_secs(10),
                poll_interval: Duration::from_millis(100),
                require_root_tool: true,
            }),
            mutation: Arc::new(Mutex::new(())),
        }
    }
}

impl NativeBackend {
    /// Read active kernel ports. An unloaded/unavailable VHCI is reported explicitly.
    /// User-space `/var/run/vhci_hcd` records are never the source of liveness.
    pub async fn sessions(&self) -> Result<Vec<NativeSession>> {
        Ok(self
            .port_states()
            .await?
            .into_iter()
            .filter(|s| s.status != NULL)
            .collect())
    }

    /// Read all VHCI ports, including unused ports, validating every status table.
    /// Every `status.N` is consumed and the row count must agree with `nports`.
    pub async fn port_states(&self) -> Result<Vec<NativeSession>> {
        tokio::time::timeout(Duration::from_secs(5), self.read_port_states())
            .await
            .map_err(|_| Error::Timeout("VHCI sysfs snapshot exceeded deadline".into()))?
    }

    async fn read_port_states(&self) -> Result<Vec<NativeSession>> {
        let configuration = &self.configuration;
        let mut directories = fs::read_dir(&configuration.platform).await?;
        let mut owners = Vec::new();
        let mut checked = 0;
        while let Some(entry) = directories.next_entry().await? {
            checked += 1;
            if checked > 4096 {
                return Err(Error::State(
                    "platform directory entry limit exceeded".into(),
                ));
            }
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue };
            if name.strip_prefix("vhci_hcd.").is_some_and(decimal_name)
                && fs::try_exists(entry.path().join("status")).await?
            {
                owners.push(entry.path());
            }
        }
        if owners.is_empty() {
            return Err(Error::Unsupported(
                "VHCI sysfs is unavailable; administrator must load vhci_hcd".into(),
            ));
        }
        if owners.len() != 1 {
            return Err(Error::State(
                "ambiguous VHCI status owners; reconciliation required".into(),
            ));
        }
        let owner = &owners[0];
        let count = read_text(&owner.join("nports"))
            .await?
            .trim()
            .parse::<usize>()
            .map_err(|_| Error::State("invalid VHCI nports".into()))?;
        if count == 0 || count > 256 {
            return Err(Error::Unsupported(
                "native usbip adapter requires 1..=256 VHCI ports (utility uses u8)".into(),
            ));
        }
        let mut attributes = fs::read_dir(owner).await?;
        let mut names = Vec::new();
        let mut entries = 0;
        while let Some(entry) = attributes.next_entry().await? {
            entries += 1;
            if entries > 1024 {
                return Err(Error::State("VHCI attribute limit exceeded".into()));
            }
            let name = entry.file_name();
            let Some(name) = name.to_str() else { continue };
            if name == "status" || name.strip_prefix("status.").is_some_and(decimal_name) {
                names.push(entry.path());
            }
        }
        names.sort();
        let mut ports = BTreeMap::new();
        for name in names {
            for mut session in parse_status(&read_text(&name).await?)? {
                if usize::from(session.port.0) >= count {
                    return Err(Error::State("VHCI port exceeds nports".into()));
                }
                if session.status == USED {
                    let bus_id = session
                        .local_bus_id
                        .as_ref()
                        .ok_or_else(|| Error::State("used VHCI port has no local bus ID".into()))?;
                    let path = configuration.devices.join(bus_id.as_str());
                    session.vendor_id = Some(read_hex_u16(&path.join("idVendor")).await?);
                    session.product_id = Some(read_hex_u16(&path.join("idProduct")).await?);
                }
                if ports.insert(session.port.0, session).is_some() {
                    return Err(Error::State(
                        "duplicate VHCI port across status attributes".into(),
                    ));
                }
            }
        }
        if ports.len() != count {
            return Err(Error::State(
                "incomplete VHCI status snapshot; reconciliation required".into(),
            ));
        }
        Ok(ports.into_values().collect())
    }

    /// Import and return the uniquely identified, enumerated session created by this operation.
    /// Checks DEVLIST metadata, an initially free port, kernel devid, local VID/PID,
    /// and the native utility's endpoint record. Failure never claims or detaches an uncertain port.
    pub async fn attach_session(&self, peer: Peer, bus_id: BusId) -> Result<NativeSession> {
        let _lock = self.mutation.lock().await;
        if format!("{} {} {}\n", peer.host, peer.port, bus_id).len() >= 100 {
            return Err(Error::InvalidInput(
                "endpoint metadata exceeds the native utility's 100-byte record buffer".into(),
            ));
        }
        let advertised =
            rustusb_protocol::discover(&peer, self.configuration.command_timeout).await?;
        let remote = advertised
            .into_iter()
            .find(|d| d.bus_id == bus_id)
            .ok_or_else(|| {
                Error::State("requested device is absent from remote export list".into())
            })?;
        // The official attach library narrows both busnum and devnum to u8.
        if remote.bus_number == 0
            || remote.bus_number > 255
            || remote.device_number == 0
            || remote.device_number > 255
            || !(1..=6).contains(&remote.speed)
        {
            return Err(Error::Unsupported(
                "remote addressing/speed exceeds the audited native utility API".into(),
            ));
        }
        let before = self.port_states().await?;
        if before.iter().all(|s| s.status != NULL) {
            return Err(Error::State("no free VHCI port".into()));
        }
        let args = vec![
            "--tcp-port".into(),
            peer.port.to_string(),
            "attach".into(),
            "--remote".into(),
            peer.host.to_string(),
            "--busid".into(),
            bus_id.to_string(),
        ];
        tracing::info!(operation = "attach", bus_id = %bus_id, "native operation started");
        if let Err(error) = self.run_tool(&args).await {
            return Err(self.reconciliation_error("attach", error).await);
        }
        let deadline = Instant::now() + self.configuration.settle_timeout;
        loop {
            let after = self
                .port_states()
                .await
                .map_err(|e| uncertain("attach", e))?;
            let candidates = matched_new_sessions(&before, &after, &remote);
            if candidates.len() > 1 {
                return Err(Error::State(
                    "attach created ambiguous matching ports; reconciliation required".into(),
                ));
            }
            if let Some(session) = candidates.first() {
                if self.record_matches(session.port, &peer, &bus_id).await? {
                    tracing::info!(
                        operation = "attach",
                        port = session.port.0,
                        "kernel enumeration verified"
                    );
                    return Ok((*session).clone());
                }
                return Err(Error::State("kernel attached but native endpoint record is missing or mismatched; reconciliation required".into()));
            }
            if Instant::now() >= deadline {
                return Err(Error::Timeout("attach tool completed but no unique matching enumerated port appeared; reconciliation required".into()));
            }
            sleep(self.configuration.poll_interval).await;
        }
    }

    /// Release a previously claimed session only if its current identity still matches.
    /// A now-unused port is already released; a replacement device is never detached.
    pub async fn detach_session(&self, expected: &NativeSession) -> Result<()> {
        self.detach_checked(expected.port, Some(expected)).await
    }

    async fn detach_checked(&self, port: VhciPort, expected: Option<&NativeSession>) -> Result<()> {
        let _lock = self.mutation.lock().await;
        let before = self.port_states().await?;
        let state = before
            .iter()
            .find(|s| s.port == port)
            .ok_or_else(|| Error::State("VHCI port is outside current nports".into()))?;
        if state.status == NULL {
            return Ok(());
        }
        if expected.is_some_and(|identity| !same_identity(identity, state)) {
            return Err(Error::Permission(
                "VHCI port identity changed; replacement session will not be detached".into(),
            ));
        }
        tracing::info!(
            operation = "detach",
            port = port.0,
            "native operation started"
        );
        if let Err(error) = self
            .run_tool(&["detach".into(), "--port".into(), port.0.to_string()])
            .await
        {
            return Err(self.reconciliation_error("detach", error).await);
        }
        let deadline = Instant::now() + self.configuration.settle_timeout;
        loop {
            let after = self
                .port_states()
                .await
                .map_err(|e| uncertain("detach", e))?;
            if after.iter().any(|s| s.port == port && s.status == NULL) {
                tracing::info!(
                    operation = "detach",
                    port = port.0,
                    "kernel port cleanup verified"
                );
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(Error::Timeout(
                    "detach tool completed but port is still active; reconciliation required"
                        .into(),
                ));
            }
            sleep(self.configuration.poll_interval).await;
        }
    }

    async fn record_matches(&self, port: VhciPort, peer: &Peer, bus_id: &BusId) -> Result<bool> {
        let path = self.configuration.records.join(format!("port{}", port.0));
        let text = match read_text(&path).await {
            Ok(text) => text,
            Err(Error::Io(error)) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(uncertain("attach record verification", error)),
        };
        let fields = text.split_whitespace().collect::<Vec<_>>();
        Ok(fields.len() == 3
            && fields[0] == peer.host.as_str()
            && fields[1] == peer.port.to_string()
            && fields[2] == bus_id.as_str())
    }

    async fn device_binding(&self, bus_id: &BusId) -> Result<Binding> {
        let path = self.configuration.devices.join(bus_id.as_str());
        fs::metadata(&path).await?;
        let driver = match fs::read_link(path.join("driver")).await {
            Ok(link) => Some(
                link.file_name()
                    .and_then(|s| s.to_str())
                    .ok_or_else(|| Error::State("invalid USB driver link".into()))?
                    .to_owned(),
            ),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        let status = if driver.as_deref() == Some("usbip-host") {
            Some(
                read_text(&path.join("usbip_status"))
                    .await?
                    .trim()
                    .parse::<u32>()
                    .map_err(|_| Error::State("invalid usbip_status".into()))?,
            )
        } else {
            None
        };
        // Distinguish a genuinely unbound device from a removal while reading its attributes.
        fs::metadata(&path).await?;
        Ok(Binding { driver, status })
    }

    async fn reconciliation_error(&self, operation: &str, error: Error) -> Error {
        let observation = match self.port_states().await {
            Ok(ports) => format!(
                "{} active kernel ports observed",
                ports.iter().filter(|s| s.status != NULL).count()
            ),
            Err(_) => "kernel port snapshot unavailable".into(),
        };
        match error {
            Error::Timeout(message) => Error::Timeout(format!(
                "{operation}: {message}; {observation}; reconciliation required"
            )),
            other => Error::Command(format!(
                "{operation}: {other}; {observation}; reconciliation required"
            )),
        }
    }

    async fn binding_error(&self, bus_id: &BusId, operation: &str, error: Error) -> Error {
        let observation = match self.device_binding(bus_id).await {
            Ok(binding) => format!(
                "driver={}, usbip_status={:?}",
                binding.driver.as_deref().unwrap_or("none"),
                binding.status
            ),
            Err(_) => "device binding snapshot unavailable".into(),
        };
        match error {
            Error::Timeout(message) => Error::Timeout(format!(
                "{operation}: {message}; {observation}; reconciliation required"
            )),
            other => Error::Command(format!(
                "{operation}: {other}; {observation}; reconciliation required"
            )),
        }
    }

    async fn run_tool(&self, args: &[String]) -> Result<()> {
        let executable = self.verify_tool().await?;
        let mut command = Command::new(executable);
        command
            .args(args)
            .env_clear()
            .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .current_dir("/")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        let mut child = command.spawn()?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| Error::Command("stdout pipe missing".into()))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| Error::Command("stderr pipe missing".into()))?;
        let outcome = tokio::time::timeout(self.configuration.command_timeout, async {
            let (status, _, _) =
                tokio::try_join!(child.wait(), drain_bounded(stdout), drain_bounded(stderr))?;
            Ok::<_, io::Error>(status)
        })
        .await;
        match outcome {
            Ok(Ok(status)) if status.success() => Ok(()),
            Ok(Ok(status)) => Err(Error::Command(format!(
                "fixed usbip exited {status}; kernel side effects may already exist"
            ))),
            Ok(Err(error)) => {
                let cleanup = kill_and_reap(&mut child).await;
                Err(Error::Command(format!(
                    "usbip output/wait failed: {error}; {cleanup}"
                )))
            }
            Err(_) => {
                let cleanup = kill_and_reap(&mut child).await;
                Err(Error::Timeout(format!(
                    "fixed usbip exceeded deadline; {cleanup}"
                )))
            }
        }
    }

    async fn verify_tool(&self) -> Result<PathBuf> {
        if !self.configuration.require_root_tool {
            return Ok(self.configuration.usbip.clone());
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let canonical = fs::canonicalize(&self.configuration.usbip).await?;
            // Validate both the configured path and the resolved path. A root-owned
            // executable can still be replaced when an ancestor is user-writable.
            for executable in [&self.configuration.usbip, &canonical] {
                let leaf = fs::metadata(executable).await?;
                if !leaf.is_file() || leaf.mode() & 0o111 == 0 {
                    return Err(Error::Permission(
                        "native tool must be an executable regular file".into(),
                    ));
                }
                let mut path = Some(executable.as_path());
                while let Some(current) = path {
                    let metadata = fs::metadata(current).await?;
                    if metadata.uid() != 0 || metadata.mode() & 0o022 != 0 {
                        return Err(Error::Permission("fixed native tool, symlink target and all parents must be root-owned and not group/world-writable".into()));
                    }
                    path = current.parent();
                }
            }
            Ok(canonical)
        }
        #[cfg(not(unix))]
        {
            Err(Error::Unsupported(
                "Linux native USB/IP requires Unix permissions".into(),
            ))
        }
    }
}

impl ExportBackend for NativeBackend {
    async fn export(&self, bus_id: BusId) -> Result<()> {
        let _lock = self.mutation.lock().await;
        let before = self.device_binding(&bus_id).await?;
        if before.driver.as_deref() == Some("usbip-host") {
            return match before.status {
                Some(1) => Ok(()),
                Some(2) => Err(Error::State(
                    "device is already occupied by a remote client".into(),
                )),
                _ => Err(Error::State(
                    "exported device has an invalid/error state".into(),
                )),
            };
        }
        tracing::info!(operation = "export", bus_id = %bus_id, "native operation started");
        if let Err(error) = self
            .run_tool(&["bind".into(), "--busid".into(), bus_id.to_string()])
            .await
        {
            return Err(self.binding_error(&bus_id, "export", error).await);
        }
        let deadline = Instant::now() + self.configuration.settle_timeout;
        loop {
            let binding = self
                .device_binding(&bus_id)
                .await
                .map_err(|e| uncertain("export", e))?;
            if binding.driver.as_deref() == Some("usbip-host")
                && matches!(binding.status, Some(1 | 2))
            {
                tracing::info!(operation = "export", bus_id = %bus_id, "kernel export binding verified");
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(Error::Timeout(
                    "bind completed but export binding was not verified; reconciliation required"
                        .into(),
                ));
            }
            sleep(self.configuration.poll_interval).await;
        }
    }

    async fn unexport(&self, bus_id: BusId) -> Result<()> {
        let _lock = self.mutation.lock().await;
        let before = self.device_binding(&bus_id).await?;
        if before.driver.as_deref() != Some("usbip-host") {
            return Ok(());
        }
        if before.status == Some(2) {
            return Err(Error::State(
                "device is occupied; coordinate client release before unexport".into(),
            ));
        }
        tracing::info!(operation = "unexport", bus_id = %bus_id, "native operation started");
        if let Err(error) = self
            .run_tool(&["unbind".into(), "--busid".into(), bus_id.to_string()])
            .await
        {
            return Err(self.binding_error(&bus_id, "unexport", error).await);
        }
        let deadline = Instant::now() + self.configuration.settle_timeout;
        loop {
            let binding = self
                .device_binding(&bus_id)
                .await
                .map_err(|e| uncertain("unexport", e))?;
            if binding
                .driver
                .as_deref()
                .is_some_and(|driver| driver != "usbip-host")
            {
                tracing::info!(operation = "unexport", bus_id = %bus_id, "replacement device driver observed");
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err(Error::Timeout("unbind completed but replacement driver was not observed; reconciliation required".into()));
            }
            sleep(self.configuration.poll_interval).await;
        }
    }
}

impl ImportBackend for NativeBackend {
    async fn attach(&self, peer: Peer, bus_id: BusId) -> Result<()> {
        self.attach_session(peer, bus_id).await.map(|_| ())
    }

    async fn detach(&self, port: VhciPort) -> Result<()> {
        self.detach_checked(port, None).await
    }
}

#[derive(Debug)]
struct Binding {
    driver: Option<String>,
    status: Option<u32>,
}

fn decimal_name(value: &str) -> bool {
    !value.is_empty() && value.bytes().all(|byte| byte.is_ascii_digit()) && value.len() <= 5
}

fn uncertain(operation: &str, error: Error) -> Error {
    Error::State(format!(
        "{operation}: {error}; kernel may have changed; reconciliation required"
    ))
}

fn parse_status(text: &str) -> Result<Vec<NativeSession>> {
    let mut lines = text.lines();
    let header = lines
        .next()
        .ok_or_else(|| Error::State("empty VHCI status".into()))?;
    if header.split_whitespace().collect::<Vec<_>>().join(" ") != HEADER {
        return Err(Error::State("unsupported VHCI status header".into()));
    }
    let mut result = Vec::new();
    let mut seen = HashSet::new();
    for line in lines {
        if result.len() >= 256 {
            return Err(Error::State("VHCI row limit exceeded".into()));
        }
        let columns = line.split_whitespace().collect::<Vec<_>>();
        if columns.len() != 7 || !matches!(columns[0], "hs" | "ss") {
            return Err(Error::State("malformed VHCI row".into()));
        }
        let parse = |field: &str| {
            field
                .parse::<u32>()
                .map_err(|_| Error::State("invalid VHCI numeric field".into()))
        };
        let raw_port = parse(columns[1])?;
        let port = u8::try_from(raw_port)
            .map_err(|_| Error::Unsupported("VHCI port exceeds usbip utility's u8 API".into()))?;
        if !seen.insert(port) {
            return Err(Error::State("duplicate VHCI port".into()));
        }
        let status = parse(columns[2])?;
        if !(4..=7).contains(&status) {
            return Err(Error::State("unknown VHCI status".into()));
        }
        let speed = parse(columns[3])?;
        let remote = u32::from_str_radix(columns[4], 16)
            .map_err(|_| Error::State("invalid remote device ID".into()))?;
        let _historical_fd = columns[5]
            .parse::<u64>()
            .map_err(|_| Error::State("invalid VHCI socket field".into()))?;
        let local = if status == USED {
            if !(1..=6).contains(&speed) || remote == 0 {
                return Err(Error::State("used port has invalid identity/speed".into()));
            }
            Some(
                BusId::new(columns[6])
                    .map_err(|_| Error::State("invalid local USB bus ID".into()))?,
            )
        } else {
            if columns[6] != "0-0" {
                return Err(Error::State(
                    "inactive port has unexpected local identity".into(),
                ));
            }
            None
        };
        result.push(NativeSession {
            port: VhciPort(port),
            status,
            local_bus_id: local,
            remote_device_id: (status == USED).then_some(remote),
            vendor_id: None,
            product_id: None,
        });
    }
    Ok(result)
}

fn matched_new_sessions<'a>(
    before: &[NativeSession],
    after: &'a [NativeSession],
    remote: &RemoteDevice,
) -> Vec<&'a NativeSession> {
    let expected = (remote.bus_number << 16) | remote.device_number;
    after
        .iter()
        .filter(|session| {
            session.status == USED
                && session.remote_device_id == Some(expected)
                && session.vendor_id == Some(remote.vendor_id)
                && session.product_id == Some(remote.product_id)
                && before
                    .iter()
                    .any(|old| old.port == session.port && old.status == NULL)
        })
        .collect()
}

async fn read_text(path: &Path) -> Result<String> {
    tokio::time::timeout(Duration::from_secs(2), read_text_inner(path))
        .await
        .map_err(|_| Error::Timeout("sysfs/record read exceeded deadline".into()))?
}

async fn read_text_inner(path: &Path) -> Result<String> {
    let file = fs::File::open(path).await?;
    let mut bytes = Vec::new();
    file.take((MAX_ATTRIBUTE + 1) as u64)
        .read_to_end(&mut bytes)
        .await?;
    if bytes.len() > MAX_ATTRIBUTE {
        return Err(Error::State(
            "sysfs/record attribute exceeds size limit".into(),
        ));
    }
    String::from_utf8(bytes).map_err(|_| Error::State("non-UTF8 sysfs/record attribute".into()))
}

async fn read_hex_u16(path: &Path) -> Result<u16> {
    u16::from_str_radix(read_text(path).await?.trim(), 16)
        .map_err(|_| Error::State("invalid USB VID/PID attribute".into()))
}

async fn drain_bounded(mut reader: impl AsyncRead + Unpin) -> io::Result<()> {
    let mut total = 0;
    let mut buffer = [0_u8; 4096];
    loop {
        let count = reader.read(&mut buffer).await?;
        if count == 0 {
            return Ok(());
        }
        total += count;
        if total > MAX_OUTPUT {
            return Err(io::Error::other("native output exceeds byte limit"));
        }
    }
}

async fn kill_and_reap(child: &mut tokio::process::Child) -> String {
    let kill_result = child.start_kill();
    match tokio::time::timeout(Duration::from_secs(2), child.wait()).await {
        Ok(Ok(_)) => match kill_result {
            Ok(()) => "child killed and reaped".into(),
            Err(error) => format!("child reaped (kill reported {error})"),
        },
        Ok(Err(error)) => format!("child cleanup could not be verified: {error}; reconciliation required"),
        Err(_) => "child termination/reaping deadline exceeded; kernel operation may still complete; reconciliation required".into(),
    }
}

fn same_identity(left: &NativeSession, right: &NativeSession) -> bool {
    left.port == right.port
        && left.local_bus_id.is_some()
        && left.remote_device_id.is_some()
        && left.local_bus_id == right.local_bus_id
        && left.remote_device_id == right.remote_device_id
        && left.vendor_id == right.vendor_id
        && left.product_id == right.product_id
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use rustusb_core::Host;
    use std::{
        num::NonZeroU16,
        os::unix::fs::{PermissionsExt, symlink},
    };
    use tempfile::TempDir;
    use tokio::io::AsyncWriteExt;

    const EMPTY: &str = "hub port sta spd dev sockfd local_busid\nhs 0000 004 000 00000000 000000 0-0\nss 0001 004 000 00000000 000000 0-0\n";
    const ATTACHED: &str = "hub port sta spd dev sockfd local_busid\nhs 0000 006 003 00010002 000007 9-1\nss 0001 004 000 00000000 000000 0-0\n";

    struct Fixture {
        directory: TempDir,
        backend: NativeBackend,
    }
    impl Fixture {
        fn new(body: &str) -> Result<Self> {
            let directory = tempfile::tempdir()?;
            let root = directory.path();
            std::fs::create_dir_all(root.join("devices/1-2"))?;
            std::fs::create_dir_all(root.join("devices/9-1"))?;
            std::fs::write(root.join("devices/9-1/idVendor"), "1234\n")?;
            std::fs::write(root.join("devices/9-1/idProduct"), "abcd\n")?;
            std::fs::create_dir_all(root.join("platform/vhci_hcd.0"))?;
            std::fs::write(root.join("platform/vhci_hcd.0/nports"), "2\n")?;
            std::fs::write(root.join("platform/vhci_hcd.0/status"), EMPTY)?;
            std::fs::create_dir_all(root.join("records"))?;
            let tool = root.join("usbip");
            std::fs::write(
                &tool,
                format!(
                    "#!/bin/sh\nBASE=${{0%/*}}\nprintf '%s\\n' \"$@\" >\"$BASE/argv\"\nprintf '%s\\n' \"$$\" >\"$BASE/pid\"\n{body}\n"
                ),
            )?;
            std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o700))?;
            let backend = NativeBackend {
                configuration: Arc::new(Configuration {
                    usbip: tool,
                    devices: root.join("devices"),
                    platform: root.join("platform"),
                    records: root.join("records"),
                    command_timeout: Duration::from_millis(200),
                    settle_timeout: Duration::from_millis(150),
                    poll_interval: Duration::from_millis(5),
                    require_root_tool: false,
                }),
                mutation: Arc::new(Mutex::new(())),
            };
            Ok(Self { directory, backend })
        }
        fn path(&self, name: &str) -> PathBuf {
            self.directory.path().join(name)
        }
        fn binding(&self, driver: &str, status: u32) -> Result<()> {
            symlink(driver, self.path("devices/1-2/driver"))?;
            std::fs::write(self.path("devices/1-2/usbip_status"), status.to_string())?;
            Ok(())
        }
    }

    async fn remote_peer() -> Result<(Peer, tokio::task::JoinHandle<io::Result<()>>)> {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await?;
        let port = NonZeroU16::new(listener.local_addr()?.port())
            .ok_or_else(|| Error::State("test listener returned zero port".into()))?;
        let task = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await?;
            let mut request = [0_u8; 8];
            stream.read_exact(&mut request).await?;
            assert_eq!(request, rustusb_protocol::DEVLIST_REQUEST);
            let mut reply = vec![1, 0x11, 0, 5, 0, 0, 0, 0];
            reply.extend(1_u32.to_be_bytes());
            let mut device = [0_u8; 312];
            device[..4].copy_from_slice(b"/usb");
            device[256..259].copy_from_slice(b"1-2");
            device[288..292].copy_from_slice(&1_u32.to_be_bytes());
            device[292..296].copy_from_slice(&2_u32.to_be_bytes());
            device[296..300].copy_from_slice(&3_u32.to_be_bytes());
            device[300..302].copy_from_slice(&0x1234_u16.to_be_bytes());
            device[302..304].copy_from_slice(&0xabcd_u16.to_be_bytes());
            reply.extend(device);
            // Exercise a fragmented response rather than treating TCP as messages.
            for chunk in reply.chunks(17) {
                stream.write_all(chunk).await?;
            }
            Ok(())
        });
        Ok((
            Peer {
                host: Host::new("127.0.0.1")?,
                port,
            },
            task,
        ))
    }

    #[test]
    fn status_is_strict_and_does_not_call_enumerating_ports_connected() -> Result<()> {
        let sessions = parse_status(ATTACHED)?;
        assert_eq!(sessions[0].remote_device_id, Some(0x10002));
        assert_eq!(sessions[0].local_bus_id, Some(BusId::new("9-1")?));
        let pending = "hub port sta spd dev sockfd local_busid\nhs 0 5 0 00000000 0000 0-0\n";
        assert_eq!(parse_status(pending)?[0].local_bus_id, None);
        for text in [
            "",
            "port status\n",
            "hub port sta spd dev sockfd local_busid\nhs 256 4 0 0 0 0-0\n",
            "hub port sta spd dev sockfd local_busid\nhs 0 6 3 10002 1 ../x\n",
            "hub port sta spd dev sockfd local_busid\nhs 0 8 0 0 0 0-0\n",
            "hub port sta spd dev sockfd local_busid\nhs 0 4 0 0 0 0-0\nhs 0 4 0 0 0 0-0\n",
        ] {
            assert!(parse_status(text).is_err(), "{text}");
        }
        Ok(())
    }

    #[tokio::test]
    async fn snapshots_cover_every_status_attribute_and_reject_missing_rows() -> Result<()> {
        let fixture = Fixture::new("exit 0")?;
        let owner = fixture.path("platform/vhci_hcd.0");
        std::fs::write(
            owner.join("status"),
            "hub port sta spd dev sockfd local_busid\nhs 0 4 0 0 0 0-0\n",
        )?;
        std::fs::write(
            owner.join("status.1"),
            "hub port sta spd dev sockfd local_busid\nss 1 4 0 0 0 0-0\n",
        )?;
        assert_eq!(fixture.backend.port_states().await?.len(), 2);
        assert!(fixture.backend.sessions().await?.is_empty());
        std::fs::remove_file(owner.join("status.1"))?;
        assert!(fixture.backend.port_states().await.is_err());
        std::fs::write(
            owner.join("status.1"),
            "hub port sta spd dev sockfd local_busid\nss 0 4 0 0 0 0-0\n",
        )?;
        assert!(fixture.backend.port_states().await.is_err());
        Ok(())
    }

    #[tokio::test]
    async fn export_and_unexport_require_observed_driver_changes() -> Result<()> {
        let fixture = Fixture::new(
            "if [ \"$1\" = bind ]; then\nln -s usbip-host \"$BASE/devices/1-2/driver\"\nprintf 1 >\"$BASE/devices/1-2/usbip_status\"\nelse\nrm \"$BASE/devices/1-2/driver\"\nln -s usb \"$BASE/devices/1-2/driver\"\nfi",
        )?;
        fixture.backend.export(BusId::new("1-2")?).await?;
        let argv = std::fs::read_to_string(fixture.path("argv"))?;
        assert_eq!(argv, "bind\n--busid\n1-2\n");
        assert_eq!(
            fixture
                .backend
                .device_binding(&BusId::new("1-2")?)
                .await?
                .status,
            Some(1)
        );
        fixture.backend.unexport(BusId::new("1-2")?).await?;
        assert_eq!(
            fixture
                .backend
                .device_binding(&BusId::new("1-2")?)
                .await?
                .driver
                .as_deref(),
            Some("usb")
        );
        // A lying successful executable does not prove a kernel mutation.
        let unchanged = Fixture::new("exit 0")?;
        assert!(matches!(
            unchanged.backend.export(BusId::new("1-2")?).await,
            Err(Error::Timeout(_))
        ));
        Ok(())
    }

    #[tokio::test]
    async fn failed_tool_can_leave_exported_state_and_busy_export_is_rejected() -> Result<()> {
        let fixture = Fixture::new(
            "ln -s usbip-host \"$BASE/devices/1-2/driver\"\nprintf 1 >\"$BASE/devices/1-2/usbip_status\"\nexit 1",
        )?;
        let result = fixture.backend.export(BusId::new("1-2")?).await;
        assert!(
            matches!(result, Err(Error::Command(ref message)) if message.contains("reconciliation required") && message.contains("usbip-host"))
        );
        assert_eq!(
            fixture
                .backend
                .device_binding(&BusId::new("1-2")?)
                .await?
                .status,
            Some(1)
        );
        let busy = Fixture::new("exit 99")?;
        busy.binding("usbip-host", 2)?;
        assert!(busy.backend.unexport(BusId::new("1-2")?).await.is_err());
        assert!(!busy.path("argv").exists());
        Ok(())
    }

    #[tokio::test]
    async fn subprocess_timeout_is_killed_and_reaped_and_output_is_bounded() -> Result<()> {
        let fixture = Fixture::new("exec /bin/sleep 5")?;
        let result = fixture.backend.run_tool(&["bind".into()]).await;
        assert!(matches!(result, Err(Error::Timeout(_))));
        let pid = std::fs::read_to_string(fixture.path("pid"))?;
        assert!(!PathBuf::from(format!("/proc/{}", pid.trim())).exists());
        let noisy = Fixture::new("exec /usr/bin/head -c 1000000 /dev/zero")?;
        let result = noisy.backend.run_tool(&["bind".into()]).await;
        assert!(
            matches!(result, Err(Error::Command(ref message)) if message.contains("byte limit"))
        );
        Ok(())
    }

    #[tokio::test]
    async fn attach_claims_only_verified_session_and_failure_does_not_hide_side_effects()
    -> Result<()> {
        let body = format!(
            "cat >\"$BASE/platform/vhci_hcd.0/status\" <<'STATE'\n{ATTACHED}STATE\nprintf '%s %s %s\\n' \"$5\" \"$2\" \"$7\" >\"$BASE/records/port0\""
        );
        let fixture = Fixture::new(&body)?;
        let (peer, server) = remote_peer().await?;
        let session = fixture
            .backend
            .attach_session(peer.clone(), BusId::new("1-2")?)
            .await?;
        assert_eq!(session.port, VhciPort(0));
        assert_eq!(session.vendor_id, Some(0x1234));
        assert_eq!(
            std::fs::read_to_string(fixture.path("argv"))?,
            format!(
                "--tcp-port\n{}\nattach\n--remote\n127.0.0.1\n--busid\n1-2\n",
                peer.port
            )
        );
        server.await.map_err(|e| Error::State(e.to_string()))??;
        let failed = Fixture::new(&format!("{body}\nexit 1"))?;
        let (peer, server) = remote_peer().await?;
        let result = failed
            .backend
            .attach_session(peer, BusId::new("1-2")?)
            .await;
        assert!(
            matches!(result, Err(Error::Command(ref message)) if message.contains("reconciliation required"))
        );
        assert_eq!(failed.backend.sessions().await?.len(), 1);
        server.await.map_err(|e| Error::State(e.to_string()))??;
        Ok(())
    }

    #[tokio::test]
    async fn attach_does_not_claim_missing_record_wrong_metadata_or_existing_port() -> Result<()> {
        let body =
            format!("cat >\"$BASE/platform/vhci_hcd.0/status\" <<'STATE'\n{ATTACHED}STATE\n");
        let fixture = Fixture::new(&body)?;
        let (peer, server) = remote_peer().await?;
        assert!(
            fixture
                .backend
                .attach_session(peer, BusId::new("1-2")?)
                .await
                .is_err()
        );
        server.await.map_err(|e| Error::State(e.to_string()))??;
        let mismatch = Fixture::new(&body)?;
        std::fs::write(mismatch.path("devices/9-1/idVendor"), "5678")?;
        let (peer, server) = remote_peer().await?;
        assert!(matches!(
            mismatch
                .backend
                .attach_session(peer, BusId::new("1-2")?)
                .await,
            Err(Error::Timeout(_))
        ));
        server.await.map_err(|e| Error::State(e.to_string()))??;
        let existing = Fixture::new("exit 0")?;
        std::fs::write(existing.path("platform/vhci_hcd.0/status"), ATTACHED)?;
        let (peer, server) = remote_peer().await?;
        assert!(
            existing
                .backend
                .attach_session(peer, BusId::new("1-2")?)
                .await
                .is_err()
        );
        server.await.map_err(|e| Error::State(e.to_string()))??;
        Ok(())
    }

    #[tokio::test]
    async fn detach_is_idempotent_and_waits_for_actual_kernel_release() -> Result<()> {
        let body = format!("cat >\"$BASE/platform/vhci_hcd.0/status\" <<'STATE'\n{EMPTY}STATE\n");
        let fixture = Fixture::new(&body)?;
        std::fs::write(fixture.path("platform/vhci_hcd.0/status"), ATTACHED)?;
        fixture.backend.detach(VhciPort(0)).await?;
        assert_eq!(
            std::fs::read_to_string(fixture.path("argv"))?,
            "detach\n--port\n0\n"
        );
        fixture.backend.detach(VhciPort(0)).await?;
        let unchanged = Fixture::new("exit 0")?;
        std::fs::write(unchanged.path("platform/vhci_hcd.0/status"), ATTACHED)?;
        assert!(matches!(
            unchanged.backend.detach(VhciPort(0)).await,
            Err(Error::Timeout(_))
        ));
        Ok(())
    }
    #[tokio::test]
    async fn checked_detach_rejects_a_reused_port_without_running_tool() -> Result<()> {
        let fixture = Fixture::new("exit 99")?;
        std::fs::write(fixture.path("platform/vhci_hcd.0/status"), ATTACHED)?;
        let mut expected = fixture.backend.sessions().await?.remove(0);
        expected.remote_device_id = Some(0x20003);
        assert!(matches!(
            fixture.backend.detach_session(&expected).await,
            Err(Error::Permission(_))
        ));
        assert!(!fixture.path("argv").exists());
        std::fs::write(fixture.path("platform/vhci_hcd.0/status"), EMPTY)?;
        fixture.backend.detach_session(&expected).await?;
        assert!(!fixture.path("argv").exists());
        Ok(())
    }

    #[tokio::test]
    async fn native_record_limit_is_rejected_before_network_or_kernel_mutations() -> Result<()> {
        let fixture = Fixture::new("exit 99")?;
        let host = format!("{}.{}.example", "a".repeat(63), "b".repeat(63));
        let peer = Peer {
            host: Host::new(host)?,
            port: NonZeroU16::new(3240).ok_or_else(|| Error::State("zero test port".into()))?,
        };
        assert!(matches!(
            fixture
                .backend
                .attach_session(peer, BusId::new("1-2")?)
                .await,
            Err(Error::InvalidInput(_))
        ));
        assert!(!fixture.path("argv").exists());
        Ok(())
    }

    #[tokio::test]
    async fn dropping_future_terminates_child_but_never_claims_kernel_rollback() -> Result<()> {
        let fixture = Fixture::new("exec /bin/sleep 5")?;
        let operation = fixture.backend.run_tool(&[]);
        assert!(
            tokio::time::timeout(Duration::from_millis(50), operation)
                .await
                .is_err()
        );
        let pid = std::fs::read_to_string(fixture.path("pid"))?;
        let process = PathBuf::from(format!("/proc/{}", pid.trim()));
        let deadline = Instant::now() + Duration::from_secs(1);
        while process.exists() && Instant::now() < deadline {
            sleep(Duration::from_millis(5)).await;
        }
        assert!(!process.exists());
        Ok(())
    }
    #[tokio::test]
    async fn executable_verification_rejects_writable_configured_parent_chain() -> Result<()> {
        let fixture = Fixture::new("exit 0")?;
        let mut configuration = Configuration {
            usbip: fixture.path("redirect"),
            devices: fixture.path("devices"),
            platform: fixture.path("platform"),
            records: fixture.path("records"),
            command_timeout: Duration::from_millis(100),
            settle_timeout: Duration::from_millis(100),
            poll_interval: Duration::from_millis(5),
            require_root_tool: true,
        };
        // Target is a normal trusted system executable, but this symlink's
        // configured parent is not trusted for privileged execution.
        symlink("/usr/bin/true", &configuration.usbip)?;
        let backend = NativeBackend {
            configuration: Arc::new(configuration),
            mutation: Arc::new(Mutex::new(())),
        };
        assert!(matches!(
            backend.verify_tool().await,
            Err(Error::Permission(_))
        ));
        configuration = Configuration {
            usbip: PathBuf::from("/usr/bin/true"),
            devices: fixture.path("devices"),
            platform: fixture.path("platform"),
            records: fixture.path("records"),
            command_timeout: Duration::from_millis(100),
            settle_timeout: Duration::from_millis(100),
            poll_interval: Duration::from_millis(5),
            require_root_tool: true,
        };
        let backend = NativeBackend {
            configuration: Arc::new(configuration),
            mutation: Arc::new(Mutex::new(())),
        };
        assert_eq!(
            backend.verify_tool().await?,
            fs::canonicalize("/usr/bin/true").await?
        );
        Ok(())
    }
}

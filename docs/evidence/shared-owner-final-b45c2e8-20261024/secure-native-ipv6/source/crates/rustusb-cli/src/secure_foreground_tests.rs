//! Real loopback mTLS subprocess tests for the actual CLI foreground adapter.
//!
//! Only this cfg(test) module accepts the child environment marker. Credentials
//! use the public memory-DER API: these tests do not exercise trusted file loading,
//! helper authorization, USB, kernel modules, or QEMU. Private DER is temporary;
//! retained diagnostics contain only protocol facts, stdout, and redacted errors.

#![cfg(all(test, target_os = "linux", feature = "rust-backend"))]

use rcgen::{
    BasicConstraints, CertificateParams, ExtendedKeyUsagePurpose, IsCa, Issuer, KeyPair,
    KeyUsagePurpose,
};
use rustusb_core::{DeviceKey, VirtualProfile};
use rustusb_protocol::secure;
use rustusb_service::jobs::LoadedJob;
use rustusb_transport::{
    AccessPolicy, CertificatePin, ClientStream, DerIdentity, LoadedServerConfig, PrincipalRule,
    TlsClient, TlsServer, TrustRoots,
};
use serde_json::{Value, json};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    net::{Ipv4Addr, SocketAddr},
    os::{fd::AsRawFd, unix::fs::OpenOptionsExt},
    path::{Path, PathBuf},
    process::{Child, Command, ExitStatus, Stdio},
    sync::Arc,
    time::{Duration, Instant},
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
};

const CHILD_MARKER: &str = "RUSTUSB_TEST_SECURE_FOREGROUND_CHILD";
const CHILD_TEST: &str = "secure_foreground_tests::secure_foreground_child";
const PIPE_BYTES: usize = 4096;
const CHILD_DEADLINE: Duration = Duration::from_secs(9);
type TestResult<T = ()> = Result<T, Box<dyn std::error::Error + Send + Sync>>;
type Peer = ClientStream<TcpStream>;

fn require(value: bool, message: &str) -> TestResult {
    if value {
        Ok(())
    } else {
        Err(std::io::Error::other(message).into())
    }
}
fn keys() -> Vec<DeviceKey> {
    vec![
        DeviceKey::virtual_profile(VirtualProfile::Hid),
        DeviceKey::virtual_profile(VirtualProfile::CdcAcm),
    ]
}
fn save(path: &Path, value: &Value) -> TestResult {
    fs::write(path, serde_json::to_vec_pretty(value)?)?;
    Ok(())
}

struct Credentials {
    directory: tempfile::TempDir,
    client: TlsClient,
}
impl Credentials {
    fn new() -> TestResult<Self> {
        let directory = tempfile::tempdir()?;
        let mut ca = CertificateParams::new(Vec::<String>::new())?;
        ca.is_ca = IsCa::Ca(BasicConstraints::Unconstrained);
        ca.key_usages = vec![KeyUsagePurpose::KeyCertSign, KeyUsagePurpose::CrlSign];
        let ca_key = KeyPair::generate()?;
        let root = ca.self_signed(&ca_key)?.der().to_vec();
        let issuer = Issuer::new(ca, ca_key);
        let mut materials = Vec::new();
        for (name, usage) in [
            ("server", ExtendedKeyUsagePurpose::ServerAuth),
            ("client", ExtendedKeyUsagePurpose::ClientAuth),
        ] {
            let mut parameters = CertificateParams::new(vec!["foreground.test".into()])?;
            parameters.key_usages = vec![KeyUsagePurpose::DigitalSignature];
            parameters.extended_key_usages = vec![usage];
            let key = KeyPair::generate()?;
            let certificate = parameters.signed_by(&key, &issuer)?.der().to_vec();
            let private = key.serialize_der();
            fs::write(directory.path().join(format!("{name}.der")), &certificate)?;
            // Protected by the private TempDir; never copied to evidence.
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(directory.path().join(format!("{name}-key.der")))?;
            file.write_all(&private)?;
            materials.push((certificate, private));
        }
        fs::write(directory.path().join("ca.der"), &root)?;
        let (client_certificate, client_key) =
            materials.pop().ok_or("client credential missing")?;
        let client = TlsClient::new(
            TrustRoots::new(vec![root])?,
            DerIdentity::new(vec![client_certificate], client_key)?,
        )?;
        Ok(Self { directory, client })
    }
}

/// Invoked as one exact libtest child only; normal test discovery does no work.
#[test]
fn secure_foreground_child() -> TestResult {
    let Some(directory) = std::env::var_os(CHILD_MARKER) else {
        return Ok(());
    };
    require(
        rustix::process::geteuid().as_raw() != 0,
        "child fixture must run without root",
    )?;
    let directory = PathBuf::from(directory);
    let tls = TlsServer::new(
        TrustRoots::new(vec![fs::read(directory.join("ca.der"))?])?,
        DerIdentity::new(
            vec![fs::read(directory.join("server.der"))?],
            fs::read(directory.join("server-key.der"))?,
        )?,
        AccessPolicy::new(vec![PrincipalRule::new(
            "foreground-test",
            vec![CertificatePin::from_der(&fs::read(
                directory.join("client.der"),
            )?)?],
            keys(),
        )?])?,
    )?;
    let loaded = LoadedServerConfig {
        broker_uid: rustix::process::geteuid().as_raw(),
        listen: SocketAddr::from((Ipv4Addr::LOCALHOST, 0)),
        devices: keys(),
        tls,
    };
    // This is the real CLI adapter, not a test reimplementation of its loop.
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(2)
        .enable_all()
        .build()?;
    let result = runtime.block_on(async {
        let signals = super::shutdown_signals::ShutdownSignals::new()?;
        super::foreground::run(
            LoadedJob::VirtualServer(Arc::new(loaded)),
            PathBuf::from("/unused-test-helper.sock"),
            true,
            signals,
        )
        .await
    });
    drop(runtime);
    let code = match result {
        Ok(()) => 0,
        Err(error) => {
            eprintln!("{error}");
            1
        }
    };
    // Avoid libtest's result suffix sharing the JSON stream. The runtime and
    // actual owner have ended before this test-only process exit.
    std::process::exit(code)
}

struct Owner(Child);
impl Drop for Owner {
    fn drop(&mut self) {
        if !matches!(self.0.try_wait(), Ok(Some(_))) {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}
struct Output {
    reader: File,
    buffered: Vec<u8>,
    captured: Vec<u8>,
    events: Vec<Value>,
    eof: bool,
    frozen: bool,
}
impl Output {
    fn read_available(&mut self) -> TestResult {
        require(
            !self.frozen,
            "attempted to drain deliberately blocked stdout",
        )?;
        let mut bytes = [0; 8192];
        loop {
            match self.reader.read(&mut bytes) {
                Ok(0) => {
                    self.eof = true;
                    break;
                }
                Ok(count) => {
                    self.captured.extend_from_slice(&bytes[..count]);
                    self.buffered.extend_from_slice(&bytes[..count]);
                    require(
                        self.captured.len() <= 256 * 1024,
                        "stdout capture exceeded bound",
                    )?;
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(error) => return Err(error.into()),
            }
        }
        Ok(())
    }
    fn parse_line(&mut self) -> TestResult<Option<Value>> {
        while let Some(end) = self.buffered.iter().position(|byte| *byte == b'\n') {
            let line: Vec<_> = self.buffered.drain(..=end).collect();
            let line = std::str::from_utf8(&line)?.trim_end();
            // Exact libtest preamble is a separate line, never stripped from JSON.
            if line.is_empty() || line == "running 1 test" {
                continue;
            }
            require(line.len() < 65536, "event exceeds stdout limit")?;
            let value: Value = serde_json::from_str(line)?;
            require(
                value["schema_version"] == 1,
                "unexpected foreground event schema",
            )?;
            self.events.push(value.clone());
            return Ok(Some(value));
        }
        require(
            self.buffered.len() < 65536,
            "unterminated oversized output line",
        )?;
        Ok(None)
    }
    async fn event(&mut self) -> TestResult<Value> {
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(event) = self.parse_line()? {
                return Ok(event);
            }
            self.read_available()?;
            if let Some(event) = self.parse_line()? {
                return Ok(event);
            }
            require(
                !self.eof && Instant::now() < deadline,
                "foreground stdout ended or timed out",
            )?;
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    }
}
fn control(child: &mut Child, value: Value) -> TestResult {
    let input = child.stdin.as_mut().ok_or("child stdin missing")?;
    serde_json::to_writer(&mut *input, &value)?;
    input.write_all(b"\n")?;
    input.flush()?;
    Ok(())
}
fn resources(snapshot: &Value, pending: bool) -> bool {
    let Some(devices) = snapshot["devices"].as_array() else {
        return false;
    };
    snapshot["schema_version"] == 1
        && devices.len() == 2
        && [("1-1", "hid_keyboard"), ("1-2", "cdc_acm")]
            .into_iter()
            .all(|(bus, profile)| {
                devices
                    .iter()
                    .filter(|device| device["bus_id"] == bus && device["profile"] == profile)
                    .count()
                    == 1
                    && devices
                        .iter()
                        .find(|device| device["bus_id"] == bus)
                        .is_some_and(|device| {
                            device["leased"] == pending
                                && device["configuration"] == u8::from(pending)
                                && device["pending_urbs"] == usize::from(pending)
                                && device["hid_queued_reports"] == 0
                                && device["cdc_queued_bytes"] == 0
                        })
            })
}
async fn connect(client: &TlsClient, address: SocketAddr) -> TestResult<Peer> {
    Ok(tokio::time::timeout(Duration::from_secs(5), async {
        client
            .connect("foreground.test", TcpStream::connect(address).await?)
            .await
            .map_err(std::io::Error::other)
    })
    .await??)
}
fn submit(
    sequence: u32,
    device: u32,
    direction: u32,
    endpoint: u32,
    length: u32,
    setup: [u8; 8],
) -> [u8; 48] {
    let mut frame = [0; 48];
    for (offset, value) in [
        (0, 1),
        (4, sequence),
        (8, device),
        (12, direction),
        (16, endpoint),
        (24, length),
    ] {
        frame[offset..offset + 4].copy_from_slice(&value.to_be_bytes());
    }
    frame[40..].copy_from_slice(&setup);
    frame
}
async fn import_pending(client: &TlsClient, address: SocketAddr) -> TestResult<Vec<Peer>> {
    let catalog = secure::discover(connect(client, address).await?).await?;
    require(
        catalog.len() == 2
            && keys().iter().all(|key| {
                catalog
                    .iter()
                    .filter(|item| &item.device_key == key)
                    .count()
                    == 1
            }),
        "mTLS catalog keys differ",
    )?;
    let mut streams = Vec::new();
    for selected in catalog {
        let (mut peer, device) =
            secure::import_device(connect(client, address).await?, &selected).await?;
        let devid = (device.bus_number << 16) | device.device_number;
        peer.write_all(&submit(1, devid, 0, 0, 0, [0, 9, 1, 0, 0, 0, 0, 0]))
            .await?;
        peer.flush().await?;
        let mut reply = [0; 48];
        tokio::time::timeout(Duration::from_secs(3), peer.read_exact(&mut reply)).await??;
        require(
            reply[..8] == [0, 0, 0, 3, 0, 0, 0, 1] && reply[20..28] == [0; 8],
            "SET_CONFIGURATION failed",
        )?;
        let (endpoint, length) = if selected.device_key == keys()[0] {
            (1, 8)
        } else {
            (2, 64)
        };
        peer.write_all(&submit(2, devid, 1, endpoint, length, [0; 8]))
            .await?;
        peer.flush().await?;
        streams.push(peer);
    }
    Ok(streams)
}
async fn closed(peer: &mut Peer) -> TestResult<String> {
    let mut byte = [0];
    match tokio::time::timeout(Duration::from_secs(2), peer.read(&mut byte)).await? {
        Ok(0) => Ok("eof".into()),
        Err(error)
            if matches!(
                error.kind(),
                std::io::ErrorKind::UnexpectedEof
                    | std::io::ErrorKind::ConnectionReset
                    | std::io::ErrorKind::BrokenPipe
                    | std::io::ErrorKind::ConnectionAborted
            ) =>
        {
            Ok(format!("transport_closed:{:?}", error.kind()))
        }
        result => Err(format!("TLS data connection did not close: {result:?}").into()),
    }
}
async fn blocked_writer(child: &mut Child) -> TestResult<String> {
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        require(
            child.try_wait()?.is_none(),
            "child exited before stdout block observation",
        )?;
        for task in fs::read_dir(format!("/proc/{}/task", child.id()))? {
            let path = task?.path();
            if let Ok(wait) = fs::read_to_string(path.join("wchan"))
                && wait.trim() == "pipe_write"
            {
                return Ok(path
                    .file_name()
                    .ok_or("task id absent")?
                    .to_string_lossy()
                    .into_owned());
            }
        }
        require(
            Instant::now() < deadline,
            "actual stdout writer did not enter pipe_write",
        )?;
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}
async fn wait(child: &mut Child, output: &mut Output, drain: bool) -> TestResult<ExitStatus> {
    let deadline = Instant::now() + CHILD_DEADLINE;
    loop {
        if drain {
            output.read_available()?;
            while output.parse_line()?.is_some() {}
        }
        if let Some(status) = child.try_wait()? {
            if drain {
                output.read_available()?;
                while output.parse_line()?.is_some() {}
                require(
                    output.eof && output.buffered.is_empty(),
                    "ready stdout did not end cleanly",
                )?;
            }
            return Ok(status);
        }
        require(
            Instant::now() < deadline,
            "foreground child did not cooperate before deadline",
        )?;
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}

#[derive(Clone, Copy)]
enum Case {
    ReadyInt,
    ReadyTerm,
    BlockedInt,
    BlockedTerm,
    BlockedDeadline,
}
impl Case {
    fn name(self) -> &'static str {
        match self {
            Self::ReadyInt => "ready-int",
            Self::ReadyTerm => "ready-term",
            Self::BlockedInt => "blocked-int",
            Self::BlockedTerm => "blocked-term",
            Self::BlockedDeadline => "blocked-deadline",
        }
    }
    fn blocked(self) -> bool {
        matches!(
            self,
            Self::BlockedInt | Self::BlockedTerm | Self::BlockedDeadline
        )
    }
    fn signal(self) -> Option<rustix::process::Signal> {
        match self {
            Self::ReadyInt | Self::BlockedInt => Some(rustix::process::Signal::INT),
            Self::ReadyTerm | Self::BlockedTerm => Some(rustix::process::Signal::TERM),
            Self::BlockedDeadline => None,
        }
    }
}

async fn run_case(case: Case, credentials: &Credentials, evidence: &Path) -> TestResult<Value> {
    let directory = evidence.join(case.name());
    fs::create_dir(&directory)?;
    let (read_fd, write_fd) = rustix::pipe::pipe_with(rustix::pipe::PipeFlags::CLOEXEC)?;
    require(
        rustix::pipe::fcntl_setpipe_size(&write_fd, PIPE_BYTES)? == PIPE_BYTES,
        "pipe capacity differs",
    )?;
    let reader = File::from(read_fd);
    let writer = File::from(write_fd);
    let flags = rustix::fs::fcntl_getfl(&reader)?;
    rustix::fs::fcntl_setfl(&reader, flags | rustix::fs::OFlags::NONBLOCK)?;
    let mut child = Owner(
        Command::new(std::env::current_exe()?)
            .args([
                "--exact",
                CHILD_TEST,
                "--nocapture",
                "--quiet",
                "--test-threads=1",
            ])
            .env(CHILD_MARKER, credentials.directory.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::from(writer.try_clone()?))
            .stderr(Stdio::from(File::create(directory.join("stderr.log"))?))
            .spawn()?,
    );
    let mut output = Output {
        reader,
        buffered: Vec::new(),
        captured: Vec::new(),
        events: Vec::new(),
        eof: false,
        frozen: false,
    };
    let result = async {
        let ready = output.event().await?;
        require(ready["event"] == "ready" && ready["transport"] == "mutual_tls" && ready["devices"].as_array().is_some_and(|devices| devices.len() == 2), "secure ready event differs")?;
        let address: SocketAddr = ready["address"].as_str().ok_or("ready address missing")?.parse()?;
        require(address.ip().is_loopback() && address.port() != 0, "fixture listener escaped loopback")?;
        let mut peers = import_pending(&credentials.client, address).await?;
        let mut before = Value::Null;
        for index in 0..100 {
            let id = format!("before-{index}");
            control(&mut child.0, json!({"op":"snapshot","id":id}))?;
            before = output.event().await?;
            require(before["event"] == "snapshot" && before["id"] == id, "snapshot response identity differs")?;
            if resources(&before["snapshot"], true) { break; }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        require(resources(&before["snapshot"], true), "both authenticated imports did not retain pending IN")?;
        for peer in &mut peers {
            let mut byte = [0];
            require(tokio::time::timeout(Duration::from_millis(40), peer.read(&mut byte)).await.is_err(), "pending IN unexpectedly completed or disconnected")?;
        }
        save(&directory.join("before.json"), &before)?;
        require(!rustix::fs::fcntl_getfl(&writer)?.contains(rustix::fs::OFlags::NONBLOCK), "child stdout became nonblocking")?;
        let mut blocked = Value::Null;
        let timing = Instant::now();
        if case.blocked() {
            output.read_available()?;
            require(output.buffered.is_empty() && rustix::io::ioctl_fionread(&output.reader)? == 0, "stdout not empty before synthetic fill")?;
            let mut padding = OpenOptions::new().write(true).custom_flags(rustix::fs::OFlags::NONBLOCK.bits() as i32)
                .open(format!("/proc/self/fd/{}", writer.as_raw_fd()))?;
            let mut filled = 0;
            loop {
                match padding.write(b"#") {
                    Ok(1) => { filled += 1; require(filled <= PIPE_BYTES, "pipe fill exceeded capacity")?; }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => break,
                    result => return Err(format!("unexpected independent pipe fill: {result:?}").into()),
                }
            }
            require(filled == PIPE_BYTES && rustix::io::ioctl_fionread(&output.reader)? == PIPE_BYTES as u64, "stdout fill was not exactly 4096 bytes")?;
            require(rustix::fs::fcntl_getfl(&padding)?.contains(rustix::fs::OFlags::NONBLOCK) && !rustix::fs::fcntl_getfl(&writer)?.contains(rustix::fs::OFlags::NONBLOCK), "independent OFD flags differ")?;
            output.frozen = true;
            control(&mut child.0, json!({"op":"snapshot","id":"blocked-output"}))?;
            let task = blocked_writer(&mut child.0).await?;
            blocked = json!({"capacity":PIPE_BYTES,"synthetic_fill":filled,"blocked_writer_task":task,"stdout_drained_after_fill":false});
        }
        // The child remains our unreaped direct child, so its PID cannot be reused.
        require(child.0.try_wait()?.is_none(), "child ended before requested termination")?;
        let signal_time = Instant::now();
        if let Some(signal) = case.signal() {
            let pid = rustix::process::Pid::from_raw(i32::try_from(child.0.id())?).ok_or("invalid child PID")?;
            rustix::process::kill_process(pid, signal)?;
        }
        // Only ready cases need actual stdout EOF; blocked cases retain a writer
        // and never drain even after process exit.
        drop(writer);
        let status = wait(&mut child.0, &mut output, !case.blocked()).await?;
        let elapsed = if case.signal().is_some() { signal_time.elapsed() } else { timing.elapsed() };
        require(status.code() == Some(if case.signal().is_some() { 0 } else { 1 }), "foreground exit status differs")?;
        if case.signal().is_some() { require(elapsed <= Duration::from_secs(8), "signal cleanup exceeded eight seconds")?; }
        else { require((Duration::from_secs(4)..=Duration::from_secs(7)).contains(&elapsed), "stdout timeout did not obey five-second budget")?; }
        let mut closures = Vec::new();
        for peer in &mut peers { closures.push(closed(peer).await?); }
        let stopped: Vec<_> = output.events.iter().filter(|event| event["event"] == "stopped").collect();
        if case.blocked() {
            require(rustix::io::ioctl_fionread(&output.reader)? == PIPE_BYTES as u64 && stopped.is_empty(), "blocked stdout was drained or terminal snapshot fabricated")?;
        } else {
            require(stopped.len() == 1 && resources(&stopped[0]["snapshot"], false), "actual stopped snapshot did not prove release")?;
        }
        let stderr = fs::read_to_string(directory.join("stderr.log"))?;
        if case.signal().is_none() { require(stderr.contains("server stdout consumer is blocked"), "stdout deadline diagnostic missing")?; }
        Ok::<_, Box<dyn std::error::Error + Send + Sync>>(json!({"case":case.name(),"result":"PASS","exit":status.code(),"elapsed_seconds":elapsed.as_secs_f64(),"mutual_tls":true,"authenticated_imports":2,"pending_in_per_import":1,"data_connection_closure":closures,"blocked":blocked,"backend_cleanup_snapshot_observed":!case.blocked(),"stopped_events":stopped}))
    }.await;
    fs::write(directory.join("stdout-consumed.log"), &output.captured)?;
    save(
        &directory.join("events-consumed.json"),
        &json!(output.events),
    )?;
    match &result {
        Ok(summary) => save(&directory.join("summary.json"), summary)?,
        Err(error) => save(
            &directory.join("summary.json"),
            &json!({"case":case.name(),"result":"FAIL","error":error.to_string(),"stdout_frozen":output.frozen,"actual_child_status":child.0.try_wait()?.map(|status|status.code()),"forced_cleanup_not_success":true}),
        )?,
    }
    result
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn secure_foreground_real_tls_signals_and_stdout_backpressure() -> TestResult {
    require(
        rustix::process::geteuid().as_raw() != 0,
        "run lifecycle tests as an ordinary user",
    )?;
    let credentials = Credentials::new()?;
    let evidence = tempfile::Builder::new()
        .prefix("rustusb-secure-foreground-tests-")
        .tempdir()?
        .keep();
    eprintln!(
        "secure foreground lifecycle evidence: {}",
        evidence.display()
    );
    let mut results = Vec::new();
    for case in [
        Case::ReadyInt,
        Case::ReadyTerm,
        Case::BlockedInt,
        Case::BlockedTerm,
        Case::BlockedDeadline,
    ] {
        match run_case(case, &credentials, &evidence).await {
            Ok(result) => results.push(result),
            Err(error) => {
                save(
                    &evidence.join("summary.json"),
                    &json!({"result":"FAIL","completed":results,"failed_case":case.name(),"error":error.to_string(),"scope":"real mTLS CLI adapter; no trusted loader, helper or USB"}),
                )?;
                return Err(format!("{}; retained evidence {}", error, evidence.display()).into());
            }
        }
    }
    save(
        &evidence.join("summary.json"),
        &json!({"result":"PASS","cases":results,"scope":"five real mTLS CLI adapter cases; no trusted loader, helper or USB; blocked cases prove exit/socket closure only"}),
    )?;
    Ok(())
}

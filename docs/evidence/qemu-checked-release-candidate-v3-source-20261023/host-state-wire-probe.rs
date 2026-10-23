//! Independent, bounded USB/IP host-state race probe for disposable QEMU guests.
//! Uses only std; neither the production protocol nor its codec is imported.
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpStream};
use std::thread;
use std::time::{Duration, Instant};

const WIRE_DEADLINE: Duration = Duration::from_secs(2);
const RPC_DEADLINE: Duration = Duration::from_secs(3);
const A: u32 = 100;
const UNLINK_A: u32 = 101;
const B: u32 = 110;
const UNLINK_B: u32 = 111;
const C: u32 = 120;
const UNLINK_C: u32 = 121;
const GET_CONFIGURATION: u32 = 130;
const GET_INTERFACE: u32 = 131;

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

fn require(condition: bool, message: impl Into<String>) -> io::Result<()> {
    if condition {
        Ok(())
    } else {
        Err(invalid(message))
    }
}

fn remaining(deadline: Instant) -> io::Result<Duration> {
    deadline
        .checked_duration_since(Instant::now())
        .filter(|duration| !duration.is_zero())
        .ok_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "probe operation deadline elapsed"))
}

// Diagnostic output belongs only to this independent guest probe. Preserve the
// original Result, OS error and shared per-frame deadline; never retry I/O.
fn observe_io<T>(phase: &str, operation: impl FnOnce() -> io::Result<T>) -> io::Result<T> {
    eprintln!(
        "{{\"event\":\"host_state_probe_io\",\"phase\":{},\"result\":\"START\"}}",
        json_string(phase)
    );
    let started = Instant::now();
    let result = operation();
    let (status, error, errno) = match &result {
        Ok(_) => ("OK", "null".to_owned(), "null".to_owned()),
        Err(error) => (
            "ERROR",
            json_string(&error.to_string()),
            error
                .raw_os_error()
                .map_or_else(|| "null".to_owned(), |code| code.to_string()),
        ),
    };
    eprintln!(
        "{{\"event\":\"host_state_probe_io\",\"phase\":{},\"result\":\"{status}\",\"elapsed_ns\":{},\"error\":{error},\"raw_os_error\":{errno}}}",
        json_string(phase),
        started.elapsed().as_nanos()
    );
    result
}

fn read_exact_until(
    stream: &mut TcpStream,
    mut bytes: &mut [u8],
    deadline: Instant,
) -> io::Result<()> {
    while !bytes.is_empty() {
        stream.set_read_timeout(Some(remaining(deadline)?))?;
        match stream.read(bytes) {
            Ok(0) => return Err(io::Error::new(io::ErrorKind::UnexpectedEof, "short frame")),
            Ok(count) => bytes = &mut bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn write_all_until(stream: &mut TcpStream, mut bytes: &[u8], deadline: Instant) -> io::Result<()> {
    while !bytes.is_empty() {
        stream.set_write_timeout(Some(remaining(deadline)?))?;
        match stream.write(bytes) {
            Ok(0) => return Err(io::Error::new(io::ErrorKind::WriteZero, "short write")),
            Ok(count) => bytes = &bytes[count..],
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn connect(port: u16) -> io::Result<TcpStream> {
    let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::new(192, 0, 2, 1)), port);
    let stream = TcpStream::connect_timeout(&address, WIRE_DEADLINE)?;
    stream.set_nodelay(true)?;
    stream.set_read_timeout(Some(WIRE_DEADLINE))?;
    stream.set_write_timeout(Some(WIRE_DEADLINE))?;
    Ok(stream)
}

fn u32_at(bytes: &[u8], offset: usize) -> io::Result<u32> {
    let field: [u8; 4] = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| invalid("missing four-byte field"))?
        .try_into()
        .map_err(|_| invalid("malformed four-byte field"))?;
    Ok(u32::from_be_bytes(field))
}

fn require_eof(stream: &mut TcpStream, deadline: Instant) -> io::Result<()> {
    let mut byte = [0; 1];
    loop {
        stream.set_read_timeout(Some(remaining(deadline)?))?;
        match stream.read(&mut byte) {
            Ok(0) => return Ok(()),
            Ok(_) => return Err(invalid("management reply has trailing bytes")),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) => return Err(error),
        }
    }
}

#[derive(Clone, PartialEq)]
struct Remote {
    bus_id: String,
    devid: u32,
    speed: u32,
    vendor: u16,
    product: u16,
    configuration: u8,
    configurations: u8,
    interfaces: u8,
}

fn remote(bytes: &[u8; 312]) -> io::Result<Remote> {
    let bus_field = &bytes[256..288];
    let length = bus_field
        .iter()
        .position(|byte| *byte == 0)
        .ok_or_else(|| invalid("remote bus ID lacks NUL terminator"))?;
    require(
        length > 0
            && bus_field[length..].iter().all(|byte| *byte == 0)
            && bus_field[..length]
                .iter()
                .all(|byte| byte.is_ascii_digit() || *byte == b'-' || *byte == b'.'),
        "remote bus ID is not a bounded decimal USB topology",
    )?;
    let bus_id = std::str::from_utf8(&bus_field[..length])
        .map_err(|_| invalid("remote bus ID is not UTF-8"))?
        .to_owned();
    let bus = u32_at(bytes, 288)?;
    let device = u32_at(bytes, 292)?;
    require(
        (1..=65535).contains(&bus) && (1..=65535).contains(&device),
        "remote bus/address exceeds USB/IP devid",
    )?;
    let result = Remote {
        bus_id,
        devid: (bus << 16) | device,
        speed: u32_at(bytes, 296)?,
        vendor: u16::from_be_bytes([bytes[300], bytes[301]]),
        product: u16::from_be_bytes([bytes[302], bytes[303]]),
        configuration: bytes[309],
        configurations: bytes[310],
        interfaces: bytes[311],
    };
    require(
        result.speed == 2
            && result.vendor == 0x0525
            && result.product == 0xa4a2
            && result.configuration == 1
            && result.configurations == 2
            && result.interfaces == 2,
        "remote is not the expected original full-speed ECM device",
    )?;
    Ok(result)
}

fn list() -> io::Result<Vec<Remote>> {
    let mut stream = observe_io("devlist_connect", || connect(3240))?;
    let deadline = Instant::now() + WIRE_DEADLINE;
    observe_io("devlist_write", || {
        write_all_until(&mut stream, &[1, 0x11, 0x80, 5, 0, 0, 0, 0], deadline)
    })?;
    let mut header = [0; 12];
    observe_io("devlist_header", || {
        read_exact_until(&mut stream, &mut header, deadline)
    })?;
    require(
        header[..8] == [1, 0x11, 0, 5, 0, 0, 0, 0],
        "DEVLIST response header differs",
    )?;
    let count = u32_at(&header, 8)?;
    require(
        count <= 1,
        "DEVLIST exceeds this isolated guest's one-device bound",
    )?;
    let mut devices = Vec::new();
    for _ in 0..count {
        let mut bytes = [0; 312];
        observe_io("devlist_device", || {
            read_exact_until(&mut stream, &mut bytes, deadline)
        })?;
        let device = remote(&bytes)?;
        let mut interfaces = [0; 8];
        observe_io("devlist_interfaces", || {
            read_exact_until(&mut stream, &mut interfaces, deadline)
        })?;
        require(
            interfaces[3] == 0 && interfaces[7] == 0,
            "DEVLIST interface reserved fields differ",
        )?;
        devices.push(device);
    }
    observe_io("devlist_eof", || require_eof(&mut stream, deadline))?;
    Ok(devices)
}

fn import_request(device: &Remote) -> Vec<u8> {
    // remote() validated the only source of this bounded bus ID.
    let mut bytes = vec![1, 0x11, 0x80, 3, 0, 0, 0, 0];
    bytes.extend_from_slice(device.bus_id.as_bytes());
    bytes.resize(40, 0);
    bytes
}

struct Session {
    stream: TcpStream,
    device: Remote,
}

impl Session {
    fn import(expected: Remote) -> io::Result<Self> {
        let mut stream = observe_io("import_connect", || connect(3240))?;
        let deadline = Instant::now() + WIRE_DEADLINE;
        observe_io("import_write", || {
            write_all_until(&mut stream, &import_request(&expected), deadline)
        })?;
        let mut header = [0; 8];
        observe_io("import_header", || {
            read_exact_until(&mut stream, &mut header, deadline)
        })?;
        require(
            header == [1, 0x11, 0, 3, 0, 0, 0, 0],
            "IMPORT did not succeed",
        )?;
        let mut bytes = [0; 312];
        observe_io("import_device", || {
            read_exact_until(&mut stream, &mut bytes, deadline)
        })?;
        let actual = remote(&bytes)?;
        require(
            actual == expected,
            "IMPORT changed the discovered physical identity",
        )?;
        Ok(Self {
            stream,
            device: actual,
        })
    }

    fn send(&mut self, bytes: &[u8]) -> io::Result<()> {
        write_all_until(&mut self.stream, bytes, Instant::now() + WIRE_DEADLINE)
    }

    fn response(&mut self) -> io::Result<Response> {
        let deadline = Instant::now() + WIRE_DEADLINE;
        let mut bytes = [0; 48];
        read_exact_until(&mut self.stream, &mut bytes, deadline)?;
        let command = u32_at(&bytes, 0)?;
        let seq = u32_at(&bytes, 4)?;
        require(
            command == 3 || command == 4,
            "unexpected USB/IP response command",
        )?;
        require(
            bytes[8..20].iter().all(|byte| *byte == 0),
            "USB/IP basic response reserved fields differ",
        )?;
        let status = u32_at(&bytes, 20)? as i32;
        require(status <= 0, "USB/IP response contains a positive errno")?;
        let actual = if command == 3 {
            require(
                bytes[28..48].iter().all(|byte| *byte == 0),
                "non-ISO SUBMIT reserved fields differ",
            )?;
            u32_at(&bytes, 24)?
        } else {
            require(
                bytes[24..48].iter().all(|byte| *byte == 0),
                "UNLINK reserved fields differ",
            )?;
            0
        };
        let inbound_length = match seq {
            C => 64,
            GET_CONFIGURATION | GET_INTERFACE => 1,
            A | B | UNLINK_A | UNLINK_B | UNLINK_C => 0,
            _ => return Err(invalid("response names an unknown probe sequence")),
        };
        require(
            actual <= inbound_length,
            "response actual length exceeds request bound",
        )?;
        let mut data = vec![0; actual as usize];
        read_exact_until(&mut self.stream, &mut data, deadline)?;
        Ok(Response {
            command,
            seq,
            status,
            actual,
            data,
        })
    }

    fn no_reply(&mut self) -> io::Result<()> {
        let deadline = Instant::now() + Duration::from_millis(150);
        let mut byte = [0; 1];
        loop {
            self.stream.set_read_timeout(Some(remaining(deadline)?))?;
            match self.stream.read(&mut byte) {
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
                    ) =>
                {
                    return Ok(());
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) => return Err(error),
                Ok(_) => {
                    return Err(invalid(
                        "held/pending request replied or closed prematurely",
                    ));
                }
            }
        }
    }

    fn get_value(&mut self, seq: u32, setup: [u8; 8], expected: u8) -> io::Result<()> {
        self.send(&submit(seq, self.device.devid, 1, 0, 1, setup))?;
        let reply = self.response()?;
        reply.submit(seq, 0, 1)?;
        require(
            reply.data == [expected],
            "same-connection configuration/alternate readback differs",
        )
    }
}

struct Response {
    command: u32,
    seq: u32,
    status: i32,
    actual: u32,
    data: Vec<u8>,
}

impl Response {
    fn submit(&self, seq: u32, status: i32, actual: u32) -> io::Result<()> {
        require(
            self.command == 3 && self.seq == seq && self.status == status && self.actual == actual,
            format!(
                "SUBMIT expected seq={seq} status={status} actual={actual}; observed command={} seq={} status={} actual={}",
                self.command, self.seq, self.status, self.actual
            ),
        )
    }

    fn unlink(&self, seq: u32, status: i32) -> io::Result<()> {
        require(
            self.command == 4 && self.seq == seq && self.status == status && self.actual == 0,
            format!(
                "UNLINK expected seq={seq} status={status}; observed command={} seq={} status={} actual={}",
                self.command, self.seq, self.status, self.actual
            ),
        )
    }
}

fn basic(command: u32, seq: u32, devid: u32, direction: u32, endpoint: u32) -> Vec<u8> {
    [command, seq, devid, direction, endpoint]
        .into_iter()
        .flat_map(u32::to_be_bytes)
        .collect()
}

fn submit(
    seq: u32,
    devid: u32,
    direction: u32,
    endpoint: u32,
    length: u32,
    setup: [u8; 8],
) -> Vec<u8> {
    let mut bytes = basic(1, seq, devid, direction, endpoint);
    for field in [0, length, 0, u32::MAX, 0] {
        bytes.extend_from_slice(&field.to_be_bytes());
    }
    bytes.extend_from_slice(&setup);
    bytes
}

fn unlink(seq: u32, devid: u32, target: u32) -> Vec<u8> {
    let mut bytes = basic(2, seq, devid, 0, 0);
    bytes.extend_from_slice(&target.to_be_bytes());
    bytes.resize(48, 0);
    bytes
}

struct Coordinator {
    stream: TcpStream,
    nonce: String,
}

impl Coordinator {
    fn connect(nonce: &str) -> io::Result<Self> {
        // The second guest can boot before the root coordinator has started.
        // Retry only an unopened startup connection, before any protocol byte
        // or IMPORT. No armed case, request or RPC is retried.
        let deadline = Instant::now() + Duration::from_secs(15);
        let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::new(192, 0, 2, 1)), 5002);
        let stream = loop {
            let timeout = remaining(deadline)?.min(Duration::from_secs(1));
            match TcpStream::connect_timeout(&address, timeout) {
                Ok(stream) => break stream,
                Err(error)
                    if matches!(
                        error.kind(),
                        io::ErrorKind::ConnectionRefused | io::ErrorKind::TimedOut
                    ) =>
                {
                    thread::sleep(remaining(deadline)?.min(Duration::from_millis(50)));
                }
                Err(error) => return Err(error),
            }
        };
        stream.set_nodelay(true)?;
        let mut result = Self {
            stream,
            nonce: nonce.to_owned(),
        };
        result.receive("READY", Instant::now() + RPC_DEADLINE)?;
        Ok(result)
    }

    fn receive(&mut self, expected: &str, deadline: Instant) -> io::Result<()> {
        let expected = format!("{expected} {}\n", self.nonce);
        let mut line = Vec::new();
        loop {
            require(line.len() < 128, "coordinator line exceeds 128 bytes")?;
            let mut byte = [0; 1];
            read_exact_until(&mut self.stream, &mut byte, deadline)?;
            line.push(byte[0]);
            if byte[0] == b'\n' {
                break;
            }
            require(
                byte[0].is_ascii_graphic() || byte[0] == b' ',
                "coordinator line contains invalid ASCII",
            )?;
        }
        require(
            line == expected.as_bytes(),
            "coordinator response command/nonce differs",
        )
    }

    fn rpc(&mut self, command: &str) -> io::Result<()> {
        let deadline = Instant::now() + RPC_DEADLINE;
        write_all_until(
            &mut self.stream,
            format!("{command} {}\n", self.nonce).as_bytes(),
            deadline,
        )?;
        self.receive(if command == "ARM" { "ARMED" } else { command }, deadline)
    }
}

fn ordinary_identity() -> io::Result<()> {
    let status = std::fs::read_to_string("/proc/self/status")?;
    let line = |prefix: &str| -> io::Result<&str> {
        status
            .lines()
            .find_map(|line| line.strip_prefix(prefix))
            .ok_or_else(|| invalid("process identity field missing"))
    };
    let uids: Vec<_> = line("Uid:")?.split_ascii_whitespace().collect();
    require(
        uids == ["1000", "1000", "1000", "1000"],
        "probe must run with all UIDs 1000",
    )?;
    for prefix in ["CapPrm:", "CapEff:", "CapAmb:"] {
        require(
            line(prefix)?.trim() == "0000000000000000",
            "probe must have no permitted/effective/ambient capabilities",
        )?;
    }
    Ok(())
}

struct Probe {
    operation: String,
    mode: String,
    nonce: String,
    stage: &'static str,
    cases: usize,
    started: Instant,
}

impl Probe {
    fn passed(&mut self, case: &'static str) {
        self.cases += 1;
        let observed = match case {
            "queued_cancel_worker_witness" => ",\"unlink_status\":-104",
            "active_completion_wins" if self.operation == "clear" => {
                ",\"submit_status\":-32,\"actual_length\":0,\"unlink_status\":0"
            }
            "active_completion_wins" if self.operation == "release" => {
                ",\"submit_status\":-22,\"actual_length\":0,\"unlink_status\":0"
            }
            "active_completion_wins" => {
                ",\"submit_status\":0,\"actual_length\":0,\"unlink_status\":0"
            }
            "new_empty_endpoint_map" => ",\"status\":-32,\"actual_length\":0",
            "clear_error_preserves_pending_endpoint" => ",\"pending\":true,\"cancel_status\":-104",
            "same_connection_health" if self.operation == "clear" => {
                ",\"configuration\":1,\"alternate\":1"
            }
            "same_connection_health" => ",\"configuration\":1,\"alternate\":0",
            "closing_hidden_and_busy" => ",\"import_status\":2,\"devlist_count\":0",
            _ => "",
        };
        println!(
            "{{\"event\":\"host_state_probe_case\",\"operation\":\"{}\",\"mode\":\"{}\",\"nonce\":\"{}\",\"case\":\"{case}\",\"result\":\"PASS\",\"elapsed_ns\":{}{observed}}}",
            self.operation,
            self.mode,
            self.nonce,
            self.started.elapsed().as_nanos()
        );
    }

    fn special(&self, seq: u32, devid: u32, second: bool) -> Vec<u8> {
        let setup = match self.operation.as_str() {
            "configuration" => [0, 9, if second { 2 } else { 1 }, 0, 0, 0, 0, 0],
            "release" => [0, 9, if second { 1 } else { 2 }, 0, 0, 0, 0, 0],
            "alternate" => [1, 11, u8::from(second), 0, 1, 0, 0, 0],
            // Both A and B address the same real ECM IN endpoint.
            _ => [2, 1, 0, 0, 0x82, 0, 0, 0],
        };
        submit(seq, devid, 0, 0, 0, setup)
    }

    fn run(&mut self) -> io::Result<()> {
        self.stage = "ordinary_identity";
        ordinary_identity()?;
        self.stage = "coordinator_ready";
        let mut coordinator = Coordinator::connect(&self.nonce)?;
        self.stage = "discovery_list";
        let mut devices = list()?;
        require(
            devices.len() == 1,
            "expected exactly one available ECM device",
        )?;
        let expected = devices
            .pop()
            .ok_or_else(|| invalid("ECM discovery was empty"))?;
        self.stage = "discovery_import";
        let mut session = Session::import(expected.clone())?;
        self.passed("import_identity");
        self.stage = "arm";
        coordinator.rpc("ARM")?;
        session.send(&self.special(A, session.device.devid, false))?;
        self.stage = "syscall_entry_held";
        coordinator.rpc("HELD")?;
        session.no_reply()?;
        self.passed("syscall_entry_held");

        if self.mode == "unlink" {
            self.unlink_case(&mut coordinator, &mut session)?;
            self.stage = "close_completed_wire";
            session.stream.shutdown(Shutdown::Both)?;
            drop(session);
        } else {
            self.stage = "queued_request_accepted";
            session.send(&self.special(B, session.device.devid, true))?;
            coordinator.rpc("ACCEPTED")?;
            session.no_reply()?;
            self.passed("queued_request_accepted");
            self.stage = "eof_holds_lease";
            session.stream.shutdown(Shutdown::Both)?;
            drop(session);
            coordinator.rpc("CLOSED")?;
            self.passed("eof_holds_lease");
            self.stage = "closing_hidden_and_busy";
            require(
                list()?.is_empty(),
                "closing ECM slot leaked through DEVLIST",
            )?;
            let mut busy = connect(3240)?;
            let deadline = Instant::now() + WIRE_DEADLINE;
            write_all_until(&mut busy, &import_request(&expected), deadline)?;
            let mut header = [0; 8];
            read_exact_until(&mut busy, &mut header, deadline)?;
            require(
                header == [1, 0x11, 0, 3, 0, 0, 0, 2],
                "second IMPORT did not explicitly report busy",
            )?;
            require_eof(&mut busy, deadline)?;
            self.passed("closing_hidden_and_busy");
            self.stage = "release_after_eof";
            coordinator.rpc("RELEASE")?;
        }

        self.stage = "original_state_restored";
        coordinator.rpc("RESTORED")?;
        let restored = list()?;
        require(
            restored == [expected],
            "restored DEVLIST does not expose the original identity",
        )?;
        self.passed("original_state_restored");
        self.stage = "bounded_server_shutdown";
        coordinator.rpc("DONE")?;
        self.passed("bounded_server_shutdown");
        println!(
            "{{\"event\":\"host_state_probe_result\",\"operation\":\"{}\",\"mode\":\"{}\",\"nonce\":\"{}\",\"result\":\"PASS\",\"cases\":{},\"elapsed_ns\":{}}}",
            self.operation,
            self.mode,
            self.nonce,
            self.cases,
            self.started.elapsed().as_nanos()
        );
        Ok(())
    }

    fn unlink_case(
        &mut self,
        coordinator: &mut Coordinator,
        session: &mut Session,
    ) -> io::Result<()> {
        self.stage = "queued_cancel_worker_witness";
        let mut frames = unlink(UNLINK_A, session.device.devid, A);
        frames.extend_from_slice(&self.special(B, session.device.devid, true));
        frames.extend_from_slice(&unlink(UNLINK_B, session.device.devid, B));
        session.send(&frames)?;
        session.response()?.unlink(UNLINK_B, -104)?;
        session.no_reply()?;
        self.passed("queued_cancel_worker_witness");

        self.stage = "dependent_request_waits";
        session.send(&submit(C, session.device.devid, 1, 2, 64, [0; 8]))?;
        coordinator.rpc("WITNESS")?;
        session.no_reply()?;
        self.passed("dependent_request_waits");
        self.stage = "active_completion_wins";
        coordinator.rpc("RELEASE")?;
        let clear = self.operation == "clear";
        let mut a_terminal = false;
        let mut a_unlinked = false;
        let mut c_terminal = false;
        for _ in 0..if clear { 2 } else { 3 } {
            let reply = session.response()?;
            match reply.seq {
                A => {
                    require(!a_terminal, "duplicate A terminal response")?;
                    let status = match self.operation.as_str() {
                        "clear" => -32,
                        "release" => -22,
                        _ => 0,
                    };
                    reply.submit(A, status, 0)?;
                    a_terminal = true;
                }
                UNLINK_A => {
                    require(!a_unlinked, "duplicate A UNLINK response")?;
                    reply.unlink(UNLINK_A, 0)?;
                    a_unlinked = true;
                }
                C if !clear => {
                    require(!c_terminal, "duplicate C terminal response")?;
                    reply.submit(C, -32, 0)?;
                    c_terminal = true;
                }
                _ => return Err(invalid("unexpected response after real ioctl release")),
            }
        }
        require(
            a_terminal && a_unlinked,
            "active A completion/UNLINK winner is incomplete",
        )?;
        self.passed("active_completion_wins");
        if clear {
            self.stage = "clear_error_preserves_pending_endpoint";
            // A silent wire alone cannot distinguish queued from USBFS-active.
            // The root coordinator independently witnesses C as the sole
            // pending request, queued=0/inflight=1/budget=384 before cancellation.
            coordinator.rpc("PENDING")?;
            session.no_reply()?;
            session.send(&unlink(UNLINK_C, session.device.devid, C))?;
            session.response()?.unlink(UNLINK_C, -104)?;
            session.no_reply()?;
            self.passed("clear_error_preserves_pending_endpoint");
        } else {
            self.stage = "new_empty_endpoint_map";
            require(c_terminal, "new endpoint map did not retire C with EPIPE")?;
            session.no_reply()?;
            self.passed("new_empty_endpoint_map");
        }
        self.stage = "same_connection_health";
        session.get_value(GET_CONFIGURATION, [0x80, 8, 0, 0, 0, 0, 1, 0], 1)?;
        session.get_value(GET_INTERFACE, [0x81, 10, 0, 0, 1, 0, 1, 0], u8::from(clear))?;
        session.no_reply()?;
        self.passed("same_connection_health");
        Ok(())
    }
}

fn json_string(value: &str) -> String {
    let mut result = String::from("\"");
    for character in value.chars().take(512) {
        match character {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            value if value.is_control() => result.push('?'),
            value => result.push(value),
        }
    }
    result.push('"');
    result
}

fn main() {
    let arguments: Vec<_> = std::env::args().collect();
    let valid = arguments.len() == 4
        && ["configuration", "alternate", "clear", "release"].contains(&arguments[1].as_str())
        && ["unlink", "eof"].contains(&arguments[2].as_str())
        && !arguments[3].is_empty()
        && arguments[3].len() <= 48
        && arguments[3]
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-');
    if !valid {
        eprintln!(
            "{{\"event\":\"host_state_probe_result\",\"result\":\"FAIL\",\"stage\":\"arguments\",\"error\":\"usage: host-state-wire-probe <configuration|alternate|clear|release> <unlink|eof> <safe-nonce-1-to-48>\"}}"
        );
        std::process::exit(1);
    }
    let mut probe = Probe {
        operation: arguments[1].clone(),
        mode: arguments[2].clone(),
        nonce: arguments[3].clone(),
        stage: "initializing",
        cases: 0,
        started: Instant::now(),
    };
    if let Err(error) = probe.run() {
        eprintln!(
            "{{\"event\":\"host_state_probe_result\",\"operation\":\"{}\",\"mode\":\"{}\",\"nonce\":\"{}\",\"result\":\"FAIL\",\"stage\":\"{}\",\"error\":{},\"cases\":{},\"elapsed_ns\":{}}}",
            probe.operation,
            probe.mode,
            probe.nonce,
            probe.stage,
            json_string(&error.to_string()),
            probe.cases,
            probe.started.elapsed().as_nanos()
        );
        std::process::exit(1);
    }
}

//! Independent USB/IP lifecycle checks against QEMU devices through real usbfs.
//! Uses only std and does not import the implementation or its codec.
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
use std::os::unix::net::UnixStream;
use std::thread;
use std::time::{Duration, Instant};

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
fn require(condition: bool, message: &str) -> io::Result<()> {
    if condition {
        Ok(())
    } else {
        Err(invalid(message))
    }
}
fn u32_at(bytes: &[u8], offset: usize) -> io::Result<u32> {
    let part = bytes
        .get(offset..offset + 4)
        .ok_or_else(|| invalid("u32 field missing"))?;
    let array: [u8; 4] = part
        .try_into()
        .map_err(|_| invalid("u32 field malformed"))?;
    Ok(u32::from_be_bytes(array))
}
fn connect(address: SocketAddr) -> io::Result<TcpStream> {
    let stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    stream.set_nodelay(true)?;
    Ok(stream)
}
fn import_request(bus_id: &str) -> io::Result<Vec<u8>> {
    require(bus_id.len() < 32, "test bus ID too long")?;
    let mut request = vec![1, 0x11, 0x80, 3, 0, 0, 0, 0];
    request.extend_from_slice(bus_id.as_bytes());
    request.resize(40, 0);
    Ok(request)
}
fn header(command: u32, seq: u32, device: u32, direction: u32, endpoint: u32) -> Vec<u8> {
    [command, seq, device, direction, endpoint]
        .into_iter()
        .flat_map(u32::to_be_bytes)
        .collect()
}
fn submit(
    seq: u32,
    device: u32,
    direction: u32,
    endpoint: u32,
    length: u32,
    setup: [u8; 8],
    payload: &[u8],
) -> Vec<u8> {
    let mut bytes = header(1, seq, device, direction, endpoint);
    for field in [0, length, 0, u32::MAX, 10] {
        bytes.extend_from_slice(&field.to_be_bytes());
    }
    bytes.extend_from_slice(&setup);
    bytes.extend_from_slice(payload);
    bytes
}
fn unlink(seq: u32, device: u32, target: u32) -> Vec<u8> {
    let mut bytes = header(2, seq, device, 0, 0);
    bytes.extend_from_slice(&target.to_be_bytes());
    bytes.resize(48, 0);
    bytes
}
struct Response {
    command: u32,
    seq: u32,
    status: i32,
    actual: u32,
    data: Vec<u8>,
}
fn response(stream: &mut TcpStream, inbound: bool) -> io::Result<Response> {
    let mut bytes = [0; 48];
    stream.read_exact(&mut bytes)?;
    let command = u32_at(&bytes, 0)?;
    require(command == 3 || command == 4, "unexpected reply command")?;
    require(
        bytes[8..20].iter().all(|byte| *byte == 0),
        "reply basic reserved fields must be zero",
    )?;
    let actual = if command == 3 { u32_at(&bytes, 24)? } else { 0 };
    require(actual <= 65_536, "reply length exceeds probe bound")?;
    let mut data = vec![0; if inbound { actual as usize } else { 0 }];
    stream.read_exact(&mut data)?;
    Ok(Response {
        command,
        seq: u32_at(&bytes, 4)?,
        status: u32_at(&bytes, 20)? as i32,
        actual,
        data,
    })
}
fn expect_submit(reply: &Response, seq: u32, status: i32, actual: u32) -> io::Result<()> {
    require(
        reply.command == 3 && reply.seq == seq && reply.status == status && reply.actual == actual,
        "SUBMIT reply fields differ from the expected wire contract",
    )
}
#[derive(Clone)]
struct Remote {
    bus_id: String,
    devid: u32,
    speed: u32,
    vendor: u16,
    product: u16,
}
fn remote(bytes: &[u8; 312]) -> io::Result<Remote> {
    let end = bytes[256..288]
        .iter()
        .position(|byte| *byte == 0)
        .ok_or_else(|| invalid("remote bus ID has no terminator"))?;
    let bus_id = std::str::from_utf8(&bytes[256..256 + end])
        .map_err(|_| invalid("remote bus ID is not UTF-8"))?
        .to_owned();
    require(
        !bus_id.is_empty()
            && bus_id
                .bytes()
                .all(|byte| byte.is_ascii_digit() || byte == b'-' || byte == b'.'),
        "invalid test bus ID",
    )?;
    let bus = u32_at(bytes, 288)?;
    let device = u32_at(bytes, 292)?;
    require(
        bus > 0 && bus <= 65535 && device > 0 && device <= 65535,
        "invalid remote bus/address",
    )?;
    Ok(Remote {
        bus_id,
        devid: (bus << 16) | device,
        speed: u32_at(bytes, 296)?,
        vendor: u16::from_be_bytes([bytes[300], bytes[301]]),
        product: u16::from_be_bytes([bytes[302], bytes[303]]),
    })
}
fn list(address: SocketAddr) -> io::Result<Vec<Remote>> {
    let mut stream = connect(address)?;
    stream.write_all(&[1, 0x11, 0x80, 5, 0, 0, 0, 0])?;
    let mut common = [0; 12];
    stream.read_exact(&mut common)?;
    require(
        common[..8] == [1, 0x11, 0, 5, 0, 0, 0, 0],
        "DEVLIST header differs",
    )?;
    let count = u32_at(&common, 8)?;
    require(
        count <= 2,
        "DEVLIST exceeds this isolated lab's device bound",
    )?;
    let mut devices = Vec::new();
    for _ in 0..count {
        let mut bytes = [0; 312];
        stream.read_exact(&mut bytes)?;
        require(bytes[311] <= 4, "interface list exceeds this lab's bound")?;
        let mut interfaces = vec![0; usize::from(bytes[311]) * 4];
        stream.read_exact(&mut interfaces)?;
        devices.push(remote(&bytes)?);
    }
    Ok(devices)
}
fn available(address: SocketAddr) -> io::Result<Vec<Remote>> {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Ok(devices) = list(address) {
            if devices.len() == 2
                && devices.iter().any(|d| d.vendor == 0x0627 && d.product == 1)
                && devices
                    .iter()
                    .any(|d| d.vendor == 0x0403 && d.product == 0x6001)
            {
                return Ok(devices);
            }
        }
        require(
            Instant::now() < deadline,
            "two original physical leases did not become available",
        )?;
        thread::sleep(Duration::from_millis(50));
    }
}
struct Session {
    stream: TcpStream,
    device: Remote,
}
impl Session {
    fn import(address: SocketAddr, expected: Remote) -> io::Result<Self> {
        let mut stream = connect(address)?;
        stream.write_all(&import_request(&expected.bus_id)?)?;
        let mut common = [0; 8];
        stream.read_exact(&mut common)?;
        require(common == [1, 0x11, 0, 3, 0, 0, 0, 0], "IMPORT failed")?;
        let mut bytes = [0; 312];
        stream.read_exact(&mut bytes)?;
        let actual = remote(&bytes)?;
        require(
            actual.bus_id == expected.bus_id
                && actual.devid == expected.devid
                && actual.speed == expected.speed
                && actual.vendor == expected.vendor
                && actual.product == expected.product,
            "IMPORT changed physical identity",
        )?;
        Ok(Self {
            stream,
            device: actual,
        })
    }
    fn control(
        &mut self,
        seq: u32,
        direction: u32,
        setup: [u8; 8],
        length: u32,
    ) -> io::Result<Response> {
        self.stream.write_all(&submit(
            seq,
            self.device.devid,
            direction,
            0,
            length,
            setup,
            &[],
        ))?;
        response(&mut self.stream, direction == 1)
    }
    fn configure(&mut self) -> io::Result<()> {
        expect_submit(&self.control(1, 0, [0, 9, 1, 0, 0, 0, 0, 0], 0)?, 1, 0, 0)
    }
    fn interrupt(&mut self, seq: u32) -> io::Result<()> {
        self.stream
            .write_all(&submit(seq, self.device.devid, 1, 1, 8, [0; 8], &[]))
    }
}
fn no_reply(stream: &mut TcpStream, millis: u64) -> io::Result<()> {
    stream.set_read_timeout(Some(Duration::from_millis(millis)))?;
    let mut byte = [0; 1];
    match stream.read(&mut byte) {
        Err(error)
            if matches!(
                error.kind(),
                io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
            ) => {}
        _ => {
            return Err(invalid(
                "pending physical request completed or connection closed unexpectedly",
            ));
        }
    }
    stream.set_read_timeout(Some(Duration::from_secs(2)))
}
fn passed(case: &str) {
    println!("{{\"event\":\"physical_probe_case\",\"case\":\"{case}\",\"result\":\"PASS\"}}");
}
fn lifecycle(address: SocketAddr) -> io::Result<()> {
    let devices = available(address)?;
    let hid = devices
        .iter()
        .find(|d| d.vendor == 0x0627)
        .ok_or_else(|| invalid("HID missing"))?
        .clone();
    {
        let mut session = Session::import(address, hid.clone())?;
        session.configure()?;
        let full = session.control(2, 1, [0x80, 6, 0, 1, 0, 0, 18, 0], 18)?;
        expect_submit(&full, 2, 0, 18)?;
        require(
            full.data[8..12] == [0x27, 6, 1, 0] && full.data[7] == 64 && session.device.speed == 3,
            "QEMU high-speed HID descriptor/EP0 differs",
        )?;
        let one = session.control(3, 1, [0x80, 6, 0, 1, 0, 0, 18, 0], 1)?;
        expect_submit(&one, 3, 0, 1)?;
        require(
            one.data == [18],
            "physical descriptor was not clamped to one byte",
        )?;
        let one_consistent = session.control(6, 1, [0x80, 6, 0, 1, 0, 0, 1, 0], 1)?;
        expect_submit(&one_consistent, 6, 0, 1)?;
        require(
            one_consistent.data == [18],
            "one-byte physical SETUP/URB descriptor differs",
        )?;
        let stalled = session.control(5, 1, [0xc0, 0xff, 0, 0, 0, 0, 1, 0], 1)?;
        expect_submit(&stalled, 5, -32, 0)?;
        passed("real_unsupported_control_stall_and_survival");
        expect_submit(
            &session.control(4, 0, [0x21, 0x0a, 0, 0, 0, 0, 0, 0], 0)?,
            4,
            0,
            0,
        )?;
        passed("real_control_descriptor_and_speed");
        // Send the host-state change and following endpoint request in one
        // TCP write. The IN belongs after configuration, not to retired state.
        let mut pipeline = submit(
            7,
            session.device.devid,
            0,
            0,
            0,
            [0, 9, 1, 0, 0, 0, 0, 0],
            &[],
        );
        pipeline.extend_from_slice(&submit(8, session.device.devid, 1, 1, 8, [0; 8], &[]));
        session.stream.write_all(&pipeline)?;
        expect_submit(&response(&mut session.stream, false)?, 7, 0, 0)?;
        no_reply(&mut session.stream, 150)?;
        session
            .stream
            .write_all(&unlink(9, session.device.devid, 8))?;
        let pipeline_cancelled = response(&mut session.stream, false)?;
        require(
            pipeline_cancelled.command == 4
                && pipeline_cancelled.seq == 9
                && pipeline_cancelled.status == -104,
            "endpoint submitted after pipelined configuration was rejected or retired as old state",
        )?;
        passed("real_configuration_barrier_preserves_following_in");
        session.interrupt(10)?;
        no_reply(&mut session.stream, 200)?;
        session
            .stream
            .write_all(&unlink(11, session.device.devid, 10))?;
        let cancelled = response(&mut session.stream, false)?;
        require(
            cancelled.command == 4 && cancelled.seq == 11 && cancelled.status == -104,
            "physical UNLINK did not confirm actual cancellation",
        )?;
        println!("RUSTUSB_PHYSICAL_PROBE_WAIT_INPUT key=3");
        session.interrupt(20)?;
        let down = response(&mut session.stream, true)?;
        expect_submit(&down, 20, 0, 8)?;
        require(
            down.data == [0, 0, 32, 0, 0, 0, 0, 0],
            "cancelled old request consumed key down or wrong report arrived",
        )?;
        session.interrupt(21)?;
        let up = response(&mut session.stream, true)?;
        expect_submit(&up, 21, 0, 8)?;
        require(up.data == [0; 8], "physical HID release report differs")?;
        session
            .stream
            .write_all(&unlink(22, session.device.devid, 20))?;
        let late = response(&mut session.stream, false)?;
        require(
            late.command == 4 && late.seq == 22 && late.status == 0,
            "late UNLINK for a completed real transfer was not zero",
        )?;
        passed("real_unlink_reaped_no_old_completion_or_input_loss");
        session.interrupt(30)?;
        no_reply(&mut session.stream, 150)?;
        // Drop the actual network connection with a real USB URB outstanding.
    }
    available(address)?;
    passed("real_pending_eof_releases_lease");
    let ftdi = devices
        .into_iter()
        .find(|d| d.vendor == 0x0403)
        .ok_or_else(|| invalid("FTDI missing"))?;
    {
        let mut session = Session::import(address, ftdi)?;
        session.configure()?;
        let descriptor = session.control(2, 1, [0x80, 6, 0, 1, 0, 0, 18, 0], 18)?;
        expect_submit(&descriptor, 2, 0, 18)?;
        require(
            session.device.speed == 2
                && descriptor.data[7] == 8
                && descriptor.data[8..12] == [3, 4, 1, 0x60],
            "QEMU FTDI FullSpeed/EP0/VID/PID differs",
        )?;
        session
            .stream
            .write_all(&submit(3, session.device.devid, 0, 2, 0, [0; 8], &[]))?;
        expect_submit(&response(&mut session.stream, false)?, 3, 0, 0)?;
        passed("real_zero_bulk_out_actual_zero");
        session
            .stream
            .write_all(&submit(4, session.device.devid, 1, 1, 1, [0; 8], &[]))?;
        no_reply(&mut session.stream, 150)?;
        session
            .stream
            .write_all(&unlink(5, session.device.devid, 4))?;
        let cancelled = response(&mut session.stream, false)?;
        require(
            cancelled.command == 4 && cancelled.seq == 5 && cancelled.status == -104,
            "one-byte real bulk IN did not remain pending and cancel",
        )?;
        passed("real_one_byte_bulk_in_pending_cancelled");
        session
            .stream
            .write_all(&submit(6, session.device.devid, 1, 1, 0, [0; 8], &[]))?;
        session
            .stream
            .set_read_timeout(Some(Duration::from_millis(150)))?;
        let mut byte = [0; 1];
        match session.stream.peek(&mut byte) {
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) =>
            {
                session
                    .stream
                    .set_read_timeout(Some(Duration::from_secs(2)))?;
                session
                    .stream
                    .write_all(&unlink(7, session.device.devid, 6))?;
                let cancelled = response(&mut session.stream, false)?;
                require(
                    cancelled.command == 4 && cancelled.seq == 7 && cancelled.status == -104,
                    "zero-byte real bulk IN did not cancel after remaining pending",
                )?;
                println!(
                    "{{\"event\":\"physical_probe_case\",\"case\":\"real_zero_bulk_in_original_length\",\"result\":\"PASS\",\"observation\":\"pending_cancelled\"}}"
                );
            }
            Ok(1) => {
                session
                    .stream
                    .set_read_timeout(Some(Duration::from_secs(2)))?;
                expect_submit(&response(&mut session.stream, true)?, 6, 0, 0)?;
                println!(
                    "{{\"event\":\"physical_probe_case\",\"case\":\"real_zero_bulk_in_original_length\",\"result\":\"PASS\",\"observation\":\"zero_completion\"}}"
                );
            }
            _ => {
                return Err(invalid(
                    "zero-byte bulk IN closed instead of preserving valid USB completion semantics",
                ));
            }
        }
        println!("RUSTUSB_PHYSICAL_PROBE_WAIT_SERIAL bytes=13");
        session
            .stream
            .write_all(&submit(8, session.device.devid, 1, 1, 15, [0; 8], &[]))?;
        let short = response(&mut session.stream, true)?;
        expect_submit(&short, 8, 0, 15)?;
        require(
            short.data[..2] == [0xb1, 0] && short.data[2..] == *b"RUSTUSB-P15!!",
            "15-byte real FTDI IN lost its two status bytes or thirteen fixed input bytes",
        )?;
        passed("real_non_mps_fifteen_byte_bulk_in");
    }
    available(address)?;
    passed("real_ftdi_descriptor_and_speed");
    passed("all_lifecycle_checks");
    Ok(())
}
fn busy(address: SocketAddr, buses: &[String]) -> io::Result<()> {
    require(buses.len() == 2, "busy mode requires HID and FTDI bus IDs")?;
    for bus in buses {
        let mut stream = connect(address)?;
        stream.write_all(&import_request(bus)?)?;
        let mut reply = [0; 8];
        stream.read_exact(&mut reply)?;
        require(
            reply == [1, 0x11, 0, 3, 0, 0, 0, 2],
            "second IMPORT did not return exact busy status two",
        )?;
    }
    passed("both_real_devices_exclusive_busy");
    Ok(())
}
fn helper_denied(device_id: &str) -> io::Result<()> {
    let digest = device_id
        .strip_prefix("usb-")
        .ok_or_else(|| invalid("invalid approved fingerprint"))?;
    require(
        digest.len() == 64 && digest.bytes().all(|byte| byte.is_ascii_hexdigit()),
        "approved fingerprint must be a bounded hexadecimal ID",
    )?;
    let mut stream = UnixStream::connect("/run/rustusb/control.sock")?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    // SO_PEERCRED rejection may be sent and closed before this write. A write
    // failure alone proves nothing: still require the bounded refusal frame
    // and EOF below, then let the guest check its explicit caller-UID error.
    let write_result = stream.write_all(
        format!("{{\"op\":\"open_physical\",\"device_id\":\"{device_id}\"}}\n").as_bytes(),
    );
    let mut reply = Vec::new();
    let mut block = [0; 1024];
    loop {
        let count = stream.read(&mut block)?;
        if count == 0 {
            break;
        }
        require(
            reply.len() + count <= 65_536,
            "helper refusal exceeded frame bound",
        )?;
        reply.extend_from_slice(&block[..count]);
    }
    require(
        reply.last() == Some(&b'\n') && reply.iter().filter(|byte| **byte == b'\n').count() == 1,
        "helper refusal must be exactly one newline-delimited frame followed by EOF",
    )?;
    if let Err(error) = write_result {
        eprintln!("OpenPhysical write failed before explicit refusal: {error}");
    }
    // The guest's jq assertion checks refusal and caller identity explicitly.
    // This ordinary read does not inspect SCM_RIGHTS; ancillary validation is
    // covered by the daemon's separate safe recvmsg tests.
    std::str::from_utf8(&reply).map_err(|_| invalid("helper refusal was not UTF-8"))?;
    io::stdout().write_all(&reply)
}
fn main() -> io::Result<()> {
    let args: Vec<_> = std::env::args().collect();
    require(
        args.len() >= 3,
        "usage: physical-wire-probe <private-address:port> <lifecycle|busy|health> [hid-bus ftdi-bus]; or helper-denied <device-id>",
    )?;
    if args[1] == "helper-denied" {
        require(args.len() == 3, "unexpected helper-denied argument")?;
        return helper_denied(&args[2]);
    }
    let address: SocketAddr = args[1].parse().map_err(|_| invalid("invalid address"))?;
    require(
        address.ip().is_loopback() || address.ip() == IpAddr::V4(Ipv4Addr::new(192, 0, 2, 1)),
        "probe only targets the isolated lab or loopback",
    )?;
    match args[2].as_str() {
        "lifecycle" => {
            require(args.len() == 3, "unexpected lifecycle argument")?;
            lifecycle(address)
        }
        "busy" => busy(address, &args[3..]),
        "health" => {
            require(args.len() == 3, "unexpected health argument")?;
            available(address)?;
            passed("both_real_devices_available");
            Ok(())
        }
        _ => Err(invalid("unknown test mode")),
    }
}

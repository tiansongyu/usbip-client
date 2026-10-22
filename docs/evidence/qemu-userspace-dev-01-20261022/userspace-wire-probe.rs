//! Independent bounded USB/IP wire checks for the isolated QEMU simulator lab.
//! Uses only std and does not import the implementation or its codec.
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, Shutdown, SocketAddr, TcpStream};
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
struct Session {
    stream: TcpStream,
    device: u32,
}
impl Session {
    fn import(address: SocketAddr, bus_id: &str, number: u32) -> io::Result<Self> {
        let mut stream = connect(address)?;
        stream.write_all(&import_request(bus_id)?)?;
        let mut common = [0; 8];
        stream.read_exact(&mut common)?;
        require(
            common == [1, 0x11, 0, 3, 0, 0, 0, 0],
            "IMPORT did not succeed",
        )?;
        let mut device = [0; 312];
        stream.read_exact(&mut device)?;
        require(
            device[256..256 + bus_id.len()] == *bus_id.as_bytes(),
            "IMPORT Bus ID differs",
        )?;
        require(
            u32_at(&device, 288)? == 1
                && u32_at(&device, 292)? == number
                && u32_at(&device, 296)? == 2,
            "virtual bus/dev/FullSpeed metadata differs",
        )?;
        require(
            device[300..304] == [0x12, 0x34, 0, number as u8],
            "virtual VID/PID differs",
        )?;
        Ok(Self {
            stream,
            device: (1 << 16) | number,
        })
    }
    fn configure(&mut self) -> io::Result<()> {
        self.stream.write_all(&submit(
            1,
            self.device,
            0,
            0,
            0,
            [0, 9, 1, 0, 0, 0, 0, 0],
            &[],
        ))?;
        expect_submit(&response(&mut self.stream, false)?, 1, 0, 0)
    }
    fn descriptor(&mut self, seq: u32, length: u32) -> io::Result<Response> {
        self.stream.write_all(&submit(
            seq,
            self.device,
            1,
            0,
            length,
            [0x80, 6, 0, 1, 0, 0, 18, 0],
            &[],
        ))?;
        response(&mut self.stream, true)
    }
}
fn health(address: SocketAddr) -> io::Result<()> {
    let mut stream = connect(address)?;
    stream.write_all(&[1, 0x11, 0x80, 5, 0, 0, 0, 0])?;
    let mut common = [0; 12];
    stream.read_exact(&mut common)?;
    require(
        common[..8] == [1, 0x11, 0, 5, 0, 0, 0, 0] && u32_at(&common, 8)? == 2,
        "DEVLIST did not restore two available profiles",
    )?;
    let mut ids = Vec::new();
    for _ in 0..2 {
        let mut device = [0; 312];
        stream.read_exact(&mut device)?;
        require(device[311] <= 2, "interface count exceeds profile bound")?;
        let mut interfaces = vec![0; usize::from(device[311]) * 4];
        stream.read_exact(&mut interfaces)?;
        let end = device[256..288]
            .iter()
            .position(|byte| *byte == 0)
            .ok_or_else(|| invalid("Bus ID lacks terminator"))?;
        ids.push(device[256..256 + end].to_vec());
    }
    ids.sort();
    require(
        ids == [b"1-1".to_vec(), b"1-2".to_vec()],
        "DEVLIST profile identity differs",
    )
}
fn available(address: SocketAddr) -> io::Result<()> {
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        if health(address).is_ok() {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(invalid("lease did not return after socket cleanup"));
        }
        thread::sleep(Duration::from_millis(50));
    }
}
fn closed(stream: &mut TcpStream) -> io::Result<()> {
    stream.shutdown(Shutdown::Write)?;
    let mut byte = [0; 1];
    match stream.read(&mut byte) {
        Ok(0) => Ok(()),
        Err(error)
            if matches!(
                error.kind(),
                io::ErrorKind::ConnectionReset | io::ErrorKind::ConnectionAborted
            ) =>
        {
            Ok(())
        }
        _ => Err(invalid(
            "malformed request did not cause a bounded connection close",
        )),
    }
}
fn passed(case: &str) {
    println!("{{\"event\":\"probe_case\",\"case\":\"{case}\",\"result\":\"PASS\"}}");
}
fn protocol(address: SocketAddr) -> io::Result<()> {
    available(address)?;
    {
        let mut session = Session::import(address, "1-1", 1)?;
        session.configure()?;
        let reply = session.descriptor(2, 1)?;
        expect_submit(&reply, 2, 0, 1)?;
        require(
            reply.data == [18],
            "descriptor was not clamped to one requested byte",
        )?;
        session.stream.write_all(&submit(
            3,
            session.device,
            1,
            0,
            8,
            [0x81, 6, 0, 0x22, 255, 0, 8, 0],
            &[],
        ))?;
        expect_submit(&response(&mut session.stream, true)?, 3, -32, 0)?;
        passed("control_bound_and_invalid_interface_stall");
        session
            .stream
            .write_all(&submit(10, session.device, 1, 1, 8, [0; 8], &[]))?;
        session
            .stream
            .set_read_timeout(Some(Duration::from_millis(200)))?;
        let mut byte = [0; 1];
        match session.stream.read(&mut byte) {
            Err(error)
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) =>
            {
                ()
            }
            _ => {
                return Err(invalid(
                    "idle interrupt IN completed or closed instead of remaining pending",
                ));
            }
        }
        session
            .stream
            .set_read_timeout(Some(Duration::from_secs(2)))?;
        session.stream.write_all(&unlink(11, session.device, 10))?;
        let reply = response(&mut session.stream, false)?;
        require(
            reply.command == 4 && reply.seq == 11 && reply.status == -104,
            "pending UNLINK was not -ECONNRESET",
        )?;
        let reply = session.descriptor(12, 18)?;
        expect_submit(&reply, 12, 0, 18)?;
        require(
            reply.data.len() == 18 && reply.data[..2] == [18, 1],
            "full device descriptor differs",
        )?;
        session.stream.write_all(&unlink(13, session.device, 12))?;
        let reply = response(&mut session.stream, false)?;
        require(
            reply.command == 4 && reply.seq == 13 && reply.status == 0,
            "completed target UNLINK was not zero",
        )?;
        passed("pending_unlink_suppresses_old_completion");
    }
    available(address)?;
    {
        let mut session = Session::import(address, "1-2", 2)?;
        session.configure()?;
        let payload = b"RUSTUSB-PROBE-01";
        session.stream.write_all(&submit(
            2,
            session.device,
            0,
            2,
            payload.len() as u32,
            [0; 8],
            payload,
        ))?;
        expect_submit(
            &response(&mut session.stream, false)?,
            2,
            0,
            payload.len() as u32,
        )?;
        session
            .stream
            .write_all(&submit(3, session.device, 1, 2, 1, [0; 8], &[]))?;
        let reply = response(&mut session.stream, true)?;
        expect_submit(&reply, 3, 0, 1)?;
        require(reply.data == payload[..1], "one-byte CDC read differs")?;
        session
            .stream
            .write_all(&submit(4, session.device, 1, 2, 64, [0; 8], &[]))?;
        let reply = response(&mut session.stream, true)?;
        expect_submit(&reply, 4, 0, 15)?;
        require(
            reply.data == payload[1..],
            "CDC remaining bytes were lost or reordered",
        )?;
        passed("cdc_bulk_out_and_bounded_in_fifo");
    }
    available(address)?;
    for (name, bytes) in [
        ("unknown_version", vec![0x77, 0x77, 0x80, 5, 0, 0, 0, 0]),
        (
            "submit_before_import",
            submit(1, 0x10001, 1, 1, 8, [0; 8], &[]),
        ),
        ("truncated_handshake", vec![1, 0x11, 0x80]),
    ] {
        let mut stream = connect(address)?;
        stream.write_all(&bytes)?;
        closed(&mut stream)?;
        drop(stream);
        available(address)?;
        passed(name);
    }
    for name in [
        "duplicate_import",
        "oversized_out",
        "invalid_direction",
        "wrong_device",
        "iso_rejected",
        "truncated_data_header",
    ] {
        let mut session = Session::import(address, "1-1", 1)?;
        let mut frame = match name {
            "duplicate_import" => import_request("1-2")?,
            "oversized_out" => submit(1, session.device, 0, 0, 65_537, [0; 8], &[]),
            "invalid_direction" => submit(1, session.device, 2, 1, 8, [0; 8], &[]),
            "wrong_device" => submit(1, 0x10002, 1, 1, 8, [0; 8], &[]),
            "iso_rejected" => submit(1, session.device, 1, 1, 8, [0; 8], &[]),
            "truncated_data_header" => vec![0, 0, 0, 1, 0, 0],
            _ => return Err(invalid("unknown internal test case")),
        };
        if name == "iso_rejected" {
            frame[32..36].copy_from_slice(&1_u32.to_be_bytes());
        }
        session.stream.write_all(&frame)?;
        closed(&mut session.stream)?;
        drop(session);
        available(address)?;
        passed(name);
    }
    passed("all_protocol_checks");
    Ok(())
}
fn busy(address: SocketAddr) -> io::Result<()> {
    for bus in ["1-1", "1-2"] {
        let mut stream = connect(address)?;
        stream.write_all(&import_request(bus)?)?;
        let mut reply = [0; 8];
        stream.read_exact(&mut reply)?;
        require(
            reply == [1, 0x11, 0, 3, 0, 0, 0, 2],
            "occupied virtual profile did not return exact busy status two",
        )?;
    }
    passed("both_profiles_exclusive_busy");
    Ok(())
}
fn main() -> io::Result<()> {
    let arguments: Vec<_> = std::env::args().collect();
    require(
        arguments.len() == 3,
        "usage: userspace-wire-probe <private-address:port> <protocol|busy|health>",
    )?;
    let address: SocketAddr = arguments[1]
        .parse()
        .map_err(|_| invalid("invalid probe address"))?;
    require(
        address.ip().is_loopback() || address.ip() == IpAddr::V4(Ipv4Addr::new(192, 0, 2, 1)),
        "probe only targets loopback or the isolated QEMU lab",
    )?;
    match arguments[2].as_str() {
        "protocol" => protocol(address),
        "busy" => busy(address),
        "health" => {
            health(address)?;
            passed("health_two_profiles");
            Ok(())
        }
        _ => Err(invalid("unknown probe mode")),
    }
}

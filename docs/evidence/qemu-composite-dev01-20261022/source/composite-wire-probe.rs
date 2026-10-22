//! Independent ECM configuration/alternate USB/IP checks and private UDP I/O.
//! Uses only std and does not import the implementation or its codec.
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream, UdpSocket};
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
    require(
        !bus_id.is_empty()
            && bus_id.len() < 32
            && bus_id
                .bytes()
                .all(|byte| byte.is_ascii_digit() || byte == b'-' || byte == b'.'),
        "test bus ID must be bounded decimal USB topology",
    )?;
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
            if devices.len() == 1 && devices[0].vendor == 0x0525 && devices[0].product == 0xa4a2 {
                return Ok(devices);
            }
        }
        require(
            Instant::now() < deadline,
            "the original ECM lease did not become available",
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
    println!("{{\"event\":\"composite_probe_case\",\"case\":\"{case}\",\"result\":\"PASS\"}}");
}
#[derive(Debug, PartialEq)]
struct Interface {
    number: u8,
    alternate: u8,
    class: u8,
    subclass: u8,
    protocol: u8,
    endpoints: Vec<(u8, u8, u16)>,
}
fn configuration(data: &[u8], value: u8) -> io::Result<Vec<Interface>> {
    require(
        data.len() >= 9 && data[0] == 9 && data[1] == 2,
        "configuration header differs",
    )?;
    require(
        usize::from(u16::from_le_bytes([data[2], data[3]])) == data.len()
            && data[4] == 2
            && data[5] == value,
        "configuration length/value/interface count differs",
    )?;
    let mut interfaces: Vec<Interface> = Vec::new();
    let mut offset = 9;
    while offset < data.len() {
        require(offset + 2 <= data.len(), "descriptor header truncated")?;
        let length = usize::from(data[offset]);
        require(
            length >= 2 && offset + length <= data.len(),
            "descriptor length invalid",
        )?;
        let descriptor = &data[offset..offset + length];
        match descriptor[1] {
            4 => {
                require(length == 9, "interface descriptor length differs")?;
                interfaces.push(Interface {
                    number: descriptor[2],
                    alternate: descriptor[3],
                    class: descriptor[5],
                    subclass: descriptor[6],
                    protocol: descriptor[7],
                    endpoints: Vec::new(),
                });
            }
            5 => {
                require(length == 7, "endpoint descriptor length differs")?;
                let interface = interfaces
                    .last_mut()
                    .ok_or_else(|| invalid("endpoint before interface"))?;
                interface.endpoints.push((
                    descriptor[2],
                    descriptor[3] & 3,
                    u16::from_le_bytes([descriptor[4], descriptor[5]]),
                ));
            }
            _ => {}
        }
        offset += length;
    }
    let expected = if value == 1 { 3 } else { 2 };
    require(
        interfaces.len() == expected,
        "alternate descriptor count differs",
    )?;
    let control = &interfaces[0];
    require(
        control.number == 0
            && control.alternate == 0
            && control.class == 2
            && control.subclass == if value == 1 { 6 } else { 2 }
            && control.protocol == if value == 1 { 0 } else { 255 }
            && control.endpoints == [(0x81, 3, 16)],
        "control interface mapping differs",
    )?;
    for interface in &interfaces[1..] {
        require(
            interface.number == 1 && interface.class == 10,
            "data interface mapping differs",
        )?;
        if value == 1 && interface.alternate == 0 {
            require(
                interface.endpoints.is_empty(),
                "ECM alt0 must have no endpoints",
            )?;
        } else {
            require(
                interface.alternate == if value == 1 { 1 } else { 0 }
                    && interface.endpoints == [(0x82, 2, 64), (0x02, 2, 64)],
                "active data endpoints differ",
            )?;
        }
    }
    Ok(interfaces)
}
fn set_config(session: &mut Session, seq: u32, value: u8) -> io::Result<()> {
    expect_submit(
        &session.control(seq, 0, [0, 9, value, 0, 0, 0, 0, 0], 0)?,
        seq,
        0,
        0,
    )
}
fn set_alt(session: &mut Session, seq: u32, alt: u8, status: i32) -> io::Result<()> {
    expect_submit(
        &session.control(seq, 0, [1, 11, alt, 0, 1, 0, 0, 0], 0)?,
        seq,
        status,
        0,
    )
}
fn get_value(session: &mut Session, seq: u32, interface: bool, expected: u8) -> io::Result<()> {
    let setup = if interface {
        [0x81, 10, 0, 0, 1, 0, 1, 0]
    } else {
        [0x80, 8, 0, 0, 0, 0, 1, 0]
    };
    let reply = session.control(seq, 1, setup, 1)?;
    expect_submit(&reply, seq, 0, 1)?;
    require(
        reply.data == [expected],
        "GET_CONFIGURATION/INTERFACE actual value differs",
    )
}
fn bulk(session: &mut Session, seq: u32) -> io::Result<()> {
    session
        .stream
        .write_all(&submit(seq, session.device.devid, 1, 2, 64, [0; 8], &[]))
}
fn cancel(session: &mut Session, seq: u32, target: u32) -> io::Result<()> {
    session
        .stream
        .write_all(&unlink(seq, session.device.devid, target))?;
    let reply = response(&mut session.stream, true)?;
    require(
        reply.command == 4 && reply.seq == seq && reply.status == -104,
        "pending request was not really cancelled",
    )
}
fn lifecycle(address: SocketAddr) -> io::Result<()> {
    let device = available(address)?.remove(0);
    {
        let mut session = Session::import(address, device.clone())?;
        session.configure()?;
        let descriptor = session.control(2, 1, [0x80, 6, 0, 1, 0, 0, 18, 0], 18)?;
        expect_submit(&descriptor, 2, 0, 18)?;
        require(
            descriptor.data[4] == 2
                && descriptor.data[7] == 64
                && descriptor.data[8..12] == [0x25, 5, 0xa2, 0xa4]
                && descriptor.data[17] == 2
                && session.device.speed == 2,
            "QEMU full-speed USB-net device differs",
        )?;
        for (index, value, seq) in [(0, 2, 3), (1, 1, 4)] {
            let descriptor = session.control(seq, 1, [0x80, 6, index, 2, 0, 0, 0, 1], 256)?;
            expect_submit(&descriptor, seq, 0, descriptor.actual)?;
            configuration(&descriptor.data, value)?;
        }
        passed("descriptor_index_is_not_configuration_value");
        get_value(&mut session, 5, false, 1)?;
        get_value(&mut session, 6, true, 0)?;
        let mut bytes = submit(
            10,
            session.device.devid,
            0,
            0,
            0,
            [0, 9, 2, 0, 0, 0, 0, 0],
            &[],
        );
        bytes.extend_from_slice(&submit(11, session.device.devid, 1, 2, 64, [0; 8], &[]));
        session.stream.write_all(&bytes)?;
        expect_submit(&response(&mut session.stream, true)?, 10, 0, 0)?;
        no_reply(&mut session.stream, 150)?;
        cancel(&mut session, 12, 11)?;
        get_value(&mut session, 13, false, 2)?;
        get_value(&mut session, 14, true, 0)?;
        passed("configuration_two_data_alt_zero_is_active");
        let mut bytes = submit(
            15,
            session.device.devid,
            0,
            0,
            0,
            [0, 9, 1, 0, 0, 0, 0, 0],
            &[],
        );
        bytes.extend_from_slice(&submit(16, session.device.devid, 1, 2, 64, [0; 8], &[]));
        session.stream.write_all(&bytes)?;
        expect_submit(&response(&mut session.stream, true)?, 15, 0, 0)?;
        expect_submit(&response(&mut session.stream, true)?, 16, -32, 0)?;
        get_value(&mut session, 17, false, 1)?;
        get_value(&mut session, 18, true, 0)?;
        passed("configuration_barrier_uses_new_empty_endpoint_map");
        let mut bytes = submit(
            20,
            session.device.devid,
            0,
            0,
            0,
            [1, 11, 1, 0, 1, 0, 0, 0],
            &[],
        );
        bytes.extend_from_slice(&submit(21, session.device.devid, 1, 2, 64, [0; 8], &[]));
        session.stream.write_all(&bytes)?;
        expect_submit(&response(&mut session.stream, true)?, 20, 0, 0)?;
        no_reply(&mut session.stream, 150)?;
        cancel(&mut session, 22, 21)?;
        get_value(&mut session, 23, true, 1)?;
        passed("alternate_one_enables_real_pending_bulk_endpoint");
        bulk(&mut session, 30)?;
        bulk(&mut session, 31)?;
        no_reply(&mut session.stream, 150)?;
        let mut bytes = submit(
            32,
            session.device.devid,
            0,
            0,
            0,
            [1, 11, 0, 0, 1, 0, 0, 0],
            &[],
        );
        bytes.extend_from_slice(&submit(33, session.device.devid, 1, 2, 64, [0; 8], &[]));
        session.stream.write_all(&bytes)?;
        let mut observed = Vec::new();
        for _ in 0..4 {
            let reply = response(&mut session.stream, true)?;
            let status = match reply.seq {
                30 | 31 => -108,
                32 => 0,
                33 => -32,
                _ => return Err(invalid("unknown queued-barrier sequence")),
            };
            expect_submit(&reply, reply.seq, status, 0)?;
            require(
                !observed.contains(&reply.seq),
                "duplicate queued-barrier reply",
            )?;
            observed.push(reply.seq);
        }
        get_value(&mut session, 34, true, 0)?;
        passed("alternate_barrier_retires_old_queued_and_active_requests");
        set_alt(&mut session, 35, 2, -32)?;
        get_value(&mut session, 36, true, 0)?;
        passed("nonexistent_alternate_stalls_without_state_change");
        set_alt(&mut session, 40, 1, 0)?;
        let reply = session.control(41, 1, [0xc0, 0xff, 0, 0, 0, 0, 1, 0], 1)?;
        expect_submit(&reply, 41, -32, 0)?;
        get_value(&mut session, 42, true, 1)?;
        passed("real_control_stall_preserves_connection");
        session
            .stream
            .write_all(&submit(43, session.device.devid, 1, 1, 1, [0; 8], &[]))?;
        expect_submit(&response(&mut session.stream, true)?, 43, -32, 0)?;
        get_value(&mut session, 44, false, 1)?;
        passed("real_interrupt_stall_preserves_connection");
        let reply = session.control(45, 0, [2, 1, 0, 0, 0x82, 0, 0, 0], 0)?;
        expect_submit(&reply, 45, -32, 0)?;
        get_value(&mut session, 46, true, 1)?;
        passed("model_clear_halt_failure_is_not_faked_success");
        set_config(&mut session, 50, 0)?;
        get_value(&mut session, 51, false, 0)?;
        expect_submit(
            &session.control(52, 1, [0x81, 10, 0, 0, 1, 0, 1, 0], 1)?,
            52,
            -32,
            0,
        )?;
        set_config(&mut session, 53, 1)?;
        get_value(&mut session, 54, false, 1)?;
        get_value(&mut session, 55, true, 0)?;
        passed("configuration_zero_and_reconfiguration_are_observed");
        let mut bytes = submit(
            60,
            session.device.devid,
            0,
            0,
            0,
            [0, 9, 2, 0, 0, 0, 0, 0],
            &[],
        );
        bytes.extend_from_slice(&unlink(61, session.device.devid, 60));
        bytes.extend_from_slice(&submit(
            62,
            session.device.devid,
            1,
            0,
            1,
            [0x80, 8, 0, 0, 0, 0, 1, 0],
            &[],
        ));
        session.stream.write_all(&bytes)?;
        let mut winner = None;
        let mut completion = false;
        let mut value = None;
        while winner.is_none() || value.is_none() || winner == Some(0) && !completion {
            let reply = response(&mut session.stream, true)?;
            match reply.seq {
                60 => {
                    expect_submit(&reply, 60, 0, 0)?;
                    require(!completion, "duplicate state completion")?;
                    completion = true;
                }
                61 => {
                    require(
                        reply.command == 4 && [-104, 0].contains(&reply.status) && winner.is_none(),
                        "state UNLINK fields differ",
                    )?;
                    winner = Some(reply.status);
                }
                62 => {
                    expect_submit(&reply, 62, 0, 1)?;
                    require(
                        value.is_none() && [1, 2].contains(&reply.data[0]),
                        "state readback differs",
                    )?;
                    value = Some(reply.data[0]);
                }
                _ => return Err(invalid("unexpected state-cancellation reply")),
            }
        }
        if winner == Some(-104) {
            require(
                !completion && value == Some(1),
                "queued host-state cancellation changed configuration or emitted old completion",
            )?;
            no_reply(&mut session.stream, 100)?;
        } else {
            require(value == Some(2), "completed configuration was not observed")?;
        }
        println!(
            "{{\"event\":\"composite_probe_case\",\"case\":\"host_state_unlink_has_real_winner\",\"result\":\"PASS\",\"observation\":\"{}\",\"configuration\":{}}}",
            if completion {
                "completion_won"
            } else {
                "cancel_won"
            },
            value.ok_or_else(|| invalid("readback missing"))?
        );
        set_config(&mut session, 63, 1)?;
        set_alt(&mut session, 64, 1, 0)?;
        bulk(&mut session, 65)?;
        no_reply(&mut session.stream, 150)?;
    }
    available(address)?;
    passed("pending_real_endpoint_eof_recovers_original_device");
    {
        let mut session = Session::import(address, device.clone())?;
        session.stream.write_all(&submit(
            70,
            session.device.devid,
            0,
            0,
            0,
            [0, 9, 2, 0, 0, 0, 0, 0],
            &[],
        ))?;
    }
    available(address)?;
    passed("submitted_host_state_eof_cleanup_converges");
    {
        let session = Session::import(address, device)?;
        let mut stream = session.stream;
        let mut iso = submit(80, session.device.devid, 1, 2, 0, [0; 8], &[]);
        iso[32..36].copy_from_slice(&1u32.to_be_bytes());
        stream.write_all(&iso)?;
        let mut byte = [0; 1];
        match stream.read(&mut byte) {
            Ok(0) => {}
            Err(error) if error.kind() == io::ErrorKind::ConnectionReset => {}
            _ => {
                return Err(invalid(
                    "unsupported ISO did not explicitly close the session",
                ));
            }
        }
    }
    available(address)?;
    passed("unsupported_iso_frame_is_explicitly_rejected");
    passed("all_lifecycle_checks");
    Ok(())
}
fn request_payload(cycle: u8) -> Vec<u8> {
    format!("RUSTUSB-ECM-REQUEST-{cycle}:0123456789abcdef").into_bytes()
}
fn reply_payload(cycle: u8) -> Vec<u8> {
    format!("RUSTUSB-ECM-RESPONSE-{cycle}:fedcba9876543210").into_bytes()
}
fn udp_peer() -> io::Result<()> {
    let socket = UdpSocket::bind("198.18.0.1:48001")?;
    socket.set_read_timeout(Some(Duration::from_secs(60)))?;
    socket.set_write_timeout(Some(Duration::from_secs(2)))?;
    println!("RUSTUSB_COMPOSITE_UDP_PEER_READY");
    for cycle in [1, 2] {
        let mut buffer = [0; 2048];
        let (length, peer) = socket.recv_from(&mut buffer)?;
        require(
            peer == "198.18.0.2:48002"
                .parse::<SocketAddr>()
                .map_err(|_| invalid("fixed peer address"))?
                && buffer[..length] == request_payload(cycle),
            "UDP peer received unexpected endpoint/payload/sequence",
        )?;
        let reply = reply_payload(cycle);
        require(
            socket.send_to(&reply, peer)? == reply.len(),
            "UDP reply short write",
        )?;
        println!(
            "{{\"event\":\"composite_udp_peer\",\"cycle\":{cycle},\"request_bytes\":{length},\"response_bytes\":{},\"exact\":true}}",
            reply.len()
        );
    }
    println!("RUSTUSB_COMPOSITE_UDP_PEER: PASS cycles=2");
    Ok(())
}
fn udp_client(cycle: u8) -> io::Result<()> {
    require(
        [1, 2].contains(&cycle),
        "only two fixed UDP cycles are authorized",
    )?;
    let socket = UdpSocket::bind("198.18.0.2:48002")?;
    socket.connect("198.18.0.1:48001")?;
    socket.set_read_timeout(Some(Duration::from_secs(5)))?;
    socket.set_write_timeout(Some(Duration::from_secs(2)))?;
    let request = request_payload(cycle);
    require(
        socket.send(&request)? == request.len(),
        "UDP request short write",
    )?;
    let mut buffer = [0; 2048];
    let length = socket.recv(&mut buffer)?;
    require(
        buffer[..length] == reply_payload(cycle),
        "UDP response bytes differ",
    )?;
    println!(
        "{{\"event\":\"composite_udp_client\",\"cycle\":{cycle},\"request_bytes\":{},\"response_bytes\":{length},\"exact\":true}}",
        request.len()
    );
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
    // and connection closure below, then check its explicit caller-UID error.
    let write_result = stream.write_all(
        format!("{{\"op\":\"open_physical\",\"device_id\":\"{device_id}\"}}\n").as_bytes(),
    );
    let mut reply = Vec::new();
    let mut block = [0; 1024];
    let closure = loop {
        let count = match stream.read(&mut block) {
            Ok(0) => break "eof",
            Ok(count) => count,
            Err(error)
                if error.kind() == io::ErrorKind::ConnectionReset
                    && reply.last() == Some(&b'\n')
                    && reply.iter().filter(|byte| **byte == b'\n').count() == 1 =>
            {
                break "reset_after_frame";
            }
            Err(error) => return Err(error),
        };
        require(
            reply.len() + count <= 65_536,
            "helper refusal exceeded frame bound",
        )?;
        reply.extend_from_slice(&block[..count]);
    };
    require(
        reply.last() == Some(&b'\n') && reply.iter().filter(|byte| **byte == b'\n').count() == 1,
        "helper refusal must be exactly one newline-delimited frame followed by connection closure",
    )?;
    eprintln!("RUSTUSB_COMPOSITE_HELPER_CONNECTION_CLOSED method={closure}");
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
        args.len() >= 2,
        "usage: composite-wire-probe <lifecycle|busy|health|helper-denied|udp-peer|udp-client> [argument]",
    )?;
    match args[1].as_str() {
        "udp-peer" => {
            require(args.len() == 2, "unexpected UDP peer argument")?;
            udp_peer()
        }
        "udp-client" => {
            require(args.len() == 3, "UDP cycle required")?;
            udp_client(args[2].parse().map_err(|_| invalid("invalid cycle"))?)
        }
        "helper-denied" => {
            require(args.len() == 3, "device ID required")?;
            helper_denied(&args[2])
        }
        mode => {
            require(
                args.len() == if mode == "busy" { 3 } else { 2 },
                "unexpected fixed network probe arguments",
            )?;
            let address = SocketAddr::new(IpAddr::V4(Ipv4Addr::new(192, 0, 2, 1)), 3240);
            match mode {
                "lifecycle" => lifecycle(address),
                "health" => {
                    available(address)?;
                    passed("original_ecm_available");
                    Ok(())
                }
                "busy" => {
                    // Use the bus ID observed by the guest management discovery.
                    let mut stream = connect(address)?;
                    stream.write_all(&import_request(
                        args.get(2).ok_or_else(|| invalid("busy bus ID missing"))?,
                    )?)?;
                    let mut reply = [0; 8];
                    stream.read_exact(&mut reply)?;
                    require(
                        reply == [1, 0x11, 0, 3, 0, 0, 0, 2],
                        "leased ECM was not explicitly busy",
                    )?;
                    passed("ecm_exclusive_busy");
                    Ok(())
                }
                _ => Err(invalid("unknown bounded probe mode")),
            }
        }
    }
}

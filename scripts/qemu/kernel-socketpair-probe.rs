//! Disposable single-guest kernel socket-family experiment, not a product API.
#![forbid(unsafe_code)]

use std::collections::BTreeSet;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::net::Shutdown;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const VHCI: &str = "/sys/devices/platform/vhci_hcd.0";
const DEVICES: [(&str, &str, &str, u32, u32); 2] =
    [("1-1", "0627", "0001", 2, 3), ("1-2", "0403", "6001", 3, 255)];

fn require(ok: bool, message: &str) -> Result<()> {
    if ok { Ok(()) } else { Err(message.into()) }
}

fn quote(value: &str) -> String {
    let mut output = String::from("\"");
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            c if c < ' ' => output.push_str(&format!("\\u{:04x}", c as u32)),
            c => output.push(c),
        }
    }
    output.push('"');
    output
}

fn emit(event: &str, fields: &str) -> Result<()> {
    let mut output = io::stdout().lock();
    writeln!(output, "{{\"event\":{},\"schema_version\":1,{fields}}}", quote(event))?;
    output.flush()?;
    Ok(())
}

fn read(path: impl AsRef<Path>) -> Result<String> {
    let mut value = String::new();
    File::open(path.as_ref())?.take(65537).read_to_string(&mut value)?;
    require(value.len() <= 65536, "bounded text input exceeded")?;
    Ok(value)
}

fn number(text: &str, radix: u32) -> Result<u32> {
    let value = text.trim();
    require(!value.is_empty() && value.chars().all(|c| c.is_digit(radix)), "invalid unsigned number")?;
    Ok(u32::from_str_radix(value, radix)?)
}

fn attr(path: &Path, name: &str) -> Result<String> {
    Ok(read(path.join(name))?.trim().to_owned())
}

fn guard() -> Result<()> {
    // No USB/sysfs open or write is allowed before both checks succeed.
    let status = read("/proc/self/status")?;
    let uid = status.lines().find(|line| line.starts_with("Uid:"))
        .ok_or("missing process UID")?.split_whitespace().skip(1).collect::<Vec<_>>();
    require(uid == ["0", "0", "0", "0"], "guest root required")?;
    let cmdline = read("/proc/cmdline")?;
    let words = cmdline.split_whitespace().collect::<Vec<_>>();
    for marker in ["RUSTUSB_QEMU_GUEST=1", "rustusb.kernel_socketpair_test=1"] {
        require(words.iter().filter(|word| **word == marker).count() == 1, "dedicated guest boot markers required")?;
    }
    require(std::env::args_os().count() == 1, "probe accepts no arguments")?;
    let stat = read("/proc/self/stat")?;
    let suffix = stat.rsplit_once(") ").ok_or("invalid process stat")?.1;
    let start = suffix.split_whitespace().nth(19).ok_or("missing starttime")?;
    emit("guard", &format!("\"pid\":{},\"starttime\":{},\"uids\":[0,0,0,0],\"guest_markers\":true,\"cmdline\":{}",
        std::process::id(), number64(start)?, quote(cmdline.trim())))
}

fn number64(text: &str) -> Result<u64> {
    require(!text.is_empty() && text.bytes().all(|c| c.is_ascii_digit()), "invalid u64")?;
    Ok(text.parse()?)
}

#[derive(Clone, PartialEq, Eq)]
struct Device {
    bus: String,
    real: PathBuf,
    device: u64,
    inode: u64,
    busnum: u32,
    devnum: u32,
    vendor: String,
    product: String,
    config: u32,
    speed: String,
    interfaces: Vec<(String, u32, u32, u32, u32, u32)>,
}

impl Device {
    fn capture(bus: &str, local: bool) -> Result<Self> {
        require(bus.len() <= 32 && bus.contains('-') && bus.bytes().all(|c| c.is_ascii_digit() || c == b'-' || c == b'.'), "invalid fixed bus ID")?;
        let path = Path::new("/sys/bus/usb/devices").join(bus);
        let real = fs::canonicalize(&path)?;
        require(if local { real.starts_with(VHCI) } else { real.starts_with("/sys/devices/pci0000:00") }, "device outside expected controller")?;
        let metadata = fs::metadata(&real)?;
        require(metadata.is_dir() && metadata.uid() == 0, "untrusted USB sysfs directory")?;
        let mut interfaces = Vec::new();
        for entry in fs::read_dir("/sys/bus/usb/devices")? {
            let entry = entry?;
            let name = entry.file_name().into_string().map_err(|_| "non-UTF8 sysfs name")?;
            if name.starts_with(&format!("{bus}:")) {
                let p = entry.path();
                interfaces.push((name, number(&attr(&p, "bInterfaceNumber")?, 16)?,
                    number(&attr(&p, "bAlternateSetting")?, 10)?, number(&attr(&p, "bInterfaceClass")?, 16)?,
                    number(&attr(&p, "bInterfaceSubClass")?, 16)?, number(&attr(&p, "bInterfaceProtocol")?, 16)?));
            }
        }
        interfaces.sort();
        require(number(&attr(&path, "bNumInterfaces")?, 10)? as usize == interfaces.len(), "incomplete current interface set")?;
        Ok(Self { bus: bus.to_owned(), real, device: metadata.dev(), inode: metadata.ino(),
            busnum: number(&attr(&path, "busnum")?, 10)?, devnum: number(&attr(&path, "devnum")?, 10)?,
            vendor: attr(&path, "idVendor")?, product: attr(&path, "idProduct")?,
            config: number(&attr(&path, "bConfigurationValue")?, 10)?, speed: attr(&path, "speed")?, interfaces })
    }

    fn json(&self) -> String {
        let interfaces = self.interfaces.iter().map(|(name, number, alt, class, subclass, protocol)|
            format!("{{\"name\":{},\"number\":{number},\"alternate\":{alt},\"class\":{class},\"subclass\":{subclass},\"protocol\":{protocol}}}", quote(name))).collect::<Vec<_>>().join(",");
        format!("{{\"bus_id\":{},\"sysfs_path\":{},\"filesystem_device\":{},\"inode\":{},\"bus_number\":{},\"device_address\":{},\"vendor\":{},\"product\":{},\"configuration\":{},\"speed\":{},\"interfaces\":[{interfaces}]}}",
            quote(&self.bus), quote(&self.real.to_string_lossy()), self.device, self.inode, self.busnum, self.devnum,
            quote(&self.vendor), quote(&self.product), self.config, quote(&self.speed))
    }
}

fn driver(path: &Path) -> Result<String> {
    Ok(fs::read_link(path.join("driver"))?.file_name().ok_or("missing driver name")?.to_string_lossy().into_owned())
}

struct Attribute { file: File, path: PathBuf, device: u64, inode: u64 }

impl Attribute {
    fn open(path: PathBuf) -> Result<Self> {
        let before = fs::symlink_metadata(&path)?;
        require(before.is_file() && before.uid() == 0 && before.mode() & 0o022 == 0, "untrusted sysfs attribute")?;
        let file = OpenOptions::new().write(true).open(&path)?;
        let metadata = file.metadata()?;
        require(before.dev() == metadata.dev() && before.ino() == metadata.ino(), "attribute replaced during open")?;
        Ok(Self { file, path, device: metadata.dev(), inode: metadata.ino() })
    }

    fn write(&mut self, text: &str, side: &str, bus: &str) -> Result<()> {
        let current = fs::symlink_metadata(&self.path)?;
        require(current.dev() == self.device && current.ino() == self.inode, "original attribute changed before write")?;
        emit("write_begin", &format!("\"bus_id\":{},\"side\":{},\"path\":{},\"attribute_fd\":{},\"filesystem_device\":{},\"inode\":{},\"value\":{}",
            quote(bus), quote(side), quote(&self.path.to_string_lossy()), self.file.as_raw_fd(), self.device, self.inode, quote(text)))?;
        // One sysfs transaction. Do not retry a short write with a second command.
        let count = self.file.write(text.as_bytes())?;
        require(count == text.len(), "short sysfs command write")?;
        emit("write_complete", &format!("\"bus_id\":{},\"side\":{},\"bytes\":{count}", quote(bus), quote(side)))
    }
}

#[derive(Clone)]
struct Port { hub: String, port: u32, status: u32, speed: u32, remote: u32, socket_display: String, local: String }

fn ports() -> Result<Vec<Port>> {
    let text = read(Path::new(VHCI).join("status"))?;
    let mut lines = text.lines();
    require(lines.next().ok_or("empty VHCI status")?.split_whitespace().collect::<Vec<_>>().join(" ") == "hub port sta spd dev sockfd local_busid", "unexpected VHCI header")?;
    let mut seen = BTreeSet::new();
    let mut rows = Vec::new();
    for line in lines {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        require(fields.len() == 7 && rows.len() < 256, "invalid VHCI row")?;
        let port = number(fields[1], 10)?;
        require(seen.insert(port) && matches!(fields[0], "hs" | "ss"), "duplicate or invalid VHCI port")?;
        // v6.8 prints a historical decimal sockfd. Preserve this display field,
        // allowing bounded hex-compatible text without treating it as identity.
        require(!fields[5].is_empty() && fields[5].len() <= 16 && fields[5].bytes().all(|c| c.is_ascii_hexdigit()), "invalid VHCI socket display")?;
        rows.push(Port { hub: fields[0].to_owned(), port, status: number(fields[2], 10)?, speed: number(fields[3], 10)?,
            remote: number(fields[4], 16)?, socket_display: fields[5].to_owned(), local: fields[6].to_owned() });
    }
    require(!rows.is_empty(), "no VHCI ports")?;
    Ok(rows)
}

struct Channel { original: Device, port: u32, stub: UnixStream, vhci: UnixStream, attr: Attribute, attach: Attribute, local: Option<Device> }

fn socket_json(socket: &UnixStream) -> Result<String> {
    require(socket.local_addr()?.is_unnamed() && socket.peer_addr()?.is_unnamed(), "socketpair must be anonymous")?;
    let path = format!("/proc/self/fd/{}", socket.as_raw_fd());
    let metadata = fs::metadata(&path)?;
    require(metadata.file_type().is_socket(), "pair FD is not a socket")?;
    Ok(format!("{{\"fd\":{},\"inode\":{},\"filesystem_device\":{},\"target\":{},\"anonymous\":true,\"family\":\"AF_UNIX\",\"type\":\"SOCK_STREAM\"}}",
        socket.as_raw_fd(), metadata.ino(), metadata.dev(), quote(&fs::read_link(path)?.to_string_lossy())))
}

fn prepare() -> Result<Vec<Channel>> {
    require(fs::canonicalize(VHCI)? == Path::new(VHCI), "unexpected VHCI controller alias")?;
    let mut status_files = Vec::new();
    for entry in fs::read_dir(VHCI)? {
        let name = entry?.file_name().into_string().map_err(|_| "non-UTF8 VHCI name")?;
        if name == "status" || name.starts_with("status.") { status_files.push(name); }
    }
    require(status_files == ["status"], "experiment requires the complete single-controller status")?;
    let rows = ports()?;
    require(rows.iter().all(|r| r.status == 4 && r.local == "0-0" && r.remote == 0 && r.socket_display.bytes().all(|c| c == b'0')), "VHCI is not entirely initially NULL")?;
    let free = rows.iter().filter(|r| r.hub == "hs").take(2).map(|r| r.port).collect::<Vec<_>>();
    require(free.len() == 2, "two empty HS ports required")?;
    emit("initial_status", &format!("\"status\":{}", quote(&read(Path::new(VHCI).join("status"))?)))?;
    let mut channels = Vec::new();
    for ((bus, vendor, product, address, class), port) in DEVICES.into_iter().zip(free) {
        let original = Device::capture(bus, false)?;
        require(original.busnum == 1 && original.devnum == address && original.vendor == vendor && original.product == product && original.config == 1 && original.speed == "12", "fixed QEMU USB identity/configuration/speed differs")?;
        require(original.interfaces.len() == 1 && original.interfaces[0].0 == format!("{bus}:1.0") && original.interfaces[0].1 == 0 && original.interfaces[0].2 == 0 && original.interfaces[0].3 == class, "fixed QEMU interface differs")?;
        require(driver(&original.real)? == "usbip-host" && attr(&original.real, "usbip_status")? == "1", "original export is not available")?;
        let node = format!("/dev/bus/usb/001/{address:03}");
        let metadata = fs::symlink_metadata(&node)?;
        require(metadata.file_type().is_char_device() && metadata.uid() == 0 && metadata.mode() & 0o777 == 0o600, "fixed USB node must be root0600 character device")?;
        let expected_dev = format!("189:{}", address - 1);
        require(attr(&original.real, "dev")? == expected_dev && metadata.rdev() == ((189_u64 << 8) | u64::from(address - 1)), "USB node/sysfs device number differs")?;
        let attr = Attribute::open(original.real.join("usbip_sockfd"))?;
        let attach = Attribute::open(Path::new(VHCI).join("attach"))?;
        let (stub, vhci) = UnixStream::pair()?;
        require(original == Device::capture(bus, false)?, "original changed while preopening attributes")?;
        emit("prepared", &format!("\"original\":{},\"port\":{port},\"stub\":{},\"vhci\":{},\"node\":{},\"node_rdev\":{},\"node_inode\":{},\"attribute_inode\":{}",
            original.json(), socket_json(&stub)?, socket_json(&vhci)?, quote(&node), metadata.rdev(), metadata.ino(), attr.inode))?;
        channels.push(Channel { original, port, stub, vhci, attr, attach, local: None });
    }
    Ok(channels)
}

fn connect(channels: &mut [Channel]) -> Result<()> {
    for channel in channels.iter_mut() {
        require(channel.original == Device::capture(&channel.original.bus, false)?, "original generation changed before handoff")?;
        require(ports()?.iter().any(|r| r.port == channel.port && r.hub == "hs" && r.status == 4), "selected port no longer NULL")?;
        channel.attr.write(&format!("{}\n", channel.stub.as_raw_fd()), "stub", &channel.original.bus)?;
        let devid = (channel.original.busnum << 16) | channel.original.devnum;
        channel.attach.write(&format!("{} {} {devid} 2\n", channel.port, channel.vhci.as_raw_fd()), "vhci", &channel.original.bus)?;
    }
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        let rows = ports()?;
        let mut complete = true;
        for channel in channels.iter_mut() {
            let row = rows.iter().find(|r| r.port == channel.port).ok_or("selected port missing")?;
            if row.status != 6 { complete = false; continue; }
            require(row.hub == "hs" && row.speed == 2 && row.remote == ((channel.original.busnum << 16) | channel.original.devnum), "attached VHCI identity differs")?;
            let local = match Device::capture(&row.local, true) { Ok(local) => local, Err(_) => { complete = false; continue; } };
            if local.config != 1 || local.interfaces.len() != 1 { complete = false; continue; }
            require(local.vendor == channel.original.vendor && local.product == channel.original.product && local.speed == "12", "enumerated local identity differs")?;
            require(local.interfaces.iter().zip(&channel.original.interfaces).all(|(a, b)| (a.1, a.2, a.3, a.4, a.5) == (b.1, b.2, b.3, b.4, b.5)), "enumerated full interface/alternate differs")?;
            let expected = if channel.original.bus == "1-1" { "usbhid" } else { "ftdi_sio" };
            if driver(&Path::new("/sys/bus/usb/devices").join(format!("{}:1.0", local.bus))).ok().as_deref() != Some(expected) { complete = false; continue; }
            channel.local = Some(local);
        }
        if complete { break; }
        require(Instant::now() < deadline, "actual VHCI enumeration deadline exceeded")?;
        thread::sleep(Duration::from_millis(50));
    }
    for channel in channels {
        let local = channel.local.as_ref().ok_or("missing enumerated receipt")?;
        emit("attached", &format!("\"original\":{},\"local\":{},\"port\":{},\"remote_devid\":{},\"stub\":{},\"vhci\":{}",
            channel.original.json(), local.json(), channel.port, (channel.original.busnum << 16) | channel.original.devnum, socket_json(&channel.stub)?, socket_json(&channel.vhci)?))?;
    }
    emit("ready", "\"devices\":2,\"transport\":\"anonymous_unix_stream_pair\",\"tls\":false,\"single_guest\":true")
}

fn stop_command() -> Result<()> {
    let (sender, receiver) = mpsc::channel();
    thread::spawn(move || {
        let result = (|| -> io::Result<Vec<u8>> {
            let mut command = Vec::new();
            let mut input = io::stdin().lock();
            for _ in 0..5 {
                let mut byte = [0_u8; 1];
                if input.read(&mut byte)? == 0 { break; }
                command.push(byte[0]);
                if byte[0] == b'\n' { break; }
            }
            Ok(command)
        })();
        let _ = sender.send(result);
    });
    let command = receiver.recv_timeout(Duration::from_secs(60))??;
    require(command == b"stop\n", "only bounded stop command is accepted")?;
    emit("stop_received", "\"command\":\"stop\"")
}

fn disconnect(channels: &[Channel]) -> Result<()> {
    let mut first: Option<Box<dyn std::error::Error>> = None;
    let mut observations = Vec::new();
    for channel in channels {
        for (side, socket) in [("stub", &channel.stub), ("vhci", &channel.vhci)] {
            let result = socket.shutdown(Shutdown::Both);
            observations.push(format!("\"bus_id\":{},\"side\":{},\"fd\":{},\"how\":\"SHUT_RDWR\",\"ok\":{},\"error\":{}",
                quote(&channel.original.bus), quote(side), socket.as_raw_fd(), result.is_ok(), result.as_ref().err().map(|error| quote(&error.to_string())).map_or("null".to_owned(), |value| value)));
            if let Err(error) = result { if first.is_none() { first = Some(error.into()); } }
        }
    }
    // Complete every owned socket shutdown before a fallible diagnostic write.
    for observation in observations {
        if let Err(error) = emit("shutdown", &observation) { if first.is_none() { first = Some(error); } }
    }
    let deadline = Instant::now() + Duration::from_secs(15);
    loop {
        let rows = ports()?;
        let mut cleared = true;
        for channel in channels {
            let local_gone = if let Some(local) = &channel.local {
                match fs::symlink_metadata(Path::new("/sys/bus/usb/devices").join(&local.bus)) {
                    Ok(_) => false,
                    Err(error) if error.kind() == io::ErrorKind::NotFound => true,
                    Err(error) => return Err(error.into()),
                }
            } else { true };
            cleared &= rows.iter().any(|r| r.port == channel.port && r.status == 4 && r.local == "0-0" && r.remote == 0 && r.socket_display.bytes().all(|c| c == b'0'))
                && local_gone && attr(&channel.original.real, "usbip_status")? == "1";
        }
        if cleared { break; }
        require(Instant::now() < deadline, "explicit shutdown did not clear both kernel ends")?;
        thread::sleep(Duration::from_millis(50));
    }
    for channel in channels {
        require(channel.original == Device::capture(&channel.original.bus, false)?, "original changed during disconnect")?;
    }
    let mut retained = Vec::new();
    for channel in channels {
        for socket in [&channel.stub, &channel.vhci] {
            let path = format!("/proc/self/fd/{}", socket.as_raw_fd());
            let metadata = fs::metadata(&path)?;
            require(metadata.file_type().is_socket(), "retained pair FD disappeared")?;
            retained.push(format!("{{\"fd\":{},\"inode\":{},\"target\":{}}}", socket.as_raw_fd(), metadata.ino(), quote(&fs::read_link(path)?.to_string_lossy())));
        }
    }
    emit("disconnected", &format!("\"ports_null\":true,\"old_local_generations_absent\":true,\"stub_available\":true,\"fds_still_open\":true,\"retained_sockets\":[{}],\"status\":{}", retained.join(","), quote(&read(Path::new(VHCI).join("status"))?)))?;
    if let Some(error) = first { return Err(error); }
    Ok(())
}

fn main() {
    let result = (|| -> Result<()> {
        guard()?;
        let mut channels = prepare()?;
        let operation = connect(&mut channels).and_then(|()| stop_command());
        // Even a failed partial setup explicitly shuts down all retained ends.
        let cleanup = disconnect(&channels);
        operation?;
        cleanup?;
        emit("complete", "\"result\":\"PASS\",\"cleanup_before_fd_drop\":true")
    })();
    if let Err(error) = result {
        let _ = emit("error", &format!("\"result\":\"FAIL\",\"error\":{}", quote(&error.to_string())));
        std::process::exit(1);
    }
}

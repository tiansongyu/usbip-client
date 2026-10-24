use std::{io, os::fd::AsRawFd, time::Duration};
use rustix::{io::Errno, net::{self, netlink::{self, SocketAddrNetlink}, sockopt, AddressFamily, SocketFlags, SocketType, RecvFlags}};
use tokio::io::unix::AsyncFd;

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("UID={} PID={}", rustix::process::getuid().as_raw(), std::process::id());
    let fd = net::socket_with(AddressFamily::NETLINK, SocketType::DGRAM, SocketFlags::CLOEXEC | SocketFlags::NONBLOCK, Some(netlink::KOBJECT_UEVENT))?;
    let raw = fd.as_raw_fd();
    sockopt::set_socket_recv_buffer_size(&fd, 64 * 1024)?;
    println!("RCVBUF_REQUEST=65536 ACTUAL={}", sockopt::socket_recv_buffer_size(&fd)?);
    net::bind(&fd, &SocketAddrNetlink::new(0, 1))?;
    let bound = SocketAddrNetlink::try_from(net::getsockname(&fd)?)?;
    println!("BOUND_PID={} GROUPS={}", bound.pid(), bound.groups());
    if bound.pid() == 0 || bound.groups() != 1 { return Err(io::Error::other("unexpected local netlink bind").into()); }
    let link = std::fs::read_link(format!("/proc/self/fd/{raw}"))?;
    println!("LIVE_FD={raw} TARGET={}", link.display());
    let monitored = AsyncFd::new(fd)?;
    let mut bytes = [0_u8; 8192];
    let mut seen = 0_usize;
    let mut unexpected = 0_usize;
    let mut truncated = 0_usize;
    let deadline = tokio::time::Instant::now() + Duration::from_millis(200);
    loop {
        tokio::select! {
            _ = tokio::time::sleep_until(deadline) => { println!("READ_WAIT_CANCELLED_AT_200MS=true"); break; }
            readiness = monitored.readable() => {
                let mut guard = readiness?;
                let result = guard.try_io(|inner| net::recvfrom(inner.get_ref(), &mut bytes, RecvFlags::DONTWAIT | RecvFlags::TRUNC).map_err(io::Error::from));
                match result {
                    Err(_) => {},
                    Ok(Ok((stored, actual, source))) => {
                        seen += 1;
                        if stored > bytes.len() { return Err(io::Error::other("stored bytes exceed buffer").into()); }
                        if actual > bytes.len() { truncated += 1; }
                        let trusted = source.and_then(|s| SocketAddrNetlink::try_from(s).ok()).is_some_and(|s| s.pid() == 0 && s.groups() == 1);
                        if !trusted { unexpected += 1; }
                    }
                    Ok(Err(e)) if e.raw_os_error() == Some(Errno::INTR.raw_os_error()) => {},
                    Ok(Err(e)) => return Err(e.into()),
                }
            }
        }
    }
    println!("OBSERVED_DATAGRAMS={seen} REJECTED_SOURCES={unexpected} TRUNCATED={truncated}");
    drop(monitored);
    match std::fs::read_link(format!("/proc/self/fd/{raw}")) {
        Err(e) if e.kind() == io::ErrorKind::NotFound => println!("ORIGINAL_FD_CLOSED=true"),
        _ => return Err(io::Error::other("original fd still observed after owner drop").into()),
    }
    println!("SCOPE=read-only socket lifecycle; no synthetic send, device operation, or hotplug I/O qualification");
    Ok(())
}

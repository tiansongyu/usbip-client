#![forbid(unsafe_code)]
use std::{io, io::Write, os::fd::AsRawFd, os::unix::net::UnixStream, time::{Duration, Instant}};
use rustix::{event::{poll, PollFd, PollFlags}, io::Errno, net::{self, netlink::{self, SocketAddrNetlink}, sockopt, AddressFamily, SocketFlags, SocketType, RecvFlags}};
fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("UID={} PID={}", rustix::process::getuid().as_raw(), std::process::id());
    let fd = net::socket_with(AddressFamily::NETLINK, SocketType::DGRAM, SocketFlags::CLOEXEC | SocketFlags::NONBLOCK, Some(netlink::KOBJECT_UEVENT))?;
    let raw = fd.as_raw_fd();
    sockopt::set_socket_recv_buffer_size(&fd, 64 * 1024)?;
    println!("RCVBUF_REQUEST=65536 ACTUAL={}", sockopt::socket_recv_buffer_size(&fd)?);
    net::bind(&fd, &SocketAddrNetlink::new(0, 1))?;
    let bound = SocketAddrNetlink::try_from(net::getsockname(&fd)?)?;
    println!("BOUND_PID={} GROUPS={}", bound.pid(), bound.groups());
    if bound.pid() == 0 || bound.groups() != 1 { return Err(io::Error::other("unexpected local netlink bind").into()); }
    let target=std::fs::read_link(format!("/proc/self/fd/{raw}"))?;println!("LIVE_FD={raw} TARGET={}",target.display());
    let (wake_read, mut wake_write) = UnixStream::pair()?;
    wake_write.set_nonblocking(true)?;
    let (ready_tx, ready_rx)=std::sync::mpsc::sync_channel::<()>(1);
    let worker = std::thread::Builder::new().name("read-only-netlink-probe".into()).spawn(move || -> io::Result<(usize,usize,usize)> {
        let mut polls=[PollFd::new(&fd,PollFlags::IN),PollFd::new(&wake_read,PollFlags::IN)];
        ready_tx.send(()).map_err(|_|io::Error::other("ready owner gone"))?;
        let mut bytes=[0_u8;8192];let mut seen=0;let mut rejected=0;let mut truncated=0;
        loop {
            match poll(&mut polls,None) { Ok(_) => {}, Err(Errno::INTR) => continue, Err(e) => return Err(e.into()) }
            let wake=polls[1].revents();if wake.intersects(PollFlags::IN|PollFlags::HUP|PollFlags::ERR) { println!("WAKE_READINESS={wake:?}");break; }
            if polls[0].revents().intersects(PollFlags::IN|PollFlags::ERR) {
                // Finite per-iteration draining keeps wake/shutdown responsive during storms.
                for _ in 0..64 {
                    match net::recvfrom(&fd,&mut bytes,RecvFlags::DONTWAIT|RecvFlags::TRUNC) {
                        Ok((stored,actual,source)) => { if stored>bytes.len() {return Err(io::Error::other("stored length exceeds buffer"));}seen+=1;if actual>bytes.len() {truncated+=1;}if !source.and_then(|s|SocketAddrNetlink::try_from(s).ok()).is_some_and(|s|s.pid()==0&&s.groups()==1) {rejected+=1;} },
                        Err(Errno::AGAIN) => break,
                        Err(Errno::INTR) => continue,
                        Err(e) => return Err(e.into()),
                    }
                }
            }
        }
        Ok((seen,rejected,truncated))
    })?;
    ready_rx.recv_timeout(Duration::from_secs(2))?;
    std::thread::sleep(Duration::from_millis(50));
    let at=Instant::now();wake_write.write_all(&[1])?;
    let result=worker.join().map_err(|_|io::Error::other("actual worker panicked"))??;
    println!("ACTUAL_WORKER_JOINED=true WAKE_TO_JOIN_US={}",at.elapsed().as_micros());
    println!("OBSERVED_DATAGRAMS={} REJECTED_SOURCES={} TRUNCATED={}",result.0,result.1,result.2);
    match std::fs::read_link(format!("/proc/self/fd/{raw}")) {Err(e) if e.kind()==io::ErrorKind::NotFound=>println!("ORIGINAL_FD_CLOSED=true"),_=>return Err(io::Error::other("original fd still observed after worker joined").into())}
    println!("SCOPE=actual unprivileged netlink socket and safe poll wake/join/drop only; zero hotplug mutation or synthetic send");
    Ok(())
}

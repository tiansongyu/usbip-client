use std::{io, os::fd::AsRawFd, sync::{Arc, atomic::{AtomicBool, Ordering}}, time::{Duration, Instant}};
use rustix::event::{poll, PollFd, PollFlags};
use rustix::event::Timespec;

fn main() -> io::Result<()> {
    let stop = Arc::new(AtomicBool::new(false));
    let stopped = Arc::clone(&stop);
    let (ready, started) = std::sync::mpsc::sync_channel(1);
    let owner = std::thread::spawn(move || -> io::Result<()> {
        let socket = udev::MonitorBuilder::new_kernel()?.match_subsystem("usb")?.listen()?;
        let fd = socket.as_raw_fd();
        let info = std::fs::read_to_string(format!("/proc/self/fdinfo/{fd}"))?;
        println!("source=kernel fdinfo_flags={}", info.lines().find(|l| l.starts_with("flags:")).map_or("missing", |v| v));
        match socket.iter().next() {
            Some(_event) => println!("initial_receive=event_metadata_discarded"),
            None => { let e=io::Error::last_os_error();println!("initial_receive_raw={:?} kind={:?}",e.raw_os_error(),e.kind()); }
        }
        ready.send(()).map_err(|_| io::Error::other("ready receiver dropped"))?;
        let interval=Timespec{tv_sec:0,tv_nsec:50_000_000};
        let mut polls=0;let mut events=0;
        while !stopped.load(Ordering::Acquire) {
            let mut fds=[PollFd::new(&socket,PollFlags::IN)];
            match poll(&mut fds,Some(&interval)) {
                Ok(_) => {},
                Err(e) if e==rustix::io::Errno::INTR => continue,
                Err(e) => return Err(e.into()),
            }
            polls+=1;
            if fds[0].revents().intersects(PollFlags::ERR|PollFlags::HUP|PollFlags::NVAL) { return Err(io::Error::other("monitor fd abnormal readiness")); }
            if fds[0].revents().contains(PollFlags::IN) {
                for _ in 0..64 {
                    match socket.iter().next() {
                        Some(_event) => events+=1,
                        None => { let e=io::Error::last_os_error();if e.kind()==io::ErrorKind::WouldBlock {break;} return Err(e); }
                    }
                }
            }
        }
        drop(socket);
        println!("poll_calls={polls} observed_events={events} socket_dropped=true");
        Ok(())
    });
    started.recv_timeout(Duration::from_secs(3)).map_err(|_|io::Error::other("owner start timeout"))?;
    std::thread::sleep(Duration::from_millis(140));
    let requested=Instant::now();stop.store(true,Ordering::Release);
    owner.join().map_err(|_|io::Error::other("owner thread panicked"))??;
    println!("stop_join_micros={} owner_thread_joined=true",requested.elapsed().as_micros());
    Ok(())
}

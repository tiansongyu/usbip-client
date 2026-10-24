use std::{io, os::fd::AsRawFd, time::Duration};
use tokio::io::unix::AsyncFd;

fn receive(socket: &udev::MonitorSocket) -> io::Result<bool> {
    match socket.iter().next() {
        Some(_event) => Ok(true),
        None => Err(io::Error::last_os_error()),
    }
}

fn main() -> io::Result<()> {
    let uid_line = std::fs::read_to_string("/proc/self/status")?.lines().find(|l| l.starts_with("Uid:")).map(str::to_owned).ok_or_else(|| io::Error::other("UID absent"))?;
    println!("{uid_line}");
    let owned = std::thread::spawn(|| -> io::Result<()> {
        // All !Send libudev values are constructed, used and dropped on this one thread.
        let runtime = tokio::runtime::Builder::new_current_thread().enable_all().build()?;
        runtime.block_on(async {
            for kernel in [false, true] {
                let builder = if kernel { udev::MonitorBuilder::new_kernel()? } else { udev::MonitorBuilder::new()? };
                let socket = builder.match_subsystem("usb")?.listen()?;
                let info = std::fs::read_to_string(format!("/proc/self/fdinfo/{}", socket.as_raw_fd()))?;
                println!("source={} fdinfo_flags={}", if kernel { "kernel" } else { "udev" }, info.lines().find(|l| l.starts_with("flags:")).map_or("missing", |v| v));
                match receive(&socket) {
                    Ok(_) => println!("immediate_receive=event_metadata_discarded"),
                    Err(e) => println!("immediate_receive=error raw={:?} kind={:?}", e.raw_os_error(), e.kind()),
                }
                let fd = AsyncFd::new(socket)?;
                match tokio::time::timeout(Duration::from_millis(200), fd.readable()).await {
                    Err(_) => println!("async_readiness=bounded_timeout"),
                    Ok(Err(e)) => return Err(e),
                    Ok(Ok(mut ready)) => match ready.try_io(|inner| receive(inner.get_ref())) {
                        Ok(Ok(_)) => println!("async_receive=event_metadata_discarded"),
                        Ok(Err(e)) => return Err(e),
                        Err(_) => println!("async_receive=spurious_wouldblock"),
                    },
                }
                drop(fd);
            }
            let mut enumerator = udev::Enumerator::new()?;
            enumerator.match_subsystem("usb")?;
            let count = enumerator.scan_devices()?.count();
            println!("usb_enumerator_count={count}");
            Ok(())
        })
    });
    let result = owned.join().map_err(|_| io::Error::other("research owner thread panicked"))?;
    result?;
    println!("owner_thread_joined=true");
    Ok(())
}

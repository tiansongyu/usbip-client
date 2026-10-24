//! Bounded single-FD handoff shared by fixed USBFS grants and anonymous Native channels.
use rustix::net::{
    RecvAncillaryBuffer, RecvAncillaryMessage, RecvFlags, ReturnFlags, SendAncillaryBuffer,
    SendAncillaryMessage, SendFlags,
};
use rustusb_core::{Error, Result};
use std::{
    io::{IoSlice, IoSliceMut},
    mem::MaybeUninit,
    os::fd::{AsFd, OwnedFd},
    time::Duration,
};
use tokio::{io::Interest, net::UnixStream};
pub(crate) const FD_MARKER: u8 = 0x46;
const FD_DEADLINE: Duration = Duration::from_secs(5);
pub(crate) async fn send_fd(stream: &UnixStream, fd: &OwnedFd) -> Result<()> {
    tokio::time::timeout(FD_DEADLINE, async {
        loop {
            stream.writable().await?;
            let result = stream.try_io(Interest::WRITABLE, || {
                let fds = [fd.as_fd()];
                let mut space = [MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(1))];
                let mut control = SendAncillaryBuffer::new(&mut space);
                if !control.push(SendAncillaryMessage::ScmRights(&fds)) {
                    return Err(std::io::Error::other(
                        "capability ancillary buffer too small",
                    ));
                }
                rustix::net::sendmsg(
                    stream,
                    &[IoSlice::new(&[FD_MARKER])],
                    &mut control,
                    SendFlags::NOSIGNAL,
                )
                .map_err(std::io::Error::from)
            });
            match result {
                Ok(1) => return Ok(()),
                Ok(_) => return Err(Error::Protocol("capability marker was not sent".into())),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => continue,
                Err(error) => return Err(Error::Io(error)),
            }
        }
    })
    .await
    .map_err(|_| Error::Timeout("physical capability send".into()))?
}

pub(crate) async fn receive_fd(stream: &UnixStream) -> Result<OwnedFd> {
    tokio::time::timeout(FD_DEADLINE, async {
        loop {
            stream.readable().await?;
            let result = stream.try_io(Interest::READABLE, || {
                let mut marker = [0; 1];
                let mut space = [MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(2))];
                let mut control = RecvAncillaryBuffer::new(&mut space);
                let message = rustix::net::recvmsg(
                    stream,
                    &mut [IoSliceMut::new(&mut marker)],
                    &mut control,
                    RecvFlags::CMSG_CLOEXEC,
                )
                .map_err(std::io::Error::from)?;
                let mut fds = Vec::new();
                let mut unexpected = false;
                for ancillary in control.drain() {
                    match ancillary {
                        RecvAncillaryMessage::ScmRights(rights) => fds.extend(rights),
                        _ => unexpected = true,
                    }
                }
                if message.bytes != 1
                    || marker != [FD_MARKER]
                    || unexpected
                    || message
                        .flags
                        .intersects(ReturnFlags::CTRUNC | ReturnFlags::TRUNC)
                    || fds.len() != 1
                {
                    return Err(std::io::Error::new(
                        std::io::ErrorKind::InvalidData,
                        "expected exactly one untruncated capability and marker",
                    ));
                }
                fds.pop()
                    .ok_or_else(|| std::io::Error::other("missing capability"))
            });
            match result {
                Ok(fd) => return Ok(fd),
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => continue,
                Err(error) if error.kind() == std::io::ErrorKind::InvalidData => {
                    return Err(Error::Protocol(
                        "invalid physical capability ancillary message".into(),
                    ));
                }
                Err(error) => return Err(Error::Io(error)),
            }
        }
    })
    .await
    .map_err(|_| Error::Timeout("physical capability receive".into()))?
}

//! Bounded kernel USB notification hints. Authoritative state still requires a scan.
//!
//! No event payload is returned, logged, or used to authorize device access.

use rustix::{
    fd::{AsFd, BorrowedFd, OwnedFd},
    io::Errno,
    net::{
        self, AddressFamily, RecvFlags, SocketFlags, SocketType,
        netlink::{self, SocketAddrNetlink},
        sockopt,
    },
};
use serde::{Deserialize, Serialize};
use std::io;

const CAPACITY: usize = 16 * 1024;

/// Fixed notification continuity failure; no raw event environment is retained.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NotificationLoss {
    /// The OS explicitly reported ENOBUFS on the actual receive queue.
    QueueOverflow,
    /// MSG_TRUNC reported a datagram larger than the fixed receive buffer.
    Truncated,
    /// A complete datagram from the kernel source failed the bounded format checks.
    Malformed,
}

/// One consumed datagram or explicit loss, without raw kernel metadata.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Notice {
    /// A complete kernel USB device/interface/action notification requires a scan.
    Usb,
    /// Kernel receive queue overflow, truncation or malformed trusted input.
    Resync(NotificationLoss),
    /// A non-USB notification or an untrusted source; no device fact was learned.
    Ignored,
}

/// A nonblocking, close-on-exec group-1 kernel notification socket.
///
/// Create this before initial enumeration. Its payloads are advisory; even a
/// privileged synthetic kernel event must not grant access or determine state.
pub struct KernelUsbEvents {
    fd: OwnedFd,
    receive_buffer: usize,
}

impl KernelUsbEvents {
    /// Subscribe as the current ordinary OS user, without sysfs writes or udevd.
    pub fn open() -> io::Result<Self> {
        let fd = net::socket_with(
            AddressFamily::NETLINK,
            SocketType::DGRAM,
            SocketFlags::CLOEXEC | SocketFlags::NONBLOCK,
            Some(netlink::KOBJECT_UEVENT),
        )?;
        sockopt::set_socket_recv_buffer_size(&fd, 64 * 1024)?;
        let receive_buffer = sockopt::socket_recv_buffer_size(&fd)?;
        if receive_buffer > 256 * 1024 {
            return Err(io::Error::other(
                "kernel notification receive buffer exceeds limit",
            ));
        }
        net::bind(&fd, &SocketAddrNetlink::new(0, 1))?;
        Ok(Self { fd, receive_buffer })
    }

    /// Actual kernel receive buffer, which can differ from the requested size.
    pub fn receive_buffer_bytes(&self) -> usize {
        self.receive_buffer
    }

    /// Consume at most one datagram; `None` means WouldBlock or interrupted receive.
    ///
    /// All other OS errors are preserved. Callers must use a finite drain budget
    /// and re-scan after [`Notice::Resync`], rather than infer missing events.
    pub fn receive(&self) -> io::Result<Option<Notice>> {
        let mut buffer = [0_u8; CAPACITY];
        match net::recvfrom(
            &self.fd,
            &mut buffer,
            RecvFlags::DONTWAIT | RecvFlags::TRUNC,
        ) {
            Ok((stored, original, source)) => {
                let trusted = source
                    .and_then(|address| SocketAddrNetlink::try_from(address).ok())
                    .is_some_and(|address| address.pid() == 0 && address.groups() == 1);
                Ok(Some(classify(&buffer[..stored], original, trusted)))
            }
            Err(Errno::AGAIN | Errno::INTR) => Ok(None),
            Err(Errno::NOBUFS) => Ok(Some(Notice::Resync(NotificationLoss::QueueOverflow))),
            Err(error) => Err(error.into()),
        }
    }
}

impl AsFd for KernelUsbEvents {
    fn as_fd(&self) -> BorrowedFd<'_> {
        self.fd.as_fd()
    }
}

fn classify(bytes: &[u8], original: usize, trusted: bool) -> Notice {
    if !trusted {
        return Notice::Ignored;
    }
    if original > bytes.len() {
        return Notice::Resync(NotificationLoss::Truncated);
    }
    if bytes.is_empty() || bytes.last() != Some(&0) {
        return Notice::Resync(NotificationLoss::Malformed);
    }
    let mut fields = bytes.split(|byte| *byte == 0);
    let header = fields.next().unwrap_or_default();
    let mut subsystem = None;
    let mut action = None;
    let mut path = None;
    for (index, field) in fields.enumerate() {
        if index >= 128 || field.len() > 4096 {
            return Notice::Resync(NotificationLoss::Malformed);
        }
        let target = if let Some(value) = field.strip_prefix(b"SUBSYSTEM=") {
            Some((&mut subsystem, value))
        } else if let Some(value) = field.strip_prefix(b"ACTION=") {
            Some((&mut action, value))
        } else {
            field
                .strip_prefix(b"DEVPATH=")
                .map(|value| (&mut path, value))
        };
        if let Some((slot, value)) = target
            && slot.replace(value).is_some()
        {
            return Notice::Resync(NotificationLoss::Malformed);
        }
    }
    if subsystem != Some(b"usb".as_slice()) {
        return Notice::Ignored;
    }
    let (Some(action), Some(path)) = (action, path) else {
        return Notice::Resync(NotificationLoss::Malformed);
    };
    if action.is_empty()
        || path.first() != Some(&b'/')
        || header.len() != action.len() + 1 + path.len()
        || !header.starts_with(action)
        || header.get(action.len()) != Some(&b'@')
        || header.get(action.len() + 1..) != Some(path)
    {
        return Notice::Resync(NotificationLoss::Malformed);
    }
    Notice::Usb
}

#[cfg(test)]
mod tests;

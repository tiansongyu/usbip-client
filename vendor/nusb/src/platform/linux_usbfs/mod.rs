mod transfer;
use std::io;
use std::num::NonZeroU32;

use rustix::io::Errno;
pub(crate) use transfer::TransferData;
mod usbfs;

#[cfg(not(target_os = "android"))]
mod enumeration;

#[cfg(not(target_os = "android"))]
pub use enumeration::{list_buses, list_devices, SysfsPath};

#[cfg(not(target_os = "android"))]
mod hotplug;

#[cfg(not(target_os = "android"))]
pub(crate) use hotplug::LinuxHotplugWatch as HotplugWatch;

mod events;

mod device;
pub(crate) use device::LinuxDevice as Device;
pub(crate) use device::LinuxEndpoint as Endpoint;
pub(crate) use device::LinuxInterface as Interface;
pub use device::{RawCompletion, RawControlTransfer};

/// Ask Linux to probe an unbound USB interface through an already-open USBFS FD.
///
/// This synchronous, potentially blocking operation borrows the descriptor and
/// preserves the real OS errno. It neither opens a path nor claims, detaches,
/// resets, or changes configuration/alternate settings. Linux selects a matching
/// driver; success does not certify the driver or device generation. The caller
/// must validate the FD and independently verify the complete intended state.
/// This wrapper creates no device wrapper, timer, or event thread and submits no
/// control transfer of its own. A selected driver's probe may perform USB I/O.
#[cfg(target_os = "linux")]
pub fn connect_kernel_driver(
    fd: std::os::fd::BorrowedFd<'_>,
    interface: u8,
) -> std::io::Result<()> {
    usbfs::attach_kernel_driver(fd, interface).map_err(std::io::Error::from)
}

#[cfg(all(test, target_os = "linux"))]
mod connect_tests {
    use std::{fs::File, os::fd::AsFd};

    #[test]
    fn borrowed_connect_preserves_errno_without_consuming_the_descriptor() -> std::io::Result<()> {
        let file = File::open("/dev/null")?;
        let result = super::connect_kernel_driver(file.as_fd(), 0);
        assert!(
            matches!(result, Err(error) if error.raw_os_error() == Some(rustix::io::Errno::NOTTY.raw_os_error()))
        );
        file.metadata()?;
        Ok(())
    }
}

use crate::transfer::TransferError;
use crate::ErrorKind;

#[derive(Copy, Clone, Debug, Hash, PartialEq, Eq)]
pub struct DeviceId {
    pub(crate) bus: u8,
    pub(crate) addr: u8,
}

fn errno_to_transfer_error(e: Errno) -> TransferError {
    match e {
        Errno::NODEV | Errno::SHUTDOWN => TransferError::Disconnected,
        Errno::PIPE => TransferError::Stall,
        Errno::NOENT | Errno::CONNRESET | Errno::TIMEDOUT => TransferError::Cancelled,
        Errno::PROTO | Errno::ILSEQ | Errno::OVERFLOW | Errno::COMM | Errno::TIME => {
            TransferError::Fault
        }
        Errno::INVAL => TransferError::InvalidArgument,
        _ => TransferError::Unknown(e.raw_os_error() as u32),
    }
}

pub fn format_os_error_code(f: &mut std::fmt::Formatter<'_>, code: u32) -> std::fmt::Result {
    write!(f, "errno {}", code)
}

impl crate::error::Error {
    pub(crate) fn new_os(kind: ErrorKind, message: &'static str, code: Errno) -> Self {
        Self {
            kind,
            code: NonZeroU32::new(code.raw_os_error() as u32),
            message,
        }
    }

    pub(crate) fn new_io(kind: ErrorKind, message: &'static str, err: io::Error) -> Self {
        Self {
            kind,
            code: err.raw_os_error().and_then(|i| NonZeroU32::new(i as u32)),
            message,
        }
    }
}

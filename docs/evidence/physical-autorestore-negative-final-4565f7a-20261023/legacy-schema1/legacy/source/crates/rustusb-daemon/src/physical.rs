//! Fixed-device capability validation and safe SCM_RIGHTS transport.

use rustix::net::{
    RecvAncillaryBuffer, RecvAncillaryMessage, RecvFlags, ReturnFlags, SendAncillaryBuffer,
    SendAncillaryMessage, SendFlags,
};
use rustusb_core::{Device, Error, PhysicalDeviceReceipt, Result};
use serde::{Deserialize, Serialize};
use std::{
    fmt,
    fs::{self, File},
    io::{IoSlice, IoSliceMut},
    mem::MaybeUninit,
    os::{
        fd::{AsFd, OwnedFd},
        unix::fs::{FileTypeExt, MetadataExt},
    },
    path::{Path, PathBuf},
    time::Duration,
};
use tokio::{io::Interest, net::UnixStream};

pub(crate) const FD_MARKER: u8 = 0x46;
const FD_DEADLINE: Duration = Duration::from_secs(5);

/// An administrator-authorized descriptor for one current whole USB device.
/// The descriptor is a device-scoped write capability, not a per-request sandbox.
pub struct PhysicalGrant {
    /// Durable helper grant ID; finish after releasing this owner's capability.
    pub lease_id: String,
    /// Open usbfs descriptor; dropping closes this process's reference.
    pub fd: OwnedFd,
    /// Validated device metadata, including potentially sensitive descriptors.
    pub device: Device,
    /// Kernel/node generation checked before handing over the capability.
    pub receipt: PhysicalDeviceReceipt,
}

impl fmt::Debug for PhysicalGrant {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PhysicalGrant")
            .field("bus_id", &self.device.bus_id)
            .field("receipt", &self.receipt)
            .finish_non_exhaustive()
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct GrantDescription {
    pub lease_id: String,
    pub device: Device,
    pub receipt: PhysicalDeviceReceipt,
}

pub(crate) fn ensure_eligible(device: &Device) -> Result<()> {
    if device.exported
        || !device.export_blockers.is_empty()
        || device.interfaces.is_empty()
        || device
            .interfaces
            .iter()
            .any(|interface| matches!(interface.driver.as_deref(), Some("usbfs" | "usbip-host")))
    {
        return Err(Error::UnsafeDevice(
            "physical device is unsafe, already exported, claimed, or incompletely described"
                .into(),
        ));
    }
    Ok(())
}

fn paths(device: &Device) -> Result<(u16, PathBuf, PathBuf)> {
    let bus = device
        .bus_id
        .as_str()
        .split_once('-')
        .and_then(|(bus, _)| bus.parse::<u16>().ok())
        .filter(|bus| *bus != 0)
        .ok_or_else(|| Error::InvalidInput("invalid physical bus number".into()))?;
    if !(1..=127).contains(&device.device_address) {
        return Err(Error::InvalidInput("invalid physical USB address".into()));
    }
    Ok((
        bus,
        PathBuf::from(format!(
            "/dev/bus/usb/{bus:03}/{:03}",
            device.device_address
        )),
        Path::new("/sys/bus/usb/devices").join(device.bus_id.as_str()),
    ))
}

fn same_node(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    a.dev() == b.dev()
        && a.ino() == b.ino()
        && a.rdev() == b.rdev()
        && a.file_type().is_char_device()
        && b.file_type().is_char_device()
}

fn validate_node(metadata: &fs::Metadata, bus: u16, address: u32) -> Result<()> {
    let minor = (u32::from(bus) - 1)
        .checked_mul(128)
        .and_then(|base| base.checked_add(address - 1))
        .ok_or_else(|| Error::InvalidInput("usbfs device number overflow".into()))?;
    if !metadata.file_type().is_char_device()
        || metadata.uid() != 0
        || rustix::fs::major(metadata.rdev()) != 189
        || rustix::fs::minor(metadata.rdev()) != minor
    {
        return Err(Error::Permission(
            "expected a root-owned usbfs character node for the authorized bus/address".into(),
        ));
    }
    Ok(())
}

pub(crate) fn open_current(device: Device) -> Result<PhysicalGrant> {
    ensure_eligible(&device)?;
    let (bus, node, sysfs) = paths(&device)?;
    let parent = node
        .parent()
        .ok_or_else(|| Error::InvalidInput("missing usbfs parent".into()))?;
    super::linux::protected_directory(parent, 0)?;
    let sysfs_before = fs::metadata(&sysfs)?;
    let node_before = fs::symlink_metadata(&node)?;
    validate_node(&node_before, bus, device.device_address)?;
    let fd = rustix::fs::open(
        &node,
        rustix::fs::OFlags::RDWR
            | rustix::fs::OFlags::NOFOLLOW
            | rustix::fs::OFlags::CLOEXEC
            | rustix::fs::OFlags::NONBLOCK,
        rustix::fs::Mode::empty(),
    )
    .map_err(std::io::Error::from)?;
    let file = File::from(fd);
    let opened = file.metadata()?;
    let node_after = fs::symlink_metadata(&node)?;
    let sysfs_after = fs::metadata(&sysfs)?;
    if !same_node(&node_before, &opened)
        || !same_node(&opened, &node_after)
        || sysfs_before.dev() != sysfs_after.dev()
        || sysfs_before.ino() != sysfs_after.ino()
    {
        return Err(Error::State(
            "physical device/node generation changed while opening; no capability was delegated"
                .into(),
        ));
    }
    let receipt = PhysicalDeviceReceipt {
        sysfs_device: sysfs_after.dev(),
        sysfs_inode: sysfs_after.ino(),
        node_device: opened.rdev(),
        node_inode: opened.ino(),
        usb_device_number: device.device_address,
        bus_number: bus,
    };
    Ok(PhysicalGrant {
        lease_id: String::new(),
        fd: file.into(),
        device,
        receipt,
    })
}

pub(crate) fn validate_received(
    fd: OwnedFd,
    description: GrantDescription,
) -> Result<PhysicalGrant> {
    super::journal::validate_lease_id(&description.lease_id)?;
    let file = File::from(fd);
    let metadata = file.metadata()?;
    let (bus, _, sysfs) = paths(&description.device)?;
    validate_node(&metadata, bus, description.device.device_address)?;
    let receipt = &description.receipt;
    let current = fs::metadata(sysfs)?;
    if metadata.rdev() != receipt.node_device
        || metadata.ino() != receipt.node_inode
        || current.dev() != receipt.sysfs_device
        || current.ino() != receipt.sysfs_inode
        || bus != receipt.bus_number
        || description.device.device_address != receipt.usb_device_number
    {
        return Err(Error::State(
            "received physical capability does not match its generation receipt".into(),
        ));
    }
    if !rustix::io::fcntl_getfd(&file)
        .map_err(std::io::Error::from)?
        .contains(rustix::io::FdFlags::CLOEXEC)
        || !rustix::fs::fcntl_getfl(&file)
            .map_err(std::io::Error::from)?
            .contains(rustix::fs::OFlags::RDWR)
    {
        return Err(Error::Permission(
            "physical capability requires close-on-exec read/write access".into(),
        ));
    }
    Ok(PhysicalGrant {
        lease_id: description.lease_id,
        fd: file.into(),
        device: description.device,
        receipt: description.receipt,
    })
}

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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Seek, SeekFrom, Write};
    use tokio::io::AsyncWriteExt;

    #[tokio::test]
    async fn descriptor_handoff_preserves_data_and_cloexec_without_root_or_usb() -> Result<()> {
        let (sender, receiver) = UnixStream::pair()?;
        let mut source = tempfile::tempfile()?;
        source.write_all(b"capability-fixture")?;
        source.seek(SeekFrom::Start(0))?;
        let source: OwnedFd = source.into();
        send_fd(&sender, &source).await?;
        let received = receive_fd(&receiver).await?;
        assert!(
            rustix::io::fcntl_getfd(&received)
                .map_err(std::io::Error::from)?
                .contains(rustix::io::FdFlags::CLOEXEC)
        );
        let mut file = File::from(received);
        let mut data = String::new();
        file.read_to_string(&mut data)?;
        assert_eq!(data, "capability-fixture");
        Ok(())
    }

    #[tokio::test]
    async fn ancillary_marker_missing_or_duplicate_rights_are_rejected() -> Result<()> {
        let (mut sender, receiver) = UnixStream::pair()?;
        sender.write_all(&[FD_MARKER]).await?;
        assert!(matches!(
            receive_fd(&receiver).await,
            Err(Error::Protocol(_))
        ));
        let (sender, receiver) = UnixStream::pair()?;
        let file = tempfile::tempfile()?;
        let fds = [file.as_fd(), file.as_fd()];
        let mut space = [MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(2))];
        let mut ancillary = SendAncillaryBuffer::new(&mut space);
        assert!(ancillary.push(SendAncillaryMessage::ScmRights(&fds)));
        sender.writable().await?;
        rustix::net::sendmsg(
            &sender,
            &[IoSlice::new(&[FD_MARKER])],
            &mut ancillary,
            SendFlags::NOSIGNAL,
        )
        .map_err(std::io::Error::from)?;
        assert!(matches!(
            receive_fd(&receiver).await,
            Err(Error::Protocol(_))
        ));
        Ok(())
    }

    #[tokio::test]
    async fn wrong_marker_and_truncated_rights_are_rejected() -> Result<()> {
        for (count, marker) in [(1, 0), (4, FD_MARKER)] {
            let (sender, receiver) = UnixStream::pair()?;
            let file = tempfile::tempfile()?;
            let fds = vec![file.as_fd(); count];
            let mut space = [MaybeUninit::uninit(); rustix::cmsg_space!(ScmRights(4))];
            let mut ancillary = SendAncillaryBuffer::new(&mut space);
            assert!(ancillary.push(SendAncillaryMessage::ScmRights(&fds)));
            sender.writable().await?;
            rustix::net::sendmsg(
                &sender,
                &[IoSlice::new(&[marker])],
                &mut ancillary,
                SendFlags::NOSIGNAL,
            )
            .map_err(std::io::Error::from)?;
            assert!(matches!(
                receive_fd(&receiver).await,
                Err(Error::Protocol(_))
            ));
        }
        Ok(())
    }

    #[test]
    fn regular_files_cannot_be_usb_capabilities() -> Result<()> {
        let file = tempfile::tempfile()?;
        assert!(matches!(
            validate_node(&file.metadata()?, 1, 1),
            Err(Error::Permission(_))
        ));
        Ok(())
    }
}

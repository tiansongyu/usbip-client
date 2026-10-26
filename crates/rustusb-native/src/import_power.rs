//! Fixed per-import runtime PM policy; never called by discovery/reconciliation.
use crate::NativeSession;
use rustusb_core::{Error, Result};
use std::path::Path;

#[cfg(not(target_os = "linux"))]
pub(crate) fn prevent_autosuspend(
    _devices: &Path,
    _session: &NativeSession,
    _uid: u32,
) -> Result<()> {
    Err(Error::Unsupported(
        "imported USB runtime power policy requires Linux".into(),
    ))
}

#[cfg(target_os = "linux")]
pub(crate) fn prevent_autosuspend(devices: &Path, session: &NativeSession, uid: u32) -> Result<()> {
    linux::prevent_autosuspend(devices, session, uid)
}

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use rustix::fs::{Mode, OFlags, open, openat};
    use std::{
        fs::{self, File},
        io::{Read, Seek, SeekFrom, Write},
        os::unix::fs::MetadataExt,
    };

    const LIMIT: u64 = 64;
    const READ: OFlags = OFlags::RDONLY
        .union(OFlags::NOFOLLOW)
        .union(OFlags::CLOEXEC)
        .union(OFlags::NONBLOCK);

    fn require(ok: bool, message: &str) -> Result<()> {
        if ok {
            Ok(())
        } else {
            Err(Error::State(message.into()))
        }
    }

    fn trusted(file: &File, uid: u32, device: u64, directory: bool) -> Result<()> {
        let metadata = file.metadata()?;
        require(
            metadata.uid() == uid
                && metadata.dev() == device
                && metadata.mode() & 0o022 == 0
                && if directory {
                    metadata.is_dir()
                } else {
                    metadata.is_file()
                },
            "untrusted imported-device power attribute",
        )
    }

    fn attribute(directory: &File, name: &str, uid: u32, writable: bool) -> Result<File> {
        let flags = if writable {
            READ.difference(OFlags::RDONLY).union(OFlags::RDWR)
        } else {
            READ
        };
        let file = File::from(
            openat(directory, name, flags, Mode::empty()).map_err(std::io::Error::from)?,
        );
        trusted(&file, uid, directory.metadata()?.dev(), false)?;
        Ok(file)
    }

    fn text(file: &mut File) -> Result<String> {
        file.seek(SeekFrom::Start(0))?;
        let mut bytes = Vec::new();
        Read::by_ref(file).take(LIMIT + 1).read_to_end(&mut bytes)?;
        require(
            !bytes.is_empty() && bytes.len() <= LIMIT as usize,
            "invalid imported power attribute length",
        )?;
        String::from_utf8(bytes)
            .map_err(|_| Error::State("invalid imported power attribute encoding".into()))
    }

    fn number(directory: &File, name: &str, radix: u32, uid: u32) -> Result<u32> {
        let value = text(&mut attribute(directory, name, uid, false)?)?;
        let value = value.trim();
        require(
            !value.is_empty() && value.chars().all(|c| c.is_digit(radix)),
            "invalid imported USB identity attribute",
        )?;
        u32::from_str_radix(value, radix)
            .map_err(|_| Error::State("imported USB identity attribute out of range".into()))
    }

    fn identity(directory: &File, alias: &Path, session: &NativeSession, uid: u32) -> Result<()> {
        let expected = session.local_device_generation.ok_or_else(|| {
            Error::State("import power policy needs the original local generation".into())
        })?;
        trusted(directory, uid, expected.filesystem_device, true)?;
        let opened = directory.metadata()?;
        let current = fs::metadata(alias)?;
        require(
            opened.ino() == expected.inode
                && current.dev() == expected.filesystem_device
                && current.ino() == expected.inode
                && session.local_device_address == Some(number(directory, "devnum", 10, uid)?)
                && session.vendor_id.map(u32::from)
                    == Some(number(directory, "idVendor", 16, uid)?)
                && session.product_id.map(u32::from)
                    == Some(number(directory, "idProduct", 16, uid)?),
            "imported USB generation or identity changed during power policy",
        )
    }

    pub(super) fn write_delay(writer: &mut impl Write) -> Result<()> {
        // A short/error store may already have effects. Never retry its suffix.
        require(
            writer.write(b"-1\n")? == 3,
            "short imported USB power store; effects require reconciliation",
        )
    }

    pub(super) fn prevent_autosuspend(
        devices: &Path,
        session: &NativeSession,
        uid: u32,
    ) -> Result<()> {
        apply(devices, session, uid, || Ok(()))
    }

    pub(super) fn apply(
        devices: &Path,
        session: &NativeSession,
        uid: u32,
        before_store: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        require(
            session.status == 6
                && session.remote_device_id.is_some()
                && session
                    .local_device_address
                    .is_some_and(|v| (1..=255).contains(&v)),
            "import power policy needs a complete USED receipt",
        )?;
        let bus = session
            .local_bus_id
            .as_ref()
            .ok_or_else(|| Error::State("import power policy needs a local bus receipt".into()))?;
        let alias = devices.join(bus.as_str());
        // Only this kernel-owned bus alias may be followed. Every child below
        // the opened, receipt-matched directory is one fixed NOFOLLOW component.
        let directory = File::from(
            open(
                &alias,
                OFlags::RDONLY | OFlags::DIRECTORY | OFlags::CLOEXEC,
                Mode::empty(),
            )
            .map_err(std::io::Error::from)?,
        );
        identity(&directory, &alias, session, uid)?;
        let power = File::from(
            openat(&directory, "power", READ | OFlags::DIRECTORY, Mode::empty())
                .map_err(std::io::Error::from)?,
        );
        trusted(&power, uid, directory.metadata()?.dev(), true)?;
        let mut delay = attribute(&power, "autosuspend_delay_ms", uid, true)?;
        let mut status = attribute(&power, "runtime_status", uid, false)?;
        let value = text(&mut delay)?;
        let value = value
            .trim()
            .parse::<i32>()
            .map_err(|_| Error::State("invalid imported USB autosuspend delay".into()))?;
        identity(&directory, &alias, session, uid)?;
        if value != -1 {
            delay.seek(SeekFrom::Start(0))?;
            // Production supplies a no-op. The filesystem tests exchange the
            // alias here, after the final check, to exercise the actual FD pin.
            before_store()?;
            write_delay(&mut delay)?;
        }
        require(
            text(&mut delay)?.trim() == "-1",
            "imported USB autosuspend policy readback differs",
        )?;
        require(
            text(&mut status)?.trim() == "active",
            "imported USB did not become runtime active",
        )?;
        identity(&directory, &alias, session, uid)
    }
}

#[cfg(all(test, target_os = "linux"))]
#[path = "import_power_tests.rs"]
mod tests;

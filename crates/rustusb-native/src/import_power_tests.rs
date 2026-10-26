//! Unprivileged files model trust/identity failures, not kernel PM callbacks.
use super::*;
use crate::LocalDeviceGeneration;
use rustusb_core::{BusId, VhciPort};
use std::{
    fs,
    io::{self, Write},
    os::unix::fs::{MetadataExt, PermissionsExt, symlink},
    path::PathBuf,
};

struct Fixture {
    _root: tempfile::TempDir,
    devices: PathBuf,
    original: PathBuf,
    replacement: PathBuf,
    session: NativeSession,
    uid: u32,
}
impl Fixture {
    fn new(delay: &str) -> Result<Self> {
        let root = tempfile::tempdir()?;
        let devices = root.path().join("devices");
        fs::create_dir(&devices)?;
        let original = root.path().join("original");
        let replacement = root.path().join("replacement");
        for path in [&original, &replacement] {
            fs::create_dir_all(path.join("power"))?;
            fs::set_permissions(path, fs::Permissions::from_mode(0o700))?;
            fs::set_permissions(path.join("power"), fs::Permissions::from_mode(0o700))?;
            for (name, value) in [
                ("devnum", "2\n"),
                ("idVendor", "1234\n"),
                ("idProduct", "abcd\n"),
                ("power/runtime_status", "active\n"),
                ("power/autosuspend_delay_ms", delay),
            ] {
                fs::write(path.join(name), value)?;
                fs::set_permissions(path.join(name), fs::Permissions::from_mode(0o600))?;
            }
        }
        symlink(&original, devices.join("9-1"))?;
        let metadata = fs::metadata(&original)?;
        let session = NativeSession {
            port: VhciPort(0),
            status: 6,
            local_bus_id: Some(BusId::new("9-1")?),
            remote_device_id: Some(65538),
            vendor_id: Some(0x1234),
            product_id: Some(0xabcd),
            local_device_address: Some(2),
            local_device_generation: Some(LocalDeviceGeneration {
                filesystem_device: metadata.dev(),
                inode: metadata.ino(),
            }),
        };
        Ok(Self {
            _root: root,
            devices,
            original,
            replacement,
            session,
            uid: rustix::process::geteuid().as_raw(),
        })
    }
    fn run(&self) -> Result<()> {
        prevent_autosuspend(&self.devices, &self.session, self.uid)
    }
    fn swap(&self) -> Result<()> {
        fs::remove_file(self.devices.join("9-1"))?;
        symlink(&self.replacement, self.devices.join("9-1"))?;
        Ok(())
    }
    fn delay(&self, path: &Path) -> Result<String> {
        Ok(fs::read_to_string(path.join("power/autosuspend_delay_ms"))?)
    }
}

#[test]
fn active_original_generation_is_required_even_when_policy_is_already_disabled() -> Result<()> {
    let f = Fixture::new("-1\n")?;
    f.run()?;
    for status in [
        "suspended\n",
        "resuming\n",
        "error\n",
        "unsupported\n",
        "unknown\n",
    ] {
        fs::write(f.original.join("power/runtime_status"), status)?;
        assert!(f.run().is_err(), "{status}");
    }
    assert_eq!(f.delay(&f.replacement)?, "-1\n");
    Ok(())
}

#[test]
fn replacement_before_open_never_changes_same_identity_replacement() -> Result<()> {
    let f = Fixture::new("00\n")?;
    f.swap()?;
    assert!(f.run().is_err());
    assert_eq!(f.delay(&f.replacement)?, "00\n");
    assert_eq!(f.delay(&f.original)?, "00\n");
    Ok(())
}

#[test]
fn last_moment_alias_exchange_cannot_redirect_opened_store_or_return_success() -> Result<()> {
    let f = Fixture::new("00\n")?;
    let result = super::linux::apply(&f.devices, &f.session, f.uid, || f.swap());
    assert!(result.is_err());
    // Equal-sized regular-file storage is only an FD-target test, not a model
    // of kernfs replacing a variable-length decimal value.
    assert_eq!(f.delay(&f.original)?, "-1\n");
    assert_eq!(f.delay(&f.replacement)?, "00\n");
    Ok(())
}

#[test]
fn symlink_children_missing_attributes_and_writable_metadata_are_rejected() -> Result<()> {
    for name in [
        "power",
        "power/autosuspend_delay_ms",
        "power/runtime_status",
        "devnum",
    ] {
        let f = Fixture::new("00\n")?;
        let path = f.original.join(name);
        if path.is_dir() {
            fs::rename(&path, f.original.join("saved-power"))?;
        } else {
            fs::remove_file(&path)?;
        }
        symlink(f.replacement.join(name), &path)?;
        assert!(f.run().is_err(), "{name}");
        assert_eq!(f.delay(&f.replacement)?, "00\n");
    }
    for name in [
        "power",
        "power/autosuspend_delay_ms",
        "power/runtime_status",
    ] {
        let f = Fixture::new("00\n")?;
        fs::set_permissions(f.original.join(name), fs::Permissions::from_mode(0o777))?;
        assert!(f.run().is_err(), "{name}");
        assert_eq!(f.delay(&f.replacement)?, "00\n");
    }
    let f = Fixture::new("00\n")?;
    fs::remove_file(f.original.join("power/autosuspend_delay_ms"))?;
    assert!(f.run().is_err());
    assert!(!f.original.join("power/autosuspend_delay_ms").exists());
    assert!(prevent_autosuspend(&f.devices, &f.session, f.uid.wrapping_add(1)).is_err());
    Ok(())
}

#[test]
fn malformed_oversized_and_incomplete_identity_never_authorize_store() -> Result<()> {
    for value in [
        "".to_owned(),
        "auto\n".to_owned(),
        "1".repeat(65),
        "2147483648\n".to_owned(),
    ] {
        let f = Fixture::new(&value)?;
        assert!(f.run().is_err());
        assert_eq!(f.delay(&f.original)?, value);
    }
    let f = Fixture::new("00\n")?;
    let mut incomplete = f.session.clone();
    incomplete.local_device_generation = None;
    assert!(prevent_autosuspend(&f.devices, &incomplete, f.uid).is_err());
    fs::write(f.original.join("devnum"), "3\n")?;
    assert!(f.run().is_err());
    assert_eq!(f.delay(&f.original)?, "00\n");
    Ok(())
}

#[test]
fn special_files_are_rejected_without_blocking_or_creating_an_attribute() -> Result<()> {
    let f = Fixture::new("00\n")?;
    let path = f.original.join("power/autosuspend_delay_ms");
    fs::remove_file(&path)?;
    rustix::fs::mkfifoat(
        rustix::fs::CWD,
        &path,
        rustix::fs::Mode::RUSR | rustix::fs::Mode::WUSR,
    )
    .map_err(std::io::Error::from)?;
    assert!(f.run().is_err());
    fs::remove_file(&path)?;
    fs::create_dir(&path)?;
    assert!(f.run().is_err());
    Ok(())
}

#[test]
fn partial_or_interrupted_store_is_never_retried() -> Result<()> {
    struct Store {
        calls: usize,
        short: bool,
    }
    impl Write for Store {
        fn write(&mut self, value: &[u8]) -> io::Result<usize> {
            self.calls += 1;
            assert_eq!(value, b"-1\n");
            if self.short {
                Ok(2)
            } else {
                Err(io::Error::from(io::ErrorKind::Interrupted))
            }
        }
        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }
    for short in [true, false] {
        let mut store = Store { calls: 0, short };
        assert!(super::linux::write_delay(&mut store).is_err());
        assert_eq!(store.calls, 1);
    }
    Ok(())
}

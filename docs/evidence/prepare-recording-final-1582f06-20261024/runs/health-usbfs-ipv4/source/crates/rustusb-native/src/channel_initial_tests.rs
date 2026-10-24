//! Filesystem-only initialization fixtures. Never opens a USB node or writes sysfs.
use super::*;
use rustusb_core::BusId;
use rustusb_protocol::RemoteInterface;
use std::os::unix::fs::symlink;

struct Fixture {
    _root: tempfile::TempDir,
    controller: PathBuf,
    devices: PathBuf,
    device: PathBuf,
    row: Row,
    remote: RemoteDevice,
    uid: u32,
}
impl Fixture {
    fn new() -> Result<Self> {
        let root = tempfile::tempdir()?;
        let controller = root.path().join("vhci_hcd.0");
        let devices = root.path().join("devices");
        fs::create_dir_all(controller.join("usb1"))?;
        fs::create_dir(&devices)?;
        let device = controller.join("usb1/1-1");
        symlink(&device, devices.join("1-1"))?;
        let row = Row {
            session: NativeSession {
                port: VhciPort(0),
                status: 6,
                local_bus_id: Some(BusId::new("1-1")?),
                remote_device_id: Some(65538),
                vendor_id: None,
                product_id: None,
                local_device_address: None,
                local_device_generation: None,
            },
            hub: "hs".into(),
            speed: 2,
        };
        let remote = RemoteDevice {
            bus_id: BusId::new("1-2")?,
            path: "/remote".into(),
            bus_number: 1,
            device_number: 2,
            speed: 2,
            vendor_id: 0x1234,
            product_id: 2,
            device_version: 0x100,
            class: 0,
            subclass: 0,
            protocol: 0,
            configuration: 1,
            num_configurations: 1,
            interfaces: vec![RemoteInterface {
                class: 3,
                subclass: 1,
                protocol: 1,
            }],
        };
        Ok(Self {
            _root: root,
            controller,
            devices,
            device,
            row,
            remote,
            uid: rustix::process::geteuid().as_raw(),
        })
    }
    fn identity(&self) -> Result<()> {
        fs::create_dir_all(&self.device)?;
        for (name, value) in [
            ("devnum", "3\n"),
            ("idVendor", "1234\n"),
            ("idProduct", "0002\n"),
        ] {
            fs::write(self.device.join(name), value)?;
        }
        Ok(())
    }
    fn profile(&self) -> Result<()> {
        fs::write(self.device.join("bConfigurationValue"), "1\n")?;
        fs::write(self.device.join("bNumInterfaces"), " 1\n")?;
        let interface = self.device.join("1-1:1.0");
        fs::create_dir_all(&interface)?;
        if !self.devices.join("1-1:1.0").exists() {
            symlink(&interface, self.devices.join("1-1:1.0"))?;
        }
        for (name, value) in [
            ("bInterfaceNumber", "00\n"),
            ("bAlternateSetting", " 0\n"),
            ("bInterfaceClass", "03\n"),
            ("bInterfaceSubClass", "01\n"),
            ("bInterfaceProtocol", "01\n"),
        ] {
            fs::write(interface.join(name), value)?;
        }
        Ok(())
    }
    fn local(&self, observer: &mut InitialObserver) -> Result<Option<NativeSession>> {
        observer.local(&self.row.session, &self.controller, &self.devices, self.uid)
    }
    fn observe_profile(&self, observer: &mut InitialObserver) -> Result<Option<ImportedProfile>> {
        observer.profile(
            &self.row.session,
            &self.remote,
            &self.controller,
            &self.devices,
            self.uid,
        )
    }
}

#[test]
fn used_before_device_add_waits_then_observes_the_same_complete_generation() -> Result<()> {
    let f = Fixture::new()?;
    let mut observer = InitialObserver::default();
    observer.pin_row(&f.row, &f.remote)?;
    assert!(f.local(&mut observer)?.is_none());
    assert!(observer.directory.is_none());
    fs::create_dir(&f.device)?;
    assert!(f.local(&mut observer)?.is_none());
    let first = observer.directory.clone();
    assert!(first.is_some()); // Pin before devnum/VID/PID exist.
    fs::write(f.device.join("devnum"), "3\n")?;
    assert!(f.local(&mut observer)?.is_none());
    f.identity()?;
    let before = f
        .local(&mut observer)?
        .ok_or_else(|| Error::State("fixture incomplete".into()))?;
    assert_eq!(observer.directory, first);
    assert!(f.observe_profile(&mut observer)?.is_none());
    fs::write(f.device.join("bConfigurationValue"), "")?;
    fs::write(f.device.join("bNumInterfaces"), "")?;
    assert!(f.observe_profile(&mut observer)?.is_none());
    fs::write(f.device.join("bConfigurationValue"), "1\n")?;
    fs::write(f.device.join("bNumInterfaces"), "1\n")?;
    assert!(f.observe_profile(&mut observer)?.is_none());
    f.profile()?;
    assert!(f.observe_profile(&mut observer)?.is_some());
    observer.pin_row(&f.row, &f.remote)?;
    let after = f
        .local(&mut observer)?
        .ok_or_else(|| Error::State("fixture incomplete".into()))?;
    assert!(crate::same_identity(&before, &after));
    observer.check_local(&after)?;
    Ok(())
}

#[test]
fn directory_is_pinned_before_attributes_and_cannot_be_replaced_or_lost() -> Result<()> {
    let f = Fixture::new()?;
    let mut observer = InitialObserver::default();
    observer.pin_row(&f.row, &f.remote)?;
    fs::create_dir(&f.device)?;
    assert!(f.local(&mut observer)?.is_none());
    let prior = observer.directory.clone();
    fs::rename(&f.device, f.controller.join("usb1/old"))?;
    assert!(f.local(&mut observer).is_err());
    f.identity()?;
    assert!(f.local(&mut observer).is_err());
    assert_eq!(observer.directory, prior);
    Ok(())
}

#[test]
fn only_never_seen_absent_attributes_are_pending() -> Result<()> {
    let f = Fixture::new()?;
    let mut observer = InitialObserver::default();
    fs::create_dir(&f.device)?;
    fs::write(f.device.join("devnum"), "3\n")?;
    assert!(f.local(&mut observer)?.is_none());
    fs::remove_file(f.device.join("devnum"))?;
    assert!(
        matches!(f.local(&mut observer), Err(Error::Io(e)) if e.kind()==std::io::ErrorKind::NotFound)
    );
    fs::create_dir(f.device.join("devnum"))?;
    assert!(f.local(&mut observer).is_err()); // Non-file I/O is never Pending.
    Ok(())
}

#[test]
fn malformed_or_wrong_owner_initial_identity_never_retries() -> Result<()> {
    let f = Fixture::new()?;
    f.identity()?;
    for invalid in ["", " \n", "0", "128", "-1", "+3", "3junk", "4294967296"] {
        fs::write(f.device.join("devnum"), invalid)?;
        assert!(f.local(&mut InitialObserver::default()).is_err());
    }
    f.identity()?;
    assert!(
        InitialObserver::default()
            .local(
                &f.row.session,
                &f.controller,
                &f.devices,
                f.uid.wrapping_add(1)
            )
            .is_err()
    );
    fs::write(f.device.join("idVendor"), [0xff])?;
    assert!(f.local(&mut InitialObserver::default()).is_err());
    Ok(())
}

#[test]
fn first_used_row_cannot_change_during_pending_initialization() -> Result<()> {
    let f = Fixture::new()?;
    let mut observer = InitialObserver::default();
    observer.pin_row(&f.row, &f.remote)?;
    for change in 0..6 {
        let mut row = f.row.clone();
        match change {
            0 => row.session.local_bus_id = Some(BusId::new("1-2")?),
            1 => row.session.port = VhciPort(1),
            2 => row.session.remote_device_id = Some(65539),
            3 => row.speed = 3,
            4 => row.hub = "ss".into(),
            _ => row.session.status = 4,
        }
        assert!(observer.check_row(&row).is_err());
    }
    observer.check_row(&f.row)?;
    Ok(())
}

#[test]
fn unconfigured_profile_requires_both_empty_attributes_and_zero_interfaces() -> Result<()> {
    let f = Fixture::new()?;
    f.identity()?;
    let mut observer = InitialObserver::default();
    f.local(&mut observer)?;
    fs::write(f.device.join("bConfigurationValue"), "")?;
    fs::write(f.device.join("bNumInterfaces"), "")?;
    assert!(f.observe_profile(&mut observer)?.is_none());
    for (config, count) in [("", "1"), ("1", ""), (" \n", ""), ("0", "0")] {
        fs::write(f.device.join("bConfigurationValue"), config)?;
        fs::write(f.device.join("bNumInterfaces"), count)?;
        assert!(f.observe_profile(&mut observer).is_err());
    }
    f.profile()?;
    fs::write(f.device.join("bConfigurationValue"), "")?;
    fs::write(f.device.join("bNumInterfaces"), "")?;
    assert!(f.observe_profile(&mut observer).is_err());
    Ok(())
}

#[test]
fn incomplete_interfaces_can_appear_but_complete_profile_mismatch_fails() -> Result<()> {
    let f = Fixture::new()?;
    f.identity()?;
    f.profile()?;
    let mut observer = InitialObserver::default();
    f.local(&mut observer)?;
    let interface = f.device.join("1-1:1.0");
    fs::remove_file(interface.join("bInterfaceProtocol"))?;
    assert!(f.observe_profile(&mut observer)?.is_none());
    fs::write(interface.join("bInterfaceProtocol"), "01\n")?;
    assert!(f.observe_profile(&mut observer)?.is_some());
    fs::write(interface.join("bInterfaceClass"), "08\n")?;
    assert!(f.observe_profile(&mut observer).is_err());
    fs::write(interface.join("bInterfaceClass"), "03\n")?;
    fs::remove_file(interface.join("bInterfaceProtocol"))?;
    assert!(f.observe_profile(&mut observer).is_err());
    Ok(())
}

#[test]
fn final_generation_recheck_rejects_replacement_after_complete_profile() -> Result<()> {
    let f = Fixture::new()?;
    f.identity()?;
    f.profile()?;
    let mut observer = InitialObserver::default();
    observer.pin_row(&f.row, &f.remote)?;
    let before = f
        .local(&mut observer)?
        .ok_or_else(|| Error::State("fixture incomplete".into()))?;
    assert!(f.observe_profile(&mut observer)?.is_some());
    fs::rename(&f.device, f.controller.join("usb1/old"))?;
    f.identity()?;
    assert!(f.local(&mut observer).is_err());
    let replacement = observe_local_fixture(&f)?;
    assert!(observer.check_local(&replacement).is_err());
    assert!(!crate::same_identity(&before, &replacement));
    Ok(())
}
fn observe_local_fixture(f: &Fixture) -> Result<NativeSession> {
    InitialObserver::default()
        .local(&f.row.session, &f.controller, &f.devices, f.uid)?
        .ok_or_else(|| Error::State("fixture replacement incomplete".into()))
}

use super::*;
use std::os::unix::fs::{PermissionsExt, symlink};

fn secure_tree(p: &Path) -> Result<()> {
    for item in fs::read_dir(p)? {
        let p = item?.path();
        let m = fs::symlink_metadata(&p)?;
        if m.file_type().is_symlink() {
            continue;
        }
        if m.is_dir() {
            secure_tree(&p)?;
            fs::set_permissions(&p, fs::Permissions::from_mode(0o755))?;
        } else {
            fs::set_permissions(&p, fs::Permissions::from_mode(0o644))?;
        }
    }
    Ok(())
}
struct Fixture {
    _temp: tempfile::TempDir,
    sys: PathBuf,
    proc: PathBuf,
    pci: PathBuf,
    uid: u32,
}
impl Fixture {
    fn new() -> Result<Self> {
        let temp = tempfile::tempdir()?;
        let sys = temp.path().join("sys");
        let proc = temp.path().join("proc");
        let pci = sys.join("devices/pci0000:00/0000:00:06.0");
        for p in [
            &pci,
            &sys.join("bus/pci/devices"),
            &sys.join("bus/pci/drivers/xhci_hcd"),
            &sys.join("bus/usb/devices"),
            &sys.join("bus/usb/drivers/usb"),
            &sys.join("bus/usb/drivers/hub"),
            &sys.join("module/usbcore/parameters"),
            &proc.join("sys/kernel/random"),
        ] {
            fs::create_dir_all(p)?;
        }
        symlink(&pci, sys.join("bus/pci/devices/0000:00:06.0"))?;
        symlink(sys.join("bus/pci/drivers/xhci_hcd"), pci.join("driver"))?;
        for (n, v) in [
            ("vendor", "0x1234"),
            ("device", "0x000d"),
            ("subsystem_vendor", "0x1af4"),
            ("subsystem_device", "0x1100"),
            ("class", "0x0c0330"),
        ] {
            fs::write(pci.join(n), format!("{v}\n"))?;
        }
        fs::write(
            proc.join("cmdline"),
            "root=/dev/vda1 usbcore.authorized_default=0 console=ttyS0\n",
        )?;
        fs::write(
            proc.join("sys/kernel/random/boot_id"),
            "11111111-2222-3333-4444-555555555555\n",
        )?;
        fs::write(
            sys.join("module/usbcore/parameters/authorized_default"),
            "0\n",
        )?;
        for (bus, speed) in [(1, 10000), (2, 480)] {
            let root = pci.join(format!("usb{bus}"));
            fs::create_dir(&root)?;
            symlink(&root, sys.join(format!("bus/usb/devices/usb{bus}")))?;
            for (n, v) in [
                ("busnum", bus.to_string()),
                ("devnum", "1".into()),
                ("devpath", "0".into()),
                ("bDeviceClass", "09".into()),
                ("authorized", "1".into()),
                ("speed", speed.to_string()),
                ("authorized_default", "0".into()),
                ("interface_authorized_default", "1".into()),
            ] {
                fs::write(root.join(n), v + "\n")?;
            }
            let hub = root.join(format!("{bus}-0:1.0"));
            fs::create_dir(&hub)?;
            fs::write(hub.join("bInterfaceClass"), "09\n")?;
            symlink(sys.join("bus/usb/drivers/hub"), hub.join("driver"))?;
            fs::create_dir(hub.join(format!("usb{bus}-port1")))?;
        }
        secure_tree(temp.path())?;
        let uid = fs::metadata(&sys)?.uid();
        Ok(Self {
            _temp: temp,
            sys,
            proc,
            pci,
            uid,
        })
    }
    fn open(&self) -> Result<PinnedController> {
        Environment::open(&self.sys, &self.proc, self.uid, false)?
            .pin(&PciAddress::new("0000:00:06.0")?)
    }
    fn arrival(&self) -> Result<PathBuf> {
        let p = self.pci.join("usb2/2-1");
        fs::create_dir(&p)?;
        symlink(&p, self.sys.join("bus/usb/devices/2-1"))?;
        symlink(self.sys.join("bus/usb/drivers/usb"), p.join("driver"))?;
        for (n, v) in [
            ("authorized", "0\n"),
            ("bDeviceClass", "00\n"),
            ("bConfigurationValue", "\n"),
        ] {
            fs::write(p.join(n), v)?;
        }
        secure_tree(&self.sys)?;
        Ok(p)
    }
}
#[test]
fn strict_addresses_and_boot_parameter_scope() -> Result<()> {
    for s in ["0000:00:00.0", "ffff:ff:1f.7"] {
        PciAddress::new(s)?;
    }
    for s in [
        "0000:00:20.0",
        "0000:00:06.8",
        "0000:00:0A.0",
        "../0000:00:06.0",
        "0000:00:6.0",
        "0000:00:06.0/",
        "é000:00:06.0",
    ] {
        assert!(PciAddress::new(s).is_err(), "{s}");
    }
    assert!(boot_default(
        "a=b usbcore.authorized_default=0 -- usbcore.authorized_default=1"
    )?);
    assert!(!boot_default("a=b -- usbcore.authorized_default=0")?);
    assert!(!boot_default("root=/dev/vda1")?);
    for s in [
        "usbcore.authorized_default=0 usbcore.authorized_default=0",
        "usbcore.authorized-default=0",
        "usbcore.authorized_default=00",
        "usbcore.authorized_default=1",
        "usbcore.authorized_default",
        "usbcore.authorized_default=\"0\"",
        "root='foo' usbcore.authorized_default=0",
        "usbcore.authorized_default=0\t",
        "usbcore.authorized_default=0\\",
    ] {
        assert!(boot_default(s).is_err(), "{s}");
    }
    Ok(())
}
#[test]
fn actual_two_root_writes_and_retained_readback() -> Result<()> {
    let f = Fixture::new()?;
    f.arrival()?;
    let mut pin = f.open()?;
    let before = pin.inspect()?;
    assert_eq!(
        before.roots.iter().map(|r| r.speed).collect::<Vec<_>>(),
        vec![10000, 480]
    );
    pin.prepare(&before.selector, &before.boot_id)?;
    let after = pin.arm()?;
    assert_eq!(after.arrivals, before.arrivals);
    assert!(
        after
            .roots
            .iter()
            .all(|r| r.device_default == 0 && r.interface_default == 0)
    );
    assert_eq!(fs::read(f.pci.join("usb2/2-1/authorized"))?, b"0\n");
    assert_eq!(pin.inspect()?, after);
    Ok(())
}
#[test]
fn boot_drift_and_selection_errors_precede_writes() -> Result<()> {
    for field in ["boot", "global", "default", "selector", "epoch"] {
        let f = Fixture::new()?;
        let mut pin = f.open()?;
        let mut s = pin.inspect()?;
        match field {
            "boot" => fs::write(f.proc.join("cmdline"), "root=/dev/vda1\n")?,
            "global" => fs::write(
                f.sys.join("module/usbcore/parameters/authorized_default"),
                "1\n",
            )?,
            "default" => fs::write(f.pci.join("usb1/authorized_default"), "1\n")?,
            "selector" => s.selector.vendor_id ^= 1,
            _ => s.boot_id = "00000000-0000-0000-0000-000000000000".into(),
        }
        assert!(pin.prepare(&s.selector, &s.boot_id).is_err(), "{field}");
        assert_eq!(
            fs::read(f.pci.join("usb1/interface_authorized_default"))?,
            b"1\n"
        );
    }
    Ok(())
}
#[test]
fn rejects_incomplete_extra_replaced_and_unsafe_topologies() -> Result<()> {
    for cause in [
        "missing",
        "extra",
        "root_replaced",
        "attribute_replaced",
        "authorized",
        "hub",
        "configured",
        "interface",
        "descendant",
        "non_generic",
        "permissions",
        "symlink",
    ] {
        let f = Fixture::new()?;
        let target = f.arrival()?;
        let mut pin = f.open()?;
        let s = pin.inspect()?;
        pin.prepare(&s.selector, &s.boot_id)?;
        match cause {
            "missing" => fs::rename(f.pci.join("usb1"), f.pci.join("old-root"))?,
            "extra" => fs::create_dir(f.pci.join("usb3"))?,
            "root_replaced" => {
                fs::rename(f.pci.join("usb1"), f.pci.join("old-root"))?;
                fs::create_dir(f.pci.join("usb1"))?;
            }
            "attribute_replaced" => {
                fs::rename(
                    f.pci.join("usb1/interface_authorized_default"),
                    f.pci.join("old-attr"),
                )?;
                fs::write(f.pci.join("usb1/interface_authorized_default"), "1\n")?;
            }
            "authorized" => fs::write(target.join("authorized"), "1\n")?,
            "hub" => fs::write(target.join("bDeviceClass"), "09\n")?,
            "configured" => fs::write(target.join("bConfigurationValue"), "1\n")?,
            "interface" => fs::create_dir(target.join("2-1:1.0"))?,
            "descendant" => fs::create_dir(target.join("unexpected"))?,
            "non_generic" => {
                fs::remove_file(target.join("driver"))?;
                symlink(&f.pci, target.join("driver"))?;
            }
            "permissions" => fs::set_permissions(
                f.pci.join("usb1/interface_authorized_default"),
                fs::Permissions::from_mode(0o666),
            )?,
            _ => {
                fs::remove_file(f.pci.join("usb1/interface_authorized_default"))?;
                symlink(
                    f.pci.join("usb2/interface_authorized_default"),
                    f.pci.join("usb1/interface_authorized_default"),
                )?;
            }
        }
        assert!(pin.arm().is_err(), "{cause}");
        assert_eq!(
            fs::read(f.pci.join("usb2/interface_authorized_default"))?,
            b"1\n",
            "{cause}"
        );
    }
    Ok(())
}
#[test]
fn short_store_is_not_retried() {
    struct Short(usize);
    impl Write for Short {
        fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
            self.0 += 1;
            Ok(1)
        }
        fn flush(&mut self) -> std::io::Result<()> {
            Ok(())
        }
    }
    let mut s = Short(0);
    assert!(write_zero(&mut s).is_err());
    assert_eq!(s.0, 1);
}

#[test]
fn preparation_is_explicit_single_use_and_failed_prepare_revokes_it() -> Result<()> {
    let f = Fixture::new()?;
    let mut pin = f.open()?;
    let s = pin.inspect()?;
    assert!(pin.arm().is_err());
    pin.prepare(&s.selector, &s.boot_id)?;
    assert!(
        pin.prepare(&s.selector, "00000000-0000-0000-0000-000000000000")
            .is_err()
    );
    assert!(pin.arm().is_err());
    pin.prepare(&s.selector, &s.boot_id)?;
    fs::write(f.pci.join("usb1/interface_authorized_default"), "0\n")?;
    assert!(pin.arm().is_err());
    assert!(pin.arm().is_err());
    assert_eq!(
        fs::read(f.pci.join("usb2/interface_authorized_default"))?,
        b"1\n"
    );
    pin.prepare(&s.selector, &s.boot_id)?;
    pin.arm()?;
    assert!(pin.arm().is_err());
    Ok(())
}

#[test]
fn full_tail_revalidation_rejects_mixed_observations() -> Result<()> {
    for cause in ["boot", "global", "root", "attribute", "arrival"] {
        let f = Fixture::new()?;
        let arrival = f.arrival()?;
        let mut pin = f.open()?;
        let pci = f.pci.clone();
        let sys = f.sys.clone();
        let proc = f.proc.clone();
        pin.observation_hook = Some(Box::new(move || {
            match cause {
                "boot" => fs::write(
                    proc.join("sys/kernel/random/boot_id"),
                    "22222222-2222-3333-4444-555555555555\n",
                )?,
                "global" => fs::write(
                    sys.join("module/usbcore/parameters/authorized_default"),
                    "1\n",
                )?,
                "root" => fs::rename(pci.join("usb1"), pci.join("gone"))?,
                "attribute" => fs::write(pci.join("usb1/interface_authorized_default"), "0\n")?,
                _ => fs::write(arrival.join("authorized"), "1\n")?,
            }
            Ok(())
        }));
        assert!(pin.inspect().is_err(), "{cause}");
        assert_eq!(
            fs::read(f.pci.join("usb2/interface_authorized_default"))?,
            b"1\n"
        );
    }
    Ok(())
}
#[test]
fn ancestor_and_root_interface_drift_refuse_stores() -> Result<()> {
    for cause in [
        "alias_mode",
        "ancestor_mode",
        "alias_symlink",
        "physical_symlink",
        "missing_hub",
        "hub_symlink",
        "hub_owner",
        "hub_class",
        "unexpected",
    ] {
        let f = Fixture::new()?;
        let mut pin = f.open()?;
        let s = pin.inspect()?;
        pin.prepare(&s.selector, &s.boot_id)?;
        let hub = f.pci.join("usb1/1-0:1.0");
        match cause {
            "alias_mode" => {
                fs::set_permissions(f.sys.join("bus/usb"), fs::Permissions::from_mode(0o777))?
            }
            "ancestor_mode" => fs::set_permissions(
                f.sys.join("devices/pci0000:00"),
                fs::Permissions::from_mode(0o777),
            )?,
            "alias_symlink" => {
                let p = f.sys.join("bus/usb/devices");
                let old = f.sys.join("old-aliases");
                fs::rename(&p, &old)?;
                symlink(&old, &p)?;
            }
            "physical_symlink" => {
                let p = f.sys.join("devices/pci0000:00");
                let old = f.sys.join("old-physical");
                fs::rename(&p, &old)?;
                symlink(&old, &p)?;
            }
            "missing_hub" => fs::remove_dir_all(&hub)?,
            "hub_symlink" => {
                let old = f.pci.join("old-hub");
                fs::rename(&hub, &old)?;
                symlink(&old, &hub)?;
            }
            "hub_owner" => {
                fs::remove_file(hub.join("driver"))?;
                symlink(f.sys.join("bus/usb/drivers/usb"), hub.join("driver"))?;
            }
            "hub_class" => fs::write(hub.join("bInterfaceClass"), "00\n")?,
            _ => fs::create_dir(f.pci.join("usb1/unknown-device"))?,
        }
        assert!(pin.arm().is_err(), "{cause}");
        assert_eq!(
            fs::read(f.pci.join("usb2/interface_authorized_default"))?,
            b"1\n"
        );
    }
    Ok(())
}

#[test]
fn original_export_scope_uses_components_not_current_bus_aliases() -> Result<()> {
    let f = Fixture::new()?;
    let pin = f.open()?;
    assert!(pin.contains_original_device(&f.pci.join("usb2/2-1"))?);
    assert!(pin.contains_original_device(&f.pci.join("usb2/2-1/2-1.2"))?);
    assert!(
        !pin.contains_original_device(&f.sys.join("devices/pci0000:00/0000:00:06.00/usb3/3-1"))?
    );
    assert!(
        !pin.contains_original_device(&f.sys.join("devices/pci0000:00/0000:00:07.0/usb2/2-1"))?
    );
    for p in [
        f.pci.clone(),
        f.pci.join("usb2"),
        f.pci.join("usb2/../2-1"),
        f.sys.join("bus/usb/devices/2-1"),
    ] {
        assert!(pin.contains_original_device(&p).is_err());
    }
    Ok(())
}
#[test]
fn physical_scope_requires_original_boot_generation_and_address() -> Result<()> {
    for other in [false, true] {
        let f = Fixture::new()?;
        let p = if other {
            let p = f.sys.join("devices/pci0000:00/0000:00:07.0/usb3/3-1");
            fs::create_dir_all(&p)?;
            symlink(&p, f.sys.join("bus/usb/devices/3-1"))?;
            p
        } else {
            f.arrival()?
        };
        let bus = if other { 3 } else { 2 };
        fs::write(p.join("busnum"), format!("{bus}\n"))?;
        fs::write(p.join("devnum"), "4\n")?;
        secure_tree(&f.sys)?;
        let pin = f.open()?;
        let m = fs::metadata(&p)?;
        let id = format!("{bus}-1").parse()?;
        let boot = "11111111-2222-3333-4444-555555555555";
        let mut receipt = rustusb_core::PhysicalDeviceReceipt {
            sysfs_device: m.dev(),
            sysfs_inode: m.ino(),
            node_device: 999,
            node_inode: 999,
            usb_device_number: 4,
            bus_number: bus,
        };
        assert_eq!(
            pin.contains_physical_generation(&id, &receipt, boot)?,
            !other
        );
        receipt.node_inode += 1;
        assert_eq!(
            pin.contains_physical_generation(&id, &receipt, boot)?,
            !other
        );
        assert!(
            pin.contains_physical_generation(&id, &receipt, "00000000-0000-0000-0000-000000000000")
                .is_err()
        );
        fs::write(p.join("devnum"), "5\n")?;
        assert!(
            pin.contains_physical_generation(&id, &receipt, boot)
                .is_err()
        );
        fs::write(p.join("devnum"), "4\n")?;
        fs::rename(&p, p.with_extension("old"))?;
        assert!(
            pin.contains_physical_generation(&id, &receipt, boot)
                .is_err()
        );
        fs::create_dir(&p)?;
        fs::write(p.join("busnum"), format!("{bus}\n"))?;
        fs::write(p.join("devnum"), "4\n")?;
        secure_tree(&f.sys)?;
        assert!(
            pin.contains_physical_generation(&id, &receipt, boot)
                .is_err()
        );
    }
    Ok(())
}

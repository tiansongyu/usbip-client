//! Isolated sysfs fixtures verify metadata, reenumeration and conservative eligibility.

use rustusb_core::{DeviceDiscovery, Result};
use rustusb_linux::SysfsDiscovery;
use std::{
    fs,
    path::{Path, PathBuf},
};

fn write(path: &Path, name: &str, value: &str) -> std::io::Result<()> {
    fs::write(path.join(name), format!("{value}\n"))
}

struct Fixture {
    temp: tempfile::TempDir,
    root: PathBuf,
}

impl Fixture {
    fn new() -> std::io::Result<Self> {
        let temp = tempfile::tempdir()?;
        let root = temp.path().join("sys/bus/usb/devices");
        fs::create_dir_all(&root)?;
        Ok(Self { temp, root })
    }

    fn device(
        &self,
        bus: u32,
        address: u32,
        classes: &[u8],
        serial: bool,
    ) -> std::io::Result<PathBuf> {
        let id = format!("{bus}-2.3");
        let path = self
            .temp
            .path()
            .join(format!("sys/devices/pci0000:00/0000:00:14.0/usb{bus}/{id}"));
        fs::create_dir_all(&path)?;
        std::os::unix::fs::symlink(&path, self.root.join(&id))?;
        for (name, value) in [
            ("idVendor", "1234"),
            ("idProduct", "5678"),
            ("bDeviceClass", "00"),
            ("bDeviceSubClass", "00"),
            ("bDeviceProtocol", "00"),
            ("version", "2.00"),
            ("speed", "480"),
            ("manufacturer", "Test"),
            ("product", "Fixture"),
            ("authorized", "1"),
        ] {
            write(&path, name, value)?;
        }
        write(&path, "devnum", &address.to_string())?;
        write(&path, "busnum", &bus.to_string())?;
        write(&path, "bNumInterfaces", &classes.len().to_string())?;
        if serial {
            write(&path, "serial", "test-secret-serial")?;
        }
        let mut descriptors = vec![
            18, 1, 0, 2, 0, 0, 0, 64, 0x34, 0x12, 0x78, 0x56, 0, 1, 1, 2, 3, 1,
        ];
        let total = 9 + classes.len() * 16;
        descriptors.extend([
            9,
            2,
            total as u8,
            (total >> 8) as u8,
            classes.len() as u8,
            1,
            0,
            0x80,
            50,
        ]);
        for (i, class) in classes.iter().copied().enumerate() {
            let intf_name = format!("{id}:1.{i}");
            let intf = path.join(&intf_name);
            fs::create_dir_all(intf.join("ep_81"))?;
            std::os::unix::fs::symlink(&intf, self.root.join(&intf_name))?;
            for (name, value) in [
                ("bInterfaceNumber", format!("{i:02x}")),
                ("bAlternateSetting", "0".into()),
                ("bInterfaceClass", format!("{class:02x}")),
                ("bInterfaceSubClass", "00".into()),
                ("bInterfaceProtocol", "00".into()),
                ("bNumEndpoints", "01".into()),
            ] {
                write(&intf, name, &value)?;
            }
            for (name, value) in [
                ("bEndpointAddress", "81"),
                ("bmAttributes", "03"),
                ("wMaxPacketSize", "0040"),
                ("bInterval", "0a"),
            ] {
                write(&intf.join("ep_81"), name, value)?;
            }
            descriptors.extend([
                9, 4, i as u8, 0, 1, class, 0, 0, 0, 7, 5, 0x81, 3, 64, 0, 10,
            ]);
        }
        fs::write(path.join("descriptors"), descriptors)?;
        Ok(path)
    }

    fn scan(&self) -> Result<Vec<rustusb_core::Device>> {
        SysfsDiscovery::new(&self.root).scan()
    }
}

#[test]
fn metadata_and_fingerprint_survive_bus_and_address_reenumeration() -> Result<()> {
    let fixture = Fixture::new()?;
    fixture.device(1, 3, &[3], true)?;
    // Root hubs and interface-like names must not become devices.
    fs::create_dir(fixture.root.join("usb1"))?;
    let before = fixture.scan()?;
    assert_eq!(before.len(), 1);
    let before = &before[0];
    assert!(
        before.export_blockers.is_empty(),
        "{:?}",
        before.export_blockers
    );
    assert_eq!(before.interfaces[0].endpoints[0].max_packet_size, 64);
    assert_eq!(before.interfaces[0].endpoints[0].interval, 10);
    assert_eq!(before.id.len(), 68);
    assert!(before.id.starts_with("usb-"));
    assert!(!before.topology.contains("usb1"));
    let before_id = before.id.clone();
    fs::remove_file(fixture.root.join("1-2.3"))?;
    fs::remove_file(fixture.root.join("1-2.3:1.0"))?;
    fixture.device(9, 42, &[3], true)?;
    let after = fixture.scan()?;
    assert_eq!(after[0].id, before_id);
    assert_ne!(after[0].bus_id.as_str(), before.bus_id.as_str());
    assert_eq!(after[0].device_address, 42);
    Ok(())
}

#[test]
fn composite_storage_and_inactive_storage_are_blocked() -> Result<()> {
    let fixture = Fixture::new()?;
    let path = fixture.device(1, 3, &[3, 8], false)?;
    let devices = fixture.scan()?;
    assert_eq!(devices[0].interfaces.len(), 2);
    assert!(
        devices[0]
            .export_blockers
            .iter()
            .any(|b| b.starts_with("storage_"))
    );
    // Hide storage in an inactive configuration: visible interface class remains HID.
    let intf = path.join("1-2.3:1.1");
    write(&intf, "bInterfaceClass", "03")?;
    let mut descriptors = fs::read(path.join("descriptors"))?;
    descriptors[17] = 2;
    // The first configuration now contains two HID interfaces.
    descriptors[18 + 9 + 16 + 5] = 3;
    // Configuration 2 contains storage but is not selected in sysfs.
    descriptors.extend([9, 2, 18, 0, 1, 2, 0, 0x80, 50, 9, 4, 0, 0, 0, 8, 6, 0x50, 0]);
    fs::write(path.join("descriptors"), descriptors)?;
    let devices = fixture.scan()?;
    assert!(devices[0].interfaces.iter().all(|i| i.class == 3));
    assert!(
        devices[0]
            .export_blockers
            .iter()
            .any(|b| b.starts_with("storage_"))
    );
    Ok(())
}

#[test]
fn unknown_hubs_short_reads_and_removed_devices_fail_closed() -> Result<()> {
    let fixture = Fixture::new()?;
    let path = fixture.device(1, 3, &[3], false)?;
    write(&path, "bDeviceClass", "09")?;
    assert!(
        fixture.scan()?[0]
            .export_blockers
            .iter()
            .any(|b| b.contains("hub"))
    );
    write(&path, "bDeviceClass", "00")?;
    write(&path.join("1-2.3:1.0"), "bInterfaceClass", "89")?;
    assert!(
        fixture.scan()?[0]
            .export_blockers
            .iter()
            .any(|b| b.contains("unknown_usb"))
    );
    fs::write(path.join("idVendor"), [])?;
    fs::write(path.join("descriptors"), [18, 1, 0])?;
    let devices = fixture.scan()?;
    assert!(
        devices[0]
            .export_blockers
            .iter()
            .any(|b| b.contains("idVendor"))
    );
    assert!(
        devices[0]
            .export_blockers
            .iter()
            .any(|b| b.contains("descriptors_malformed"))
    );
    fs::remove_dir_all(path)?;
    assert!(fixture.scan()?.is_empty());
    Ok(())
}

#[test]
fn kernel_export_occupancy_and_attribute_limits_are_explicit() -> Result<()> {
    let fixture = Fixture::new()?;
    let path = fixture.device(1, 3, &[3], false)?;
    std::os::unix::fs::symlink("/sys/bus/usb/drivers/usbip-host", path.join("driver"))?;
    write(&path, "usbip_status", "2")?;
    let devices = fixture.scan()?;
    assert!(devices[0].exported);
    assert_eq!(devices[0].occupied, Some(true));
    assert!(
        devices[0]
            .export_blockers
            .iter()
            .any(|b| b.contains("in_use"))
    );
    write(&path, "usbip_status", "1")?;
    fs::write(path.join("manufacturer"), vec![b'a'; 5000])?;
    let devices = fixture.scan()?;
    assert_eq!(devices[0].occupied, Some(false));
    assert!(
        devices[0]
            .export_blockers
            .iter()
            .any(|b| b.contains("manufacturer"))
    );
    Ok(())
}

#[test]
fn missing_root_is_reported_instead_of_successful_empty_scan() {
    assert!(
        SysfsDiscovery::new("/definitely/not/a/sysfs/root")
            .scan()
            .is_err()
    );
}

#[test]
fn storage_driver_is_blocked_even_for_vendor_class_quirks() -> Result<()> {
    let fixture = Fixture::new()?;
    let path = fixture.device(1, 3, &[0xff], false)?;
    for driver in ["usb-storage", "uas", "ums-realtek"] {
        let link = path.join("1-2.3:1.0/driver");
        std::os::unix::fs::symlink(format!("/sys/bus/usb/drivers/{driver}"), &link)?;
        let devices = fixture.scan()?;
        assert!(
            devices[0]
                .export_blockers
                .iter()
                .any(|blocker| blocker.starts_with("storage_driver_"))
        );
        fs::remove_file(link)?;
    }
    Ok(())
}

#[test]
fn block_descendants_are_denied_without_following_subsystem_symlinks() -> Result<()> {
    let fixture = Fixture::new()?;
    let path = fixture.device(1, 3, &[0xff], false)?;
    let outside = fixture.temp.path().join("outside-subsystem");
    fs::create_dir_all(outside.join("block/sda"))?;
    std::os::unix::fs::symlink(&outside, path.join("subsystem"))?;
    assert!(fixture.scan()?[0].export_blockers.is_empty());
    fs::create_dir_all(path.join("1-2.3:1.0/host0/target0/0:0:0:0/block/sda"))?;
    assert!(
        fixture.scan()?[0]
            .export_blockers
            .iter()
            .any(|blocker| blocker == "storage_block_device_descendant_present")
    );
    Ok(())
}

#[test]
fn descendant_budget_exhaustion_refuses_to_claim_storage_safety() -> Result<()> {
    let fixture = Fixture::new()?;
    let mut path = fixture.device(1, 3, &[3], false)?;
    for _ in 0..18 {
        path = path.join("nested");
        fs::create_dir(&path)?;
    }
    assert!(
        fixture.scan()?[0]
            .export_blockers
            .iter()
            .any(|blocker| blocker == "storage_descendant_safety_unproven")
    );
    Ok(())
}

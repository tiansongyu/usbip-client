//! Revalidate the current configuration before taking or restoring interfaces.

use super::{Slot, observe};
use rustusb_core::{Device, DeviceDiscovery, Error, PhysicalDeviceReceipt, Result};
use rustusb_linux::SysfsDiscovery;
use std::{collections::BTreeSet, sync::Arc};

fn scope(paths: &observe::Paths, receipt: &PhysicalDeviceReceipt, config: u8) -> Result<()> {
    if !observe::generation(paths, receipt)? || observe::configuration(paths)? != config {
        return Err(Error::State(
            "physical generation or configuration changed during safety observation".into(),
        ));
    }
    Ok(())
}

fn check(
    paths: &observe::Paths,
    receipt: &PhysicalDeviceReceipt,
    grant: &Device,
    config: u8,
    numbers: &[u8],
    owned: &BTreeSet<u8>,
) -> Result<()> {
    scope(paths, receipt, config)?;
    if config == 0 {
        if !numbers.is_empty() || !owned.is_empty() {
            return Err(Error::State(
                "unconfigured physical device still has claimed interfaces".into(),
            ));
        }
        return scope(paths, receipt, config);
    }
    let current = SysfsDiscovery::new(&paths.root)
        .scan()?
        .into_iter()
        .find(|device| device.bus_id.as_str() == paths.bus_id)
        .ok_or_else(|| {
            Error::State("physical safety observation lost the granted device".into())
        })?;
    scope(paths, receipt, config)?;
    if current.id != grant.id
        || current.vendor_id != grant.vendor_id
        || current.product_id != grant.product_id
        || current.device_address != receipt.usb_device_number
        || current.exported
        || current.occupied == Some(true)
        || !current.export_blockers.is_empty()
    {
        return Err(Error::UnsafeDevice(
            "current physical configuration is unsafe, incomplete, or differs from its grant"
                .into(),
        ));
    }
    let expected: BTreeSet<_> = numbers.iter().copied().collect();
    let actual: BTreeSet<_> = current
        .interfaces
        .iter()
        .map(|interface| interface.number)
        .collect();
    if expected.len() != numbers.len()
        || actual != expected
        || actual.len() != current.interfaces.len()
        || !owned.is_subset(&expected)
    {
        return Err(Error::State(
            "physical configuration interfaces differ from the validated descriptor".into(),
        ));
    }
    for interface in current.interfaces {
        let driver = interface.driver.as_deref();
        if driver == Some("usbip-host")
            || driver == Some("usbfs") && !owned.contains(&interface.number)
            || owned.contains(&interface.number) && driver != Some("usbfs")
        {
            return Err(Error::State(
                "physical configuration contains an external or changed interface owner".into(),
            ));
        }
    }
    scope(paths, receipt, config)
}

/// Only already-owned usbfs interfaces may appear while acquiring siblings.
pub(super) async fn before_claim(slot: &Arc<Slot>, config: u8, owned: BTreeSet<u8>) -> Result<()> {
    let numbers = observe::interface_numbers(&slot.usb, config)?;
    let slot = slot.clone();
    tokio::task::spawn_blocking(move || {
        check(
            &slot.paths,
            &slot.receipt,
            &slot.source,
            config,
            &numbers,
            &owned,
        )
    })
    .await
    .map_err(|_| Error::State("physical configuration safety observer failed".into()))?
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        os::unix::fs::{MetadataExt, symlink},
        path::PathBuf,
    };

    struct Fixture {
        _owner: observe::test_fixture::Fixture,
        paths: observe::Paths,
        device: PathBuf,
        grant: Device,
        receipt: PhysicalDeviceReceipt,
    }

    impl Fixture {
        fn new() -> Result<Self> {
            let owner = observe::test_fixture::Fixture::new()?;
            let root = owner.root.join("bus");
            let device = owner.root.join("devices/pci0000:00/controller/usb1/1-2");
            fs::create_dir_all(&root)?;
            fs::create_dir_all(&device)?;
            symlink(&device, root.join("1-2"))?;
            for (name, value) in [
                ("idVendor", "1234"),
                ("idProduct", "5678"),
                ("bDeviceClass", "00"),
                ("bDeviceSubClass", "00"),
                ("bDeviceProtocol", "00"),
                ("version", "2.00"),
                ("speed", "12"),
                ("authorized", "1"),
                ("devnum", "3"),
                ("busnum", "1"),
                ("bNumInterfaces", "2"),
                ("bConfigurationValue", "2"),
            ] {
                fs::write(device.join(name), format!("{value}\n"))?;
            }
            let mut descriptors = vec![
                18, 1, 0, 2, 0, 0, 0, 64, 0x34, 0x12, 0x78, 0x56, 0, 1, 0, 0, 0, 1, 9, 2, 27, 0, 2,
                2, 0, 0x80, 50,
            ];
            for (number, class) in [(0, 2), (1, 0x0a)] {
                let name = format!("1-2:2.{number}");
                let interface = device.join(&name);
                fs::create_dir(&interface)?;
                symlink(&interface, root.join(name))?;
                for (name, value) in [
                    ("bInterfaceNumber", format!("{number:02x}")),
                    ("bAlternateSetting", "0".into()),
                    ("bInterfaceClass", format!("{class:02x}")),
                    ("bInterfaceSubClass", "00".into()),
                    ("bInterfaceProtocol", "00".into()),
                    ("bNumEndpoints", "00".into()),
                ] {
                    fs::write(interface.join(name), format!("{value}\n"))?;
                }
                descriptors.extend([9, 4, number, 0, 0, class, 0, 0, 0]);
            }
            fs::write(device.join("descriptors"), descriptors)?;
            let grant = SysfsDiscovery::new(&root)
                .scan()?
                .pop()
                .ok_or_else(|| Error::State("missing safety fixture".into()))?;
            assert!(
                grant.export_blockers.is_empty(),
                "{:?}",
                grant.export_blockers
            );
            let metadata = fs::metadata(&device)?;
            Ok(Self {
                _owner: owner,
                paths: observe::Paths {
                    root,
                    bus_id: "1-2".into(),
                },
                device,
                grant,
                receipt: PhysicalDeviceReceipt {
                    sysfs_device: metadata.dev(),
                    sysfs_inode: metadata.ino(),
                    node_device: 0,
                    node_inode: 0,
                    usb_device_number: 3,
                    bus_number: 1,
                },
            })
        }
        fn check(&self, owned: &[u8]) -> Result<()> {
            check(
                &self.paths,
                &self.receipt,
                &self.grant,
                2,
                &[0, 1],
                &owned.iter().copied().collect(),
            )
        }
        fn driver(&self, number: u8, name: &str) -> Result<()> {
            symlink(
                format!("/sys/bus/usb/drivers/{name}"),
                self.device.join(format!("1-2:2.{number}/driver")),
            )?;
            Ok(())
        }
    }

    #[test]
    fn current_configuration_allows_only_our_claimed_siblings() -> Result<()> {
        let fixture = Fixture::new()?;
        fixture.check(&[])?;
        fixture.driver(0, "usbfs")?;
        assert!(fixture.check(&[]).is_err());
        fixture.check(&[0])?;
        assert!(fixture.check(&[0, 1]).is_err());
        fixture.driver(1, "usbip-host")?;
        assert!(fixture.check(&[0]).is_err());
        Ok(())
    }

    #[test]
    fn newly_bound_storage_quirks_and_block_children_refuse_claim() -> Result<()> {
        for driver in ["usb-storage", "uas", "ums-test"] {
            let fixture = Fixture::new()?;
            // Class remains vendor-specific rather than declaring Mass Storage.
            fs::write(fixture.device.join("1-2:2.1/bInterfaceClass"), "ff\n")?;
            fixture.driver(1, driver)?;
            assert!(matches!(fixture.check(&[]), Err(Error::UnsafeDevice(_))));
        }
        let fixture = Fixture::new()?;
        fs::create_dir_all(fixture.device.join("1-2:2.1/host0/target/block"))?;
        assert!(matches!(fixture.check(&[]), Err(Error::UnsafeDevice(_))));
        Ok(())
    }

    #[test]
    fn inactive_storage_and_incomplete_descriptors_remain_blocked() -> Result<()> {
        let fixture = Fixture::new()?;
        let mut descriptors = fs::read(fixture.device.join("descriptors"))?;
        descriptors[17] = 2;
        descriptors.extend([9, 2, 18, 0, 1, 3, 0, 0x80, 50, 9, 4, 0, 0, 0, 8, 6, 0x50, 0]);
        fs::write(fixture.device.join("descriptors"), &descriptors)?;
        assert!(fixture.check(&[]).is_err());
        fs::write(fixture.device.join("descriptors"), &descriptors[..20])?;
        assert!(fixture.check(&[]).is_err());
        Ok(())
    }

    #[test]
    fn changed_generation_identity_configuration_or_interface_set_denies_claim() -> Result<()> {
        let fixture = Fixture::new()?;
        assert!(
            check(
                &fixture.paths,
                &fixture.receipt,
                &fixture.grant,
                2,
                &[0],
                &BTreeSet::new()
            )
            .is_err()
        );
        let mut receipt = fixture.receipt.clone();
        receipt.sysfs_inode += 1;
        assert!(
            check(
                &fixture.paths,
                &receipt,
                &fixture.grant,
                2,
                &[0, 1],
                &BTreeSet::new()
            )
            .is_err()
        );
        fs::write(fixture.device.join("idProduct"), "5679\n")?;
        assert!(fixture.check(&[]).is_err());
        fs::write(fixture.device.join("idProduct"), "5678\n")?;
        fs::write(fixture.device.join("bConfigurationValue"), "1\n")?;
        assert!(fixture.check(&[]).is_err());
        Ok(())
    }

    #[test]
    fn configuration_zero_does_not_require_interfaces_or_claim_any() -> Result<()> {
        let fixture = Fixture::new()?;
        fs::write(fixture.device.join("bConfigurationValue"), "\n")?;
        check(
            &fixture.paths,
            &fixture.receipt,
            &fixture.grant,
            0,
            &[],
            &BTreeSet::new(),
        )?;
        assert!(
            check(
                &fixture.paths,
                &fixture.receipt,
                &fixture.grant,
                0,
                &[0],
                &BTreeSet::new()
            )
            .is_err()
        );
        fs::write(fixture.device.join("devnum"), "4\n")?;
        assert!(
            check(
                &fixture.paths,
                &fixture.receipt,
                &fixture.grant,
                0,
                &[],
                &BTreeSet::new()
            )
            .is_err()
        );
        Ok(())
    }
}

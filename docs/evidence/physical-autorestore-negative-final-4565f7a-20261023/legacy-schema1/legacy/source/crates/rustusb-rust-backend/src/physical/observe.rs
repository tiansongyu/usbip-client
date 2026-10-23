//! Fixed-path generation and driver observations, offloaded from Tokio workers.

use super::*;
use nusb::descriptors::TransferType;
use rustusb_protocol::RemoteInterface;
use std::{
    fs::{self, File},
    io::Read,
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::PathBuf,
};

#[derive(Clone)]
pub(super) struct Paths {
    pub root: PathBuf,
    pub bus_id: String,
}

impl Paths {
    fn device(&self) -> PathBuf {
        self.root.join(&self.bus_id)
    }
    fn interface(&self, configuration: u8, number: u8) -> PathBuf {
        self.root
            .join(format!("{}:{configuration}.{number}", self.bus_id))
    }
}

fn bounded(path: &std::path::Path) -> Result<String> {
    let mut bytes = Vec::new();
    File::open(path)?.take(129).read_to_end(&mut bytes)?;
    if bytes.len() > 128 || bytes.iter().any(|byte| !byte.is_ascii()) {
        return Err(Error::State(
            "physical sysfs attribute exceeds observation bounds".into(),
        ));
    }
    String::from_utf8(bytes)
        .map(|value| value.trim().to_owned())
        .map_err(|_| Error::State("physical sysfs attribute is not ASCII".into()))
}

fn alternate(paths: &Paths, config: u8, number: u8) -> Result<u8> {
    bounded(&paths.interface(config, number).join("bAlternateSetting"))?
        .parse::<u8>()
        .map_err(|_| Error::State("invalid physical alternate setting observation".into()))
}

pub(super) fn generation(paths: &Paths, receipt: &PhysicalDeviceReceipt) -> Result<bool> {
    let path = paths.device();
    let metadata = match fs::metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
    };
    if !metadata.is_dir()
        || metadata.dev() != receipt.sysfs_device
        || metadata.ino() != receipt.sysfs_inode
    {
        return Ok(false);
    }
    let bus = bounded(&path.join("busnum"))?
        .parse::<u16>()
        .map_err(|_| Error::State("invalid observed bus number".into()))?;
    let address = bounded(&path.join("devnum"))?
        .parse::<u32>()
        .map_err(|_| Error::State("invalid observed USB address".into()))?;
    Ok(bus == receipt.bus_number && address == receipt.usb_device_number)
}

pub(super) fn configuration(paths: &Paths) -> Result<u8> {
    let text = bounded(&paths.device().join("bConfigurationValue"))?;
    if text.is_empty() {
        return Ok(0);
    }
    text.parse()
        .map_err(|_| Error::State("invalid physical configuration observation".into()))
}

pub(super) fn drivers(paths: &Paths, config: u8, numbers: &[u8]) -> Result<Vec<PhysicalDriver>> {
    let mut drivers = Vec::new();
    for &interface in numbers {
        let path = paths.interface(config, interface);
        if !fs::metadata(&path)?.is_dir() {
            return Err(Error::State("physical interface disappeared".into()));
        }
        let driver = match fs::read_link(path.join("driver")) {
            Ok(link) => {
                let name = link
                    .file_name()
                    .and_then(|name| name.to_str())
                    .filter(|name| {
                        !name.is_empty()
                            && name.len() <= 64
                            && name
                                .bytes()
                                .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
                    })
                    .ok_or_else(|| Error::State("invalid observed kernel driver name".into()))?;
                Some(name.to_owned())
            }
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => None,
            Err(error) => return Err(error.into()),
        };
        drivers.push(PhysicalDriver { interface, driver });
    }
    Ok(drivers)
}

fn restoration_scope(paths: &Paths, receipt: &PhysicalDeviceReceipt, config: u8) -> Result<()> {
    if !generation(paths, receipt)? {
        return Err(Error::State(
            "physical generation changed during restoration".into(),
        ));
    }
    if configuration(paths)? != config {
        return Err(Error::State(
            "physical configuration changed during restoration".into(),
        ));
    }
    Ok(())
}

fn restoration_driver(
    paths: &Paths,
    receipt: &PhysicalDeviceReceipt,
    config: u8,
    number: u8,
) -> Result<Option<String>> {
    restoration_scope(paths, receipt, config)?;
    let driver = drivers(paths, config, &[number])?
        .pop()
        .ok_or_else(|| Error::State("physical restoration interface was not observed".into()))?;
    restoration_scope(paths, receipt, config)?;
    Ok(driver.driver)
}

pub(super) fn alternate_matches(
    paths: &Paths,
    receipt: &PhysicalDeviceReceipt,
    config: u8,
    number: u8,
    expected: u8,
) -> Result<bool> {
    restoration_scope(paths, receipt, config)?;
    let observed = alternate(paths, config, number)?;
    restoration_scope(paths, receipt, config)?;
    Ok(observed == expected)
}

// CONNECT can synchronously bind sibling interfaces through the driver's probe.
// Reobserve every owned interface instead of reusing a pre-CONNECT snapshot.
pub(super) fn reconnect_drivers(
    paths: &Paths,
    receipt: &PhysicalDeviceReceipt,
    config: u8,
    originals: &[PhysicalDriver],
    owned: &BTreeSet<u8>,
    mut connect: impl FnMut(u8) -> Result<()>,
) -> Result<()> {
    for original in originals {
        if !owned.contains(&original.interface) {
            continue;
        }
        let current = restoration_driver(paths, receipt, config, original.interface)?;
        if current == original.driver {
            continue;
        }
        if current.is_some() || original.driver.is_none() {
            return Err(Error::State(
                "an external driver change prevents safe physical restoration".into(),
            ));
        }
        if let Err(error) = connect(original.interface) {
            // A concurrent or sibling probe may have completed between the
            // observation and CONNECT. Only actual original bindings in the
            // same scope can reconcile an error; EBUSY alone proves nothing.
            if restoration_driver(paths, receipt, config, original.interface)? != original.driver {
                return Err(error);
            }
        }
    }
    Ok(())
}

pub(super) async fn verify(slot: &Arc<Slot>) -> Result<bool> {
    let paths = slot.paths.clone();
    let receipt = slot.receipt.clone();
    tokio::task::spawn_blocking(move || generation(&paths, &receipt))
        .await
        .map_err(|_| Error::State("physical generation observer failed".into()))?
}

pub(super) async fn observe_drivers(
    slot: &Arc<Slot>,
    config: u8,
    numbers: Vec<u8>,
) -> Result<Vec<PhysicalDriver>> {
    let paths = slot.paths.clone();
    let receipt = slot.receipt.clone();
    tokio::task::spawn_blocking(move || {
        if !generation(&paths, &receipt)? {
            return Err(Error::State("physical generation is gone".into()));
        }
        drivers(&paths, config, &numbers)
    })
    .await
    .map_err(|_| Error::State("physical driver observer failed".into()))?
}

pub(super) async fn validate_grant(slot: &Arc<Slot>) -> Result<bool> {
    let slot = slot.clone();
    tokio::task::spawn_blocking(move || {
        if !generation(&slot.paths, &slot.receipt)? {
            return Ok(false);
        }
        if configuration(&slot.paths)? != slot.original_configuration {
            return Err(Error::State(
                "physical configuration changed after authorization".into(),
            ));
        }
        for interface in &slot.source.interfaces {
            let alt = alternate(&slot.paths, slot.original_configuration, interface.number)?;
            if alt != interface.alternate_setting {
                return Err(Error::State(
                    "physical alternate setting changed after authorization".into(),
                ));
            }
        }
        let numbers = slot
            .source
            .interfaces
            .iter()
            .map(|interface| interface.number)
            .collect::<Vec<_>>();
        if drivers(&slot.paths, slot.original_configuration, &numbers)?
            != slot.lock()?.original_drivers
        {
            return Err(Error::State(
                "physical driver bindings changed after authorization".into(),
            ));
        }
        Ok(true)
    })
    .await
    .map_err(|_| Error::State("physical grant observer failed".into()))?
}

#[cfg(test)]
pub(super) mod test_fixture {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static NEXT: AtomicU64 = AtomicU64::new(0);
    pub(in super::super) struct Fixture {
        pub root: PathBuf,
    }
    impl Fixture {
        pub fn new() -> Result<Self> {
            for _ in 0..16 {
                let root = std::env::temp_dir().join(format!(
                    "rustusb-physical-fixture-{}-{}",
                    std::process::id(),
                    NEXT.fetch_add(1, Ordering::Relaxed)
                ));
                match fs::create_dir(&root) {
                    Ok(()) => return Ok(Self { root }),
                    Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                    Err(error) => return Err(error.into()),
                }
            }
            Err(Error::State(
                "could not allocate isolated physical fixture".into(),
            ))
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }
}

pub(super) fn interface_numbers(usb: &nusb::Device, config: u8) -> Result<Vec<u8>> {
    if config == 0 {
        return Ok(Vec::new());
    }
    let descriptor = usb
        .configurations()
        .find(|descriptor| descriptor.configuration_value() == config)
        .ok_or_else(|| Error::State("physical configuration descriptor missing".into()))?;
    Ok(descriptor
        .interfaces()
        .map(|interface| interface.interface_number())
        .collect())
}

fn validate_descriptors(usb: &nusb::Device) -> Result<()> {
    let mut total = 0usize;
    let mut configurations = BTreeSet::new();
    for config in usb.configurations() {
        total += config.as_bytes().len();
        if total > 256 * 1024
            || config.num_interfaces() > 32
            || config.configuration_value() == 0
            || !configurations.insert(config.configuration_value())
        {
            return Err(Error::UnsafeDevice(
                "physical configuration descriptor budget or identity invalid".into(),
            ));
        }
        let mut settings = BTreeSet::new();
        for interface in config.interface_alt_settings() {
            if matches!(interface.class(), 8 | 9)
                || !settings.insert((interface.interface_number(), interface.alternate_setting()))
            {
                return Err(Error::UnsafeDevice(
                    "storage, hub, or duplicate physical interface refused".into(),
                ));
            }
            let mut endpoints = BTreeSet::new();
            for endpoint in interface.endpoints() {
                if endpoint.address() & 0x0f == 0
                    || endpoint.address() & 0x70 != 0
                    || endpoint.max_packet_size() == 0
                    || !endpoints.insert(endpoint.address())
                    || endpoint.transfer_type() == TransferType::Control
                {
                    return Err(Error::UnsafeDevice(
                        "invalid physical endpoint descriptor".into(),
                    ));
                }
            }
            if endpoints.len() != usize::from(interface.num_endpoints()) {
                return Err(Error::UnsafeDevice(
                    "incomplete physical endpoint metadata".into(),
                ));
            }
        }
        if config.interfaces().count() != usize::from(config.num_interfaces()) {
            return Err(Error::UnsafeDevice(
                "incomplete physical interface metadata".into(),
            ));
        }
    }
    if configurations.len() != usize::from(usb.device_descriptor().num_configurations()) {
        return Err(Error::UnsafeDevice(
            "incomplete physical configuration descriptors".into(),
        ));
    }
    Ok(())
}

pub(super) async fn initialize(source: PhysicalSource) -> Result<Slot> {
    let PhysicalSource {
        fd,
        device,
        receipt,
    } = source;
    if receipt.bus_number == 0
        || !(1..=127).contains(&receipt.usb_device_number)
        || receipt.usb_device_number != device.device_address
        || !device.export_blockers.is_empty()
        || device.exported
        || device.occupied == Some(true)
        || matches!(device.class, 8 | 9)
        || device.interfaces.iter().any(|interface| {
            matches!(interface.class, 8 | 9)
                || interface.driver.as_deref().is_some_and(|driver| {
                    driver == "usbfs"
                        || driver == "usbip-host"
                        || driver == "usb-storage"
                        || driver == "uas"
                        || driver.starts_with("ums-")
                })
        })
    {
        return Err(Error::UnsafeDevice(
            "physical grant metadata is unsafe or inconsistent".into(),
        ));
    }
    let node = File::from(fd.try_clone()?).metadata()?;
    if !node.file_type().is_char_device()
        || node.rdev() != receipt.node_device
        || node.ino() != receipt.node_inode
        || rustix::fs::major(node.rdev()) != 189
        || rustix::fs::minor(node.rdev())
            != (u32::from(receipt.bus_number) - 1) * 128 + receipt.usb_device_number - 1
    {
        return Err(Error::UnsafeDevice(
            "physical FD is not the granted usbfs character node".into(),
        ));
    }
    let paths = Paths {
        root: PathBuf::from("/sys/bus/usb/devices"),
        bus_id: device.bus_id.to_string(),
    };
    let original: Vec<_> = device
        .interfaces
        .iter()
        .map(|interface| PhysicalDriver {
            interface: interface.number,
            driver: interface.driver.clone(),
        })
        .collect();
    let numbers: Vec<_> = original.iter().map(|driver| driver.interface).collect();
    let (observed_config, observed_drivers) = {
        let paths = paths.clone();
        let receipt = receipt.clone();
        tokio::task::spawn_blocking(move || {
            if !generation(&paths, &receipt)? {
                return Err(Error::State(
                    "physical capability generation no longer exists".into(),
                ));
            }
            let config = configuration(&paths)?;
            let drivers = drivers(&paths, config, &numbers)?;
            Ok((config, drivers))
        })
        .await
        .map_err(|_| Error::State("physical source observer failed".into()))??
    };
    if observed_drivers != original {
        return Err(Error::State(
            "physical drivers changed after authorization".into(),
        ));
    }
    let usb = nusb::Device::from_fd(fd)
        .await
        .map_err(|_| Error::State("could not initialize the granted usbfs descriptor".into()))?;
    let descriptor = usb.device_descriptor();
    if descriptor.vendor_id() != device.vendor_id
        || descriptor.product_id() != device.product_id
        || descriptor.class() != device.class
        || descriptor.subclass() != device.subclass
        || descriptor.protocol() != device.protocol
    {
        return Err(Error::UnsafeDevice(
            "physical descriptor differs from authorized metadata".into(),
        ));
    }
    validate_descriptors(&usb)?;
    if format!(
        "{:x}.{:02x}",
        descriptor.usb_version() >> 8,
        descriptor.usb_version() & 0xff
    ) != device.usb_version
    {
        return Err(Error::UnsafeDevice(
            "physical USB specification differs from authorization".into(),
        ));
    }
    if interface_numbers(&usb, observed_config)?
        != original
            .iter()
            .map(|driver| driver.interface)
            .collect::<Vec<_>>()
    {
        return Err(Error::UnsafeDevice(
            "physical interfaces differ from authorization".into(),
        ));
    }
    if observed_config != 0 {
        let config = usb
            .configurations()
            .find(|config| config.configuration_value() == observed_config)
            .ok_or_else(|| {
                Error::UnsafeDevice("authorized physical configuration is absent".into())
            })?;
        for original in &device.interfaces {
            let actual = config
                .interface_alt_settings()
                .find(|interface| {
                    interface.interface_number() == original.number
                        && interface.alternate_setting() == original.alternate_setting
                })
                .ok_or_else(|| {
                    Error::UnsafeDevice("authorized physical alternate setting is absent".into())
                })?;
            if (actual.class(), actual.subclass(), actual.protocol())
                != (original.class, original.subclass, original.protocol)
                || actual.endpoints().count() != original.endpoints.len()
                || actual.endpoints().any(|endpoint| {
                    !original.endpoints.iter().any(|original| {
                        endpoint.address() == original.address
                            && endpoint.attributes() == original.attributes
                            && endpoint.max_packet_size_raw() == original.max_packet_size
                            && endpoint.interval() == original.interval
                    })
                })
            {
                return Err(Error::UnsafeDevice(
                    "physical interface or endpoint differs from authorization".into(),
                ));
            }
        }
    }
    let speed = match usb.speed() {
        Some(nusb::Speed::Low) => 1,
        Some(nusb::Speed::Full) => 2,
        Some(nusb::Speed::High) => 3,
        Some(nusb::Speed::Super) => 5,
        Some(nusb::Speed::SuperPlus) => 6,
        _ => {
            return Err(Error::Unsupported(
                "unknown physical USB connection speed".into(),
            ));
        }
    };
    let expected_speed = match speed {
        1 => "1.5",
        2 => "12",
        3 => "480",
        5 => "5000",
        6 => "10000",
        _ => "",
    };
    if device.speed != expected_speed {
        return Err(Error::UnsafeDevice(
            "physical speed differs from the authorization snapshot".into(),
        ));
    }
    let ep0 = if speed >= 5 {
        1u16.checked_shl(u32::from(descriptor.max_packet_size_0()))
            .filter(|value| *value == 512)
    } else {
        Some(u16::from(descriptor.max_packet_size_0()))
            .filter(|value| matches!(value, 8 | 16 | 32 | 64))
    }
    .ok_or_else(|| Error::UnsafeDevice("physical endpoint zero descriptor is invalid".into()))?;
    if observed_config != 0
        && usb
            .active_configuration()
            .map_err(|_| Error::State("usbfs active configuration is unknown".into()))?
            .configuration_value()
            != observed_config
    {
        return Err(Error::State(
            "usbfs and sysfs disagree on the active physical configuration".into(),
        ));
    }
    let metadata = RemoteDevice {
        bus_id: device.bus_id.clone(),
        path: format!("/sys/bus/usb/devices/{}", device.bus_id),
        bus_number: u32::from(receipt.bus_number),
        device_number: receipt.usb_device_number,
        speed,
        vendor_id: descriptor.vendor_id(),
        product_id: descriptor.product_id(),
        device_version: descriptor.device_version(),
        class: descriptor.class(),
        subclass: descriptor.subclass(),
        protocol: descriptor.protocol(),
        configuration: observed_config,
        num_configurations: descriptor.num_configurations(),
        interfaces: device
            .interfaces
            .iter()
            .map(|interface| RemoteInterface {
                class: interface.class,
                subclass: interface.subclass,
                protocol: interface.protocol,
            })
            .collect(),
    };
    let snapshot = PhysicalDeviceSnapshot {
        bus_id: device.bus_id.to_string(),
        device_id: device.id.clone(),
        state: "available".into(),
        leased: false,
        pending_urbs: 0,
        queued_urbs: 0,
        usbfs_inflight: 0,
        usbfs_reaped: 0,
        cancelled: 0,
        configuration: observed_config,
        ep0_max_packet_size: ep0,
        speed,
        original_drivers: original,
        current_drivers: observed_drivers,
        cleanup_verified: true,
        counters: PhysicalCounters::default(),
    };
    let slot = Slot {
        usb,
        metadata,
        source: device,
        receipt,
        paths,
        original_configuration: observed_config,
        state: Mutex::new(snapshot),
        changed: Notify::new(),
    };
    let slot = Arc::new(slot);
    if !verify(&slot).await? {
        return Err(Error::State(
            "physical generation changed during initialization".into(),
        ));
    }
    Arc::try_unwrap(slot)
        .map_err(|_| Error::State("physical initialization ownership was not exclusive".into()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn restoration_fixture() -> Result<(test_fixture::Fixture, Paths, PhysicalDeviceReceipt)> {
        let fixture = test_fixture::Fixture::new()?;
        let paths = Paths {
            root: fixture.root.clone(),
            bus_id: "1-1".into(),
        };
        fs::create_dir(paths.device())?;
        fs::write(paths.device().join("busnum"), "1\n")?;
        fs::write(paths.device().join("devnum"), "2\n")?;
        fs::write(paths.device().join("bConfigurationValue"), "1\n")?;
        for number in [0, 1] {
            fs::create_dir(paths.interface(1, number))?;
            fs::write(paths.interface(1, number).join("bAlternateSetting"), "0\n")?;
        }
        let metadata = fs::metadata(paths.device())?;
        let receipt = PhysicalDeviceReceipt {
            sysfs_device: metadata.dev(),
            sysfs_inode: metadata.ino(),
            node_device: 0,
            node_inode: 0,
            usb_device_number: 2,
            bus_number: 1,
        };
        Ok((fixture, paths, receipt))
    }

    fn cdc_drivers() -> [PhysicalDriver; 2] {
        [0, 1].map(|interface| PhysicalDriver {
            interface,
            driver: Some("cdc_ether".into()),
        })
    }

    fn bind_driver(paths: &Paths, number: u8, name: &str) -> Result<()> {
        std::os::unix::fs::symlink(
            format!("/fixture-drivers/{name}"),
            paths.interface(1, number).join("driver"),
        )?;
        Ok(())
    }

    fn change_restoration_scope(paths: &Paths, configuration_change: bool) -> Result<()> {
        if configuration_change {
            fs::write(paths.device().join("bConfigurationValue"), "2\n")?;
        } else {
            fs::rename(paths.device(), paths.root.join("old-generation"))?;
            fs::create_dir(paths.device())?;
        }
        Ok(())
    }

    #[test]
    fn reconnect_observes_sibling_binding_without_a_second_connect() -> Result<()> {
        let (_fixture, paths, receipt) = restoration_fixture()?;
        let originals = cdc_drivers();
        let mut attempts = Vec::new();
        reconnect_drivers(
            &paths,
            &receipt,
            1,
            &originals,
            &BTreeSet::from([0, 1]),
            |number| {
                attempts.push(number);
                bind_driver(&paths, 0, "cdc_ether")?;
                bind_driver(&paths, 1, "cdc_ether")
            },
        )?;
        assert_eq!(attempts, [0]);
        assert_eq!(drivers(&paths, 1, &[0, 1])?, originals);
        Ok(())
    }

    #[test]
    fn reconnect_errors_require_actual_original_binding_to_converge() -> Result<()> {
        for observed in [None, Some("other_driver"), Some("cdc_ether")] {
            let (_fixture, paths, receipt) = restoration_fixture()?;
            let originals = cdc_drivers();
            let mut attempts = Vec::new();
            let result = reconnect_drivers(
                &paths,
                &receipt,
                1,
                &originals[..1],
                &BTreeSet::from([0]),
                |number| {
                    attempts.push(number);
                    if let Some(name) = observed {
                        bind_driver(&paths, number, name)?;
                    }
                    Err(Error::Io(std::io::Error::from_raw_os_error(16)))
                },
            );
            assert_eq!(result.is_ok(), observed == Some("cdc_ether"), "{result:?}");
            assert_eq!(attempts, [0]);
        }
        Ok(())
    }

    #[test]
    fn reconnect_rejects_changed_scope_before_and_after_connect() -> Result<()> {
        for before_connect in [false, true] {
            for configuration_change in [false, true] {
                let (_fixture, paths, receipt) = restoration_fixture()?;
                let originals = cdc_drivers();
                if before_connect {
                    change_restoration_scope(&paths, configuration_change)?;
                }
                let mut attempts = Vec::new();
                let result = reconnect_drivers(
                    &paths,
                    &receipt,
                    1,
                    &originals[..1],
                    &BTreeSet::from([0]),
                    |number| {
                        attempts.push(number);
                        bind_driver(&paths, number, "cdc_ether")?;
                        change_restoration_scope(&paths, configuration_change)?;
                        Err(Error::Io(std::io::Error::from_raw_os_error(16)))
                    },
                );
                assert!(result.is_err(), "{result:?}");
                assert_eq!(attempts.len(), usize::from(!before_connect));
            }
        }
        Ok(())
    }

    #[test]
    fn reconnect_never_changes_foreign_or_unowned_bindings() -> Result<()> {
        let (_fixture, paths, receipt) = restoration_fixture()?;
        let originals = cdc_drivers();
        bind_driver(&paths, 1, "other_driver")?;
        let mut attempts = Vec::new();
        reconnect_drivers(
            &paths,
            &receipt,
            1,
            &originals,
            &BTreeSet::from([0]),
            |number| {
                attempts.push(number);
                bind_driver(&paths, number, "cdc_ether")
            },
        )?;
        assert_eq!(attempts, [0]);
        assert_eq!(
            drivers(&paths, 1, &[1])?[0].driver.as_deref(),
            Some("other_driver")
        );
        attempts.clear();
        assert!(
            reconnect_drivers(
                &paths,
                &receipt,
                1,
                &originals,
                &BTreeSet::from([1]),
                |number| {
                    attempts.push(number);
                    Ok(())
                },
            )
            .is_err()
        );
        assert!(attempts.is_empty());

        let originally_unbound = [PhysicalDriver {
            interface: 0,
            driver: None,
        }];
        assert!(
            reconnect_drivers(
                &paths,
                &receipt,
                1,
                &originally_unbound,
                &BTreeSet::from([0]),
                |number| {
                    attempts.push(number);
                    Ok(())
                },
            )
            .is_err()
        );
        assert!(attempts.is_empty());
        Ok(())
    }

    #[test]
    fn alternate_already_restored_by_probe_does_not_need_reclaiming() -> Result<()> {
        let (_fixture, paths, receipt) = restoration_fixture()?;
        bind_driver(&paths, 0, "cdc_ether")?;
        bind_driver(&paths, 1, "cdc_ether")?;
        fs::write(paths.interface(1, 1).join("bAlternateSetting"), "1\n")?;
        assert!(alternate_matches(&paths, &receipt, 1, 1, 1)?);
        assert!(!alternate_matches(&paths, &receipt, 1, 1, 0)?);
        fs::write(paths.device().join("bConfigurationValue"), "2\n")?;
        assert!(alternate_matches(&paths, &receipt, 1, 1, 1).is_err());
        fs::write(paths.device().join("bConfigurationValue"), "1\n")?;
        change_restoration_scope(&paths, false)?;
        assert!(alternate_matches(&paths, &receipt, 1, 1, 1).is_err());
        Ok(())
    }

    #[test]
    fn generation_distinguishes_missing_replaced_and_changed_address() -> Result<()> {
        let fixture = test_fixture::Fixture::new()?;
        let paths = Paths {
            root: fixture.root.clone(),
            bus_id: "1-1".into(),
        };
        fs::create_dir(paths.device())?;
        fs::write(paths.device().join("busnum"), "1\n")?;
        fs::write(paths.device().join("devnum"), "2\n")?;
        let metadata = fs::metadata(paths.device())?;
        let receipt = PhysicalDeviceReceipt {
            sysfs_device: metadata.dev(),
            sysfs_inode: metadata.ino(),
            node_device: 0,
            node_inode: 0,
            usb_device_number: 2,
            bus_number: 1,
        };
        assert!(generation(&paths, &receipt)?);
        fs::write(paths.device().join("devnum"), "3\n")?;
        assert!(!generation(&paths, &receipt)?);
        fs::write(paths.device().join("devnum"), "2\n")?;
        fs::rename(paths.device(), fixture.root.join("old-generation"))?;
        fs::create_dir(paths.device())?;
        assert!(!generation(&paths, &receipt)?);
        fs::remove_dir(paths.device())?;
        assert!(!generation(&paths, &receipt)?);
        Ok(())
    }

    #[test]
    fn alternate_is_decimal_and_driver_observations_are_bounded() -> Result<()> {
        let fixture = test_fixture::Fixture::new()?;
        let paths = Paths {
            root: fixture.root.clone(),
            bus_id: "1-1".into(),
        };
        fs::create_dir(paths.interface(1, 0))?;
        fs::write(paths.interface(1, 0).join("bAlternateSetting"), "10\n")?;
        assert_eq!(alternate(&paths, 1, 0)?, 10);
        assert_eq!(
            drivers(&paths, 1, &[0])?,
            [PhysicalDriver {
                interface: 0,
                driver: None
            }]
        );
        std::os::unix::fs::symlink(
            "/fixture-drivers/usbhid",
            paths.interface(1, 0).join("driver"),
        )?;
        assert_eq!(
            drivers(&paths, 1, &[0])?[0].driver.as_deref(),
            Some("usbhid")
        );
        fs::write(
            paths.interface(1, 0).join("bAlternateSetting"),
            vec![b'1'; 129],
        )?;
        assert!(alternate(&paths, 1, 0).is_err());
        assert!(drivers(&paths, 1, &[255]).is_err());
        Ok(())
    }
}

//! Read-only OS owner and complete USB baseline observations for the journal.

use crate::journal::{Baseline, OriginalInterface, Owner, Record};
use rustusb_core::{Device, DeviceDiscovery, Error, PhysicalDeviceReceipt, Result};
use rustusb_linux::SysfsDiscovery;
use std::{
    fs::{self, File},
    io::Read,
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::Path,
};

fn bounded(path: &Path, limit: usize) -> Result<String> {
    let mut bytes = Vec::new();
    File::open(path)?
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)?;
    if bytes.len() > limit {
        return Err(Error::State(
            "physical lifecycle observation exceeds bounds".into(),
        ));
    }
    String::from_utf8(bytes)
        .map_err(|_| Error::State("invalid lifecycle observation encoding".into()))
}

pub(crate) fn uuid(path: &Path) -> Result<String> {
    let value = bounded(path, 64)?.trim().to_owned();
    crate::journal::validate_lease_id(&value)?;
    Ok(value)
}

pub(crate) fn boot_id() -> Result<String> {
    uuid(Path::new("/proc/sys/kernel/random/boot_id"))
}

fn start_time(stat: &str, pid: u32) -> Result<u64> {
    let prefix = format!("{pid} (");
    let end = stat.rfind(") ").filter(|end| *end >= prefix.len());
    let value = if stat.starts_with(&prefix) {
        end.and_then(|end| stat[end + 2..].split_ascii_whitespace().nth(19))
            .and_then(|value| value.parse().ok())
    } else {
        None
    };
    value.ok_or_else(|| Error::State("cannot establish physical grant owner start time".into()))
}

pub(crate) fn owner(uid: u32, pid: Option<i32>) -> Result<Owner> {
    let pid = pid
        .and_then(|pid| u32::try_from(pid).ok())
        .filter(|pid| *pid > 0)
        .ok_or_else(|| Error::Permission("physical grant requires a kernel peer PID".into()))?;
    let stat = bounded(&Path::new("/proc").join(pid.to_string()).join("stat"), 4096)?;
    Ok(Owner {
        uid,
        pid,
        start_time: start_time(&stat, pid)?,
        boot_id: boot_id()?,
    })
}

pub(crate) fn require_owner(record: &Record, caller: &Owner) -> Result<()> {
    if record.owner != *caller {
        return Err(Error::Permission("only the original OS process may finish a physical grant; root reconciliation is separate".into()));
    }
    Ok(())
}

pub(crate) fn require_ended_owner(record: &Record) -> Result<()> {
    if boot_id()? != record.owner.boot_id {
        return Ok(());
    }
    let stat = match bounded(
        &Path::new("/proc")
            .join(record.owner.pid.to_string())
            .join("stat"),
        4096,
    ) {
        Ok(stat) => stat,
        Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    if start_time(&stat, record.owner.pid)? != record.owner.start_time {
        return Ok(());
    }
    Err(Error::State("original physical grant owner still exists; stop and reap it before administrator reconciliation".into()))
}

/// Observe an ended original process without granting old-boot recovery rights.
/// A still-present matching tuple, including a zombie, remains live here.
pub(crate) fn owner_ended_same_boot(record: &Record) -> Result<bool> {
    owner_ended_at(
        record,
        Path::new("/proc/sys/kernel/random/boot_id"),
        Path::new("/proc"),
    )
}

fn owner_ended_at(record: &Record, boot: &Path, processes: &Path) -> Result<bool> {
    if uuid(boot)? != record.owner.boot_id {
        return Err(Error::State(
            "automatic physical recovery requires the original boot; record retained".into(),
        ));
    }
    let stat = match bounded(
        &processes.join(record.owner.pid.to_string()).join("stat"),
        4096,
    ) {
        Ok(stat) => stat,
        Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => return Ok(true),
        Err(error) => return Err(error),
    };
    Ok(start_time(&stat, record.owner.pid)? != record.owner.start_time)
}

fn generation_at(root: &Path, baseline: &Baseline) -> Result<bool> {
    let path = root.join(baseline.bus_id.as_str());
    let metadata = match fs::metadata(&path) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(false),
        Err(error) => return Err(error.into()),
    };
    if !metadata.is_dir()
        || metadata.dev() != baseline.receipt.sysfs_device
        || metadata.ino() != baseline.receipt.sysfs_inode
    {
        return Ok(false);
    }
    let bus = bounded(&path.join("busnum"), 32)?
        .trim()
        .parse::<u16>()
        .map_err(|_| Error::State("invalid physical bus number".into()))?;
    let address = bounded(&path.join("devnum"), 32)?
        .trim()
        .parse::<u32>()
        .map_err(|_| Error::State("invalid physical device address".into()))?;
    Ok(bus == baseline.receipt.bus_number && address == baseline.receipt.usb_device_number)
}

fn configuration(root: &Path, baseline: &Baseline) -> Result<u8> {
    let text = bounded(
        &root
            .join(baseline.bus_id.as_str())
            .join("bConfigurationValue"),
        32,
    )?;
    if text.trim().is_empty() {
        return Ok(0);
    }
    text.trim()
        .parse()
        .map_err(|_| Error::State("invalid physical configuration value".into()))
}

fn interfaces(device: &Device) -> Vec<OriginalInterface> {
    let mut result: Vec<_> = device
        .interfaces
        .iter()
        .map(|interface| OriginalInterface {
            number: interface.number,
            alternate: interface.alternate_setting,
            driver: interface.driver.clone(),
        })
        .collect();
    result.sort_by_key(|interface| interface.number);
    result
}

fn same_baseline(expected: &Baseline, observed: &Baseline) -> Result<()> {
    if expected != observed {
        return Err(Error::State("physical original configuration, full interface/alternate/driver set, or generation is not restored; unresolved grant retained".into()));
    }
    Ok(())
}

/// Independently observe complete current state within the original generation.
pub(crate) fn observe_current(baseline: &Baseline) -> Result<Baseline> {
    observe_current_device(baseline).map(|(observed, _)| observed)
}

/// Return the checked current scanner device for a subsequent fixed-path open.
/// Callers must compare the new descriptor receipt and reobserve after opening.
pub(crate) fn observe_current_device(baseline: &Baseline) -> Result<(Baseline, Device)> {
    let root = Path::new("/sys/bus/usb/devices");
    if !generation_at(root, baseline)? {
        return Err(Error::State(
            "physical generation changed during observation".into(),
        ));
    }
    let config = configuration(root, baseline)?;
    let devices = SysfsDiscovery::default().scan()?;
    let mut matches = devices
        .iter()
        .filter(|device| device.id == baseline.device_id);
    let device = matches
        .next()
        .ok_or_else(|| Error::State("physical device absent or incompletely observable".into()))?;
    if matches.next().is_some()
        || device.bus_id != baseline.bus_id
        || device.device_address != baseline.receipt.usb_device_number
    {
        return Err(Error::State(
            "physical fingerprint or current address is ambiguous".into(),
        ));
    }
    crate::physical::ensure_eligible(device)?;
    let node = fs::symlink_metadata(format!(
        "/dev/bus/usb/{:03}/{:03}",
        baseline.receipt.bus_number, baseline.receipt.usb_device_number
    ))?;
    if !node.file_type().is_char_device()
        || node.uid() != 0
        || node.rdev() != baseline.receipt.node_device
        || node.ino() != baseline.receipt.node_inode
    {
        return Err(Error::State(
            "physical capability node changed during observation".into(),
        ));
    }
    let observed = Baseline {
        device_id: device.id.clone(),
        bus_id: device.bus_id.clone(),
        receipt: baseline.receipt.clone(),
        configuration: config,
        interfaces: interfaces(device),
    };
    if !generation_at(root, baseline)? || configuration(root, baseline)? != config {
        return Err(Error::State(
            "physical generation or configuration changed during observation".into(),
        ));
    }
    Ok((observed, device.clone()))
}

pub(crate) fn capture(device: &Device, receipt: &PhysicalDeviceReceipt) -> Result<Baseline> {
    let mut expected = Baseline {
        device_id: device.id.clone(),
        bus_id: device.bus_id.clone(),
        receipt: receipt.clone(),
        configuration: 0,
        interfaces: interfaces(device),
    };
    expected.configuration = configuration(Path::new("/sys/bus/usb/devices"), &expected)?;
    let observed = observe_current(&expected)?;
    same_baseline(&expected, &observed)?;
    // Reobserve the full set, not only one interface or the configuration value.
    same_baseline(&observed, &observe_current(&observed)?)?;
    Ok(observed)
}

/// A point-in-time observation, never a claim that delegated FDs were revoked.
pub(crate) fn verify(record: &Record, acknowledge_gone: bool) -> Result<&'static str> {
    let gone = boot_id()? != record.owner.boot_id
        || !generation_at(Path::new("/sys/bus/usb/devices"), &record.baseline)?;
    if gone {
        return if acknowledge_gone {
            Ok("gone")
        } else {
            Err(Error::State("original physical generation is gone; root must explicitly acknowledge gone without applying its baseline to a replacement".into()))
        };
    }
    same_baseline(&record.baseline, &observe_current(&record.baseline)?)?;
    same_baseline(&record.baseline, &observe_current(&record.baseline)?)?;
    Ok("restored")
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::BusId;
    fn baseline() -> Result<Baseline> {
        Ok(Baseline {
            device_id: format!("usb-{}", "a".repeat(64)),
            bus_id: BusId::new("1-1")?,
            receipt: PhysicalDeviceReceipt {
                sysfs_device: 1,
                sysfs_inode: 2,
                node_device: 3,
                node_inode: 4,
                usb_device_number: 2,
                bus_number: 1,
            },
            configuration: 1,
            interfaces: vec![
                OriginalInterface {
                    number: 0,
                    alternate: 0,
                    driver: Some("cdc_ether".into()),
                },
                OriginalInterface {
                    number: 1,
                    alternate: 1,
                    driver: Some("cdc_ether".into()),
                },
            ],
        })
    }
    #[test]
    fn original_complete_baseline_rejects_each_independent_difference() -> Result<()> {
        let original = baseline()?;
        same_baseline(&original, &original)?;
        let mut changed = original.clone();
        changed.configuration = 2;
        assert!(same_baseline(&original, &changed).is_err());
        changed = original.clone();
        changed.interfaces.pop();
        assert!(same_baseline(&original, &changed).is_err());
        changed = original.clone();
        changed.interfaces[1].alternate = 0;
        assert!(same_baseline(&original, &changed).is_err());
        changed = original.clone();
        changed.interfaces[0].driver = None;
        assert!(same_baseline(&original, &changed).is_err());
        changed = original.clone();
        changed.receipt.sysfs_inode += 1;
        assert!(same_baseline(&original, &changed).is_err());
        // A legitimately unbound original is retained, not invented as a driver.
        changed = original;
        changed.interfaces[0].driver = None;
        same_baseline(&changed, &changed)?;
        Ok(())
    }
    #[test]
    fn peer_identity_requires_uid_pid_start_and_boot_not_a_bearer_lease() -> Result<()> {
        let caller = owner(
            rustix::process::getuid().as_raw(),
            Some(std::process::id() as i32),
        )?;
        let record = Record {
            lease_id: "00000000-0000-0000-0000-000000000001".into(),
            owner: caller.clone(),
            baseline: baseline()?,
            recovery: crate::journal::RecoveryAuthorization::ManualOnly,
        };
        require_owner(&record, &caller)?;
        assert!(require_ended_owner(&record).is_err());
        for index in 0..4 {
            let mut other = caller.clone();
            match index {
                0 => other.uid += 1,
                1 => other.pid += 1,
                2 => other.start_time += 1,
                _ => other.boot_id = "00000000-0000-0000-0000-000000000002".into(),
            }
            assert!(require_owner(&record, &other).is_err());
        }
        Ok(())
    }
    #[test]
    fn stat_command_with_spaces_and_closing_parentheses_keeps_field_offsets() -> Result<()> {
        let suffix = (3..=21).map(|_| "0").collect::<Vec<_>>().join(" ");
        assert_eq!(
            start_time(&format!("42 (odd ) name) {suffix} 123 0"), 42)?,
            123
        );
        for stat in ["", "42 (x)", "43 (x) R 1", "42 (x) R 1"] {
            assert!(start_time(stat, 42).is_err());
        }
        Ok(())
    }
    #[test]
    fn same_path_replacement_is_gone_not_the_old_generation() -> Result<()> {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("1-1");
        fs::create_dir(&path)?;
        fs::write(path.join("busnum"), "1\n")?;
        fs::write(path.join("devnum"), "2\n")?;
        let mut old = baseline()?;
        let metadata = fs::metadata(&path)?;
        old.receipt.sysfs_device = metadata.dev();
        old.receipt.sysfs_inode = metadata.ino();
        assert!(generation_at(directory.path(), &old)?);
        fs::rename(&path, directory.path().join("old"))?;
        fs::create_dir(&path)?;
        assert!(!generation_at(directory.path(), &old)?);
        Ok(())
    }

    #[test]
    fn automatic_owner_observation_rejects_old_boot_and_unknown_process_state() -> Result<()> {
        let root = tempfile::tempdir()?;
        let boot = root.path().join("boot");
        let processes = root.path().join("proc");
        let process = processes.join("42");
        fs::create_dir_all(&process)?;
        let mut record = Record {
            lease_id: "00000000-0000-0000-0000-000000000001".into(),
            owner: Owner {
                uid: 1000,
                pid: 42,
                start_time: 123,
                boot_id: "11111111-2222-3333-4444-555555555555".into(),
            },
            baseline: baseline()?,
            recovery: crate::journal::RecoveryAuthorization::ConnectOriginalDrivers,
        };
        fs::write(&boot, &record.owner.boot_id)?;
        let prefix = (4..=21).map(|_| "0").collect::<Vec<_>>().join(" ");
        for state in ["R", "Z"] {
            fs::write(
                process.join("stat"),
                format!("42 (odd ) name) {state} {prefix} 123 0"),
            )?;
            assert!(!owner_ended_at(&record, &boot, &processes)?);
        }
        record.owner.start_time = 122;
        assert!(owner_ended_at(&record, &boot, &processes)?);
        fs::write(process.join("stat"), "42 (broken) R 1")?;
        assert!(owner_ended_at(&record, &boot, &processes).is_err());
        fs::remove_file(process.join("stat"))?;
        assert!(owner_ended_at(&record, &boot, &processes)?);
        fs::write(&boot, "00000000-0000-0000-0000-000000000002")?;
        assert!(owner_ended_at(&record, &boot, &processes).is_err());
        Ok(())
    }
}

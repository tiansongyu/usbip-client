//! Read-only observation stamps. These are neither authorization nor cleanup receipts.

use crate::{SysfsDiscovery, attribute, names};
use rustusb_core::{BusId, Device, Error, Result};
use serde::{Deserialize, Serialize};
use std::{fs, io, os::unix::fs::MetadataExt, path::Path};

/// A current sysfs observation, separate from a candidate persistent fingerprint.
///
/// Inode/address equality does not authenticate hardware or prove uninterrupted
/// presence. The kernel can reuse identifiers; callers must revalidate mutations.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservationStamp {
    /// Filesystem containing the actual device directory.
    pub sysfs_device: u64,
    /// Inode of that directory, not an opened USB capability.
    pub sysfs_inode: u64,
    /// Current USB bus number.
    pub bus_number: u16,
    /// Current USB address.
    pub device_number: u8,
}

/// Device metadata accompanied by a stamp checked before and after its scan.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ObservedDevice {
    /// The existing discovery result, including its eligibility blockers.
    pub device: Device,
    /// Observational only; must never authorize device access.
    pub stamp: ObservationStamp,
}

fn stamp(path: &Path) -> Result<ObservationStamp> {
    let metadata = fs::metadata(path)?;
    let parse = |name: &str, max: u32| -> Result<u32> {
        let value = attribute(&path.join(name))?;
        value
            .parse::<u32>()
            .ok()
            .filter(|number| *number > 0 && *number <= max)
            .ok_or_else(|| Error::State("device address changed or is incomplete".into()))
    };
    Ok(ObservationStamp {
        sysfs_device: metadata.dev(),
        sysfs_inode: metadata.ino(),
        bus_number: parse("busnum", u16::MAX.into())? as u16,
        device_number: parse("devnum", 127)? as u8,
    })
}

impl SysfsDiscovery {
    /// Scan metadata with before/after inode and address checks.
    ///
    /// A disappearing entry is omitted. A mixed generation rejects the complete
    /// scan, allowing an observer to preserve its last successful snapshot.
    /// This remains a sequence of reads, not an atomic kernel transaction.
    pub fn observe(&self) -> Result<Vec<ObservedDevice>> {
        let entries = names(&self.root)?;
        let mut devices = Vec::new();
        for name in &entries {
            let Ok(bus_id) = BusId::new(name.clone()) else {
                continue;
            };
            let path = self.root.join(name);
            let before = match stamp(&path) {
                Ok(stamp) => stamp,
                Err(Error::Io(error))
                    if error.kind() == io::ErrorKind::NotFound
                        && matches!(fs::metadata(&path), Err(current) if current.kind() == io::ErrorKind::NotFound) =>
                {
                    continue;
                }
                Err(error) => return Err(error),
            };
            let Some(device) = self.device(bus_id, &entries)? else {
                continue;
            };
            let after = match stamp(&path) {
                Ok(stamp) => stamp,
                Err(Error::Io(error))
                    if error.kind() == io::ErrorKind::NotFound
                        && matches!(fs::metadata(&path), Err(current) if current.kind() == io::ErrorKind::NotFound) =>
                {
                    continue;
                }
                Err(error) => return Err(error),
            };
            if before != after
                || u32::from(after.device_number) != device.device_address
                || device
                    .export_blockers
                    .iter()
                    .any(|reason| reason == "device_changed_during_scan")
            {
                return Err(Error::State("device changed during observation".into()));
            }
            devices.push(ObservedDevice {
                device,
                stamp: after,
            });
        }
        Ok(devices)
    }
}

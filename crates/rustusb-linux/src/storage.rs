//! Bounded storage-use observations. A report is never an export permission.
//!
//! This module reads kernel metadata only. It does not open block/USB devices,
//! unmount filesystems, acquire storage ownership, or prove absence of users.

use crate::SysfsDiscovery;
use rustusb_core::{DeviceDiscovery, Error, Result};
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    fs::{self, File},
    io::Read,
    os::fd::AsRawFd,
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

mod process;

const MAX_BLOCKS: usize = 64;
const MAX_LUNS: usize = 32;
const MAX_USES: usize = 256;
const MAX_TASKS: usize = 8192;
const MAX_ENTRIES: usize = 65_536;
const MAX_TEXT: usize = 16 * 1024 * 1024;
const MAX_FILE: usize = 1024 * 1024;
const SYSFS: i64 = 0x62656572;
const PROCFS: i64 = 0x9fa0;

/// Linux device number, independent of a volatile device-node filename.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct DeviceNumber {
    /// Kernel major number.
    pub major: u32,
    /// Kernel minor number.
    pub minor: u32,
}
impl DeviceNumber {
    fn parse(value: &str) -> Option<Self> {
        let (major, minor) = value.trim().split_once(':')?;
        Some(Self {
            major: decimal(major)?,
            minor: decimal(minor)?,
        })
    }
    fn from_raw(value: u64) -> Self {
        Self {
            major: rustix::fs::major(value),
            minor: rustix::fs::minor(value),
        }
    }
}

/// Observed block graph node. Kernel RO is not a remote write-protection claim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BlockNode {
    /// Kernel-generated name; no filesystem label or serial.
    pub name: String,
    /// Current block device number.
    pub device: DeviceNumber,
    /// Current sysfs directory inode (only one part of generation identity).
    pub sysfs_inode: u64,
    /// Disk sequence when supplied by the kernel; partitions may omit it.
    pub disk_sequence: Option<u64>,
    /// Kernel partition number; absent for a whole disk or mapped volume.
    pub partition: Option<u32>,
    /// Size in kernel 512-byte sectors, when readable.
    pub sectors: Option<u64>,
    /// Kernel block-object flag; does not establish device firmware protection.
    pub kernel_read_only: Option<bool>,
    /// Hint derived from the DM UUID prefix only, never an encryption audit.
    pub dm_hint: Option<&'static str>,
    /// Mapped consumers of this node.
    pub holders: Vec<DeviceNumber>,
    /// Immediate backing nodes of this node.
    pub slaves: Vec<DeviceNumber>,
}

/// A bounded positive observation, not an inference about process intent.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub struct StorageUse {
    /// `holder`, `mount`, `block_fd`, `character_fd`, `filesystem_fd`, `mapping`, or `swap`.
    pub kind: &'static str,
    /// Device matched by this observation.
    pub device: DeviceNumber,
    /// Visible process ID, if applicable.
    pub pid: Option<u32>,
    /// Visible thread ID, if applicable.
    pub tid: Option<u32>,
    /// Task start ticks, as reported in the observer's time namespace.
    pub start_ticks: Option<u64>,
}

/// Point-in-time metadata; even an error-free empty report cannot approve export.
#[derive(Debug, Serialize)]
pub struct StorageInspection {
    /// Independent report schema.
    pub schema_version: u32,
    /// Requested candidate fingerprint.
    pub device_id: String,
    /// Current physical USB address.
    pub bus_id: String,
    /// Pinned sysfs inode and filesystem device number.
    pub usb_generation: (u64, u64),
    /// Current USB device address, checked again at completion.
    pub usb_address: u32,
    /// Observed SCSI addresses (host:channel:target:lun).
    pub luns: Vec<String>,
    /// Observed block descendants, partitions and mapped consumers.
    pub blocks: Vec<BlockNode>,
    /// Known usage retained even when a later observation fails.
    pub uses: BTreeSet<StorageUse>,
    /// True only when a positive use was observed; false never means safe.
    pub busy_observed: bool,
    /// Finite coverage failure codes, without sensitive paths or error text.
    pub issues: BTreeSet<&'static str>,
    /// Inherent gaps that apply to every scan, independently of issues.
    pub limitations: Vec<&'static str>,
    /// Number of task directories attempted, including incomplete tasks.
    pub tasks_observed: usize,
    /// Elapsed scan time. The cooperative budget cannot interrupt a blocked syscall.
    pub elapsed_ms: u64,
}

struct Budget {
    start: Instant,
    entries: usize,
    text: usize,
    issues: BTreeSet<&'static str>,
}
impl Budget {
    fn new() -> Self {
        Self {
            start: Instant::now(),
            entries: 0,
            text: 0,
            issues: BTreeSet::new(),
        }
    }
    fn issue(&mut self, code: &'static str) {
        self.issues.insert(code);
    }
    fn available(&mut self) -> bool {
        if self.start.elapsed() >= Duration::from_secs(10) {
            self.issue("time_budget");
            return false;
        }
        if self.entries >= MAX_ENTRIES {
            self.issue("entry_budget");
            return false;
        }
        if self.text >= MAX_TEXT {
            self.issue("text_budget");
            return false;
        }
        true
    }
    fn entries(&mut self, path: &Path) -> Vec<PathBuf> {
        let mut result = Vec::new();
        if !self.available() {
            return result;
        }
        let Ok(entries) = fs::read_dir(path) else {
            self.issue("directory_unreadable_or_gone");
            return result;
        };
        for entry in entries {
            if !self.available() {
                break;
            }
            self.entries += 1;
            match entry {
                Ok(entry) => result.push(entry.path()),
                Err(_) => self.issue("directory_changed_or_unreadable"),
            }
        }
        result.sort();
        result
    }
    fn text(&mut self, path: &Path, limit: usize) -> Option<String> {
        if !self.available() {
            return None;
        }
        let max = limit.min(MAX_TEXT.saturating_sub(self.text));
        let mut data = Vec::new();
        let result = File::open(path).and_then(|f| f.take(max as u64 + 1).read_to_end(&mut data));
        self.text = self.text.saturating_add(data.len());
        if result.is_err() {
            self.issue("metadata_unreadable_or_gone");
            return None;
        }
        if data.len() > max {
            self.issue("text_file_or_total_limit");
            return None;
        }
        match String::from_utf8(data) {
            Ok(value) => Some(value),
            Err(_) => {
                self.issue("invalid_metadata_encoding");
                None
            }
        }
    }
    fn number<T: std::str::FromStr>(&mut self, path: &Path) -> Option<T> {
        let value = self.text(path, 128)?;
        let parsed = decimal(value.trim());
        if parsed.is_none() {
            self.issue("invalid_numeric_metadata");
        }
        parsed
    }
    fn optional_number<T: std::str::FromStr>(&mut self, path: &Path) -> Option<T> {
        match fs::symlink_metadata(path) {
            Ok(_) => self.number(path),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(_) => {
                self.issue("metadata_unreadable_or_gone");
                None
            }
        }
    }
}

fn decimal<T: std::str::FromStr>(value: &str) -> Option<T> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    value.parse().ok()
}
fn safe_name(path: &Path) -> Option<String> {
    let name = path.file_name()?.to_str()?;
    (name.len() <= 64
        && !name.is_empty()
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_:!.".contains(&b)))
    .then(|| name.to_owned())
}
fn identity(path: &Path) -> Option<(u64, u64)> {
    fs::metadata(path).ok().map(|m| (m.dev(), m.ino()))
}
fn add_use(uses: &mut BTreeSet<StorageUse>, usage: StorageUse, budget: &mut Budget) {
    if uses.contains(&usage) {
        return;
    }
    if uses.len() >= MAX_USES {
        budget.issue("usage_limit");
    } else {
        uses.insert(usage);
    }
}
fn standalone_use(kind: &'static str, device: DeviceNumber) -> StorageUse {
    StorageUse {
        kind,
        device,
        pid: None,
        tid: None,
        start_ticks: None,
    }
}

#[derive(Default, PartialEq, Eq)]
struct Inventory {
    luns: BTreeSet<String>,
    blocks: BTreeMap<DeviceNumber, BlockNode>,
    characters: BTreeSet<DeviceNumber>,
}

fn device_number(path: &Path, budget: &mut Budget) -> Option<DeviceNumber> {
    let value = budget.text(&path.join("dev"), 128)?;
    let parsed = DeviceNumber::parse(&value);
    if parsed.is_none() {
        budget.issue("invalid_device_number");
    }
    parsed
}

fn graph_links(
    path: &Path,
    sys_devices: &Path,
    budget: &mut Budget,
) -> Vec<(DeviceNumber, PathBuf)> {
    let mut result = Vec::new();
    for entry in budget.entries(path) {
        match fs::canonicalize(entry) {
            Ok(target) if target.starts_with(sys_devices) => {
                if let Some(number) = device_number(&target, budget) {
                    result.push((number, target));
                }
            }
            _ => budget.issue("block_link_unresolved_or_outside_sysfs"),
        }
        if result.len() >= MAX_BLOCKS {
            budget.issue("block_link_limit");
            break;
        }
    }
    result.sort();
    result
}

fn inventory(usb: &Path, sys_devices: &Path, budget: &mut Budget) -> Inventory {
    let mut found = Inventory::default();
    let mut pending = VecDeque::from([(usb.to_path_buf(), 0usize)]);
    let mut visited = BTreeSet::new();
    while let Some((path, depth)) = pending.pop_front() {
        if !budget.available() {
            break;
        }
        let Some(stamp) = identity(&path) else {
            budget.issue("sysfs_generation_gone");
            continue;
        };
        if !visited.insert(stamp) {
            continue;
        }
        let name = safe_name(&path);
        if let Some(name) = &name
            && name.split(':').count() == 4
            && name.split(':').all(|part| decimal::<u64>(part).is_some())
        {
            if found.luns.len() < MAX_LUNS {
                found.luns.insert(name.clone());
            } else {
                budget.issue("lun_limit");
            }
        }
        let entries = budget.entries(&path);
        let names: BTreeSet<_> = entries.iter().filter_map(|p| p.file_name()).collect();
        let has = |s: &str| names.contains(std::ffi::OsStr::new(s));
        if has("dev")
            && let Some(number) = device_number(&path, budget)
        {
            if has("size") && has("ro") {
                if found.blocks.len() >= MAX_BLOCKS {
                    budget.issue("block_limit");
                    continue;
                }
                let holders = graph_links(&path.join("holders"), sys_devices, budget);
                let slaves = graph_links(&path.join("slaves"), sys_devices, budget);
                for (_, holder) in &holders {
                    pending.push_back((holder.clone(), depth + 1));
                }
                let dm_hint = if has("dm") {
                    budget.text(&path.join("dm/uuid"), 4096).map(|uuid| {
                        if uuid.starts_with("CRYPT-") {
                            "crypt"
                        } else if uuid.starts_with("LVM-") {
                            "lvm"
                        } else {
                            "other"
                        }
                    })
                } else {
                    None
                };
                let ro = match budget.number::<u8>(&path.join("ro")) {
                    Some(0) => Some(false),
                    Some(1) => Some(true),
                    _ => {
                        budget.issue("invalid_block_read_only");
                        None
                    }
                };
                if let Some(name) = name {
                    found.blocks.insert(
                        number,
                        BlockNode {
                            name,
                            device: number,
                            sysfs_inode: stamp.1,
                            disk_sequence: budget.optional_number(&path.join("diskseq")),
                            partition: budget.optional_number(&path.join("partition")),
                            sectors: budget.number(&path.join("size")),
                            kernel_read_only: ro,
                            dm_hint,
                            holders: holders.iter().map(|(n, _)| *n).collect(),
                            slaves: slaves.iter().map(|(n, _)| *n).collect(),
                        },
                    );
                } else {
                    budget.issue("invalid_kernel_name");
                }
            } else if found.characters.len() < MAX_BLOCKS {
                // USB devnum, SCSI generic and bsg character nodes are also use surfaces.
                found.characters.insert(number);
            } else {
                budget.issue("character_node_limit");
            }
        }
        for child in entries {
            if child
                .file_name()
                .is_some_and(|n| n == "power" || n == "holders" || n == "slaves")
            {
                continue;
            }
            match fs::symlink_metadata(&child) {
                Ok(metadata) if metadata.is_dir() => {
                    if depth < 16 {
                        pending.push_back((child, depth + 1));
                    } else {
                        budget.issue("sysfs_depth_limit");
                    }
                }
                Ok(_) => (), // General sysfs links are deliberately not traversed.
                Err(_) => budget.issue("sysfs_changed_or_unreadable"),
            }
        }
        if identity(&path) != Some(stamp) {
            budget.issue("sysfs_generation_changed");
        }
    }
    if found.luns.is_empty() || found.blocks.is_empty() {
        budget.issue("no_complete_storage_inventory");
    }
    found
}

/// Inspect one current USB fingerprint using fixed kernel metadata roots.
///
/// Requires effective UID 0. This synchronous function belongs on an owned
/// blocking worker. It never authorizes export, even with no observed usage.
/// A blocked kernel metadata call can exceed the cooperative time budget.
pub fn inspect(device_id: &str) -> Result<StorageInspection> {
    if !rustix::process::geteuid().is_root() {
        return Err(Error::Permission("storage inspection requires root".into()));
    }
    if device_id.len() != 68
        || !device_id.starts_with("usb-")
        || !device_id[4..]
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(Error::InvalidInput(
            "expected a current USB fingerprint".into(),
        ));
    }
    for (path, magic) in [("/sys", SYSFS), ("/proc", PROCFS)] {
        let file = File::open(path)?;
        if rustix::fs::fstatfs(&file)
            .map_err(std::io::Error::from)?
            .f_type as i64
            != magic
        {
            return Err(Error::State(
                "storage inspection requires kernel sysfs and procfs".into(),
            ));
        }
    }
    let devices = SysfsDiscovery::default().scan()?;
    let mut matches = devices.iter().filter(|d| d.id == device_id);
    let device = matches
        .next()
        .ok_or_else(|| Error::State("storage inspection target is absent".into()))?;
    if matches.next().is_some() {
        return Err(Error::State(
            "storage inspection target is ambiguous".into(),
        ));
    }
    let path = Path::new("/sys/bus/usb/devices").join(device.bus_id.as_str());
    let canonical = fs::canonicalize(&path)?;
    if !canonical.starts_with("/sys/devices") {
        return Err(Error::State("USB sysfs ancestry is invalid".into()));
    }
    let pinned = File::open(&canonical)?;
    if !pinned.metadata()?.is_dir()
        || rustix::fs::fstatfs(&pinned)
            .map_err(std::io::Error::from)?
            .f_type as i64
            != SYSFS
    {
        return Err(Error::State("USB sysfs generation is invalid".into()));
    }
    let pinned_path = PathBuf::from(format!("/proc/self/fd/{}", pinned.as_raw_fd()));
    if !same_target(device, &pinned_path, &canonical) {
        return Err(Error::State(
            "USB fingerprint or address changed before inspection".into(),
        ));
    }
    let mut report = inspect_paths(
        device_id,
        device.bus_id.as_str(),
        &pinned_path,
        &canonical,
        Path::new("/sys/devices"),
        Path::new("/proc"),
    )?;
    if !same_target(device, &pinned_path, &canonical) {
        report.issues.insert("usb_fingerprint_or_address_changed");
    }
    // The actual directory FD stays alive through the entire observation.
    drop(pinned);
    Ok(report)
}

fn same_target(device: &rustusb_core::Device, pinned: &Path, canonical: &Path) -> bool {
    let mut issues = Vec::new();
    let vendor = crate::number(pinned, "idVendor", 16, 65535, &mut issues) as u16;
    let product = crate::number(pinned, "idProduct", 16, 65535, &mut issues) as u16;
    let serial = crate::optional_text(pinned, "serial", &mut issues);
    let address = crate::number(pinned, "devnum", 10, 127, &mut issues);
    let topology = crate::physical_topology(canonical, &device.bus_id);
    issues.is_empty()
        && vendor == device.vendor_id
        && product == device.product_id
        && serial == device.serial
        && address != 0
        && address == device.device_address
        && topology.as_deref() == Some(device.topology.as_str())
        && crate::fingerprint(vendor, product, serial.as_deref(), &device.topology) == device.id
}

fn inspect_paths(
    id: &str,
    bus_id: &str,
    usb: &Path,
    original: &Path,
    sys_devices: &Path,
    proc: &Path,
) -> Result<StorageInspection> {
    let mut budget = Budget::new();
    let generation =
        identity(usb).ok_or_else(|| Error::State("USB generation unavailable".into()))?;
    let address = budget
        .number::<u32>(&usb.join("devnum"))
        .ok_or_else(|| Error::State("USB address unavailable".into()))?;
    let before = inventory(usb, sys_devices, &mut budget);
    let mut uses = BTreeSet::new();
    for block in before.blocks.values() {
        if !block.holders.is_empty() {
            add_use(
                &mut uses,
                standalone_use("holder", block.device),
                &mut budget,
            );
        }
    }
    let tasks = process::scan(proc, &before, &mut uses, &mut budget);
    let after = inventory(usb, sys_devices, &mut budget);
    if before != after {
        budget.issue("inventory_changed_or_incomplete");
    }
    if identity(usb) != Some(generation)
        || identity(original) != Some(generation)
        || budget.number::<u32>(&usb.join("devnum")) != Some(address)
    {
        budget.issue("usb_generation_changed_or_gone");
    }
    Ok(StorageInspection {
        schema_version: 1,
        device_id: id.to_owned(),
        bus_id: bus_id.to_owned(),
        usb_generation: generation,
        usb_address: address,
        luns: before.luns.into_iter().collect(),
        blocks: before.blocks.into_values().collect(),
        busy_observed: !uses.is_empty(),
        uses,
        tasks_observed: tasks,
        elapsed_ms: budget.start.elapsed().as_millis().min(u64::MAX as u128) as u64,
        issues: budget.issues,
        limitations: vec![
            "not_an_export_permission",
            "non_atomic_observation",
            "hidden_or_taskless_namespaces",
            "kernel_references_not_enumerable",
            "filesystem_and_encryption_signatures_not_probed",
            "unenumerated_luns_possible",
            "device_write_protection_unknown",
        ],
    })
}

#[cfg(test)]
mod tests;

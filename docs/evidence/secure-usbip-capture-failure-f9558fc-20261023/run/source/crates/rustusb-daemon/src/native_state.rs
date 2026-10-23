//! Separate durable secure Native intents. Never reinterprets USBFS journal schemas.
use crate::native_channel::SecureImportTarget;
use rustix::fs::{AtFlags, FlockOperation, Mode, OFlags};
use rustusb_core::{Error, Result};
use rustusb_native::channel::{ImportIntent, ImportReceipt};
use rustusb_native::export_channel::{ExportReceipt, RegistrationSnapshot, StubReceipt};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::File,
    io::{Read, Write},
    os::unix::fs::MetadataExt,
    path::{Component, Path},
};
const LIMIT: usize = 1024 * 1024;
const DATA: &str = "channels.json";
const TEMP: &str = ".channels.tmp";
const LOCK: &str = "lock";
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Owner {
    pub uid: u32,
    pub pid: u32,
    pub starttime: u64,
    pub boot: String,
}
pub(crate) fn uuid(path: &str) -> Result<String> {
    let mut text = String::new();
    File::open(path)?.take(65).read_to_string(&mut text)?;
    let value = text.trim();
    validate_id(value)?;
    Ok(value.to_owned())
}
pub(crate) fn validate_id(value: &str) -> Result<()> {
    if value.len() != 36
        || !value.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
            }
        })
    {
        return Err(Error::InvalidInput("invalid Native channel UUID".into()));
    }
    Ok(())
}
fn starttime(pid: u32) -> Result<u64> {
    let mut stat = String::new();
    File::open(format!("/proc/{pid}/stat"))?
        .take(4097)
        .read_to_string(&mut stat)?;
    if stat.len() > 4096 || !stat.starts_with(&format!("{pid} (")) {
        return Err(Error::State("invalid Native owner observation".into()));
    }
    stat.rsplit_once(") ")
        .and_then(|(_, s)| s.split_whitespace().nth(19))
        .and_then(|s| s.parse().ok())
        .filter(|n| *n > 0)
        .ok_or_else(|| Error::State("cannot observe Native owner starttime".into()))
}
pub(crate) fn owner(uid: u32, pid: Option<i32>) -> Result<Owner> {
    let pid = pid
        .and_then(|p| u32::try_from(p).ok())
        .filter(|p| *p > 0)
        .ok_or_else(|| Error::Permission("Native channel requires kernel peer PID".into()))?;
    Ok(Owner {
        uid,
        pid,
        starttime: starttime(pid)?,
        boot: uuid("/proc/sys/kernel/random/boot_id")?,
    })
}
pub(crate) fn owner_ended(owner: &Owner) -> Result<bool> {
    if owner.boot != uuid("/proc/sys/kernel/random/boot_id")? {
        return Ok(true);
    }
    match starttime(owner.pid) {
        Ok(start) => Ok(start != owner.starttime),
        Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(error) => Err(error),
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "phase", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum Phase {
    Prepared {},
    Intent { intent: ImportIntent },
    Observed { receipt: ImportReceipt },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Record {
    pub channel_id: String,
    pub owner: Owner,
    pub target: SecureImportTarget,
    pub state: Phase,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "phase", rename_all = "snake_case", deny_unknown_fields)]
pub(crate) enum StubPhase {
    Prepared {},
    Intent {},
    Observed { receipt: StubReceipt },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct StubRecord {
    pub channel_id: String,
    pub state: StubPhase,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ExportRecord {
    pub lease_id: String,
    pub owner: Owner,
    pub registration: RegistrationSnapshot,
    pub bound: Option<ExportReceipt>,
    pub stub: Option<StubRecord>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Disk {
    schema_version: u32,
    records: Vec<Record>,
    exports: Vec<ExportRecord>,
}
fn validate_directory(directory: &File, uid: u32) -> Result<()> {
    let metadata = directory.metadata()?;
    if !metadata.is_dir() || metadata.uid() != uid || metadata.mode() & 0o022 != 0 {
        return Err(Error::Permission(
            "Native channel journal requires protected, owner-controlled directories".into(),
        ));
    }
    Ok(())
}

// Traverse each component through its parent FD. No untrusted ancestor, symlink,
// or '..' is accepted. Only the private test entry point uses an anchor above /.
fn open_directory(path: &Path, anchor: &Path, uid: u32) -> Result<(File, bool)> {
    if !path.is_absolute() || !anchor.is_absolute() {
        return Err(Error::InvalidInput(
            "Native channel journal path must be absolute".into(),
        ));
    }
    let relative = path
        .strip_prefix(anchor)
        .map_err(|_| Error::InvalidInput("Native channel journal path escapes its root".into()))?;
    let components: Vec<_> = relative.components().collect();
    if components.is_empty()
        || components.len() > 128
        || components
            .iter()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(Error::InvalidInput(
            "invalid Native channel journal path components".into(),
        ));
    }
    let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
    let mut directory =
        File::from(rustix::fs::open(anchor, flags, Mode::empty()).map_err(std::io::Error::from)?);
    validate_directory(&directory, uid)?;
    let mut final_created = false;
    for (index, component) in components.iter().enumerate() {
        let Component::Normal(name) = component else {
            return Err(Error::InvalidInput(
                "invalid Native channel journal component".into(),
            ));
        };
        let mut created = false;
        let child = match rustix::fs::openat(&directory, *name, flags, Mode::empty()) {
            Ok(child) => child,
            Err(rustix::io::Errno::NOENT) => {
                match rustix::fs::mkdirat(&directory, *name, Mode::RUSR | Mode::WUSR | Mode::XUSR) {
                    Ok(()) => created = true,
                    Err(rustix::io::Errno::EXIST) => {}
                    Err(error) => return Err(std::io::Error::from(error).into()),
                }
                let child = rustix::fs::openat(&directory, *name, flags, Mode::empty())
                    .map_err(std::io::Error::from)?;
                if created {
                    rustix::fs::fchmod(&child, Mode::RUSR | Mode::WUSR | Mode::XUSR)
                        .map_err(std::io::Error::from)?;
                    directory.sync_all()?;
                }
                child
            }
            Err(error) => return Err(std::io::Error::from(error).into()),
        };
        directory = File::from(child);
        validate_directory(&directory, uid)?;
        if index + 1 == components.len() {
            final_created = created;
        }
    }
    Ok((directory, final_created))
}

fn validate_file(metadata: &std::fs::Metadata, uid: u32) -> Result<()> {
    if !metadata.is_file()
        || metadata.uid() != uid
        || metadata.nlink() != 1
        || metadata.mode() & 0o7777 != 0o600
    {
        return Err(Error::Permission(
            "Native channel journal files must be owner-only 0600 regular files with one link"
                .into(),
        ));
    }
    Ok(())
}

fn identity_at(directory: &File, name: &str, uid: u32) -> Result<Option<(u64, u64)>> {
    let stat = match rustix::fs::statat(directory, name, AtFlags::SYMLINK_NOFOLLOW) {
        Ok(stat) => stat,
        Err(rustix::io::Errno::NOENT) => return Ok(None),
        Err(error) => return Err(std::io::Error::from(error).into()),
    };
    if rustix::fs::FileType::from_raw_mode(stat.st_mode) != rustix::fs::FileType::RegularFile
        || stat.st_uid != uid
        || stat.st_nlink != 1
        || stat.st_mode & 0o7777 != 0o600
    {
        return Err(Error::Permission(
            "untrusted Native channel journal file".into(),
        ));
    }
    Ok(Some((stat.st_dev, stat.st_ino)))
}

fn open_file(directory: &File, name: &str, uid: u32, create: bool) -> Result<File> {
    let prior = identity_at(directory, name, uid)?;
    if !create && prior.is_none() {
        return Err(std::io::Error::from(std::io::ErrorKind::NotFound).into());
    }
    let flags = OFlags::RDWR | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC;
    let flags = if create {
        flags | OFlags::CREATE | OFlags::EXCL
    } else {
        flags
    };
    let file = File::from(
        rustix::fs::openat(directory, name, flags, Mode::RUSR | Mode::WUSR)
            .map_err(std::io::Error::from)?,
    );
    if create {
        rustix::fs::fchmod(&file, Mode::RUSR | Mode::WUSR).map_err(std::io::Error::from)?;
    }
    let metadata = file.metadata()?;
    validate_file(&metadata, uid)?;
    let identity = Some((metadata.dev(), metadata.ino()));
    if (!create && prior != identity) || identity_at(directory, name, uid)? != identity {
        return Err(Error::State(
            "Native channel journal file changed while opening".into(),
        ));
    }
    Ok(file)
}

pub(crate) struct Store {
    directory: File,
    lock: File,
    uid: u32,
    identity: Option<(u64, u64)>,
    bytes: Vec<u8>,
    records: BTreeMap<String, Record>,
    exports: BTreeMap<String, ExportRecord>,
    live: BTreeSet<String>,
    unresolved: BTreeSet<String>,
    poisoned: bool,
}
impl Store {
    pub(crate) fn open(path: &Path) -> Result<Self> {
        Self::open_anchored(path, Path::new("/"), 0)
    }
    fn open_anchored(path: &Path, anchor: &Path, uid: u32) -> Result<Self> {
        let (directory, created) = open_directory(path, anchor, uid)?;
        if directory.metadata()?.mode() & 0o7777 != 0o700 {
            return Err(Error::Permission(
                "Native state directory must be root0700".into(),
            ));
        }
        let lock_exists = identity_at(&directory, LOCK, uid)?.is_some();
        if !created && !lock_exists {
            return Err(Error::State(
                "existing Native state lost its lock; refusing initialization".into(),
            ));
        }
        let lock = open_file(&directory, LOCK, uid, !lock_exists)?;
        rustix::fs::flock(&lock, FlockOperation::NonBlockingLockExclusive)
            .map_err(std::io::Error::from)?;
        lock.sync_all()?;
        directory.sync_all()?;
        let identity = identity_at(&directory, DATA, uid)?;
        let mut store = Self {
            directory,
            lock,
            uid,
            identity,
            bytes: Vec::new(),
            records: BTreeMap::new(),
            exports: BTreeMap::new(),
            live: BTreeSet::new(),
            unresolved: BTreeSet::new(),
            poisoned: false,
        };
        if identity.is_none() {
            if !created {
                return Err(Error::State(
                    "existing Native state lost its journal; refusing initialization".into(),
                ));
            }
            store.commit(BTreeMap::new())?;
        } else {
            store.bytes = store.read_bytes()?;
            let disk: Disk = serde_json::from_slice(&store.bytes)
                .map_err(|_| Error::State("invalid Native channel journal".into()))?;
            if disk.schema_version != 1 || disk.records.len() + disk.exports.len() > 32 {
                return Err(Error::State("unsupported Native channel journal".into()));
            }
            for record in disk.records {
                validate_id(&record.channel_id)?;
                validate_id(&record.owner.boot)?;
                record.target.validate()?;
                let intent = match &record.state {
                    Phase::Prepared {} => None,
                    Phase::Intent { intent } => Some(intent),
                    Phase::Observed { receipt } => Some(&receipt.intent),
                };
                if let Some(intent) = intent {
                    let valid_path = intent.controller.path.parent()
                        == Some(Path::new("/sys/devices/platform"))
                        && intent
                            .controller
                            .path
                            .file_name()
                            .and_then(|n| n.to_str())
                            .and_then(|n| n.strip_prefix("vhci_hcd."))
                            .is_some_and(|n| {
                                !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit())
                            });
                    if !valid_path
                        || intent.controller.filesystem_device == 0
                        || intent.controller.inode == 0
                        || intent.controller.attribute_inode == 0
                        || intent.controller.attribute_device == 0
                    {
                        return Err(Error::State(
                            "invalid persisted Native controller receipt".into(),
                        ));
                    }
                }
                if let Phase::Observed { receipt } = &record.state
                    && (receipt.session.port != receipt.intent.port
                        || receipt.session.status != 6
                        || receipt.session.local_bus_id.is_none()
                        || receipt.session.local_device_generation.is_none()
                        || receipt.session.local_device_address.is_none()
                        || receipt.session.vendor_id != Some(record.target.remote.vendor_id)
                        || receipt.session.product_id != Some(record.target.remote.product_id)
                        || receipt.session.remote_device_id
                            != Some(
                                (record.target.remote.bus_number << 16)
                                    | record.target.remote.device_number,
                            ))
                {
                    return Err(Error::State(
                        "invalid persisted Native local receipt".into(),
                    ));
                }
                if record.owner.uid == 0
                    || record.owner.pid == 0
                    || record.owner.starttime == 0
                    || store
                        .records
                        .insert(record.channel_id.clone(), record)
                        .is_some()
                {
                    return Err(Error::State(
                        "invalid or duplicate Native owner/record".into(),
                    ));
                }
            }
            for record in disk.exports {
                validate_id(&record.lease_id)?;
                validate_id(&record.owner.boot)?;
                record.registration.validate()?;
                if record.owner.uid == 0 || record.owner.pid == 0 || record.owner.starttime == 0 {
                    return Err(Error::State("invalid persisted Native export owner".into()));
                }
                if let Some(bound) = &record.bound
                    && (bound.generation != record.registration.generation
                        || bound.attribute_device == 0
                        || bound.attribute_inode == 0
                        || bound.bound_profile.configuration.is_some()
                        || !bound.bound_profile.interfaces.is_empty())
                {
                    return Err(Error::State(
                        "invalid persisted Native bound receipt".into(),
                    ));
                }
                if let Some(stub) = &record.stub {
                    validate_id(&stub.channel_id)?;
                    if record.bound.is_none() {
                        return Err(Error::State("stub without bound export".into()));
                    }
                    if let StubPhase::Observed { receipt } = &stub.state {
                        record
                            .registration
                            .validate_exported_profile(&receipt.current_profile)?;
                        if Some(&receipt.export) != record.bound.as_ref()
                            || receipt.kernel_socket_inode == 0
                            || receipt.usbip_status != 2
                        {
                            return Err(Error::State(
                                "invalid persisted Native stub receipt".into(),
                            ));
                        }
                    }
                }
                if store.records.contains_key(&record.lease_id)
                    || store
                        .exports
                        .insert(record.lease_id.clone(), record)
                        .is_some()
                {
                    return Err(Error::State("duplicate Native export lease".into()));
                }
            }
            store.unresolved = store
                .records
                .keys()
                .chain(store.exports.keys())
                .cloned()
                .collect();
        }
        store.healthy()?;
        Ok(store)
    }
    fn read_bytes(&self) -> Result<Vec<u8>> {
        let mut bytes = Vec::new();
        open_file(&self.directory, DATA, self.uid, false)?
            .take((LIMIT + 1) as u64)
            .read_to_end(&mut bytes)?;
        if bytes.len() > LIMIT {
            return Err(Error::State("Native journal exceeds bound".into()));
        }
        Ok(bytes)
    }
    fn healthy(&mut self) -> Result<()> {
        let valid = (|| -> Result<bool> {
            validate_directory(&self.directory, self.uid)?;
            if self.directory.metadata()?.mode() & 0o7777 != 0o700 {
                return Ok(false);
            }
            validate_file(&self.lock.metadata()?, self.uid)?;
            Ok(identity_at(&self.directory, LOCK, self.uid)?
                == Some((self.lock.metadata()?.dev(), self.lock.metadata()?.ino()))
                && identity_at(&self.directory, DATA, self.uid)? == self.identity
                && (self.identity.is_none() || self.read_bytes()? == self.bytes))
        })();
        if self.poisoned || !matches!(valid, Ok(true)) {
            self.poisoned = true;
            return Err(Error::State(
                "Native journal changed or is uncertain; authorization blocked".into(),
            ));
        }
        Ok(())
    }
    fn commit(&mut self, records: BTreeMap<String, Record>) -> Result<()> {
        self.commit_all(records, self.exports.clone())
    }
    fn commit_all(
        &mut self,
        records: BTreeMap<String, Record>,
        exports: BTreeMap<String, ExportRecord>,
    ) -> Result<()> {
        self.healthy()?;
        let result = (|| -> Result<(Vec<u8>, (u64, u64))> {
            let bytes = serde_json::to_vec(&Disk {
                schema_version: 1,
                records: records.values().cloned().collect(),
                exports: exports.values().cloned().collect(),
            })
            .map_err(|_| Error::State("cannot encode Native journal".into()))?;
            if bytes.len() > LIMIT || records.len() + exports.len() > 32 {
                return Err(Error::State("Native journal capacity exceeded".into()));
            }
            if identity_at(&self.directory, TEMP, self.uid)?.is_some() {
                rustix::fs::unlinkat(&self.directory, TEMP, AtFlags::empty())
                    .map_err(std::io::Error::from)?;
            }
            let mut file = open_file(&self.directory, TEMP, self.uid, true)?;
            file.write_all(&bytes)?;
            file.sync_all()?;
            let identity = (file.metadata()?.dev(), file.metadata()?.ino());
            rustix::fs::renameat(&self.directory, TEMP, &self.directory, DATA)
                .map_err(std::io::Error::from)?;
            self.directory.sync_all()?;
            if identity_at(&self.directory, DATA, self.uid)? != Some(identity) {
                return Err(Error::State("Native journal changed during commit".into()));
            }
            Ok((bytes, identity))
        })();
        match result {
            Ok((bytes, identity)) => {
                self.bytes = bytes;
                self.identity = Some(identity);
                self.records = records;
                self.exports = exports;
                Ok(())
            }
            Err(error) => {
                self.poisoned = true;
                Err(error)
            }
        }
    }
    pub(crate) fn reserve(&mut self, record: Record) -> Result<()> {
        self.healthy()?;
        if !self.unresolved.is_empty()
            || self.records.len() + self.exports.len() >= 16
            || self.records.values().any(|r| {
                r.target.server_id == record.target.server_id
                    && r.target.device_key == record.target.device_key
            })
        {
            return Err(Error::State(
                "existing or unresolved Native channel blocks import".into(),
            ));
        }
        let id = record.channel_id.clone();
        let mut records = self.records.clone();
        if records.insert(id.clone(), record).is_some() {
            return Err(Error::State("duplicate Native channel".into()));
        }
        self.commit(records)?;
        self.live.insert(id);
        Ok(())
    }
    pub(crate) fn update(&mut self, id: &str, phase: Phase) -> Result<()> {
        let mut records = self.records.clone();
        let record = records
            .get_mut(id)
            .ok_or_else(|| Error::State("missing Native intent".into()))?;
        record.state = phase;
        self.commit(records)
    }
    pub(crate) fn retire(&mut self, id: &str) -> Result<()> {
        let mut records = self.records.clone();
        records.remove(id);
        self.commit(records)?;
        self.live.remove(id);
        self.unresolved.remove(id);
        Ok(())
    }
    pub(crate) fn unresolved(&mut self, id: &str) {
        self.live.remove(id);
        if self.records.contains_key(id) || self.exports.contains_key(id) {
            self.unresolved.insert(id.to_owned());
        }
    }
    #[cfg(test)]
    pub(crate) fn snapshot(&mut self) -> Result<Vec<Record>> {
        self.healthy()?;
        Ok(self.records.values().cloned().collect())
    }
    pub(crate) fn reserve_export(&mut self, record: ExportRecord) -> Result<()> {
        self.healthy()?;
        if !self.unresolved.is_empty()
            || self.records.len() + self.exports.len() >= 16
            || self
                .exports
                .values()
                .any(|r| r.registration.device_key == record.registration.device_key)
            || self.records.contains_key(&record.lease_id)
        {
            return Err(Error::State(
                "existing or unresolved Native lease blocks export".into(),
            ));
        }
        record.registration.validate()?;
        let id = record.lease_id.clone();
        let mut exports = self.exports.clone();
        if exports.insert(id.clone(), record).is_some() {
            return Err(Error::State("duplicate Native export".into()));
        }
        self.commit_all(self.records.clone(), exports)?;
        self.live.insert(id);
        Ok(())
    }
    pub(crate) fn update_export(
        &mut self,
        id: &str,
        action: impl FnOnce(&mut ExportRecord) -> Result<()>,
    ) -> Result<()> {
        let mut exports = self.exports.clone();
        action(
            exports
                .get_mut(id)
                .ok_or_else(|| Error::State("missing Native export".into()))?,
        )?;
        self.commit_all(self.records.clone(), exports)
    }
    pub(crate) fn retire_export(&mut self, id: &str) -> Result<()> {
        let mut exports = self.exports.clone();
        exports.remove(id);
        self.commit_all(self.records.clone(), exports)?;
        self.live.remove(id);
        self.unresolved.remove(id);
        Ok(())
    }
    pub(crate) fn pending(&mut self, after: Option<&str>) -> Result<serde_json::Value> {
        self.healthy()?;
        if let Some(after) = after {
            validate_id(after)?;
        }
        let mut rows = BTreeMap::new();
        for r in self.records.values() {
            let (phase, port) = match &r.state {
                Phase::Prepared {} => ("prepared", None),
                Phase::Intent { intent } => ("intent", Some(intent.port)),
                Phase::Observed { receipt } => ("observed", Some(receipt.intent.port)),
            };
            rows.insert(r.channel_id.clone(), serde_json::json!({"channel_id":r.channel_id,"kind":"import","owner":r.owner,"device_key":r.target.device_key,"server_id":r.target.server_id,"phase":phase,"port":port}));
        }
        for r in self.exports.values() {
            let phase = match r.stub.as_ref().map(|s| &s.state) {
                Some(StubPhase::Prepared {}) => "stub_prepared",
                Some(StubPhase::Intent {}) => "stub_intent",
                Some(StubPhase::Observed { .. }) => "stub_observed",
                None if r.bound.is_some() => "exported",
                None => "registering",
            };
            rows.insert(r.lease_id.clone(), serde_json::json!({"channel_id":r.lease_id,"kind":"export","owner":r.owner,"device_key":r.registration.device_key,"phase":phase,"stub_channel_id":r.stub.as_ref().map(|s| &s.channel_id)}));
        }
        let mut candidates = rows
            .into_iter()
            .filter(|(id, _)| after.is_none_or(|after| id.as_str() > after));
        let page: Vec<_> = candidates.by_ref().take(8).collect();
        let next = if candidates.next().is_some() {
            page.last().map(|(id, _)| id.clone())
        } else {
            None
        };
        Ok(
            serde_json::json!({"schema_version":1,"records":page.into_iter().map(|(_,r)| r).collect::<Vec<_>>(),"next_after":next}),
        )
    }
    pub(crate) fn reconcile(&mut self, id: &str) -> Result<()> {
        self.healthy()?;
        if self.live.contains(id) {
            return Err(Error::State(
                "Native channel is still owned by this helper".into(),
            ));
        }
        if let Some(record) = self.exports.get(id) {
            if !owner_ended(&record.owner)? {
                return Err(Error::State("Native owner has not ended".into()));
            }
            let old_boot = record.owner.boot != uuid("/proc/sys/kernel/random/boot_id")?;
            if !(old_boot || record.registration.gone()? || record.registration.restored()?) {
                return Err(Error::State(
                    "original Native drivers/configuration are not restored; lease retained".into(),
                ));
            }
            return self.retire_export(id);
        }
        let record = self
            .records
            .get(id)
            .ok_or_else(|| Error::State("unknown Native channel".into()))?;
        if !owner_ended(&record.owner)? {
            return Err(Error::State("Native owner has not ended".into()));
        }
        let old_boot = record.owner.boot != uuid("/proc/sys/kernel/random/boot_id")?;
        let safe = old_boot
            || match &record.state {
                Phase::Prepared {} => true,
                Phase::Observed { receipt } => rustusb_native::channel::released(receipt)?,
                Phase::Intent { intent } => rustusb_native::channel::unidentified_released(intent)?,
            };
        if !safe {
            return Err(Error::State(
                "Native release cannot be proven; pending record retained".into(),
            ));
        }
        self.retire(id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{fs, os::unix::fs::PermissionsExt};
    fn export_fixture(id: &str) -> Result<ExportRecord> {
        use rustusb_native::export_channel::{ExportInterface, ExportProfile, HardwareGeneration};
        let bus = rustusb_core::BusId::new("1-1")?;
        let generation = HardwareGeneration {
            bus_id: bus.clone(),
            sysfs_path: "/sys/devices/test/1-1".into(),
            filesystem_device: 1,
            inode: 2,
            bus_number: 1,
            device_number: 3,
            vendor_id: 1,
            product_id: 2,
            device_version: 0x100,
            speed: 3,
            class: 0,
            subclass: 0,
            protocol: 0,
            num_configurations: 1,
        };
        Ok(ExportRecord {
            lease_id: id.into(),
            owner: Owner {
                uid: 1000,
                pid: 1,
                starttime: 1,
                boot: "00000000-0000-4000-8000-000000000002".into(),
            },
            registration: RegistrationSnapshot {
                device_key: rustusb_core::DeviceKey::native(&format!("usb-{}", "a".repeat(64)))?,
                generation,
                original_profile: ExportProfile {
                    configuration: Some(1),
                    interfaces: vec![ExportInterface {
                        number: 0,
                        alternate: 0,
                        class: 3,
                        subclass: 1,
                        protocol: 1,
                        driver: Some("usbhid".into()),
                    }],
                },
                original_device_driver: "usb".into(),
                published_device: rustusb_protocol::RemoteDevice {
                    bus_id: bus,
                    path: "/sys/devices/test/1-1".into(),
                    bus_number: 1,
                    device_number: 3,
                    speed: 3,
                    vendor_id: 1,
                    product_id: 2,
                    device_version: 0x100,
                    class: 0,
                    subclass: 0,
                    protocol: 0,
                    configuration: 1,
                    num_configurations: 1,
                    interfaces: vec![rustusb_protocol::RemoteInterface {
                        class: 3,
                        subclass: 1,
                        protocol: 1,
                    }],
                },
            },
            bound: None,
            stub: None,
        })
    }
    #[test]
    fn published_registration_and_actual_export_phases_remain_distinct() -> Result<()> {
        use rustusb_native::export_channel::ExportProfile;
        let record = export_fixture("00000000-0000-4000-8000-000000000001")?;
        record.registration.validate()?;
        assert_eq!(record.registration.published_device.configuration, 1);
        record
            .registration
            .validate_exported_profile(&ExportProfile {
                configuration: None,
                interfaces: vec![],
            })?;
        let mut imported = record.registration.original_profile.clone();
        for interface in &mut imported.interfaces {
            interface.driver = None;
        }
        record.registration.validate_exported_profile(&imported)?;
        imported.interfaces[0].alternate = 1;
        assert!(
            record
                .registration
                .validate_exported_profile(&imported)
                .is_err()
        );
        assert!(
            record
                .registration
                .validate_exported_profile(&record.registration.original_profile)
                .is_err()
        );
        let mut corrupted = record.registration;
        corrupted.published_device.product_id = 42;
        assert!(corrupted.validate().is_err());
        Ok(())
    }
    #[test]
    fn export_registration_survives_restart_and_blocks_adoption_without_usb() -> Result<()> {
        let root = tempfile::tempdir()?;
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700))?;
        let path = root.path().join("native");
        let uid = rustix::process::geteuid().as_raw();
        let record = export_fixture("00000000-0000-4000-8000-000000000001")?;
        let mut store = Store::open_anchored(&path, root.path(), uid)?;
        store.reserve_export(record.clone())?;
        assert!(
            store
                .reserve_export(export_fixture("00000000-0000-4000-8000-000000000003")?)
                .is_err()
        );
        drop(store);
        let mut reopened = Store::open_anchored(&path, root.path(), uid)?;
        assert!(reopened.unresolved.contains(&record.lease_id));
        assert_eq!(reopened.pending(None)?["records"][0]["kind"], "export");
        assert!(
            reopened
                .reserve_export(export_fixture("00000000-0000-4000-8000-000000000004")?)
                .is_err()
        );
        // Deleting the durable registration is never a side effect of a rejected grant.
        assert_eq!(reopened.exports.len(), 1);
        Ok(())
    }
    #[test]
    fn pending_pages_are_small_stable_summaries_and_validate_cursors() -> Result<()> {
        let root = tempfile::tempdir()?;
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700))?;
        let path = root.path().join("native");
        let uid = rustix::process::geteuid().as_raw();
        let mut store = Store::open_anchored(&path, root.path(), uid)?;
        for n in 0..9u8 {
            let mut record = export_fixture(&format!("00000000-0000-4000-8000-{n:012x}"))?;
            record.registration.device_key =
                rustusb_core::DeviceKey::native(&format!("usb-{n:064x}"))?;
            store.reserve_export(record)?;
        }
        let first = store.pending(None)?;
        assert_eq!(first["records"].as_array().map(Vec::len), Some(8));
        let cursor = first["next_after"]
            .as_str()
            .ok_or_else(|| Error::State("test missing cursor".into()))?;
        let last = store.pending(Some(cursor))?;
        assert_eq!(last["records"].as_array().map(Vec::len), Some(1));
        assert!(last["next_after"].is_null());
        assert!(first["records"][0].get("registration").is_none());
        assert!(
            serde_json::to_vec(&first)
                .map_err(|_| Error::State("test JSON".into()))?
                .len()
                < crate::MAX_FRAME_BYTES
        );
        assert!(store.pending(Some("invalid")).is_err());
        Ok(())
    }
    #[test]
    fn journal_lock_and_same_bytes_identity_are_enforced_without_usb() -> Result<()> {
        let root = tempfile::tempdir()?;
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700))?;
        let uid = rustix::process::geteuid().as_raw();
        let path = root.path().join("native");
        let mut store = Store::open_anchored(&path, root.path(), uid)?;
        assert!(Store::open_anchored(&path, root.path(), uid).is_err());
        assert!(store.snapshot()?.is_empty());
        fs::write(path.join(DATA), b"{\"schema_version\":1,\"records\":[]} ")?;
        assert!(store.snapshot().is_err());
        assert!(store.poisoned);
        drop(store);
        fs::remove_file(path.join(DATA))?;
        assert!(Store::open_anchored(&path, root.path(), uid).is_err());
        Ok(())
    }
    #[test]
    fn persisted_prepared_channel_blocks_new_grants_after_restart() -> Result<()> {
        let root = tempfile::tempdir()?;
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700))?;
        let uid = rustix::process::geteuid().as_raw();
        let path = root.path().join("native");
        let target = SecureImportTarget {
            server_id: "example".into(),
            device_key: rustusb_core::DeviceKey::virtual_profile(rustusb_core::VirtualProfile::Hid),
            remote: rustusb_protocol::RemoteDevice {
                bus_id: rustusb_core::BusId::new("1-1")?,
                path: "/test".into(),
                bus_number: 1,
                device_number: 2,
                speed: 3,
                vendor_id: 1,
                product_id: 2,
                device_version: 0,
                class: 0,
                subclass: 0,
                protocol: 0,
                configuration: 1,
                num_configurations: 1,
                interfaces: vec![rustusb_protocol::RemoteInterface {
                    class: 3,
                    subclass: 1,
                    protocol: 1,
                }],
            },
        };
        let id = "00000000-0000-4000-8000-000000000001".to_owned();
        let mut first = Store::open_anchored(&path, root.path(), uid)?;
        let record = Record {
            channel_id: id.clone(),
            owner: Owner {
                uid: 1000,
                pid: 1,
                starttime: 1,
                boot: "00000000-0000-4000-8000-000000000002".into(),
            },
            target,
            state: Phase::Prepared {},
        };
        first.reserve(record.clone())?;
        drop(first);
        let mut reopened = Store::open_anchored(&path, root.path(), uid)?;
        assert_eq!(reopened.snapshot()?.len(), 1);
        assert!(reopened.unresolved.contains(&id));
        let mut next = record;
        next.channel_id = "00000000-0000-4000-8000-000000000003".into();
        assert!(reopened.reserve(next).is_err());
        Ok(())
    }
}

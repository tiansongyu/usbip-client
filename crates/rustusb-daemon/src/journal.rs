//! Bounded, root-protected physical authorization records, not a recovery engine.
//!
//! All operations are synchronous and must be offloaded by the async helper.
//! The process-wide caller serializes access; the retained flock additionally
//! excludes other helpers. An uncertain write poisons both reads and mutations.

use rustix::fs::{AtFlags, FlockOperation, Mode, OFlags};
use rustusb_core::{BusId, Error, PhysicalDeviceReceipt, Result};
use serde::{Deserialize, Deserializer, Serialize, de};
use std::{
    cell::Cell,
    collections::{BTreeMap, BTreeSet},
    fmt,
    fs::File,
    io::{Read, Write},
    marker::PhantomData,
    os::unix::fs::MetadataExt,
    path::{Component, Path},
};

const SCHEMA_VERSION: u32 = 1;
const MAX_RECORDS: usize = 256;
const MAX_INTERFACES: usize = 256;
const MAX_BYTES: usize = 16 * 1024 * 1024;
const JOURNAL: &str = "journal.json";
const LOCK: &str = "lock";
// Only this locked directory's secure, fixed temporary file may be removed.
const TEMP: &str = ".journal.tmp";

/// One interface in the independently observed original active configuration.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct OriginalInterface {
    /// USB interface number, unique within this baseline.
    pub number: u8,
    /// Actual original alternate setting, including nonzero settings.
    pub alternate: u8,
    /// Original driver basename; absence is an explicit observation.
    #[serde(deserialize_with = "required_driver")]
    pub driver: Option<String>,
}

/// Nonsensitive state against which cooperative finish is independently checked.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Baseline {
    /// Administrator-authorized persistent fingerprint.
    pub device_id: String,
    /// Current kernel address; never sufficient authorization by itself.
    pub bus_id: BusId,
    /// Exact sysfs and usbfs generation granted to this owner.
    pub receipt: PhysicalDeviceReceipt,
    /// Actual original active configuration, with zero meaning unconfigured.
    pub configuration: u8,
    /// Complete original active interface set, bounded before allocation.
    #[serde(deserialize_with = "interfaces")]
    pub interfaces: Vec<OriginalInterface>,
}

/// OS peer identity captured by the helper, not supplied by the RPC caller.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Owner {
    /// Peer effective UID.
    pub uid: u32,
    /// Peer process ID, interpreted together with start time and boot ID.
    pub pid: u32,
    /// Linux /proc starttime ticks for this process generation.
    pub start_time: u64,
    /// Canonical lowercase kernel boot UUID.
    pub boot_id: String,
}

/// One unresolved authorization, persisted before any FD delivery.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Record {
    /// Random helper-issued canonical UUID, not a bearer capability.
    pub lease_id: String,
    /// Original OS owner permitted to declare cooperative finish.
    pub owner: Owner,
    /// Original state which a restart must not silently replace.
    pub baseline: Baseline,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Journal {
    schema_version: u32,
    #[serde(deserialize_with = "records")]
    records: Vec<Record>,
}

#[derive(Serialize)]
struct JournalRef<'a> {
    schema_version: u32,
    records: Vec<&'a Record>,
}

fn bounded_vec<'de, D, T, const N: usize>(deserializer: D) -> std::result::Result<Vec<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    struct Visitor<T, const N: usize>(PhantomData<T>);
    impl<'de, T: Deserialize<'de>, const N: usize> de::Visitor<'de> for Visitor<T, N> {
        type Value = Vec<T>;

        fn expecting(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
            write!(formatter, "an array containing at most {N} entries")
        }

        fn visit_seq<A: de::SeqAccess<'de>>(
            self,
            mut sequence: A,
        ) -> std::result::Result<Self::Value, A::Error> {
            if sequence.size_hint().is_some_and(|size| size > N) {
                return Err(de::Error::custom("physical journal array exceeds limit"));
            }
            let mut values = Vec::with_capacity(sequence.size_hint().unwrap_or(0).min(N));
            while values.len() < N {
                let Some(value) = sequence.next_element()? else {
                    return Ok(values);
                };
                values.push(value);
            }
            if sequence.next_element::<de::IgnoredAny>()?.is_some() {
                return Err(de::Error::custom("physical journal array exceeds limit"));
            }
            Ok(values)
        }
    }
    deserializer.deserialize_seq(Visitor::<T, N>(PhantomData))
}

fn required_driver<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Option<String>, D::Error> {
    Option::<String>::deserialize(deserializer)
}

fn records<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<Record>, D::Error> {
    bounded_vec::<D, Record, MAX_RECORDS>(deserializer)
}

fn interfaces<'de, D: Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<Vec<OriginalInterface>, D::Error> {
    bounded_vec::<D, OriginalInterface, MAX_INTERFACES>(deserializer)
}

/// Validate the canonical 8-4-4-4-12 lowercase hexadecimal UUID wire format.
pub(crate) fn validate_lease_id(value: &str) -> Result<()> {
    if value.len() != 36
        || !value.bytes().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                byte == b'-'
            } else {
                byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
            }
        })
    {
        return Err(Error::InvalidInput(
            "physical lease ID must be a canonical lowercase UUID".into(),
        ));
    }
    Ok(())
}

fn validate_record(record: &Record) -> Result<()> {
    validate_lease_id(&record.lease_id)?;
    validate_lease_id(&record.owner.boot_id)
        .map_err(|_| Error::InvalidInput("physical owner boot ID is invalid".into()))?;
    if record.owner.uid == u32::MAX
        || record.owner.pid == 0
        || record.owner.pid > i32::MAX as u32
        || record.owner.start_time == 0
    {
        return Err(Error::InvalidInput(
            "physical owner identity is invalid".into(),
        ));
    }
    let baseline = &record.baseline;
    super::validate_device_id(&baseline.device_id)?;
    let bus = baseline
        .bus_id
        .as_str()
        .split_once('-')
        .and_then(|(bus, _)| bus.parse::<u16>().ok())
        .ok_or_else(|| Error::InvalidInput("physical baseline bus is invalid".into()))?;
    let receipt = &baseline.receipt;
    if bus == 0
        || receipt.bus_number != bus
        || receipt.sysfs_device == 0
        || receipt.sysfs_inode == 0
        || receipt.node_inode == 0
        || !(1..=127).contains(&receipt.usb_device_number)
        || rustix::fs::major(receipt.node_device) != 189
        || rustix::fs::minor(receipt.node_device)
            != (u32::from(bus) - 1) * 128 + receipt.usb_device_number - 1
    {
        return Err(Error::InvalidInput(
            "physical baseline receipt is invalid".into(),
        ));
    }
    if baseline.interfaces.len() > MAX_INTERFACES
        || (baseline.configuration == 0) != baseline.interfaces.is_empty()
    {
        return Err(Error::InvalidInput(
            "physical baseline configuration/interface set is invalid".into(),
        ));
    }
    let mut numbers = BTreeSet::new();
    for interface in &baseline.interfaces {
        if !numbers.insert(interface.number)
            || interface.driver.as_ref().is_some_and(|driver| {
                driver.is_empty()
                    || driver.len() > 64
                    || !driver
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || b"_-".contains(&byte))
                    || matches!(driver.as_str(), "usbfs" | "usbip-host")
            })
        {
            return Err(Error::InvalidInput(
                "physical baseline interface/driver is invalid".into(),
            ));
        }
    }
    Ok(())
}

fn validate_directory(directory: &File, uid: u32) -> Result<()> {
    let metadata = directory.metadata()?;
    if !metadata.is_dir() || metadata.uid() != uid || metadata.mode() & 0o022 != 0 {
        return Err(Error::Permission(
            "physical journal requires protected, owner-controlled directories".into(),
        ));
    }
    Ok(())
}

// Traverse each component through its parent FD. No untrusted ancestor, symlink,
// or '..' is accepted. Only the private test entry point uses an anchor above /.
fn open_directory(path: &Path, anchor: &Path, uid: u32) -> Result<(File, bool)> {
    if !path.is_absolute() || !anchor.is_absolute() {
        return Err(Error::InvalidInput(
            "physical journal path must be absolute".into(),
        ));
    }
    let relative = path
        .strip_prefix(anchor)
        .map_err(|_| Error::InvalidInput("physical journal path escapes its root".into()))?;
    let components: Vec<_> = relative.components().collect();
    if components.is_empty()
        || components.len() > 128
        || components
            .iter()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(Error::InvalidInput(
            "invalid physical journal path components".into(),
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
                "invalid physical journal component".into(),
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
            "physical journal files must be owner-only 0600 regular files with one link".into(),
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
        return Err(Error::Permission("untrusted physical journal file".into()));
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
            "physical journal file changed while opening".into(),
        ));
    }
    Ok(file)
}

/// Synchronous durable authorization table. The retained lock is never delegated.
pub(crate) struct Store {
    directory: File,
    lock: File,
    uid: u32,
    journal_identity: Option<(u64, u64)>,
    records: BTreeMap<String, Record>,
    poisoned: Cell<bool>,
    journal_bytes: Option<Vec<u8>>,
    #[cfg(test)]
    fail_after_rename: bool,
}

impl Store {
    /// Open a protected absolute directory, creating missing protected parents.
    /// An existing final directory without its journal is never reinitialized.
    pub(crate) fn open(path: &Path) -> Result<Self> {
        Self::open_anchored(path, Path::new("/"), 0)
    }

    fn open_anchored(path: &Path, anchor: &Path, uid: u32) -> Result<Self> {
        let (directory, created) = open_directory(path, anchor, uid)?;
        let lock_exists = identity_at(&directory, LOCK, uid)?.is_some();
        if !created && !lock_exists {
            return Err(Error::State(
                "existing physical state directory has no lifetime lock; refusing reinitialization"
                    .into(),
            ));
        }
        let lock = open_file(&directory, LOCK, uid, !lock_exists)?;
        rustix::fs::flock(&lock, FlockOperation::NonBlockingLockExclusive)
            .map_err(std::io::Error::from)?;
        if !lock_exists {
            lock.sync_all()?;
            directory.sync_all()?;
        }
        let journal_identity = identity_at(&directory, JOURNAL, uid)?;
        let mut store = Self {
            directory,
            lock,
            uid,
            journal_identity,
            records: BTreeMap::new(),
            poisoned: Cell::new(false),
            journal_bytes: None,
            #[cfg(test)]
            fail_after_rename: false,
        };
        if journal_identity.is_none() {
            if !created {
                return Err(Error::State(
                    "existing physical state directory has no journal; refusing initialization"
                        .into(),
                ));
            }
            store.commit(&BTreeMap::new())?;
        } else {
            store.load()?;
        }
        store.healthy()?;
        Ok(store)
    }

    fn healthy(&self) -> Result<()> {
        if self.poisoned.get() {
            return Err(Error::State(
                "physical journal commit or observation is uncertain; this store is quarantined"
                    .into(),
            ));
        }
        let result = self.check_unchanged();
        if result.is_err() {
            self.poisoned.set(true);
        }
        result
    }

    fn check_unchanged(&self) -> Result<()> {
        validate_directory(&self.directory, self.uid)?;
        if self.directory.metadata()?.mode() & 0o7777 != 0o700 {
            return Err(Error::Permission(
                "physical journal directory must be owner-only 0700".into(),
            ));
        }
        let lock = self.lock.metadata()?;
        validate_file(&lock, self.uid)?;
        if identity_at(&self.directory, LOCK, self.uid)? != Some((lock.dev(), lock.ino()))
            || identity_at(&self.directory, JOURNAL, self.uid)? != self.journal_identity
        {
            return Err(Error::State(
                "physical journal or lifetime lock was replaced or removed".into(),
            ));
        }
        if let Some(expected) = &self.journal_bytes {
            let file = open_file(&self.directory, JOURNAL, self.uid, false)?;
            if file.metadata()?.len() != expected.len() as u64 {
                return Err(Error::State(
                    "physical journal changed outside this helper".into(),
                ));
            }
            let mut observed = Vec::new();
            file.take(MAX_BYTES as u64 + 1).read_to_end(&mut observed)?;
            if observed != *expected {
                return Err(Error::State(
                    "physical journal changed outside this helper".into(),
                ));
            }
        }
        Ok(())
    }

    fn load(&mut self) -> Result<()> {
        let file = open_file(&self.directory, JOURNAL, self.uid, false)?;
        if file.metadata()?.len() > MAX_BYTES as u64 {
            return Err(Error::State("physical journal exceeds byte limit".into()));
        }
        let mut bytes = Vec::new();
        file.take(MAX_BYTES as u64 + 1).read_to_end(&mut bytes)?;
        if bytes.len() > MAX_BYTES {
            return Err(Error::State("physical journal exceeds byte limit".into()));
        }
        let journal: Journal = serde_json::from_slice(&bytes)
            .map_err(|_| Error::State("physical journal schema or JSON is invalid".into()))?;
        if journal.schema_version != SCHEMA_VERSION || journal.records.len() > MAX_RECORDS {
            return Err(Error::State(
                "unsupported physical journal version or record limit".into(),
            ));
        }
        self.journal_bytes = Some(bytes);
        let mut devices = BTreeSet::new();
        for record in journal.records {
            validate_record(&record)?;
            if !devices.insert(record.baseline.device_id.clone())
                || self
                    .records
                    .insert(record.lease_id.clone(), record)
                    .is_some()
            {
                return Err(Error::State(
                    "duplicate physical journal lease or device".into(),
                ));
            }
        }
        Ok(())
    }

    /// Persist one new authorization before any descriptor can be delivered.
    pub(crate) fn reserve(&mut self, record: Record) -> Result<()> {
        self.healthy()?;
        validate_record(&record)?;
        if self.records.len() >= MAX_RECORDS
            || self.records.contains_key(&record.lease_id)
            || self
                .records
                .values()
                .any(|old| old.baseline.device_id == record.baseline.device_id)
        {
            return Err(Error::State(
                "physical journal is full or authorization is unresolved".into(),
            ));
        }
        let mut next = self.records.clone();
        next.insert(record.lease_id.clone(), record);
        self.commit(&next)?;
        self.records = next;
        Ok(())
    }

    /// Return a copy of an unresolved authorization; poisoned stores refuse reads.
    pub(crate) fn get(&self, lease_id: &str) -> Result<Option<Record>> {
        self.healthy()?;
        validate_lease_id(lease_id)?;
        Ok(self.records.get(lease_id).cloned())
    }

    /// Test whether a fingerprint remains blocked, including a new generation.
    pub(crate) fn has_device(&self, device_id: &str) -> Result<bool> {
        self.healthy()?;
        super::validate_device_id(device_id)?;
        Ok(self
            .records
            .values()
            .any(|record| record.baseline.device_id == device_id))
    }

    /// Copy the bounded table in stable lease-ID order.
    pub(crate) fn records(&self) -> Result<Vec<Record>> {
        self.healthy()?;
        Ok(self.records.values().cloned().collect())
    }

    /// Durably remove a separately verified authorization; absence is idempotent.
    /// This storage operation performs no USB verification or capability revocation.
    pub(crate) fn retire(&mut self, lease_id: &str) -> Result<bool> {
        self.healthy()?;
        validate_lease_id(lease_id)?;
        if !self.records.contains_key(lease_id) {
            return Ok(false);
        }
        let mut next = self.records.clone();
        next.remove(lease_id);
        self.commit(&next)?;
        self.records = next;
        Ok(true)
    }

    fn commit(&mut self, next: &BTreeMap<String, Record>) -> Result<()> {
        self.healthy()?;
        let result = self.write_atomic(next);
        if result.is_err() {
            // Even rename/fsync errors can leave a new on-disk table. Keep the
            // old cache and prohibit all further use until an explicit reopen.
            self.poisoned.set(true);
        }
        result
    }

    fn write_atomic(&mut self, next: &BTreeMap<String, Record>) -> Result<()> {
        validate_directory(&self.directory, self.uid)?;
        let lock = self.lock.metadata()?;
        validate_file(&lock, self.uid)?;
        if identity_at(&self.directory, LOCK, self.uid)? != Some((lock.dev(), lock.ino()))
            || identity_at(&self.directory, JOURNAL, self.uid)? != self.journal_identity
        {
            return Err(Error::State(
                "physical journal or lifetime lock was replaced".into(),
            ));
        }
        let mut bytes = serde_json::to_vec(&JournalRef {
            schema_version: SCHEMA_VERSION,
            records: next.values().collect(),
        })
        .map_err(|_| Error::State("cannot serialize physical journal".into()))?;
        bytes.push(b'\n');
        if bytes.len() > MAX_BYTES {
            return Err(Error::State("physical journal exceeds byte limit".into()));
        }
        // A crashed prior writer may have left this fixed private name. Never
        // unlink a symlink, hardlink, foreign owner or permissive file here.
        if identity_at(&self.directory, TEMP, self.uid)?.is_some() {
            rustix::fs::unlinkat(&self.directory, TEMP, AtFlags::empty())
                .map_err(std::io::Error::from)?;
        }
        let mut temporary = open_file(&self.directory, TEMP, self.uid, true)?;
        temporary.write_all(&bytes)?;
        temporary.sync_all()?;
        let metadata = temporary.metadata()?;
        rustix::fs::renameat(&self.directory, TEMP, &self.directory, JOURNAL)
            .map_err(std::io::Error::from)?;
        #[cfg(test)]
        if self.fail_after_rename {
            return Err(std::io::Error::other("injected directory fsync failure").into());
        }
        self.directory.sync_all()?;
        self.journal_identity = Some((metadata.dev(), metadata.ino()));
        self.journal_bytes = Some(bytes);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        fs,
        os::unix::fs::{PermissionsExt, symlink},
    };

    struct Fixture {
        root: tempfile::TempDir,
    }

    impl Fixture {
        fn new() -> Result<Self> {
            let root = tempfile::tempdir()?;
            fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700))?;
            Ok(Self { root })
        }

        fn path(&self) -> std::path::PathBuf {
            self.root.path().join("parent/physical")
        }

        fn open(&self) -> Result<Store> {
            // Only tests bypass /tmp's untrusted ancestors. The fixture root
            // and every traversed child still receive identical validation.
            Store::open_anchored(
                &self.path(),
                self.root.path(),
                rustix::process::getuid().as_raw(),
            )
        }

        fn replace(&self, bytes: &[u8]) -> Result<()> {
            let path = self.path().join(JOURNAL);
            fs::write(&path, bytes)?;
            fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
            Ok(())
        }
    }

    fn record(number: u32) -> Result<Record> {
        Ok(Record {
            lease_id: format!("{number:08x}-1234-5678-9abc-000000000000"),
            owner: Owner {
                uid: 1000,
                pid: 123,
                start_time: 456,
                boot_id: "11111111-2222-3333-4444-555555555555".into(),
            },
            baseline: Baseline {
                device_id: format!("usb-{number:064x}"),
                bus_id: BusId::new("1-2")?,
                receipt: PhysicalDeviceReceipt {
                    sysfs_device: 1,
                    sysfs_inode: 200 + u64::from(number),
                    node_device: rustix::fs::makedev(189, 1),
                    node_inode: 400 + u64::from(number),
                    usb_device_number: 2,
                    bus_number: 1,
                },
                configuration: 1,
                interfaces: vec![OriginalInterface {
                    number: 0,
                    alternate: 1,
                    driver: Some("cdc_ether".into()),
                }],
            },
        })
    }

    fn encoded(records: Vec<Record>) -> Result<Vec<u8>> {
        serde_json::to_vec(&Journal {
            schema_version: SCHEMA_VERSION,
            records,
        })
        .map_err(|error| std::io::Error::other(error).into())
    }

    #[test]
    fn durable_reserve_reload_and_idempotent_retirement() -> Result<()> {
        let fixture = Fixture::new()?;
        let original = record(1)?;
        let mut store = fixture.open()?;
        assert!(store.records()?.is_empty());
        store.reserve(original.clone())?;
        assert_eq!(store.get(&original.lease_id)?, Some(original.clone()));
        assert!(store.has_device(&original.baseline.device_id)?);
        drop(store);
        let mut reopened = fixture.open()?;
        assert_eq!(reopened.records()?, vec![original.clone()]);
        assert!(reopened.retire(&original.lease_id)?);
        assert!(!reopened.retire(&original.lease_id)?);
        drop(reopened);
        assert!(fixture.open()?.records()?.is_empty());
        Ok(())
    }

    #[test]
    fn active_file_loss_truncation_and_same_length_rewrite_quarantine_store() -> Result<()> {
        for mode in 0..3 {
            let fixture = Fixture::new()?;
            let mut store = fixture.open()?;
            store.reserve(record(1)?)?;
            let path = fixture.path().join(JOURNAL);
            match mode {
                0 => fs::remove_file(&path)?,
                1 => fs::write(&path, b"{}")?,
                _ => {
                    let mut bytes = fs::read(&path)?;
                    bytes[0] = b'[';
                    fs::write(&path, bytes)?;
                }
            }
            assert!(store.records().is_err());
            assert!(store.reserve(record(2)?).is_err());
            assert!(store.retire(&record(1)?.lease_id).is_err());
        }
        Ok(())
    }

    #[test]
    fn missing_original_driver_is_not_an_explicit_unbound_baseline() -> Result<()> {
        let mut value =
            serde_json::to_value(record(1)?).map_err(|_| Error::State("fixture encode".into()))?;
        value["baseline"]["interfaces"][0]
            .as_object_mut()
            .ok_or_else(|| Error::State("fixture interface".into()))?
            .remove("driver");
        assert!(serde_json::from_value::<Record>(value).is_err());
        let mut explicit = record(1)?;
        explicit.baseline.interfaces[0].driver = None;
        validate_record(&explicit)?;
        Ok(())
    }

    #[test]
    fn lost_lifetime_lock_cannot_create_a_second_lock_domain() -> Result<()> {
        let fixture = Fixture::new()?;
        let mut original = fixture.open()?;
        original.reserve(record(1)?)?;
        fs::remove_file(fixture.path().join(LOCK))?;
        assert!(fixture.open().is_err());
        assert!(original.records().is_err());
        Ok(())
    }

    #[test]
    fn lifetime_lock_excludes_other_openers() -> Result<()> {
        let fixture = Fixture::new()?;
        let store = fixture.open()?;
        assert!(fixture.open().is_err());
        drop(store);
        assert!(fixture.open().is_ok());
        Ok(())
    }

    #[test]
    fn existing_directory_without_journal_is_never_initialized() -> Result<()> {
        let fixture = Fixture::new()?;
        fs::create_dir_all(fixture.path())?;
        assert!(fixture.open().is_err());
        assert!(!fixture.path().join(JOURNAL).exists());
        Ok(())
    }

    #[test]
    fn duplicate_lease_or_device_does_not_change_durable_table() -> Result<()> {
        let fixture = Fixture::new()?;
        let mut store = fixture.open()?;
        let original = record(1)?;
        store.reserve(original.clone())?;
        let before = fs::read(fixture.path().join(JOURNAL))?;
        let mut duplicate = record(2)?;
        duplicate.lease_id.clone_from(&original.lease_id);
        assert!(store.reserve(duplicate).is_err());
        let mut generation = record(2)?;
        generation
            .baseline
            .device_id
            .clone_from(&original.baseline.device_id);
        assert!(store.reserve(generation).is_err());
        assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
        assert_eq!(store.records()?, vec![original]);
        Ok(())
    }

    #[test]
    fn strict_schema_rejects_corruption_unknown_fields_versions_and_duplicates() -> Result<()> {
        let fixture = Fixture::new()?;
        drop(fixture.open()?);
        let original = record(1)?;
        let mut same_device = record(2)?;
        same_device
            .baseline
            .device_id
            .clone_from(&original.baseline.device_id);
        let mut unknown_record = serde_json::to_value(&original).map_err(std::io::Error::other)?;
        unknown_record["unknown"] = serde_json::json!(true);
        let cases = vec![
            b"{".to_vec(),
            b"{\"schema_version\":2,\"records\":[]}".to_vec(),
            b"{\"schema_version\":1,\"records\":[],\"unknown\":true}".to_vec(),
            b"{\"schema_version\":1}".to_vec(),
            encoded(vec![original.clone(), original.clone()])?,
            encoded(vec![original, same_device])?,
            serde_json::to_vec(&serde_json::json!({"schema_version":1,"records":[unknown_record]}))
                .map_err(std::io::Error::other)?,
        ];
        for bytes in cases {
            fixture.replace(&bytes)?;
            assert!(fixture.open().is_err());
        }
        Ok(())
    }

    #[test]
    fn byte_record_and_interface_limits_fail_closed() -> Result<()> {
        let fixture = Fixture::new()?;
        drop(fixture.open()?);
        let file = File::options()
            .write(true)
            .open(fixture.path().join(JOURNAL))?;
        file.set_len(MAX_BYTES as u64 + 1)?;
        assert!(fixture.open().is_err());
        let records = (0..=MAX_RECORDS)
            .map(|number| record(number as u32))
            .collect::<Result<Vec<_>>>()?;
        fixture.replace(&encoded(records)?)?;
        assert!(fixture.open().is_err());
        let mut excessive = record(1)?;
        excessive.baseline.interfaces = (0..=MAX_INTERFACES)
            .map(|number| OriginalInterface {
                number: number as u8,
                alternate: 0,
                driver: None,
            })
            .collect();
        fixture.replace(&encoded(vec![excessive])?)?;
        assert!(fixture.open().is_err());
        // The actual boundary remains usable; no arbitrary profile narrowing.
        let mut full = record(1)?;
        full.baseline.interfaces = (0..MAX_INTERFACES)
            .map(|number| OriginalInterface {
                number: number as u8,
                alternate: 7,
                driver: None,
            })
            .collect();
        fixture.replace(&encoded(vec![full.clone()])?)?;
        assert_eq!(fixture.open()?.records()?, vec![full]);
        Ok(())
    }

    #[test]
    fn table_capacity_is_enforced_without_poisoning_valid_cache() -> Result<()> {
        let fixture = Fixture::new()?;
        drop(fixture.open()?);
        let records = (0..MAX_RECORDS)
            .map(|number| record(number as u32))
            .collect::<Result<Vec<_>>>()?;
        fixture.replace(&encoded(records)?)?;
        let mut store = fixture.open()?;
        assert!(store.reserve(record(999)?).is_err());
        assert_eq!(store.records()?.len(), MAX_RECORDS);
        Ok(())
    }

    #[test]
    fn invalid_owner_receipt_interface_and_uuid_are_rejected() -> Result<()> {
        let fixture = Fixture::new()?;
        let mut store = fixture.open()?;
        for bad in [
            "",
            "../journal.json",
            "AAAAAAAA-1234-5678-9abc-000000000000",
            "00000000_1234-5678-9abc-000000000000",
        ] {
            assert!(validate_lease_id(bad).is_err());
        }
        let mut cases = Vec::new();
        let mut bad = record(1)?;
        bad.owner.pid = 0;
        cases.push(bad);
        let mut bad = record(1)?;
        bad.owner.boot_id = "unknown".into();
        cases.push(bad);
        let mut bad = record(1)?;
        bad.baseline.receipt.bus_number = 2;
        cases.push(bad);
        let mut bad = record(1)?;
        bad.baseline.receipt.node_device = rustix::fs::makedev(189, 2);
        cases.push(bad);
        let mut bad = record(1)?;
        bad.baseline
            .interfaces
            .push(bad.baseline.interfaces[0].clone());
        cases.push(bad);
        for driver in ["usbfs", "usbip-host", "../driver", "", "\n"] {
            let mut bad = record(1)?;
            bad.baseline.interfaces[0].driver = Some(driver.into());
            cases.push(bad);
        }
        for bad in cases {
            assert!(store.reserve(bad).is_err());
            assert!(store.records()?.is_empty());
        }
        Ok(())
    }

    #[test]
    fn symlinks_hardlinks_and_permissive_files_are_rejected() -> Result<()> {
        for name in [JOURNAL, LOCK] {
            let fixture = Fixture::new()?;
            drop(fixture.open()?);
            let path = fixture.path().join(name);
            fs::set_permissions(&path, fs::Permissions::from_mode(0o644))?;
            assert!(fixture.open().is_err());
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
            let linked = fixture.root.path().join("linked");
            fs::hard_link(&path, &linked)?;
            assert!(fixture.open().is_err());
            fs::remove_file(&linked)?;
            fs::rename(&path, &linked)?;
            symlink(&linked, &path)?;
            assert!(fixture.open().is_err());
        }
        Ok(())
    }

    #[test]
    fn protected_traversal_rejects_symlink_writable_wrong_owner_and_escape() -> Result<()> {
        let fixture = Fixture::new()?;
        fs::create_dir(fixture.root.path().join("real"))?;
        symlink("real", fixture.root.path().join("parent"))?;
        assert!(fixture.open().is_err());
        fs::remove_file(fixture.root.path().join("parent"))?;
        fs::create_dir(fixture.root.path().join("parent"))?;
        fs::set_permissions(
            fixture.root.path().join("parent"),
            fs::Permissions::from_mode(0o777),
        )?;
        assert!(fixture.open().is_err());
        let uid = rustix::process::getuid().as_raw();
        assert!(
            Store::open_anchored(&fixture.path(), fixture.root.path(), uid.wrapping_add(1))
                .is_err()
        );
        let escape = fixture.root.path().join("../escape");
        assert!(Store::open_anchored(&escape, fixture.root.path(), uid).is_err());
        assert!(Store::open(Path::new("relative/path")).is_err());
        Ok(())
    }

    #[test]
    fn secure_stale_temporary_is_removed_only_under_owned_lock() -> Result<()> {
        let fixture = Fixture::new()?;
        let mut store = fixture.open()?;
        let temporary = fixture.path().join(TEMP);
        fs::write(&temporary, b"interrupted transaction")?;
        fs::set_permissions(&temporary, fs::Permissions::from_mode(0o600))?;
        store.reserve(record(1)?)?;
        assert!(!temporary.exists());
        let sentinel = fixture.root.path().join("sentinel");
        fs::write(&sentinel, b"must remain")?;
        symlink(&sentinel, &temporary)?;
        assert!(store.reserve(record(2)?).is_err());
        assert_eq!(fs::read(sentinel)?, b"must remain");
        assert!(temporary.is_symlink());
        assert!(store.records().is_err());
        Ok(())
    }

    #[test]
    fn pre_rename_failure_keeps_old_disk_and_poison_refuses_all_access() -> Result<()> {
        let fixture = Fixture::new()?;
        let mut store = fixture.open()?;
        let original = record(1)?;
        store.reserve(original.clone())?;
        fs::create_dir(fixture.path().join(TEMP))?;
        let before = fs::read(fixture.path().join(JOURNAL))?;
        assert!(store.reserve(record(2)?).is_err());
        assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
        assert_eq!(store.records.len(), 1);
        assert!(store.get(&original.lease_id).is_err());
        assert!(store.has_device(&original.baseline.device_id).is_err());
        assert!(store.records().is_err());
        assert!(store.retire(&original.lease_id).is_err());
        assert!(store.reserve(record(3)?).is_err());
        drop(store);
        assert_eq!(fixture.open()?.records()?, vec![original]);
        Ok(())
    }

    #[test]
    fn uncertain_post_rename_failure_never_installs_new_cache_or_reopens_grants() -> Result<()> {
        let fixture = Fixture::new()?;
        let mut store = fixture.open()?;
        let original = record(1)?;
        store.reserve(original.clone())?;
        store.fail_after_rename = true;
        assert!(store.retire(&original.lease_id).is_err());
        assert_eq!(store.records.get(&original.lease_id), Some(&original));
        assert!(store.records().is_err());
        assert!(store.reserve(record(2)?).is_err());
        drop(store);
        // The injected error models uncertain directory durability. A normal
        // reopen observes this actual file, not the failed writer's old cache;
        // this is not a power-loss or storage durability simulation.
        assert!(fixture.open()?.records()?.is_empty());
        Ok(())
    }
}

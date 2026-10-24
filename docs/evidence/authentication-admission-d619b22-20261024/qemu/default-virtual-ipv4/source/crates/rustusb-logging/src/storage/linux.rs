use crate::{
    AuditAction, Cursor, Error, Event, LogId, LogPage, LogRecord, MAX_PAGE_RECORDS,
    MAX_RECORD_BYTES, MAX_SCAN_BYTES, MAX_SEGMENT_BYTES, MAX_SEGMENTS, Options, ReadFilter, Result,
};
use rustix::fs::{AtFlags, FileType, FlockOperation, Mode, OFlags, RenameFlags};
use serde::{Deserialize, Serialize};
use std::{
    collections::BTreeSet,
    fs::{File, Metadata},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    os::unix::fs::MetadataExt,
    path::{Component, Path, PathBuf},
};

const WRITER: &str = "writer.lock";
const ACCESS: &str = "access.lock";
const HEADER_LIMIT: usize = 256;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Identity(u64, u64);
impl Identity {
    fn of(meta: &Metadata) -> Self {
        Self(meta.dev(), meta.ino())
    }
}

/// A reader holds only metadata; every read reopens and verifies the directory.
#[derive(Clone)]
pub(crate) struct Source {
    path: PathBuf,
    uid: u32,
    directory: Identity,
    access: Identity,
    #[cfg(test)]
    anchor: Option<PathBuf>,
}

struct Directory {
    file: File,
    path: PathBuf,
    uid: u32,
    identity: Identity,
    #[cfg(test)]
    anchor: Option<PathBuf>,
}
impl Directory {
    fn open(path: &Path, writer: bool) -> Result<Self> {
        Self::open_from(path, writer, Path::new("/"), None)
    }

    fn open_from(
        path: &Path,
        writer: bool,
        anchor: &Path,
        fixture_uid: Option<u32>,
    ) -> Result<Self> {
        if !path.is_absolute()
            || path.as_os_str().len() > 4096
            || path
                .components()
                .any(|c| matches!(c, Component::ParentDir | Component::CurDir))
        {
            return Err(Error::InvalidInput("log directory path"));
        }
        let parts: Vec<_> = path
            .strip_prefix(anchor)
            .map_err(|_| Error::Permission("log directory trust anchor"))?
            .components()
            .collect();
        if parts.is_empty() {
            return Err(Error::InvalidInput("private log leaf required"));
        }
        let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
        let mut file = File::from(rustix::fs::open(anchor, flags, Mode::empty()).map_err(io)?);
        let mut ancestor_uids = Vec::with_capacity(parts.len());
        for part in parts {
            let before = file.metadata()?;
            if !before.is_dir() || before.mode() & 0o022 != 0 {
                return Err(Error::Permission("unsafe log ancestor"));
            }
            ancestor_uids.push(before.uid());
            let Component::Normal(name) = part else {
                return Err(Error::InvalidInput("log directory component"));
            };
            file = File::from(rustix::fs::openat(&file, name, flags, Mode::empty()).map_err(io)?);
        }
        let meta = file.metadata()?;
        let caller = rustix::process::geteuid().as_raw();
        let uid = meta.uid();
        if !meta.is_dir()
            || meta.mode() & 0o7777 != 0o700
            || (writer && caller != uid)
            || (!writer && caller != 0 && caller != uid)
            || ancestor_uids
                .iter()
                .any(|v| *v != 0 && *v != uid && Some(*v) != fixture_uid)
        {
            return Err(Error::Permission("log directory owner or mode"));
        }
        Ok(Self {
            file,
            path: path.to_owned(),
            uid,
            identity: Identity::of(&meta),
            #[cfg(test)]
            anchor: fixture_uid.map(|_| anchor.to_owned()),
        })
    }

    fn reopen(&self) -> Result<Self> {
        #[cfg(test)]
        if let Some(anchor) = &self.anchor {
            return Self::open_from(&self.path, false, anchor, Some(self.uid));
        }
        Self::open(&self.path, false)
    }

    fn verify(&self) -> Result<()> {
        let current = self.reopen()?;
        if current.identity != self.identity || current.uid != self.uid {
            return Err(Error::Permission("log directory identity changed"));
        }
        Ok(())
    }

    fn identity(&self, name: &str) -> Result<Option<Identity>> {
        let stat = match rustix::fs::statat(&self.file, name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(stat) => stat,
            Err(rustix::io::Errno::NOENT) => return Ok(None),
            Err(error) => return Err(io(error).into()),
        };
        if FileType::from_raw_mode(stat.st_mode) != FileType::RegularFile
            || stat.st_uid != self.uid
            || stat.st_mode & 0o7777 != 0o600
            || stat.st_nlink != 1
        {
            return Err(Error::Permission(
                "unsafe log file type, owner, mode or links",
            ));
        }
        Ok(Some(Identity(stat.st_dev, stat.st_ino)))
    }

    fn open_file(&self, name: &str, create: bool, writable: bool) -> Result<File> {
        self.verify()?;
        let expected = self.identity(name)?;
        let mut flags = OFlags::NOFOLLOW | OFlags::CLOEXEC | OFlags::NONBLOCK;
        flags |= if writable {
            OFlags::RDWR
        } else {
            OFlags::RDONLY
        };
        if create {
            flags |= OFlags::CREATE | OFlags::EXCL;
        }
        let file = File::from(
            rustix::fs::openat(&self.file, name, flags, Mode::RUSR | Mode::WUSR).map_err(io)?,
        );
        if create {
            rustix::fs::fchmod(&file, Mode::RUSR | Mode::WUSR).map_err(io)?;
        }
        let actual = Identity::of(&file.metadata()?);
        if (!create && expected != Some(actual)) || self.identity(name)? != Some(actual) {
            return Err(Error::Permission("log file identity changed during open"));
        }
        Ok(file)
    }

    fn names(&self) -> Result<BTreeSet<String>> {
        self.verify()?;
        let mut entries = rustix::fs::Dir::read_from(&self.file).map_err(io)?;
        let mut names = BTreeSet::new();
        while let Some(entry) = entries.read() {
            let entry = entry.map_err(io)?;
            let bytes = entry.file_name().to_bytes();
            if bytes == b"." || bytes == b".." {
                continue;
            }
            let name = std::str::from_utf8(bytes)
                .map_err(|_| Error::Permission("unknown log directory entry"))?;
            if name != WRITER && name != ACCESS && slot_number(name).is_none() {
                return Err(Error::Permission("unknown log directory entry"));
            }
            if names.len() >= usize::from(MAX_SEGMENTS) + 2 || !names.insert(name.to_owned()) {
                return Err(Error::Corrupt("duplicate or excessive log entries"));
            }
            self.identity(name)?;
        }
        self.verify()?;
        Ok(names)
    }

    fn access(&self, expected: Identity, exclusive: bool) -> Result<File> {
        // Fresh open, deliberately not File::try_clone: flock belongs to the OFD.
        if self.identity(ACCESS)?.is_none() {
            return Err(Error::Corrupt("log history lacks access lock"));
        }
        let lock = self.open_file(ACCESS, false, false)?;
        if Identity::of(&lock.metadata()?) != expected || lock.metadata()?.len() != 0 {
            return Err(Error::Permission("log access lock identity changed"));
        }
        rustix::fs::flock(
            &lock,
            if exclusive {
                FlockOperation::LockExclusive
            } else {
                FlockOperation::LockShared
            },
        )
        .map_err(io)?;
        self.verify()?;
        if self.identity(ACCESS)? != Some(expected) {
            return Err(Error::Permission("log access lock replaced"));
        }
        Ok(lock)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Header {
    schema_version: u32,
    segment: LogId,
    epoch: LogId,
    ordinal: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Stamp {
    identity: Identity,
    bytes: u64,
    mtime: (i64, i64),
    ctime: (i64, i64),
}
impl Stamp {
    fn of(meta: &Metadata) -> Self {
        Self {
            identity: Identity::of(meta),
            bytes: meta.len(),
            mtime: (meta.mtime(), meta.mtime_nsec()),
            ctime: (meta.ctime(), meta.ctime_nsec()),
        }
    }
}
#[derive(Debug, Clone, PartialEq, Eq)]
struct Segment {
    slot: u8,
    header: Header,
    header_bytes: u64,
    stamp: Stamp,
}
impl Segment {
    fn cursor(&self, offset: u64) -> Result<Cursor> {
        Cursor::new(self.header.segment.clone(), offset)
    }
}

fn slot_name(slot: u8) -> String {
    format!("segment-{slot:02}.ndjson")
}
fn slot_number(name: &str) -> Option<u8> {
    (0..MAX_SEGMENTS).find(|slot| slot_name(*slot) == name)
}
fn io(error: rustix::io::Errno) -> std::io::Error {
    error.into()
}

fn bounded_line(reader: &mut BufReader<File>, limit: usize) -> Result<Vec<u8>> {
    let mut line = Vec::new();
    Read::take(reader, (limit + 1) as u64).read_until(b'\n', &mut line)?;
    if line.len() > limit {
        return Err(Error::Corrupt("log line exceeds bound"));
    }
    Ok(line)
}

fn inventory(directory: &Directory) -> Result<Vec<Segment>> {
    let names = directory.names()?;
    for lock in [WRITER, ACCESS] {
        if names.contains(lock) && directory.open_file(lock, false, false)?.metadata()?.len() != 0 {
            return Err(Error::Corrupt("log lock is not empty"));
        }
    }
    let mut segments = Vec::new();
    let mut identities = BTreeSet::new();
    let mut ordinals = BTreeSet::new();
    for slot in 0..MAX_SEGMENTS {
        let name = slot_name(slot);
        if !names.contains(&name) {
            continue;
        }
        if usize::from(slot) != segments.len() {
            return Err(Error::Corrupt("noncontiguous log slots"));
        }
        let file = directory.open_file(&name, false, false)?;
        let stamp = Stamp::of(&file.metadata()?);
        if stamp.bytes > MAX_SEGMENT_BYTES {
            return Err(Error::Corrupt("segment exceeds hard bound"));
        }
        let mut reader = BufReader::new(file);
        let line = bounded_line(&mut reader, HEADER_LIMIT)?;
        if !line.ends_with(b"\n") {
            return Err(Error::Corrupt("incomplete log header"));
        }
        let header: Header =
            serde_json::from_slice(&line).map_err(|_| Error::Corrupt("malformed log header"))?;
        if header.schema_version != 1
            || header.ordinal == 0
            || !identities.insert(header.segment.clone())
            || !ordinals.insert(header.ordinal)
            || segments
                .last()
                .is_some_and(|previous: &Segment| previous.header.ordinal <= header.ordinal)
        {
            return Err(Error::Corrupt("ambiguous log header ordering"));
        }
        if Stamp::of(&reader.get_ref().metadata()?) != stamp
            || directory.identity(&name)? != Some(stamp.identity)
        {
            return Err(Error::Permission("log header changed during read"));
        }
        segments.push(Segment {
            slot,
            header,
            header_bytes: line.len() as u64,
            stamp,
        });
    }
    if !segments.is_empty() && (!names.contains(ACCESS) || !names.contains(WRITER)) {
        return Err(Error::Corrupt("log history lacks its locks"));
    }
    Ok(segments)
}

fn decoded(line: &[u8], header: &Header) -> Result<LogRecord> {
    let record: LogRecord = serde_json::from_slice(line)
        .map_err(|_| Error::Corrupt("malformed complete log record"))?;
    record
        .validate()
        .map_err(|_| Error::Corrupt("invalid complete log record"))?;
    if record.epoch != header.epoch {
        return Err(Error::Corrupt("record epoch differs from segment"));
    }
    Ok(record)
}

fn validate_history(directory: &Directory, segments: &[Segment]) -> Result<()> {
    let mut prior: Option<(LogId, u64)> = None;
    let mut closed_epochs = BTreeSet::new();
    let mut prior_epoch: Option<LogId> = None;
    for segment in segments.iter().rev() {
        if prior_epoch
            .as_ref()
            .is_some_and(|epoch| epoch != &segment.header.epoch)
        {
            if let Some(epoch) = prior_epoch.take() {
                closed_epochs.insert(epoch);
            }
            if closed_epochs.contains(&segment.header.epoch) {
                return Err(Error::Corrupt("process epoch is interleaved"));
            }
        }
        prior_epoch = Some(segment.header.epoch.clone());
        let mut file = directory.open_file(&slot_name(segment.slot), false, false)?;
        file.seek(SeekFrom::Start(segment.header_bytes))?;
        let mut reader = BufReader::new(file);
        loop {
            let line = bounded_line(&mut reader, MAX_RECORD_BYTES)?;
            if !line.ends_with(b"\n") && line.len() == MAX_RECORD_BYTES {
                return Err(Error::Corrupt(
                    "incomplete record exceeds permitted payload",
                ));
            }
            if line.is_empty() || !line.ends_with(b"\n") {
                break; // A bounded final incomplete line is preserved, never accepted.
            }
            let record = decoded(&line, &segment.header)?;
            if prior.as_ref().is_some_and(|(epoch, sequence)| {
                epoch == &record.epoch && record.sequence <= *sequence
            }) {
                return Err(Error::Corrupt("record sequence is not increasing"));
            }
            prior = Some((record.epoch.clone(), record.sequence));
        }
        if Stamp::of(&reader.get_ref().metadata()?) != segment.stamp {
            return Err(Error::Permission("log segment changed during validation"));
        }
    }
    Ok(())
}

pub(crate) struct Storage {
    directory: Directory,
    writer: File,
    writer_identity: Identity,
    access_identity: Identity,
    options: Options,
    epoch: LogId,
    segments: Vec<Segment>,
    active: Option<File>,
    sequence: u64,
    poisoned: bool,
}
impl Storage {
    pub(crate) fn open(options: &Options, epoch: &LogId) -> Result<Self> {
        options.validate()?;
        Self::open_directory(options, epoch, Directory::open(&options.directory, true)?)
    }
    #[cfg(test)]
    pub(crate) fn fixture(options: &Options, epoch: &LogId, anchor: &Path) -> Result<Self> {
        options.validate()?;
        let directory = Directory::open_from(
            &options.directory,
            true,
            anchor,
            Some(rustix::process::geteuid().as_raw()),
        )?;
        Self::open_directory(options, epoch, directory)
    }
    fn open_directory(options: &Options, epoch: &LogId, directory: Directory) -> Result<Self> {
        let names = directory.names()?;
        let history = names.iter().any(|name| slot_number(name).is_some());
        if history && (!names.contains(WRITER) || !names.contains(ACCESS)) {
            return Err(Error::Corrupt("log history lacks its locks"));
        }
        let writer = match directory.open_file(WRITER, !names.contains(WRITER), true) {
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                directory.open_file(WRITER, false, true)?
            }
            result => result?,
        };
        let writer_identity = Identity::of(&writer.metadata()?);
        if writer.metadata()?.len() != 0 {
            return Err(Error::Corrupt("writer lock is not empty"));
        }
        rustix::fs::flock(&writer, FlockOperation::NonBlockingLockExclusive).map_err(|error| {
            if error == rustix::io::Errno::WOULDBLOCK {
                Error::Busy
            } else {
                io(error).into()
            }
        })?;
        if directory.identity(WRITER)? != Some(writer_identity) {
            return Err(Error::Permission("writer lock replaced"));
        }
        let access = directory.open_file(ACCESS, directory.identity(ACCESS)?.is_none(), true)?;
        if access.metadata()?.len() != 0 {
            return Err(Error::Corrupt("access lock is not empty"));
        }
        let access_identity = Identity::of(&access.metadata()?);
        access.sync_all()?;
        writer.sync_all()?;
        directory.file.sync_all()?;
        let _access = directory.access(access_identity, true)?;
        let segments = inventory(&directory)?;
        validate_history(&directory, &segments)?;
        if segments
            .iter()
            .any(|segment| segment.stamp.bytes > options.segment_bytes)
        {
            return Err(Error::InvalidInput(
                "retained history exceeds configured segment bound",
            ));
        }
        if segments
            .iter()
            .any(|segment| &segment.header.epoch == epoch)
        {
            return Err(Error::InvalidInput("process epoch must be fresh"));
        }
        let mut store = Self {
            directory,
            writer,
            writer_identity,
            access_identity,
            options: options.clone(),
            epoch: epoch.clone(),
            segments,
            active: None,
            sequence: 0,
            poisoned: false,
        };
        store.rotate()?;
        Ok(store)
    }

    pub(crate) fn source(&self) -> Source {
        Source {
            path: self.directory.path.clone(),
            uid: self.directory.uid,
            directory: self.directory.identity,
            access: self.access_identity,
            #[cfg(test)]
            anchor: self.directory.anchor.clone(),
        }
    }

    fn verify(&self) -> Result<()> {
        self.directory.verify()?;
        if self.directory.identity(WRITER)? != Some(self.writer_identity)
            || Identity::of(&self.writer.metadata()?) != self.writer_identity
            || self.writer.metadata()?.len() != 0
            || inventory(&self.directory)? != self.segments
        {
            return Err(Error::Permission(
                "log storage identity or content metadata changed",
            ));
        }
        Ok(())
    }

    fn rotate(&mut self) -> Result<()> {
        self.verify()?;
        validate_history(&self.directory, &self.segments)?;
        let ordinal = self
            .segments
            .first()
            .map_or(Some(1), |s| s.header.ordinal.checked_add(1))
            .ok_or(Error::Corrupt("segment ordinal exhausted"))?;
        if let Some(active) = self.active.take() {
            active.sync_all()?;
        }
        while self.segments.len() >= usize::from(self.options.retained_segments) {
            self.verify()?;
            let segment = self
                .segments
                .last()
                .ok_or(Error::Corrupt("missing retained segment"))?;
            rustix::fs::unlinkat(
                &self.directory.file,
                slot_name(segment.slot),
                AtFlags::empty(),
            )
            .map_err(io)?;
            self.segments.pop();
        }
        for segment in self.segments.iter().rev() {
            self.directory.verify()?;
            if self.directory.identity(&slot_name(segment.slot))? != Some(segment.stamp.identity) {
                return Err(Error::Permission("segment changed before rotation"));
            }
            rustix::fs::renameat_with(
                &self.directory.file,
                slot_name(segment.slot),
                &self.directory.file,
                slot_name(segment.slot + 1),
                RenameFlags::NOREPLACE,
            )
            .map_err(io)?;
            if self.directory.identity(&slot_name(segment.slot + 1))?
                != Some(segment.stamp.identity)
            {
                return Err(Error::Permission("segment changed during rotation"));
            }
        }
        let header = Header {
            schema_version: 1,
            segment: LogId::fresh()?,
            epoch: self.epoch.clone(),
            ordinal,
        };
        let mut bytes =
            serde_json::to_vec(&header).map_err(|_| Error::Corrupt("header encoding"))?;
        bytes.push(b'\n');
        if bytes.len() > HEADER_LIMIT {
            return Err(Error::Corrupt("header exceeds bound"));
        }
        let mut active = self.directory.open_file(&slot_name(0), true, true)?;
        active.write_all(&bytes)?;
        active.sync_all()?;
        self.directory.file.sync_all()?;
        self.segments = inventory(&self.directory)?;
        self.active = Some(active);
        Ok(())
    }

    pub(crate) fn append(&mut self, record: &LogRecord) -> Result<Cursor> {
        if self.poisoned {
            return Err(Error::Worker("log storage is unhealthy"));
        }
        let bytes = record.encode_line()?;
        if record.epoch != self.epoch || record.sequence <= self.sequence {
            return Err(Error::InvalidInput("record epoch or sequence"));
        }
        let result = self.append_line(record, &bytes);
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }
    fn append_line(&mut self, record: &LogRecord, bytes: &[u8]) -> Result<Cursor> {
        let _access = self.directory.access(self.access_identity, true)?;
        self.verify()?;
        let size = self
            .segments
            .first()
            .ok_or(Error::Corrupt("active segment missing"))?
            .stamp
            .bytes;
        if size
            .checked_add(bytes.len() as u64)
            .is_none_or(|size| size > self.options.segment_bytes)
        {
            self.rotate()?;
        }
        let active = self
            .active
            .as_mut()
            .ok_or(Error::Corrupt("active file missing"))?;
        active.write_all(bytes)?;
        active.sync_all()?;
        let stamp = Stamp::of(&active.metadata()?);
        if self.directory.identity(&slot_name(0))? != Some(stamp.identity) {
            return Err(Error::Permission("active segment replaced during append"));
        }
        let segment = self
            .segments
            .first_mut()
            .ok_or(Error::Corrupt("active segment missing"))?;
        segment.stamp = stamp;
        self.sequence = record.sequence;
        segment.cursor(segment.stamp.bytes)
    }
    pub(crate) fn sync(&mut self) -> Result<()> {
        if self.poisoned {
            return Err(Error::Worker("log storage is unhealthy"));
        }
        let result = (|| {
            let _access = self.directory.access(self.access_identity, true)?;
            self.verify()?;
            self.active
                .as_ref()
                .ok_or(Error::Corrupt("active file missing"))?
                .sync_all()?;
            self.directory.file.sync_all()?;
            Ok(())
        })();
        if result.is_err() {
            self.poisoned = true;
        }
        result
    }
}

mod reader;
pub use reader::read_directory;
pub(crate) use reader::read_source;

#[cfg(test)]
mod tests;

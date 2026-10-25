//! Checked user preferences. These files carry no device or service authority.
use crate::Options;
use rustix::fs::{AtFlags, FileType, FlockOperation, Mode, OFlags};
use rustusb_core::{Error, Result};
use serde::{Deserialize, Serialize};
use std::{
    ffi::OsString,
    fs::{File, Metadata},
    io::{Read, Write},
    os::unix::fs::MetadataExt,
    path::{Component, Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

const LIMIT: u64 = 4096;
const NAME: &str = "preferences.json";
static TEMPORARIES: AtomicU64 = AtomicU64::new(0);

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    schema_version: u32,
    options: Options,
}

/// A nonowning location and the last successfully loaded/saved frontend options.
/// No file descriptor, credential, USB handle or authorization is retained.
#[derive(Debug, Clone)]
pub struct Settings {
    directory: PathBuf,
    saved: Option<Options>,
}
impl Settings {
    /// Resolve a user config directory without reading or changing any files.
    /// Relative/empty XDG paths are ignored; an absolute HOME is the fallback.
    pub fn default_directory() -> Option<PathBuf> {
        directory_from(
            std::env::var_os("XDG_CONFIG_HOME"),
            std::env::var_os("HOME"),
        )
    }
    /// Read a bounded strict document, or defaults when the directory/file is absent.
    /// This blocking operation never creates a directory; callers use an owned worker.
    pub fn load(directory: PathBuf) -> Result<Self> {
        validate_path(&directory)?;
        let saved = match Directory::open(&directory, false)? {
            Some(dir) => dir.read_document()?.map(|entry| entry.options),
            None => None,
        };
        Ok(Self { directory, saved })
    }
    /// Last checked options, or defaults when no saved document exists.
    pub fn options(&self) -> Options {
        self.saved.unwrap_or_default()
    }
    /// Whether a complete document was actually loaded or successfully saved.
    pub fn has_saved(&self) -> bool {
        self.saved.is_some()
    }
    /// The selected directory, for local operator display only.
    pub fn directory(&self) -> &Path {
        &self.directory
    }
    /// Last successfully checked preferences, without loading files.
    pub fn saved(&self) -> Option<Options> {
        self.saved
    }
    pub(crate) fn save(&mut self, options: Options) -> Result<()> {
        self.save_with(options, || Ok(()))
    }
    fn save_with(
        &mut self,
        options: Options,
        before_commit: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        validate_options(options)?;
        let dir = Directory::open(&self.directory, true)?
            .ok_or_else(|| Error::State("preferences directory missing after create".into()))?;
        let _lock = dir.lock()?;
        let previous = dir.read_document()?;
        let bytes = serde_json::to_vec_pretty(&Document {
            schema_version: 1,
            options,
        })
        .map_err(|_| Error::State("preferences encoding failed".into()))?;
        if bytes.len() as u64 > LIMIT {
            return Err(Error::InvalidInput("preferences byte limit".into()));
        }
        dir.replace_bytes(
            NAME,
            LIMIT,
            &bytes,
            previous.as_ref().map(|e| &e.checked),
            before_commit,
        )?;
        self.saved = Some(options);
        Ok(())
    }
}
fn directory_from(xdg: Option<OsString>, home: Option<OsString>) -> Option<PathBuf> {
    if let Some(path) = xdg.map(PathBuf::from).filter(|p| p.is_absolute()) {
        return Some(path.join("rustusb"));
    }
    home.map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .map(|p| p.join(".config/rustusb"))
}
fn validate_options(options: Options) -> Result<()> {
    if !(2..=120).contains(&options.refresh_seconds) {
        return Err(Error::InvalidInput(
            "preferences refresh interval must be 2..120".into(),
        ));
    }
    Ok(())
}
fn validate_path(path: &Path) -> Result<()> {
    if !path.is_absolute()
        || path.as_os_str().len() > 4096
        || path.components().count() < 2
        || path
            .components()
            .any(|p| !matches!(p, Component::RootDir | Component::Normal(_)))
    {
        return Err(Error::InvalidInput(
            "absolute bounded private user directory required".into(),
        ));
    }
    if rustix::process::geteuid().is_root() {
        return Err(Error::Permission(
            "private user state must not be loaded or saved as root".into(),
        ));
    }
    Ok(())
}
fn id(metadata: &Metadata) -> (u64, u64) {
    (metadata.dev(), metadata.ino())
}
fn stamp(metadata: &Metadata) -> (u64, u64, u64, i64, i64, i64, i64) {
    (
        metadata.dev(),
        metadata.ino(),
        metadata.len(),
        metadata.mtime(),
        metadata.mtime_nsec(),
        metadata.ctime(),
        metadata.ctime_nsec(),
    )
}
struct Entry {
    options: Options,
    checked: CheckedBytes,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct CheckedBytes {
    pub(crate) bytes: Vec<u8>,
    identity: (u64, u64),
}
pub(crate) struct Directory {
    file: File,
    path: PathBuf,
    uid: u32,
}
impl Directory {
    pub(crate) fn open(path: &Path, create: bool) -> Result<Option<Self>> {
        validate_path(path)?;
        let uid = rustix::process::geteuid().as_raw();
        let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
        let mut file =
            File::from(rustix::fs::open("/", flags, Mode::empty()).map_err(std::io::Error::from)?);
        for part in path.components().skip(1) {
            let metadata = file.metadata()?;
            let protected =
                (metadata.uid() == 0 || metadata.uid() == uid) && metadata.mode() & 0o022 == 0;
            let temporary = metadata.uid() == 0 && metadata.mode() & 0o1777 == 0o1777;
            if !metadata.is_dir() || !(protected || temporary) {
                return Err(Error::Permission("private user ancestor owner/mode".into()));
            }
            let Component::Normal(name) = part else {
                return Err(Error::InvalidInput("private user path component".into()));
            };
            let fd = match rustix::fs::openat(&file, name, flags, Mode::empty()) {
                Ok(fd) => fd,
                Err(rustix::io::Errno::NOENT) if !create => return Ok(None),
                Err(rustix::io::Errno::NOENT) => {
                    match rustix::fs::mkdirat(&file, name, Mode::RWXU) {
                        Ok(()) | Err(rustix::io::Errno::EXIST) => {}
                        Err(e) => return Err(std::io::Error::from(e).into()),
                    }
                    rustix::fs::openat(&file, name, flags, Mode::empty())
                        .map_err(std::io::Error::from)?
                }
                Err(e) => return Err(std::io::Error::from(e).into()),
            };
            file = File::from(fd);
        }
        let metadata = file.metadata()?;
        if metadata.uid() != uid || metadata.mode() & 0o7777 != 0o700 {
            return Err(Error::Permission(
                "private user directory must be caller-owned mode 0700".into(),
            ));
        }
        Ok(Some(Self {
            file,
            path: path.into(),
            uid,
        }))
    }
    pub(crate) fn verify(&self) -> Result<()> {
        let current = Self::open(&self.path, false)?
            .ok_or_else(|| Error::State("private user directory disappeared".into()))?;
        if id(&current.file.metadata()?) != id(&self.file.metadata()?) {
            return Err(Error::State(
                "private user directory identity changed".into(),
            ));
        }
        Ok(())
    }
    pub(crate) fn identity(&self, name: &str) -> Result<Option<(u64, u64)>> {
        let stat = match rustix::fs::statat(&self.file, name, AtFlags::SYMLINK_NOFOLLOW) {
            Ok(s) => s,
            Err(rustix::io::Errno::NOENT) => return Ok(None),
            Err(e) => return Err(std::io::Error::from(e).into()),
        };
        if FileType::from_raw_mode(stat.st_mode) != FileType::RegularFile
            || stat.st_uid != self.uid
            || stat.st_mode & 0o7777 != 0o600
            || stat.st_nlink != 1
        {
            return Err(Error::Permission(
                "private user file must be caller-owned 0600 regular/single-link".into(),
            ));
        }
        Ok(Some((stat.st_dev, stat.st_ino)))
    }
    pub(crate) fn open_file(&self, name: &str, create: bool) -> Result<File> {
        self.verify()?;
        let before = self.identity(name)?;
        let access = if create || name.ends_with(".lock") {
            OFlags::RDWR
        } else {
            OFlags::RDONLY
        };
        let flags = access | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC;
        let flags = if create {
            flags | OFlags::CREATE | OFlags::EXCL
        } else {
            flags
        };
        let file = File::from(
            rustix::fs::openat(&self.file, name, flags, Mode::RUSR | Mode::WUSR)
                .map_err(std::io::Error::from)?,
        );
        if create {
            rustix::fs::fchmod(&file, Mode::RUSR | Mode::WUSR).map_err(std::io::Error::from)?;
        }
        let identity = Some(id(&file.metadata()?));
        if (!create && before != identity) || self.identity(name)? != identity {
            return Err(Error::State("private user file identity changed".into()));
        }
        Ok(file)
    }
    fn lock(&self) -> Result<File> {
        self.lock_named("preferences.lock")
    }
    pub(crate) fn lock_named(&self, name: &str) -> Result<File> {
        let file = match self.open_file(name, true) {
            Ok(file) => file,
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                self.open_file(name, false)?
            }
            Err(e) => return Err(e),
        };
        if file.metadata()?.len() != 0 {
            return Err(Error::Permission("private lock must be empty".into()));
        }
        rustix::fs::flock(&file, FlockOperation::NonBlockingLockExclusive)
            .map_err(|_| Error::State("private state writer is busy/unavailable".into()))?;
        self.check_lock(name, &file)?;
        Ok(file)
    }
    pub(crate) fn check_lock(&self, name: &str, file: &File) -> Result<()> {
        self.verify()?;
        if file.metadata()?.len() != 0 || self.identity(name)? != Some(id(&file.metadata()?)) {
            return Err(Error::State("private lock identity changed".into()));
        }
        Ok(())
    }
    pub(crate) fn sync(&self) -> Result<()> {
        self.file.sync_all()?;
        self.verify()
    }
    fn read_document(&self) -> Result<Option<Entry>> {
        let Some(checked) = self.read_bytes(NAME, LIMIT)? else {
            return Ok(None);
        };
        let document: Document = serde_json::from_slice(&checked.bytes)
            .map_err(|_| Error::InvalidInput("preferences schema".into()))?;
        if document.schema_version != 1 {
            return Err(Error::InvalidInput("preferences schema version".into()));
        }
        validate_options(document.options)?;
        Ok(Some(Entry {
            options: document.options,
            checked,
        }))
    }
    pub(crate) fn read_bytes(&self, name: &str, limit: u64) -> Result<Option<CheckedBytes>> {
        self.verify()?;
        if self.identity(name)?.is_none() {
            return Ok(None);
        }
        let mut file = self.open_file(name, false)?;
        let before = file.metadata()?;
        if before.len() == 0 || before.len() > limit {
            return Err(Error::InvalidInput("private document byte limit".into()));
        }
        let mut bytes = Vec::with_capacity(before.len() as usize);
        (&mut file).take(limit + 1).read_to_end(&mut bytes)?;
        let after = file.metadata()?;
        if bytes.len() as u64 != before.len()
            || stamp(&before) != stamp(&after)
            || self.identity(name)? != Some(id(&after))
        {
            return Err(Error::State("private document changed during read".into()));
        }
        self.verify()?;
        Ok(Some(CheckedBytes {
            bytes,
            identity: id(&after),
        }))
    }
    pub(crate) fn replace_bytes(
        &self,
        name: &str,
        limit: u64,
        bytes: &[u8],
        previous: Option<&CheckedBytes>,
        before_commit: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        if bytes.is_empty() || bytes.len() as u64 > limit {
            return Err(Error::InvalidInput("private document byte limit".into()));
        }
        self.verify()?;
        if self.read_bytes(name, limit)?.as_ref() != previous {
            return Err(Error::State("private document changed before write".into()));
        }
        let sequence = TEMPORARIES
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| Error::State("private temporary sequence exhausted".into()))?;
        let temporary = format!(".{name}-{}-{sequence}.tmp", std::process::id());
        let mut file = self.open_file(&temporary, true)?;
        let identity = id(&file.metadata()?);
        let mut published = false;
        let result = (|| -> Result<()> {
            file.write_all(bytes)?;
            file.sync_all()?;
            before_commit()?;
            self.verify()?;
            if self.read_bytes(name, limit)?.as_ref() != previous {
                return Err(Error::State(
                    "private document changed before publication; previous file preserved".into(),
                ));
            }
            if self.identity(&temporary)? != Some(identity) {
                return Err(Error::State("private temporary identity changed".into()));
            }
            rustix::fs::renameat(&self.file, &temporary, &self.file, name)
                .map_err(std::io::Error::from)?;
            published = true;
            self.sync()?;
            if self.identity(name)? != Some(identity) {
                return Err(Error::State("private published identity changed".into()));
            }
            Ok(())
        })();
        if let Err(error) = result {
            if published {
                return Err(Error::State(format!(
                    "private publication uncertain after rename: {error}"
                )));
            }
            let cleanup = (|| -> Result<()> {
                if self.identity(&temporary)? == Some(identity) {
                    rustix::fs::unlinkat(&self.file, &temporary, AtFlags::empty())
                        .map_err(std::io::Error::from)?;
                }
                Ok(())
            })();
            return match cleanup {
                Ok(()) => Err(error),
                Err(cleanup) => Err(Error::State(format!(
                    "private save failed: {error}; temporary cleanup failed: {cleanup}"
                ))),
            };
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests;

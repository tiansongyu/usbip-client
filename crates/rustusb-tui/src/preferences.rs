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
    pub(crate) fn saved(&self) -> Option<Options> {
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
        let sequence = TEMPORARIES
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| Error::State("preferences temporary sequence exhausted".into()))?;
        let temporary = format!(".preferences-{}-{sequence}.tmp", std::process::id());
        let mut file = dir.open_file(&temporary, true)?;
        let identity = id(&file.metadata()?);
        let mut published = false;
        let result = (|| -> Result<()> {
            file.write_all(&bytes)?;
            file.sync_all()?;
            before_commit()?;
            dir.verify()?;
            let current = dir.read_document()?;
            if current.as_ref().map(|e| (&e.identity, &e.bytes))
                != previous.as_ref().map(|e| (&e.identity, &e.bytes))
            {
                return Err(Error::State(
                    "preferences changed before publication; previous file preserved".into(),
                ));
            }
            if dir.identity(&temporary)? != Some(identity) {
                return Err(Error::State(
                    "preferences temporary identity changed".into(),
                ));
            }
            rustix::fs::renameat(&dir.file, &temporary, &dir.file, NAME)
                .map_err(std::io::Error::from)?;
            published = true;
            dir.file.sync_all()?;
            dir.verify()?;
            if dir.identity(NAME)? != Some(identity) {
                return Err(Error::State(
                    "preferences published identity changed".into(),
                ));
            }
            Ok(())
        })();
        if let Err(error) = result {
            if published {
                return Err(Error::State(format!(
                    "preferences publication uncertain after rename: {error}"
                )));
            }
            let cleanup = (|| -> Result<()> {
                if dir.identity(&temporary)? == Some(identity) {
                    rustix::fs::unlinkat(&dir.file, &temporary, AtFlags::empty())
                        .map_err(std::io::Error::from)?;
                }
                Ok(())
            })();
            return match cleanup {
                Ok(()) => Err(error),
                Err(cleanup) => Err(Error::State(format!(
                    "preferences save failed: {error}; temporary cleanup failed: {cleanup}"
                ))),
            };
        }
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
            "absolute bounded preferences directory required".into(),
        ));
    }
    if rustix::process::geteuid().is_root() {
        return Err(Error::Permission(
            "user preferences must not be loaded or saved as root".into(),
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
    bytes: Vec<u8>,
    identity: (u64, u64),
}
struct Directory {
    file: File,
    path: PathBuf,
    uid: u32,
}
impl Directory {
    fn open(path: &Path, create: bool) -> Result<Option<Self>> {
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
                return Err(Error::Permission("preferences ancestor owner/mode".into()));
            }
            let Component::Normal(name) = part else {
                return Err(Error::InvalidInput("preferences path component".into()));
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
                "preferences directory must be caller-owned mode 0700".into(),
            ));
        }
        Ok(Some(Self {
            file,
            path: path.into(),
            uid,
        }))
    }
    fn verify(&self) -> Result<()> {
        let current = Self::open(&self.path, false)?
            .ok_or_else(|| Error::State("preferences directory disappeared".into()))?;
        if id(&current.file.metadata()?) != id(&self.file.metadata()?) {
            return Err(Error::State(
                "preferences directory identity changed".into(),
            ));
        }
        Ok(())
    }
    fn identity(&self, name: &str) -> Result<Option<(u64, u64)>> {
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
                "preferences file must be caller-owned 0600 regular/single-link".into(),
            ));
        }
        Ok(Some((stat.st_dev, stat.st_ino)))
    }
    fn open_file(&self, name: &str, create: bool) -> Result<File> {
        self.verify()?;
        let before = self.identity(name)?;
        let access = if create || name == "preferences.lock" {
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
            return Err(Error::State("preferences file identity changed".into()));
        }
        Ok(file)
    }
    fn lock(&self) -> Result<File> {
        let file = match self.open_file("preferences.lock", true) {
            Ok(file) => file,
            Err(Error::Io(error)) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                self.open_file("preferences.lock", false)?
            }
            Err(e) => return Err(e),
        };
        if file.metadata()?.len() != 0 {
            return Err(Error::Permission("preferences lock must be empty".into()));
        }
        rustix::fs::flock(&file, FlockOperation::NonBlockingLockExclusive)
            .map_err(|_| Error::State("preferences save is busy".into()))?;
        self.verify()?;
        Ok(file)
    }
    fn read_document(&self) -> Result<Option<Entry>> {
        self.verify()?;
        if self.identity(NAME)?.is_none() {
            return Ok(None);
        }
        let mut file = self.open_file(NAME, false)?;
        let before = file.metadata()?;
        if before.len() == 0 || before.len() > LIMIT {
            return Err(Error::InvalidInput("preferences byte limit".into()));
        }
        let mut bytes = Vec::with_capacity(before.len() as usize);
        (&mut file).take(LIMIT + 1).read_to_end(&mut bytes)?;
        let after = file.metadata()?;
        if bytes.len() as u64 != before.len()
            || stamp(&before) != stamp(&after)
            || self.identity(NAME)? != Some(id(&after))
        {
            return Err(Error::State("preferences changed during read".into()));
        }
        self.verify()?;
        let document: Document = serde_json::from_slice(&bytes)
            .map_err(|_| Error::InvalidInput("preferences schema".into()))?;
        if document.schema_version != 1 {
            return Err(Error::InvalidInput("preferences schema version".into()));
        }
        validate_options(document.options)?;
        Ok(Some(Entry {
            options: document.options,
            bytes,
            identity: id(&after),
        }))
    }
}
#[cfg(test)]
mod tests;

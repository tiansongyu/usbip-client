//! Safe descriptor-relative filesystem checks for application state and endpoint locks.

use crate::config::valid_path;
use rustix::fs::{AtFlags, FileType, Mode, OFlags};
use rustusb_core::{Error, Result};
use std::{
    fs::File,
    os::unix::fs::MetadataExt,
    path::{Component, Path, PathBuf},
};

pub(crate) struct Directory {
    pub file: File,
    path: PathBuf,
    uid: u32,
    mode: u32,
    #[cfg(test)]
    anchor: PathBuf,
}
impl Directory {
    pub fn open(path: &Path, uid: u32, mode: u32) -> Result<Self> {
        Self::open_from(path, uid, mode, Path::new("/"), 0)
    }
    fn open_from(
        path: &Path,
        uid: u32,
        mode: u32,
        anchor: &Path,
        administrator: u32,
    ) -> Result<Self> {
        valid_path(path)?;
        let components: Vec<_> = path
            .strip_prefix(anchor)
            .map_err(|_| Error::Permission("agent directory trust root".into()))?
            .components()
            .collect();
        if components.is_empty() {
            return Err(Error::InvalidInput("agent leaf directory required".into()));
        }
        let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
        let mut file = File::from(
            rustix::fs::open(anchor, flags, Mode::empty()).map_err(std::io::Error::from)?,
        );
        for (index, part) in components.iter().enumerate() {
            let metadata = file.metadata()?;
            if !metadata.is_dir() || metadata.uid() != administrator || metadata.mode() & 0o022 != 0
            {
                return Err(Error::Permission(
                    "agent ancestors must be protected administrator-owned directories".into(),
                ));
            }
            let Component::Normal(name) = part else {
                return Err(Error::InvalidInput("agent directory component".into()));
            };
            file = File::from(
                rustix::fs::openat(&file, *name, flags, Mode::empty())
                    .map_err(std::io::Error::from)?,
            );
            if index + 1 == components.len() {
                let metadata = file.metadata()?;
                if !metadata.is_dir() || metadata.uid() != uid || metadata.mode() & 0o7777 != mode {
                    return Err(Error::Permission("agent directory owner/mode".into()));
                }
            }
        }
        Ok(Self {
            file,
            path: path.to_owned(),
            uid,
            mode,
            #[cfg(test)]
            anchor: anchor.to_owned(),
        })
    }
    pub fn verify(&self) -> Result<()> {
        #[cfg(not(test))]
        let current = Self::open(&self.path, self.uid, self.mode)?;
        #[cfg(test)]
        let current = Self::open_from(
            &self.path,
            self.uid,
            self.mode,
            &self.anchor,
            rustix::process::geteuid().as_raw(),
        )?;
        let a = current.file.metadata()?;
        let b = self.file.metadata()?;
        if (a.dev(), a.ino()) != (b.dev(), b.ino()) {
            return Err(Error::State("agent directory identity changed".into()));
        }
        Ok(())
    }
    pub fn identity(&self, name: &str) -> Result<Option<(u64, u64)>> {
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
                "agent file must be owner-only regular and singly linked".into(),
            ));
        }
        Ok(Some((stat.st_dev, stat.st_ino)))
    }
    pub fn open_file(&self, name: &str, create: bool) -> Result<File> {
        self.verify()?;
        let before = self.identity(name)?;
        let flags = OFlags::RDWR | OFlags::NOFOLLOW | OFlags::NONBLOCK | OFlags::CLOEXEC;
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
        let metadata = file.metadata()?;
        let id = Some((metadata.dev(), metadata.ino()));
        if (!create && before != id) || self.identity(name)? != id {
            return Err(Error::State("agent file identity changed".into()));
        }
        Ok(file)
    }
    pub fn lock(&self) -> Result<File> {
        let file = if self.identity("owner.lock")?.is_none() {
            self.open_file("owner.lock", true)?
        } else {
            self.open_file("owner.lock", false)?
        };
        rustix::fs::flock(&file, rustix::fs::FlockOperation::NonBlockingLockExclusive)
            .map_err(|_| Error::State("another agent owns this directory".into()))?;
        self.verify()?;
        Ok(file)
    }
    #[cfg(test)]
    pub fn fixture(path: &Path, anchor: &Path) -> Result<Self> {
        Self::open_from(
            path,
            rustix::process::geteuid().as_raw(),
            0o700,
            anchor,
            rustix::process::geteuid().as_raw(),
        )
    }
}

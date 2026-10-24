//! Private, no-replacement diagnostic artifact publication.

use serde::Serialize;
use std::{fmt, path::Path};

/// Observable artifact state, independent of whether collection was complete.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PublicationState {
    /// This operation did not create a staging file.
    NotCreated,
    /// A private staging artifact was created without publishing the final name.
    Partial,
    /// The complete file and publication were synchronized and verified.
    Published,
    /// Namespace identity or publication durability could not be confirmed.
    Unconfirmed,
}

/// Closed output failure categories; no original filesystem message is exposed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum FileFailureKind {
    /// The local path or encoded document exceeded its finite input contract.
    InvalidInput,
    /// A directory or file did not have trusted type, ownership or permissions.
    Permission,
    /// A destination or newly generated staging name already existed.
    AlreadyExists,
    /// A pinned directory or file no longer matched its original identity.
    IdentityChanged,
    /// An actual file operation failed.
    Io,
    /// Checked publication is unavailable on this operating system.
    Unsupported,
}

/// Successful file work, not a USB cleanup or collection-completeness proof.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FileReceipt {
    /// Whether the synchronized artifact retained its partial or final name.
    pub publication: PublicationState,
    /// Generated staging basename only; never a caller-supplied path.
    pub partial_name: Option<String>,
    /// Bytes actually accepted by successful writes.
    pub written_bytes: u64,
    /// SHA256 of those bytes; this digest does not establish durability itself.
    pub sha256: String,
}

/// Failure preserving actual file side effects without disclosing raw paths.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct FileFailure {
    /// Fixed failure category.
    pub kind: FileFailureKind,
    /// Original OS errno when one was returned by an operation.
    pub errno: Option<i32>,
    /// Actual known publication state, including post-rename uncertainty.
    pub publication: PublicationState,
    /// Generated partial basename, when its namespace may still be relevant.
    pub partial_name: Option<String>,
    /// Bytes accepted by successful writes before the failure.
    pub written_bytes: u64,
    /// SHA256 of accepted bytes, or none when this operation created no file.
    pub sha256: Option<String>,
}

impl FileFailure {
    fn before_create(kind: FileFailureKind) -> Self {
        Self {
            kind,
            errno: None,
            publication: PublicationState::NotCreated,
            partial_name: None,
            written_bytes: 0,
            sha256: None,
        }
    }

    #[cfg(target_os = "linux")]
    fn io(error: std::io::Error) -> Self {
        let kind = match error.kind() {
            std::io::ErrorKind::AlreadyExists => FileFailureKind::AlreadyExists,
            std::io::ErrorKind::PermissionDenied => FileFailureKind::Permission,
            _ => FileFailureKind::Io,
        };
        Self {
            errno: error.raw_os_error(),
            ..Self::before_create(kind)
        }
    }
}

impl fmt::Display for FileFailure {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self.kind {
            FileFailureKind::InvalidInput => "invalid diagnostic output input",
            FileFailureKind::Permission => "diagnostic output permission refused",
            FileFailureKind::AlreadyExists => "diagnostic output already exists",
            FileFailureKind::IdentityChanged => "diagnostic output identity changed",
            FileFailureKind::Io => "diagnostic output I/O failed",
            FileFailureKind::Unsupported => "checked diagnostic output is unsupported",
        })
    }
}

impl std::error::Error for FileFailure {}

/// Unique owner of one prepared artifact; dropping it never unlinks a name.
pub(crate) struct PreparedOutput {
    #[cfg(target_os = "linux")]
    inner: linux::Output,
}

impl PreparedOutput {
    /// Check an existing private parent and create a new private staging file.
    pub(crate) fn prepare(path: &Path) -> Result<Self, FileFailure> {
        #[cfg(target_os = "linux")]
        {
            linux::Output::prepare(path, Path::new("/")).map(|inner| Self { inner })
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = path;
            Err(FileFailure::before_create(FileFailureKind::Unsupported))
        }
    }

    /// Write and sync a bounded JSON document, optionally publishing its name.
    ///
    /// The caller must retain and join the actual synchronous work. `publish`
    /// false leaves a synchronized valid JSON document under its partial name.
    pub(crate) fn finish(self, bytes: &[u8], publish: bool) -> Result<FileReceipt, FileFailure> {
        #[cfg(target_os = "linux")]
        {
            self.inner.finish(bytes, publish)
        }
        #[cfg(not(target_os = "linux"))]
        {
            let _ = (bytes, publish);
            Err(FileFailure::before_create(FileFailureKind::Unsupported))
        }
    }
}

#[cfg(target_os = "linux")]
mod linux {
    use super::*;
    use rustix::fs::{AtFlags, FileType, Mode, OFlags, RenameFlags};
    use sha2::{Digest, Sha256};
    use std::{
        ffi::OsString,
        fs::{File, Metadata},
        io::{self, Write},
        os::unix::{ffi::OsStrExt, fs::MetadataExt},
        path::{Component, PathBuf},
    };

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    struct Identity(u64, u64);

    impl Identity {
        fn of(metadata: &Metadata) -> Self {
            Self(metadata.dev(), metadata.ino())
        }
    }

    struct Directory {
        file: File,
        path: PathBuf,
        identity: Identity,
        uid: u32,
        #[cfg(test)]
        anchor: PathBuf,
    }

    impl Directory {
        fn open(path: &Path, anchor: &Path) -> Result<Self, FileFailure> {
            let caller = rustix::process::geteuid().as_raw();
            let parts = path
                .strip_prefix(anchor)
                .map_err(|_| FileFailure::before_create(FileFailureKind::Permission))?
                .components();
            let flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
            let mut file = File::from(
                rustix::fs::open(anchor, flags, Mode::empty())
                    .map_err(io::Error::from)
                    .map_err(FileFailure::io)?,
            );
            for part in parts {
                let metadata = file.metadata().map_err(FileFailure::io)?;
                if !metadata.is_dir()
                    || metadata.mode() & 0o022 != 0
                    || (metadata.uid() != 0 && metadata.uid() != caller)
                {
                    return Err(FileFailure::before_create(FileFailureKind::Permission));
                }
                let Component::Normal(name) = part else {
                    return Err(FileFailure::before_create(FileFailureKind::InvalidInput));
                };
                file = File::from(
                    rustix::fs::openat(&file, name, flags, Mode::empty())
                        .map_err(io::Error::from)
                        .map_err(FileFailure::io)?,
                );
            }
            let metadata = file.metadata().map_err(FileFailure::io)?;
            if !metadata.is_dir() || metadata.uid() != caller || metadata.mode() & 0o7777 != 0o700 {
                return Err(FileFailure::before_create(FileFailureKind::Permission));
            }
            Ok(Self {
                identity: Identity::of(&metadata),
                file,
                path: path.to_owned(),
                uid: caller,
                #[cfg(test)]
                anchor: anchor.to_owned(),
            })
        }

        fn verify(&self) -> Result<(), FileFailure> {
            #[cfg(test)]
            let anchor = self.anchor.as_path();
            #[cfg(not(test))]
            let anchor = Path::new("/");
            let current = Self::open(&self.path, anchor)?;
            if current.identity != self.identity || current.uid != self.uid {
                return Err(FileFailure::before_create(FileFailureKind::IdentityChanged));
            }
            Ok(())
        }

        fn exists(&self, name: &std::ffi::OsStr) -> Result<bool, FileFailure> {
            match rustix::fs::statat(&self.file, name, AtFlags::SYMLINK_NOFOLLOW) {
                Ok(_) => Ok(true),
                Err(rustix::io::Errno::NOENT) => Ok(false),
                Err(error) => Err(FileFailure::io(error.into())),
            }
        }

        fn verify_entry(&self, name: &std::ffi::OsStr, file: &File) -> Result<(), FileFailure> {
            self.verify()?;
            let metadata = file.metadata().map_err(FileFailure::io)?;
            let entry = rustix::fs::statat(&self.file, name, AtFlags::SYMLINK_NOFOLLOW)
                .map_err(io::Error::from)
                .map_err(FileFailure::io)?;
            if !metadata.is_file()
                || metadata.uid() != self.uid
                || metadata.mode() & 0o7777 != 0o600
                || metadata.nlink() != 1
                || FileType::from_raw_mode(entry.st_mode) != FileType::RegularFile
                || entry.st_uid != self.uid
                || entry.st_mode & 0o7777 != 0o600
                || entry.st_nlink != 1
            {
                return Err(FileFailure::before_create(FileFailureKind::Permission));
            }
            if (metadata.dev(), metadata.ino()) != (entry.st_dev, entry.st_ino) {
                return Err(FileFailure::before_create(FileFailureKind::IdentityChanged));
            }
            Ok(())
        }
    }

    pub(super) struct Output {
        directory: Directory,
        file: File,
        final_name: OsString,
        partial_name: String,
        identity: Identity,
        written: u64,
        digest: Sha256,
        renamed: bool,
        #[cfg(test)]
        fault: Option<Fault>,
    }

    #[cfg(test)]
    #[derive(Clone, Copy)]
    pub(super) enum Fault {
        WriteAfter(usize),
        FileSync,
        DirectorySync,
    }

    impl Output {
        pub(super) fn prepare(path: &Path, anchor: &Path) -> Result<Self, FileFailure> {
            let raw = path.as_os_str().as_bytes();
            if !path.is_absolute()
                || raw.len() > 4096
                || raw.last() == Some(&b'/')
                || raw.contains(&0)
                || raw
                    .split(|byte| *byte == b'/')
                    .any(|part| part == b"." || part == b"..")
            {
                return Err(FileFailure::before_create(FileFailureKind::InvalidInput));
            }
            let parent = path
                .parent()
                .ok_or_else(|| FileFailure::before_create(FileFailureKind::InvalidInput))?;
            let final_name = path
                .file_name()
                .filter(|name| !name.is_empty() && name.as_bytes().len() <= 255)
                .ok_or_else(|| FileFailure::before_create(FileFailureKind::InvalidInput))?
                .to_owned();
            let directory = Directory::open(parent, anchor)?;
            if directory.exists(&final_name)? {
                return Err(FileFailure::before_create(FileFailureKind::AlreadyExists));
            }
            let id = rustusb_logging::LogId::fresh().map_err(|error| match error {
                rustusb_logging::Error::Io(error) => FileFailure::io(error),
                rustusb_logging::Error::Unsupported => {
                    FileFailure::before_create(FileFailureKind::Unsupported)
                }
                _ => FileFailure::before_create(FileFailureKind::InvalidInput),
            })?;
            let partial_name = format!(".rustusb-diagnostic-{}.partial", id.as_str());
            if final_name == std::ffi::OsStr::new(&partial_name) {
                return Err(FileFailure::before_create(FileFailureKind::InvalidInput));
            }
            let file = File::from(
                rustix::fs::openat(
                    &directory.file,
                    partial_name.as_str(),
                    OFlags::WRONLY
                        | OFlags::CREATE
                        | OFlags::EXCL
                        | OFlags::NOFOLLOW
                        | OFlags::NONBLOCK
                        | OFlags::CLOEXEC,
                    Mode::RUSR | Mode::WUSR,
                )
                .map_err(io::Error::from)
                .map_err(FileFailure::io)?,
            );
            let mut output = Self {
                directory,
                file,
                final_name,
                partial_name,
                identity: Identity(0, 0),
                written: 0,
                digest: Sha256::new(),
                renamed: false,
                #[cfg(test)]
                fault: None,
            };
            rustix::fs::fchmod(&output.file, Mode::RUSR | Mode::WUSR)
                .map_err(io::Error::from)
                .map_err(|error| output.failure(FileFailure::io(error), false))?;
            output.identity = Identity::of(
                &output
                    .file
                    .metadata()
                    .map_err(|error| output.failure(FileFailure::io(error), false))?,
            );
            output
                .verify(false)
                .map_err(|failure| output.failure(failure, true))?;
            Ok(output)
        }

        fn failure(&self, mut failure: FileFailure, uncertain: bool) -> FileFailure {
            failure.publication = if self.renamed || uncertain {
                PublicationState::Unconfirmed
            } else {
                PublicationState::Partial
            };
            failure.partial_name = (!self.renamed).then(|| self.partial_name.clone());
            failure.written_bytes = self.written;
            failure.sha256 = Some(format!("{:x}", self.digest.clone().finalize()));
            failure
        }

        fn verify(&self, final_entry: bool) -> Result<(), FileFailure> {
            let name = if final_entry {
                self.final_name.as_os_str()
            } else {
                std::ffi::OsStr::new(&self.partial_name)
            };
            self.directory.verify_entry(name, &self.file)?;
            let metadata = self.file.metadata().map_err(FileFailure::io)?;
            if Identity::of(&metadata) != self.identity || metadata.len() != self.written {
                return Err(FileFailure::before_create(FileFailureKind::IdentityChanged));
            }
            Ok(())
        }

        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            #[cfg(test)]
            if let Some(Fault::WriteAfter(limit)) = self.fault {
                let remaining = limit.saturating_sub(self.written as usize);
                if remaining == 0 {
                    return Err(io::Error::from_raw_os_error(
                        rustix::io::Errno::IO.raw_os_error(),
                    ));
                }
                return self.file.write(&bytes[..bytes.len().min(remaining)]);
            }
            self.file.write(bytes)
        }

        fn sync_file(&self) -> io::Result<()> {
            #[cfg(test)]
            if matches!(self.fault, Some(Fault::FileSync)) {
                return Err(io::Error::from_raw_os_error(
                    rustix::io::Errno::IO.raw_os_error(),
                ));
            }
            self.file.sync_all()
        }

        fn sync_directory(&self) -> io::Result<()> {
            #[cfg(test)]
            if matches!(self.fault, Some(Fault::DirectorySync)) {
                return Err(io::Error::from_raw_os_error(
                    rustix::io::Errno::IO.raw_os_error(),
                ));
            }
            self.directory.file.sync_all()
        }

        pub(super) fn finish(
            mut self,
            bytes: &[u8],
            publish: bool,
        ) -> Result<FileReceipt, FileFailure> {
            if bytes.is_empty()
                || bytes.len() > crate::MAX_EXPORT_BYTES
                || serde_json::from_slice::<serde::de::IgnoredAny>(bytes).is_err()
            {
                return Err(self.failure(
                    FileFailure::before_create(FileFailureKind::InvalidInput),
                    false,
                ));
            }
            self.verify(false)
                .map_err(|failure| self.failure(failure, true))?;
            let mut remaining = bytes;
            while !remaining.is_empty() {
                match self.write(remaining) {
                    Ok(0) => {
                        return Err(self.failure(
                            FileFailure::io(io::Error::from(io::ErrorKind::WriteZero)),
                            false,
                        ));
                    }
                    Ok(count) => {
                        self.written += count as u64;
                        self.digest.update(&remaining[..count]);
                        remaining = &remaining[count..];
                    }
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                    Err(error) => return Err(self.failure(FileFailure::io(error), false)),
                }
            }
            self.sync_file()
                .map_err(|error| self.failure(FileFailure::io(error), false))?;
            self.verify(false)
                .map_err(|failure| self.failure(failure, true))?;
            if publish {
                rustix::fs::renameat_with(
                    &self.directory.file,
                    self.partial_name.as_str(),
                    &self.directory.file,
                    &self.final_name,
                    RenameFlags::NOREPLACE,
                )
                .map_err(io::Error::from)
                .map_err(|error| self.failure(FileFailure::io(error), false))?;
                self.renamed = true;
            }
            self.sync_directory()
                .map_err(|error| self.failure(FileFailure::io(error), false))?;
            self.verify(publish)
                .map_err(|failure| self.failure(failure, true))?;
            Ok(FileReceipt {
                publication: if publish {
                    PublicationState::Published
                } else {
                    PublicationState::Partial
                },
                partial_name: (!publish).then(|| self.partial_name.clone()),
                written_bytes: self.written,
                sha256: format!("{:x}", self.digest.finalize()),
            })
        }
    }

    #[cfg(test)]
    #[path = "output_tests.rs"]
    mod tests;
}

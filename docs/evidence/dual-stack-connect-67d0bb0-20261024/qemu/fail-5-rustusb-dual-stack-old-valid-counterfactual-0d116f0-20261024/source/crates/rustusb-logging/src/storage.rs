//! Checked, bounded operational history. This is not an application WAL.

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::read_directory;
#[cfg(target_os = "linux")]
pub(crate) use linux::{Source, Storage, read_source};

#[cfg(not(target_os = "linux"))]
mod unsupported {
    use crate::{Cursor, Error, LogId, LogPage, LogRecord, Options, ReadFilter, Result};
    use std::path::Path;

    pub(crate) struct Storage;
    #[derive(Clone)]
    pub(crate) struct Source;
    impl Storage {
        pub(crate) fn open(_: &Options, _: &LogId) -> Result<Self> {
            Err(Error::Unsupported)
        }
        pub(crate) fn source(&self) -> Source {
            Source
        }
        pub(crate) fn append(&mut self, _: &LogRecord) -> Result<Cursor> {
            Err(Error::Unsupported)
        }
        pub(crate) fn sync(&mut self) -> Result<()> {
            Err(Error::Unsupported)
        }
    }
    pub(crate) fn read_source(
        _: &Source,
        _: &ReadFilter,
        _: Option<&Cursor>,
        _: u8,
    ) -> Result<LogPage> {
        Err(Error::Unsupported)
    }
    /// Read bounded process history on supported platforms; never creates files.
    pub fn read_directory(_: &Path, _: Option<Cursor>, _: u8) -> Result<LogPage> {
        Err(Error::Unsupported)
    }
}
#[cfg(not(target_os = "linux"))]
pub use unsupported::read_directory;
#[cfg(not(target_os = "linux"))]
pub(crate) use unsupported::{Source, Storage, read_source};

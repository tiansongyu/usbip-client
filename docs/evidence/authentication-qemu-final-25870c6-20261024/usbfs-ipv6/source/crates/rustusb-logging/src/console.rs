//! Preserve returned console I/O failures without recursively printing them.

use crate::{Error, Result};
use std::{
    io::{self, Write},
    sync::{Arc, Mutex},
};
use tracing_subscriber::fmt::MakeWriter;

#[derive(Clone, Copy)]
struct Failure {
    kind: io::ErrorKind,
    errno: Option<i32>,
}

#[derive(Default)]
pub(crate) struct ConsoleState {
    first: Mutex<Option<Failure>>,
}

impl ConsoleState {
    fn record(&self, error: &io::Error) {
        // The actual I/O has already returned. Never hold this metadata lock
        // over a write or recursively format/log the original error.
        if let Ok(mut first) = self.first.lock() {
            first.get_or_insert(Failure {
                kind: error.kind(),
                errno: error.raw_os_error(),
            });
        }
    }

    pub(crate) fn check(&self) -> Result<()> {
        let first = *self
            .first
            .lock()
            .map_err(|_| Error::Worker("console error state poisoned"))?;
        match first {
            None => Ok(()),
            Some(failure) => Err(Error::Io(match failure.errno {
                Some(errno) => io::Error::from_raw_os_error(errno),
                None => io::Error::new(failure.kind, "console log output failed"),
            })),
        }
    }
}

#[derive(Clone)]
pub(crate) struct ConsoleWriter(pub(crate) Arc<ConsoleState>);

impl<'writer> MakeWriter<'writer> for ConsoleWriter {
    type Writer = Checked<io::Stderr>;

    fn make_writer(&'writer self) -> Self::Writer {
        Checked {
            inner: io::stderr(),
            state: self.0.clone(),
        }
    }
}

pub(crate) struct Checked<W> {
    inner: W,
    state: Arc<ConsoleState>,
}

impl<W: Write> Checked<W> {
    fn observed<T>(&self, result: io::Result<T>) -> io::Result<T> {
        if let Err(error) = &result {
            self.state.record(error);
        }
        result
    }
}

impl<W: Write> Write for Checked<W> {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let result = self.inner.write(bytes);
        self.observed(result)
    }

    fn write_all(&mut self, bytes: &[u8]) -> io::Result<()> {
        // Keep the underlying stderr write_all's existing line-lock behavior,
        // including its actual partial-write/error semantics.
        let result = self.inner.write_all(bytes);
        self.observed(result)
    }

    fn flush(&mut self) -> io::Result<()> {
        let result = self.inner.flush();
        self.observed(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    type TestResult = std::result::Result<(), Box<dyn std::error::Error + Send + Sync>>;

    #[test]
    fn healthy_real_writer_preserves_exact_bytes_and_console_result() -> TestResult {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("output");
        let state = Arc::new(ConsoleState::default());
        let mut checked = Checked {
            inner: std::fs::File::create(&path)?,
            state: state.clone(),
        };
        checked.write_all(b"first\n")?;
        checked.write_all(b"second\n")?;
        checked.flush()?;
        state.check()?;
        assert_eq!(std::fs::read(path)?, b"first\nsecond\n");
        Ok(())
    }

    #[cfg(target_os = "linux")]
    #[test]
    fn actual_failed_writes_return_original_errors_and_latch_only_the_first() -> TestResult {
        let directory = tempfile::tempdir()?;
        let path = directory.path().join("readonly");
        std::fs::write(&path, b"preserve")?;
        let state = Arc::new(ConsoleState::default());
        let mut checked = Checked {
            inner: std::fs::File::open(&path)?,
            state: state.clone(),
        };
        let failed = checked
            .write_all(b"must not be acknowledged")
            .err()
            .ok_or("readonly write succeeded")?;
        assert_eq!(failed.raw_os_error(), Some(9));
        assert_eq!(std::fs::read(path)?, b"preserve");
        // A different real kernel error must not replace the original one.
        checked.inner = std::fs::OpenOptions::new().write(true).open("/dev/full")?;
        let later = checked
            .write_all(b"later")
            .err()
            .ok_or("full device write succeeded")?;
        assert_eq!(later.raw_os_error(), Some(28));
        let retained = match state.check() {
            Err(Error::Io(error)) => error.raw_os_error(),
            _ => None,
        };
        assert_eq!(retained, Some(9));
        Ok(())
    }
}

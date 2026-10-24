//! Actual TLS write/flush fences for the fixed single-stream h2 encoder.
//!
//! Only headers and counters are inspected; application payload is never kept.

use std::{
    future::Future,
    io,
    pin::Pin,
    task::{Context, Poll},
};
use tokio::{
    io::{AsyncRead, AsyncWrite, ReadBuf},
    sync::watch,
};

const PREFACE: &[u8; 24] = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";

/// Outgoing client bytes start with the HTTP/2 connection preface.
#[derive(Clone, Copy)]
pub(crate) enum Side {
    Client,
    Server,
}

#[derive(Clone, Copy, Default, PartialEq, Eq)]
struct Fence {
    data: u64,
    fin: bool,
    closed: bool,
    failure: Option<io::ErrorKind>,
    write_pending: bool,
}

/// A nonowning receipt. It never keeps the underlying TLS stream alive.
pub(crate) struct FlushMonitor {
    state: watch::Receiver<Fence>,
    changed: Option<Pin<Box<dyn Future<Output = ()> + Send>>>,
}

impl Clone for FlushMonitor {
    fn clone(&self) -> Self {
        Self {
            state: self.state.clone(),
            changed: None,
        }
    }
}

impl FlushMonitor {
    /// Whether the last underlying TLS write, flush or shutdown poll was pending.
    pub(crate) fn write_pending(&self) -> bool {
        self.state.borrow().write_pending
    }

    /// Wait for a complete DATA byte prefix and, if requested, the actual FIN.
    /// The target is checked afresh, including after a cancelled earlier flush.
    pub(crate) fn poll_flushed(
        &mut self,
        cx: &mut Context<'_>,
        target_data: u64,
        require_fin: bool,
    ) -> Poll<io::Result<()>> {
        loop {
            let state = *self.state.borrow_and_update();
            if let Some(kind) = state.failure {
                return Poll::Ready(Err(io::Error::new(kind, "framed TLS output failed")));
            }
            if state.data >= target_data && (!require_fin || state.fin) {
                return Poll::Ready(Ok(()));
            }
            if state.closed {
                return Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::BrokenPipe,
                    "framed TLS output ended before its flush receipt",
                )));
            }
            if self.changed.is_none() {
                // Clone the receiver's observed version, not a fresh subscribe:
                // a change between the snapshot and this clone remains visible.
                let mut receiver = self.state.clone();
                self.changed = Some(Box::pin(async move {
                    let _ = receiver.changed().await;
                }));
            }
            if let Some(changed) = self.changed.as_mut() {
                match changed.as_mut().poll(cx) {
                    Poll::Pending => return Poll::Pending,
                    Poll::Ready(()) => self.changed = None,
                }
            }
        }
    }
}

struct Parser {
    preface: usize,
    header: [u8; 9],
    header_bytes: usize,
    remaining: usize,
    payload_length: u64,
    data_frame: bool,
    end_stream: bool,
    complete_data: u64,
    complete_fin: bool,
}

impl Parser {
    fn new(side: Side) -> Self {
        Self {
            preface: if matches!(side, Side::Client) { 0 } else { 24 },
            header: [0; 9],
            header_bytes: 0,
            remaining: 0,
            payload_length: 0,
            data_frame: false,
            end_stream: false,
            complete_data: 0,
            complete_fin: false,
        }
    }

    fn finish_frame(&mut self) -> io::Result<()> {
        if self.data_frame {
            self.complete_data = self
                .complete_data
                .checked_add(self.payload_length)
                .ok_or_else(|| invalid("framed TLS output counter exhausted"))?;
            self.complete_fin |= self.end_stream;
        }
        self.header_bytes = 0;
        Ok(())
    }

    fn accept(&mut self, mut bytes: &[u8]) -> io::Result<()> {
        while !bytes.is_empty() {
            if self.preface < PREFACE.len() {
                let count = (PREFACE.len() - self.preface).min(bytes.len());
                if bytes[..count] != PREFACE[self.preface..self.preface + count] {
                    return Err(invalid("unexpected framed TLS client preface"));
                }
                self.preface += count;
                bytes = &bytes[count..];
                continue;
            }
            if self.header_bytes < self.header.len() {
                let count = (self.header.len() - self.header_bytes).min(bytes.len());
                self.header[self.header_bytes..self.header_bytes + count]
                    .copy_from_slice(&bytes[..count]);
                self.header_bytes += count;
                bytes = &bytes[count..];
                if self.header_bytes != self.header.len() {
                    continue;
                }
                self.remaining = (usize::from(self.header[0]) << 16)
                    | (usize::from(self.header[1]) << 8)
                    | usize::from(self.header[2]);
                self.payload_length = self.remaining as u64;
                self.data_frame = self.header[3] == 0;
                self.end_stream = self.data_frame && self.header[4] & 1 != 0;
                if self.data_frame {
                    let stream = u32::from_be_bytes([
                        self.header[5],
                        self.header[6],
                        self.header[7],
                        self.header[8],
                    ]);
                    // The fixed local encoder never pads DATA. Private v2 uses
                    // only stream 1; other frames are skipped, never a fence.
                    if stream != 1 || self.header[4] & !1 != 0 || self.complete_fin {
                        return Err(invalid("unexpected framed TLS DATA encoding"));
                    }
                }
                if self.remaining == 0 {
                    self.finish_frame()?;
                }
                continue;
            }
            let count = self.remaining.min(bytes.len());
            self.remaining -= count;
            bytes = &bytes[count..];
            if self.remaining == 0 {
                self.finish_frame()?;
            }
        }
        Ok(())
    }
}

fn invalid(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

/// Observes only successful scalar writes and actual underlying flush receipts.
pub(crate) struct TrackingIo<S> {
    inner: Option<S>,
    parser: Parser,
    state: watch::Sender<Fence>,
}

impl<S> TrackingIo<S> {
    pub(crate) fn new(inner: S, side: Side) -> (Self, FlushMonitor) {
        let (state, receiver) = watch::channel(Fence::default());
        (
            Self {
                inner: Some(inner),
                parser: Parser::new(side),
                state,
            },
            FlushMonitor {
                state: receiver,
                changed: None,
            },
        )
    }

    fn failed(&self, kind: io::ErrorKind) {
        self.state.send_if_modified(|state| {
            if state.failure.is_some() {
                return false;
            }
            state.failure = Some(kind);
            true
        });
    }

    fn pending(&self, pending: bool) {
        self.state.send_if_modified(|state| {
            let changed = state.write_pending != pending;
            state.write_pending = pending;
            changed
        });
    }

    fn flushed(&self) {
        self.state.send_if_modified(|state| {
            let changed =
                state.data != self.parser.complete_data || state.fin != self.parser.complete_fin;
            state.data = self.parser.complete_data;
            state.fin = self.parser.complete_fin;
            changed
        });
    }
}

impl<S: AsyncRead + Unpin> AsyncRead for TrackingIo<S> {
    fn poll_read(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buffer: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        match self.get_mut().inner.as_mut() {
            Some(inner) => Pin::new(inner).poll_read(cx, buffer),
            None => Poll::Ready(Err(io::ErrorKind::BrokenPipe.into())),
        }
    }
}

impl<S: AsyncWrite + Unpin> AsyncWrite for TrackingIo<S> {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        let this = self.get_mut();
        let Some(inner) = this.inner.as_mut() else {
            return Poll::Ready(Err(io::ErrorKind::BrokenPipe.into()));
        };
        let result = Pin::new(inner).poll_write(cx, bytes);
        this.pending(result.is_pending());
        match result {
            Poll::Pending => Poll::Pending,
            Poll::Ready(Err(error)) => {
                this.failed(error.kind());
                Poll::Ready(Err(error))
            }
            Poll::Ready(Ok(count)) => {
                let result = bytes
                    .get(..count)
                    .ok_or_else(|| invalid("framed TLS writer returned an invalid length"))
                    .and_then(|accepted| this.parser.accept(accepted));
                if let Err(error) = result {
                    this.failed(error.kind());
                    return Poll::Ready(Err(error));
                }
                if count == 0 && !bytes.is_empty() {
                    this.failed(io::ErrorKind::WriteZero);
                    return Poll::Ready(Err(io::ErrorKind::WriteZero.into()));
                }
                Poll::Ready(Ok(count))
            }
        }
    }

    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        let Some(inner) = this.inner.as_mut() else {
            return Poll::Ready(Err(io::ErrorKind::BrokenPipe.into()));
        };
        let result = Pin::new(inner).poll_flush(cx);
        this.pending(result.is_pending());
        match result {
            Poll::Pending => Poll::Pending,
            Poll::Ready(Ok(())) => {
                this.flushed();
                Poll::Ready(Ok(()))
            }
            Poll::Ready(Err(error)) => {
                this.failed(error.kind());
                Poll::Ready(Err(error))
            }
        }
    }

    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let this = self.get_mut();
        let Some(inner) = this.inner.as_mut() else {
            return Poll::Ready(Err(io::ErrorKind::BrokenPipe.into()));
        };
        let result = Pin::new(inner).poll_shutdown(cx);
        this.pending(result.is_pending());
        match result {
            Poll::Pending => Poll::Pending,
            Poll::Ready(Ok(())) => {
                // AsyncWrite shutdown includes flushing its internal buffers.
                this.flushed();
                Poll::Ready(Ok(()))
            }
            Poll::Ready(Err(error)) => {
                this.failed(error.kind());
                Poll::Ready(Err(error))
            }
        }
    }
}

impl<S> Drop for TrackingIo<S> {
    fn drop(&mut self) {
        drop(self.inner.take());
        self.state.send_if_modified(|state| {
            let changed = !state.closed;
            state.closed = true;
            changed
        });
    }
}

#[cfg(test)]
mod tests;

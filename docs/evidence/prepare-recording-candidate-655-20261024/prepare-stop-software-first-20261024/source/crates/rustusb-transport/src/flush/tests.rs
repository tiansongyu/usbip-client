//! Controlled AsyncWrite contract tests, not h2/TLS or USB runtime evidence.
//! All receipts use TrackingIo/FlushMonitor; no parser fields are inspected.

use super::{FlushMonitor, Side, TrackingIo};
use std::{
    future::{Future, poll_fn},
    io::{self, IoSlice},
    pin::Pin,
    sync::{Arc, Mutex},
    task::{Context, Poll, Waker},
    time::Duration,
};
use tokio::{
    io::{AsyncWrite, AsyncWriteExt},
    sync::oneshot,
    task::JoinHandle,
};

const WAIT: Duration = Duration::from_secs(2);
const CLIENT_PREFACE: &[u8] = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n";

struct State {
    bytes: Vec<u8>,
    max_write: usize,
    write_open: bool,
    flush_open: bool,
    write_error: Option<io::ErrorKind>,
    flush_error: Option<io::ErrorKind>,
    write_waker: Option<Waker>,
    flush_waker: Option<Waker>,
    scalar_calls: usize,
    vectored_calls: usize,
    dropped: bool,
}

#[derive(Clone)]
struct Control(Arc<Mutex<State>>);
struct Writer(Control);

impl Control {
    fn new(max_write: usize) -> (Writer, Self) {
        let control = Self(Arc::new(Mutex::new(State {
            bytes: Vec::new(),
            max_write,
            write_open: true,
            flush_open: true,
            write_error: None,
            flush_error: None,
            write_waker: None,
            flush_waker: None,
            scalar_calls: 0,
            vectored_calls: 0,
            dropped: false,
        })));
        (Writer(control.clone()), control)
    }

    fn inspect<T>(&self, operation: impl FnOnce(&State) -> T) -> io::Result<T> {
        let state = self
            .0
            .lock()
            .map_err(|_| io::Error::other("fixture lock"))?;
        Ok(operation(&state))
    }

    fn change(&self, operation: impl FnOnce(&mut State)) -> io::Result<()> {
        let (write, flush) = {
            let mut state = self
                .0
                .lock()
                .map_err(|_| io::Error::other("fixture lock"))?;
            operation(&mut state);
            (state.write_waker.take(), state.flush_waker.take())
        };
        for waker in [write, flush].into_iter().flatten() {
            waker.wake();
        }
        Ok(())
    }

    fn bytes(&self) -> io::Result<Vec<u8>> {
        self.inspect(|state| state.bytes.clone())
    }
}

impl AsyncWrite for Writer {
    fn poll_write(
        self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        let mut state = match self.0.0.lock() {
            Ok(state) => state,
            Err(_) => return Poll::Ready(Err(io::Error::other("fixture lock"))),
        };
        state.scalar_calls += 1;
        if let Some(kind) = state.write_error {
            return Poll::Ready(Err(kind.into()));
        }
        if !state.write_open {
            state.write_waker = Some(cx.waker().clone());
            return Poll::Pending;
        }
        let count = bytes.len().min(state.max_write);
        state.bytes.extend_from_slice(&bytes[..count]);
        Poll::Ready(Ok(count))
    }

    fn poll_flush(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        let mut state = match self.0.0.lock() {
            Ok(state) => state,
            Err(_) => return Poll::Ready(Err(io::Error::other("fixture lock"))),
        };
        if let Some(kind) = state.flush_error {
            return Poll::Ready(Err(kind.into()));
        }
        if !state.flush_open {
            state.flush_waker = Some(cx.waker().clone());
            return Poll::Pending;
        }
        Poll::Ready(Ok(()))
    }

    fn poll_shutdown(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.poll_flush(cx)
    }

    fn is_write_vectored(&self) -> bool {
        true
    }

    fn poll_write_vectored(
        self: Pin<&mut Self>,
        _cx: &mut Context<'_>,
        _buffers: &[IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        match self.0.0.lock() {
            Ok(mut state) => state.vectored_calls += 1,
            Err(_) => return Poll::Ready(Err(io::Error::other("fixture lock"))),
        }
        Poll::Ready(Err(io::Error::other(
            "TrackingIo must use its scalar fallback",
        )))
    }
}

impl Drop for Writer {
    fn drop(&mut self) {
        if let Ok(mut state) = self.0.0.lock() {
            state.dropped = true;
        }
    }
}

fn frame(kind: u8, flags: u8, stream: u32, payload: &[u8]) -> io::Result<Vec<u8>> {
    let length = u32::try_from(payload.len()).map_err(io::Error::other)?;
    if length > 0x00ff_ffff {
        return Err(io::Error::other("fixture frame length"));
    }
    let length = length.to_be_bytes();
    let mut bytes = vec![length[1], length[2], length[3], kind, flags];
    bytes.extend_from_slice(&stream.to_be_bytes());
    bytes.extend_from_slice(payload);
    Ok(bytes)
}

fn pending(monitor: &mut FlushMonitor, target: u64, fin: bool) {
    let mut cx = Context::from_waker(Waker::noop());
    assert!(monitor.poll_flushed(&mut cx, target, fin).is_pending());
}

async fn receipt(monitor: &mut FlushMonitor, target: u64, fin: bool) -> io::Result<()> {
    tokio::time::timeout(WAIT, poll_fn(|cx| monitor.poll_flushed(cx, target, fin)))
        .await
        .map_err(|_| io::Error::other("flush receipt did not wake"))?
}

async fn signal(receiver: oneshot::Receiver<()>) -> io::Result<()> {
    tokio::time::timeout(WAIT, receiver)
        .await
        .map_err(|_| io::Error::other("fixture did not become pending"))?
        .map_err(io::Error::other)
}

async fn joined<T>(mut task: JoinHandle<io::Result<T>>) -> io::Result<T> {
    match tokio::time::timeout(WAIT, &mut task).await {
        Ok(result) => result.map_err(io::Error::other)?,
        Err(_) => {
            task.abort();
            let _ = task.await;
            Err(io::Error::other("fixture task did not wake"))
        }
    }
}

async fn waiter(
    mut monitor: FlushMonitor,
    target: u64,
    fin: bool,
) -> io::Result<JoinHandle<io::Result<()>>> {
    let (started, receiver) = oneshot::channel();
    let mut started = Some(started);
    let task = tokio::spawn(async move {
        poll_fn(|cx| {
            let result = monitor.poll_flushed(cx, target, fin);
            if result.is_pending()
                && let Some(started) = started.take()
            {
                let _ = started.send(());
            }
            result
        })
        .await
    });
    signal(receiver).await?;
    Ok(task)
}

fn error_kind<T>(result: io::Result<T>) -> io::Result<io::ErrorKind> {
    match result {
        Err(error) => Ok(error.kind()),
        Ok(_) => Err(io::Error::other("expected fixture operation to fail")),
    }
}

#[tokio::test]
async fn segmented_successful_prefixes_require_complete_data_frames() -> io::Result<()> {
    let (writer, control) = Control::new(3);
    let (mut io, mut monitor) = TrackingIo::new(writer, Side::Server);
    let mut wire = frame(4, 0, 0, &[])?; // SETTINGS has no application bytes.
    wire.extend(frame(6, 0, 0, b"12345678")?);
    wire.extend(frame(0, 0, 1, b"abcde")?);
    let first_end = wire.len();
    wire.extend(frame(6, 1, 0, b"12345678")?);
    wire.extend(frame(0, 0, 1, b"fg")?);
    let mut accepted = 0;
    while accepted < wire.len() {
        let count = io.write(&wire[accepted..]).await?;
        assert!((1..=3).contains(&count));
        accepted += count;
        assert_eq!(control.bytes()?, wire[..accepted]);
        io.flush().await?;
        if accepted < first_end {
            pending(&mut monitor, 1, false);
        } else {
            receipt(&mut monitor, 5, false).await?;
        }
        if accepted < wire.len() {
            pending(&mut monitor, 6, false);
        }
    }
    receipt(&mut monitor, 7, false).await?;
    pending(&mut monitor, 8, false);
    pending(&mut monitor, 7, true);
    assert_eq!(control.bytes()?, wire);
    Ok(())
}

#[tokio::test]
async fn blocked_write_and_flush_each_need_their_actual_wakeup() -> io::Result<()> {
    let (writer, control) = Control::new(usize::MAX);
    control.change(|state| {
        state.write_open = false;
        state.flush_open = false;
    })?;
    let (mut io, mut monitor) = TrackingIo::new(writer, Side::Server);
    let wire = frame(0, 0, 1, b"abc")?;
    let expected = wire.clone();
    let waiting = waiter(monitor.clone(), 3, false).await?;
    let (write_started, write_rx) = oneshot::channel();
    let (flush_started, flush_rx) = oneshot::channel();
    let task = tokio::spawn(async move {
        let mut started = Some(write_started);
        let count = poll_fn(|cx| {
            let result = Pin::new(&mut io).poll_write(cx, &wire);
            if result.is_pending()
                && let Some(started) = started.take()
            {
                let _ = started.send(());
            }
            result
        })
        .await?;
        assert_eq!(count, wire.len());
        let mut started = Some(flush_started);
        poll_fn(|cx| {
            let result = Pin::new(&mut io).poll_flush(cx);
            if result.is_pending()
                && let Some(started) = started.take()
            {
                let _ = started.send(());
            }
            result
        })
        .await?;
        Ok(io)
    });
    signal(write_rx).await?;
    assert!(control.bytes()?.is_empty());
    pending(&mut monitor, 3, false);
    control.change(|state| state.write_open = true)?;
    signal(flush_rx).await?;
    assert_eq!(control.bytes()?, expected);
    pending(&mut monitor, 3, false);
    control.change(|state| state.flush_open = true)?;
    let io = joined(task).await?;
    joined(waiting).await?;
    assert!(!control.inspect(|state| state.dropped)?);
    drop(io);
    assert!(control.inspect(|state| state.dropped)?);
    receipt(&mut monitor, 3, false).await?;
    Ok(())
}

#[tokio::test]
async fn client_preface_is_exact_and_accepted_one_byte_at_a_time() -> io::Result<()> {
    let (writer, control) = Control::new(1);
    let (mut io, mut monitor) = TrackingIo::new(writer, Side::Client);
    let mut wire = CLIENT_PREFACE.to_vec();
    wire.extend(frame(0, 0, 1, b"ok")?);
    for offset in 0..wire.len() {
        assert_eq!(io.write(&wire[offset..]).await?, 1);
        io.flush().await?;
        if offset + 1 < wire.len() {
            pending(&mut monitor, 1, false);
        }
    }
    receipt(&mut monitor, 2, false).await?;
    assert_eq!(control.bytes()?, wire);

    let (writer, control) = Control::new(1);
    let (mut io, monitor) = TrackingIo::new(writer, Side::Client);
    io.write_all(&CLIENT_PREFACE[..23]).await?;
    let waiting = waiter(monitor, 1, false).await?;
    assert_eq!(
        error_kind(io.write(b"X").await)?,
        io::ErrorKind::InvalidData
    );
    assert_eq!(
        error_kind(joined(waiting).await)?,
        io::ErrorKind::InvalidData
    );
    let mut accepted = CLIENT_PREFACE[..23].to_vec();
    accepted.push(b'X');
    assert_eq!(control.bytes()?, accepted);

    let (writer, _) = Control::new(usize::MAX);
    let (mut io, mut monitor) = TrackingIo::new(writer, Side::Client);
    assert_eq!(
        error_kind(io.write(&frame(0, 0, 1, b"x")?).await)?,
        io::ErrorKind::InvalidData
    );
    assert_eq!(
        error_kind(receipt(&mut monitor, 1, false).await)?,
        io::ErrorKind::InvalidData
    );
    Ok(())
}

#[tokio::test]
async fn zero_data_end_stream_requires_last_header_byte_and_flush() -> io::Result<()> {
    let (writer, control) = Control::new(usize::MAX);
    let (mut io, mut monitor) = TrackingIo::new(writer, Side::Server);
    let mut expected = frame(0, 0, 1, b"abc")?;
    io.write_all(&expected).await?;
    io.flush().await?;
    receipt(&mut monitor, 3, false).await?;
    let waiting = waiter(monitor.clone(), 3, true).await?;
    let ping_ack = frame(6, 1, 0, b"12345678")?;
    io.write_all(&ping_ack).await?;
    expected.extend(ping_ack);
    io.flush().await?;
    pending(&mut monitor, 3, true);
    let fin = frame(0, 1, 1, &[])?;
    io.write_all(&fin[..8]).await?;
    io.flush().await?;
    pending(&mut monitor, 3, true);
    control.change(|state| state.flush_open = false)?;
    io.write_all(&fin[8..]).await?;
    expected.extend(fin);
    pending(&mut monitor, 3, true);
    let (started, receiver) = oneshot::channel();
    let task = tokio::spawn(async move {
        let mut started = Some(started);
        poll_fn(|cx| {
            let result = Pin::new(&mut io).poll_flush(cx);
            if result.is_pending()
                && let Some(started) = started.take()
            {
                let _ = started.send(());
            }
            result
        })
        .await?;
        Ok(io)
    });
    signal(receiver).await?;
    pending(&mut monitor, 3, true);
    control.change(|state| state.flush_open = true)?;
    let _io = joined(task).await?;
    joined(waiting).await?;
    pending(&mut monitor, 4, true);
    assert_eq!(control.bytes()?, expected);
    Ok(())
}

#[tokio::test]
async fn write_flush_zero_and_drop_failures_wake_pending_receipts() -> io::Result<()> {
    for scenario in ["write", "flush", "zero", "drop"] {
        let (writer, control) = Control::new(usize::MAX);
        let (mut io, mut monitor) = TrackingIo::new(writer, Side::Server);
        let waiting = waiter(monitor.clone(), 3, false).await?;
        let wire = frame(0, 0, 1, b"abc")?;
        let expected = match scenario {
            "write" => {
                control.change(|state| state.write_error = Some(io::ErrorKind::ConnectionReset))?;
                assert_eq!(
                    error_kind(io.write(&wire).await)?,
                    io::ErrorKind::ConnectionReset
                );
                assert!(control.bytes()?.is_empty());
                io::ErrorKind::ConnectionReset
            }
            "flush" => {
                io.write_all(&wire).await?;
                control.change(|state| state.flush_error = Some(io::ErrorKind::BrokenPipe))?;
                assert_eq!(error_kind(io.flush().await)?, io::ErrorKind::BrokenPipe);
                assert_eq!(control.bytes()?, wire);
                io::ErrorKind::BrokenPipe
            }
            "zero" => {
                control.change(|state| state.max_write = 0)?;
                assert_eq!(io.write(&[]).await?, 0);
                pending(&mut monitor, 3, false);
                assert_eq!(error_kind(io.write(&wire).await)?, io::ErrorKind::WriteZero);
                assert!(control.bytes()?.is_empty());
                io::ErrorKind::WriteZero
            }
            _ => {
                io.write_all(&wire[..8]).await?;
                io.flush().await?;
                pending(&mut monitor, 3, false);
                io::ErrorKind::BrokenPipe
            }
        };
        if scenario == "drop" {
            drop(io);
            assert!(control.inspect(|state| state.dropped)?);
            assert_eq!(error_kind(joined(waiting).await)?, expected, "{scenario}");
        } else {
            // The error notification itself must wake the task: dropping the
            // writer here first could conceal a missing write/flush wakeup.
            assert_eq!(error_kind(joined(waiting).await)?, expected, "{scenario}");
            assert!(!control.inspect(|state| state.dropped)?);
            drop(io);
            assert!(control.inspect(|state| state.dropped)?);
        }
        assert_eq!(error_kind(receipt(&mut monitor, 3, false).await)?, expected);
    }
    Ok(())
}

#[tokio::test]
async fn cancelled_receipt_does_not_keep_its_old_target() -> io::Result<()> {
    let (writer, _) = Control::new(usize::MAX);
    let (mut io, mut monitor) = TrackingIo::new(writer, Side::Server);
    io.write_all(&frame(0, 0, 1, b"abc")?).await?;
    io.flush().await?;
    {
        let mut cancelled = Box::pin(poll_fn(|cx| monitor.poll_flushed(cx, 999, false)));
        poll_fn(|cx| {
            assert!(cancelled.as_mut().poll(cx).is_pending());
            Poll::Ready(())
        })
        .await;
    }
    receipt(&mut monitor, 3, false).await?;
    {
        let mut cancelled = Box::pin(poll_fn(|cx| monitor.poll_flushed(cx, 4, true)));
        poll_fn(|cx| {
            assert!(cancelled.as_mut().poll(cx).is_pending());
            Poll::Ready(())
        })
        .await;
    }
    io.write_all(&frame(0, 0, 1, b"d")?).await?;
    io.flush().await?;
    pending(&mut monitor, 7, false);
    receipt(&mut monitor, 4, false).await?;
    let waiting = waiter(monitor, 7, false).await?;
    io.write_all(&frame(0, 0, 1, b"efg")?).await?;
    io.flush().await?;
    joined(waiting).await?;
    Ok(())
}

#[tokio::test]
async fn vectored_fallback_uses_scalar_prefix_once_without_changing_bytes() -> io::Result<()> {
    let (writer, control) = Control::new(3);
    let (mut io, mut monitor) = TrackingIo::new(writer, Side::Server);
    assert!(!io.is_write_vectored());
    let mut wire = frame(4, 0, 0, &[])?;
    wire.extend(frame(0, 0, 1, b"payload")?);
    let mut accepted = io.write(&wire).await?;
    while accepted < wire.len() {
        let middle = (accepted + 2).min(wire.len());
        let slices = [
            IoSlice::new(&[]),
            IoSlice::new(&wire[accepted..middle]),
            IoSlice::new(&wire[middle..]),
        ];
        let count = io.write_vectored(&slices).await?;
        assert!((1..=middle - accepted).contains(&count));
        accepted += count;
        assert_eq!(control.bytes()?, wire[..accepted]);
    }
    io.flush().await?;
    receipt(&mut monitor, 7, false).await?;
    pending(&mut monitor, 8, false);
    assert_eq!(control.bytes()?, wire);
    assert_eq!(control.inspect(|state| state.vectored_calls)?, 0);
    assert!(control.inspect(|state| state.scalar_calls)? > 1);
    Ok(())
}

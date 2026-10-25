//! Actual h2 connection-close ownership with a controllable shutdown syscall.
use super::*;
use std::sync::atomic::{AtomicBool, Ordering};
use std::task::Waker;
use tokio::sync::Notify;

#[derive(Default)]
struct Gate {
    entered: AtomicBool,
    open: AtomicBool,
    fail: AtomicBool,
    dropped: AtomicBool,
    changed: Notify,
    wake: Mutex<Option<Waker>>,
}
impl Gate {
    async fn entered(&self) {
        loop {
            let changed = self.changed.notified();
            tokio::pin!(changed);
            changed.as_mut().enable();
            if self.entered.load(Ordering::Acquire) {
                return;
            }
            changed.await;
        }
    }
    fn release(&self) -> io::Result<()> {
        self.open.store(true, Ordering::Release);
        if let Some(wake) = self
            .wake
            .lock()
            .map_err(|_| io::Error::other("shutdown gate poison"))?
            .take()
        {
            wake.wake();
        }
        Ok(())
    }
}
struct HeldShutdown {
    stream: DuplexStream,
    gate: Arc<Gate>,
}
impl AsyncRead for HeldShutdown {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        b: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.stream).poll_read(cx, b)
    }
}
impl AsyncWrite for HeldShutdown {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        b: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.stream).poll_write(cx, b)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.stream).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        self.gate.entered.store(true, Ordering::Release);
        self.gate.changed.notify_waiters();
        if self.gate.fail.load(Ordering::Acquire) {
            return Poll::Ready(Err(io::ErrorKind::BrokenPipe.into()));
        }
        if !self.gate.open.load(Ordering::Acquire) {
            match self.gate.wake.lock() {
                Ok(mut wake) => *wake = Some(cx.waker().clone()),
                Err(_) => return Poll::Ready(Err(io::Error::other("shutdown gate poison"))),
            }
            if !self.gate.open.load(Ordering::Acquire) {
                return Poll::Pending;
            }
        }
        Pin::new(&mut self.stream).poll_shutdown(cx)
    }
}
impl Drop for HeldShutdown {
    fn drop(&mut self) {
        self.gate.dropped.store(true, Ordering::Release);
    }
}

#[derive(Clone, Copy)]
enum Action {
    Release,
    Stop,
    Timeout,
    Error,
}

async fn boundary(action: Action) -> std::result::Result<(), Box<dyn std::error::Error>> {
    let gate = Arc::new(Gate::default());
    let (a, b) = tokio::io::duplex(128 * 1024);
    let (a, b) = tokio::join!(
        client(a, tuple(), short()),
        server(
            HeldShutdown {
                stream: b,
                gate: gate.clone()
            },
            tuple(),
            short()
        )
    );
    let (mut a, mut b) = (a?, b?);
    let operation = async {
        a.io.write_all(b"request").await?;
        a.io.shutdown().await?;
        let mut request = Vec::new();
        b.io.read_to_end(&mut request).await?;
        assert_eq!(request, b"request");
        b.io.write_all(b"reply").await?;
        b.io.shutdown().await?;
        let mut reply = Vec::new();
        a.io.read_to_end(&mut reply).await?;
        assert_eq!(reply, b"reply");
        tokio::time::timeout(Duration::from_secs(2), gate.entered())
            .await
            .map_err(|_| io::Error::other("actual shutdown not entered"))?;
        assert!(!*b.monitor.ended().borrow());
        assert!(!gate.dropped.load(Ordering::Acquire));
        Ok::<(), io::Error>(())
    }
    .await;
    if operation.is_err() {
        a.owner.request_stop();
        b.owner.request_stop();
    } else {
        match action {
            Action::Release => gate.release()?,
            Action::Stop => b.owner.request_stop(),
            Action::Timeout => {}
            Action::Error => {
                gate.fail.store(true, Ordering::Release);
                gate.release()?;
            }
        }
    }
    let (a, b) = tokio::join!(a.owner.join(), b.owner.join());
    // Both original real driver handles are terminal before any assertion.
    operation?;
    let b = b?;
    assert!(gate.dropped.load(Ordering::Acquire));
    assert_eq!(
        b.reason,
        match action {
            Action::Release => LinkEnd::PeerEof,
            Action::Stop => LinkEnd::LocalStop,
            Action::Timeout => LinkEnd::LocalDataStall,
            Action::Error => LinkEnd::Io,
        }
    );
    assert_eq!(a?.reason, LinkEnd::PeerEof);
    Ok(())
}

#[tokio::test]
async fn actual_shutdown_stays_owned_until_its_original_gate_releases()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    boundary(Action::Release).await
}
#[tokio::test]
async fn cooperative_stop_joins_the_pending_actual_network_shutdown()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    boundary(Action::Stop).await
}
#[tokio::test]
async fn actual_shutdown_timeout_is_not_a_healthy_peer_completion()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    boundary(Action::Timeout).await
}
#[tokio::test]
async fn actual_shutdown_io_error_is_not_a_healthy_peer_completion()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    boundary(Action::Error).await
}

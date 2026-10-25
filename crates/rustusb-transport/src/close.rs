//! Keep the real transport alive through its peer's final close.
//!
//! h2 has already selected connection shutdown. Tail bytes are bounded discard,
//! never new application data or logs; actual TLS/transport errors stay errors.
use std::{
    io,
    pin::Pin,
    task::{Context, Poll},
};
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
const TAIL_BUDGET: usize = 65_536;
pub(crate) struct ClosingIo<S> {
    inner: S,
    write_closed: bool,
    read_closed: bool,
    discarded: usize,
    failure: Option<io::ErrorKind>,
}
impl<S> ClosingIo<S> {
    pub(crate) fn new(inner: S) -> Self {
        Self {
            inner,
            write_closed: false,
            read_closed: false,
            discarded: 0,
            failure: None,
        }
    }
}
impl<S: AsyncRead + Unpin> AsyncRead for ClosingIo<S> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        b: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_read(cx, b)
    }
}
impl<S: AsyncRead + AsyncWrite + Unpin> AsyncWrite for ClosingIo<S> {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        b: &[u8],
    ) -> Poll<io::Result<usize>> {
        Pin::new(&mut self.inner).poll_write(cx, b)
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        if let Some(kind) = self.failure {
            return Poll::Ready(Err(io::Error::new(kind, "transport close failed")));
        }
        if self.read_closed {
            return Poll::Ready(Ok(()));
        }
        if !self.write_closed {
            match Pin::new(&mut self.inner).poll_shutdown(cx) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(Err(error)) => {
                    self.failure = Some(error.kind());
                    return Poll::Ready(Err(error));
                }
                Poll::Ready(Ok(())) => self.write_closed = true,
            }
        }
        let mut bytes = [0; 1024];
        for _ in 0..32 {
            let capacity = (TAIL_BUDGET - self.discarded + 1).min(bytes.len());
            let mut read = ReadBuf::new(&mut bytes[..capacity]);
            match Pin::new(&mut self.inner).poll_read(cx, &mut read) {
                Poll::Pending => return Poll::Pending,
                Poll::Ready(Err(error)) => {
                    self.failure = Some(error.kind());
                    return Poll::Ready(Err(error));
                }
                Poll::Ready(Ok(())) => {}
            }
            let count = read.filled().len();
            if count == 0 {
                self.read_closed = true;
                return Poll::Ready(Ok(()));
            }
            if count > TAIL_BUDGET - self.discarded {
                self.failure = Some(io::ErrorKind::InvalidData);
                return Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "transport close tail exceeds bound",
                )));
            }
            self.discarded += count;
        }
        cx.waker().wake_by_ref();
        Poll::Pending
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    #[tokio::test]
    async fn shutdown_waits_for_real_peer_eof_and_is_resumable() -> io::Result<()> {
        let (a, mut b) = tokio::io::duplex(128);
        let mut a = ClosingIo::new(a);
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(10), a.shutdown())
                .await
                .is_err()
        );
        let mut byte = [0];
        assert_eq!(b.read(&mut byte).await?, 0);
        b.write_all(b"known closing tail").await?;
        b.shutdown().await?;
        a.shutdown().await?;
        a.shutdown().await?;
        Ok(())
    }
    #[tokio::test]
    async fn excess_tail_is_an_error_instead_of_clean_close() -> io::Result<()> {
        let (a, mut b) = tokio::io::duplex(TAIL_BUDGET * 2);
        b.write_all(&vec![0; TAIL_BUDGET + 1]).await?;
        b.shutdown().await?;
        let mut a = ClosingIo::new(a);
        assert_eq!(
            a.shutdown().await.err().map(|e| e.kind()),
            Some(io::ErrorKind::InvalidData)
        );
        assert_eq!(
            a.shutdown().await.err().map(|e| e.kind()),
            Some(io::ErrorKind::InvalidData)
        );
        Ok(())
    }
}

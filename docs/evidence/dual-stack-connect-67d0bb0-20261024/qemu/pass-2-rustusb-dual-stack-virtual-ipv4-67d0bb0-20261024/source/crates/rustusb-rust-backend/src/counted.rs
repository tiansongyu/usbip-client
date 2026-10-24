//! Payload-free counters at the plaintext USB/IP application boundary.
use crate::monitor::Counters;
use std::{
    io,
    pin::Pin,
    sync::Arc,
    task::{Context, Poll},
};
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
pub(crate) struct Counted<S> {
    inner: S,
    counters: Arc<Counters>,
}
impl<S> Counted<S> {
    pub(crate) fn new(inner: S, counters: Arc<Counters>) -> Self {
        Self { inner, counters }
    }
}
impl<S: AsyncRead + Unpin> AsyncRead for Counted<S> {
    fn poll_read(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        buf: &mut ReadBuf<'_>,
    ) -> Poll<io::Result<()>> {
        let before = buf.filled().len();
        let result = Pin::new(&mut self.inner).poll_read(cx, buf);
        if matches!(result, Poll::Ready(Ok(()))) {
            self.counters.received(buf.filled().len() - before);
        }
        result
    }
}
impl<S: AsyncWrite + Unpin> AsyncWrite for Counted<S> {
    fn poll_write(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<io::Result<usize>> {
        let result = Pin::new(&mut self.inner).poll_write(cx, bytes);
        if let Poll::Ready(Ok(count)) = result {
            self.counters.sent(count);
        }
        result
    }
    fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_flush(cx)
    }
    fn poll_shutdown(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<io::Result<()>> {
        Pin::new(&mut self.inner).poll_shutdown(cx)
    }
    fn is_write_vectored(&self) -> bool {
        self.inner.is_write_vectored()
    }
    fn poll_write_vectored(
        mut self: Pin<&mut Self>,
        cx: &mut Context<'_>,
        bytes: &[io::IoSlice<'_>],
    ) -> Poll<io::Result<usize>> {
        let result = Pin::new(&mut self.inner).poll_write_vectored(cx, bytes);
        if let Poll::Ready(Ok(count)) = result {
            self.counters.sent(count);
        }
        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    struct PartialThenError(bool);
    impl AsyncWrite for PartialThenError {
        fn poll_write(
            mut self: Pin<&mut Self>,
            _: &mut Context<'_>,
            bytes: &[u8],
        ) -> Poll<io::Result<usize>> {
            if self.0 {
                Poll::Ready(Err(io::Error::new(
                    io::ErrorKind::BrokenPipe,
                    "fixture write",
                )))
            } else {
                self.0 = true;
                Poll::Ready(Ok(bytes.len().min(3)))
            }
        }
        fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "fixture flush",
            )))
        }
        fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> {
            Poll::Ready(Ok(()))
        }
    }
    #[tokio::test]
    async fn partial_success_is_retained_when_following_write_or_flush_fails() {
        let counters = Counters::new();
        counters.enable();
        let mut io = Counted::new(PartialThenError(false), counters.clone());
        assert!(io.write_all(b"longer than three").await.is_err());
        assert!(io.flush().await.is_err());
        assert_eq!(counters.snapshot().app_tx_bytes, 3);
    }
    #[tokio::test]
    async fn management_bytes_are_excluded_and_only_successful_reads_are_counted() -> io::Result<()>
    {
        let counters = Counters::new();
        let (stream, mut peer) = tokio::io::duplex(32);
        let mut io = Counted::new(stream, counters.clone());
        peer.write_all(b"management").await?;
        let mut excluded = [0; 10];
        io.read_exact(&mut excluded).await?;
        io.write_all(b"reply").await?;
        assert_eq!(
            (
                counters.snapshot().app_rx_bytes,
                counters.snapshot().app_tx_bytes
            ),
            (0, 0)
        );
        counters.enable();
        peer.write_all(b"urb").await?;
        drop(peer);
        let mut bytes = Vec::new();
        io.read_to_end(&mut bytes).await?;
        assert_eq!(bytes, b"urb");
        assert_eq!(counters.snapshot().app_rx_bytes, 3);
        assert!(io.write_all(b"closed").await.is_err());
        assert_eq!(counters.snapshot().app_tx_bytes, 0);
        Ok(())
    }
}

//! Count only successful application reads/writes; retain no USB payload.

use crate::model::Metrics;
use rustusb_core::Result;
use std::{
    sync::{
        Arc,
        atomic::{AtomicBool, AtomicU64, Ordering},
    },
    time::Instant,
};
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};

pub(super) struct Counters {
    received: AtomicU64,
    sent: AtomicU64,
    saturated: AtomicBool,
    start: Instant,
}
impl Counters {
    pub(super) fn new() -> Arc<Self> {
        Arc::new(Self {
            received: AtomicU64::new(0),
            sent: AtomicU64::new(0),
            saturated: AtomicBool::new(false),
            start: Instant::now(),
        })
    }
    fn add(&self, counter: &AtomicU64, count: usize) {
        let amount = u64::try_from(count).unwrap_or(u64::MAX);
        let mut old = counter.load(Ordering::Relaxed);
        loop {
            let next = old.saturating_add(amount);
            match counter.compare_exchange_weak(old, next, Ordering::Relaxed, Ordering::Relaxed) {
                Ok(_) => {
                    if old.checked_add(amount).is_none() {
                        self.saturated.store(true, Ordering::Relaxed);
                    }
                    break;
                }
                Err(current) => old = current,
            }
        }
    }
    pub(super) fn snapshot(&self) -> Metrics {
        Metrics {
            app_rx_bytes: self.received.load(Ordering::Relaxed),
            app_tx_bytes: self.sent.load(Ordering::Relaxed),
            saturated: self.saturated.load(Ordering::Relaxed),
            elapsed_millis: u64::try_from(self.start.elapsed().as_millis()).unwrap_or(u64::MAX),
        }
    }
}

async fn direction(
    reader: &mut (impl AsyncRead + Unpin),
    writer: &mut (impl AsyncWrite + Unpin),
    counters: &Counters,
    incoming: bool,
) -> Result<()> {
    let mut bytes = [0u8; 16 * 1024];
    loop {
        let count = reader.read(&mut bytes).await?;
        if incoming {
            counters.add(&counters.received, count);
        }
        if count == 0 {
            return Ok(());
        }
        let mut offset = 0;
        while offset < count {
            let written = writer.write(&bytes[offset..count]).await?;
            if written == 0 {
                return Err(std::io::Error::from(std::io::ErrorKind::WriteZero).into());
            }
            if !incoming {
                counters.add(&counters.sent, written);
            }
            offset += written;
        }
        writer.flush().await?;
    }
}

pub(super) async fn relay<A, B>(network: A, kernel: B, counters: Arc<Counters>) -> Result<()>
where
    A: AsyncRead + AsyncWrite + Unpin,
    B: AsyncRead + AsyncWrite + Unpin,
{
    let (mut read_net, mut write_net) = tokio::io::split(network);
    let (mut read_kernel, mut write_kernel) = tokio::io::split(kernel);
    tokio::select! {
        result = direction(&mut read_net, &mut write_kernel, &counters, true) => result,
        result = direction(&mut read_kernel, &mut write_net, &counters, false) => result,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[tokio::test]
    async fn real_duplex_bytes_are_counted_and_first_eof_closes_both_sides() -> Result<()> {
        let (network, mut peer) = tokio::io::duplex(8);
        let (kernel, mut device) = tokio::io::duplex(8);
        let counters = Counters::new();
        let observed = counters.clone();
        let task = tokio::spawn(relay(network, kernel, counters));
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            peer.write_all(b"request").await?;
            let mut request = [0u8; 7];
            device.read_exact(&mut request).await?;
            assert_eq!(&request, b"request");
            device.write_all(b"reply").await?;
            let mut reply = [0u8; 5];
            peer.read_exact(&mut reply).await?;
            assert_eq!(&reply, b"reply");
            drop(peer);
            task.await
                .map_err(|_| rustusb_core::Error::State("relay fixture task".into()))??;
            let mut tail = [0u8; 1];
            assert_eq!(device.read(&mut tail).await?, 0);
            let metrics = observed.snapshot();
            assert_eq!((metrics.app_rx_bytes, metrics.app_tx_bytes), (7, 5));
            Ok::<_, rustusb_core::Error>(())
        })
        .await
        .map_err(|_| rustusb_core::Error::Timeout("relay fixture".into()))?
    }
}

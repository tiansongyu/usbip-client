//! Bounded system-name resolution and owned, staggered TCP address attempts.
//!
//! The caller supplies the operation deadline and cancellation boundary. Dropping
//! this future closes every TCP candidate. An already running system resolver is
//! not cancellable; its actual-work permit remains held until it really returns.

use rustusb_core::{Error, Peer, Result};
use std::{
    collections::VecDeque,
    future::{Future, poll_fn},
    io,
    net::{IpAddr, SocketAddr, ToSocketAddrs},
    pin::Pin,
    sync::{Arc, LazyLock},
    task::Poll,
    time::Duration,
};
use tokio::{net::TcpStream, sync::Semaphore, time::Instant};

const MAX_RAW_ADDRESSES: usize = 64;
const MAX_ADDRESSES: usize = 16;
const ATTEMPT_DELAY: Duration = Duration::from_millis(250);
const FAILURE_DELAY: Duration = Duration::from_millis(100);
static RESOLVERS: LazyLock<Arc<Semaphore>> = LazyLock::new(|| Arc::new(Semaphore::new(4)));

/// Connect to one validated endpoint using bounded, staggered TCP candidates.
///
/// IP literals bypass the four-work system resolver budget. Hostname answers are
/// limited to 64 raw and 16 unique socket addresses; overflow fails before TCP.
/// Address families interleave from the OS-preferred family. New attempts start
/// after 250ms, shortened to at least 100ms following a definitive TCP failure.
/// Only the TCP winner is returned; no TLS or USB operation runs on losing sockets.
///
/// Wrap the entire DNS/TCP/TLS/application operation in its original deadline;
/// this helper does not refresh it or impose a premature per-address timeout.
/// Cancelled resolver waiters cannot free actual blocking-work capacity. The
/// retained-address limits do not bound libc allocation or OS lookup duration.
pub async fn connect(peer: &Peer) -> Result<TcpStream> {
    let addresses = resolve_peer(peer, Arc::clone(&RESOLVERS)).await?;
    race(addresses, |address| TcpStream::connect(address))
        .await
        .map_err(Error::Io)
}

async fn resolve_peer(peer: &Peer, budget: Arc<Semaphore>) -> Result<Vec<SocketAddr>> {
    if let Ok(address) = peer.host.as_str().parse::<IpAddr>() {
        return Ok(vec![SocketAddr::new(address, peer.port.get())]);
    }
    let hostname = peer.host.as_str().to_owned();
    let port = peer.port.get();
    resolve_with(budget, move || (hostname.as_str(), port).to_socket_addrs()).await
}

async fn resolve_with<F, I>(budget: Arc<Semaphore>, lookup: F) -> Result<Vec<SocketAddr>>
where
    F: FnOnce() -> io::Result<I> + Send + 'static,
    I: Iterator<Item = SocketAddr> + Send + 'static,
{
    let permit = budget
        .try_acquire_owned()
        .map_err(|_| Error::State("endpoint resolver capacity exhausted".into()))?;
    tokio::task::spawn_blocking(move || {
        // A cancelled waiter drops only its JoinHandle. The real closure owns
        // this permit until its resolver and answer validation actually finish.
        let _actual_work_permit = permit;
        ordered(lookup()?)
    })
    .await
    .map_err(|_| Error::State("endpoint resolver worker failed".into()))?
    .map_err(Error::Io)
}

fn ordered(addresses: impl Iterator<Item = SocketAddr>) -> io::Result<Vec<SocketAddr>> {
    let mut unique = Vec::new();
    for (index, address) in addresses.take(MAX_RAW_ADDRESSES + 1).enumerate() {
        if index == MAX_RAW_ADDRESSES {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "endpoint address count exceeds bound",
            ));
        }
        if !unique.contains(&address) {
            if unique.len() == MAX_ADDRESSES {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "endpoint unique address count exceeds bound",
                ));
            }
            unique.push(address);
        }
    }
    let preferred_v6 = unique
        .first()
        .ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::AddrNotAvailable,
                "endpoint resolved no addresses",
            )
        })?
        .is_ipv6();
    let (mut first, mut second): (VecDeque<_>, VecDeque<_>) = unique
        .into_iter()
        .partition(|address| address.is_ipv6() == preferred_v6);
    let mut result = Vec::with_capacity(first.len() + second.len());
    while !first.is_empty() || !second.is_empty() {
        if let Some(address) = first.pop_front() {
            result.push(address);
        }
        if let Some(address) = second.pop_front() {
            result.push(address);
        }
    }
    Ok(result)
}

async fn race<D, F, T>(addresses: Vec<SocketAddr>, mut dial: D) -> io::Result<T>
where
    D: FnMut(SocketAddr) -> F,
    F: Future<Output = io::Result<T>> + Send,
{
    // Only the checked literal/ordered resolver path reaches this helper.
    if addresses.is_empty() || addresses.len() > MAX_ADDRESSES {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "endpoint candidate count invalid",
        ));
    }
    let mut addresses = addresses.into_iter();
    let mut pending: Vec<Pin<Box<F>>> = Vec::with_capacity(MAX_ADDRESSES);
    let mut last_error = None;
    let mut last_started = None;
    let mut next_start = Instant::now();
    let mut timer = Box::pin(tokio::time::sleep_until(next_start));
    let mut remaining = true;
    let outcome = poll_fn(|cx| {
        loop {
            let mut index = 0;
            let mut failed = false;
            while index < pending.len() {
                match pending[index].as_mut().poll(cx) {
                    Poll::Ready(Ok(stream)) => return Poll::Ready(Ok(stream)),
                    Poll::Ready(Err(error)) => {
                        // Drop the completed future, preserving the actual OS error.
                        drop(pending.remove(index));
                        last_error = Some(error);
                        failed = true;
                    }
                    Poll::Pending => index += 1,
                }
            }
            if failed && let Some(started) = last_started {
                next_start = next_start.min(started + FAILURE_DELAY);
            }
            if remaining && Instant::now() >= next_start {
                if let Some(address) = addresses.next() {
                    let started = Instant::now();
                    pending.push(Box::pin(dial(address)));
                    last_started = Some(started);
                    next_start = started + ATTEMPT_DELAY;
                    remaining = addresses.len() != 0;
                    // Poll the newly owned candidate before considering another.
                    continue;
                }
                remaining = false;
            }
            if pending.is_empty() && !remaining {
                return Poll::Ready(Err(last_error.take().unwrap_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::AddrNotAvailable,
                        "endpoint has no TCP candidate",
                    )
                })));
            }
            if remaining {
                timer.as_mut().reset(next_start);
                if timer.as_mut().poll(cx).is_ready() {
                    continue;
                }
            }
            return Poll::Pending;
        }
    })
    .await;
    // The caller cannot obtain the winner while another candidate remains owned.
    // Dropping the outer future performs the same inline destruction on cancel.
    drop(pending);
    outcome
}

#[cfg(test)]
mod tests;

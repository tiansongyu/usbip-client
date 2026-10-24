use super::*;
use rustusb_core::Host;
use std::{
    num::NonZeroU16,
    sync::{
        Condvar, Mutex,
        atomic::{AtomicUsize, Ordering},
    },
};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::{TcpListener, TcpSocket},
};

fn address(value: &str) -> io::Result<SocketAddr> {
    value
        .parse()
        .map_err(|_| io::Error::other("test socket address"))
}
fn peer(host: &str, port: u16) -> Result<Peer> {
    Ok(Peer {
        host: Host::new(host)?,
        port: NonZeroU16::new(port).ok_or_else(|| Error::State("test port zero".into()))?,
    })
}
fn state(message: &'static str) -> Error {
    Error::State(message.into())
}

#[test]
fn bounded_answers_preserve_preference_order_scope_and_deduplication() -> Result<()> {
    let values = [
        "[::1]:3241",
        "[::2]:3241",
        "127.0.0.1:3241",
        "127.0.0.2:3241",
        "[::1]:3241",
    ]
    .map(address)
    .into_iter()
    .collect::<io::Result<Vec<_>>>()?;
    let result = ordered(values.into_iter())?;
    assert_eq!(
        result,
        [
            address("[::1]:3241")?,
            address("127.0.0.1:3241")?,
            address("[::2]:3241")?,
            address("127.0.0.2:3241")?
        ]
    );
    let values = ["127.0.0.1:3241", "127.0.0.2:3241", "[::1]:3241"]
        .map(address)
        .into_iter()
        .collect::<io::Result<Vec<_>>>()?;
    assert_eq!(
        ordered(values.into_iter())?,
        [
            address("127.0.0.1:3241")?,
            address("[::1]:3241")?,
            address("127.0.0.2:3241")?
        ]
    );
    let scoped = SocketAddr::V6(std::net::SocketAddrV6::new(
        std::net::Ipv6Addr::LOCALHOST,
        3241,
        7,
        8,
    ));
    assert_eq!(ordered([scoped].into_iter())?, vec![scoped]);
    let one = address("127.0.0.1:3241")?;
    assert_eq!(ordered(std::iter::repeat_n(one, 64))?, vec![one]);
    assert!(ordered(std::iter::repeat_n(one, 65)).is_err());
    assert!(ordered(std::iter::empty()).is_err());
    assert!(ordered((1..=17).map(|n| SocketAddr::from(([127, 0, 0, 1], n)))).is_err());
    assert_eq!(
        ordered((1..=16).map(|n| SocketAddr::from(([127, 0, 0, 1], n))))?.len(),
        16
    );
    Ok(())
}

#[tokio::test]
async fn actual_literal_and_system_hostname_connections_transfer_bytes() -> Result<()> {
    for host in ["127.0.0.1", "localhost"] {
        let listener = TcpListener::bind("127.0.0.1:0").await?;
        let target = peer(host, listener.local_addr()?.port())?;
        let (outgoing, incoming) = tokio::join!(
            tokio::time::timeout(Duration::from_secs(3), connect(&target)),
            listener.accept()
        );
        let mut outgoing = outgoing.map_err(|_| state("actual hostname connect timeout"))??;
        let (mut incoming, _) = incoming?;
        outgoing.write_all(b"bounded-connector").await?;
        let mut bytes = [0; 17];
        incoming.read_exact(&mut bytes).await?;
        assert_eq!(&bytes, b"bounded-connector");
    }
    Ok(())
}

#[cfg(target_os = "linux")]
#[tokio::test]
async fn real_full_accept_queue_does_not_hide_healthy_later_candidate() -> Result<()> {
    let socket = TcpSocket::new_v4()?;
    socket.bind(address("127.0.0.1:0")?)?;
    let blocked = socket.listen(1)?;
    let first = blocked.local_addr()?;
    let mut held = Vec::new();
    let mut observed_pending = false;
    for _ in 0..16 {
        match tokio::time::timeout(Duration::from_millis(100), TcpStream::connect(first)).await {
            Ok(Ok(stream)) => held.push(stream),
            Ok(Err(error)) => return Err(error.into()),
            Err(_) => {
                observed_pending = true;
                break;
            }
        }
    }
    assert!(
        observed_pending,
        "fixture must establish an actual pending first TCP address"
    );
    let healthy = TcpListener::bind("127.0.0.1:0").await?;
    let result = tokio::time::timeout(
        Duration::from_secs(2),
        race(vec![first, healthy.local_addr()?], TcpStream::connect),
    )
    .await
    .map_err(|_| state("healthy second candidate was not attempted"))??;
    let (mut accepted, _) = healthy.accept().await?;
    drop(result);
    assert_eq!(accepted.read(&mut [0]).await?, 0);
    drop(held);
    drop(blocked);
    Ok(())
}

#[tokio::test(start_paused = true)]
async fn launch_spacing_and_failure_acceleration_have_exact_minima() -> Result<()> {
    let addresses = vec![address("[::1]:3241")?, address("127.0.0.1:3241")?];
    for fail in [false, true] {
        let starts = Arc::new(Mutex::new(Vec::new()));
        let capture = Arc::clone(&starts);
        let start = Instant::now();
        let winner = race(addresses.clone(), move |address| {
            if let Ok(mut rows) = capture.lock() {
                rows.push((address, Instant::now()));
            }
            async move {
                if address.is_ipv6() {
                    if fail {
                        Err(io::Error::from(io::ErrorKind::ConnectionRefused))
                    } else {
                        std::future::pending().await
                    }
                } else {
                    Ok(17_u8)
                }
            }
        })
        .await?;
        assert_eq!(winner, 17);
        let rows = starts
            .lock()
            .map_err(|_| state("test clock capture poisoned"))?;
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].1, start);
        assert_eq!(
            rows[1].1 - start,
            if fail { FAILURE_DELAY } else { ATTEMPT_DELAY }
        );
    }
    Ok(())
}

struct DropReceipt(Arc<AtomicUsize>);
impl Drop for DropReceipt {
    fn drop(&mut self) {
        self.0.fetch_add(1, Ordering::SeqCst);
    }
}

#[tokio::test(start_paused = true)]
async fn ready_winner_drops_all_owned_losers_before_returning() -> Result<()> {
    let dropped = Arc::new(AtomicUsize::new(0));
    let counts = Arc::clone(&dropped);
    let starts = Arc::new(AtomicUsize::new(0));
    let launched = Arc::clone(&starts);
    let addresses = vec![
        address("[::1]:3241")?,
        address("127.0.0.1:3241")?,
        address("127.0.0.2:3241")?,
    ];
    let winner = race(addresses, move |_| {
        let index = launched.fetch_add(1, Ordering::SeqCst);
        let receipt = DropReceipt(Arc::clone(&counts));
        async move {
            tokio::time::sleep(Duration::from_millis(if index == 0 { 300 } else { 50 })).await;
            Ok(receipt)
        }
    })
    .await?;
    assert_eq!(starts.load(Ordering::SeqCst), 2);
    assert_eq!(dropped.load(Ordering::SeqCst), 1);
    drop(winner);
    assert_eq!(dropped.load(Ordering::SeqCst), 2);
    Ok(())
}

async fn socket_pair() -> Result<(TcpStream, TcpStream)> {
    let listener = TcpListener::bind("127.0.0.1:0").await?;
    let (outgoing, incoming) = tokio::join!(
        TcpStream::connect(listener.local_addr()?),
        listener.accept()
    );
    Ok((outgoing?, incoming?.0))
}

#[tokio::test]
async fn cancelling_race_closes_each_actual_owned_socket() -> Result<()> {
    let (first, mut peer_one) = socket_pair().await?;
    let (second, mut peer_two) = socket_pair().await?;
    let mut streams = VecDeque::from([first, second]);
    let started = Arc::new(AtomicUsize::new(0));
    let captured = Arc::clone(&started);
    tokio::time::pause();
    let outcome = tokio::time::timeout(
        Duration::from_millis(500),
        race(
            vec![address("[::1]:3241")?, address("127.0.0.1:3241")?],
            move |_| {
                captured.fetch_add(1, Ordering::SeqCst);
                let stream = streams.pop_front();
                async move {
                    let _owned = stream;
                    std::future::pending::<io::Result<TcpStream>>().await
                }
            },
        ),
    )
    .await;
    tokio::time::resume();
    assert!(outcome.is_err());
    assert_eq!(started.load(Ordering::SeqCst), 2);
    for peer in [&mut peer_one, &mut peer_two] {
        let read = tokio::time::timeout(Duration::from_secs(1), peer.read(&mut [0]))
            .await
            .map_err(|_| state("cancelled candidate socket still open"))??;
        assert_eq!(read, 0);
    }
    Ok(())
}

struct ResolverLatch {
    released: Mutex<bool>,
    wake: Condvar,
}
impl ResolverLatch {
    fn release(&self) {
        let mut value = match self.released.lock() {
            Ok(value) => value,
            Err(error) => error.into_inner(),
        };
        *value = true;
        self.wake.notify_all();
    }
    fn wait(&self) -> io::Result<()> {
        let value = self
            .released
            .lock()
            .map_err(|_| io::Error::other("test resolver latch"))?;
        let (_guard, timeout) = self
            .wake
            .wait_timeout_while(value, Duration::from_secs(5), |released| !*released)
            .map_err(|_| io::Error::other("test resolver wait"))?;
        if timeout.timed_out() {
            return Err(io::Error::other("bounded test resolver wait expired"));
        }
        Ok(())
    }
}
struct ReleaseOnDrop(Arc<ResolverLatch>);
impl Drop for ReleaseOnDrop {
    fn drop(&mut self) {
        self.0.release();
    }
}

#[tokio::test]
async fn cancelled_waiters_cannot_reuse_actual_resolver_capacity() -> Result<()> {
    let latch = Arc::new(ResolverLatch {
        released: Mutex::new(false),
        wake: Condvar::new(),
    });
    let _release = ReleaseOnDrop(Arc::clone(&latch));
    let budget = Arc::new(Semaphore::new(4));
    let (ready, mut started) = tokio::sync::mpsc::channel(4);
    let mut waiters = Vec::new();
    let result_address = address("127.0.0.1:3241")?;
    for _ in 0..4 {
        let gate = Arc::clone(&latch);
        let ready = ready.clone();
        let budget = Arc::clone(&budget);
        waiters.push(tokio::spawn(async move {
            resolve_with(budget, move || {
                ready
                    .blocking_send(())
                    .map_err(|_| io::Error::other("test resolver readiness"))?;
                gate.wait()?;
                Ok([result_address].into_iter())
            })
            .await
        }));
    }
    for _ in 0..4 {
        tokio::time::timeout(Duration::from_secs(2), started.recv())
            .await
            .map_err(|_| state("actual resolver did not start"))?
            .ok_or_else(|| state("test readiness closed"))?;
    }
    for waiter in &waiters {
        waiter.abort();
    }
    for waiter in waiters {
        assert!(matches!(waiter.await,Err(error) if error.is_cancelled()));
    }
    assert_eq!(budget.available_permits(), 0);
    let fifth = resolve_with(
        Arc::clone(&budget),
        move || Ok([result_address].into_iter()),
    )
    .await;
    assert!(
        matches!(fifth,Err(Error::State(ref message)) if message=="endpoint resolver capacity exhausted")
    );
    assert_eq!(
        resolve_peer(&peer("127.0.0.1", 3241)?, Arc::clone(&budget)).await?,
        vec![result_address]
    );
    latch.release();
    let all = tokio::time::timeout(
        Duration::from_secs(2),
        Arc::clone(&budget).acquire_many_owned(4),
    )
    .await
    .map_err(|_| state("actual resolver permits not returned"))?
    .map_err(|_| state("test budget closed"))?;
    drop(all);
    assert_eq!(budget.available_permits(), 4);
    Ok(())
}

#[tokio::test(start_paused = true)]
async fn all_tcp_failures_keep_the_os_error_and_pace_attempts() -> Result<()> {
    let start = Instant::now();
    let addresses = vec![
        address("127.0.0.1:1")?,
        address("127.0.0.1:2")?,
        address("127.0.0.1:3")?,
    ];
    let result = race(addresses, |_| {
        std::future::ready(Err::<u8, _>(io::Error::from_raw_os_error(111)))
    })
    .await;
    assert_eq!(
        result.err().and_then(|error| error.raw_os_error()),
        Some(111)
    );
    assert_eq!(Instant::now() - start, 2 * FAILURE_DELAY);
    assert!(
        race(Vec::new(), |_| std::future::ready(Ok(0_u8)))
            .await
            .is_err()
    );
    Ok(())
}

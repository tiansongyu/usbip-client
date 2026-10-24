use std::{error::Error, io, net::ToSocketAddrs, sync::{Arc, mpsc}, time::Duration};
use tokio::{net::{TcpListener, TcpSocket, TcpStream}, sync::{Semaphore, oneshot}, time::timeout};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

async fn sequential_loopback() -> Result<()> {
    // No firewall or host configuration: an ordinary loopback listener with a
    // full accept queue leaves the next SYN unanswered on this Linux host.
    let socket = TcpSocket::new_v4()?;
    socket.bind("127.0.0.1:0".parse()?)?;
    let saturated = socket.listen(1)?;
    let blocked_address = saturated.local_addr()?;
    let mut held = Vec::new();
    for _ in 0..2 {
        held.push(timeout(Duration::from_secs(1), TcpStream::connect(blocked_address)).await??);
    }
    let healthy = TcpListener::bind("127.0.0.1:0").await?;
    let healthy_address = healthy.local_addr()?;
    let candidates = [blocked_address, healthy_address];
    let sequential = timeout(Duration::from_millis(200), TcpStream::connect(&candidates[..])).await;
    if sequential.is_ok() {
        return Err(io::Error::other("full-queue first candidate did not remain pending").into());
    }
    if timeout(Duration::from_millis(20), healthy.accept()).await.is_ok() {
        return Err(io::Error::other("sequential connect unexpectedly attempted healthy second candidate").into());
    }
    let direct = timeout(Duration::from_secs(1), TcpStream::connect(healthy_address)).await??;
    let accepted = timeout(Duration::from_secs(1), healthy.accept()).await??;
    drop((direct, accepted, held, saturated, healthy));
    println!("sequential_loopback PASS first_full_accept_queue=true deadline_ms=200 healthy_second_not_attempted=true same_healthy_direct_connect=true");
    Ok(())
}

async fn actual_worker_quota() -> Result<()> {
    let budget = Arc::new(Semaphore::new(4));
    let mut waiters = Vec::new();
    let mut entered = Vec::new();
    let mut release = Vec::new();
    for _ in 0..4 {
        let permit = Arc::clone(&budget).try_acquire_owned()?;
        let (entered_tx, entered_rx) = oneshot::channel();
        let (release_tx, release_rx) = mpsc::channel();
        release.push(release_tx);
        entered.push(entered_rx);
        waiters.push(tokio::spawn(async move {
            tokio::task::spawn_blocking(move || {
                let _actual_work_permit = permit;
                let _ = entered_tx.send(());
                let _ = release_rx.recv();
            }).await
        }));
    }
    for signal in entered {
        timeout(Duration::from_secs(2), signal).await??;
    }
    if Arc::clone(&budget).try_acquire_owned().is_ok() {
        return Err(io::Error::other("fifth resolver slot unexpectedly available").into());
    }
    for waiter in waiters {
        waiter.abort();
        let aborted = waiter.await;
        if !aborted.is_err_and(|error| error.is_cancelled()) {
            return Err(io::Error::other("async waiter did not actually cancel").into());
        }
    }
    if budget.available_permits() != 0 || Arc::clone(&budget).try_acquire_owned().is_ok() {
        return Err(io::Error::other("waiter cancellation released actual blocking work quota").into());
    }
    for signal in release {
        signal.send(())?;
    }
    timeout(Duration::from_secs(2), async {
        while budget.available_permits() != 4 {
            tokio::time::sleep(Duration::from_millis(1)).await;
        }
    }).await?;
    println!("actual_worker_quota PASS started=4 fifth_denied=true cancelled_async_waiters=4 permits_while_workers_blocked=0 permits_after_actual_completion=4");
    Ok(())
}

#[tokio::main]
async fn main() -> Result<()> {
    sequential_loopback().await?;
    actual_worker_quota().await?;
    let addresses = tokio::task::spawn_blocking(|| ("localhost", 3241).to_socket_addrs().map(|x|x.collect::<Vec<_>>())).await??;
    if addresses.is_empty() { return Err(io::Error::other("localhost lookup empty").into()); }
    println!("actual_std_lookup PASS host=localhost answer_count={}", addresses.len());
    println!("RESULT PASS isolated_library_probe_not_product_or_USB_acceptance=true");
    Ok(())
}

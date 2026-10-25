//! Software h2/duplex contract tests, without USB, helper, or trusted-file claims.

use super::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[path = "quiet_tests.rs"]
mod quiet_tests;

#[path = "close_tests.rs"]
mod close_tests;

pub(super) fn tuple() -> TcpTuple {
    use std::net::{Ipv4Addr, SocketAddrV4};
    TcpTuple {
        local: SocketAddrV4::new(Ipv4Addr::LOCALHOST, 12345).into(),
        peer: SocketAddrV4::new(Ipv4Addr::LOCALHOST, 23456).into(),
    }
}
pub(super) fn short() -> ProbePolicy {
    ProbePolicy {
        interval: Duration::from_millis(20),
        response_timeout: Duration::from_millis(500),
    }
}

async fn pair() -> std::result::Result<(Link, Link), Box<dyn std::error::Error>> {
    let (a, b) = tokio::io::duplex(128 * 1024);
    let (a, b) = tokio::try_join!(client(a, tuple(), short()), server(b, tuple(), short()))?;
    Ok((a, b))
}

async fn observed(
    monitor: &LinkMonitor,
    predicate: impl Fn(LinkHealth) -> bool,
) -> std::result::Result<LinkHealth, Box<dyn std::error::Error>> {
    let mut changes = monitor.changes();
    Ok(tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            let snapshot = monitor.snapshot()?;
            if predicate(snapshot) {
                return Ok::<_, Error>(snapshot);
            }
            changes
                .changed()
                .await
                .map_err(|_| Error::LinkDriverFailed)?;
        }
    })
    .await??)
}

async fn stop(a: Link, b: Link) -> std::result::Result<(), Box<dyn std::error::Error>> {
    a.owner.request_stop();
    b.owner.request_stop();
    let (a, b) = tokio::time::timeout(Duration::from_secs(3), async {
        tokio::join!(a.owner.join(), b.owner.join())
    })
    .await?;
    a?;
    b?;
    Ok(())
}

#[test]
fn policy_has_fixed_production_bounds() {
    assert!(ProbePolicy::new(Duration::from_millis(999), Duration::from_secs(15)).is_err());
    assert!(ProbePolicy::new(Duration::from_secs(61), Duration::from_secs(180)).is_err());
    assert!(ProbePolicy::new(Duration::from_secs(5), Duration::from_secs(9)).is_err());
    assert!(ProbePolicy::new(Duration::from_secs(5), Duration::from_secs(301)).is_err());
    assert_eq!(ProbePolicy::default().interval(), Duration::from_secs(5));
    assert_eq!(
        ProbePolicy::default().response_timeout(),
        Duration::from_secs(15)
    );
}

#[tokio::test]
async fn real_ping_continues_at_full_window_but_credit_waits_for_actual_reads()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (mut a, mut b) = pair().await?;
        tokio::time::timeout(Duration::from_secs(3), async {
            a.io.write_all(&vec![0x5a; WINDOW as usize]).await?;
            a.io.flush().await
        })
        .await??;
        let full = observed(&b.monitor, |h| {
            h.stats.is_some_and(|s| s.receive_pending_bytes == WINDOW)
                && h.last_reply_sequence.is_some()
        })
        .await?;
        assert_eq!(full.stats.map(|s| s.app_read_bytes), Some(0));
        assert!(full.last_round_trip_micros.is_some());
        assert!(
            tokio::time::timeout(Duration::from_millis(40), a.io.write_all(&[0x31]))
                .await
                .is_err()
        );
        let mut read = vec![0; 32_768];
        b.io.read_exact(&mut read).await?;
        assert!(read.iter().all(|b| *b == 0x5a));
        tokio::time::timeout(Duration::from_secs(3), a.io.write_all(&[0x31])).await??;
        observed(&b.monitor, |h| {
            h.stats.is_some_and(|s| s.app_read_bytes == 32_768)
        })
        .await?;
        stop(a, b).await
    })
    .await?
}

#[tokio::test]
async fn half_close_keeps_cached_bytes_and_reverse_direction_until_actual_consumption()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (mut a, mut b) = pair().await?;
        a.io.write_all(b"three").await?;
        a.io.shutdown().await?;
        observed(&b.monitor, |h| {
            h.phase == LinkPhase::Draining && h.stats.is_some_and(|s| s.receive_pending_bytes == 5)
        })
        .await?;
        assert!(!*b.monitor.ended().borrow());
        let mut received = Vec::new();
        b.io.read_to_end(&mut received).await?;
        assert_eq!(received, b"three");
        b.io.write_all(b"reverse").await?;
        b.io.shutdown().await?;
        let mut reverse = Vec::new();
        a.io.read_to_end(&mut reverse).await?;
        assert_eq!(reverse, b"reverse");
        let (a, b) = tokio::time::timeout(Duration::from_secs(3), async {
            tokio::join!(a.owner.join(), b.owner.join())
        })
        .await?;
        assert_eq!(a?.reason, LinkEnd::PeerEof);
        assert_eq!(b?.reason, LinkEnd::PeerEof);
        Ok(())
    })
    .await?
}

/// Raw peer completes only fixed HTTP/2 setup, then deliberately ignores PING.
/// This is a same-connection protocol test, not a network blackhole/hardware test.
pub(super) async fn silent_peer(mut io: DuplexStream, unknown_ack: bool) -> io::Result<()> {
    let mut preface = [0; 24];
    io.read_exact(&mut preface).await?;
    if &preface != b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" {
        return Err(failed());
    }
    let mut connected = false;
    while !connected {
        let mut header = [0; 9];
        io.read_exact(&mut header).await?;
        let length =
            (usize::from(header[0]) << 16) | (usize::from(header[1]) << 8) | usize::from(header[2]);
        if length > FRAME {
            return Err(failed());
        }
        let mut body = vec![0; length];
        io.read_exact(&mut body).await?;
        match header[3] {
            4 if header[4] & 1 == 0 => {
                io.write_all(&[0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 4, 1, 0, 0, 0, 0])
                    .await?;
            }
            1 => {
                // HPACK indexed :status=200; no END_STREAM.
                io.write_all(&[0, 0, 1, 1, 4, 0, 0, 0, 1, 0x88]).await?;
                connected = true;
            }
            _ => {}
        }
    }
    if unknown_ack {
        io.write_all(&[0, 0, 8, 6, 1, 0, 0, 0, 0, 83, 69, 67, 82, 69, 84, 33, 33])
            .await?;
    }
    let mut bytes = [0; 1024];
    while io.read(&mut bytes).await? != 0 {}
    Ok(())
}

#[tokio::test]
async fn no_same_connection_pong_is_terminal_and_wakes_pending_application_read()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (a, b) = tokio::io::duplex(128 * 1024);
        let peer = tokio::spawn(silent_peer(b, false));
        let mut link = client(a, tuple(), short()).await?;
        let error = tokio::time::timeout(Duration::from_secs(3), link.io.read_u8())
            .await?
            .err();
        assert_eq!(error.map(|e| e.kind()), Some(io::ErrorKind::TimedOut));
        let final_result = link.owner.join().await?;
        assert_eq!(final_result.reason, LinkEnd::ProbeTimeout);
        assert_eq!(final_result.final_health.probe, ProbeState::TimedOut);
        assert!(final_result.final_health.last_probe_sequence.is_some());
        assert!(final_result.final_health.last_reply_sequence.is_none());
        tokio::time::timeout(Duration::from_secs(3), peer).await???;
        Ok(())
    })
    .await?
}

#[tokio::test]
async fn many_legal_small_messages_do_not_exhaust_retired_frame_metadata()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (mut a, mut b) = pair().await?;
        let send = async {
            for _ in 0..64 {
                a.io.write_all(&[0; 48]).await?;
                a.io.flush().await?;
                a.io.write_all(&[0; 8]).await?;
                a.io.flush().await?;
            }
            Ok::<_, io::Error>(())
        };
        let receive = async {
            let mut body = vec![0; 64 * 56];
            b.io.read_exact(&mut body).await?;
            Ok::<_, io::Error>(())
        };
        let (sent, received) = tokio::time::timeout(Duration::from_secs(3), async {
            tokio::join!(send, receive)
        })
        .await?;
        sent?;
        received?;
        stop(a, b).await
    })
    .await?
}

#[tokio::test]
async fn cooperative_stop_is_joined_and_monitor_does_not_keep_network_alive()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (a, b) = pair().await?;
        let monitor = a.monitor.clone();
        a.owner.request_stop();
        let result = a.owner.join().await?;
        assert_eq!(result.reason, LinkEnd::LocalStop);
        assert_eq!(monitor.snapshot()?.end, Some(LinkEnd::LocalStop));
        b.owner.request_stop();
        b.owner.join().await?;
        Ok(())
    })
    .await?
}

fn poison_health(
    observation: Arc<Observation>,
) -> std::result::Result<(), Box<dyn std::error::Error>> {
    // Deliberate test-only unwinding while a real metadata guard is held.
    // resume_unwind avoids a panic hook printing fixture text to production logs.
    let thread = std::thread::spawn(move || -> std::result::Result<(), &'static str> {
        let _guard = observation.health.lock().map_err(|_| "already poisoned")?;
        std::panic::resume_unwind(Box::new("metadata fixture"));
    });
    if thread.join().is_ok() {
        return Err("metadata fixture did not poison its lock".into());
    }
    Ok(())
}

#[tokio::test]
async fn poison_during_capacity_update_is_an_io_error_not_pending_or_counter_overflow()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (mut a, b) = pair().await?;
        a.io.write_all(&vec![0; WINDOW as usize]).await?;
        a.io.flush().await?;
        observed(&b.monitor, |h| {
            h.stats.is_some_and(|s| s.receive_pending_bytes == WINDOW)
        })
        .await?;
        poison_health(a.monitor.observation.clone())?;
        // Current-thread runtime: before any other driver poll, this write must
        // reject its poisoned metadata update rather than quietly return Pending.
        let error =
            a.io.write_all(&[1])
                .await
                .err()
                .ok_or("poisoned write unexpectedly succeeded")?;
        assert_eq!(error.kind(), io::ErrorKind::Other);
        assert_eq!(error.to_string(), "data-link observation failed");
        assert_eq!(a.monitor.snapshot(), Err(Error::LinkDriverFailed));
        let ended = a.monitor.ended();
        assert_eq!(a.owner.join().await, Err(Error::LinkDriverFailed));
        assert!(*ended.borrow());
        b.owner.request_stop();
        b.owner.join().await?;
        Ok(())
    })
    .await?
}

#[tokio::test]
async fn poisoned_shutdown_metadata_cannot_claim_fin_or_healthy_terminal()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (mut a, b) = pair().await?;
        poison_health(a.monitor.observation.clone())?;
        let error =
            a.io.shutdown()
                .await
                .err()
                .ok_or("poisoned shutdown succeeded")?;
        assert_eq!(error.kind(), io::ErrorKind::Other);
        assert_eq!(a.monitor.snapshot(), Err(Error::LinkDriverFailed));
        assert_eq!(a.owner.join().await, Err(Error::LinkDriverFailed));
        b.owner.request_stop();
        b.owner.join().await?;
        Ok(())
    })
    .await?
}

#[tokio::test]
async fn actual_counter_exhaustion_remains_distinct_from_metadata_poison()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (mut a, b) = pair().await?;
        a.io.data_enqueued = u64::MAX;
        let error = a.io.write_all(&[1]).await.err().ok_or("counter wrapped")?;
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        let result = a.owner.join().await?;
        assert_eq!(result.reason, LinkEnd::CounterOverflow);
        assert_eq!(result.final_health.end, Some(LinkEnd::CounterOverflow));
        b.owner.request_stop();
        b.owner.join().await?;
        Ok(())
    })
    .await?
}

#[path = "adversarial_tests.rs"]
mod adversarial_tests;

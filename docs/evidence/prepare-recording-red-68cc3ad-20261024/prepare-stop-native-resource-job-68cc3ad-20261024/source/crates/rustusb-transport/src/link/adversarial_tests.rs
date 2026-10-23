//! Software adversarial contracts for bounded HTTP/2 framing.
//! Frame corpus is synthetic HTTP/2 payload, never device USB data.
use super::*;
use tokio::sync::oneshot;

#[derive(Clone, Copy)]
enum Attack {
    OneByteBurst,
    EmptyBurst,
    Trailers,
}

#[derive(Debug)]
struct WireObservation {
    frames_sent: usize,
    payload_sent: usize,
    goaway: Option<u32>,
}

async fn attacking_peer(
    mut io: DuplexStream,
    arm: oneshot::Receiver<()>,
    attack: Attack,
) -> io::Result<WireObservation> {
    let mut preface = [0; 24];
    io.read_exact(&mut preface).await?;
    if &preface != b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n" {
        return Err(failed());
    }
    loop {
        let mut header = [0; 9];
        io.read_exact(&mut header).await?;
        let n =
            (usize::from(header[0]) << 16) | (usize::from(header[1]) << 8) | usize::from(header[2]);
        if n > FRAME {
            return Err(failed());
        }
        let mut body = vec![0; n];
        io.read_exact(&mut body).await?;
        if header[3] == 4 && header[4] & 1 == 0 {
            io.write_all(&[0, 0, 0, 4, 0, 0, 0, 0, 0, 0, 0, 0, 4, 1, 0, 0, 0, 0])
                .await?;
        }
        if header[3] == 1 {
            io.write_all(&[0, 0, 1, 1, 4, 0, 0, 0, 1, 0x88]).await?;
            break;
        }
    }
    arm.await
        .map_err(|_| io::Error::other("attack not armed"))?;
    let (mut corpus, frames_sent, payload_sent) = match attack {
        Attack::OneByteBurst => {
            // Auto max=max(65535/2,25600)=32767. 128*255=32640;
            // frame129 crosses32767 if the batch remains buffered in h2.
            let mut bytes = Vec::with_capacity(1290);
            for _ in 0..129 {
                bytes.extend_from_slice(&[0, 0, 1, 0, 0, 0, 0, 0, 1, 0x31]);
            }
            (bytes, 129, 129)
        }
        Attack::EmptyBurst => {
            // Non-final empty lifetime cap100 is independent of pump retirement.
            let mut bytes = Vec::with_capacity(909);
            for _ in 0..101 {
                bytes.extend_from_slice(&[0, 0, 0, 0, 0, 0, 0, 0, 1]);
            }
            (bytes, 101, 0)
        }
        Attack::Trailers => (vec![0, 0, 5, 1, 5, 0, 0, 0, 1, 0, 1, b'x', 1, b'y'], 1, 0),
    };
    // Corpus fits in the private128KiB duplex and is submitted before yielding
    // back to this current-thread runtime's real Connection driver.
    io.write_all(&corpus).await?;
    io.flush().await?;
    corpus.clear();
    let mut goaway = None;
    loop {
        let mut header = [0; 9];
        match io.read_exact(&mut header).await {
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => break,
            Err(error) => return Err(error),
        }
        let n =
            (usize::from(header[0]) << 16) | (usize::from(header[1]) << 8) | usize::from(header[2]);
        if n > FRAME {
            return Err(failed());
        }
        let mut body = vec![0; n];
        io.read_exact(&mut body).await?;
        if header[3] == 7 && n >= 8 {
            goaway = Some(u32::from_be_bytes([body[4], body[5], body[6], body[7]]));
        }
    }
    Ok(WireObservation {
        frames_sent,
        payload_sent,
        goaway,
    })
}

async fn rejected_burst(
    attack: Attack,
    frames: usize,
    payload: usize,
) -> std::result::Result<(), Box<dyn std::error::Error>> {
    let (a, b) = tokio::io::duplex(128 * 1024);
    let (arm, armed) = oneshot::channel();
    let peer = tokio::spawn(attacking_peer(b, armed, attack));
    let link = client(a, tuple(), short()).await?;
    arm.send(()).map_err(|_| "attack arm failed")?;
    let result = link.owner.join().await?;
    let wire = peer.await??;
    assert_eq!(result.reason, LinkEnd::Protocol, "{wire:?}");
    assert_eq!(wire.frames_sent, frames);
    assert_eq!(wire.payload_sent, payload);
    // Actual outgoing wire reason, not an inferred library log/debug string.
    assert_eq!(wire.goaway, Some(11), "{wire:?}");
    assert!(
        result
            .final_health
            .stats
            .is_some_and(|s| s.receive_pending_bytes <= WINDOW)
    );
    assert!(result.final_health.last_reply_sequence.is_none());
    Ok(())
}

#[tokio::test]
async fn buffered_one_byte_batch_hits_real_auto_metadata_budget()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(
        Duration::from_secs(8),
        rejected_burst(Attack::OneByteBurst, 129, 129),
    )
    .await?
}

#[tokio::test]
async fn empty_nonfinal_flood_has_a_lifetime_bound_despite_zero_byte_credit()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(
        Duration::from_secs(8),
        rejected_burst(Attack::EmptyBurst, 101, 0),
    )
    .await?
}

#[tokio::test]
async fn actual_trailer_headers_are_rejected_instead_of_clean_application_eof()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (a, b) = tokio::io::duplex(128 * 1024);
        let (arm, armed) = oneshot::channel();
        let peer = tokio::spawn(attacking_peer(b, armed, Attack::Trailers));
        let mut link = client(a, tuple(), short()).await?;
        arm.send(()).map_err(|_| "attack arm failed")?;
        let error = link
            .io
            .read_u8()
            .await
            .err()
            .ok_or("trailers became clean success")?;
        assert_eq!(error.kind(), io::ErrorKind::InvalidData);
        assert_eq!(link.owner.join().await?.reason, LinkEnd::Protocol);
        peer.await??;
        Ok(())
    })
    .await?
}

#[tokio::test]
async fn promptly_consumed_one_byte_frames_are_legal_not_blanket_rejected()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    tokio::time::timeout(Duration::from_secs(8), async {
        let (mut a, mut b) = pair().await?;
        for _ in 0..1024 {
            a.io.write_all(&[0x31]).await?;
            a.io.flush().await?;
            assert_eq!(b.io.read_u8().await?, 0x31);
        }
        assert_eq!(
            b.monitor.snapshot()?.stats.map(|s| s.app_read_bytes),
            Some(1024)
        );
        stop(a, b).await
    })
    .await?
}

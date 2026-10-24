//! The unquiet control proves the capture can see the pinned library's payload.

use super::*;
use std::sync::{Arc, Mutex};
use tokio::sync::Notify;
use tracing_subscriber::fmt::MakeWriter;

#[derive(Clone)]
struct Capture {
    bytes: Arc<Mutex<Vec<u8>>>,
    wake: Arc<Notify>,
}

impl Capture {
    fn text(&self) -> io::Result<String> {
        let bytes = self
            .bytes
            .lock()
            .map_err(|_| io::Error::other("capture lock failed"))?;
        Ok(String::from_utf8_lossy(&bytes).into_owned())
    }

    fn clear(&self) -> io::Result<()> {
        self.bytes
            .lock()
            .map_err(|_| io::Error::other("capture lock failed"))?
            .clear();
        Ok(())
    }
}

impl io::Write for Capture {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.bytes
            .lock()
            .map_err(|_| io::Error::other("capture lock failed"))?
            .extend_from_slice(bytes);
        self.wake.notify_one();
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl<'a> MakeWriter<'a> for Capture {
    type Writer = Capture;
    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}

#[tokio::test]
async fn peer_ping_payload_is_captured_unquiet_but_never_leaks_from_owned_link()
-> std::result::Result<(), Box<dyn std::error::Error>> {
    let capture = Capture {
        bytes: Arc::new(Mutex::new(Vec::new())),
        wake: Arc::new(Notify::new()),
    };
    // A global subscriber also observes any accidentally unquiet spawned driver;
    // Tokio task spawning does not inherit a task-local subscriber.
    tracing::subscriber::set_global_default(
        tracing_subscriber::fmt()
            .with_writer(capture.clone())
            .with_ansi(false)
            .without_time()
            .with_max_level(tracing::Level::TRACE)
            .finish(),
    )?;
    let decimal = "83, 69, 67, 82, 69, 84, 33, 33";
    let (a, b) = tokio::io::duplex(128 * 1024);
    let peer = tokio::spawn(silent_peer(b, true));
    let (mut sender, connection) = h2::client::handshake(a).await?;
    let request = http::Request::builder()
        .method("CONNECT")
        .uri(
            http::Uri::builder()
                .authority("rustusb.invalid:443")
                .build()?,
        )
        .body(())?;
    let (response, send) = sender.send_request(request, false)?;
    let mut connection = Box::pin(connection);
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if capture.text()?.contains(decimal) {
                return Ok::<_, io::Error>(());
            }
            tokio::select! {
                result = &mut connection => {
                    result.map_err(|_| io::Error::other("unquiet connection failed"))?;
                    return Err(io::Error::other("unquiet connection ended before capture"));
                },
                _ = capture.wake.notified() => {},
            }
        }
    })
    .await??;
    drop(response);
    drop(send);
    drop(sender);
    // Drop the actual pinned future and join the raw peer after socket EOF.
    drop(connection);
    tokio::time::timeout(Duration::from_secs(3), peer).await???;
    capture.clear()?;

    let (a, b) = tokio::io::duplex(128 * 1024);
    let peer = tokio::spawn(silent_peer(b, true));
    let link = client(a, tuple(), short()).await?;
    let outcome = tokio::time::timeout(Duration::from_secs(3), link.owner.join()).await??;
    assert_eq!(outcome.reason, LinkEnd::ProbeTimeout);
    tokio::time::timeout(Duration::from_secs(3), peer).await???;
    tracing::info!(target: "rustusb_transport::audit", reason = "probe_timeout", "typed link audit remains visible");
    let text = capture.text()?;
    assert!(text.contains("typed link audit remains visible"));
    for private in [
        "SECRET!!",
        decimal,
        "5345435245542121",
        "53, 45, 43, 52, 45, 54, 21, 21",
    ] {
        assert!(!text.contains(private));
    }
    Ok(())
}

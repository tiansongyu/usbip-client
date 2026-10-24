//! Bounded event output shared by server and client service lifecycles.

use rustusb_core::{Error, Result};
use serde_json::Value;
use std::{io::Write, time::Duration};
use tokio::sync::{mpsc, oneshot};

pub(super) struct EventOutput {
    sender: mpsc::Sender<(Vec<u8>, oneshot::Sender<std::io::Result<()>>)>,
    // Cancellation leaves this true: its OS write may still be blocked.
    pub(super) in_flight: bool,
}

impl EventOutput {
    pub(super) fn new() -> Result<Self> {
        let (sender, mut receiver) =
            mpsc::channel::<(Vec<u8>, oneshot::Sender<std::io::Result<()>>)>(2);
        // This std thread holds only bounded encoded events and stdout. It
        // never owns the server/leases and cannot delay Tokio runtime shutdown.
        std::thread::Builder::new()
            .name("virtual-test-stdout".into())
            .spawn(move || {
                let mut output = std::io::stdout().lock();
                while let Some((bytes, complete)) = receiver.blocking_recv() {
                    let result = output.write_all(&bytes).and_then(|()| output.flush());
                    let failed = result.is_err();
                    let _ = complete.send(result);
                    if failed {
                        break;
                    }
                }
            })?;
        Ok(Self {
            sender,
            in_flight: false,
        })
    }

    pub(super) async fn event(&mut self, value: Value) -> Result<()> {
        let mut bytes = serde_json::to_vec(&value)
            .map_err(|_| Error::State("could not encode server event".into()))?;
        if bytes.len() >= 65_536 {
            return Err(Error::State("server event exceeds output limit".into()));
        }
        bytes.push(b'\n');
        self.bytes(bytes).await
    }

    /// Send bounded already-rendered text through the same stdout owner/deadline.
    pub(super) async fn text(&mut self, text: String) -> Result<()> {
        if text.len() >= 65_536 {
            return Err(Error::State("log text exceeds output limit".into()));
        }
        self.bytes(text.into_bytes()).await
    }

    async fn bytes(&mut self, bytes: Vec<u8>) -> Result<()> {
        let (complete, receipt) = oneshot::channel();
        self.in_flight = true;
        let result = tokio::time::timeout(Duration::from_secs(5), async {
            self.sender
                .send((bytes, complete))
                .await
                .map_err(|_| Error::State("server event writer stopped".into()))?;
            receipt
                .await
                .map_err(|_| Error::State("server event writer stopped".into()))??;
            Ok(())
        })
        .await
        .map_err(|_| Error::Timeout("server stdout consumer is blocked".into()))?;
        self.in_flight = false;
        result
    }
}

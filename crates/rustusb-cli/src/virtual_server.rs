//! Bounded, local-only JSONL test controls for the opt-in virtual backend.

use rustusb_core::{Error, Result};
use rustusb_rust_backend::{ServerConfig, ServerHandle, VirtualBackend};
use serde::Deserialize;
use serde_json::{Value, json};
use std::{
    io::{BufRead, Read, Write},
    net::{IpAddr, SocketAddr},
    time::Duration,
};
use tokio::sync::{mpsc, oneshot};

const MAX_LINE: usize = 4096;

#[derive(Deserialize)]
#[serde(tag = "op", rename_all = "snake_case", deny_unknown_fields)]
pub(super) enum Control {
    HidKey { id: String, key: u8 },
    CdcEmit { id: String, hex: String },
    Snapshot { id: String },
    Shutdown { id: String },
}

impl Control {
    pub(super) fn id(&self) -> &str {
        match self {
            Self::HidKey { id, .. }
            | Self::CdcEmit { id, .. }
            | Self::Snapshot { id }
            | Self::Shutdown { id } => id,
        }
    }
}

pub(super) fn parse(line: &[u8]) -> Result<Control> {
    // Parser errors can embed input fragments, so never forward serde's error text.
    let control: Control = serde_json::from_slice(line)
        .map_err(|_| Error::InvalidInput("invalid local control JSON or fields".into()))?;
    let id = control.id();
    if id.is_empty()
        || id.len() > 64
        || !id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err(Error::InvalidInput(
            "control ID must be 1..64 safe ASCII characters".into(),
        ));
    }
    Ok(control)
}

fn hex_bytes(hex: &str) -> Result<Vec<u8>> {
    if hex.is_empty() || hex.len() > 2048 || !hex.len().is_multiple_of(2) {
        return Err(Error::InvalidInput("hex must encode 1..1024 bytes".into()));
    }
    fn nibble(b: u8) -> Result<u8> {
        match b {
            b'0'..=b'9' => Ok(b - b'0'),
            b'a'..=b'f' => Ok(b - b'a' + 10),
            b'A'..=b'F' => Ok(b - b'A' + 10),
            _ => Err(Error::InvalidInput("invalid hex digit".into())),
        }
    }
    hex.as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok(nibble(pair[0])? * 16 + nibble(pair[1])?))
        .collect()
}

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

fn read_line(reader: &mut impl BufRead) -> Result<Option<Vec<u8>>> {
    let mut line = Vec::with_capacity(256);
    let count = reader
        .take((MAX_LINE + 1) as u64)
        .read_until(b'\n', &mut line)?;
    if count > MAX_LINE {
        return Err(Error::InvalidInput(
            "local control line exceeds 4096 bytes".into(),
        ));
    }
    if count == 0 { Ok(None) } else { Ok(Some(line)) }
}

pub(super) fn controls() -> Result<mpsc::Receiver<Result<Vec<u8>>>> {
    let (sender, receiver) = mpsc::channel(2);
    // stdin is a blocking OS stream. A dedicated std thread avoids pinning a Tokio
    // worker or making runtime shutdown wait on an uncancellable Tokio stdin read.
    // It holds only stdin and a bounded channel, never any server/lease ownership.
    std::thread::Builder::new()
        .name("virtual-test-stdin".into())
        .spawn(move || {
            let input = std::io::stdin();
            let mut reader = input.lock();
            loop {
                match read_line(&mut reader) {
                    Ok(Some(line)) => {
                        if sender.blocking_send(Ok(line)).is_err() {
                            break;
                        }
                    }
                    Ok(None) => break,
                    Err(error) => {
                        let _ = sender.blocking_send(Err(error));
                        break;
                    }
                }
            }
        })?;
    Ok(receiver)
}

pub(super) async fn run(
    listen: SocketAddr,
    trusted_network: bool,
    allowed_peers: Vec<IpAddr>,
    control_stdin: bool,
) -> Result<()> {
    let mut output = EventOutput::new()?;
    let backend = VirtualBackend::new()?;
    let server = ServerHandle::bind(
        ServerConfig {
            listen,
            trusted_network,
            allowed_peers,
        },
        backend.clone(),
    )
    .await?;
    let operations = async {
        output.event(json!({"schema_version":1,"event":"ready","address":server.local_addr(),"devices":backend.snapshot()?.devices})).await?;
        if !control_stdin {
            std::future::pending::<()>().await;
        }
        let mut controls = controls()?;
        loop {
            let next = controls.recv().await;
            let Some(line) = next else {
                break;
            };
            let control = match line.and_then(|line| parse(&line)) {
                Ok(control) => control,
                Err(_) => {
                    output
                        .event(json!({"schema_version":1,"event":"error","code":"invalid_control"}))
                        .await?;
                    continue;
                }
            };
            let id = control.id().to_owned();
            let result = match control {
                Control::HidKey { key, .. } => backend.hid_key(key),
                Control::CdcEmit { hex, .. } => {
                    hex_bytes(&hex).and_then(|bytes| backend.cdc_emit(&bytes))
                }
                Control::Snapshot { .. } => {
                    output.event(json!({"schema_version":1,"event":"snapshot","id":id,"snapshot":backend.snapshot()?})).await?;
                    continue;
                }
                Control::Shutdown { .. } => {
                    output
                        .event(json!({"schema_version":1,"event":"ack","id":id,"ok":true}))
                        .await?;
                    break;
                }
            };
            // Payload and parser input never appear in events or tracing.
            output.event(match result {
                Ok(()) => json!({"schema_version":1,"event":"ack","id":id,"ok":true}),
                Err(_) => json!({"schema_version":1,"event":"error","id":id,"code":"control_rejected"}),
            }).await?;
        }
        Ok(())
    };
    let result: Result<()> = {
        tokio::pin!(operations);
        tokio::select! {
            result = &mut operations => result,
            signal = tokio::signal::ctrl_c() => signal.map_err(Error::Io),
        }
    };
    // All early event/control failures still await deterministic lease cleanup.
    let shutdown = server.shutdown().await;
    result?;
    shutdown?;
    if output.in_flight {
        // Ctrl-C cancelled a blocked output. Connections are already reclaimed;
        // do not wait on the same consumer again to emit a final event.
        return Ok(());
    }
    output
        .event(json!({"schema_version":1,"event":"stopped","snapshot":backend.snapshot()?}))
        .await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn controls_reject_unbounded_or_sensitive_input_without_echo() -> Result<()> {
        let mut long = std::io::Cursor::new(vec![b'x'; MAX_LINE + 128]);
        assert!(read_line(&mut long).is_err());
        for bad in [
            br#"{"id":"bad\n","op":"snapshot"}"#.as_slice(),
            br#"{"id":"x","op":"snapshot","secret":"payload"}"#,
            br#"{"id":"x","op":"other"}"#,
        ] {
            let error = parse(bad)
                .err()
                .ok_or_else(|| Error::State("accepted invalid control".into()))?;
            assert!(!error.to_string().contains("payload"));
        }
        assert!(hex_bytes(&"aa".repeat(1025)).is_err());
        assert!(hex_bytes("0g").is_err());
        assert_eq!(hex_bytes("0034aF")?, [0, 0x34, 0xaf]);
        assert!(parse(br#"{"id":"cycle-1","op":"hid_key","key":30}"#).is_ok());
        Ok(())
    }
}

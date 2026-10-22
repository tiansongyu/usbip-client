#!/usr/bin/env bash
# Reproduce the jiegec/usbip source audit without editing upstream source.
# Existing source remains unchanged; Cargo may create target/ and Cargo.lock.
# QEMU runs only when RUN_QEMU=1, in a disposable VM with simulated devices.
set -euo pipefail
revision=61938f8c48ed01c508faaacfa7cb3fc3810b5ae0
audit_dir="${AUDIT_DIR:-$(mktemp -d /tmp/rustusb-audit.XXXXXX)}"
upstream="${UPSTREAM_DIR:-$audit_dir/upstream}"
mkdir -p "$audit_dir"
if [[ ! -d "$upstream/.git" ]]; then
    git clone https://github.com/jiegec/usbip.git "$upstream"
    git -C "$upstream" checkout --detach "$revision"
fi
actual_revision=$(git -C "$upstream" rev-parse HEAD)
[[ "$actual_revision" == "$revision" ]] || {
    echo "Expected $revision; found $actual_revision" >&2
    exit 1
}
git -C "$upstream" diff --exit-code
rustc --version
cargo --version
uname -a
cargo test --manifest-path "$upstream/Cargo.toml" --all-features 2>&1 | tee "$audit_dir/upstream-test.log"
mkdir -p "$audit_dir/probe/src"
python3 - "$upstream" "$audit_dir/probe/Cargo.toml" <<'PY'
import json, pathlib, sys
pathlib.Path(sys.argv[2]).write_text('''[package]
name = "rustusb-upstream-audit"
version = "0.0.0"
edition = "2024"
[dependencies]
usbip = { path = ''' + json.dumps(sys.argv[1]) + ''' }
tokio = { version = "1", features = ["full"] }
''')
PY
cat > "$audit_dir/probe/src/main.rs" <<'RUST'
use std::{io, pin::Pin, sync::{Arc, Mutex}, task::{Context, Poll}};
use tokio::io::{AsyncRead, AsyncWrite, ReadBuf};
use usbip::{*, usbip_protocol::*};

#[derive(Default)]
struct Socket { input: Vec<u8>, cursor: usize, output: Vec<u8>, fail_write: bool }
impl AsyncRead for Socket {
    fn poll_read(mut self: Pin<&mut Self>, _: &mut Context<'_>, buf: &mut ReadBuf<'_>) -> Poll<io::Result<()>> {
        let n = buf.remaining().min(self.input.len() - self.cursor);
        buf.put_slice(&self.input[self.cursor..self.cursor+n]);
        self.cursor += n;
        Poll::Ready(Ok(()))
    }
}
impl AsyncWrite for Socket {
    fn poll_write(mut self: Pin<&mut Self>, _: &mut Context<'_>, buf: &[u8]) -> Poll<io::Result<usize>> {
        if self.fail_write { return Poll::Ready(Err(io::ErrorKind::BrokenPipe.into())); }
        self.output.extend_from_slice(buf);
        Poll::Ready(Ok(buf.len()))
    }
    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> { Poll::Ready(Ok(())) }
    fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<io::Result<()>> { Poll::Ready(Ok(())) }
}
fn server() -> Arc<UsbIpServer> {
    let mut acm = cdc::UsbCdcAcmHandler::new();
    acm.tx_buffer = vec![b'a'; 16];
    let dev = UsbDevice::new(0).with_interface(
        ClassCode::CDC as u8, cdc::CDC_ACM_SUBCLASS, 0, None,
        cdc::UsbCdcAcmHandler::endpoints(),
        Arc::new(Mutex::new(Box::new(acm))),
    );
    Arc::new(UsbIpServer::new_simulated(vec![dev]))
}
fn import(id: &str) -> Vec<u8> {
    let mut busid = [0;32]; busid[..id.len()].copy_from_slice(id.as_bytes());
    UsbIpCommand::OpReqImport {status:0, busid}.to_bytes()
}
fn submit(ep:u32, length:u32, setup:[u8;8]) -> Vec<u8> {
    UsbIpCommand::UsbIpCmdSubmit {
        header: UsbIpHeaderBasic {command:1,seqnum:1,devid:0,direction:1,ep},
        transfer_flags:0,transfer_buffer_length:length,start_frame:0,number_of_packets:0,
        interval:0,setup,data:vec![],iso_packet_descriptor:vec![],
    }.to_bytes()
}
async fn execute(server: Arc<UsbIpServer>, input: Vec<u8>, fail_write:bool)
    -> Result<(io::Result<()>,Vec<u8>),tokio::task::JoinError> {
    tokio::spawn(async move {
        let mut s = Socket {input,fail_write,..Default::default()};
        let result = handler(&mut s,server).await;
        (result,s.output)
    }).await
}
async fn available(server: Arc<UsbIpServer>) -> u32 {
    let (result, bytes) = execute(server,UsbIpCommand::OpReqDevlist{status:0}.to_bytes(),false).await.unwrap();
    result.unwrap();
    u32::from_be_bytes(bytes[8..12].try_into().unwrap())
}
#[tokio::main]
async fn main() {
    // These assertions reproduce known upstream defects; PASS means observed,
    // never that the upstream passed a security acceptance gate.
    let s=server();
    execute(s.clone(),import("0-0-0"),false).await.unwrap().0.unwrap();
    assert_eq!(available(s).await,1);
    println!("BASELINE clean EOF releases device: observed");

    let s=server();
    assert!(execute(s,submit(0,18,[0x80,6,0,1,0,0,18,0]),false).await.unwrap_err().is_panic());
    println!("DEFECT SUBMIT before IMPORT panics: reproduced");

    let s=server(); let mut req=import("0-0-0");
    req.extend(submit(0,64,[0xa1,1,0,0,255,0,64,0]));
    assert!(execute(s.clone(),req,false).await.unwrap_err().is_panic());
    assert_eq!(available(s).await,0);
    println!("DEFECT interface 255 panics and strands device: reproduced");

    let s=server(); let mut req=import("0-0-0"); req.extend(import("missing"));
    execute(s.clone(),req,false).await.unwrap().0.unwrap();
    assert_eq!(available(s).await,0);
    println!("DEFECT repeated IMPORT strands previous device: reproduced");

    let s=server();
    assert_eq!(execute(s.clone(),import("0-0-0"),true).await.unwrap().0.unwrap_err().kind(),io::ErrorKind::BrokenPipe);
    assert_eq!(available(s).await,0);
    println!("DEFECT response write error strands imported device: reproduced");

    let s=server(); let mut req=import("0-0-0"); req.extend(submit(2,1,[0;8]));
    let (_,bytes)=execute(s,req,false).await.unwrap();
    let actual=u32::from_be_bytes(bytes[320+24..320+28].try_into().unwrap());
    assert_eq!(actual,16);
    println!("DEFECT bulk IN length=1 returns 16 bytes: reproduced");

    let s=server(); let mut req=import("0-0-0");
    req.extend(submit(0,1,[0x80,6,0,1,0,0,64,0]));
    let (_,bytes)=execute(s,req,false).await.unwrap();
    let actual=u32::from_be_bytes(bytes[320+24..320+28].try_into().unwrap());
    assert_eq!(actual,18);
    println!("DEFECT descriptor IN length=1 returns 18 bytes: reproduced");

    let mut oversized=submit(2,8*1024*1024,[0;8]);
    oversized[12..16].copy_from_slice(&0u32.to_be_bytes());
    oversized.resize(48+8*1024*1024,0);
    let mut socket=Socket{input:oversized,..Default::default()};
    match UsbIpCommand::read_from_socket(&mut socket).await.unwrap() {
        UsbIpCommand::UsbIpCmdSubmit{data,..} => assert_eq!(data.len(),8*1024*1024),
        _=>panic!("wrong command"),
    }
    println!("LIMIT parser accepts and allocates 8 MiB OUT: observed (no OOM probe)");
    println!("Audit probes finished; reproduced defects are NOT production readiness.");
}
RUST
cargo run --manifest-path "$audit_dir/probe/Cargo.toml" --release 2>&1 | tee "$audit_dir/probe.log"
if [[ "${RUN_QEMU:-0}" == 1 ]]; then
    (cd "$upstream" && ./scripts/qemu/run-qemu-test.sh --build --dump-log) 2>&1 | tee "$audit_dir/qemu.log"
    cp "$upstream/.qemu-serial.log" "$audit_dir/qemu-serial.log"
fi
git -C "$upstream" diff --exit-code
printf 'Audit evidence directory: %s\n' "$audit_dir"

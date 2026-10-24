//! Guest-only, read-only system resolver observation; never a product connector.
#![forbid(unsafe_code)]
use std::{error::Error, fs, net::ToSocketAddrs, process::{Command, Stdio}};

fn run() -> Result<(),Box<dyn Error>> {
    let command_line=fs::read("/proc/cmdline")?;
    let fields=String::from_utf8(command_line)?;
    if std::env::var("RUSTUSB_QEMU_GUEST").as_deref()!=Ok("1")
        || !fields.split_ascii_whitespace().any(|x|x=="rustusb.secure=1")
        || !fields.split_ascii_whitespace().any(|x|x=="rustusb.role=client") {
        return Err("resolver probe requires the isolated secure client guest".into());
    }
    let executable=format!("/proc/{}/exe",std::process::id());
    let receipt=Command::new("/bin/busybox").args(["sha256sum",&executable])
        .stdin(Stdio::null()).output()?;
    if !receipt.status.success() || receipt.stdout.len()>1024 || !receipt.stderr.is_empty() {
        return Err("actual probe executable observation failed".into());
    }
    print!("RESOLVER_ACTUAL_EXE {}",String::from_utf8(receipt.stdout)?);
    let addresses:Vec<_>=("rustusb-dual-stack.test",3241).to_socket_addrs()?.take(65).collect();
    println!("RESOLVER_RAW_COUNT {}",addresses.len());
    for (index,address) in addresses.iter().enumerate() {
        println!("RESOLVER_CANDIDATE {index} {address}");
    }
    if addresses.is_empty() || addresses.len()>64 {
        return Err("probe result count outside the experiment bound".into());
    }
    Ok(())
}
fn main() {
    if let Err(error)=run() { eprintln!("resolver observation failed: {error}");std::process::exit(2); }
}

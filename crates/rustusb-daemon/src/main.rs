//! Entry point for the administrator-installed RustUSB privileged helper.

use clap::Parser;
use std::path::PathBuf;

#[derive(Parser)]
#[command(
    name = "rustusb-helper",
    about = "Restricted local Linux USB/IP helper; never starts usbipd or creates encryption"
)]
struct Args {
    /// Root-owned, non-writable-by-others policy file.
    #[arg(long, default_value = "/etc/rustusb/policy.json")]
    config: PathBuf,
    /// Socket in a root-owned, non-writable-by-others directory.
    #[arg(long, default_value = "/run/rustusb/control.sock")]
    socket: PathBuf,
}

#[tokio::main]
async fn main() -> std::process::ExitCode {
    let args = Args::parse();
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .init();
    match rustusb_daemon::serve(&args.config, &args.socket).await {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("rustusb-helper: {error}");
            std::process::ExitCode::FAILURE
        }
    }
}

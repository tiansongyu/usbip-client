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
    /// Root-only persistent physical grant journal, kept across helper restarts.
    #[arg(long, default_value = "/var/lib/rustusb/physical")]
    physical_state_dir: PathBuf,
}

#[tokio::main]
async fn main() -> std::process::ExitCode {
    let args = Args::parse();
    tracing_subscriber::fmt()
        .with_writer(std::io::stderr)
        .init();
    match rustusb_daemon::serve_with_physical_state(
        &args.config,
        &args.socket,
        &args.physical_state_dir,
    )
    .await
    {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("rustusb-helper: {error}");
            std::process::ExitCode::FAILURE
        }
    }
}

//! Entry point for the administrator-installed RustUSB privileged helper.

use clap::Parser;
use std::{io::Write, path::PathBuf};

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
    /// Separate root-only secure Native channel intents, retained across restarts.
    #[arg(long, default_value = "/var/lib/rustusb/native")]
    native_state_dir: PathBuf,
    /// Write bounded sanitized NDJSON to an existing root-owned mode0700 directory.
    #[arg(long, conflicts_with = "log_json")]
    log_directory: Option<PathBuf>,
    /// Maximum bytes per segment; requires --log-directory.
    #[arg(long,requires="log_directory",value_parser=clap::value_parser!(u64).range(4096..=8388608))]
    log_max_bytes: Option<u64>,
    /// Total retained segments including active.
    #[arg(long,requires="log_directory",value_parser=clap::value_parser!(u8).range(2..=16))]
    log_retain: Option<u8>,
    /// Use JSON stderr tracing in legacy mode; file logs are always JSON.
    #[arg(long)]
    log_json: bool,
}

fn main() -> std::process::ExitCode {
    let args = Args::parse();
    let options = args.log_directory.clone().map(|directory| {
        let mut options = rustusb_logging::Options::new(directory);
        if let Some(bytes) = args.log_max_bytes {
            options.segment_bytes = bytes;
        }
        if let Some(retain) = args.log_retain {
            options.retained_segments = retain;
        }
        options
    });
    // This startup I/O occurs before constructing Tokio or receiving capabilities.
    let logger = match rustusb_logging::initialize(options, args.log_json) {
        Ok(logger) => logger,
        Err(error) => {
            report_stderr(format_args!(
                "rustusb-helper logging initialization failed: {error}"
            ));
            return std::process::ExitCode::FAILURE;
        }
    };
    let file_mode = logger.monitor().is_some();
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            report_stderr(format_args!(
                "rustusb-helper runtime initialization failed: {error}"
            ));
            if let Err(error) = logger.shutdown() {
                report_stderr(format_args!(
                    "rustusb-helper logging shutdown failed before device work: {error}"
                ));
            }
            return std::process::ExitCode::FAILURE;
        }
    };
    let result = runtime.block_on(rustusb_daemon::serve_with_state(
        &args.config,
        &args.socket,
        &args.physical_state_dir,
        &args.native_state_dir,
    ));
    let mut failed = false;
    if let Err(error) = result {
        failed = true;
        if file_mode || args.log_json {
            tracing::error!(event_code="operation_failed", failure=rustusb_logging::FailureCategory::from(&error).as_str(), error=%error, "restricted helper failed");
        } else {
            report_stderr(format_args!("rustusb-helper: {error}"));
        }
    }
    // The helper has joined its actual mutation/connection work before logging.
    if let Err(error) = logger.shutdown() {
        failed = true;
        report_stderr(format_args!(
            "rustusb-helper logging failed after operation cleanup: {error}"
        ));
    }
    if failed {
        std::process::ExitCode::FAILURE
    } else {
        std::process::ExitCode::SUCCESS
    }
}

fn report_stderr(message: std::fmt::Arguments<'_>) {
    let _ = writeln!(std::io::stderr().lock(), "{message}");
}

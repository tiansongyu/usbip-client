//! Unprivileged RustUSB frontend. Mutations use the common authenticated local service.

#[cfg(all(feature = "secure-transport", target_os = "linux"))]
mod agent;

#[cfg(target_os = "linux")]
mod device_watch;
mod diagnostics;
mod event_output;
#[cfg(any(feature = "rust-backend", feature = "secure-transport"))]
mod foreground;
mod logs;
#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
mod physical_server;
#[cfg(all(feature = "secure-transport", target_os = "linux"))]
mod secure_client;
#[cfg(all(test, feature = "rust-backend", target_os = "linux"))]
mod secure_foreground_tests;
#[cfg(feature = "secure-transport")]
mod secure_server;
mod shutdown_signals;
#[cfg(feature = "rust-backend")]
mod virtual_server;

use clap::{Parser, Subcommand};
use rustusb_core::{BusId, Error, Host, Peer, Result, VhciPort};
use rustusb_daemon::diagnostics::{HelperDiagnostics, diagnose_helper};
use rustusb_daemon::{Request, request};
use rustusb_native::diagnostics::{DiagnosticCheck, NativeDiagnostics, diagnose_native};
use serde::Serialize;
use std::{
    io::{self, Write},
    num::NonZeroU16,
    path::PathBuf,
    process::ExitCode,
    time::Duration,
};

#[derive(Debug, Parser)]
#[command(
    name = "rustusb",
    version,
    about = "Linux USB/IP device management (experimental preview)"
)]
struct Cli {
    /// Emit machine-readable JSON to stdout; logs remain on stderr.
    #[arg(long, global = true)]
    json: bool,
    /// Root-owned local helper socket.
    #[arg(long, global = true, default_value = "/run/rustusb/control.sock")]
    socket: PathBuf,
    /// Discovery timeout covering DNS, connect and response.
    #[arg(long,global=true,default_value_t=10,value_parser=clap::value_parser!(u64).range(1..=120))]
    timeout: u64,
    /// Emit JSON tracing logs on stderr.
    #[arg(long, global = true)]
    log_json: bool,
    /// Write sanitized bounded NDJSON to an existing private directory instead of stderr.
    #[arg(long, global = true, conflicts_with = "log_json")]
    log_directory: Option<PathBuf>,
    /// Segment byte limit (4096..=8388608); requires a file log directory.
    #[arg(long,global=true,requires="log_directory",value_parser=clap::value_parser!(u64).range(4096..=8388608))]
    log_max_bytes: Option<u64>,
    /// Total retained segments including active (2..=16).
    #[arg(long,global=true,requires="log_directory",value_parser=clap::value_parser!(u8).range(2..=16))]
    log_retain: Option<u8>,
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Export bounded private diagnostics without changing or stopping USB devices.
    Diagnostics(diagnostics::Args),
    /// Read sanitized stored logs or authorized agent audit projections.
    Logs(logs::Args),
    /// Run or query the nonroot shared application service using fixed profiles.
    #[cfg(all(feature = "secure-transport", target_os = "linux"))]
    Agent {
        /// Root-managed public manifest; callers cannot override profile endpoints.
        #[arg(long)]
        config: PathBuf,
        #[command(subcommand)]
        command: agent::Command,
    },
    /// Serve configured devices with mandatory mutual TLS and per-device authorization.
    #[cfg(feature = "secure-transport")]
    SecureServer {
        /// Root-managed startup configuration; endpoints and credentials cannot be overridden.
        #[arg(long)]
        config: PathBuf,
        /// Device engine; every configured key must belong to this backend.
        #[arg(long, value_enum)]
        backend: secure_server::Backend,
        /// Accept bounded local test controls; EOF also stops the service.
        #[arg(long)]
        control_stdin: bool,
    },
    /// Read the stable-key device catalog over the configured mutually authenticated TLS endpoint.
    #[cfg(feature = "secure-transport")]
    SecureDiscover {
        /// Root-managed configuration for this dedicated nonroot broker UID.
        #[arg(long)]
        config: PathBuf,
    },
    /// Import the configured stable device key and own its encrypted VHCI relay until stopped.
    #[cfg(feature = "secure-transport")]
    SecureAttach {
        /// Fixed trusted endpoint, identity and device key; no insecure overrides.
        #[arg(long)]
        config: PathBuf,
    },
    /// Inspect unresolved secure Native channels through the root helper (root only).
    NativePending {
        /// Continue a previous bounded page using its next_after value.
        #[arg(long)]
        after: Option<String>,
    },
    /// Verify a stopped Native channel and retire its record (root only; no USB writes).
    NativeReconcile { channel_id: String },
    /// Inspect unresolved physical grants through the root helper (root only).
    #[cfg(feature = "physical-nusb")]
    PhysicalPending {
        /// Continue a previous bounded page using its next_after value.
        #[arg(long)]
        after: Option<String>,
    },
    /// Verify restored physical state and retire its grant record (root only; no USB writes).
    #[cfg(feature = "physical-nusb")]
    PhysicalReconcile {
        lease_id: String,
        /// Acknowledge that the old generation is gone, without affecting its replacement.
        #[arg(long)]
        acknowledge_gone: bool,
    },
    /// Serve explicitly authorized whole USB devices through Linux USBFS (experimental, no TLS).
    #[cfg(feature = "physical-nusb")]
    PhysicalServer {
        /// Exact fingerprints in the helper's separate physical_device_ids allowlist.
        #[arg(long = "device-id", required = true)]
        device_ids: Vec<String>,
        /// Bind a literal address; default loopback only.
        #[arg(long, default_value = "127.0.0.1:3240")]
        listen: std::net::SocketAddr,
        /// Acknowledge an isolated administrator-managed test network.
        #[arg(long)]
        allow_insecure_test_network: bool,
        /// Exact permitted source IP; required for a non-loopback listener.
        #[arg(long)]
        allow_peer: Vec<std::net::IpAddr>,
        /// Read local snapshot/shutdown JSONL controls; EOF also closes the server.
        #[arg(long)]
        control_stdin: bool,
    },
    /// Serve two virtual test devices; this experimental mode provides no authentication or TLS.
    #[cfg(feature = "rust-backend")]
    VirtualServer {
        /// Bind a literal address. The default is loopback only.
        #[arg(long, default_value = "127.0.0.1:3240")]
        listen: std::net::SocketAddr,
        /// Explicitly acknowledge an isolated administrator-managed test network.
        #[arg(long)]
        allow_insecure_test_network: bool,
        /// Exact allowed peer IP; required together with the acknowledgement for non-loopback.
        #[arg(long)]
        allow_peer: Vec<std::net::IpAddr>,
        /// Accept bounded local JSONL test controls on stdin; EOF shuts the server down.
        #[arg(long)]
        control_stdin: bool,
    },
    /// Scan local devices without changing drivers. Discovery is not compatibility validation.
    Devices {
        /// Observe kernel USB hints and reconciled snapshots until interrupted.
        #[arg(long)]
        watch: bool,
    },
    /// Read installed tool, module and helper capabilities without loading modules.
    Doctor,
    /// Query a server's USB/IP device list over an administrator-managed trusted network.
    Discover {
        host: Host,
        #[arg(long, default_value = "3240")]
        port: NonZeroU16,
    },
    /// Export an administrator-allowlisted device fingerprint via the privileged helper.
    Export { device_id: String },
    /// Release an allowlisted export and request local driver reattachment.
    Unexport { device_id: String },
    /// Import a remote bus ID through an allowlisted endpoint and Linux VHCI.
    Attach {
        host: Host,
        bus_id: BusId,
        #[arg(long, default_value = "3240")]
        port: NonZeroU16,
    },
    /// Disconnect an authorized local VHCI port (0..255).
    Detach { port: u8 },
    /// Read authoritative native kernel session state through the helper.
    Sessions,
    /// Query helper and kernel status.
    Status,
}

#[derive(Serialize)]
struct Doctor {
    schema_version: u32,
    native: NativeDiagnostics,
    helper: HelperDiagnostics,
    required_for_export: [&'static str; 3],
    required_for_import: [&'static str; 3],
    built_in_encryption: bool,
    secure_broker_commands_available: bool,
    network_security_verified: bool,
    hardware_test_performed: bool,
    note: &'static str,
}

fn doctor_text(report: &Doctor, writer: &mut impl Write) -> Result<()> {
    let native = &report.native;
    writeln!(writer, "RustUSB local diagnostics (read-only)")?;
    writeln!(
        writer,
        "System: {} / {} / kernel {}",
        native.system.os,
        native.system.architecture,
        native.system.kernel_release.as_deref().unwrap_or("unknown")
    )?;
    let mut show = |name: &str, check: &DiagnosticCheck| -> Result<()> {
        writeln!(writer, "{name}: {:?} — {}", check.status, check.detail)?;
        writeln!(writer, "  Action: {}", check.action)?;
        Ok(())
    };
    show("kernel release", &native.system.kernel)?;
    for (name, tool) in [("usbip", &native.usbip), ("usbipd", &native.usbipd)] {
        show(name, &tool.check)?;
    }
    for driver in [&native.usbip_host, &native.vhci_hcd] {
        show(&format!("{} loaded", driver.module), &driver.loaded)?;
        show(&format!("{} sysfs", driver.module), &driver.sysfs)?;
        show(
            &format!("{} availability", driver.module),
            &driver.availability,
        )?;
    }
    for tool in [&native.usbip, &native.usbipd] {
        writeln!(
            writer,
            "Tool: {} / version {}",
            tool.path,
            tool.version.as_deref().unwrap_or("unknown")
        )?;
    }
    writeln!(
        writer,
        "Helper: {:?} — {}",
        report.helper.status, report.helper.detail
    )?;
    writeln!(writer, "  Action: {}", report.helper.action)?;
    writeln!(
        writer,
        "Export requires: {}",
        report.required_for_export.join(", ")
    )?;
    writeln!(
        writer,
        "Import requires: {}",
        report.required_for_import.join(", ")
    )?;
    writeln!(writer, "{}", report.note)?;
    Ok(())
}

fn output<T: Serialize>(value: &T) -> Result<()> {
    let mut stdout = io::stdout().lock();
    serde_json::to_writer_pretty(&mut stdout, value).map_err(|e| Error::State(e.to_string()))?;
    writeln!(stdout)?;
    Ok(())
}

async fn invoke(cli: &Cli, req: Request) -> Result<()> {
    let response = request(&cli.socket, req).await?;
    if !response.ok {
        return Err(Error::State(
            response
                .error
                .unwrap_or_else(|| "helper rejected request".into()),
        ));
    }
    output(&response.data)
}

async fn run(cli: Cli, monitor: Option<rustusb_logging::LogMonitor>) -> Result<()> {
    #[cfg(not(all(feature = "secure-transport", target_os = "linux")))]
    let _ = &monitor;
    match &cli.command {
        Command::Diagnostics(args) => {
            diagnostics::run(args.clone(), cli.socket.clone(), cli.json).await
        }
        Command::Logs(args) => logs::run(args.clone(), cli.json).await,
        #[cfg(all(feature = "secure-transport", target_os = "linux"))]
        Command::Agent { config, command } => {
            agent::run(config.clone(), command.clone(), monitor.clone()).await
        }
        #[cfg(feature = "secure-transport")]
        Command::SecureServer {
            config,
            backend,
            control_stdin,
        } => secure_server::run(&cli.socket, config.clone(), *backend, *control_stdin).await,
        Command::NativePending { after } => {
            invoke(
                &cli,
                Request::NativePending {
                    after: after.clone(),
                },
            )
            .await
        }
        Command::NativeReconcile { channel_id } => {
            invoke(
                &cli,
                Request::ReconcileNative {
                    channel_id: channel_id.clone(),
                },
            )
            .await
        }
        #[cfg(feature = "secure-transport")]
        Command::SecureDiscover { config } => {
            #[cfg(target_os = "linux")]
            {
                secure_client::discover(config.clone(), Duration::from_secs(cli.timeout)).await
            }
            #[cfg(not(target_os = "linux"))]
            {
                let _ = config;
                Err(Error::Unsupported(
                    "trusted secure broker configuration requires Linux".into(),
                ))
            }
        }
        #[cfg(feature = "secure-transport")]
        Command::SecureAttach { config } => {
            #[cfg(target_os = "linux")]
            {
                secure_client::attach(
                    &cli.socket,
                    config.clone(),
                    Duration::from_secs(cli.timeout),
                )
                .await
            }
            #[cfg(not(target_os = "linux"))]
            {
                let _ = config;
                Err(Error::Unsupported(
                    "secure VHCI import requires Linux".into(),
                ))
            }
        }
        #[cfg(feature = "physical-nusb")]
        Command::PhysicalServer {
            device_ids,
            listen,
            allow_insecure_test_network,
            allow_peer,
            control_stdin,
        } => {
            #[cfg(target_os = "linux")]
            {
                physical_server::run(
                    &cli.socket,
                    device_ids.clone(),
                    *listen,
                    *allow_insecure_test_network,
                    allow_peer.clone(),
                    *control_stdin,
                )
                .await
            }
            #[cfg(not(target_os = "linux"))]
            {
                let _ = (
                    device_ids,
                    listen,
                    allow_insecure_test_network,
                    allow_peer,
                    control_stdin,
                );
                Err(Error::Unsupported(
                    "physical USBFS serving requires Linux".into(),
                ))
            }
        }
        #[cfg(feature = "rust-backend")]
        Command::VirtualServer {
            listen,
            allow_insecure_test_network,
            allow_peer,
            control_stdin,
        } => {
            virtual_server::run(
                *listen,
                *allow_insecure_test_network,
                allow_peer.clone(),
                *control_stdin,
            )
            .await
        }
        Command::Devices { watch } => {
            if *watch {
                #[cfg(target_os = "linux")]
                return device_watch::run(cli.json).await;
                #[cfg(not(target_os = "linux"))]
                return Err(Error::Unsupported(
                    "local USB monitoring requires Linux".into(),
                ));
            }
            let devices = rustusb_discovery::scan().await?;
            tracing::info!(
                device_count = devices.len(),
                "local device discovery completed"
            );
            tracing::info!(
                event_code = "discovery_completed",
                device_count = devices.len(),
                "local device scan completed"
            );
            if cli.json {
                return output(&devices);
            }
            let mut stdout = io::stdout().lock();
            writeln!(
                stdout,
                "{:<68} {:<11} {:<9} PRODUCT / EXPORT CONDITION",
                "DEVICE ID", "BUS ID", "VID:PID"
            )?;
            for d in devices {
                let condition = if d.exported {
                    "exported".to_owned()
                } else if d.export_blockers.is_empty() {
                    "eligible; unverified".to_owned()
                } else {
                    d.export_blockers.join("; ")
                };
                let product = d.product.as_deref().unwrap_or("unknown");
                writeln!(
                    stdout,
                    "{:<68} {:<11} {:04x}:{:04x}  {} / {}",
                    d.id, d.bus_id, d.vendor_id, d.product_id, product, condition
                )?;
            }
            Ok(())
        }
        Command::Doctor => {
            let (native, helper) = tokio::join!(diagnose_native(), diagnose_helper(&cli.socket));
            let result = Doctor {
                schema_version: 1,
                native,
                helper,
                required_for_export: ["native.usbip", "native.usbip_host.sysfs", "helper"],
                required_for_import: ["native.usbip", "native.vhci_hcd.sysfs", "helper"],
                built_in_encryption: false,
                secure_broker_commands_available: cfg!(feature = "secure-transport"),
                network_security_verified: false,
                hardware_test_performed: false,
                note: "Component observations are independent. Device/peer policy, usbipd listener, VPN/firewall/TLS, remote reachability, and USB I/O require separate verification. No module was loaded, service started, device bound, or session imported.",
            };
            if cli.json {
                output(&result)
            } else {
                doctor_text(&result, &mut io::stdout().lock())
            }
        }
        Command::Discover { host, port } => {
            let devices = rustusb_protocol::discover(
                &Peer {
                    host: host.clone(),
                    port: *port,
                },
                Duration::from_secs(cli.timeout),
            )
            .await?;
            tracing::info!(
                device_count = devices.len(),
                "remote device discovery completed"
            );
            if cli.json {
                return output(&devices);
            }
            let mut stdout = io::stdout().lock();
            for d in devices {
                writeln!(
                    stdout,
                    "{:<12} {:04x}:{:04x} class={:02x} interfaces={} (unverified)",
                    d.bus_id,
                    d.vendor_id,
                    d.product_id,
                    d.class,
                    d.interfaces.len()
                )?;
            }
            Ok(())
        }
        Command::Export { device_id } => {
            invoke(
                &cli,
                Request::Export {
                    device_id: device_id.clone(),
                },
            )
            .await
        }
        Command::Unexport { device_id } => {
            invoke(
                &cli,
                Request::Unexport {
                    device_id: device_id.clone(),
                },
            )
            .await
        }
        Command::Attach { host, bus_id, port } => {
            invoke(
                &cli,
                Request::Attach {
                    peer: Peer {
                        host: host.clone(),
                        port: *port,
                    },
                    bus_id: bus_id.clone(),
                },
            )
            .await
        }
        Command::Detach { port } => {
            invoke(
                &cli,
                Request::Detach {
                    port: VhciPort(*port),
                },
            )
            .await
        }
        #[cfg(feature = "physical-nusb")]
        Command::PhysicalPending { after } => {
            invoke(
                &cli,
                Request::PhysicalPending {
                    after: after.clone(),
                },
            )
            .await
        }
        #[cfg(feature = "physical-nusb")]
        Command::PhysicalReconcile {
            lease_id,
            acknowledge_gone,
        } => {
            invoke(
                &cli,
                Request::ReconcilePhysical {
                    lease_id: lease_id.clone(),
                    acknowledge_gone: *acknowledge_gone,
                },
            )
            .await
        }
        Command::Sessions => invoke(&cli, Request::Sessions).await,
        Command::Status => invoke(&cli, Request::Status).await,
    }
}

fn logging_options(cli: &Cli) -> rustusb_logging::Result<Option<rustusb_logging::Options>> {
    let Some(directory) = cli.log_directory.clone() else {
        return Ok(None);
    };
    if matches!(cli.command, Command::Logs(_) | Command::Diagnostics(_)) {
        return Err(rustusb_logging::Error::InvalidInput(
            "a diagnostic or log observer cannot enable a file writer",
        ));
    }
    let mut options = rustusb_logging::Options::new(directory);
    if let Some(bytes) = cli.log_max_bytes {
        options.segment_bytes = bytes;
    }
    if let Some(retained) = cli.log_retain {
        options.retained_segments = retained;
    }
    options.validate()?;
    Ok(Some(options))
}

fn main() -> ExitCode {
    let cli = Cli::parse();
    let options = match logging_options(&cli) {
        Ok(options) => options,
        Err(error) => {
            report_stderr(format_args!("logging initialization failed: {error}"));
            return ExitCode::FAILURE;
        }
    };
    // Checked blocking log startup precedes Tokio and any USB acquisition.
    let logger = match rustusb_logging::initialize(options, cli.log_json) {
        Ok(logger) => logger,
        Err(error) => {
            report_stderr(format_args!("logging initialization failed: {error}"));
            return ExitCode::FAILURE;
        }
    };
    let monitor = logger.monitor();
    let runtime = match tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
    {
        Ok(runtime) => runtime,
        Err(error) => {
            report_stderr(format_args!("runtime initialization failed: {error}"));
            if let Err(error) = logger.shutdown() {
                report_stderr(format_args!(
                    "logging shutdown failed before device work: {error}"
                ));
            }
            return ExitCode::FAILURE;
        }
    };
    let operation = runtime.block_on(execute(cli, monitor));
    // execute preserves the existing actual-owner cleanup tree. Only then may
    // logging close admission, drain records and join its real file workers.
    match logger.shutdown() {
        Ok(_) => operation,
        Err(error) => {
            report_stderr(format_args!(
                "logging failed after the requested operation; inspect its result before retrying: {error}"
            ));
            ExitCode::FAILURE
        }
    }
}

fn report_stderr(message: std::fmt::Arguments<'_>) {
    // This is the last error path, possibly after stderr itself failed. Keep
    // the caller's failure status without another panic or stdout fallback.
    let _ = writeln!(io::stderr().lock(), "{message}");
}

async fn execute(cli: Cli, monitor: Option<rustusb_logging::LogMonitor>) -> ExitCode {
    let json = cli.json;
    let owns_signal_cleanup = match &cli.command {
        Command::Logs(_) | Command::Diagnostics(_) | Command::Devices { watch: true } => true,
        #[cfg(all(feature = "secure-transport", target_os = "linux"))]
        Command::Agent {
            command: agent::Command::Run,
            ..
        } => true,
        #[cfg(feature = "rust-backend")]
        Command::VirtualServer { .. } => true,
        #[cfg(feature = "physical-nusb")]
        Command::PhysicalServer { .. } => true,
        #[cfg(feature = "secure-transport")]
        Command::SecureServer { .. }
        | Command::SecureDiscover { .. }
        | Command::SecureAttach { .. } => true,
        _ => false,
    };
    let result = if owns_signal_cleanup {
        // The server must await its connection/lease tree on shutdown signals.
        // Racing it with the helper-client cancellation branch would drop that
        // cleanup future and incorrectly return a helper-operation diagnostic.
        run(cli, monitor).await
    } else {
        tokio::select! {
            result=run(cli, monitor)=>result,
            signal=tokio::signal::ctrl_c()=>match signal {
                Ok(())=>Err(Error::State("cancelled; an accepted helper operation may still complete. Inspect sessions/status before retrying".into())),
                Err(e)=>Err(Error::Io(e)),
            },
        }
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            if json && !owns_signal_cleanup {
                let _ = output(&serde_json::json!({"ok":false,"error":error.to_string()}));
            }
            tracing::error!(event_code="operation_failed", failure=rustusb_logging::FailureCategory::from(&error).as_str(), error=%error,"operation failed");
            ExitCode::FAILURE
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cli_schema_and_input_validation() {
        use clap::CommandFactory;
        Cli::command().debug_assert();
        assert!(Cli::try_parse_from(["rustusb", "devices", "--json"]).is_ok());
        assert!(
            Cli::try_parse_from([
                "rustusb",
                "native-pending",
                "--after",
                "00000000-0000-0000-0000-000000000001"
            ])
            .is_ok()
        );
        assert!(
            Cli::try_parse_from([
                "rustusb",
                "native-reconcile",
                "00000000-0000-0000-0000-000000000001"
            ])
            .is_ok()
        );
        assert!(Cli::try_parse_from(["rustusb", "attach", "::1", "1-2", "--port", "3241"]).is_ok());
        for args in [
            vec!["rustusb", "detach", "256"],
            vec!["rustusb", "attach", "-bad", "../1"],
            vec!["rustusb", "discover", "host", "--port", "0"],
            vec!["rustusb", "--timeout", "0", "devices"],
        ] {
            assert!(Cli::try_parse_from(args).is_err());
        }
        #[cfg(feature = "secure-transport")]
        {
            assert!(
                Cli::try_parse_from([
                    "rustusb",
                    "secure-server",
                    "--config",
                    "/etc/rustusb/server.json",
                    "--backend",
                    "native"
                ])
                .is_ok()
            );
            assert!(
                Cli::try_parse_from([
                    "rustusb",
                    "secure-server",
                    "--config",
                    "/etc/rustusb/server.json",
                    "--backend",
                    "native",
                    "--listen",
                    "0.0.0.0:3240"
                ])
                .is_err()
            );
            assert!(
                Cli::try_parse_from([
                    "rustusb",
                    "secure-discover",
                    "--config",
                    "/etc/rustusb/client.json"
                ])
                .is_ok()
            );
            assert!(
                Cli::try_parse_from([
                    "rustusb",
                    "secure-attach",
                    "--config",
                    "/etc/rustusb/client.json"
                ])
                .is_ok()
            );
            for override_flag in [
                "--host",
                "--port",
                "--insecure",
                "--device-id",
                "--private-key",
            ] {
                assert!(
                    Cli::try_parse_from([
                        "rustusb",
                        "secure-attach",
                        "--config",
                        "/etc/rustusb/client.json",
                        override_flag,
                        "ignored"
                    ])
                    .is_err()
                );
            }
        }
        #[cfg(feature = "rust-backend")]
        {
            assert!(
                Cli::try_parse_from([
                    "rustusb",
                    "secure-server",
                    "--config",
                    "/etc/rustusb/server.json",
                    "--backend",
                    "virtual"
                ])
                .is_ok()
            );
            assert!(
                Cli::try_parse_from([
                    "rustusb",
                    "secure-server",
                    "--config",
                    "/etc/rustusb/server.json",
                    "--backend",
                    "virtual",
                    "--listen",
                    "0.0.0.0:3240"
                ])
                .is_err()
            );
        }
    }
}

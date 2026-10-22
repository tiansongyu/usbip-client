//! Unprivileged RustUSB frontend. Mutations use the common authenticated local service.

use clap::{Parser, Subcommand};
use rustusb_core::{BusId, DeviceDiscovery, Error, Host, Peer, Result, VhciPort};
use rustusb_daemon::{Request, request};
use rustusb_linux::SysfsDiscovery;
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
    about = "Linux USB/IP device management (Phase 1 preview)"
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
    #[command(subcommand)]
    command: Command,
}

#[derive(Debug, Subcommand)]
enum Command {
    /// Scan local devices without changing drivers. Discovery is not compatibility validation.
    Devices,
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
    operating_system: &'static str,
    architecture: &'static str,
    usbip_tool_present: bool,
    usbipd_tool_present: bool,
    usbip_host_loaded: bool,
    vhci_hcd_loaded: bool,
    usb_sysfs_present: bool,
    helper_socket_present: bool,
    built_in_encryption: bool,
    hardware_redirection_verified: bool,
    note: &'static str,
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

async fn run(cli: Cli) -> Result<()> {
    match &cli.command {
        Command::Devices => {
            let devices = tokio::task::spawn_blocking(|| SysfsDiscovery::default().scan())
                .await
                .map_err(|e| Error::State(format!("discovery task: {e}")))??;
            tracing::info!(
                device_count = devices.len(),
                "local device discovery completed"
            );
            if cli.json {
                return output(&devices);
            }
            let mut stdout = io::stdout().lock();
            writeln!(
                stdout,
                "DEVICE ID                             BUS ID      VID:PID    PRODUCT / EXPORT CONDITION"
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
                    "{:<37} {:<11} {:04x}:{:04x}  {} / {}",
                    d.id, d.bus_id, d.vendor_id, d.product_id, product, condition
                )?;
            }
            Ok(())
        }
        Command::Doctor => {
            let socket = cli.socket.clone();
            let result=tokio::task::spawn_blocking(move || Doctor {
                operating_system:std::env::consts::OS,architecture:std::env::consts::ARCH,
                usbip_tool_present:std::path::Path::new("/usr/bin/usbip").exists(),
                usbipd_tool_present:std::path::Path::new("/usr/bin/usbipd").exists(),
                usbip_host_loaded:std::path::Path::new("/sys/module/usbip_host").exists(),
                vhci_hcd_loaded:std::path::Path::new("/sys/module/vhci_hcd").exists(),
                usb_sysfs_present:std::path::Path::new("/sys/bus/usb/devices").is_dir(),
                helper_socket_present:socket.exists(),built_in_encryption:false,hardware_redirection_verified:false,
                note:"Read-only checks; installed wrappers may need matching linux-tools. Load modules and configure VPN/firewall as administrator. No device was bound or imported.",
            }).await.map_err(|e|Error::State(e.to_string()))?;
            output(&result)
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
        Command::Sessions => invoke(&cli, Request::Sessions).await,
        Command::Status => invoke(&cli, Request::Status).await,
    }
}

#[tokio::main]
async fn main() -> ExitCode {
    let cli = Cli::parse();
    let json = cli.json;
    let filter = tracing_subscriber::EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info"));
    let initialized = if cli.log_json {
        tracing_subscriber::fmt()
            .with_env_filter(filter)
            .with_writer(io::stderr)
            .json()
            .try_init()
    } else {
        tracing_subscriber::fmt()
            .with_env_filter(filter)
            .with_writer(io::stderr)
            .with_ansi(false)
            .try_init()
    };
    if let Err(error) = initialized {
        eprintln!("logging initialization failed: {error}");
        return ExitCode::FAILURE;
    }
    let result = tokio::select! {
        result=run(cli)=>result,
        signal=tokio::signal::ctrl_c()=>match signal {
            Ok(())=>Err(Error::State("cancelled; an accepted helper operation may still complete. Inspect sessions/status before retrying".into())),
            Err(e)=>Err(Error::Io(e)),
        },
    };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            if json {
                let _ = output(&serde_json::json!({"ok":false,"error":error.to_string()}));
            }
            tracing::error!(error=%error,"operation failed");
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
        assert!(Cli::try_parse_from(["rustusb", "attach", "::1", "1-2", "--port", "3241"]).is_ok());
        for args in [
            vec!["rustusb", "detach", "256"],
            vec!["rustusb", "attach", "-bad", "../1"],
            vec!["rustusb", "discover", "host", "--port", "0"],
            vec!["rustusb", "--timeout", "0", "devices"],
        ] {
            assert!(Cli::try_parse_from(args).is_err());
        }
    }
}

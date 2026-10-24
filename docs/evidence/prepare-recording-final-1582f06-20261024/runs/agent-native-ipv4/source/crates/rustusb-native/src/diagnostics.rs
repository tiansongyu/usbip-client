//! Read-only, bounded observations of the Linux Native backend prerequisites.
//!
//! Only the fixed version subcommands are executed. This module never loads a
//! module, binds a device, starts a server, or verifies a network security claim.

use crate::NativeBackend;
use serde::Serialize;
use std::{
    io,
    path::{Component, Path, PathBuf},
    process::Stdio,
    time::Duration,
};
use tokio::{
    fs,
    io::{AsyncRead, AsyncReadExt},
    process::Command,
    time::{Instant, timeout, timeout_at},
};

const TOOL_DEADLINE: Duration = Duration::from_secs(3);
const READ_DEADLINE: Duration = Duration::from_secs(2);
const KERNEL_DEADLINE: Duration = Duration::from_secs(3);
const OUTPUT_LIMIT: usize = 8192;
const INDEX_LIMIT: usize = 4 * 1024 * 1024;

/// Stable observation labels; availability never implies successful redirection.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagnosticStatus {
    /// The requested read-only observation succeeded.
    Available,
    /// A required filesystem object is absent.
    Missing,
    /// The object exists but has an unexpected type or invalid contents.
    Invalid,
    /// The executable or one of its parents has unsafe ownership or permissions.
    Untrusted,
    /// The tool is not an executable regular file.
    NotExecutable,
    /// A read or command exceeded its deadline.
    Timeout,
    /// The version command exited unsuccessfully.
    Nonzero,
    /// The daemon's version command requires root before processing its arguments.
    RequiresRoot,
    /// A command exceeded the bounded diagnostic output budget.
    OutputLimit,
    /// The observation could not establish the requested capability.
    Unknown,
    /// The operating system does not provide this Linux backend.
    Unsupported,
    /// An I/O operation failed without establishing absence.
    IoError,
    /// The module is not currently represented in the loaded-module sysfs tree.
    NotLoaded,
    /// The matching kernel's module index identifies a built-in driver.
    BuiltIn,
    /// The matching kernel configuration explicitly disables the driver.
    Disabled,
}

/// One observation and an action that can improve or validate its result.
#[derive(Debug, Clone, Serialize)]
pub struct DiagnosticCheck {
    /// Machine-readable outcome of this observation.
    pub status: DiagnosticStatus,
    /// Bounded, non-sensitive description; never raw subprocess stderr.
    pub detail: String,
    /// Suggested next action; diagnostics do not execute it.
    pub action: String,
}

/// Operating-system identity, independent of any tool or module readiness.
#[derive(Debug, Clone, Serialize)]
pub struct SystemDiagnostic {
    /// Compile-target operating system name.
    pub os: String,
    /// Compile-target CPU architecture.
    pub architecture: String,
    /// Running kernel release from the fixed procfs attribute, when valid.
    pub kernel_release: Option<String>,
    /// Outcome of identifying the Linux running kernel.
    pub kernel: DiagnosticCheck,
}

/// Separate loaded, effective-interface, and install-time kernel observations.
#[derive(Debug, Clone, Serialize)]
pub struct KernelDiagnostic {
    /// Linux module name using underscores, rather than the sysfs driver name.
    pub module: String,
    /// Whether a real module directory was observed in sysfs.
    pub loaded: DiagnosticCheck,
    /// Whether the driver exposes valid, usable-shaped sysfs interfaces.
    pub sysfs: DiagnosticCheck,
    /// Evidence from the matching kernel's module files or configuration.
    pub availability: DiagnosticCheck,
}

/// A fixed tool's trusted-file and bounded version-subcommand result.
#[derive(Debug, Clone, Serialize)]
pub struct ToolDiagnostic {
    /// Fixed executable path; no caller-provided program is accepted.
    pub path: String,
    /// Combined trust and execution result.
    pub check: DiagnosticCheck,
    /// Safe ASCII single-line version, only after a successful version command.
    pub version: Option<String>,
}

/// Native backend observations, not an overall security or hardware certificate.
#[derive(Debug, Clone, Serialize)]
pub struct NativeDiagnostics {
    /// Operating-system and running-kernel identity.
    pub system: SystemDiagnostic,
    /// Native device-export prerequisites.
    pub usbip_host: KernelDiagnostic,
    /// Native device-import prerequisites.
    pub vhci_hcd: KernelDiagnostic,
    /// Fixed usbip utility version probe.
    pub usbip: ToolDiagnostic,
    /// Fixed usbipd version probe; no daemon is started.
    pub usbipd: ToolDiagnostic,
    /// Explicit boundaries of what these observations establish.
    pub scope: String,
}

struct ProbePaths {
    release: PathBuf,
    modules: PathBuf,
    boot: PathBuf,
    loaded: PathBuf,
    host_driver: PathBuf,
    backend: NativeBackend,
}

impl Default for ProbePaths {
    fn default() -> Self {
        Self {
            release: "/proc/sys/kernel/osrelease".into(),
            modules: "/lib/modules".into(),
            boot: "/boot".into(),
            loaded: "/sys/module".into(),
            host_driver: "/sys/bus/usb/drivers/usbip-host".into(),
            backend: NativeBackend::default(),
        }
    }
}

/// Diagnose fixed Linux Native prerequisites without changing system state.
///
/// Kernel reads and trusted tool probes are bounded. The two version commands
/// each have a three-second total deadline plus at most two seconds to reap a
/// killed child; after the two-second kernel-identity read they run concurrently.
/// Dropping this future kills a running child through `kill_on_drop`. Helpers,
/// authorization, VPN/TLS, remote reachability, and actual USB I/O are outside
/// this report and must be checked separately.
pub async fn diagnose_native() -> NativeDiagnostics {
    let paths = ProbePaths::default();
    let system = identify_system(&paths).await;
    let linux = std::env::consts::OS == "linux";
    let (usbip_host, vhci_hcd, usbip, usbipd) = tokio::join!(
        kernel_diagnostic(
            &paths,
            "usbip_host",
            system.kernel_release.as_deref(),
            linux
        ),
        kernel_diagnostic(&paths, "vhci_hcd", system.kernel_release.as_deref(), linux),
        probe_fixed_tool(Tool::Usbip, linux),
        probe_fixed_tool(Tool::Usbipd, linux),
    );
    NativeDiagnostics {
        system,
        usbip_host,
        vhci_hcd,
        usbip,
        usbipd,
        scope: "Read-only local prerequisites only. No module loading, USB binding, server startup, VPN/TLS verification, remote reachability test, or hardware redirection test was performed.".into(),
    }
}

fn check(
    status: DiagnosticStatus,
    detail: impl Into<String>,
    action: impl Into<String>,
) -> DiagnosticCheck {
    DiagnosticCheck {
        status,
        detail: detail.into(),
        action: action.into(),
    }
}

fn io_check(error: &io::Error, detail: &str, action: &str) -> DiagnosticCheck {
    let status = if error.kind() == io::ErrorKind::NotFound {
        DiagnosticStatus::Missing
    } else {
        DiagnosticStatus::IoError
    };
    check(status, format!("{detail} ({:?})", error.kind()), action)
}

async fn identify_system(paths: &ProbePaths) -> SystemDiagnostic {
    let mut result = SystemDiagnostic {
        os: std::env::consts::OS.into(),
        architecture: std::env::consts::ARCH.into(),
        kernel_release: None,
        kernel: check(
            DiagnosticStatus::Unsupported,
            "Linux Native backend is unavailable on this operating system",
            "Use a supported Linux host; Windows requires an independently evaluated driver",
        ),
    };
    if result.os != "linux" {
        return result;
    }
    match timeout(READ_DEADLINE, read_bounded(&paths.release, 256)).await {
        Ok(Ok(bytes)) => {
            let release = std::str::from_utf8(&bytes).ok().map(str::trim);
            if let Some(release) = release.filter(|value| {
                !value.is_empty()
                    && value.len() <= 128
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_alphanumeric() || b"._-+".contains(&byte))
            }) {
                result.kernel_release = Some(release.into());
                result.kernel = check(
                    DiagnosticStatus::Available,
                    "Running Linux kernel release identified",
                    "Use tools and modules packaged for this exact running kernel",
                );
            } else {
                result.kernel = check(
                    DiagnosticStatus::Invalid,
                    "Kernel release attribute is not a bounded safe release token",
                    "Check the procfs mount and running-kernel identity",
                );
            }
        }
        Ok(Err(error)) => {
            result.kernel = io_check(
                &error,
                "Running-kernel release could not be read",
                "Check that procfs is mounted and readable",
            )
        }
        Err(_) => {
            result.kernel = check(
                DiagnosticStatus::Timeout,
                "Running-kernel release read exceeded two seconds",
                "Check the procfs mount before retrying",
            )
        }
    }
    result
}

async fn kernel_diagnostic(
    paths: &ProbePaths,
    module: &str,
    release: Option<&str>,
    linux: bool,
) -> KernelDiagnostic {
    let fallback = |status, detail: &str| KernelDiagnostic {
        module: module.into(),
        loaded: check(
            status,
            detail,
            "Ask the administrator to inspect this Linux kernel and its module packages",
        ),
        sysfs: check(
            status,
            detail,
            "Check the sysfs mount and matching USB/IP driver",
        ),
        availability: check(
            status,
            detail,
            "Inspect the matching kernel configuration and module package",
        ),
    };
    if !linux {
        return fallback(
            DiagnosticStatus::Unsupported,
            "Linux Native driver is unavailable on this operating system",
        );
    }
    let result = timeout(KERNEL_DEADLINE, async {
        let loaded = match fs::metadata(paths.loaded.join(module)).await {
            Ok(metadata) if metadata.is_dir() => check(DiagnosticStatus::Available, "Loaded-module sysfs directory observed", "Confirm the effective driver interface below; loading alone does not establish readiness"),
            Ok(_) => check(DiagnosticStatus::Invalid, "Loaded-module path is not a directory", "Check that /sys is a genuine, complete sysfs mount"),
            Err(error) if error.kind() == io::ErrorKind::NotFound => check(DiagnosticStatus::NotLoaded, "No loaded-module sysfs directory observed", "An administrator may load the matching usbip_host or vhci_hcd module after reviewing policy"),
            Err(error) => io_check(&error, "Loaded-module state could not be read", "Check sysfs visibility and read permissions"),
        };
        let sysfs = if module == "usbip_host" { host_sysfs(&paths.host_driver).await } else { vhci_sysfs(&paths.backend).await };
        let availability = module_availability(paths, module, release).await;
        KernelDiagnostic { module: module.into(), loaded, sysfs, availability }
    }).await;
    match result {
        Ok(result) => result,
        Err(_) => fallback(
            DiagnosticStatus::Timeout,
            "Kernel prerequisite reads exceeded three seconds",
        ),
    }
}

async fn host_sysfs(driver: &Path) -> DiagnosticCheck {
    match fs::metadata(driver).await {
        Ok(metadata) if metadata.is_dir() => {}
        Ok(_) => {
            return check(
                DiagnosticStatus::Invalid,
                "usbip-host driver path is not a directory",
                "Check the sysfs mount and usbip_host driver",
            );
        }
        Err(error) => {
            return io_check(
                &error,
                "usbip-host driver directory unavailable",
                "An administrator should verify usbip_host is loaded and sysfs is mounted",
            );
        }
    }
    for name in ["bind", "unbind", "match_busid", "rebind"] {
        match fs::metadata(driver.join(name)).await {
            Ok(metadata) if metadata.is_file() => {}
            Ok(_) => {
                return check(
                    DiagnosticStatus::Invalid,
                    "usbip-host control attribute has an unexpected type",
                    "Inspect the sysfs mount and kernel USB/IP driver ABI",
                );
            }
            Err(error) => {
                return io_check(
                    &error,
                    "usbip-host control attribute unavailable",
                    "Inspect the matching kernel driver; a directory alone does not establish support",
                );
            }
        }
    }
    check(
        DiagnosticStatus::Available,
        "usbip-host driver and all four expected control attributes observed; no writes attempted",
        "Use authorized test devices and the restricted helper for export validation",
    )
}

async fn vhci_sysfs(backend: &NativeBackend) -> DiagnosticCheck {
    match backend.port_states().await {
        Ok(ports) => check(
            DiagnosticStatus::Available,
            format!(
                "Validated complete VHCI snapshot: {} ports, {} active",
                ports.len(),
                ports.iter().filter(|port| port.status != 4).count()
            ),
            "Use an authorized test peer and verify device enumeration and actual I/O after import",
        ),
        Err(rustusb_core::Error::Unsupported(_)) => check(
            DiagnosticStatus::Missing,
            "No supported VHCI controller/status interface observed",
            "An administrator should verify vhci_hcd is loaded; the adapter requires 1..=256 ports",
        ),
        Err(rustusb_core::Error::Timeout(_)) => check(
            DiagnosticStatus::Timeout,
            "VHCI sysfs snapshot exceeded its deadline",
            "Check sysfs and inspect active connections before retrying",
        ),
        Err(rustusb_core::Error::Io(error)) => io_check(
            &error,
            "VHCI sysfs snapshot could not be read",
            "Check sysfs visibility and read permissions",
        ),
        Err(_) => check(
            DiagnosticStatus::Invalid,
            "VHCI snapshot is incomplete or inconsistent",
            "Inspect kernel VHCI state and reconcile existing connections; retry after hotplug settles",
        ),
    }
}

async fn module_availability(
    paths: &ProbePaths,
    module: &str,
    release: Option<&str>,
) -> DiagnosticCheck {
    let Some(release) = release else {
        return check(
            DiagnosticStatus::Unknown,
            "Running kernel is unknown; no module-package claim can be made",
            "Identify the running kernel before installing matching USB/IP modules",
        );
    };
    let directory = paths.modules.join(release);
    let mut unreadable = false;
    match read_bounded(&directory.join("modules.dep"), INDEX_LIMIT).await {
        Ok(bytes) => {
            if let Ok(index) = std::str::from_utf8(&bytes) {
                for (line_number, line) in index.lines().enumerate() {
                    if line_number % 1024 == 0 {
                        tokio::task::yield_now().await;
                    }
                    let Some((entry, _)) = line.split_once(':') else {
                        continue;
                    };
                    if module_entry(entry, module) {
                        match fs::metadata(directory.join(entry)).await {
                            Ok(metadata) if metadata.is_file() => {
                                return check(
                                    DiagnosticStatus::Available,
                                    "Matching running-kernel module index and regular module file observed; loadability was not tested",
                                    "Have the administrator load the matching module after reviewing policy; the current helper does not load modules",
                                );
                            }
                            _ => unreadable = true,
                        }
                    }
                }
            } else {
                unreadable = true;
            }
        }
        Err(_) => unreadable = true,
    }
    if let Ok(bytes) = read_bounded(&directory.join("modules.builtin"), INDEX_LIMIT).await
        && let Ok(index) = std::str::from_utf8(&bytes)
    {
        for (line_number, entry) in index.lines().enumerate() {
            if line_number % 1024 == 0 {
                tokio::task::yield_now().await;
            }
            if module_entry(entry, module) {
                return check(
                    DiagnosticStatus::BuiltIn,
                    "Matching running-kernel built-in module index contains this driver",
                    "Verify the effective sysfs interface; built-in indexing does not prove usable USB redirection",
                );
            }
        }
    }
    let config_name = if module == "usbip_host" {
        "CONFIG_USBIP_HOST"
    } else {
        "CONFIG_USBIP_VHCI_HCD"
    };
    if let Ok(bytes) =
        read_bounded(&paths.boot.join(format!("config-{release}")), INDEX_LIMIT).await
        && let Ok(config) = std::str::from_utf8(&bytes)
    {
        let disabled = format!("# {config_name} is not set");
        let disabled_value = format!("{config_name}=n");
        let builtin = format!("{config_name}=y");
        let modular = format!("{config_name}=m");
        for (line_number, line) in config.lines().enumerate() {
            if line_number % 1024 == 0 {
                tokio::task::yield_now().await;
            }
            if line == disabled || line == disabled_value {
                return check(
                    DiagnosticStatus::Disabled,
                    "Matching kernel configuration explicitly disables this driver",
                    "Install or boot a supported kernel configured with USB/IP support",
                );
            }
            if line == builtin {
                return check(
                    DiagnosticStatus::BuiltIn,
                    "Matching kernel configuration enables the driver built in",
                    "Verify the effective sysfs interface; no module-load operation is necessary",
                );
            }
            if line == modular {
                return check(
                    DiagnosticStatus::Unknown,
                    "Kernel configuration enables a module but its matching installed module file was not established",
                    "Install the USB/IP module package for the running kernel and regenerate its module index",
                );
            }
        }
    }
    check(
        DiagnosticStatus::Unknown,
        if unreadable {
            "Matching module index/file or kernel configuration was absent, unreadable, or invalid"
        } else {
            "No matching module or readable configuration establishes driver availability"
        },
        "Inspect the running kernel's modules.dep, modules.builtin, and configuration; absence of evidence does not prove the driver is disabled",
    )
}

fn module_entry(entry: &str, module: &str) -> bool {
    let path = Path::new(entry);
    if entry.len() > 512
        || !path
            .components()
            .all(|part| matches!(part, Component::Normal(_)))
    {
        return false;
    }
    let Some(name) = path.file_name().and_then(|name| name.to_str()) else {
        return false;
    };
    let plain = name
        .strip_suffix(".xz")
        .or_else(|| name.strip_suffix(".gz"))
        .or_else(|| name.strip_suffix(".zst"))
        .unwrap_or(name);
    plain
        .strip_suffix(".ko")
        .is_some_and(|name| name.replace('-', "_") == module)
}

async fn read_bounded(path: &Path, limit: usize) -> io::Result<Vec<u8>> {
    let metadata = fs::metadata(path).await?;
    if !metadata.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "expected a regular file",
        ));
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(rustix::fs::OFlags::NONBLOCK.bits() as i32);
    let file = options.open(path).await?;
    if !file.metadata().await?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "file type changed",
        ));
    }
    let mut bytes = Vec::new();
    file.take((limit + 1) as u64)
        .read_to_end(&mut bytes)
        .await?;
    if bytes.len() > limit {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "read limit exceeded",
        ));
    }
    Ok(bytes)
}

#[derive(Clone, Copy)]
enum Tool {
    Usbip,
    Usbipd,
}

impl Tool {
    fn path(self) -> &'static str {
        match self {
            Self::Usbip => "/usr/bin/usbip",
            Self::Usbipd => "/usr/bin/usbipd",
        }
    }
    fn argument(self) -> &'static str {
        match self {
            Self::Usbip => "version",
            Self::Usbipd => "--version",
        }
    }
}

async fn probe_fixed_tool(tool: Tool, linux: bool) -> ToolDiagnostic {
    if !linux {
        return ToolDiagnostic {
            path: tool.path().into(),
            version: None,
            check: check(
                DiagnosticStatus::Unsupported,
                "Linux Native utility is not probed on this operating system",
                "Use a supported Linux host",
            ),
        };
    }
    let deadline = Instant::now() + TOOL_DEADLINE;
    let verified = timeout_at(deadline, trusted_tool(Path::new(tool.path()))).await;
    let result = match verified {
        Ok(Ok(path)) => run_version(&path, tool, deadline).await,
        Ok(Err(result)) => (result, None),
        Err(_) => (
            check(
                DiagnosticStatus::Timeout,
                "Fixed tool trust inspection exceeded three seconds",
                "Check the tool filesystem and package installation",
            ),
            None,
        ),
    };
    ToolDiagnostic {
        path: tool.path().into(),
        check: result.0,
        version: result.1,
    }
}

async fn trusted_tool(path: &Path) -> Result<PathBuf, DiagnosticCheck> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let canonical = fs::canonicalize(path).await.map_err(|error| {
            io_check(
                &error,
                "Fixed utility could not be resolved",
                "Install the distribution USB/IP tools for this running kernel",
            )
        })?;
        for executable in [path, canonical.as_path()] {
            let metadata = fs::metadata(executable).await.map_err(|error| {
                io_check(
                    &error,
                    "Fixed utility metadata could not be read",
                    "Repair the distribution USB/IP tool installation",
                )
            })?;
            if !metadata.is_file() || metadata.mode() & 0o111 == 0 {
                return Err(check(
                    DiagnosticStatus::NotExecutable,
                    "Fixed utility is not an executable regular file",
                    "Repair the distribution package's utility type and executable permissions",
                ));
            }
            let mut current = Some(executable);
            while let Some(ancestor) = current {
                let metadata = fs::metadata(ancestor).await.map_err(|error| {
                    io_check(
                        &error,
                        "Utility parent metadata could not be read",
                        "Repair the fixed utility path and parent directories",
                    )
                })?;
                if metadata.uid() != 0 || metadata.mode() & 0o022 != 0 {
                    return Err(check(
                        DiagnosticStatus::Untrusted,
                        "Fixed utility, resolved target, or parent is not root-owned and protected from group/world writes",
                        "Ask the administrator to repair the trusted package path; diagnostics will not execute it",
                    ));
                }
                current = ancestor.parent();
            }
        }
        Ok(canonical)
    }
    #[cfg(not(unix))]
    {
        let _ = path;
        Err(check(
            DiagnosticStatus::Unsupported,
            "Unix executable trust metadata is unavailable",
            "Use a supported Linux host",
        ))
    }
}

async fn run_version(
    path: &Path,
    tool: Tool,
    deadline: Instant,
) -> (DiagnosticCheck, Option<String>) {
    let mut command = Command::new(path);
    command
        .arg(tool.argument())
        .env_clear()
        .env("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")
        .env("LANG", "C")
        .env("LC_ALL", "C")
        .current_dir("/")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            return (
                io_check(
                    &error,
                    "Fixed version command could not start",
                    "Check the utility, its interpreter/loader, and executable permissions",
                ),
                None,
            );
        }
    };
    let Some(stdout) = child.stdout.take() else {
        return failed_child(
            &mut child,
            DiagnosticStatus::IoError,
            "Version stdout pipe unavailable",
        )
        .await;
    };
    let Some(stderr) = child.stderr.take() else {
        return failed_child(
            &mut child,
            DiagnosticStatus::IoError,
            "Version stderr pipe unavailable",
        )
        .await;
    };
    let outcome = timeout_at(deadline, async {
        tokio::try_join!(child.wait(), capture_output(stdout), capture_output(stderr))
    })
    .await;
    match outcome {
        Ok(Ok((status, stdout, stderr))) if status.success() => {
            let version = safe_version(&stdout).or_else(|| safe_version(&stderr));
            if version.is_some() {
                (
                    check(
                        DiagnosticStatus::Available,
                        "Trusted fixed version command succeeded",
                        "Keep the distribution tool package aligned with the running kernel; this probe does not test device operations",
                    ),
                    version,
                )
            } else {
                (
                    check(
                        DiagnosticStatus::Invalid,
                        "Version command succeeded without a bounded safe ASCII version line",
                        "Inspect the distribution wrapper and matching kernel-specific utility installation",
                    ),
                    None,
                )
            }
        }
        Ok(Ok((status, stdout, stderr))) => {
            let requires_root = matches!(tool, Tool::Usbipd) && root_required(&stdout, &stderr);
            let state = if requires_root {
                DiagnosticStatus::RequiresRoot
            } else {
                DiagnosticStatus::Nonzero
            };
            let detail = if requires_root {
                "usbipd rejected the unprivileged version probe before reporting a version".into()
            } else {
                format!(
                    "Fixed version command exited unsuccessfully ({})",
                    status
                        .code()
                        .map_or_else(|| "signal".into(), |code| code.to_string())
                )
            };
            (
                check(
                    state,
                    detail,
                    if requires_root {
                        "Have the administrator verify the packaged usbipd version; do not run the entire CLI as root or start an unprotected server"
                    } else {
                        "Install the matching kernel-specific USB/IP tools and inspect the distribution wrapper"
                    },
                ),
                None,
            )
        }
        Ok(Err(error)) if error.kind() == io::ErrorKind::InvalidData => {
            failed_child(
                &mut child,
                DiagnosticStatus::OutputLimit,
                "Fixed version command exceeded an 8 KiB output stream limit",
            )
            .await
        }
        Ok(Err(_)) => {
            failed_child(
                &mut child,
                DiagnosticStatus::IoError,
                "Fixed version command output or wait failed",
            )
            .await
        }
        Err(_) => {
            failed_child(
                &mut child,
                DiagnosticStatus::Timeout,
                "Fixed version command exceeded its three-second deadline",
            )
            .await
        }
    }
}

async fn failed_child(
    child: &mut tokio::process::Child,
    status: DiagnosticStatus,
    detail: &str,
) -> (DiagnosticCheck, Option<String>) {
    let _ = child.start_kill();
    let reaped = matches!(
        timeout(Duration::from_secs(2), child.wait()).await,
        Ok(Ok(_))
    );
    (
        check(
            status,
            format!(
                "{detail}; {}",
                if reaped {
                    "child reaped"
                } else {
                    "child reap was not confirmed within two seconds"
                }
            ),
            "Inspect the distribution USB/IP tool installation before retrying; no management subcommand was executed",
        ),
        None,
    )
}

async fn capture_output(mut stream: impl AsyncRead + Unpin) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    (&mut stream)
        .take((OUTPUT_LIMIT + 1) as u64)
        .read_to_end(&mut bytes)
        .await?;
    if bytes.len() > OUTPUT_LIMIT {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "version output limit",
        ));
    }
    Ok(bytes)
}

fn safe_version(bytes: &[u8]) -> Option<String> {
    let text = std::str::from_utf8(bytes)
        .ok()?
        .trim_end_matches(['\r', '\n']);
    if text.is_empty()
        || text.len() > 160
        || !text
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b" ._+-:/()".contains(&byte))
        || !text.starts_with("usbip")
        || !text.bytes().any(|byte| byte.is_ascii_digit())
    {
        return None;
    }
    Some(text.into())
}

fn root_required(stdout: &[u8], stderr: &[u8]) -> bool {
    // Recognize only the upstream daemon's pre-option root gate. Arbitrary
    // stderr is never copied into structured diagnostics or tracing output.
    [stdout, stderr].into_iter().any(|bytes| {
        std::str::from_utf8(bytes).is_ok_and(|text| {
            text.lines().any(|line| {
                matches!(
                    line.trim(),
                    "usbipd: error: not running as root?" | "usbipd: error: not running as root"
                )
            })
        })
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{error::Error, sync::Arc};

    type TestResult = Result<(), Box<dyn Error>>;

    struct Fixture {
        directory: tempfile::TempDir,
        paths: ProbePaths,
    }

    impl Fixture {
        fn new() -> Result<Self, Box<dyn Error>> {
            let directory = tempfile::tempdir()?;
            let root = directory.path();
            for child in [
                "modules/test-kernel",
                "boot",
                "loaded",
                "platform",
                "devices",
                "records",
            ] {
                std::fs::create_dir_all(root.join(child))?;
            }
            std::fs::write(root.join("release"), "test-kernel\n")?;
            let mut backend = NativeBackend::default();
            let configuration = Arc::get_mut(&mut backend.configuration)
                .ok_or("unexpected shared fixture configuration")?;
            configuration.platform = root.join("platform");
            configuration.devices = root.join("devices");
            configuration.records = root.join("records");
            let paths = ProbePaths {
                release: root.join("release"),
                modules: root.join("modules"),
                boot: root.join("boot"),
                loaded: root.join("loaded"),
                host_driver: root.join("host-driver"),
                backend,
            };
            Ok(Self { directory, paths })
        }

        fn path(&self, child: &str) -> PathBuf {
            self.directory.path().join(child)
        }

        #[cfg(unix)]
        fn script(&self, body: &str) -> Result<PathBuf, Box<dyn Error>> {
            use std::os::unix::fs::PermissionsExt;
            let path = self.path("version-tool");
            std::fs::write(&path, format!("#!/bin/sh\n{body}\n"))?;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755))?;
            Ok(path)
        }
    }

    #[test]
    fn module_entries_require_exact_safe_paths_and_names() {
        assert!(module_entry(
            "kernel/drivers/usb/usbip/usbip-host.ko.zst",
            "usbip_host"
        ));
        assert!(module_entry(
            "kernel/drivers/usb/usbip/vhci-hcd.ko",
            "vhci_hcd"
        ));
        for entry in [
            "../usbip-host.ko",
            "/usbip-host.ko",
            "usbip-host.ko.bad",
            "kernel/usbip-hostile.ko",
            "kernel/../usbip-host.ko",
        ] {
            assert!(!module_entry(entry, "usbip_host"));
        }
    }

    #[tokio::test]
    async fn module_installation_and_loaded_sysfs_are_separate_observations() -> TestResult {
        let fixture = Fixture::new()?;
        let absent =
            kernel_diagnostic(&fixture.paths, "usbip_host", Some("test-kernel"), true).await;
        assert_eq!(absent.loaded.status, DiagnosticStatus::NotLoaded);
        assert_eq!(absent.sysfs.status, DiagnosticStatus::Missing);
        assert_eq!(absent.availability.status, DiagnosticStatus::Unknown);
        std::fs::write(
            fixture.path("modules/test-kernel/modules.dep"),
            "kernel/usbip-host.ko:\n",
        )?;
        let indexed = module_availability(&fixture.paths, "usbip_host", Some("test-kernel")).await;
        assert_eq!(indexed.status, DiagnosticStatus::Unknown);
        std::fs::create_dir(fixture.path("modules/test-kernel/kernel"))?;
        std::fs::write(fixture.path("modules/test-kernel/kernel/usbip-host.ko"), [])?;
        let installed =
            kernel_diagnostic(&fixture.paths, "usbip_host", Some("test-kernel"), true).await;
        assert_eq!(installed.availability.status, DiagnosticStatus::Available);
        assert_eq!(installed.loaded.status, DiagnosticStatus::NotLoaded);
        std::fs::write(fixture.path("loaded/usbip_host"), [])?;
        assert_eq!(
            kernel_diagnostic(&fixture.paths, "usbip_host", Some("test-kernel"), true)
                .await
                .loaded
                .status,
            DiagnosticStatus::Invalid
        );
        Ok(())
    }

    #[tokio::test]
    async fn kernel_configuration_distinguishes_disabled_builtin_and_unknown() -> TestResult {
        let fixture = Fixture::new()?;
        let config = fixture.path("boot/config-test-kernel");
        for (contents, expected) in [
            (
                "# CONFIG_USBIP_HOST is not set\n",
                DiagnosticStatus::Disabled,
            ),
            ("CONFIG_USBIP_HOST=y\n", DiagnosticStatus::BuiltIn),
            ("CONFIG_USBIP_HOST=m\n", DiagnosticStatus::Unknown),
        ] {
            std::fs::write(&config, contents)?;
            assert_eq!(
                module_availability(&fixture.paths, "usbip_host", Some("test-kernel"))
                    .await
                    .status,
                expected
            );
        }
        assert_eq!(
            module_availability(&fixture.paths, "usbip_host", None)
                .await
                .status,
            DiagnosticStatus::Unknown
        );
        std::fs::write(
            fixture.path("modules/test-kernel/modules.builtin"),
            "kernel/vhci-hcd.ko\n",
        )?;
        assert_eq!(
            module_availability(&fixture.paths, "vhci_hcd", Some("test-kernel"))
                .await
                .status,
            DiagnosticStatus::BuiltIn
        );
        Ok(())
    }

    #[tokio::test]
    async fn effective_driver_checks_require_attributes_and_valid_complete_status() -> TestResult {
        let fixture = Fixture::new()?;
        std::fs::write(&fixture.paths.host_driver, [])?;
        assert_eq!(
            host_sysfs(&fixture.paths.host_driver).await.status,
            DiagnosticStatus::Invalid
        );
        std::fs::remove_file(&fixture.paths.host_driver)?;
        std::fs::create_dir(&fixture.paths.host_driver)?;
        assert_eq!(
            host_sysfs(&fixture.paths.host_driver).await.status,
            DiagnosticStatus::Missing
        );
        for name in ["bind", "unbind", "match_busid", "rebind"] {
            std::fs::write(fixture.paths.host_driver.join(name), [])?;
        }
        assert_eq!(
            host_sysfs(&fixture.paths.host_driver).await.status,
            DiagnosticStatus::Available
        );
        let vhci = fixture.path("platform/vhci_hcd.0");
        std::fs::create_dir(&vhci)?;
        std::fs::write(vhci.join("nports"), "1\n")?;
        std::fs::write(vhci.join("status"), "not a status table\n")?;
        assert_eq!(
            vhci_sysfs(&fixture.paths.backend).await.status,
            DiagnosticStatus::Invalid
        );
        std::fs::write(
            vhci.join("status"),
            "hub port sta spd dev sockfd local_busid\nhs 0 4 0 00000000 0 0-0\n",
        )?;
        assert_eq!(
            vhci_sysfs(&fixture.paths.backend).await.status,
            DiagnosticStatus::Available
        );
        Ok(())
    }

    #[tokio::test]
    async fn kernel_release_and_file_reads_are_bounded_and_do_not_accept_directories() -> TestResult
    {
        let fixture = Fixture::new()?;
        assert_eq!(
            identify_system(&fixture.paths)
                .await
                .kernel_release
                .as_deref(),
            Some("test-kernel")
        );
        std::fs::write(&fixture.paths.release, "../../other-kernel\n")?;
        assert_eq!(
            identify_system(&fixture.paths).await.kernel.status,
            DiagnosticStatus::Invalid
        );
        assert!(read_bounded(&fixture.paths.loaded, 16).await.is_err());
        std::fs::write(&fixture.paths.release, [b'A'; 257])?;
        assert!(read_bounded(&fixture.paths.release, 256).await.is_err());
        Ok(())
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn trust_checks_distinguish_missing_nonexecutable_and_untrusted_paths() -> TestResult {
        let _process_fixture = crate::PROCESS_FIXTURE_LOCK.lock().await;
        let fixture = Fixture::new()?;
        let missing = trusted_tool(&fixture.path("missing")).await;
        assert!(matches!(
            missing,
            Err(DiagnosticCheck {
                status: DiagnosticStatus::Missing,
                ..
            })
        ));
        let regular = fixture.path("regular");
        std::fs::write(&regular, "not executable")?;
        let not_executable = trusted_tool(&regular).await;
        assert!(matches!(
            not_executable,
            Err(DiagnosticCheck {
                status: DiagnosticStatus::NotExecutable,
                ..
            })
        ));
        let script = fixture.script("exit 0")?;
        let untrusted = trusted_tool(&script).await;
        assert!(matches!(
            untrusted,
            Err(DiagnosticCheck {
                status: DiagnosticStatus::Untrusted,
                ..
            })
        ));
        Ok(())
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn version_runner_uses_fixed_arguments_environment_and_safe_output() -> TestResult {
        let _process_fixture = crate::PROCESS_FIXTURE_LOCK.lock().await;
        let fixture = Fixture::new()?;
        let script = fixture.script("[ \"$#\" = 1 ] && [ \"$1\" = version ] && [ \"$PATH\" = /usr/sbin:/usr/bin:/sbin:/bin ] && [ \"$LC_ALL\" = C ] || exit 19\nprintf 'usbip (usbip-utils 2.0)\\n'")?;
        let (result, version) =
            run_version(&script, Tool::Usbip, Instant::now() + TOOL_DEADLINE).await;
        assert_eq!(result.status, DiagnosticStatus::Available, "{result:?}");
        assert_eq!(version.as_deref(), Some("usbip (usbip-utils 2.0)"));
        let script = fixture.script("printf 'PRIVATE-STDERR-CONTENT\\n' >&2\nexit 7")?;
        let (result, version) =
            run_version(&script, Tool::Usbip, Instant::now() + TOOL_DEADLINE).await;
        assert_eq!(result.status, DiagnosticStatus::Nonzero, "{result:?}");
        assert!(!result.detail.contains("PRIVATE"));
        assert!(version.is_none());
        let script = fixture.script("[ \"$1\" = --version ] || exit 19\nprintf 'usbipd: error: not running as root?\\n' >&2\nexit 1")?;
        let (result, _) = run_version(&script, Tool::Usbipd, Instant::now() + TOOL_DEADLINE).await;
        assert_eq!(result.status, DiagnosticStatus::RequiresRoot, "{result:?}");
        Ok(())
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn version_runner_timeout_and_output_limit_kill_and_reap() -> TestResult {
        let _process_fixture = crate::PROCESS_FIXTURE_LOCK.lock().await;
        let fixture = Fixture::new()?;
        let pidfile = fixture.path("pid");
        let script = fixture.script(&format!(
            "printf '%s' \"$$\" > '{}'\nexec /bin/sleep 30",
            pidfile.display()
        ))?;
        let (result, _) = run_version(
            &script,
            Tool::Usbip,
            Instant::now() + Duration::from_millis(150),
        )
        .await;
        assert_eq!(result.status, DiagnosticStatus::Timeout, "{result:?}");
        assert!(result.detail.ends_with("child reaped"));
        let pid = std::fs::read_to_string(&pidfile)?;
        assert!(!std::fs::exists(format!("/proc/{pid}"))?);
        let script = fixture.script("while :; do printf 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'; done")?;
        let (result, _) = run_version(&script, Tool::Usbip, Instant::now() + TOOL_DEADLINE).await;
        assert_eq!(result.status, DiagnosticStatus::OutputLimit, "{result:?}");
        assert!(result.detail.ends_with("child reaped"));
        Ok(())
    }

    #[test]
    fn version_does_not_expose_multiline_control_or_arbitrary_output() {
        assert_eq!(
            safe_version(b"usbipd (usbip-utils 2.0)\n"),
            Some("usbipd (usbip-utils 2.0)".into())
        );
        for bytes in [
            b"usbip 2.0\nsecret".as_slice(),
            b"usbip 2.0\x1b[31m",
            b"secret device 123",
            b"usbip unknown",
            b"usbip 2.0\tsecret",
        ] {
            assert!(safe_version(bytes).is_none());
        }
        assert!(!root_required(&[], b"secret: root required"));
    }
}

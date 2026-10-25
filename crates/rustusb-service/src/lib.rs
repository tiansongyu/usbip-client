//! Shared, nonroot application service for owned USB/IP jobs and local frontends.
//!
//! A frontend requests work; the service retains the real task and cooperatively
//! joins cleanup even when the requesting connection disappears.

pub mod automation;
#[cfg(target_os = "linux")]
pub mod catalog;
pub mod config;
pub mod context;
pub mod diagnostics;
/// Common read-only discovery used by all local frontends.
pub use rustusb_discovery as discovery;
pub mod foreground;
#[cfg(target_os = "linux")]
mod fs;
pub mod health;
#[cfg(target_os = "linux")]
pub mod ipc;
pub mod jobs;
pub mod model;
pub mod rpc;
mod rule_rejections;
mod rules;
mod run_context;
#[cfg(target_os = "linux")]
mod runtime;
mod store;

#[cfg(not(target_os = "linux"))]
use rustusb_core::Error;
use rustusb_core::Result;

/// Read a fresh Linux kernel UUID; no device or privileged state is changed.
pub(crate) fn fresh_id() -> Result<String> {
    #[cfg(target_os = "linux")]
    {
        let value = std::fs::read_to_string("/proc/sys/kernel/random/uuid")?;
        Ok(value.trim_end().to_owned())
    }
    #[cfg(not(target_os = "linux"))]
    {
        Err(Error::Unsupported(
            "application service requires Linux".into(),
        ))
    }
}

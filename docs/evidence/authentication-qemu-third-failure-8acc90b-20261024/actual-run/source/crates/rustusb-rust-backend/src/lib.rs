//! Opt-in, bounded USB/IP service with virtual profiles and a Linux USBFS adapter.
//!
//! Physical access requires the `physical-nusb` feature and explicitly delegated
//! device descriptors. Explicit TLS entry points authenticate peers and apply
//! stable device-key authorization to discovery and pre-claim IMPORT.
//! The Linux Native VHCI client remains responsible for kernel import operations.

mod counted;
mod descriptors;
mod device;
mod link;
mod managed;
mod monitor;
#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
mod physical;
mod server;
mod session;
mod wire;

pub use device::*;
pub use monitor::{ServerControl, ServerMonitor};
#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
pub use physical::*;
#[doc(hidden)]
pub use server::validate_server_frame;
pub use server::{SecureServerConfig, ServerConfig, ServerHandle};

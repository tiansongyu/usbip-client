//! Opt-in, bounded USB/IP service with virtual profiles and a Linux USBFS adapter.
//!
//! Physical access requires the `physical-nusb` feature and explicitly delegated
//! device descriptors. This service does not provide authentication or TLS.
//! The Linux Native VHCI client remains responsible for kernel import operations.

mod descriptors;
mod device;
#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
mod physical;
mod server;
mod session;
mod wire;

pub use device::*;
#[cfg(all(feature = "physical-nusb", target_os = "linux"))]
pub use physical::*;
#[doc(hidden)]
pub use server::validate_server_frame;
pub use server::{ServerConfig, ServerHandle};

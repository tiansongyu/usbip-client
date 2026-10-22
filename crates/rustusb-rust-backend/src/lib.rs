//! Opt-in, bounded USB/IP server for two explicitly virtual test devices.
//!
//! This crate does not access physical USB hardware or provide authentication/TLS.
//! The Linux Native VHCI client remains responsible for kernel import operations.

mod descriptors;
mod device;
mod server;
mod wire;

pub use device::*;
#[doc(hidden)]
pub use server::validate_server_frame;
pub use server::{ServerConfig, ServerHandle};

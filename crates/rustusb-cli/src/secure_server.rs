//! Dedicated-broker server entry point without credential or endpoint overrides.

use super::shutdown_signals::ShutdownSignals;
use rustusb_core::{DeviceBackend, DeviceKey, Error, Result};
use rustusb_service::jobs::LoadedJob;
use rustusb_transport::config::load_server;
use std::path::{Path, PathBuf};
use std::sync::Arc;

#[derive(Debug, Clone, Copy, clap::ValueEnum)]
pub(super) enum Backend {
    #[cfg(feature = "rust-backend")]
    Virtual,
    #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
    Usbfs,
    /// Linux usbip-host through anonymous helper-owned kernel channels.
    Native,
}

fn validate_pool(backend: Backend, devices: &[DeviceKey]) -> Result<()> {
    let expected = match backend {
        #[cfg(feature = "rust-backend")]
        Backend::Virtual => DeviceBackend::Virtual,
        #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
        Backend::Usbfs => DeviceBackend::Physical,
        Backend::Native => DeviceBackend::Native,
    };
    if devices.is_empty() || devices.iter().any(|device| device.backend() != expected) {
        return Err(Error::InvalidInput(
            "all configured devices must match the selected secure backend".into(),
        ));
    }
    Ok(())
}

pub(super) async fn run(
    socket: &Path,
    config_path: PathBuf,
    backend: Backend,
    control_stdin: bool,
) -> Result<()> {
    let mut signals = ShutdownSignals::new()?;
    // The actual blocking reader owns every credential buffer. Even if a
    // signal wins, join it before returning, then acquire no device or listener.
    let mut loading = tokio::task::spawn_blocking(move || load_server(&config_path));
    let loaded = tokio::select! {
        biased;
        signal = signals.recv() => {
            let _ = loading.await;
            return signal;
        }
        result = &mut loading => result
            .map_err(|_| Error::State("secure server configuration task failed".into()))?
            .map_err(|error| Error::InvalidInput(format!("secure server configuration: {error}")))?,
    };
    // This precedes every helper request, backend initialization and bind.
    validate_pool(backend, &loaded.devices)?;
    let loaded = Arc::new(loaded);
    let job = match backend {
        #[cfg(feature = "rust-backend")]
        Backend::Virtual => LoadedJob::VirtualServer(loaded),
        #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
        Backend::Usbfs => LoadedJob::PhysicalServer(loaded),
        Backend::Native => LoadedJob::NativeServer(loaded),
    };
    super::foreground::run(job, socket.to_owned(), control_stdin, signals).await
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustusb_core::VirtualProfile;

    #[test]
    fn namespace_mismatch_is_rejected_before_any_service_action() -> Result<()> {
        let virtual_key = DeviceKey::virtual_profile(VirtualProfile::Hid);
        let native = DeviceKey::native(&format!("usb-{}", "a".repeat(64)))?;
        let physical = DeviceKey::physical(&format!("usb-{}", "b".repeat(64)))?;
        assert!(validate_pool(Backend::Native, &[]).is_err());
        assert!(validate_pool(Backend::Native, std::slice::from_ref(&native)).is_ok());
        assert!(validate_pool(Backend::Native, &[native.clone(), virtual_key.clone()]).is_err());
        assert!(validate_pool(Backend::Native, std::slice::from_ref(&physical)).is_err());
        #[cfg(feature = "rust-backend")]
        {
            assert!(validate_pool(Backend::Virtual, &[]).is_err());
            assert!(validate_pool(Backend::Virtual, std::slice::from_ref(&virtual_key)).is_ok());
            for wrong in [native, physical.clone()] {
                assert!(validate_pool(Backend::Virtual, &[virtual_key.clone(), wrong]).is_err());
            }
        }
        #[cfg(all(feature = "physical-nusb", target_os = "linux"))]
        {
            assert!(validate_pool(Backend::Usbfs, &[virtual_key]).is_err());
            assert!(validate_pool(Backend::Usbfs, &[physical]).is_ok());
        }
        Ok(())
    }
}

//! Root-managed profile selection and per-operation OS-identity authorization.

use rustusb_core::{DeviceBackend, Error, Result};
use rustusb_transport::config::{LoadedClientConfig, LoadedServerConfig};
use serde::{Deserialize, Serialize};
use std::{
    collections::{BTreeMap, BTreeSet},
    path::{Component, Path, PathBuf},
    sync::Arc,
};

/// An action authorized independently for each local OS identity and profile.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    /// Read profiles, jobs, sessions and state events.
    Read,
    /// Start a fixed administrator-selected profile.
    Start,
    /// Request cooperative job or session stop.
    Stop,
    /// Perform an authenticated read-only remote catalog request.
    Discover,
    /// Read the bounded audit history.
    Audit,
}

/// Fixed implementation selected by the administrator, never by an RPC caller.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Backend {
    /// Authenticated client importing the exact configured remote device.
    Client,
    /// Linux usbip-host server through helper-owned anonymous channels.
    Native,
    /// Optional virtual USB/IP profiles.
    Virtual,
    /// Optional experimental physical USBFS server.
    Usbfs,
}

/// Public profile metadata, containing no private credentials or runtime handles.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    /// Bounded administrator-chosen label.
    pub id: String,
    /// Fixed backend selection.
    pub backend: Backend,
    /// Protected startup TLS configuration; RPC never overrides this path.
    pub config: PathBuf,
}

/// One operator's exact allowed profiles and actions.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Operator {
    /// Kernel-reported UID, not a claimed request field.
    pub uid: u32,
    /// Explicit bounded profile allowlist; no wildcard expansion.
    pub profiles: BTreeSet<String>,
    /// Explicit action allowlist.
    pub actions: BTreeSet<Action>,
}

/// Trusted schema-1 application manifest, safe for local clients to read.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    /// Exactly one.
    pub schema_version: u32,
    /// Dedicated nonzero service UID.
    pub broker_uid: u32,
    /// Fixed privileged helper endpoint.
    pub helper_socket: PathBuf,
    /// Broker-owned mode-0711 directory below protected root-owned ancestors.
    pub control_directory: PathBuf,
    /// Broker-owned mode-0700 directory below protected root-owned ancestors.
    pub data_directory: PathBuf,
    /// At most sixteen unique profiles.
    pub profiles: Vec<Profile>,
    /// At most sixty-four operator rules.
    pub operators: Vec<Operator>,
}

impl Manifest {
    /// Read only public protected metadata; this never loads a private key.
    pub fn read(path: &Path) -> Result<Self> {
        let bytes = rustusb_transport::config::read_root_document(path)
            .map_err(|_| Error::Permission("trusted agent manifest could not be read".into()))?;
        let value: Self = serde_json::from_slice(&bytes)
            .map_err(|_| Error::InvalidInput("agent manifest schema".into()))?;
        value.validate()?;
        Ok(value)
    }

    /// Validate all bounded labels, selections and rule references before startup.
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != 1
            || self.broker_uid == 0
            || self.profiles.is_empty()
            || self.profiles.len() > 16
            || self.operators.len() > 64
        {
            return Err(Error::InvalidInput(
                "agent manifest bounds/version/broker UID".into(),
            ));
        }
        for path in [
            &self.helper_socket,
            &self.control_directory,
            &self.data_directory,
        ] {
            valid_path(path)?;
        }
        if self.control_directory == self.data_directory
            || self
                .control_directory
                .join("control.sock")
                .as_os_str()
                .as_encoded_bytes()
                .len()
                > 107
        {
            return Err(Error::InvalidInput(
                "distinct agent directories and bounded UDS path required".into(),
            ));
        }
        let mut ids = BTreeSet::new();
        for profile in &self.profiles {
            valid_label(&profile.id)?;
            valid_path(&profile.config)?;
            if !ids.insert(&profile.id) {
                return Err(Error::InvalidInput("duplicate agent profile".into()));
            }
        }
        let mut uids = BTreeSet::new();
        for operator in &self.operators {
            if !uids.insert(operator.uid)
                || operator.profiles.is_empty()
                || operator.profiles.len() > 16
                || operator.actions.is_empty()
                || operator.profiles.iter().any(|id| !ids.contains(id))
            {
                return Err(Error::InvalidInput(
                    "agent operator rule or unknown profile".into(),
                ));
            }
        }
        Ok(())
    }

    /// Authorize the OS identity for this exact action and configured profile.
    pub fn authorize(&self, uid: u32, profile: &str, action: Action) -> Result<()> {
        if !self.profiles.iter().any(|p| p.id == profile) {
            return Err(denied());
        }
        if (uid == 0 && matches!(action, Action::Read | Action::Audit))
            || self.operators.iter().any(|r| {
                r.uid == uid && r.actions.contains(&action) && r.profiles.contains(profile)
            })
        {
            return Ok(());
        }
        Err(denied())
    }

    /// Fixed application endpoint; clients never supply a different socket in RPC.
    pub fn socket_path(&self) -> PathBuf {
        self.control_directory.join("control.sock")
    }
}

/// Validated immutable TLS inputs belonging to one fixed profile.
#[derive(Clone)]
pub enum Credentials {
    /// Exact configured client identity and remote device.
    Client(Arc<LoadedClientConfig>),
    /// Exact configured server identity, device pool and principal policy.
    Server(Arc<LoadedServerConfig>),
}

/// Fully validated startup inputs, loaded before any listener or device access.
pub struct LoadedConfig {
    /// Public trusted manifest and operator rules.
    pub manifest: Manifest,
    /// Bounded fixed profiles with loaded credential buffers.
    pub credentials: BTreeMap<String, Credentials>,
}

impl LoadedConfig {
    /// Load the manifest and every profile for the actual dedicated nonroot UID.
    /// This blocking routine must execute in an owned blocking task.
    pub fn load(path: &Path) -> Result<Self> {
        let manifest = Manifest::read(path)?;
        #[cfg(target_os = "linux")]
        if rustix::process::geteuid().as_raw() != manifest.broker_uid {
            return Err(Error::Permission(
                "agent must run as its dedicated nonroot broker UID".into(),
            ));
        }
        #[cfg(not(target_os = "linux"))]
        return Err(Error::Unsupported("agent requires Linux".into()));
        let mut credentials = BTreeMap::new();
        for profile in &manifest.profiles {
            let loaded = if profile.backend == Backend::Client {
                let loaded =
                    rustusb_transport::config::load_client(&profile.config).map_err(|_| {
                        Error::Permission("trusted client profile could not be loaded".into())
                    })?;
                if loaded.broker_uid != manifest.broker_uid {
                    return Err(denied());
                }
                Credentials::Client(Arc::new(loaded))
            } else {
                if profile.backend == Backend::Virtual && !cfg!(feature = "rust-backend")
                    || profile.backend == Backend::Usbfs
                        && !cfg!(all(feature = "physical-nusb", target_os = "linux"))
                {
                    return Err(Error::Unsupported(
                        "agent profile backend was not compiled".into(),
                    ));
                }
                let loaded =
                    rustusb_transport::config::load_server(&profile.config).map_err(|_| {
                        Error::Permission("trusted server profile could not be loaded".into())
                    })?;
                let expected = match profile.backend {
                    Backend::Native => DeviceBackend::Native,
                    Backend::Virtual => DeviceBackend::Virtual,
                    Backend::Usbfs => DeviceBackend::Physical,
                    Backend::Client => return Err(denied()),
                };
                if loaded.broker_uid != manifest.broker_uid
                    || loaded.devices.is_empty()
                    || loaded.devices.iter().any(|key| key.backend() != expected)
                {
                    return Err(Error::InvalidInput(
                        "agent profile device namespace/UID".into(),
                    ));
                }
                Credentials::Server(Arc::new(loaded))
            };
            credentials.insert(profile.id.clone(), loaded);
        }
        Ok(Self {
            manifest,
            credentials,
        })
    }
}

pub(crate) fn valid_label(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
    {
        return Err(Error::InvalidInput("bounded agent label".into()));
    }
    Ok(())
}

pub(crate) fn valid_path(path: &Path) -> Result<()> {
    if !path.is_absolute()
        || path.as_os_str().as_encoded_bytes().len() > 4096
        || path.components().count() > 64
        || path
            .as_os_str()
            .as_encoded_bytes()
            .iter()
            .any(|b| b.is_ascii_control())
        || path
            .components()
            .any(|c| !matches!(c, Component::RootDir | Component::Normal(_)))
    {
        return Err(Error::InvalidInput("absolute protected agent path".into()));
    }
    Ok(())
}
fn denied() -> Error {
    Error::Permission("agent profile action denied".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn manifest() -> Manifest {
        Manifest {
            schema_version: 1,
            broker_uid: 1001,
            helper_socket: "/run/rustusb/helper.sock".into(),
            control_directory: "/run/rustusb/agent".into(),
            data_directory: "/var/lib/rustusb/agent".into(),
            profiles: vec![Profile {
                id: "keyboard".into(),
                backend: Backend::Client,
                config: "/etc/rustusb/client.json".into(),
            }],
            operators: vec![Operator {
                uid: 1002,
                profiles: BTreeSet::from(["keyboard".into()]),
                actions: BTreeSet::from([Action::Read, Action::Start]),
            }],
        }
    }
    #[test]
    fn exact_profile_action_and_os_uid_are_required() -> Result<()> {
        let manifest = manifest();
        manifest.validate()?;
        assert!(manifest.authorize(1002, "keyboard", Action::Start).is_ok());
        assert!(manifest.authorize(1002, "keyboard", Action::Stop).is_err());
        assert!(manifest.authorize(1003, "keyboard", Action::Read).is_err());
        assert!(manifest.authorize(1002, "other", Action::Read).is_err());
        assert!(manifest.authorize(0, "keyboard", Action::Read).is_ok());
        assert!(manifest.authorize(0, "keyboard", Action::Start).is_err());
        Ok(())
    }
    #[test]
    fn malformed_or_ambiguous_manifests_fail_before_credentials() -> Result<()> {
        let mut value = manifest();
        value.broker_uid = 0;
        assert!(value.validate().is_err());
        value = manifest();
        value.profiles.push(value.profiles[0].clone());
        assert!(value.validate().is_err());
        value = manifest();
        value.operators[0].profiles.insert("absent".into());
        assert!(value.validate().is_err());
        value = manifest();
        value.data_directory = "/var/lib/../tmp".into();
        assert!(value.validate().is_err());
        Ok(())
    }
}

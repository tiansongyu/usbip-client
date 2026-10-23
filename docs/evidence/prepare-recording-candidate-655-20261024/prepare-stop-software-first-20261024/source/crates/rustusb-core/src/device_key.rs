//! Stable, backend-qualified authorization keys, separate from temporary Bus IDs.

use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::{fmt, str::FromStr};

/// The device implementation named by a stable authorization key.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DeviceBackend {
    /// Whole physical device delegated through Linux USBFS.
    Physical,
    /// Linux kernel usbip_host export.
    Native,
    /// One fixed simulated USB device profile.
    Virtual,
}

/// Fixed virtual profiles; these names cannot authorize a physical device.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VirtualProfile {
    /// The built-in virtual HID profile.
    Hid,
    /// The built-in virtual CDC ACM profile.
    CdcAcm,
}

/// A canonical stable device authorization key, never a temporary USB bus ID.
/// Physical fingerprints express administrator intent, not cryptographic hardware identity.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct DeviceKey(String);

impl DeviceKey {
    /// Construct a USBFS key from `usb-` and exactly 64 lowercase hexadecimal digits.
    pub fn physical(fingerprint: &str) -> Result<Self> {
        Self::from_fingerprint("physical", fingerprint)
    }

    /// Construct a Native export key from an existing candidate fingerprint.
    pub fn native(fingerprint: &str) -> Result<Self> {
        Self::from_fingerprint("native", fingerprint)
    }

    /// Construct the canonical key for a fixed virtual profile.
    pub fn virtual_profile(profile: VirtualProfile) -> Self {
        Self(match profile {
            VirtualProfile::Hid => "virtual:hid".into(),
            VirtualProfile::CdcAcm => "virtual:cdc-acm".into(),
        })
    }

    /// Borrow the canonical backend-qualified string.
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Return the implementation namespace selected by this validated key.
    pub fn backend(&self) -> DeviceBackend {
        if self.0.starts_with("physical:") {
            DeviceBackend::Physical
        } else if self.0.starts_with("native:") {
            DeviceBackend::Native
        } else {
            DeviceBackend::Virtual
        }
    }

    /// Borrow the exact fingerprint for a physical/Native key; virtual keys have none.
    pub fn fingerprint(&self) -> Option<&str> {
        self.0
            .strip_prefix("physical:")
            .or_else(|| self.0.strip_prefix("native:"))
    }

    fn from_fingerprint(namespace: &str, value: &str) -> Result<Self> {
        if !value.strip_prefix("usb-").is_some_and(|hex| {
            hex.len() == 64
                && hex
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        }) {
            return Err(Error::InvalidInput(
                "device key requires an exact lowercase device fingerprint".into(),
            ));
        }
        Ok(Self(format!("{namespace}:{value}")))
    }
}

impl FromStr for DeviceKey {
    type Err = Error;

    fn from_str(value: &str) -> Result<Self> {
        match value {
            "virtual:hid" => Ok(Self::virtual_profile(VirtualProfile::Hid)),
            "virtual:cdc-acm" => Ok(Self::virtual_profile(VirtualProfile::CdcAcm)),
            _ => {
                if let Some(fingerprint) = value.strip_prefix("physical:") {
                    Self::physical(fingerprint)
                } else if let Some(fingerprint) = value.strip_prefix("native:") {
                    Self::native(fingerprint)
                } else {
                    Err(Error::InvalidInput(
                        "unknown device key namespace or profile".into(),
                    ))
                }
            }
        }
    }
}

impl TryFrom<String> for DeviceKey {
    type Error = Error;

    fn try_from(value: String) -> Result<Self> {
        value.parse()
    }
}

impl From<DeviceKey> for String {
    fn from(value: DeviceKey) -> Self {
        value.0
    }
}

impl fmt::Display for DeviceKey {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_namespaces_round_trip_and_remain_distinct() -> Result<()> {
        let id = format!("usb-{}", "a".repeat(64));
        let keys = [
            DeviceKey::physical(&id)?,
            DeviceKey::native(&id)?,
            DeviceKey::virtual_profile(VirtualProfile::Hid),
            DeviceKey::virtual_profile(VirtualProfile::CdcAcm),
        ];
        assert_ne!(keys[0], keys[1]);
        assert_eq!(keys[0].fingerprint(), Some(id.as_str()));
        assert_eq!(keys[2].fingerprint(), None);
        assert_eq!(keys[0].backend(), DeviceBackend::Physical);
        assert_eq!(keys[1].backend(), DeviceBackend::Native);
        assert_eq!(keys[2].backend(), DeviceBackend::Virtual);
        for key in keys {
            assert_eq!(key.as_str().parse::<DeviceKey>()?, key);
            let encoded =
                serde_json::to_string(&key).map_err(|_| Error::State("test encoding".into()))?;
            let decoded: DeviceKey =
                serde_json::from_str(&encoded).map_err(|_| Error::State("test decoding".into()))?;
            assert_eq!(decoded, key);
        }
        Ok(())
    }

    #[test]
    fn untrusted_keys_cannot_alias_profiles_or_temporary_addresses() {
        for value in [
            "",
            "1-2",
            "physical:1-2",
            "usb-abc",
            "virtual:1-2",
            "virtual:other",
            "virtual:HID",
            "virtual:hid ",
            " virtual:hid",
            "native:usb-ABC",
            "physical:usb-",
            "native:virtual:hid",
            "virtual:hid\n",
        ] {
            assert!(value.parse::<DeviceKey>().is_err());
            let quoted = format!("{value:?}");
            assert!(serde_json::from_str::<DeviceKey>(&quoted).is_err());
        }
        assert!(
            format!("physical:usb-{}", "A".repeat(64))
                .parse::<DeviceKey>()
                .is_err()
        );
        assert!(
            format!("native:usb-{}", "0".repeat(65))
                .parse::<DeviceKey>()
                .is_err()
        );
    }
}

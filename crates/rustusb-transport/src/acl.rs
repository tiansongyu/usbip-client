use crate::{Error, Result};
pub use rustusb_core::{DeviceKey, VirtualProfile};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    sync::Arc,
};

const MAX_PRINCIPALS: usize = 64;
const MAX_PINS: usize = 2;
const MAX_DEVICES: usize = 16;
pub(crate) const MAX_CERT_BYTES: usize = 16 * 1024;

/// SHA-256 of a complete DER leaf certificate; a pin never replaces PKI validation.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct CertificatePin([u8; 32]);

impl CertificatePin {
    /// Parse exactly 64 lowercase hexadecimal digits without accepting prefixes or whitespace.
    pub fn from_hex(value: &str) -> Result<Self> {
        if value.len() != 64
            || !value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::InvalidInput("certificate fingerprint"));
        }
        let mut digest = [0_u8; 32];
        for (out, pair) in digest.iter_mut().zip(value.as_bytes().chunks_exact(2)) {
            let digit = |b: u8| {
                if b.is_ascii_digit() {
                    b - b'0'
                } else {
                    b - b'a' + 10
                }
            };
            *out = (digit(pair[0]) << 4) | digit(pair[1]);
        }
        Ok(Self(digest))
    }

    /// Hash bounded certificate bytes; this operation does not validate a certificate.
    /// Only the TLS server's completed standard verification can create a verified peer.
    pub fn from_der(der: &[u8]) -> Result<Self> {
        if der.is_empty() || der.len() > MAX_CERT_BYTES {
            return Err(Error::InvalidInput("certificate size"));
        }
        Ok(Self(Sha256::digest(der).into()))
    }

    /// Return the certificate digest for non-secret audit correlation.
    pub fn digest(&self) -> &[u8; 32] {
        &self.0
    }
}

impl fmt::Debug for CertificatePin {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("CertificatePin([redacted])")
    }
}

/// One administrator-defined principal and its exact certificate/device allowlists.
#[derive(Clone)]
pub struct PrincipalRule {
    id: String,
    pins: Vec<CertificatePin>,
    devices: Vec<DeviceKey>,
}

impl PrincipalRule {
    /// Validate an ASCII principal ID (1..64), 1..2 unique pins, and 1..16 unique devices.
    pub fn new(id: &str, pins: Vec<CertificatePin>, devices: Vec<DeviceKey>) -> Result<Self> {
        if id.is_empty()
            || id.len() > 64
            || !id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_.-".contains(&b))
        {
            return Err(Error::InvalidInput("principal identifier"));
        }
        if pins.is_empty()
            || pins.len() > MAX_PINS
            || pins.iter().collect::<BTreeSet<_>>().len() != pins.len()
        {
            return Err(Error::InvalidInput("principal certificate list"));
        }
        if devices.is_empty()
            || devices.len() > MAX_DEVICES
            || devices.iter().collect::<BTreeSet<_>>().len() != devices.len()
        {
            return Err(Error::InvalidInput("principal device list"));
        }
        Ok(Self {
            id: id.into(),
            pins,
            devices,
        })
    }
}

impl fmt::Debug for PrincipalRule {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("PrincipalRule")
            .field("id", &self.id)
            .field("pin_count", &self.pins.len())
            .field("device_count", &self.devices.len())
            .finish()
    }
}

/// Immutable startup authorization policy. An empty policy denies every certificate.
#[derive(Clone)]
pub struct AccessPolicy {
    principals: usize,
    by_pin: BTreeMap<CertificatePin, Arc<PrincipalRule>>,
}

impl AccessPolicy {
    /// Build at most 64 rules, rejecting repeated principal IDs and every shared pin.
    /// Reloading files does not change an existing policy or its authenticated streams.
    pub fn new(rules: Vec<PrincipalRule>) -> Result<Self> {
        if rules.len() > MAX_PRINCIPALS {
            return Err(Error::InvalidInput("principal count"));
        }
        let principals = rules.len();
        let mut ids = BTreeSet::new();
        let mut by_pin = BTreeMap::new();
        for rule in rules {
            if !ids.insert(rule.id.clone()) {
                return Err(Error::InvalidInput("duplicate principal"));
            }
            let rule = Arc::new(rule);
            for pin in &rule.pins {
                if by_pin.insert(*pin, rule.clone()).is_some() {
                    return Err(Error::InvalidInput("certificate assigned more than once"));
                }
            }
        }
        Ok(Self { principals, by_pin })
    }

    pub(crate) fn verified_peer(&self, pin: CertificatePin) -> Result<VerifiedPeer> {
        let principal = self
            .by_pin
            .get(&pin)
            .cloned()
            .ok_or(Error::UnmappedCertificate)?;
        Ok(VerifiedPeer { principal, pin })
    }
}

impl fmt::Debug for AccessPolicy {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AccessPolicy")
            .field("principal_count", &self.principals)
            .finish()
    }
}

/// A peer created only after completed mutual TLS and an exact configured leaf-pin match.
/// It describes authorization, not a device lease or proof of hardware identity.
#[derive(Clone)]
pub struct VerifiedPeer {
    principal: Arc<PrincipalRule>,
    pin: CertificatePin,
}

impl VerifiedPeer {
    /// Return the administrator's principal identifier, never a client-supplied name.
    pub fn principal(&self) -> &str {
        &self.principal.id
    }

    /// Return the authenticated leaf certificate's digest.
    pub fn certificate_pin(&self) -> CertificatePin {
        self.pin
    }

    /// Apply the same exact predicate to discovery and pre-claim IMPORT authorization.
    pub fn allows(&self, device: &DeviceKey) -> bool {
        self.principal.devices.contains(device)
    }

    /// Borrow the finite allowlist; registering or claiming devices remains the caller's job.
    pub fn allowed_devices(&self) -> &[DeviceKey] {
        &self.principal.devices
    }
}

impl fmt::Debug for VerifiedPeer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("VerifiedPeer")
            .field("principal", &self.principal.id)
            .finish()
    }
}

//! Strict administrator delegation and finite automatic-rule input types.
//!
//! These validation APIs neither start jobs nor persist rules. A valid policy or
//! rule is not a device capability, a nonstorage classification, an observation
//! of availability or a cleanup proof. Agent/RPC/scheduler integration must also
//! journal the rule occurrence and original Start intent in one transaction.

use crate::config::{Action, Backend, Credentials, LoadedConfig, valid_label};
use rustusb_core::{DeviceBackend, DeviceKey, Error, Result, application::RuleId};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeSet, path::Path};

/// Maximum complete root policy document, including JSON whitespace.
pub const MAX_POLICY_BYTES: usize = 64 * 1024;
/// Maximum distinct operator/profile grants in one startup policy.
pub const MAX_GRANTS: usize = 64;
/// Maximum rules an administrator may delegate to one operator/profile pair.
pub const MAX_RULES_PER_GRANT: u8 = 4;
/// Maximum device-key count in the complete fixed profile effect.
pub const MAX_PROFILE_DEVICES: usize = 16;

/// A condition kind; it never contains an executable or an arbitrary expression.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum TriggerKind {
    /// Every fixed Native/USBFS target has a fresh, safe local observation.
    LocalProfileReady,
    /// The exact fixed client target appears in an authenticated remote catalog.
    RemoteDeviceAvailable,
}

/// Explicit occurrence rearm intent, still subject to safety and old-owner checks.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RearmKind {
    /// Consume at most one occurrence until an explicit authorized edit.
    Once,
    /// A later distinct safe observation may be considered after actual release.
    NewObservation,
}

/// A bounded rule definition within a fixed administrator-selected profile.
///
/// The profile, caller UID and complete device pool are bound independently by
/// the service. In particular, this definition cannot narrow a multi-device
/// server job's actual effect to one selected device row.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "RuleSpecFile")]
pub struct RuleSpec {
    name: String,
    trigger: TriggerKind,
    settle_seconds: u16,
    rearm: RearmKind,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RuleSpecFile {
    name: String,
    trigger: TriggerKind,
    settle_seconds: u16,
    rearm: RearmKind,
}

impl RuleSpec {
    /// Validate a name of 1..64 UTF-8 bytes and a settle interval of 2..120 seconds.
    pub fn new(
        name: String,
        trigger: TriggerKind,
        settle_seconds: u16,
        rearm: RearmKind,
    ) -> Result<Self> {
        if name.is_empty()
            || name.len() > 64
            || name.trim() != name
            || name.chars().any(char::is_control)
            || !(2..=120).contains(&settle_seconds)
        {
            return Err(Error::InvalidInput(
                "automatic rule name or settle bounds".into(),
            ));
        }
        Ok(Self {
            name,
            trigger,
            settle_seconds,
            rearm,
        })
    }

    /// Bounded display name, carrying no authorization.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Closed condition kind.
    pub fn trigger(&self) -> TriggerKind {
        self.trigger
    }

    /// Required continuously fresh candidate interval in seconds.
    pub fn settle_seconds(&self) -> u16 {
        self.settle_seconds
    }

    /// Explicit rearm intent; this never permits storage recovery.
    pub fn rearm(&self) -> RearmKind {
        self.rearm
    }
}

impl TryFrom<RuleSpecFile> for RuleSpec {
    type Error = Error;

    fn try_from(value: RuleSpecFile) -> Result<Self> {
        Self::new(value.name, value.trigger, value.settle_seconds, value.rearm)
    }
}

/// Positive compare-and-swap input with room for one checked revision advance.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "u64", into = "u64")]
pub struct ExpectedRevision(u64);

impl ExpectedRevision {
    /// Reject zero and the exhausted maximum before a mutation can be admitted.
    pub fn new(value: u64) -> Result<Self> {
        if value == 0 || value == u64::MAX {
            return Err(Error::InvalidInput("automatic rule revision bounds".into()));
        }
        Ok(Self(value))
    }

    /// Exact committed revision the caller expects; not an authorization token.
    pub fn get(self) -> u64 {
        self.0
    }
}

impl TryFrom<u64> for ExpectedRevision {
    type Error = Error;

    fn try_from(value: u64) -> Result<Self> {
        Self::new(value)
    }
}

impl From<ExpectedRevision> for u64 {
    fn from(value: ExpectedRevision) -> Self {
        value.0
    }
}

/// Strict typed rule edit, independent of Start's deduplication namespace.
///
/// The future service must bind a separate original mutation RequestId and the
/// actual OS peer before persisting an edit. This type does not itself apply an
/// edit, start or stop a job, or acknowledge a durable transaction.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "change", rename_all = "snake_case", deny_unknown_fields)]
pub enum RuleMutation {
    /// Create a fresh rule; its identity is allocated by the service.
    Create {
        /// Bounded definition within the selected fixed profile.
        spec: RuleSpec,
        /// Whether to consider future conditions after durable authorization.
        enabled: bool,
    },
    /// Replace a definition using an exact current revision.
    Replace {
        /// Existing identity, never a new device selection.
        rule_id: RuleId,
        /// Positive revision with room for its successor.
        expected_revision: ExpectedRevision,
        /// Complete replacement definition.
        spec: RuleSpec,
    },
    /// Enable or pause future conditions, without claiming current-job cleanup.
    SetEnabled {
        /// Existing rule identity.
        rule_id: RuleId,
        /// Positive revision with room for its successor.
        expected_revision: ExpectedRevision,
        /// Future-trigger intent only.
        enabled: bool,
    },
    /// Retire a rule while preserving unresolved ownership/tombstone history.
    Delete {
        /// Existing rule identity.
        rule_id: RuleId,
        /// Positive revision with room for its successor.
        expected_revision: ExpectedRevision,
    },
}

/// One immutable administrator delegation, bound to the complete fixed effect.
/// These metadata contain no credentials, sockets, live owner or USB capability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct AutomationGrant {
    operator_uid: u32,
    profile: String,
    devices: BTreeSet<DeviceKey>,
    triggers: BTreeSet<TriggerKind>,
    rearm: BTreeSet<RearmKind>,
    max_rules: u8,
}

impl AutomationGrant {
    /// Exact OS UID explicitly delegated by the administrator.
    pub fn operator_uid(&self) -> u32 {
        self.operator_uid
    }
    /// Exact fixed profile ID.
    pub fn profile(&self) -> &str {
        &self.profile
    }
    /// Complete configured effect, not a user-selected subset.
    pub fn devices(&self) -> &BTreeSet<DeviceKey> {
        &self.devices
    }
    /// Finite condition kinds permitted for this backend.
    pub fn triggers(&self) -> &BTreeSet<TriggerKind> {
        &self.triggers
    }
    /// Explicit administrator-permitted occurrence policies.
    pub fn rearm(&self) -> &BTreeSet<RearmKind> {
        &self.rearm
    }
    /// Per-operator/profile rule admission ceiling, at most four.
    pub fn max_rules(&self) -> u8 {
        self.max_rules
    }
}

/// A strict root-managed startup policy, constructed only through trusted reading.
///
/// No Default/Deserialize or user-JSON constructor grants authority. Absence of
/// an explicitly loaded policy means no automation delegation. Changes require
/// cooperative service restart; this object is not a live-reload mechanism.
///
/// Ordinary user JSON cannot construct a validated delegation:
///
/// ```compile_fail
/// use rustusb_service::automation::AutomationPolicy;
/// let _policy: Result<AutomationPolicy, _> = serde_json::from_str("{}");
/// ```
#[derive(Debug, Clone)]
pub struct AutomationPolicy {
    broker_uid: u32,
    grants: Vec<AutomationGrant>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PolicyFile {
    schema_version: u32,
    broker_uid: u32,
    grants: Vec<GrantFile>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct GrantFile {
    operator_uid: u32,
    profile: String,
    devices: Vec<DeviceKey>,
    triggers: Vec<TriggerKind>,
    rearm: Vec<RearmKind>,
    max_rules: u8,
}

impl AutomationPolicy {
    /// Read a protected root document and bind every grant to loaded fixed inputs.
    ///
    /// This is a blocking startup routine: retain and join its actual blocking
    /// task before exposing a listener. It loads no additional TLS credentials,
    /// contacts no remote endpoint and performs no device operation.
    pub fn read(path: &Path, config: &LoadedConfig) -> Result<Self> {
        let bytes = rustusb_transport::config::read_root_document(path)
            .map_err(|_| Error::Permission("trusted automation policy could not be read".into()))?;
        Self::bind(&bytes, config)
    }

    fn bind(bytes: &[u8], config: &LoadedConfig) -> Result<Self> {
        if bytes.len() > MAX_POLICY_BYTES {
            return Err(Error::InvalidInput("automation policy byte bound".into()));
        }
        let file: PolicyFile = serde_json::from_slice(bytes)
            .map_err(|_| Error::InvalidInput("automation policy schema".into()))?;
        config.manifest.validate()?;
        if file.schema_version != 1
            || file.broker_uid == 0
            || file.broker_uid != config.manifest.broker_uid
            || file.grants.len() > MAX_GRANTS
        {
            return Err(Error::InvalidInput(
                "automation policy version, UID or grant bound".into(),
            ));
        }
        let mut identities = BTreeSet::new();
        let mut grants = Vec::with_capacity(file.grants.len());
        for raw in file.grants {
            valid_label(&raw.profile)?;
            if !identities.insert((raw.operator_uid, raw.profile.clone()))
                || !(1..=MAX_RULES_PER_GRANT).contains(&raw.max_rules)
                || raw.devices.is_empty()
                || raw.devices.len() > MAX_PROFILE_DEVICES
                || raw.triggers.is_empty()
                || raw.triggers.len() > 2
                || raw.rearm.is_empty()
                || raw.rearm.len() > 2
            {
                return Err(Error::InvalidInput(
                    "automation grant identity or bounds".into(),
                ));
            }
            let devices: BTreeSet<_> = raw.devices.iter().cloned().collect();
            let triggers: BTreeSet<_> = raw.triggers.iter().copied().collect();
            let rearm: BTreeSet<_> = raw.rearm.iter().copied().collect();
            if devices.len() != raw.devices.len()
                || triggers.len() != raw.triggers.len()
                || rearm.len() != raw.rearm.len()
            {
                return Err(Error::InvalidInput(
                    "duplicate automation grant selection".into(),
                ));
            }
            let grant = AutomationGrant {
                operator_uid: raw.operator_uid,
                profile: raw.profile,
                devices,
                triggers,
                rearm,
                max_rules: raw.max_rules,
            };
            grant_binding(config, &grant)?;
            grants.push(grant);
        }
        Ok(Self {
            broker_uid: file.broker_uid,
            grants,
        })
    }

    /// Borrow finite administrator metadata; filter by authorized UID/profile
    /// before publishing it to an unprivileged client.
    pub fn grants(&self) -> &[AutomationGrant] {
        &self.grants
    }

    /// Recheck explicit delegation, current profile binding and all action ACLs.
    ///
    /// The caller must use the actual OS peer for mutations, or the original
    /// persisted delegated UID for a background occurrence. This check does not
    /// authorize by a caller-supplied UID field and never claims SO_PEERCRED for
    /// an internally scheduled action. Root has no implicit automation grant.
    pub fn authorize<'a>(
        &'a self,
        config: &LoadedConfig,
        operator_uid: u32,
        profile: &str,
        spec: &RuleSpec,
    ) -> Result<&'a AutomationGrant> {
        let denied = || Error::Permission("automatic rule delegation denied".into());
        config.manifest.validate()?;
        if config.manifest.broker_uid != self.broker_uid {
            return Err(denied());
        }
        for action in [Action::Read, Action::Start, Action::Stop] {
            config.manifest.authorize(operator_uid, profile, action)?;
        }
        if spec.trigger == TriggerKind::RemoteDeviceAvailable {
            config
                .manifest
                .authorize(operator_uid, profile, Action::Discover)?;
        }
        let grant = self
            .grants
            .iter()
            .find(|grant| grant.operator_uid == operator_uid && grant.profile == profile)
            .ok_or_else(denied)?;
        if !grant.triggers.contains(&spec.trigger) || !grant.rearm.contains(&spec.rearm) {
            return Err(denied());
        }
        grant_binding(config, grant)?;
        Ok(grant)
    }
}

fn grant_binding(config: &LoadedConfig, grant: &AutomationGrant) -> Result<()> {
    let invalid =
        || Error::InvalidInput("automation grant must match its complete fixed profile".into());
    let profile = config
        .manifest
        .profiles
        .iter()
        .find(|p| p.id == grant.profile)
        .ok_or_else(invalid)?;
    let credentials = config.credentials.get(&grant.profile).ok_or_else(invalid)?;
    let (uid, keys, trigger) = match (profile.backend, credentials) {
        (Backend::Client, Credentials::Client(loaded)) => (
            loaded.broker_uid,
            vec![loaded.device.clone()],
            TriggerKind::RemoteDeviceAvailable,
        ),
        (Backend::Native | Backend::Usbfs, Credentials::Server(loaded)) => {
            let expected = if profile.backend == Backend::Native {
                DeviceBackend::Native
            } else {
                DeviceBackend::Physical
            };
            if loaded.devices.iter().any(|key| key.backend() != expected) {
                return Err(invalid());
            }
            (
                loaded.broker_uid,
                loaded.devices.clone(),
                TriggerKind::LocalProfileReady,
            )
        }
        _ => return Err(invalid()),
    };
    let set: BTreeSet<_> = keys.iter().cloned().collect();
    if uid != config.manifest.broker_uid
        || keys.is_empty()
        || keys.len() > MAX_PROFILE_DEVICES
        || keys.len() != set.len()
        || set != grant.devices
        || grant.triggers != BTreeSet::from([trigger])
    {
        return Err(invalid());
    }
    Ok(())
}

#[cfg(test)]
#[path = "automation_tests.rs"]
mod tests;

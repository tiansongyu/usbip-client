//! Strict administrator delegation and finite automatic-rule input types.
//!
//! These validation APIs neither start jobs nor persist rules. A valid policy or
//! rule is not a device capability, a nonstorage classification, an observation
//! of availability or a cleanup proof. Agent/RPC/scheduler integration must also
//! journal the rule occurrence and original Start intent in one transaction.

use crate::config::{Action, Backend, Credentials, LoadedConfig, valid_label};
use rustusb_core::{
    DeviceBackend, DeviceKey, Error, Result,
    application::{JobId, RequestId, RuleId},
};
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

    /// Minimum settling interval between matching fresh condition observations.
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

/// An original rule-edit result, settled only by an actual durable transaction.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "decision", rename_all = "snake_case", deny_unknown_fields)]
pub enum RuleDecision {
    /// The original positive mutation, independent of its later rule/job state.
    Applied {
        /// Exact immutable positive edit receipt.
        outcome: RuleMutationOutcome,
    },
    /// The original completed request was durably recorded without applying its edit.
    Rejected {
        /// Independent rejection sequence; never a cleanup or rule-event sequence.
        sequence: u64,
        /// Fixed secret-free failure category, not an arbitrary error message.
        failure: crate::model::Failure,
    },
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

/// Hash of bounded observation metadata; not a device identity or capability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct ObservationToken(String);

impl ObservationToken {
    /// Validate exactly64 lowercase hexadecimal digits without accepting raw USB data.
    pub fn new(value: String) -> Result<Self> {
        if value.len() != 64
            || !value
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::InvalidInput("automatic observation token".into()));
        }
        Ok(Self(value))
    }
    /// Borrow the finite metadata digest; it never authorizes a device operation.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}
impl TryFrom<String> for ObservationToken {
    type Error = Error;
    fn try_from(value: String) -> Result<Self> {
        Self::new(value)
    }
}
impl From<ObservationToken> for String {
    fn from(value: ObservationToken) -> Self {
        value.0
    }
}

/// Explicit background delegation committed with its original Start intent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuleOrigin {
    /// Exact committed rule-audit sequence of this firing.
    pub sequence: u64,
    /// Original rule, including after a later edit or retirement.
    pub rule_id: RuleId,
    /// Original positive rule revision, not the current editor revision.
    pub revision: u64,
    /// UID from the authorized durable rule; not a new SO_PEERCRED claim.
    pub operator_uid: u32,
    /// Exact fixed profile effect.
    pub profile: String,
    /// Consumed observation tuple, not a physical generation proof.
    pub observation: ObservationToken,
    /// Original Start namespace identity, never a mutation retry identity.
    pub request_id: RequestId,
    /// Exact job intent committed in the same transaction.
    pub job_id: JobId,
}

/// Outcome of a committed rule edit; it does not assert device availability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuleMutationOutcome {
    /// The original created or edited identity.
    pub rule_id: RuleId,
    /// Revision produced by that edit, even if later edits have occurred.
    pub revision: u64,
    /// Future triggering was retired; unresolved current-job history remains.
    pub deleted: bool,
}

/// Current automation observation, distinct from a job's operation/cleanup facts.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuleStatus {
    /// Future triggering was explicitly disabled.
    Disabled,
    /// A manual Stop suppressed future triggering until explicit enable.
    PausedByUser,
    /// No matching complete safe condition is currently stable.
    Waiting,
    /// A fresh matching condition is settling.
    Candidate,
    /// A Start intent committed; the original runner is being registered.
    Admitting,
    /// The original actual owner is still supervised.
    Active,
    /// The once policy already consumed this revision.
    OnceConsumed,
    /// The current observation was already consumed.
    AwaitingNewObservation,
    /// Current administrator delegation or action permission is absent.
    BlockedPermission,
    /// Complete current metadata explicitly blocks safe automation.
    BlockedUnsafe,
    /// Remote metadata cannot prove the required full nonstorage classification.
    SafetyEvidenceUnavailable,
    /// No sufficiently fresh complete observation is available.
    Stale,
    /// Previous ownership or actual release is unresolved.
    NeedsReconciliation,
    /// The service is stopping or durable/observation admission is unavailable.
    Unavailable,
    /// Another current or unresolved job already occupies the fixed profile.
    ProfileBusy,
}

/// One finite rule observation, with current and original identities separated.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuleView {
    /// Persistent rule identity.
    pub id: RuleId,
    /// Exact current revision required by edits.
    pub revision: u64,
    /// Authorized original operator, not a claimed request field.
    pub operator_uid: u32,
    /// Complete fixed administrator-selected effect.
    pub profile: String,
    /// Current bounded definition.
    pub spec: RuleSpec,
    /// Future-trigger intent, independent of actual current-job cleanup.
    pub enabled: bool,
    /// Sticky manual Stop state; explicit enable is needed to resume.
    pub paused_by_user: bool,
    /// Retired future intent with still-retained history.
    pub deleted: bool,
    /// Live metadata condition, never release evidence.
    pub status: RuleStatus,
    /// Last committed firing, unchanged by an edit or observer restart.
    #[serde(deserialize_with = "required_option")]
    pub last_origin: Option<RuleOrigin>,
    /// Independent job operation facts; sessions retain actual cleanup evidence.
    #[serde(deserialize_with = "required_option")]
    pub last_job: Option<crate::model::Job>,
    /// Fixed latest observation error, not an arbitrary network/USB message.
    #[serde(deserialize_with = "required_option")]
    pub observation_error: Option<crate::model::Failure>,
    /// Fixed failure before automatic job admission; actual job errors stay separate.
    #[serde(deserialize_with = "required_option")]
    pub admission_error: Option<crate::model::Failure>,
}

/// Positive rule audit categories in their own finite sequence/schema.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RuleEventKind {
    /// A new rule committed.
    Created,
    /// A definition changed with exact revision CAS.
    Replaced,
    /// Explicit enable/rearm committed.
    Enabled,
    /// Future triggering was disabled without claiming actual-job release.
    Disabled,
    /// Future intent was retired, preserving unresolved ownership.
    Deleted,
    /// Manual Stop durably paused a rule in the same Stop transaction.
    PausedByStop,
    /// The exact occurrence and original Start intent committed together.
    Fired,
}

/// Payload-free rule audit, independent of frozen legacy audit/log schemas.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RuleEvent {
    /// Monotonic automation sequence, distinct from the legacy audit sequence.
    pub sequence: u64,
    /// Actual mutation/Stop peer, or the explicitly delegated firing UID.
    pub actor_uid: u32,
    /// Original rule owner; may differ from a stopping peer.
    pub operator_uid: u32,
    /// Fixed profile scope.
    pub profile: String,
    /// Original rule identity.
    pub rule_id: RuleId,
    /// Resulting edit revision or original firing revision.
    pub revision: u64,
    /// Closed operation kind.
    pub kind: RuleEventKind,
    /// Original mutation/Start identity; null only for a manual Stop pause.
    #[serde(deserialize_with = "required_option")]
    pub request_id: Option<RequestId>,
    /// Exact related Start/Stop job; not a cleanup receipt.
    #[serde(deserialize_with = "required_option")]
    pub job_id: Option<JobId>,
}

/// Public grant metadata for an already authorized UID/profile read.
/// Deserializing this view cannot create an `AutomationPolicy` or authority.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AutomationGrantInfo {
    /// Complete fixed effect; a rule cannot narrow this device pool.
    pub devices: Vec<DeviceKey>,
    /// Closed administrator-permitted condition kinds.
    pub triggers: Vec<TriggerKind>,
    /// Explicit permitted occurrence policies.
    pub rearm: Vec<RearmKind>,
    /// Per-operator/profile admission ceiling.
    pub max_rules: u8,
}

impl From<&AutomationGrant> for AutomationGrantInfo {
    fn from(grant: &AutomationGrant) -> Self {
        Self {
            devices: grant.devices.iter().cloned().collect(),
            triggers: grant.triggers.iter().copied().collect(),
            rearm: grant.rearm.iter().copied().collect(),
            max_rules: grant.max_rules,
        }
    }
}

pub(crate) fn required_option<'de, D, T>(decoder: D) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(decoder)
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
    settled_rule_results: bool,
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
struct DecisionPolicyFile {
    schema_version: u32,
    broker_uid: u32,
    grants: Vec<GrantFile>,
    settled_rule_results: bool,
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
    pub(crate) fn check_binding(&self, config: &LoadedConfig) -> Result<()> {
        config.manifest.validate()?;
        if self.broker_uid != config.manifest.broker_uid {
            return Err(Error::Permission("automatic policy broker binding".into()));
        }
        for grant in &self.grants {
            grant_binding(config, grant)?;
        }
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn fixture(bytes: &[u8], config: &LoadedConfig) -> Result<Self> {
        Self::bind(bytes, config)
    }

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

    /// Load explicit root schema2 delegation and opt in to disk4 settled results.
    ///
    /// Separate from the frozen schema1 reader; retain/join its blocking startup
    /// work before opening a listener. This does not grant additional USB access.
    pub fn read_decisions(path: &Path, config: &LoadedConfig) -> Result<Self> {
        let bytes = rustusb_transport::config::read_root_document(path)
            .map_err(|_| Error::Permission("trusted decision policy could not be read".into()))?;
        Self::bind_decisions(&bytes, config)
    }

    fn bind_decisions(bytes: &[u8], config: &LoadedConfig) -> Result<Self> {
        if bytes.len() > MAX_POLICY_BYTES {
            return Err(Error::InvalidInput("decision policy byte bound".into()));
        }
        let file: DecisionPolicyFile = serde_json::from_slice(bytes)
            .map_err(|_| Error::InvalidInput("decision policy schema".into()))?;
        if file.schema_version != 2 || !file.settled_rule_results {
            return Err(Error::InvalidInput(
                "explicit decision policy version/opt-in".into(),
            ));
        }
        let mut policy = Self::bind_file(
            PolicyFile {
                schema_version: 1,
                broker_uid: file.broker_uid,
                grants: file.grants,
            },
            config,
        )?;
        policy.settled_rule_results = true;
        Ok(policy)
    }

    /// Explicit administrator opt-in to the versioned settled-result journal.
    pub fn settled_rule_results(&self) -> bool {
        self.settled_rule_results
    }

    #[cfg(test)]
    pub(crate) fn fixture_decisions(bytes: &[u8], config: &LoadedConfig) -> Result<Self> {
        Self::bind_decisions(bytes, config)
    }

    fn bind(bytes: &[u8], config: &LoadedConfig) -> Result<Self> {
        if bytes.len() > MAX_POLICY_BYTES {
            return Err(Error::InvalidInput("automation policy byte bound".into()));
        }
        let file: PolicyFile = serde_json::from_slice(bytes)
            .map_err(|_| Error::InvalidInput("automation policy schema".into()))?;
        Self::bind_file(file, config)
    }

    fn bind_file(file: PolicyFile, config: &LoadedConfig) -> Result<Self> {
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
            settled_rule_results: false,
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

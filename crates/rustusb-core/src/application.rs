//! Bounded application identities and read-only helper lifecycle evidence.
//! Identifiers correlate operations; they never grant resource ownership.

use crate::{DeviceKey, Error, Result};
use serde::{Deserialize, Serialize};
use std::{fmt, str::FromStr};

fn canonical_uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(index, byte)| {
            if matches!(index, 8 | 13 | 18 | 23) {
                byte == b'-'
            } else {
                byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
            }
        })
}

macro_rules! identity {
    ($name:ident, $description:literal) => {
        #[doc = $description]
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
        #[serde(try_from = "String", into = "String")]
        pub struct $name(String);
        impl $name {
            /// Validate a canonical lowercase UUID, without granting authorization.
            pub fn new(value: impl Into<String>) -> Result<Self> {
                let value = value.into();
                if !canonical_uuid(&value) {
                    return Err(Error::InvalidInput("invalid application identity".into()));
                }
                Ok(Self(value))
            }
            /// Borrow the canonical representation.
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }
        impl TryFrom<String> for $name {
            type Error = Error;
            fn try_from(value: String) -> Result<Self> {
                Self::new(value)
            }
        }
        impl From<$name> for String {
            fn from(value: $name) -> Self {
                value.0
            }
        }
        impl FromStr for $name {
            type Err = Error;
            fn from_str(value: &str) -> Result<Self> {
                Self::new(value)
            }
        }
        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(self.as_str())
            }
        }
    };
}

identity!(
    InstanceId,
    "One application epoch, independent of process ID reuse."
);
identity!(
    JobId,
    "One profile job with a supervised actual owner task."
);
identity!(
    SessionId,
    "One application device session within its recorded epoch."
);
identity!(
    AttemptId,
    "One distinct helper capability attempt, never an implicit retry."
);
identity!(
    RequestId,
    "One control request in an explicit bounded deduplication window."
);
identity!(
    RuleId,
    "One automatic rule identity; it grants no background execution authority."
);
identity!(
    ResourceId,
    "One helper-issued resource identity, not a bearer capability."
);

/// Immutable application correlation for one helper capability attempt.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrokerCorrelation {
    /// Epoch which originally requested this capability.
    pub instance_id: InstanceId,
    /// Device session, not an entire multi-device server job.
    pub session_id: SessionId,
    /// Fresh identity for this import, export, stub or USBFS grant.
    pub attempt_id: AttemptId,
}
impl BrokerCorrelation {
    /// Reject accidental reuse before a helper mutation.
    pub fn validate(&self) -> Result<()> {
        if self.instance_id.as_str() == self.session_id.as_str()
            || self.instance_id.as_str() == self.attempt_id.as_str()
            || self.session_id.as_str() == self.attempt_id.as_str()
        {
            return Err(Error::InvalidInput(
                "application correlation identities must be distinct".into(),
            ));
        }
        Ok(())
    }
}

/// Concrete helper capability namespace for exact read-only inspection.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BrokerResourceKind {
    /// Kernel VHCI import channel.
    NativeImport,
    /// A device's kernel export registration.
    NativeExport,
    /// A connection on a same-process export registration.
    NativeStub,
    /// A separately authorized whole-device USBFS grant.
    UsbfsGrant,
}

/// Recorded progress, not a fresh assertion of current kernel ownership.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BrokerOperationPhase {
    /// Recorded before kernel mutation.
    Prepared,
    /// Mutation may have begun.
    Intent,
    /// An actual receipt was recorded.
    Observed,
    /// USBFS capability granted; release is not yet proven.
    Granted,
    /// Outcome remains uncertain.
    Unresolved,
}

/// Actual retirement criterion; gone/old-boot never promises present-driver restoration.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BrokerCleanupProof {
    /// No kernel handoff or device mutation began.
    NotStarted,
    /// Original VHCI is NULL and its old local generation is gone.
    NativeImportReleased,
    /// An unidentified import intent was reconciled with all controller ports
    /// NULL and no downstream USB device; no complete local receipt is implied.
    NativeImportIntentReleased,
    /// Complete original Native export baseline restored.
    NativeExportRestored,
    /// Original stub stopped and its export independently AVAILABLE.
    NativeStubAvailable,
    /// Original USBFS baseline independently verified restored.
    PhysicalBaselineRestored,
    /// Original hardware generation gone; no replacement adopted.
    OriginalGenerationGone,
    /// Original capability belonged to an earlier kernel boot.
    PreviousBoot,
}

/// Durable proof committed atomically with retirement of a tracked resource.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrokerTerminal {
    /// Original helper-issued identity.
    pub resource_id: ResourceId,
    /// Original capability namespace.
    pub kind: BrokerResourceKind,
    /// Approved original device identity.
    pub device_key: DeviceKey,
    /// Original authorized nonroot broker UID.
    pub owner_uid: u32,
    /// Exact original application correlation.
    pub correlation: BrokerCorrelation,
    /// Independently verified criterion.
    pub proof: BrokerCleanupProof,
}

/// Missing/evicted/legacy-untracked/inaccessible evidence remains Unknown.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
pub enum BrokerOperationState {
    /// No accessible retained proof; never a cleanup success receipt.
    Unknown {},
    /// Matching record exists, without transferring ownership to a new process.
    Active {
        /// Original helper identity.
        resource_id: ResourceId,
        /// Original approved device.
        device_key: DeviceKey,
        /// Persisted progress, distinct from current kernel state.
        phase: BrokerOperationPhase,
    },
    /// Exact retirement evidence retained by the root helper.
    Terminal {
        /// Atomically committed proof.
        terminal: BrokerTerminal,
    },
}

/// Versioned inspection reply; callers verify root peer and the exact echo.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrokerOperationStatus {
    /// Inspection schema1, independent from helper disk formats.
    pub schema_version: u32,
    /// Requested namespace.
    pub kind: BrokerResourceKind,
    /// Exact requested correlation.
    pub correlation: BrokerCorrelation,
    /// Recorded state or retained proof, never an adopted handle.
    pub operation: BrokerOperationState,
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn correlation_and_wire_identities_are_strict() -> Result<()> {
        let good = "00000000-0000-0000-0000-000000000001";
        assert_eq!(SessionId::new(good)?.as_str(), good);
        for value in [
            "",
            "../state",
            "00000000-0000-0000-0000-00000000000A",
            "000000000000000000000000000000000001",
            "00000000-0000-0000-0000-000000000001\n",
        ] {
            assert!(serde_json::from_value::<SessionId>(serde_json::json!(value)).is_err());
        }
        let mut value = BrokerCorrelation {
            instance_id: InstanceId::new(good)?,
            session_id: SessionId::new("00000000-0000-0000-0000-000000000002")?,
            attempt_id: AttemptId::new("00000000-0000-0000-0000-000000000003")?,
        };
        value.validate()?;
        value.attempt_id = AttemptId::new(good)?;
        assert!(value.validate().is_err());
        Ok(())
    }
    #[test]
    fn unknown_is_not_a_cleanup_success_receipt() -> Result<()> {
        assert!(matches!(
            serde_json::from_value::<BrokerOperationState>(serde_json::json!({"state":"unknown"}))
                .map_err(|_| Error::InvalidInput("test status fixture".into()))?,
            BrokerOperationState::Unknown {}
        ));
        assert!(
            serde_json::from_value::<BrokerOperationState>(
                serde_json::json!({"state":"unknown","cleanup_verified":true})
            )
            .is_err()
        );
        assert!(
            serde_json::from_value::<BrokerOperationState>(serde_json::json!({"state":"terminal"}))
                .is_err()
        );
        Ok(())
    }
}

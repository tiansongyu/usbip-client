//! Bounded broker-owned read-only lifecycle observations. No recovery authority.
use crate::{Request, Response};
use rustusb_core::{
    DeviceKey, Error, Result,
    application::{
        BrokerCorrelation, BrokerOperationPhase, BrokerOperationState, BrokerOperationStatus,
        BrokerResourceKind, ResourceId,
    },
};
use serde::{Deserialize, Serialize};
use std::path::Path;

/// One persisted pending capability, not a fresh kernel observation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrokerPendingRecord {
    /// Original helper-issued identity, not a bearer capability.
    pub resource_id: ResourceId,
    /// Capability namespace.
    pub kind: BrokerResourceKind,
    /// Original approved device key.
    pub device_key: DeviceKey,
    /// Recorded progress, without ownership transfer.
    pub phase: BrokerOperationPhase,
    /// Optional application correlation; legacy records remain untracked.
    #[serde(deserialize_with = "required_correlation")]
    pub correlation: Option<BrokerCorrelation>,
}
fn required_correlation<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Option<BrokerCorrelation>, D::Error> {
    Option::deserialize(d)
}
/// At most eight currently visible broker-owned pending records.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrokerPendingPage {
    /// Page protocol version, independent from storage schemas.
    pub schema_version: u32,
    /// Requested namespace.
    pub kind: BrokerResourceKind,
    /// Sorted visible records; not an authoritative current-kernel snapshot.
    pub records: Vec<BrokerPendingRecord>,
    /// Exclusive continuation cursor, absent on the final page.
    pub next_after: Option<ResourceId>,
}
fn decode<T: serde::de::DeserializeOwned>(response: Response) -> Result<T> {
    if !response.ok {
        return Err(Error::State(
            response
                .error
                .unwrap_or_else(|| "helper lifecycle observation failed".into()),
        ));
    }
    if response.error.is_some() {
        return Err(Error::Protocol(
            "inconsistent helper lifecycle response".into(),
        ));
    }
    serde_json::from_value(response.data)
        .map_err(|_| Error::Protocol("invalid helper lifecycle observation".into()))
}
/// Inspect exact retained evidence under the current broker UID and device pool.
/// Unknown is never a cleanup receipt; this does not adopt the original handle.
pub async fn inspect_broker_operation(
    socket: &Path,
    kind: BrokerResourceKind,
    correlation: BrokerCorrelation,
) -> Result<BrokerOperationStatus> {
    correlation.validate()?;
    let status: BrokerOperationStatus = decode(
        crate::request(
            socket,
            Request::InspectBrokerOperation {
                kind,
                correlation: correlation.clone(),
            },
        )
        .await?,
    )?;
    if status.schema_version != 1 || status.kind != kind || status.correlation != correlation {
        return Err(Error::Protocol(
            "helper operation observation echo mismatch".into(),
        ));
    }
    if let BrokerOperationState::Terminal { terminal } = &status.operation
        && (terminal.kind != kind || terminal.correlation != correlation)
    {
        return Err(Error::Protocol(
            "helper terminal correlation mismatch".into(),
        ));
    }
    Ok(status)
}
/// Read a bounded page without expanding root-only reconciliation permissions.
pub async fn broker_pending(
    socket: &Path,
    kind: BrokerResourceKind,
    after: Option<ResourceId>,
) -> Result<BrokerPendingPage> {
    let page: BrokerPendingPage = decode(
        crate::request(
            socket,
            Request::BrokerPending {
                kind,
                after: after.clone(),
            },
        )
        .await?,
    )?;
    if page.schema_version != 1
        || page.kind != kind
        || page.records.len() > 8
        || page.records.iter().any(|r| {
            r.kind != kind
                || after.as_ref().is_some_and(|a| r.resource_id <= *a)
                || r.correlation
                    .as_ref()
                    .is_some_and(|c| c.validate().is_err())
        })
        || page
            .records
            .windows(2)
            .any(|p| p[0].resource_id >= p[1].resource_id)
        || page
            .next_after
            .as_ref()
            .is_some_and(|n| page.records.last().is_none_or(|r| &r.resource_id != n))
    {
        return Err(Error::Protocol("invalid broker pending page".into()));
    }
    Ok(page)
}
#[cfg(target_os = "linux")]
fn visible(
    policy: &crate::Policy,
    uid: u32,
    kind: BrokerResourceKind,
    key: &DeviceKey,
    server: Option<&str>,
) -> bool {
    if policy.authorize_broker(uid).is_err() {
        return false;
    }
    match kind {
        BrokerResourceKind::NativeImport => server.is_some_and(|server| {
            policy
                .secure_imports
                .iter()
                .any(|rule| rule.server_id == server && rule.device_keys.contains(key))
        }),
        BrokerResourceKind::NativeExport | BrokerResourceKind::NativeStub => {
            key.backend() == rustusb_core::DeviceBackend::Native
                && key
                    .fingerprint()
                    .is_some_and(|id| policy.authorize_secure_native(uid, id).is_ok())
        }
        BrokerResourceKind::UsbfsGrant => {
            key.backend() == rustusb_core::DeviceBackend::Physical
                && key.fingerprint().is_some_and(|id| {
                    policy
                        .secure_physical_device_ids
                        .iter()
                        .any(|allowed| allowed == id)
                })
        }
    }
}
#[cfg(target_os = "linux")]
fn page(
    kind: BrokerResourceKind,
    after: Option<ResourceId>,
    mut records: Vec<BrokerPendingRecord>,
) -> BrokerPendingPage {
    records.sort_by(|a, b| a.resource_id.cmp(&b.resource_id));
    records.retain(|r| after.as_ref().is_none_or(|after| r.resource_id > *after));
    let more = records.len() > 8;
    records.truncate(8);
    let next_after = if more {
        records.last().map(|r| r.resource_id.clone())
    } else {
        None
    };
    BrokerPendingPage {
        schema_version: 1,
        kind,
        records,
        next_after,
    }
}
#[cfg(target_os = "linux")]
pub(crate) async fn serve(
    app: &crate::linux::App,
    uid: u32,
    request: Request,
) -> Result<serde_json::Value> {
    app.policy.authorize_broker(uid)?;
    let (kind, correlation, after) = match request {
        Request::InspectBrokerOperation { kind, correlation } => {
            correlation.validate()?;
            (kind, Some(correlation), None)
        }
        Request::BrokerPending { kind, after } => (kind, None, after),
        _ => {
            return Err(Error::InvalidInput(
                "invalid broker observation request".into(),
            ));
        }
    };
    let (observation, records) = if kind == BrokerResourceKind::UsbfsGrant {
        #[cfg(feature = "physical-usb")]
        {
            if let Some(store) = app.journal.clone() {
                let c = correlation.clone();
                let (state, rows) = tokio::task::spawn_blocking(move || {
                    let store = store
                        .lock()
                        .map_err(|_| Error::State("physical journal lock poisoned".into()))?;
                    let state = match c {
                        Some(c) => store.tracked_status(uid, &c)?,
                        None => None,
                    };
                    let rows = store
                        .records()?
                        .into_iter()
                        .map(|r| {
                            Ok((
                                r.owner.uid,
                                None,
                                BrokerPendingRecord {
                                    resource_id: ResourceId::new(r.lease_id)?,
                                    kind: BrokerResourceKind::UsbfsGrant,
                                    device_key: DeviceKey::physical(&r.baseline.device_id)?,
                                    phase: BrokerOperationPhase::Granted,
                                    correlation: r.correlation,
                                },
                            ))
                        })
                        .collect::<Result<Vec<_>>>()?;
                    Ok::<_, Error>((state.map(|s| (s, None)), rows))
                })
                .await
                .map_err(|_| Error::State("physical observation task failed".into()))??;
                (state, rows)
            } else {
                (None, Vec::new())
            }
        }
        #[cfg(not(feature = "physical-usb"))]
        {
            return Err(Error::Unsupported(
                "physical lifecycle support is not enabled".into(),
            ));
        }
    } else if let Some(store) = app.native_state.clone() {
        let c = correlation.clone();
        tokio::task::spawn_blocking(move || {
            let mut store = store
                .lock()
                .map_err(|_| Error::State("Native journal lock poisoned".into()))?;
            let state = match c {
                Some(c) => store.tracked_status(uid, kind, &c)?,
                None => None,
            };
            Ok::<_, Error>((state, store.broker_rows()?))
        })
        .await
        .map_err(|_| Error::State("Native observation task failed".into()))??
    } else {
        (None, Vec::new())
    };
    if let Some(correlation) = correlation {
        let operation = observation
            .filter(|(s, server)| {
                let key = match s {
                    BrokerOperationState::Active { device_key, .. } => device_key,
                    BrokerOperationState::Terminal { terminal } => &terminal.device_key,
                    BrokerOperationState::Unknown {} => return false,
                };
                visible(&app.policy, uid, kind, key, server.as_deref())
            })
            .map(|(state, _)| state)
            .unwrap_or(BrokerOperationState::Unknown {});
        serde_json::to_value(BrokerOperationStatus {
            schema_version: 1,
            kind,
            correlation,
            operation,
        })
        .map_err(|_| Error::Protocol("cannot encode broker observation".into()))
    } else {
        let rows = records
            .into_iter()
            .filter(|(owner, server, r)| {
                *owner == uid
                    && r.kind == kind
                    && visible(&app.policy, uid, kind, &r.device_key, server.as_deref())
            })
            .map(|(_, _, r)| r)
            .collect();
        serde_json::to_value(page(kind, after, rows))
            .map_err(|_| Error::Protocol("cannot encode broker pending page".into()))
    }
}

#[cfg(all(test, target_os = "linux"))]
mod tests {
    use super::*;
    use rustusb_core::{
        VirtualProfile,
        application::{AttemptId, InstanceId, SessionId},
    };
    fn id(n: u32) -> String {
        format!("00000000-0000-0000-0000-{n:012x}")
    }
    fn correlation() -> Result<BrokerCorrelation> {
        Ok(BrokerCorrelation {
            instance_id: InstanceId::new(id(1))?,
            session_id: SessionId::new(id(2))?,
            attempt_id: AttemptId::new(id(3))?,
        })
    }
    fn policy() -> crate::Policy {
        crate::Policy {
            allowed_uids: vec![1000],
            secure_broker_uid: Some(2000),
            secure_imports: vec![crate::SecureImportRule {
                server_id: "approved".into(),
                device_keys: vec![DeviceKey::virtual_profile(VirtualProfile::Hid)],
            }],
            secure_physical_device_ids: vec![],
            secure_native_device_ids: vec![],
            physical_device_ids: vec![],
            physical_connect_restore_device_ids: vec![],
            export_device_ids: vec![],
            peers: vec![],
            trusted_network: false,
        }
    }
    #[test]
    fn retired_import_scope_still_requires_original_logical_server_and_current_pool() -> Result<()>
    {
        let mut p = policy();
        p.validate()?;
        let key = DeviceKey::virtual_profile(VirtualProfile::Hid);
        assert!(visible(
            &p,
            2000,
            BrokerResourceKind::NativeImport,
            &key,
            Some("approved")
        ));
        for (uid, server) in [
            (1000, Some("approved")),
            (2000, Some("other")),
            (2000, None),
        ] {
            assert!(!visible(
                &p,
                uid,
                BrokerResourceKind::NativeImport,
                &key,
                server
            ));
        }
        p.secure_imports.clear();
        assert!(!visible(
            &p,
            2000,
            BrokerResourceKind::NativeImport,
            &key,
            Some("approved")
        ));
        assert!(!visible(
            &p,
            2000,
            BrokerResourceKind::NativeExport,
            &key,
            None
        ));
        Ok(())
    }
    #[test]
    fn pending_page_is_sorted_bounded_and_cursor_is_only_a_continuation() -> Result<()> {
        let kind = BrokerResourceKind::NativeImport;
        let mut rows = Vec::new();
        for n in (1..=10).rev() {
            rows.push(BrokerPendingRecord {
                resource_id: ResourceId::new(id(n))?,
                kind,
                device_key: DeviceKey::virtual_profile(VirtualProfile::Hid),
                phase: BrokerOperationPhase::Unresolved,
                correlation: None,
            });
        }
        let first = page(kind, None, rows.clone());
        assert_eq!(first.records.len(), 8);
        assert_eq!(first.records[0].resource_id.as_str(), id(1));
        assert_eq!(
            first.next_after.as_ref().map(ResourceId::as_str),
            Some(id(8).as_str())
        );
        let second = page(kind, first.next_after, rows);
        assert_eq!(second.records.len(), 2);
        assert!(second.next_after.is_none());
        Ok(())
    }
    #[test]
    fn tracked_and_old_ipc_shapes_are_separate_and_query_echo_is_typed() -> Result<()> {
        let c = correlation()?;
        let request = Request::InspectBrokerOperation {
            kind: BrokerResourceKind::NativeImport,
            correlation: c.clone(),
        };
        let encoded = serde_json::to_value(&request).map_err(|e| Error::State(e.to_string()))?;
        assert!(matches!(
            serde_json::from_value::<Request>(encoded.clone()),
            Ok(Request::InspectBrokerOperation { .. })
        ));
        let mut extra = encoded;
        extra["owner_uid"] = serde_json::json!(0);
        assert!(serde_json::from_value::<Request>(extra).is_err());
        let legacy = serde_json::json!({"op":"prepare_native_export","device_id":format!("usb-{}","a".repeat(64)),"correlation":c});
        assert!(serde_json::from_value::<Request>(legacy).is_err());
        let status = BrokerOperationStatus {
            schema_version: 1,
            kind: BrokerResourceKind::NativeImport,
            correlation: correlation()?,
            operation: BrokerOperationState::Unknown {},
        };
        let response = Response::success(
            serde_json::to_value(status).map_err(|e| Error::State(e.to_string()))?,
        );
        assert!(matches!(
            decode::<BrokerOperationStatus>(response)?.operation,
            BrokerOperationState::Unknown {}
        ));
        assert!(
            decode::<BrokerOperationStatus>(Response::failure(Error::State(
                "journal corrupt".into()
            )))
            .is_err()
        );
        Ok(())
    }
}

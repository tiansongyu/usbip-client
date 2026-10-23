//! Private Native storage migration and atomic tracked lifecycle evidence.
use super::*;
use crate::BrokerPendingRecord;
use rustusb_core::{
    DeviceBackend,
    application::{
        BrokerCleanupProof as Proof, BrokerCorrelation as Correlation,
        BrokerOperationPhase as Progress, BrokerOperationState, BrokerResourceKind as Kind,
        BrokerTerminal, ResourceId,
    },
};
const SLOTS: usize = 128;
const TERMINAL_BYTES: usize = 1024;

pub(super) fn required_correlation<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Option<Correlation>, D::Error> {
    Option::deserialize(d)
}
fn required_server<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Option<String>, D::Error> {
    Option::deserialize(d)
}
fn bounded<'de, D: serde::Deserializer<'de>, T: Deserialize<'de>, const N: usize>(
    d: D,
) -> std::result::Result<Vec<T>, D::Error> {
    struct Visitor<T, const N: usize>(std::marker::PhantomData<T>);
    impl<'de, T: Deserialize<'de>, const N: usize> serde::de::Visitor<'de> for Visitor<T, N> {
        type Value = Vec<T>;
        fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            write!(f, "at most {N} Native records")
        }
        fn visit_seq<A: serde::de::SeqAccess<'de>>(
            self,
            mut seq: A,
        ) -> std::result::Result<Self::Value, A::Error> {
            if seq.size_hint().is_some_and(|n| n > N) {
                return Err(serde::de::Error::custom("Native array exceeds bound"));
            }
            let mut out = Vec::new();
            while let Some(value) = seq.next_element()? {
                if out.len() == N {
                    return Err(serde::de::Error::custom("Native array exceeds bound"));
                }
                out.push(value);
            }
            Ok(out)
        }
    }
    d.deserialize_seq(Visitor::<T, N>(std::marker::PhantomData))
}
fn legacy_records<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Vec<LegacyRecord>, D::Error> {
    bounded::<D, LegacyRecord, 32>(d)
}
fn legacy_exports<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Vec<LegacyExport>, D::Error> {
    bounded::<D, LegacyExport, 32>(d)
}
fn records<'de, D: serde::Deserializer<'de>>(d: D) -> std::result::Result<Vec<Record>, D::Error> {
    bounded::<D, Record, 32>(d)
}
fn exports<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Vec<ExportRecord>, D::Error> {
    bounded::<D, ExportRecord, 32>(d)
}
fn terminals<'de, D: serde::Deserializer<'de>>(
    d: D,
) -> std::result::Result<Vec<TerminalEntry>, D::Error> {
    bounded::<D, TerminalEntry, 128>(d)
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct TerminalEntry {
    pub terminal: BrokerTerminal,
    // Needed to recheck the original logical import pool after retirement.
    #[serde(deserialize_with = "required_server")]
    pub server_id: Option<String>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyRecord {
    channel_id: String,
    owner: Owner,
    target: SecureImportTarget,
    state: Phase,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyStub {
    channel_id: String,
    state: StubPhase,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyExport {
    lease_id: String,
    owner: Owner,
    registration: RegistrationSnapshot,
    bound: Option<ExportReceipt>,
    stub: Option<LegacyStub>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacyDisk {
    schema_version: u32,
    #[serde(deserialize_with = "legacy_records")]
    records: Vec<LegacyRecord>,
    #[serde(deserialize_with = "legacy_exports")]
    exports: Vec<LegacyExport>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Disk {
    pub schema_version: u32,
    #[serde(deserialize_with = "records")]
    pub records: Vec<Record>,
    #[serde(deserialize_with = "exports")]
    pub exports: Vec<ExportRecord>,
    #[serde(deserialize_with = "terminals")]
    pub terminals: Vec<TerminalEntry>,
}
pub(super) fn decode_disk(bytes: &[u8]) -> Result<Disk> {
    #[derive(Deserialize)]
    struct Version {
        schema_version: u32,
    }
    let version: Version = serde_json::from_slice(bytes)
        .map_err(|_| Error::State("invalid Native journal version".into()))?;
    match version.schema_version {
        1 => {
            let disk: LegacyDisk = serde_json::from_slice(bytes)
                .map_err(|_| Error::State("invalid legacy Native journal".into()))?;
            Ok(Disk {
                schema_version: 1,
                records: disk
                    .records
                    .into_iter()
                    .map(|r| Record {
                        channel_id: r.channel_id,
                        owner: r.owner,
                        target: r.target,
                        state: r.state,
                        correlation: None,
                    })
                    .collect(),
                exports: disk
                    .exports
                    .into_iter()
                    .map(|r| ExportRecord {
                        lease_id: r.lease_id,
                        owner: r.owner,
                        registration: r.registration,
                        bound: r.bound,
                        correlation: None,
                        stub: r.stub.map(|s| StubRecord {
                            channel_id: s.channel_id,
                            state: s.state,
                            correlation: None,
                        }),
                    })
                    .collect(),
                terminals: Vec::new(),
            })
        }
        2 => serde_json::from_slice(bytes)
            .map_err(|_| Error::State("invalid tracked Native journal".into())),
        _ => Err(Error::State("unsupported Native channel journal".into())),
    }
}
fn identities<'a>(
    records: &'a BTreeMap<String, Record>,
    exports: &'a BTreeMap<String, ExportRecord>,
) -> Vec<(Kind, u32, &'a Correlation)> {
    let mut out = Vec::new();
    for r in records.values() {
        if let Some(c) = &r.correlation {
            out.push((Kind::NativeImport, r.owner.uid, c));
        }
    }
    for r in exports.values() {
        if let Some(c) = &r.correlation {
            out.push((Kind::NativeExport, r.owner.uid, c));
        }
        if let Some(s) = &r.stub
            && let Some(c) = &s.correlation
        {
            out.push((Kind::NativeStub, r.owner.uid, c));
        }
    }
    out
}
fn validate_tables(
    records: &BTreeMap<String, Record>,
    exports: &BTreeMap<String, ExportRecord>,
    terminals: &[TerminalEntry],
) -> Result<usize> {
    let active = identities(records, exports);
    if active.len() + terminals.len() > SLOTS {
        return Err(Error::State(
            "Native tracked evidence capacity exceeded".into(),
        ));
    }
    let mut seen = BTreeSet::new();
    let mut ids = BTreeSet::new();
    for (kind, uid, c) in &active {
        c.validate()?;
        if *uid == 0 || !seen.insert((*kind, *uid, (*c).clone())) {
            return Err(Error::State(
                "duplicate Native correlation; inspect recorded operation".into(),
            ));
        }
    }
    for r in records.values() {
        if !ids.insert(r.channel_id.as_str()) {
            return Err(Error::State("duplicate Native resource identity".into()));
        }
    }
    for r in exports.values() {
        if !ids.insert(r.lease_id.as_str()) {
            return Err(Error::State("duplicate Native resource identity".into()));
        }
        if let Some(s) = &r.stub
            && !ids.insert(s.channel_id.as_str())
        {
            return Err(Error::State("duplicate Native stub identity".into()));
        }
    }
    for e in terminals {
        let t = &e.terminal;
        t.correlation.validate()?;
        let valid = match t.kind {
            Kind::NativeImport => {
                e.server_id
                    .as_ref()
                    .is_some_and(|s| crate::validate_server_id(s).is_ok())
                    && matches!(
                        t.proof,
                        Proof::NotStarted
                            | Proof::NativeImportReleased
                            | Proof::NativeImportIntentReleased
                            | Proof::PreviousBoot
                    )
            }
            Kind::NativeExport => {
                e.server_id.is_none()
                    && t.device_key.backend() == DeviceBackend::Native
                    && matches!(
                        t.proof,
                        Proof::NativeExportRestored
                            | Proof::OriginalGenerationGone
                            | Proof::PreviousBoot
                    )
            }
            Kind::NativeStub => {
                e.server_id.is_none()
                    && t.device_key.backend() == DeviceBackend::Native
                    && matches!(
                        t.proof,
                        Proof::NotStarted
                            | Proof::NativeStubAvailable
                            | Proof::NativeExportRestored
                            | Proof::OriginalGenerationGone
                            | Proof::PreviousBoot
                    )
            }
            Kind::UsbfsGrant => false,
        };
        if !valid
            || t.owner_uid == 0
            || !seen.insert((t.kind, t.owner_uid, t.correlation.clone()))
            || !ids.insert(t.resource_id.as_str())
            || serde_json::to_vec(e)
                .map_err(|_| Error::State("invalid Native proof encoding".into()))?
                .len()
                > TERMINAL_BYTES
        {
            return Err(Error::State(
                "invalid or duplicate Native terminal proof".into(),
            ));
        }
    }
    Ok(active.len())
}
pub(super) fn encode_disk(
    version: u32,
    records: &BTreeMap<String, Record>,
    exports: &BTreeMap<String, ExportRecord>,
    terminals: &[TerminalEntry],
) -> Result<(u32, Vec<u8>)> {
    let active = validate_tables(records, exports, terminals)?;
    let version = if version == 2 || active > 0 || !terminals.is_empty() {
        2
    } else {
        1
    };
    let encoded = if version == 1 {
        serde_json::to_vec(&LegacyDisk {
            schema_version: 1,
            records: records
                .values()
                .map(|r| LegacyRecord {
                    channel_id: r.channel_id.clone(),
                    owner: r.owner.clone(),
                    target: r.target.clone(),
                    state: r.state.clone(),
                })
                .collect(),
            exports: exports
                .values()
                .map(|r| LegacyExport {
                    lease_id: r.lease_id.clone(),
                    owner: r.owner.clone(),
                    registration: r.registration.clone(),
                    bound: r.bound.clone(),
                    stub: r.stub.as_ref().map(|s| LegacyStub {
                        channel_id: s.channel_id.clone(),
                        state: s.state.clone(),
                    }),
                })
                .collect(),
        })
    } else {
        serde_json::to_vec(&Disk {
            schema_version: 2,
            records: records.values().cloned().collect(),
            exports: exports.values().cloned().collect(),
            terminals: terminals.to_vec(),
        })
    };
    let bytes = encoded.map_err(|_| Error::State("cannot encode Native journal".into()))?;
    if bytes.len().saturating_add(active * TERMINAL_BYTES) > LIMIT {
        return Err(Error::State(
            "Native journal lacks reserved terminal capacity".into(),
        ));
    }
    Ok((version, bytes))
}
fn terminal(
    id: &str,
    kind: Kind,
    owner: &Owner,
    key: rustusb_core::DeviceKey,
    correlation: &Option<Correlation>,
    proof: Proof,
    server_id: Option<String>,
) -> Result<Option<TerminalEntry>> {
    correlation
        .as_ref()
        .map(|c| {
            Ok(TerminalEntry {
                terminal: BrokerTerminal {
                    resource_id: ResourceId::new(id)?,
                    kind,
                    owner_uid: owner.uid,
                    device_key: key,
                    correlation: c.clone(),
                    proof,
                },
                server_id,
            })
        })
        .transpose()
}
impl Store {
    pub(super) fn validate_tracking(&self) -> Result<()> {
        validate_tables(&self.records, &self.exports, &self.terminals).map(|_| ())
    }
    pub(super) fn admit_tracking(
        &self,
        records: &BTreeMap<String, Record>,
        exports: &BTreeMap<String, ExportRecord>,
    ) -> Result<Vec<TerminalEntry>> {
        // Validate duplicates before eviction, including the oldest retained proof.
        let active = identities(records, exports);
        let mut seen = BTreeSet::new();
        for (kind, uid, c) in &active {
            c.validate()?;
            if !seen.insert((*kind, *uid, (*c).clone())) {
                return Err(Error::State(
                    "duplicate Native correlation; inspect recorded operation".into(),
                ));
            }
        }
        for t in &self.terminals {
            if seen.contains(&(
                t.terminal.kind,
                t.terminal.owner_uid,
                t.terminal.correlation.clone(),
            )) {
                return Err(Error::State(
                    "Native operation already completed; inspect retained proof".into(),
                ));
            }
        }
        if active.len() > SLOTS {
            return Err(Error::State(
                "Native tracked evidence capacity exceeded".into(),
            ));
        }
        let mut terminals = self.terminals.clone();
        while active.len() + terminals.len() > SLOTS {
            terminals.remove(0);
        }
        validate_tables(records, exports, &terminals)?;
        Ok(terminals)
    }
    pub(crate) fn reserve_stub(&mut self, lease: &str, stub: StubRecord) -> Result<()> {
        let mut exports = self.exports.clone();
        let r = exports
            .get_mut(lease)
            .ok_or_else(|| Error::State("missing Native export".into()))?;
        if r.stub.is_some() {
            return Err(Error::State(
                "Native persisted stub is already occupied".into(),
            ));
        }
        r.stub = Some(stub);
        let terminals = self.admit_tracking(&self.records, &exports)?;
        self.commit_full(self.records.clone(), exports, terminals)
    }
    pub(crate) fn retire_verified(&mut self, id: &str, proof: Proof) -> Result<()> {
        let mut records = self.records.clone();
        let mut terminals = self.terminals.clone();
        let r = records
            .remove(id)
            .ok_or_else(|| Error::State("missing Native retirement record".into()))?;
        if let Some(t) = terminal(
            id,
            Kind::NativeImport,
            &r.owner,
            r.target.device_key,
            &r.correlation,
            proof,
            Some(r.target.server_id),
        )? {
            terminals.push(t);
        }
        self.commit_full(records, self.exports.clone(), terminals)?;
        self.live.remove(id);
        self.unresolved.remove(id);
        Ok(())
    }
    pub(crate) fn retire_stub_verified(
        &mut self,
        lease: &str,
        id: &str,
        proof: Proof,
    ) -> Result<()> {
        let mut exports = self.exports.clone();
        let mut terminals = self.terminals.clone();
        let r = exports
            .get_mut(lease)
            .ok_or_else(|| Error::State("missing Native export".into()))?;
        let s = r
            .stub
            .take()
            .filter(|s| s.channel_id == id)
            .ok_or_else(|| Error::State("Native stub durable identity changed".into()))?;
        if let Some(t) = terminal(
            id,
            Kind::NativeStub,
            &r.owner,
            r.registration.device_key.clone(),
            &s.correlation,
            proof,
            None,
        )? {
            terminals.push(t);
        }
        self.commit_full(self.records.clone(), exports, terminals)
    }
    pub(crate) fn retire_export_verified(&mut self, id: &str, proof: Proof) -> Result<()> {
        let mut exports = self.exports.clone();
        let mut terminals = self.terminals.clone();
        let r = exports
            .remove(id)
            .ok_or_else(|| Error::State("missing Native export retirement record".into()))?;
        if let Some(s) = r.stub
            && let Some(t) = terminal(
                &s.channel_id,
                Kind::NativeStub,
                &r.owner,
                r.registration.device_key.clone(),
                &s.correlation,
                proof,
                None,
            )?
        {
            terminals.push(t);
        }
        if let Some(t) = terminal(
            id,
            Kind::NativeExport,
            &r.owner,
            r.registration.device_key,
            &r.correlation,
            proof,
            None,
        )? {
            terminals.push(t);
        }
        self.commit_full(self.records.clone(), exports, terminals)?;
        self.live.remove(id);
        self.unresolved.remove(id);
        Ok(())
    }
    pub(crate) fn broker_rows(
        &mut self,
    ) -> Result<Vec<(u32, Option<String>, BrokerPendingRecord)>> {
        self.healthy()?;
        let mut out = Vec::new();
        for r in self.records.values() {
            let phase = if self.unresolved.contains(&r.channel_id) {
                Progress::Unresolved
            } else {
                match r.state {
                    Phase::Prepared {} => Progress::Prepared,
                    Phase::Intent { .. } => Progress::Intent,
                    Phase::Observed { .. } => Progress::Observed,
                }
            };
            out.push((
                r.owner.uid,
                Some(r.target.server_id.clone()),
                BrokerPendingRecord {
                    resource_id: ResourceId::new(&r.channel_id)?,
                    kind: Kind::NativeImport,
                    device_key: r.target.device_key.clone(),
                    phase,
                    correlation: r.correlation.clone(),
                },
            ));
        }
        for r in self.exports.values() {
            let phase = if self.unresolved.contains(&r.lease_id) {
                Progress::Unresolved
            } else if r.bound.is_some() {
                Progress::Observed
            } else {
                Progress::Prepared
            };
            out.push((
                r.owner.uid,
                None,
                BrokerPendingRecord {
                    resource_id: ResourceId::new(&r.lease_id)?,
                    kind: Kind::NativeExport,
                    device_key: r.registration.device_key.clone(),
                    phase,
                    correlation: r.correlation.clone(),
                },
            ));
            if let Some(s) = &r.stub {
                let phase = if self.unresolved.contains(&r.lease_id) {
                    Progress::Unresolved
                } else {
                    match s.state {
                        StubPhase::Prepared {} => Progress::Prepared,
                        StubPhase::Intent {} => Progress::Intent,
                        StubPhase::Observed { .. } => Progress::Observed,
                    }
                };
                out.push((
                    r.owner.uid,
                    None,
                    BrokerPendingRecord {
                        resource_id: ResourceId::new(&s.channel_id)?,
                        kind: Kind::NativeStub,
                        device_key: r.registration.device_key.clone(),
                        phase,
                        correlation: s.correlation.clone(),
                    },
                ));
            }
        }
        Ok(out)
    }
    pub(crate) fn tracked_status(
        &mut self,
        uid: u32,
        kind: Kind,
        correlation: &Correlation,
    ) -> Result<Option<(BrokerOperationState, Option<String>)>> {
        correlation.validate()?;
        for (owner, server_id, r) in self.broker_rows()? {
            if owner == uid && r.kind == kind && r.correlation.as_ref() == Some(correlation) {
                return Ok(Some((
                    BrokerOperationState::Active {
                        resource_id: r.resource_id,
                        device_key: r.device_key,
                        phase: r.phase,
                    },
                    server_id,
                )));
            }
        }
        Ok(self
            .terminals
            .iter()
            .find(|e| {
                e.terminal.owner_uid == uid
                    && e.terminal.kind == kind
                    && &e.terminal.correlation == correlation
            })
            .map(|e| {
                (
                    BrokerOperationState::Terminal {
                        terminal: e.terminal.clone(),
                    },
                    e.server_id.clone(),
                )
            }))
    }
}

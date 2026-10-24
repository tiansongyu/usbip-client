fn correlation(number: u32) -> Result<BrokerCorrelation> {
    Ok(BrokerCorrelation {
        instance_id: rustusb_core::application::InstanceId::new(
            "10000000-0000-0000-0000-000000000001",
        )?,
        session_id: rustusb_core::application::SessionId::new(
            "20000000-0000-0000-0000-000000000002",
        )?,
        attempt_id: rustusb_core::application::AttemptId::new(format!(
            "30000000-0000-0000-0000-{number:012x}"
        ))?,
    })
}

fn tracked_record(number: u32) -> Result<Record> {
    let mut record = record(number)?;
    record.correlation = Some(correlation(number)?);
    Ok(record)
}

fn terminal(number: u32) -> Result<BrokerTerminal> {
    let record = tracked_record(number)?;
    Ok(BrokerTerminal {
        resource_id: ResourceId::new(record.lease_id)?,
        kind: BrokerResourceKind::UsbfsGrant,
        device_key: DeviceKey::physical(&record.baseline.device_id)?,
        owner_uid: record.owner.uid,
        correlation: correlation(number)?,
        proof: BrokerCleanupProof::PhysicalBaselineRestored,
    })
}

fn encoded_tracked(records: &[Record], terminals: &[BrokerTerminal]) -> Result<Vec<u8>> {
    serde_json::to_vec(&JournalRef {
        schema_version: TRACKED_SCHEMA_VERSION,
        records: records.iter().collect(),
        terminals,
    })
    .map_err(|error| std::io::Error::other(error).into())
}

#[test]
fn tracked_upgrade_preserves_old_permissions_and_explicit_null_correlations() -> Result<()> {
    for schema in [LEGACY_SCHEMA_VERSION, RECOVERY_SCHEMA_VERSION] {
        let fixture = Fixture::new()?;
        drop(fixture.open()?);
        let mut originals = vec![record(1)?];
        if schema == RECOVERY_SCHEMA_VERSION {
            let mut authorized = record(2)?;
            authorized.recovery = RecoveryAuthorization::ConnectOriginalDrivers;
            originals.push(authorized);
        }
        let before = encoded_version(originals.clone(), schema)?;
        fixture.replace(&before)?;
        let mut store = fixture.open()?;
        assert_eq!(store.records()?, originals);
        assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
        assert_eq!(store.tracked_status(1000, &correlation(1)?)?, None);
        let tracked = tracked_record(3)?;
        store.reserve(tracked.clone())?;
        assert_eq!(store.schema_version, TRACKED_SCHEMA_VERSION);
        let bytes = fs::read(fixture.path().join(JOURNAL))?;
        let value: serde_json::Value =
            serde_json::from_slice(&bytes).map_err(std::io::Error::other)?;
        assert_eq!(value["records"][0]["recovery"], "manual_only");
        assert!(
            value["records"][0]
                .get("correlation")
                .is_some_and(|value| value.is_null())
        );
        assert!(serde_json::from_slice::<JournalV1>(&bytes).is_err());
        assert!(serde_json::from_slice::<JournalV2>(&bytes).is_err());
        drop(store);
        let mut store = fixture.open()?;
        originals.push(tracked.clone());
        assert_eq!(store.records()?, originals);
        assert!(store.retire_verified(&tracked.lease_id, BrokerCleanupProof::NotStarted)?);
        for original in &originals[..originals.len() - 1] {
            assert!(store.retire(&original.lease_id)?);
        }
        assert_eq!(store.schema_version, TRACKED_SCHEMA_VERSION);
        assert!(store.records()?.is_empty());
        assert_eq!(store.terminals.len(), 1);
        drop(store);
        let mut store = fixture.open()?;
        store.reserve(record(4)?)?;
        assert_eq!(store.schema_version, TRACKED_SCHEMA_VERSION);
        assert!(
            store
                .get(&record(4)?.lease_id)?
                .is_some_and(|record| record.correlation.is_none())
        );
    }
    Ok(())
}

#[test]
fn physical_terminal_proofs_are_atomic_owner_scoped_and_survive_reopen() -> Result<()> {
    let fixture = Fixture::new()?;
    let mut store = fixture.open()?;
    for (index, proof) in [
        BrokerCleanupProof::NotStarted,
        BrokerCleanupProof::PhysicalBaselineRestored,
        BrokerCleanupProof::OriginalGenerationGone,
        BrokerCleanupProof::PreviousBoot,
    ]
    .into_iter()
    .enumerate()
    {
        let number = index as u32 + 1;
        let tracked = tracked_record(number)?;
        store.reserve(tracked.clone())?;
        assert_eq!(store.tracked_status(1001, &correlation(number)?)?, None);
        assert_eq!(
            store.tracked_status(1000, &correlation(number)?)?,
            Some(BrokerOperationState::Active {
                resource_id: ResourceId::new(tracked.lease_id.clone())?,
                device_key: DeviceKey::physical(&tracked.baseline.device_id)?,
                phase: BrokerOperationPhase::Granted,
            })
        );
        let before = fs::read(fixture.path().join(JOURNAL))?;
        assert!(store.retire(&tracked.lease_id).is_err());
        assert!(
            store
                .retire_verified(&tracked.lease_id, BrokerCleanupProof::NativeImportReleased)
                .is_err()
        );
        assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
        assert!(store.retire_verified(&tracked.lease_id, proof)?);
        assert!(!store.retire_verified(&tracked.lease_id, proof)?);
        drop(store);
        store = fixture.open()?;
        let mut expected = terminal(number)?;
        expected.proof = proof;
        assert_eq!(
            store.tracked_status(1000, &correlation(number)?)?,
            Some(BrokerOperationState::Terminal { terminal: expected })
        );
        assert_eq!(store.tracked_status(1001, &correlation(number)?)?, None);
        assert!(store.get(&tracked.lease_id)?.is_none());
        assert!(!store.has_device(&tracked.baseline.device_id)?);
    }
    Ok(())
}

#[test]
fn tracked_attempt_reuse_is_rejected_before_mutation_for_active_and_terminal() -> Result<()> {
    let fixture = Fixture::new()?;
    let mut store = fixture.open()?;
    let first = tracked_record(1)?;
    store.reserve(first.clone())?;
    for retired in [false, true] {
        if retired {
            store.retire_verified(
                &first.lease_id,
                BrokerCleanupProof::PhysicalBaselineRestored,
            )?;
        }
        let before = fs::read(fixture.path().join(JOURNAL))?;
        let mut repeated = tracked_record(2)?;
        repeated.correlation = first.correlation.clone();
        assert!(store.reserve(repeated).is_err());
        let mut invalid = tracked_record(3)?;
        let mut aliases = correlation(3)?;
        aliases.attempt_id =
            rustusb_core::application::AttemptId::new(aliases.instance_id.as_str())?;
        invalid.correlation = Some(aliases);
        assert!(store.reserve(invalid).is_err());
        let mut root = tracked_record(3)?;
        root.owner.uid = 0;
        assert!(store.reserve(root).is_err());
        assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
    }
    let mut separate_owner = tracked_record(2)?;
    separate_owner.owner.uid = 1001;
    separate_owner.correlation = first.correlation;
    store.reserve(separate_owner)?;
    assert!(matches!(
        store.tracked_status(1000, &correlation(1)?)?,
        Some(BrokerOperationState::Terminal { .. })
    ));
    assert!(matches!(
        store.tracked_status(1001, &correlation(1)?)?,
        Some(BrokerOperationState::Active { .. })
    ));
    drop(store);
    assert_eq!(fixture.open()?.records()?.len(), 1);
    Ok(())
}

#[test]
fn tracked_slots_reserve_cleanup_capacity_and_evict_only_oldest_terminal() -> Result<()> {
    let fixture = Fixture::new()?;
    drop(fixture.open()?);
    let mut records = (1..=MAX_TRACKED as u32)
        .map(tracked_record)
        .collect::<Result<Vec<_>>>()?;
    records.push(record(900)?);
    fixture.replace(&encoded_tracked(&records, &[])?)?;
    let mut store = fixture.open()?;
    let before = fs::read(fixture.path().join(JOURNAL))?;
    assert!(store.reserve(tracked_record(129)?).is_err());
    assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
    // Untracked records do not consume tracked retention capacity.
    store.reserve(record(901)?)?;
    store.retire_verified(&record(1)?.lease_id, BrokerCleanupProof::PreviousBoot)?;
    store.retire_verified(
        &record(2)?.lease_id,
        BrokerCleanupProof::OriginalGenerationGone,
    )?;
    assert_eq!(store.terminals.len(), 2);
    store.reserve(tracked_record(129)?)?;
    assert_eq!(store.tracked_status(1000, &correlation(1)?)?, None);
    assert!(matches!(
        store.tracked_status(1000, &correlation(2)?)?,
        Some(BrokerOperationState::Terminal { .. })
    ));
    assert!(matches!(
        store.tracked_status(1000, &correlation(3)?)?,
        Some(BrokerOperationState::Active { .. })
    ));
    drop(store);
    let mut store = fixture.open()?;
    assert_eq!(store.terminals.len(), 1);
    store.reserve(tracked_record(130)?)?;
    assert_eq!(store.tracked_status(1000, &correlation(2)?)?, None);
    assert!(store.terminals.is_empty());
    assert_eq!(store.records()?.len(), MAX_TRACKED + 2);
    assert!(store.reserve(tracked_record(131)?).is_err());
    Ok(())
}

#[test]
fn disk_three_requires_correlations_and_terminals_and_rejects_cross_namespace_proof() -> Result<()>
{
    let fixture = Fixture::new()?;
    drop(fixture.open()?);
    let valid: serde_json::Value =
        serde_json::from_slice(&encoded_tracked(&[tracked_record(1)?], &[terminal(2)?])?)
            .map_err(std::io::Error::other)?;
    let mut cases = Vec::new();
    let mut missing = valid.clone();
    missing["records"][0]
        .as_object_mut()
        .ok_or_else(|| Error::State("fixture record".into()))?
        .remove("correlation");
    cases.push(missing);
    let mut missing = valid.clone();
    missing
        .as_object_mut()
        .ok_or_else(|| Error::State("fixture journal".into()))?
        .remove("terminals");
    cases.push(missing);
    for (field, value) in [
        ("kind", serde_json::json!("native_import")),
        ("device_key", serde_json::json!("virtual:hid")),
        ("owner_uid", serde_json::json!(0)),
        ("proof", serde_json::json!("native_export_restored")),
    ] {
        let mut malformed = valid.clone();
        malformed["terminals"][0][field] = value;
        cases.push(malformed);
    }
    for value in cases {
        fixture.replace(&serde_json::to_vec(&value).map_err(std::io::Error::other)?)?;
        assert!(fixture.open().is_err());
    }
    let mut null_record = record(1)?;
    null_record.correlation = None;
    fixture.replace(&encoded_tracked(&[null_record.clone()], &[])?)?;
    assert_eq!(fixture.open()?.records()?, vec![null_record]);
    let text = String::from_utf8(encoded_tracked(&[tracked_record(1)?], &[terminal(2)?])?)
        .map_err(std::io::Error::other)?;
    for (needle, replacement) in [
        (
            "\"schema_version\":3",
            "\"schema_version\":3,\"schema_version\":3",
        ),
        (
            "\"owner_uid\":1000",
            "\"owner_uid\":1000,\"owner_uid\":1000",
        ),
        (
            "\"kind\":\"usbfs_grant\"",
            "\"kind\":\"usbfs_grant\",\"unexpected\":true",
        ),
    ] {
        let malformed = text.replace(needle, replacement);
        assert_ne!(malformed, text);
        fixture.replace(malformed.as_bytes())?;
        assert!(fixture.open().is_err());
    }
    Ok(())
}

#[test]
fn old_schemas_forbid_tracking_fields_and_disk_three_rejects_duplicate_or_excess_slots()
-> Result<()> {
    let fixture = Fixture::new()?;
    drop(fixture.open()?);
    for schema in [LEGACY_SCHEMA_VERSION, RECOVERY_SCHEMA_VERSION] {
        let original: serde_json::Value =
            serde_json::from_slice(&encoded_version(vec![record(1)?], schema)?)
                .map_err(std::io::Error::other)?;
        let mut correlation = original.clone();
        correlation["records"][0]["correlation"] = serde_json::Value::Null;
        let mut terminals = original;
        terminals["terminals"] = serde_json::json!([]);
        for value in [correlation, terminals] {
            fixture.replace(&serde_json::to_vec(&value).map_err(std::io::Error::other)?)?;
            assert!(fixture.open().is_err());
        }
    }
    let mut repeated = tracked_record(2)?;
    repeated.correlation = Some(correlation(1)?);
    let mut repeated_terminal = terminal(2)?;
    repeated_terminal.correlation = correlation(1)?;
    for bytes in [
        encoded_tracked(&[tracked_record(1)?, repeated], &[])?,
        encoded_tracked(&[tracked_record(1)?], &[repeated_terminal])?,
        encoded_tracked(&[], &[terminal(1)?, terminal(1)?])?,
        encoded_tracked(&[tracked_record(1)?], &[terminal(1)?])?,
        encoded_tracked(
            &(1..=129).map(tracked_record).collect::<Result<Vec<_>>>()?,
            &[],
        )?,
        encoded_tracked(&[], &(1..=129).map(terminal).collect::<Result<Vec<_>>>()?)?,
        encoded_tracked(
            &[tracked_record(129)?],
            &(1..=128).map(terminal).collect::<Result<Vec<_>>>()?,
        )?,
    ] {
        fixture.replace(&bytes)?;
        assert!(fixture.open().is_err());
    }
    Ok(())
}

#[test]
fn tracked_upgrade_failure_preserves_cache_and_quarantines_all_reads() -> Result<()> {
    for after_rename in [false, true] {
        let fixture = Fixture::new()?;
        let mut store = fixture.open()?;
        store.reserve(record(1)?)?;
        let before = fs::read(fixture.path().join(JOURNAL))?;
        if after_rename {
            store.fail_after_rename = true;
        } else {
            fs::create_dir(fixture.path().join(TEMP))?;
        }
        assert!(store.reserve(tracked_record(2)?).is_err());
        assert_eq!(store.schema_version, LEGACY_SCHEMA_VERSION);
        assert_eq!(store.records.len(), 1);
        assert!(store.terminals.is_empty());
        assert!(store.tracked_status(1000, &correlation(2)?).is_err());
        assert!(store.records().is_err());
        assert!(
            store
                .retire_verified(&record(1)?.lease_id, BrokerCleanupProof::NotStarted)
                .is_err()
        );
        if !after_rename {
            assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
        }
        drop(store);
        let store = fixture.open()?;
        assert_eq!(store.records()?.len(), if after_rename { 2 } else { 1 });
        assert_eq!(
            store.schema_version,
            if after_rename {
                TRACKED_SCHEMA_VERSION
            } else {
                LEGACY_SCHEMA_VERSION
            }
        );
    }
    Ok(())
}

#[test]
fn failed_terminal_commit_never_installs_partial_cache_or_suppresses_durable_evidence() -> Result<()>
{
    for after_rename in [false, true] {
        let fixture = Fixture::new()?;
        let mut store = fixture.open()?;
        let tracked = tracked_record(1)?;
        store.reserve(tracked.clone())?;
        let before = fs::read(fixture.path().join(JOURNAL))?;
        if after_rename {
            store.fail_after_rename = true;
        } else {
            fs::create_dir(fixture.path().join(TEMP))?;
        }
        assert!(
            store
                .retire_verified(
                    &tracked.lease_id,
                    BrokerCleanupProof::PhysicalBaselineRestored
                )
                .is_err()
        );
        assert_eq!(store.records.get(&tracked.lease_id), Some(&tracked));
        assert!(store.terminals.is_empty());
        assert!(store.tracked_status(1000, &correlation(1)?).is_err());
        assert!(store.reserve(tracked_record(2)?).is_err());
        if !after_rename {
            assert_eq!(fs::read(fixture.path().join(JOURNAL))?, before);
        }
        drop(store);
        // Reopening observes the actual renamed file; this is not a power-loss test.
        let store = fixture.open()?;
        assert_eq!(store.records()?.is_empty(), after_rename);
        assert_eq!(store.terminals.len(), usize::from(after_rename));
        assert_eq!(
            store.tracked_status(1000, &correlation(1)?)?,
            if after_rename {
                Some(BrokerOperationState::Terminal {
                    terminal: terminal(1)?,
                })
            } else {
                Some(BrokerOperationState::Active {
                    resource_id: ResourceId::new(tracked.lease_id)?,
                    device_key: DeviceKey::physical(&tracked.baseline.device_id)?,
                    phase: BrokerOperationPhase::Granted,
                })
            }
        );
    }
    Ok(())
}

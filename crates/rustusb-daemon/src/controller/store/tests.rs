use super::*;
use std::{
    fs,
    os::unix::fs::{PermissionsExt, symlink},
    path::PathBuf,
};
struct Fixture {
    temp: tempfile::TempDir,
    path: PathBuf,
    uid: u32,
}
impl Fixture {
    fn new() -> Result<Self> {
        let temp = tempfile::tempdir()?;
        fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700))?;
        let path = temp.path().join("controllers");
        let uid = fs::metadata(temp.path())?.uid();
        Ok(Self { temp, path, uid })
    }
    fn open(&self) -> Result<Store> {
        Store::open_anchored(&self.path, self.temp.path(), self.uid)
    }
}
fn change(revision: u64, policy: u32, request: u32) -> Result<Change> {
    Ok(Change {
        policy_id: PolicyId::new(format!("10000000-0000-0000-0000-{policy:012x}"))?,
        request_id: RequestId::new(format!("20000000-0000-0000-0000-{request:012x}"))?,
        expected_revision: revision,
        mutation: Mutation::Enroll {
            selector: Selector {
                address: format!("0000:00:{:02x}.0", policy % 32).parse()?,
                vendor_id: 1,
                device_id: 2,
                subsystem_vendor_id: 3,
                subsystem_device_id: 4,
            },
            expected_boot: "30000000-0000-0000-0000-000000000001".into(),
        },
    })
}
fn verification() -> Result<Verification> {
    Ok(Verification {
        helper_epoch: InstanceId::new("40000000-0000-0000-0000-000000000001")?,
        boot_id: "30000000-0000-0000-0000-000000000001".into(),
        pci: ObjectIdentity {
            device: 1,
            inode: 2,
        },
        roots: [(1, 480), (2, 10000)]
            .into_iter()
            .map(|(bus, speed)| RootObservation {
                bus,
                speed,
                identity: ObjectIdentity {
                    device: 1,
                    inode: u64::from(bus) + 10,
                },
                device_attribute: ObjectIdentity {
                    device: 1,
                    inode: u64::from(bus) + 20,
                },
                interface_attribute: ObjectIdentity {
                    device: 1,
                    inode: u64::from(bus) + 30,
                },
                device_default: 0,
                interface_default: 0,
            })
            .collect(),
    })
}
fn begin(s: &mut Store, c: &Change) -> Result<()> {
    let selector = s.selection(c)?;
    s.begin(c, selector)
}
#[test]
fn persisted_pending_reopen_cas_and_exact_replay() -> Result<()> {
    let f = Fixture::new()?;
    let mut s = f.open()?;
    assert!(f.open().is_err());
    let c = change(0, 1, 1)?;
    begin(&mut s, &c)?;
    assert_eq!(s.disk().revision, 1);
    assert!(s.selection(&c).is_err());
    assert!(matches!(
        s.replay(&c)?.map(|r| r.outcome),
        Some(Outcome::Pending {})
    ));
    let mut altered = c.clone();
    altered.expected_revision = 1;
    assert!(s.replay(&altered).is_err());
    drop(s);
    let mut s = f.open()?;
    assert_eq!(s.replay(&c)?.map(|r| r.revision), Some(1));
    let result = s.complete(
        &c,
        Outcome::Armed {
            verification: verification()?,
        },
    )?;
    assert_eq!(s.replay(&c)?, Some(result));
    drop(s);
    assert!(matches!(
        f.open()?.disk().records[0].outcome,
        Outcome::Armed { .. }
    ));
    Ok(())
}
#[test]
fn retirement_keeps_monotonic_history_and_expired_requests_cannot_reapply() -> Result<()> {
    let f = Fixture::new()?;
    let mut s = f.open()?;
    let first = change(0, 1, 1)?;
    begin(&mut s, &first)?;
    s.complete(
        &first,
        Outcome::Armed {
            verification: verification()?,
        },
    )?;
    let mut retire = change(1, 1, 2)?;
    retire.mutation = Mutation::Retire {
        expected_boot: first.mutation.boot().into(),
    };
    begin(&mut s, &retire)?;
    s.complete(&retire, Outcome::Retired {})?;
    assert!(s.disk().records[0].retired);
    let third = change(2, 1, 3)?;
    begin(&mut s, &third)?;
    assert_eq!(s.disk().revision, 3);
    assert!(s.replay(&first)?.is_none());
    assert!(s.selection(&first).is_err());
    assert_eq!(s.disk().records.len(), 1);
    Ok(())
}
#[test]
fn uncertain_commits_poison_and_restart_reads_actual_disk() -> Result<()> {
    for stage in [1, 2] {
        let f = Fixture::new()?;
        let mut s = f.open()?;
        let c = change(0, 1, 1)?;
        begin(&mut s, &c)?;
        s.fail_stage = stage;
        assert!(
            s.complete(
                &c,
                Outcome::Armed {
                    verification: verification()?
                }
            )
            .is_err()
        );
        assert!(s.poisoned());
        assert!(s.replay(&c).is_err());
        drop(s);
        let s = f.open()?;
        assert_eq!(s.disk().revision, 1);
        assert_eq!(
            matches!(s.disk().records[0].outcome, Outcome::Armed { .. }),
            stage == 2
        );
    }
    Ok(())
}
#[test]
fn missing_corrupt_oversized_and_unknown_history_refuse_reset() -> Result<()> {
    for cause in [
        "missing_data",
        "missing_lock",
        "malformed",
        "oversized",
        "unknown",
        "schema",
        "symlink",
        "hardlink",
        "permissions",
    ] {
        let f = Fixture::new()?;
        drop(f.open()?);
        let data = f.path.join(DATA);
        match cause {
            "missing_data" => fs::remove_file(&data)?,
            "missing_lock" => fs::remove_file(f.path.join(LOCK))?,
            "malformed" => fs::write(&data, b"{")?,
            "oversized" => fs::write(&data, vec![b' '; LIMIT + 1])?,
            "unknown" => fs::write(
                &data,
                b"{\"schema_version\":1,\"revision\":0,\"records\":[],\"unknown\":0}",
            )?,
            "schema" => fs::write(
                &data,
                b"{\"schema_version\":2,\"revision\":0,\"records\":[]}",
            )?,
            "symlink" => {
                let old = f.path.join("old");
                fs::rename(&data, &old)?;
                symlink(&old, &data)?;
            }
            "hardlink" => fs::hard_link(&data, f.path.join("extra-link"))?,
            _ => fs::set_permissions(&data, fs::Permissions::from_mode(0o644))?,
        }
        assert!(f.open().is_err(), "{cause}");
    }
    Ok(())
}
#[test]
fn live_store_replacement_and_mode_drift_poison() -> Result<()> {
    for cause in ["data", "lock", "directory"] {
        let f = Fixture::new()?;
        let mut s = f.open()?;
        match cause {
            "data" => fs::write(f.path.join(DATA), b"{}")?,
            "lock" => {
                let p = f.path.join(LOCK);
                fs::rename(&p, f.path.join("old-lock"))?;
                fs::write(&p, b"")?;
                fs::set_permissions(&p, fs::Permissions::from_mode(0o600))?;
            }
            _ => fs::set_permissions(&f.path, fs::Permissions::from_mode(0o750))?,
        }
        assert!(s.replay(&change(0, 1, 1)?).is_err());
        assert!(s.poisoned());
    }
    Ok(())
}
#[test]
fn bounded_slots_duplicate_controller_and_completion_reserve() -> Result<()> {
    let f = Fixture::new()?;
    let mut s = f.open()?;
    let first = change(0, 1, 1)?;
    begin(&mut s, &first)?;
    let mut duplicate = change(1, 2, 2)?;
    duplicate.mutation = first.mutation.clone();
    assert!(s.selection(&duplicate).is_err());
    s.complete(
        &first,
        Outcome::Armed {
            verification: verification()?,
        },
    )?;
    for i in 2..=16 {
        let c = change(u64::from(i - 1), i, i)?;
        begin(&mut s, &c)?;
        let before = serde_json::to_vec(s.disk())
            .map_err(|e| Error::State(e.to_string()))?
            .len();
        s.complete(
            &c,
            Outcome::Armed {
                verification: verification()?,
            },
        )?;
        let after = serde_json::to_vec(s.disk())
            .map_err(|e| Error::State(e.to_string()))?
            .len();
        assert!(after - before < 4096);
    }
    assert!(s.selection(&change(16, 17, 17)?).is_err());
    assert_eq!(s.disk().records.len(), 16);
    assert!(s.bytes.len() < LIMIT);
    Ok(())
}

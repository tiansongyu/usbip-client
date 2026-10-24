use super::*;
use crate::{AuditProjection, Level};
use rustusb_core::application::InstanceId;
use std::{
    fs::{self, OpenOptions},
    os::unix::fs::{PermissionsExt, symlink},
    sync::mpsc,
    time::Duration,
};

struct Fixture {
    root: tempfile::TempDir,
    options: Options,
}
impl Fixture {
    fn new() -> Result<Self> {
        let root = tempfile::tempdir()?;
        fs::set_permissions(root.path(), fs::Permissions::from_mode(0o700))?;
        let directory = root.path().join("logs");
        fs::create_dir(&directory)?;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?;
        Ok(Self {
            root,
            options: Options {
                directory,
                segment_bytes: 4096,
                retained_segments: 3,
            },
        })
    }
    fn open(&self, epoch: &LogId) -> Result<Storage> {
        Storage::fixture(&self.options, epoch, self.root.path())
    }
    fn directory(&self) -> Result<Directory> {
        Directory::open_from(
            &self.options.directory,
            false,
            self.root.path(),
            Some(rustix::process::geteuid().as_raw()),
        )
    }
    fn path(&self, name: &str) -> PathBuf {
        self.options.directory.join(name)
    }
}
fn id(value: u64) -> Result<LogId> {
    LogId::new(format!("00000000-0000-0000-0000-{value:012x}"))
}
fn record(epoch: &LogId, sequence: u64) -> LogRecord {
    LogRecord {
        schema_version: 1,
        epoch: epoch.clone(),
        sequence,
        elapsed_millis: sequence,
        wall_unix_millis: None,
        level: Level::Info,
        event: Event::Started {
            pid: std::process::id(),
            uid: rustix::process::geteuid().as_raw(),
        },
    }
}
fn audit(
    epoch: &LogId,
    sequence: u64,
    profile: Option<&str>,
    uid: u32,
    action: AuditAction,
) -> Result<LogRecord> {
    let mut value = record(epoch, sequence);
    value.event = Event::Audit(AuditProjection {
        application_instance: InstanceId::new(id(999)?.as_str())
            .map_err(|_| Error::InvalidInput("test instance"))?,
        audit_sequence: sequence,
        actor_uid: uid,
        profile: profile.map(str::to_owned),
        job_id: None,
        session_id: None,
        action,
        failure: None,
        state: None,
        evidence: None,
        cleanup: None,
    });
    Ok(value)
}

#[test]
fn empty_reader_does_not_create_locks_or_headers() -> Result<()> {
    let fixture = Fixture::new()?;
    let page = reader::fixture_read(&fixture.directory()?, None, 16)?;
    assert!(page.records.is_empty());
    assert!(page.next_after.is_none());
    assert!(!page.gap);
    assert_eq!(fs::read_dir(&fixture.options.directory)?.count(), 0);
    Ok(())
}

#[test]
fn writer_header_is_not_a_started_record_and_append_returns_synced_boundary() -> Result<()> {
    let fixture = Fixture::new()?;
    let epoch = id(1)?;
    let mut store = fixture.open(&epoch)?;
    let source = store.source();
    let empty = read_source(&source, &ReadFilter::All, None, 16)?;
    assert!(empty.records.is_empty());
    let cursor = store.append(&record(&epoch, 1))?;
    let page = read_source(&source, &ReadFilter::All, None, 16)?;
    assert_eq!(page.records.len(), 1);
    assert_eq!(page.next_after, Some(cursor.clone()));
    assert_eq!(
        fs::metadata(fixture.path(&slot_name(0)))?.len(),
        cursor.offset()
    );
    assert!(
        read_source(&source, &ReadFilter::All, Some(&cursor), 16)?
            .records
            .is_empty()
    );
    store.sync()?;
    Ok(())
}

#[test]
fn rotation_is_complete_bounded_and_expired_cursor_reports_gap() -> Result<()> {
    let fixture = Fixture::new()?;
    let epoch = id(2)?;
    let mut store = fixture.open(&epoch)?;
    let source = store.source();
    let old = store.append(&record(&epoch, 1))?;
    for sequence in 2..=90 {
        store.append(&record(&epoch, sequence))?;
    }
    let segments = inventory(&fixture.directory()?)?;
    assert_eq!(segments.len(), 3);
    assert!(
        segments
            .iter()
            .all(|s| s.stamp.bytes <= fixture.options.segment_bytes)
    );
    assert!(
        segments
            .windows(2)
            .all(|pair| pair[0].header.ordinal > pair[1].header.ordinal)
    );
    let mut page = read_source(&source, &ReadFilter::All, Some(&old), 7)?;
    assert!(page.gap);
    let mut sequences = Vec::new();
    for _ in 0..30 {
        sequences.extend(page.records.iter().map(|record| record.sequence));
        if !page.more {
            break;
        }
        page = read_source(&source, &ReadFilter::All, page.next_after.as_ref(), 7)?;
    }
    assert!(sequences.windows(2).all(|pair| pair[0] < pair[1]));
    assert_eq!(sequences.last(), Some(&90));
    assert!(sequences.first().is_some_and(|value| *value > 1));
    validate_history(&fixture.directory()?, &segments)?;
    Ok(())
}

#[test]
fn restart_seals_partial_tail_without_rewriting_original_bytes() -> Result<()> {
    let fixture = Fixture::new()?;
    let epoch = id(3)?;
    let mut old = fixture.open(&epoch)?;
    old.append(&record(&epoch, 1))?;
    drop(old);
    let mut damaged = OpenOptions::new()
        .append(true)
        .open(fixture.path(&slot_name(0)))?;
    damaged.write_all(b"{\"incomplete\":")?;
    damaged.sync_all()?;
    drop(damaged);
    let original = fs::read(fixture.path(&slot_name(0)))?;
    let new_epoch = id(4)?;
    let mut store = fixture.open(&new_epoch)?;
    assert_eq!(fs::read(fixture.path(&slot_name(1)))?, original);
    store.append(&record(&new_epoch, 1))?;
    let page = read_source(&store.source(), &ReadFilter::All, None, 16)?;
    assert_eq!(page.records.len(), 2);
    assert!(page.gap && page.partial_tail);
    assert_eq!(page.records[0].epoch, epoch);
    assert_eq!(page.records[1].epoch, new_epoch);
    assert_eq!(fs::read(fixture.path(&slot_name(1)))?, original);
    Ok(())
}

#[test]
fn malformed_complete_record_prevents_startup_without_deleting_history() -> Result<()> {
    let fixture = Fixture::new()?;
    drop(fixture.open(&id(5)?)?);
    let mut file = OpenOptions::new()
        .append(true)
        .open(fixture.path(&slot_name(0)))?;
    file.write_all(b"{\"not_a_record\":true}\n")?;
    file.sync_all()?;
    drop(file);
    let original = fs::read(fixture.path(&slot_name(0)))?;
    assert!(matches!(fixture.open(&id(6)?), Err(Error::Corrupt(_))));
    assert_eq!(fs::read(fixture.path(&slot_name(0)))?, original);
    assert!(!fixture.path(&slot_name(1)).exists());
    Ok(())
}

#[test]
fn header_unknown_fields_duplicate_ids_and_slot_gaps_are_not_guessed() -> Result<()> {
    for variant in 0..3 {
        let fixture = Fixture::new()?;
        drop(fixture.open(&id(10)?)?);
        let original = fs::read(fixture.path(&slot_name(0)))?;
        match variant {
            0 => {
                let mut header: serde_json::Value =
                    serde_json::from_slice(&original).map_err(|_| Error::Corrupt("test header"))?;
                header["unexpected"] = true.into();
                let mut bytes =
                    serde_json::to_vec(&header).map_err(|_| Error::Corrupt("test header"))?;
                bytes.push(b'\n');
                fs::write(fixture.path(&slot_name(0)), bytes)?;
            }
            1 => {
                fs::write(fixture.path(&slot_name(1)), &original)?;
                fs::set_permissions(
                    fixture.path(&slot_name(1)),
                    fs::Permissions::from_mode(0o600),
                )?;
            }
            _ => {
                fs::rename(fixture.path(&slot_name(0)), fixture.path(&slot_name(1)))?;
            }
        }
        let before: Vec<_> = fs::read_dir(&fixture.options.directory)?.collect();
        assert!(matches!(fixture.open(&id(11)?), Err(Error::Corrupt(_))));
        assert_eq!(
            fs::read_dir(&fixture.options.directory)?.count(),
            before.len()
        );
    }
    Ok(())
}

#[test]
fn second_writer_is_busy_and_readers_use_independent_lock_descriptions() -> Result<()> {
    let fixture = Fixture::new()?;
    let store = fixture.open(&id(12)?)?;
    assert!(matches!(fixture.open(&id(13)?), Err(Error::Busy)));
    let gate = store.directory.access(store.access_identity, true)?;
    let probe = store.directory.open_file(ACCESS, false, false)?;
    assert_eq!(
        rustix::fs::flock(&probe, FlockOperation::NonBlockingLockShared),
        Err(rustix::io::Errno::WOULDBLOCK),
    );
    drop(probe);
    let source = store.source();
    let (sent, receive) = mpsc::channel();
    let (started, entered) = mpsc::channel();
    let worker = std::thread::spawn(move || {
        let _ = started.send(());
        let result = read_source(&source, &ReadFilter::All, None, 16);
        let _ = sent.send(result);
    });
    let began = entered.recv_timeout(Duration::from_secs(2)).is_ok();
    let blocked = matches!(
        receive.recv_timeout(Duration::from_millis(50)),
        Err(mpsc::RecvTimeoutError::Timeout)
    );
    drop(gate); // Always release the real lock, including on assertion failure.
    let result = receive
        .recv_timeout(Duration::from_secs(2))
        .map_err(|_| Error::Worker("reader did not finish after lock release"))?;
    worker
        .join()
        .map_err(|_| Error::Worker("reader fixture panicked"))?;
    assert!(began);
    assert!(blocked);
    assert!(result?.records.is_empty());
    Ok(())
}

#[test]
fn typed_audit_filter_never_exposes_generic_or_other_profile_records() -> Result<()> {
    let fixture = Fixture::new()?;
    let epoch = id(14)?;
    let mut store = fixture.open(&epoch)?;
    store.append(&record(&epoch, 1))?;
    store.append(&audit(&epoch, 2, Some("allowed"), 700, AuditAction::Start)?)?;
    store.append(&audit(&epoch, 3, Some("other"), 700, AuditAction::Denied)?)?;
    store.append(&audit(&epoch, 4, None, 700, AuditAction::Denied)?)?;
    store.append(&audit(&epoch, 5, None, 701, AuditAction::Denied)?)?;
    store.append(&audit(&epoch, 6, None, 700, AuditAction::Recovery)?)?;
    let source = store.source();
    let filter = ReadFilter::Audit {
        profile: "allowed".into(),
        actor_uid: 700,
        root_admin: false,
    };
    let page = read_source(&source, &filter, None, 16)?;
    assert_eq!(
        page.records.iter().map(|r| r.sequence).collect::<Vec<_>>(),
        [2, 4]
    );
    let root = ReadFilter::Audit {
        profile: "allowed".into(),
        actor_uid: 0,
        root_admin: true,
    };
    assert_eq!(
        read_source(&source, &root, None, 16)?
            .records
            .iter()
            .map(|r| r.sequence)
            .collect::<Vec<_>>(),
        [2, 4, 5, 6]
    );
    assert!(matches!(
        read_source(
            &source,
            &ReadFilter::Audit {
                profile: "allowed".into(),
                actor_uid: 700,
                root_admin: true
            },
            None,
            16
        ),
        Err(Error::InvalidInput(_))
    ));
    Ok(())
}

#[test]
fn filtered_scan_window_has_bounded_bytes_and_forward_progress() -> Result<()> {
    let mut fixture = Fixture::new()?;
    fixture.options.segment_bytes = MAX_SEGMENT_BYTES;
    let epoch = id(15)?;
    let store = fixture.open(&epoch)?;
    let source = store.source();
    drop(store);
    // A stored-history fixture avoids thousands of incidental fsync calls.
    let mut file = OpenOptions::new()
        .append(true)
        .open(fixture.path(&slot_name(0)))?;
    for sequence in 1..=1800 {
        file.write_all(&record(&epoch, sequence).encode_line()?)?;
    }
    file.sync_all()?;
    drop(file);
    let filter = ReadFilter::Audit {
        profile: "allowed".into(),
        actor_uid: 700,
        root_admin: false,
    };
    let first = read_source(&source, &filter, None, 16)?;
    assert!(first.records.is_empty() && first.more);
    assert!(first.scanned_bytes > 250_000 && first.scanned_bytes as usize <= MAX_SCAN_BYTES);
    let next = read_source(&source, &filter, first.next_after.as_ref(), 16)?;
    assert!(next.records.is_empty());
    assert_ne!(first.next_after, next.next_after);
    Ok(())
}

#[test]
fn cursor_inside_record_and_page_overflow_are_rejected() -> Result<()> {
    let fixture = Fixture::new()?;
    let epoch = id(16)?;
    let mut store = fixture.open(&epoch)?;
    let cursor = store.append(&record(&epoch, 1))?;
    let bad = Cursor::new(cursor.segment.clone(), cursor.offset - 1)?;
    assert!(matches!(
        read_source(&store.source(), &ReadFilter::All, Some(&bad), 16),
        Err(Error::InvalidInput(_))
    ));
    assert!(matches!(
        read_source(&store.source(), &ReadFilter::All, None, 17),
        Err(Error::InvalidInput(_))
    ));
    assert!(matches!(
        read_source(&store.source(), &ReadFilter::All, None, 0),
        Err(Error::InvalidInput(_))
    ));
    Ok(())
}

#[test]
fn unsafe_links_modes_unknown_files_and_missing_lock_preserve_originals() -> Result<()> {
    for variant in 0..5 {
        let fixture = Fixture::new()?;
        drop(fixture.open(&id(20)?)?);
        let original = fs::read(fixture.path(&slot_name(0)))?;
        match variant {
            0 => {
                fs::set_permissions(
                    fixture.path(&slot_name(0)),
                    fs::Permissions::from_mode(0o644),
                )?;
            }
            1 => {
                fs::hard_link(
                    fixture.path(&slot_name(0)),
                    fixture.root.path().join("duplicate"),
                )?;
            }
            2 => {
                fs::rename(
                    fixture.path(&slot_name(0)),
                    fixture.root.path().join("outside"),
                )?;
                symlink(
                    fixture.root.path().join("outside"),
                    fixture.path(&slot_name(0)),
                )?;
            }
            3 => {
                fs::write(fixture.path("unrelated"), b"keep")?;
            }
            _ => {
                fs::remove_file(fixture.path(ACCESS))?;
            }
        }
        assert!(fixture.open(&id(21)?).is_err());
        assert_eq!(fs::read(fixture.path(&slot_name(0)))?, original);
        if variant == 3 {
            assert_eq!(fs::read(fixture.path("unrelated"))?, b"keep");
        }
        if variant == 4 {
            assert!(!fixture.path(ACCESS).exists());
        }
    }
    Ok(())
}

#[test]
fn directory_and_active_replacement_latch_writer_failure() -> Result<()> {
    for replace_directory in [false, true] {
        let fixture = Fixture::new()?;
        let epoch = id(22)?;
        let mut store = fixture.open(&epoch)?;
        let source = store.source();
        if replace_directory {
            fs::rename(&fixture.options.directory, fixture.root.path().join("old"))?;
            fs::create_dir(&fixture.options.directory)?;
            fs::set_permissions(
                &fixture.options.directory,
                fs::Permissions::from_mode(0o700),
            )?;
        } else {
            fs::rename(fixture.path(&slot_name(0)), fixture.root.path().join("old"))?;
            fs::write(fixture.path(&slot_name(0)), b"replacement\n")?;
            fs::set_permissions(
                fixture.path(&slot_name(0)),
                fs::Permissions::from_mode(0o600),
            )?;
        }
        assert!(store.append(&record(&epoch, 1)).is_err());
        assert!(matches!(
            store.append(&record(&epoch, 2)),
            Err(Error::Worker(_))
        ));
        assert!(read_source(&source, &ReadFilter::All, None, 16).is_err());
    }
    Ok(())
}

#[test]
fn fifo_is_refused_without_blocking_and_public_tmp_path_is_not_trusted() -> Result<()> {
    let fixture = Fixture::new()?;
    rustix::fs::mknodat(
        &fixture.directory()?.file,
        WRITER,
        FileType::Fifo,
        Mode::RUSR | Mode::WUSR,
        0,
    )
    .map_err(io)?;
    assert!(matches!(fixture.open(&id(24)?), Err(Error::Permission(_))));
    assert!(matches!(
        Storage::open(&fixture.options, &id(25)?),
        Err(Error::Permission(_))
    ));
    Ok(())
}

#[test]
fn actual_write_error_is_returned_and_storage_cannot_reopen_as_healthy() -> Result<()> {
    let fixture = Fixture::new()?;
    let epoch = id(26)?;
    let mut store = fixture.open(&epoch)?;
    let before = fs::read(fixture.path(&slot_name(0)))?;
    // Replace only this fixture's writer handle with a real read-only handle of
    // the same inode. The OS write returns EBADF; no synthetic error is injected.
    store.active = Some(File::open(fixture.path(&slot_name(0)))?);
    assert!(matches!(
        store.append(&record(&epoch, 1)),
        Err(Error::Io(_))
    ));
    assert!(matches!(store.sync(), Err(Error::Worker(_))));
    assert_eq!(fs::read(fixture.path(&slot_name(0)))?, before);
    Ok(())
}

#[test]
fn lowering_byte_limit_or_exhausting_ordinal_does_not_rewrite_history() -> Result<()> {
    let mut fixture = Fixture::new()?;
    fixture.options.segment_bytes = 8192;
    let epoch = id(27)?;
    let mut store = fixture.open(&epoch)?;
    for sequence in 1..=30 {
        store.append(&record(&epoch, sequence))?;
    }
    drop(store);
    let before = fs::read(fixture.path(&slot_name(0)))?;
    assert!(before.len() > 4096);
    fixture.options.segment_bytes = 4096;
    assert!(matches!(
        fixture.open(&id(28)?),
        Err(Error::InvalidInput(_))
    ));
    assert_eq!(fs::read(fixture.path(&slot_name(0)))?, before);

    let other = Fixture::new()?;
    drop(other.open(&id(29)?)?);
    let original = fs::read(other.path(&slot_name(0)))?;
    let mut header: Header =
        serde_json::from_slice(&original).map_err(|_| Error::Corrupt("test header"))?;
    header.ordinal = u64::MAX;
    let mut bytes = serde_json::to_vec(&header).map_err(|_| Error::Corrupt("test header"))?;
    bytes.push(b'\n');
    fs::write(other.path(&slot_name(0)), &bytes)?;
    assert!(matches!(other.open(&id(30)?), Err(Error::Corrupt(_))));
    assert_eq!(fs::read(other.path(&slot_name(0)))?, bytes);
    Ok(())
}

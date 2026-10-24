//! These ordinary-user filesystem fixtures trust only their private temporary
//! anchor. They do not claim that production accepts the writable /tmp chain.

use super::*;
use std::{
    fs,
    os::unix::fs::{PermissionsExt, symlink},
    sync::{Arc, Barrier},
};

type TestResult = Result<(), Box<dyn std::error::Error>>;
const DOCUMENT: &[u8] = b"{\"schema_version\":1,\"complete\":false}\n";

struct Fixture {
    anchor: tempfile::TempDir,
    directory: PathBuf,
    target: PathBuf,
}

impl Fixture {
    fn new() -> Result<Self, Box<dyn std::error::Error>> {
        let anchor = tempfile::tempdir()?;
        fs::set_permissions(anchor.path(), fs::Permissions::from_mode(0o700))?;
        let directory = anchor.path().join("private");
        fs::create_dir(&directory)?;
        fs::set_permissions(&directory, fs::Permissions::from_mode(0o700))?;
        Ok(Self {
            target: directory.join("bundle.json"),
            directory,
            anchor,
        })
    }

    fn prepare(&self) -> Result<Output, FileFailure> {
        Output::prepare(&self.target, self.anchor.path())
    }
}

fn failure<T>(result: Result<T, FileFailure>) -> Result<FileFailure, Box<dyn std::error::Error>> {
    match result {
        Err(error) => Ok(error),
        Ok(_) => Err("expected checked output to fail".into()),
    }
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[test]
fn new_file_is_private_exact_and_published_without_staging() -> TestResult {
    let fixture = Fixture::new()?;
    let output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    let original = fs::metadata(&partial)?;
    let receipt = output.finish(DOCUMENT, true)?;
    assert_eq!(receipt.publication, PublicationState::Published);
    assert_eq!(receipt.partial_name, None);
    assert_eq!(receipt.written_bytes, DOCUMENT.len() as u64);
    assert_eq!(receipt.sha256, digest(DOCUMENT));
    assert_eq!(fs::read(&fixture.target)?, DOCUMENT);
    let final_metadata = fs::metadata(&fixture.target)?;
    assert_eq!(final_metadata.mode() & 0o7777, 0o600);
    assert_eq!(final_metadata.nlink(), 1);
    assert_eq!(final_metadata.uid(), rustix::process::geteuid().as_raw());
    assert_eq!(Identity::of(&final_metadata), Identity::of(&original));
    assert!(!partial.exists());
    assert_eq!(fs::read_dir(&fixture.directory)?.count(), 1);
    Ok(())
}

#[test]
fn cancelled_publication_keeps_a_synced_valid_private_partial() -> TestResult {
    let fixture = Fixture::new()?;
    let receipt = fixture.prepare()?.finish(DOCUMENT, false)?;
    assert_eq!(receipt.publication, PublicationState::Partial);
    let Some(name) = receipt.partial_name else {
        return Err("missing generated partial name".into());
    };
    assert!(name.starts_with(".rustusb-diagnostic-"));
    assert!(name.ends_with(".partial"));
    assert!(!name.contains('/'));
    let path = fixture.directory.join(name);
    let saved = fs::read(&path)?;
    assert_eq!(saved, DOCUMENT);
    assert!(serde_json::from_slice::<serde::de::IgnoredAny>(&saved).is_ok());
    assert_eq!(fs::metadata(path)?.mode() & 0o7777, 0o600);
    assert!(!fixture.target.exists());
    assert_eq!(receipt.sha256, digest(DOCUMENT));
    Ok(())
}

#[test]
fn every_existing_destination_type_is_refused_before_staging() -> TestResult {
    let fixture = Fixture::new()?;
    let original = fixture.directory.join("original");
    fs::write(&original, b"unchanged")?;
    let kinds = ["file", "symlink", "hardlink", "directory", "fifo"];
    for kind in kinds {
        match kind {
            "file" => fs::write(&fixture.target, b"existing")?,
            "symlink" => symlink("absent", &fixture.target)?,
            "hardlink" => fs::hard_link(&original, &fixture.target)?,
            "directory" => fs::create_dir(&fixture.target)?,
            "fifo" => rustix::fs::mknodat(
                rustix::fs::CWD,
                &fixture.target,
                FileType::Fifo,
                Mode::RUSR | Mode::WUSR,
                0,
            )?,
            _ => return Err("unknown fixture kind".into()),
        }
        let before = fs::symlink_metadata(&fixture.target)?;
        let error = failure(fixture.prepare())?;
        assert_eq!(error.kind, FileFailureKind::AlreadyExists);
        assert_eq!(error.publication, PublicationState::NotCreated);
        assert_eq!(error.partial_name, None);
        assert_eq!(error.sha256, None);
        let after = fs::symlink_metadata(&fixture.target)?;
        assert_eq!(Identity::of(&before), Identity::of(&after));
        assert_eq!(fs::read_dir(&fixture.directory)?.count(), 2);
        if kind == "directory" {
            fs::remove_dir(&fixture.target)?;
        } else {
            fs::remove_file(&fixture.target)?;
        }
    }
    assert_eq!(fs::read(original)?, b"unchanged");
    Ok(())
}

#[test]
fn unsafe_ancestors_and_nonprivate_leaf_are_refused_without_chmod() -> TestResult {
    let fixture = Fixture::new()?;
    fs::set_permissions(&fixture.directory, fs::Permissions::from_mode(0o755))?;
    let error = failure(fixture.prepare())?;
    assert_eq!(error.kind, FileFailureKind::Permission);
    assert_eq!(fs::metadata(&fixture.directory)?.mode() & 0o7777, 0o755);
    fs::set_permissions(&fixture.directory, fs::Permissions::from_mode(0o700))?;
    fs::set_permissions(fixture.anchor.path(), fs::Permissions::from_mode(0o777))?;
    let error = failure(fixture.prepare())?;
    assert_eq!(error.kind, FileFailureKind::Permission);
    assert_eq!(fs::metadata(fixture.anchor.path())?.mode() & 0o7777, 0o777);
    fs::set_permissions(fixture.anchor.path(), fs::Permissions::from_mode(0o700))?;
    assert_eq!(fs::read_dir(&fixture.directory)?.count(), 0);
    Ok(())
}

#[test]
fn symlink_parent_and_noncanonical_paths_do_not_create_artifacts() -> TestResult {
    let fixture = Fixture::new()?;
    let linked = fixture.anchor.path().join("linked");
    symlink(&fixture.directory, &linked)?;
    assert!(Output::prepare(&linked.join("bundle.json"), fixture.anchor.path()).is_err());
    for target in [
        PathBuf::from("relative.json"),
        fixture.directory.join("../bundle.json"),
        fixture.directory.join("./bundle.json"),
    ] {
        let error = failure(Output::prepare(&target, fixture.anchor.path()))?;
        assert_eq!(error.kind, FileFailureKind::InvalidInput);
    }
    let trailing = PathBuf::from(format!("{}/", fixture.target.display()));
    assert_eq!(
        failure(Output::prepare(&trailing, fixture.anchor.path()))?.kind,
        FileFailureKind::InvalidInput
    );
    assert_eq!(fs::read_dir(&fixture.directory)?.count(), 0);
    Ok(())
}

#[test]
fn late_destination_creation_is_preserved_and_never_overwritten() -> TestResult {
    let fixture = Fixture::new()?;
    let output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    fs::write(&fixture.target, b"late destination")?;
    let before = fs::metadata(&fixture.target)?;
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::AlreadyExists);
    assert_eq!(error.publication, PublicationState::Partial);
    assert_eq!(error.written_bytes, DOCUMENT.len() as u64);
    assert_eq!(error.sha256, Some(digest(DOCUMENT)));
    assert_eq!(fs::read(&fixture.target)?, b"late destination");
    assert_eq!(
        Identity::of(&before),
        Identity::of(&fs::metadata(&fixture.target)?)
    );
    assert_eq!(fs::read(partial)?, DOCUMENT);
    Ok(())
}

#[test]
fn concurrent_prepared_publishers_have_exactly_one_winner() -> TestResult {
    let fixture = Fixture::new()?;
    let first = fixture.prepare()?;
    let second = fixture.prepare()?;
    let barrier = Arc::new(Barrier::new(2));
    let other_barrier = barrier.clone();
    let first = std::thread::spawn(move || {
        barrier.wait();
        first.finish(DOCUMENT, true)
    });
    let second = std::thread::spawn(move || {
        other_barrier.wait();
        second.finish(DOCUMENT, true)
    });
    let first = first.join().map_err(|_| "first publisher panicked")?;
    let second = second.join().map_err(|_| "second publisher panicked")?;
    assert_eq!(usize::from(first.is_ok()) + usize::from(second.is_ok()), 1);
    let failed = if first.is_err() { first } else { second };
    let error = failure(failed)?;
    assert_eq!(error.kind, FileFailureKind::AlreadyExists);
    assert_eq!(error.publication, PublicationState::Partial);
    assert_eq!(fs::read(&fixture.target)?, DOCUMENT);
    assert_eq!(fs::read_dir(&fixture.directory)?.count(), 2);
    Ok(())
}

#[test]
fn changed_parent_identity_cannot_redirect_writes() -> TestResult {
    let fixture = Fixture::new()?;
    let output = fixture.prepare()?;
    let partial_name = output.partial_name.clone();
    let old = fixture.anchor.path().join("old");
    fs::rename(&fixture.directory, &old)?;
    fs::create_dir(&fixture.directory)?;
    fs::set_permissions(&fixture.directory, fs::Permissions::from_mode(0o700))?;
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::IdentityChanged);
    assert_eq!(error.publication, PublicationState::Unconfirmed);
    assert_eq!(error.written_bytes, 0);
    assert!(fs::read(old.join(partial_name))?.is_empty());
    assert_eq!(fs::read_dir(&fixture.directory)?.count(), 0);
    Ok(())
}

#[test]
fn replaced_or_hardlinked_staging_is_never_mutated_or_removed() -> TestResult {
    let fixture = Fixture::new()?;
    let output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    let other = fixture.directory.join("other");
    fs::write(&other, b"other owner data")?;
    fs::remove_file(&partial)?;
    symlink(&other, &partial)?;
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.publication, PublicationState::Unconfirmed);
    assert_eq!(error.written_bytes, 0);
    assert!(fs::symlink_metadata(&partial)?.file_type().is_symlink());
    assert_eq!(fs::read(&other)?, b"other owner data");
    fs::remove_file(&partial)?;
    let output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    fs::hard_link(&partial, fixture.directory.join("extra-link"))?;
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::Permission);
    assert_eq!(error.publication, PublicationState::Unconfirmed);
    assert_eq!(fs::metadata(&partial)?.nlink(), 2);
    assert!(fs::read(&partial)?.is_empty());
    assert!(!fixture.target.exists());
    Ok(())
}

#[test]
fn changed_file_permissions_are_refused_without_repair() -> TestResult {
    let fixture = Fixture::new()?;
    let output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    fs::set_permissions(&partial, fs::Permissions::from_mode(0o644))?;
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::Permission);
    assert_eq!(error.written_bytes, 0);
    assert_eq!(fs::metadata(partial)?.mode() & 0o7777, 0o644);
    assert!(!fixture.target.exists());
    Ok(())
}

#[test]
fn encoded_cap_is_checked_before_any_write_and_exact_limit_is_accepted() -> TestResult {
    let fixture = Fixture::new()?;
    let output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    let too_large = vec![b' '; crate::MAX_EXPORT_BYTES + 1];
    let error = failure(output.finish(&too_large, true))?;
    assert_eq!(error.kind, FileFailureKind::InvalidInput);
    assert_eq!(error.written_bytes, 0);
    assert!(fs::read(&partial)?.is_empty());
    let mut exact = DOCUMENT.to_vec();
    exact.resize(crate::MAX_EXPORT_BYTES, b' ');
    let receipt = fixture.prepare()?.finish(&exact, true)?;
    assert_eq!(receipt.written_bytes, crate::MAX_EXPORT_BYTES as u64);
    assert_eq!(fs::read(&fixture.target)?, exact);
    Ok(())
}

#[test]
fn an_invalid_json_partial_is_not_reported_as_valid_finished_work() -> TestResult {
    let fixture = Fixture::new()?;
    let output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    let error = failure(output.finish(b"{truncated", false))?;
    assert_eq!(error.kind, FileFailureKind::InvalidInput);
    assert_eq!(error.publication, PublicationState::Partial);
    assert_eq!(error.written_bytes, 0);
    assert!(fs::read(partial)?.is_empty());
    assert!(!fixture.target.exists());
    Ok(())
}

#[test]
fn actual_read_only_descriptor_write_error_preserves_errno_and_empty_partial() -> TestResult {
    let fixture = Fixture::new()?;
    let mut output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    // The real inode/entry remains unchanged; replace only this private test's
    // owned descriptor with a read-only FD so the OS rejects the actual write.
    output.file = File::open(&partial)?;
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::Io);
    assert_eq!(error.errno, Some(rustix::io::Errno::BADF.raw_os_error()));
    assert_eq!(error.publication, PublicationState::Partial);
    assert_eq!(error.written_bytes, 0);
    assert_eq!(error.sha256, Some(digest(b"")));
    assert!(fs::read(partial)?.is_empty());
    assert!(!fixture.target.exists());
    Ok(())
}

#[test]
fn injected_failure_after_real_writes_retains_exact_prefix_and_digest() -> TestResult {
    let fixture = Fixture::new()?;
    let mut output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    // Safe software injection: the prefix goes through File::write; the EIO is
    // injected after16 bytes, not claimed as a real kernel/filesystem fault.
    output.fault = Some(Fault::WriteAfter(16));
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::Io);
    assert_eq!(error.errno, Some(rustix::io::Errno::IO.raw_os_error()));
    assert_eq!(error.publication, PublicationState::Partial);
    assert_eq!(error.written_bytes, 16);
    assert_eq!(error.sha256, Some(digest(&DOCUMENT[..16])));
    assert_eq!(fs::read(partial)?, &DOCUMENT[..16]);
    assert!(!fixture.target.exists());
    Ok(())
}

#[test]
fn injected_file_sync_failure_is_not_a_published_success() -> TestResult {
    let fixture = Fixture::new()?;
    let mut output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    output.fault = Some(Fault::FileSync);
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::Io);
    assert_eq!(error.publication, PublicationState::Partial);
    assert_eq!(error.written_bytes, DOCUMENT.len() as u64);
    assert_eq!(fs::read(partial)?, DOCUMENT);
    assert!(!fixture.target.exists());
    Ok(())
}

#[test]
fn injected_post_rename_sync_failure_keeps_visible_final_unconfirmed() -> TestResult {
    let fixture = Fixture::new()?;
    let mut output = fixture.prepare()?;
    let partial = fixture.directory.join(&output.partial_name);
    output.fault = Some(Fault::DirectorySync);
    let error = failure(output.finish(DOCUMENT, true))?;
    assert_eq!(error.kind, FileFailureKind::Io);
    assert_eq!(error.publication, PublicationState::Unconfirmed);
    assert_eq!(error.partial_name, None);
    assert_eq!(error.written_bytes, DOCUMENT.len() as u64);
    assert_eq!(error.sha256, Some(digest(DOCUMENT)));
    assert!(!partial.exists());
    assert_eq!(fs::read(&fixture.target)?, DOCUMENT);
    Ok(())
}

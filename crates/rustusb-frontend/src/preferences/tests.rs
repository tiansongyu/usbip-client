use super::*;
use std::os::unix::fs::{PermissionsExt, symlink};
fn fixture() -> Result<(tempfile::TempDir, Settings)> {
    let root = tempfile::tempdir()?;
    std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700))?;
    let settings = Settings::load(root.path().join("config/rustusb"))?;
    Ok((root, settings))
}
#[test]
fn missing_load_is_read_only_and_atomic_roundtrip_retains_only_user_options() -> Result<()> {
    let (_root, mut settings) = fixture()?;
    assert!(!settings.directory.exists());
    assert!(!settings.has_saved());
    assert_eq!(settings.options(), Options::default());
    let options = Options {
        refresh_seconds: 13,
        auto_refresh: false,
        monochrome: true,
    };
    settings.save(options)?;
    assert_eq!(
        std::fs::metadata(&settings.directory)?.mode() & 0o7777,
        0o700
    );
    assert_eq!(
        std::fs::metadata(settings.directory.join(NAME))?.mode() & 0o7777,
        0o600
    );
    assert_eq!(
        Settings::load(settings.directory.clone())?.options(),
        options
    );
    settings.save(Options::default())?;
    assert_eq!(
        Settings::load(settings.directory.clone())?.options(),
        Options::default()
    );
    assert_eq!(std::fs::read_dir(&settings.directory)?.count(), 2);
    Ok(())
}
#[test]
fn strict_schema_bounds_and_unknown_files_are_never_overwritten() -> Result<()> {
    let (_root, mut settings) = fixture()?;
    settings.save(Options::default())?;
    let path = settings.directory.join(NAME);
    let bad=[b"".to_vec(),vec![b' ';4097],b"null".to_vec(),br#"{"schema_version":2,"options":{"refresh_seconds":2,"auto_refresh":true,"monochrome":false}}"#.to_vec(),br#"{"schema_version":1,"options":{"refresh_seconds":1,"auto_refresh":true,"monochrome":false}}"#.to_vec(),br#"{"schema_version":1,"options":{"refresh_seconds":2,"auto_refresh":true,"monochrome":false,"endpoint":"malicious"}}"#.to_vec(),br#"{"schema_version":1,"options":{"refresh_seconds":2,"auto_refresh":true,"monochrome":false},"credential":"not allowed"}"#.to_vec()];
    for bytes in bad {
        std::fs::write(&path, &bytes)?;
        assert!(Settings::load(settings.directory.clone()).is_err());
        assert!(settings.save(Options::default()).is_err());
        assert_eq!(std::fs::read(&path)?, bytes);
    }
    Ok(())
}
#[test]
fn links_modes_and_ancestor_redirection_are_refused() -> Result<()> {
    let (root, mut settings) = fixture()?;
    settings.save(Options::default())?;
    let path = settings.directory.join(NAME);
    let original = std::fs::read(&path)?;
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644))?;
    assert!(Settings::load(settings.directory.clone()).is_err());
    assert!(settings.save(Options::default()).is_err());
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))?;
    let other = root.path().join("other");
    std::fs::hard_link(&path, &other)?;
    assert!(Settings::load(settings.directory.clone()).is_err());
    assert!(settings.save(Options::default()).is_err());
    std::fs::remove_file(&other)?;
    std::fs::rename(&path, &other)?;
    symlink(&other, &path)?;
    assert!(Settings::load(settings.directory.clone()).is_err());
    assert!(settings.save(Options::default()).is_err());
    assert_eq!(std::fs::read(&other)?, original);
    std::fs::remove_file(&path)?;
    std::fs::rename(&other, &path)?;
    let alias = root.path().join("alias");
    symlink(&settings.directory, &alias)?;
    assert!(Settings::load(alias).is_err());
    std::fs::set_permissions(&settings.directory, std::fs::Permissions::from_mode(0o755))?;
    assert!(Settings::load(settings.directory.clone()).is_err());
    assert!(Settings::load(PathBuf::from("relative/path")).is_err());
    assert!(Settings::load(root.path().join("../other")).is_err());
    Ok(())
}
#[test]
fn cooperative_save_lock_uses_a_fresh_open_description() -> Result<()> {
    let (_root, mut settings) = fixture()?;
    settings.save(Options::default())?;
    let dir = Directory::open(&settings.directory, false)?
        .ok_or_else(|| Error::State("fixture absent".into()))?;
    let lock = dir.lock()?;
    let mut second = settings.clone();
    assert!(
        second
            .save(Options {
                refresh_seconds: 4,
                ..Options::default()
            })
            .is_err()
    );
    assert_eq!(
        Settings::load(settings.directory.clone())?.options(),
        Options::default()
    );
    drop(lock);
    second.save(Options {
        refresh_seconds: 4,
        ..Options::default()
    })?;
    assert_eq!(
        Settings::load(settings.directory.clone())?
            .options()
            .refresh_seconds,
        4
    );
    Ok(())
}
#[test]
fn concurrent_replacement_before_publish_is_retained_and_own_temp_is_removed() -> Result<()> {
    let (_root, mut settings) = fixture()?;
    settings.save(Options::default())?;
    let directory = settings.directory.clone();
    let path = directory.join(NAME);
    let replacement = Document {
        schema_version: 1,
        options: Options {
            refresh_seconds: 11,
            ..Options::default()
        },
    };
    let bytes =
        serde_json::to_vec(&replacement).map_err(|_| Error::State("fixture JSON".into()))?;
    let expected = bytes.clone();
    let result = settings.save_with(
        Options {
            refresh_seconds: 5,
            ..Options::default()
        },
        move || {
            let alternate = directory.join("manual.json");
            std::fs::write(&alternate, bytes)?;
            std::fs::set_permissions(&alternate, std::fs::Permissions::from_mode(0o600))?;
            std::fs::rename(alternate, &path)?;
            Ok(())
        },
    );
    assert!(result.is_err());
    assert_eq!(std::fs::read(settings.directory.join(NAME))?, expected);
    assert_eq!(std::fs::read_dir(&settings.directory)?.count(), 2);
    Ok(())
}
#[test]
fn xdg_selection_ignores_relative_values_and_never_invents_a_home() {
    assert_eq!(
        directory_from(Some("/xdg".into()), Some("/home/user".into())),
        Some(PathBuf::from("/xdg/rustusb"))
    );
    assert_eq!(
        directory_from(Some("relative".into()), Some("/home/user".into())),
        Some(PathBuf::from("/home/user/.config/rustusb"))
    );
    assert_eq!(
        directory_from(Some("".into()), Some("/home/user".into())),
        Some(PathBuf::from("/home/user/.config/rustusb"))
    );
    assert!(directory_from(None, None).is_none());
    assert!(directory_from(None, Some("relative".into())).is_none());
}

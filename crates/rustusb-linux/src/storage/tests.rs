use super::*;
use std::os::unix::fs::symlink;

pub(super) fn block(name: &str, device: DeviceNumber) -> BlockNode {
    BlockNode {
        name: name.into(),
        device,
        sysfs_inode: 1,
        disk_sequence: None,
        partition: None,
        sectors: Some(128),
        kernel_read_only: Some(false),
        dm_hint: None,
        holders: vec![],
        slaves: vec![],
    }
}
fn write(path: &Path, value: &str) -> Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| Error::State("fixture parent".into()))?;
    fs::create_dir_all(parent)?;
    fs::write(path, value)?;
    Ok(())
}
fn block_tree(path: &Path, number: &str, partition: Option<u32>) -> Result<()> {
    write(&path.join("dev"), number)?;
    write(&path.join("size"), "131072\n")?;
    write(&path.join("ro"), "0\n")?;
    fs::create_dir_all(path.join("holders"))?;
    fs::create_dir_all(path.join("slaves"))?;
    if let Some(p) = partition {
        write(&path.join("partition"), &p.to_string())?;
    }
    Ok(())
}
#[test]
fn finds_second_lun_partitions_and_crypt_holder_without_following_general_links() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let root = temp.path().join("devices");
    let usb = root.join("usb1/1-1");
    let lun0 = usb.join("1-1:1.0/host0/target0:0:0/0:0:0:0/block/sda");
    let lun1 = usb.join("1-1:1.0/host0/target0:0:0/0:0:0:1/block/sdb");
    let dm = root.join("virtual/block/dm-0");
    block_tree(&lun0, "8:0", None)?;
    block_tree(&lun1, "8:16", None)?;
    block_tree(&lun1.join("sdb1"), "8:17", Some(1))?;
    block_tree(&dm, "253:0", None)?;
    write(&dm.join("dm/uuid"), "CRYPT-LUKS2-private-secret-label")?;
    symlink(&dm, lun1.join("sdb1/holders/dm-0"))?;
    symlink(lun1.join("sdb1"), dm.join("slaves/sdb1"))?;
    symlink(&usb, dm.join("driver"))?;
    let mut budget = Budget::new();
    let found = inventory(&usb, &root, &mut budget);
    assert_eq!(found.luns.len(), 2);
    assert_eq!(found.blocks.len(), 4);
    assert_eq!(
        found.blocks[&DeviceNumber {
            major: 253,
            minor: 0
        }]
            .dm_hint,
        Some("crypt")
    );
    assert_eq!(
        found.blocks[&DeviceNumber {
            major: 8,
            minor: 17
        }]
            .holders,
        vec![DeviceNumber {
            major: 253,
            minor: 0
        }]
    );
    assert!(budget.issues.is_empty(), "{:?}", budget.issues);
    Ok(())
}
#[test]
fn unreadable_outside_holder_and_invalid_ro_remain_unknown() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let root = temp.path().join("devices");
    let usb = root.join("1-1");
    let disk = usb.join("0:0:0:0/block/sda");
    block_tree(&disk, "8:0", None)?;
    write(&disk.join("ro"), "2")?;
    let outside = temp.path().join("outside");
    block_tree(&outside, "253:0", None)?;
    symlink(outside, disk.join("holders/dm-0"))?;
    let mut budget = Budget::new();
    let found = inventory(&usb, &root, &mut budget);
    assert_eq!(found.blocks.len(), 1);
    assert_eq!(
        found.blocks[&DeviceNumber { major: 8, minor: 0 }].kernel_read_only,
        None
    );
    assert!(budget.issues.contains("invalid_block_read_only"));
    assert!(
        budget
            .issues
            .contains("block_link_unresolved_or_outside_sysfs")
    );
    Ok(())
}
#[test]
fn second_thread_mount_and_mapping_failures_are_not_treated_as_empty() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let root = temp.path().join("devices");
    let usb = root.join("1-1");
    write(&usb.join("devnum"), "2")?;
    block_tree(&usb.join("0:0:0:1/block/sdb"), "8:16", None)?;
    let proc = temp.path().join("proc");
    write(&proc.join("swaps"), "Filename Type Size Used Priority\n")?;
    for tid in [123, 124] {
        let task = proc.join(format!("123/task/{tid}"));
        write(
            &task.join("stat"),
            &format!("{tid} (a\nb)) S {} 9876 0", vec!["0"; 18].join(" ")),
        )?;
        write(&task.join("ns/mnt"), "namespace fixture")?;
        fs::create_dir_all(task.join("root"))?;
        fs::create_dir_all(task.join("fd"))?;
        let direct = proc.join(tid.to_string());
        fs::create_dir_all(direct.join("map_files"))?;
        fs::copy(task.join("stat"), direct.join("stat"))?;
        if tid == 124 {
            write(
                &task.join("mountinfo"),
                "12 1 8:16 / /mnt rw - ext4 /dev/sdb rw\n",
            )?;
            symlink(
                temp.path().join("absent"),
                direct.join("map_files/1234-5678"),
            )?;
        } else {
            write(
                &task.join("mountinfo"),
                "12 1 0:4 / / rw - tmpfs tmpfs rw\n",
            )?;
        }
    }
    let report = inspect_paths("test-fingerprint", "1-1", &usb, &usb, &root, &proc)?;
    assert_eq!(report.tasks_observed, 2);
    assert!(report.busy_observed);
    assert!(
        report
            .uses
            .iter()
            .any(|u| u.kind == "mount" && u.tid == Some(124))
    );
    assert!(report.issues.contains("mapping_target_unreadable_or_gone"));
    assert!(report.limitations.contains(&"not_an_export_permission"));
    Ok(())
}
#[test]
fn budgets_preserve_positive_uses_and_bound_text() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let path = temp.path().join("large");
    fs::write(&path, vec![b'x'; 129])?;
    let mut budget = Budget::new();
    let mut uses = BTreeSet::new();
    add_use(
        &mut uses,
        standalone_use("holder", DeviceNumber { major: 8, minor: 0 }),
        &mut budget,
    );
    assert!(budget.text(&path, 128).is_none());
    assert_eq!(uses.len(), 1);
    assert!(budget.issues.contains("text_file_or_total_limit"));
    budget.entries = MAX_ENTRIES;
    assert!(!budget.available());
    assert_eq!(uses.len(), 1);
    assert!(budget.issues.contains("entry_budget"));
    Ok(())
}
#[test]
fn numeric_inputs_reject_signs_overflow_and_extra_components() {
    for bad in ["8:-1", "+8:1", "4294967296:1", "8:1:2", "8:"] {
        assert!(DeviceNumber::parse(bad).is_none());
    }
}

#[test]
fn replacement_original_path_is_reported_without_discarding_inventory() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let root = temp.path().join("devices");
    let pinned = root.join("old-generation");
    let replacement = root.join("1-1");
    write(&pinned.join("devnum"), "2")?;
    write(&replacement.join("devnum"), "3")?;
    block_tree(&pinned.join("0:0:0:0/block/sda"), "8:0", None)?;
    let proc = temp.path().join("proc");
    write(&proc.join("swaps"), "Filename Type Size Used Priority\n")?;
    let report = inspect_paths("fixture", "1-1", &pinned, &replacement, &root, &proc)?;
    assert!(report.issues.contains("usb_generation_changed_or_gone"));
    assert_eq!(report.blocks.len(), 1);
    assert_eq!(report.usb_address, 2);
    Ok(())
}

#[test]
fn holder_cycle_terminates_and_inventory_caps_keep_partial_data() -> Result<()> {
    let temp = tempfile::tempdir()?;
    let root = temp.path().join("devices");
    let usb = root.join("1-1");
    let first = usb.join("0:0:0:0/block/sda");
    let second = root.join("virtual/block/dm-0");
    block_tree(&first, "8:0", None)?;
    block_tree(&second, "253:0", None)?;
    symlink(&second, first.join("holders/dm-0"))?;
    symlink(&first, second.join("holders/sda"))?;
    let mut budget = Budget::new();
    let found = inventory(&usb, &root, &mut budget);
    assert_eq!(found.blocks.len(), 2);
    for n in 1..=MAX_BLOCKS {
        block_tree(
            &usb.join(format!("0:0:0:{n}/block/sd{n}")),
            &format!("9:{n}"),
            None,
        )?;
    }
    let mut budget = Budget::new();
    let found = inventory(&usb, &root, &mut budget);
    assert_eq!(found.blocks.len(), MAX_BLOCKS);
    assert!(budget.issues.contains("block_limit"));
    assert!(budget.issues.contains("lun_limit"));
    Ok(())
}

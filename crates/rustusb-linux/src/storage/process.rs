use super::*;

fn task_start(value: &str, tid: u32) -> Option<u64> {
    // comm may contain spaces, newlines and ')'; the last ')' closes it.
    let (prefix, fields) = value.rsplit_once(')')?;
    let (pid, _) = prefix.split_once(" (")?;
    if decimal::<u32>(pid)? != tid {
        return None;
    }
    decimal(fields.split_whitespace().nth(19)?)
}

fn mount_devices(text: &str) -> std::result::Result<BTreeSet<DeviceNumber>, ()> {
    let mut result = BTreeSet::new();
    if text.is_empty() {
        return Err(());
    }
    for (index, row) in text.lines().enumerate() {
        if index >= 4096 {
            return Err(());
        }
        let fields: Vec<_> = row.split_whitespace().collect();
        let Some(separator) = fields.iter().position(|field| *field == "-") else {
            return Err(());
        };
        if separator < 6
            || fields.len() != separator + 4
            || decimal::<u64>(fields[0]).is_none()
            || decimal::<u64>(fields[1]).is_none()
            || !fields[4].starts_with('/')
        {
            return Err(());
        }
        let Some(number) = DeviceNumber::parse(fields[2]) else {
            return Err(());
        };
        result.insert(number);
    }
    Ok(result)
}

fn target_usage(
    metadata: &fs::Metadata,
    inventory: &Inventory,
) -> Option<(&'static str, DeviceNumber)> {
    classify(
        metadata.file_type().is_block_device(),
        metadata.file_type().is_char_device(),
        metadata.rdev(),
        metadata.dev(),
        inventory,
    )
}
fn classify(
    block: bool,
    character: bool,
    rdev: u64,
    dev: u64,
    inventory: &Inventory,
) -> Option<(&'static str, DeviceNumber)> {
    let number = DeviceNumber::from_raw(if block || character { rdev } else { dev });
    if block && inventory.blocks.contains_key(&number) {
        Some(("block_fd", number))
    } else if character && inventory.characters.contains(&number) {
        Some(("character_fd", number))
    } else if !block && !character && inventory.blocks.contains_key(&number) {
        Some(("filesystem_fd", number))
    } else {
        None
    }
}

fn mapping_name(path: &Path) -> bool {
    path.file_name()
        .and_then(|n| n.to_str())
        .and_then(|name| name.split_once('-'))
        .is_some_and(|(a, b)| {
            !a.is_empty()
                && !b.is_empty()
                && a.len() <= 16
                && b.len() <= 16
                && a.bytes().chain(b.bytes()).all(|c| c.is_ascii_hexdigit())
        })
}

fn swap_path(row: &str) -> Option<PathBuf> {
    let fields: Vec<_> = row.split_whitespace().collect();
    if fields.len() != 5
        || !matches!(fields[1], "file" | "partition")
        || decimal::<u64>(fields[2]).is_none()
        || decimal::<u64>(fields[3]).is_none()
        || fields[4].parse::<i32>().is_err()
    {
        return None;
    }
    let mut bytes = Vec::new();
    let mut chars = fields[0].as_bytes().iter().copied();
    while let Some(c) = chars.next() {
        if c == b'\\' {
            let escape = [chars.next()?, chars.next()?, chars.next()?];
            bytes.push(match &escape {
                b"040" => b' ',
                b"011" => b'\t',
                b"012" => b'\n',
                b"134" => b'\\',
                _ => return None,
            });
        } else {
            bytes.push(c);
        }
    }
    use std::os::unix::ffi::OsStringExt;
    let path = PathBuf::from(std::ffi::OsString::from_vec(bytes));
    (path.is_absolute() && !path.as_os_str().as_encoded_bytes().contains(&0)).then_some(path)
}

pub(super) fn scan(
    proc: &Path,
    inventory: &Inventory,
    uses: &mut BTreeSet<StorageUse>,
    budget: &mut Budget,
) -> usize {
    let mut tasks = 0;
    let mut mount_views = BTreeSet::new();
    for process in budget.entries(proc) {
        let Some(pid) = process
            .file_name()
            .and_then(|n| n.to_str())
            .and_then(decimal::<u32>)
        else {
            continue;
        };
        for thread in budget.entries(&process.join("task")) {
            if !budget.available() {
                break;
            }
            let Some(tid) = thread
                .file_name()
                .and_then(|n| n.to_str())
                .and_then(decimal::<u32>)
            else {
                continue;
            };
            if tasks >= MAX_TASKS {
                budget.issue("task_limit");
                return tasks;
            }
            tasks += 1;
            let start = budget
                .text(&thread.join("stat"), 4096)
                .and_then(|s| task_start(&s, tid));
            let Some(start) = start else {
                budget.issue("task_identity_unreadable_or_invalid");
                continue;
            };
            let usage = |kind, device| StorageUse {
                kind,
                device,
                pid: Some(pid),
                tid: Some(tid),
                start_ticks: Some(start),
            };
            let view = (
                identity(&thread.join("ns/mnt")),
                identity(&thread.join("root")),
            );
            if let (Some(namespace), Some(root)) = view {
                if mount_views.insert((namespace, root))
                    && let Some(text) = budget.text(&thread.join("mountinfo"), MAX_FILE)
                {
                    if text.is_empty() {
                        budget.issue("mountinfo_invalid_or_limit");
                    }
                    for (index, row) in text.lines().enumerate() {
                        if index >= 4096 {
                            budget.issue("mountinfo_invalid_or_limit");
                            break;
                        }
                        match mount_devices(row) {
                            Ok(devices) => {
                                for device in devices {
                                    if inventory.blocks.contains_key(&device) {
                                        add_use(uses, usage("mount", device), budget);
                                    }
                                }
                            }
                            Err(()) => budget.issue("mountinfo_invalid_or_limit"),
                        }
                    }
                }
            } else {
                budget.issue("mount_view_unreadable_or_gone");
            }
            // map_files is absent from the nested task/TID inode table. The
            // top-level numeric lookup also accepts TIDs, including non-leaders.
            let direct = proc.join(tid.to_string());
            let direct_start = budget
                .text(&direct.join("stat"), 4096)
                .and_then(|s| task_start(&s, tid));
            for (directory, mapping) in [("fd", false), ("map_files", true)] {
                if mapping && direct_start != Some(start) {
                    budget.issue("mapping_task_identity_changed_or_unreadable");
                    continue;
                }
                let directory = if mapping {
                    direct.join(directory)
                } else {
                    thread.join(directory)
                };
                for path in budget.entries(&directory) {
                    if !budget.available() {
                        break;
                    }
                    let valid = if mapping {
                        mapping_name(&path)
                    } else {
                        path.file_name()
                            .and_then(|n| n.to_str())
                            .and_then(decimal::<u32>)
                            .is_some()
                    };
                    if !valid {
                        budget.issue("invalid_proc_entry");
                        continue;
                    }
                    match fs::metadata(path) {
                        Ok(metadata) => {
                            if let Some((kind, device)) = target_usage(&metadata, inventory) {
                                add_use(
                                    uses,
                                    usage(if mapping { "mapping" } else { kind }, device),
                                    budget,
                                );
                            }
                        }
                        Err(_) => budget.issue(if mapping {
                            "mapping_target_unreadable_or_gone"
                        } else {
                            "fd_target_unreadable_or_gone"
                        }),
                    }
                }
            }
            if budget
                .text(&direct.join("stat"), 4096)
                .and_then(|s| task_start(&s, tid))
                != Some(start)
            {
                budget.issue("mapping_task_identity_changed_or_unreadable");
            }
            // cwd/root and executable references can survive without an FD entry.
            for field in ["cwd", "root", "exe"] {
                match fs::metadata(thread.join(field)) {
                    Ok(metadata) => {
                        if let Some((_, device)) = target_usage(&metadata, inventory) {
                            add_use(uses, usage("filesystem_reference", device), budget);
                        }
                    }
                    Err(_) => budget.issue("task_reference_unreadable_or_gone"),
                }
            }
            let end = budget
                .text(&thread.join("stat"), 4096)
                .and_then(|s| task_start(&s, tid));
            if end != Some(start)
                || view
                    != (
                        identity(&thread.join("ns/mnt")),
                        identity(&thread.join("root")),
                    )
            {
                budget.issue("task_identity_or_view_changed");
            }
        }
        if !budget.available() {
            break;
        }
    }
    if let Some(text) = budget.text(&proc.join("swaps"), MAX_FILE) {
        let mut rows = text.lines();
        if rows
            .next()
            .map(|line| line.split_whitespace().collect::<Vec<_>>())
            != Some(vec!["Filename", "Type", "Size", "Used", "Priority"])
        {
            budget.issue("invalid_swap_header");
        } else {
            for (index, row) in rows.enumerate() {
                if index >= 256 || !budget.available() {
                    budget.issue("swap_limit");
                    break;
                }
                let Some(path) = swap_path(row) else {
                    budget.issue("invalid_swap_entry");
                    continue;
                };
                match fs::metadata(path) {
                    Ok(metadata) => {
                        if let Some((_, device)) = target_usage(&metadata, inventory) {
                            add_use(uses, standalone_use("swap", device), budget);
                        }
                    }
                    Err(_) => budget.issue("swap_target_unresolved_in_observer_view"),
                }
            }
        }
    }
    tasks
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn task_stat_accepts_comm_newlines_and_parentheses_but_checks_tid() {
        let stat = format!("123 (a )\nb)) S {} 9876 0\n", vec!["0"; 18].join(" "));
        assert_eq!(task_start(&stat, 123), Some(9876));
        assert_eq!(task_start(&stat, 124), None);
        assert_eq!(task_start("123 (bad) S 0", 123), None);
    }
    #[test]
    fn mountinfo_matches_numeric_filesystem_and_rejects_truncation() {
        let text = "12 1 8:17 /dir\\040with\\040spaces /mnt rw shared:1 - ext4 /dev/sdb1 rw\n";
        assert_eq!(
            mount_devices(text),
            Ok(BTreeSet::from([DeviceNumber {
                major: 8,
                minor: 17
            }]))
        );
        assert_eq!(
            mount_devices("12 1 0:4 net:[4026531992] /run/netns/a rw - nsfs nsfs rw"),
            Ok(BTreeSet::from([DeviceNumber { major: 0, minor: 4 }]))
        );
        for bad in [
            "",
            "12 1 8:1 / / rw - ext4",
            "12 1 8:-1 / / rw - ext4 /dev/x rw",
            "12 1 8:1 / / rw ext4 /dev/x rw",
        ] {
            assert!(mount_devices(bad).is_err());
        }
    }
    #[test]
    fn swap_paths_decode_kernel_escapes_and_reject_relative_or_bad_rows() {
        assert_eq!(
            swap_path("/a\\040b file 100 0 -2"),
            Some(PathBuf::from("/a b"))
        );
        for bad in [
            "relative file 100 0 -2",
            "/a\\077 file 1 0 -2",
            "/a partition -1 0 -2",
            "/a file 1 0",
        ] {
            assert_eq!(swap_path(bad), None);
        }
    }
    #[test]
    fn bare_block_and_character_targets_use_rdev_not_filesystem_dev() {
        let mut inventory = Inventory::default();
        let block = DeviceNumber {
            major: 8,
            minor: 17,
        };
        inventory
            .blocks
            .insert(block, super::super::tests::block("sdb1", block));
        inventory.characters.insert(DeviceNumber {
            major: 189,
            minor: 1,
        });
        assert_eq!(
            classify(
                true,
                false,
                rustix::fs::makedev(8, 17),
                rustix::fs::makedev(0, 5),
                &inventory
            ),
            Some(("block_fd", block))
        );
        assert_eq!(
            classify(false, false, 0, rustix::fs::makedev(8, 17), &inventory),
            Some(("filesystem_fd", block))
        );
        assert!(
            classify(
                true,
                false,
                rustix::fs::makedev(8, 18),
                rustix::fs::makedev(8, 17),
                &inventory
            )
            .is_none()
        );
        assert_eq!(
            classify(false, true, rustix::fs::makedev(189, 1), 0, &inventory).map(|r| r.0),
            Some("character_fd")
        );
    }
}

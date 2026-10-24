use super::*;

fn empty(after: Option<&Cursor>) -> LogPage {
    LogPage {
        schema_version: 1,
        records: Vec::new(),
        next_after: None,
        oldest_available: None,
        gap: after.is_some(),
        partial_tail: false,
        scanned_bytes: 0,
        more: false,
        status: None,
    }
}

fn validate_request(filter: &ReadFilter, limit: u8) -> Result<()> {
    if !(1..=MAX_PAGE_RECORDS).contains(&limit) {
        return Err(Error::InvalidInput("log page limit"));
    }
    if let ReadFilter::Audit {
        profile,
        actor_uid,
        root_admin,
    } = filter
    {
        if profile.is_empty()
            || profile.len() > 64
            || !profile
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"_-.".contains(&b))
            || (*root_admin && *actor_uid != 0)
        {
            return Err(Error::InvalidInput("authorized log filter"));
        }
    }
    Ok(())
}

fn visible(record: &LogRecord, filter: &ReadFilter) -> bool {
    match filter {
        ReadFilter::All => true,
        ReadFilter::Audit {
            profile,
            actor_uid,
            root_admin,
        } => match &record.event {
            Event::Audit(audit) => match &audit.profile {
                Some(actual) => actual == profile,
                None => {
                    *root_admin
                        || (audit.actor_uid == *actor_uid && audit.action == AuditAction::Denied)
                }
            },
            _ => false,
        },
    }
}

pub(crate) fn read_source(
    source: &Source,
    filter: &ReadFilter,
    after: Option<&Cursor>,
    limit: u8,
) -> Result<LogPage> {
    validate_request(filter, limit)?;
    #[cfg(test)]
    let directory = match &source.anchor {
        Some(anchor) => Directory::open_from(&source.path, false, anchor, Some(source.uid))?,
        None => Directory::open(&source.path, false)?,
    };
    #[cfg(not(test))]
    let directory = Directory::open(&source.path, false)?;
    if directory.identity != source.directory || directory.uid != source.uid {
        return Err(Error::Permission("reader directory identity changed"));
    }
    read_locked(&directory, source.access, filter, after, limit)
}

/// Read a finite snapshot from an existing private log directory without creating
/// locks, enabling logging, contacting the helper, or changing stored records.
/// The caller must run this synchronous filesystem operation in an owned worker.
pub fn read_directory(path: &Path, after: Option<Cursor>, limit: u8) -> Result<LogPage> {
    validate_request(&ReadFilter::All, limit)?;
    let directory = Directory::open(path, false)?;
    read_directory_open(&directory, after.as_ref(), limit)
}

fn read_directory_open(
    directory: &Directory,
    after: Option<&Cursor>,
    limit: u8,
) -> Result<LogPage> {
    let names = directory.names()?;
    if names.is_empty() {
        return Ok(empty(after));
    }
    let Some(access) = directory.identity(ACCESS)? else {
        return Err(Error::Corrupt("log history lacks access lock"));
    };
    if !names.contains(WRITER) {
        return Err(Error::Corrupt("log history lacks writer lock"));
    }
    read_locked(directory, access, &ReadFilter::All, after, limit)
}

fn read_locked(
    directory: &Directory,
    access: Identity,
    filter: &ReadFilter,
    after: Option<&Cursor>,
    limit: u8,
) -> Result<LogPage> {
    let _access = directory.access(access, false)?;
    let segments = inventory(directory)?;
    let Some(oldest) = segments.last() else {
        return Ok(empty(after));
    };
    let mut page = empty(None);
    page.oldest_available = Some(oldest.cursor(oldest.header_bytes)?);
    let chronological: Vec<_> = segments.iter().rev().collect();
    let (start, offset) = match after {
        Some(cursor) => match chronological
            .iter()
            .position(|s| s.header.segment == cursor.segment)
        {
            Some(index) => (index, cursor.offset),
            None => {
                page.gap = true;
                (0, oldest.header_bytes)
            }
        },
        None => (0, oldest.header_bytes),
    };
    let mut previous: Option<(LogId, u64)> = None;
    for (index, segment) in chronological.iter().enumerate().skip(start) {
        let at = if index == start {
            offset
        } else {
            segment.header_bytes
        };
        let mut file = directory.open_file(&slot_name(segment.slot), false, false)?;
        if Stamp::of(&file.metadata()?) != segment.stamp {
            return Err(Error::Permission("snapshot segment changed"));
        }
        if at < segment.header_bytes || at > segment.stamp.bytes {
            return Err(Error::InvalidInput(
                "cursor is outside complete record boundaries",
            ));
        }
        if at != segment.header_bytes {
            file.seek(SeekFrom::Start(at - 1))?;
            let mut previous_byte = [0];
            file.read_exact(&mut previous_byte)?;
            if previous_byte[0] != b'\n' {
                return Err(Error::InvalidInput(
                    "cursor is not a complete line boundary",
                ));
            }
        }
        file.seek(SeekFrom::Start(at))?;
        let mut reader = BufReader::new(file);
        let mut position = at;
        page.next_after = Some(segment.cursor(position)?);
        loop {
            // Reserve one full maximum line rather than reading past the scan
            // ceiling and pretending those discarded bytes were not inspected.
            if page.records.len() >= usize::from(limit)
                || page.scanned_bytes as usize + MAX_RECORD_BYTES > MAX_SCAN_BYTES
            {
                page.more = position < segment.stamp.bytes || index + 1 < chronological.len();
                verify_snapshot(directory, &segments)?;
                return Ok(page);
            }
            let line = bounded_line(&mut reader, MAX_RECORD_BYTES)?;
            if line.is_empty() {
                break;
            }
            page.scanned_bytes += line.len() as u32;
            if !line.ends_with(b"\n") {
                if line.len() == MAX_RECORD_BYTES {
                    return Err(Error::Corrupt(
                        "incomplete record exceeds permitted payload",
                    ));
                }
                page.gap = true;
                page.partial_tail = true;
                // The final fragment is deliberately left on disk. Moving to
                // the next sealed segment provides progress without inventing
                // a valid cursor into the incomplete fragment.
                break;
            }
            let record = decoded(&line, &segment.header)?;
            if previous.as_ref().is_some_and(|(epoch, sequence)| {
                epoch == &record.epoch && record.sequence <= *sequence
            }) {
                return Err(Error::Corrupt("snapshot record sequence is not increasing"));
            }
            previous = Some((record.epoch.clone(), record.sequence));
            position = position
                .checked_add(line.len() as u64)
                .ok_or(Error::Corrupt("record offset overflow"))?;
            page.next_after = Some(segment.cursor(position)?);
            if visible(&record, filter) {
                page.records.push(record);
            }
        }
        if Stamp::of(&reader.get_ref().metadata()?) != segment.stamp {
            return Err(Error::Permission("snapshot segment changed during read"));
        }
    }
    verify_snapshot(directory, &segments)?;
    Ok(page)
}

fn verify_snapshot(directory: &Directory, expected: &[Segment]) -> Result<()> {
    if inventory(directory)? != expected {
        return Err(Error::Permission("log snapshot changed during read"));
    }
    Ok(())
}

#[cfg(test)]
pub(super) fn fixture_read(
    directory: &Directory,
    after: Option<&Cursor>,
    limit: u8,
) -> Result<LogPage> {
    validate_request(&ReadFilter::All, limit)?;
    read_directory_open(directory, after, limit)
}

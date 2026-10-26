//! Separate bounded controller policy history; no USB resource journal reuse.
use super::*;
use crate::native_state::{
    identity_at, open_directory, open_file, validate_directory, validate_file,
};
use rustix::fs::{AtFlags, FlockOperation};
use std::{
    fs::File,
    io::{Read, Write},
    os::unix::fs::MetadataExt,
};
const LIMIT: usize = 64 * 1024;
const DATA: &str = "controllers.json";
const LOCK: &str = "lock";
const TEMP: &str = ".controllers.tmp";

pub(super) struct Store {
    directory: File,
    lock: File,
    uid: u32,
    identity: Option<(u64, u64)>,
    bytes: Vec<u8>,
    disk: Disk,
    poisoned: bool,
    #[cfg(test)]
    fail_stage: u8,
}
impl Store {
    pub(super) fn open(path: &Path) -> Result<Self> {
        Self::open_anchored(path, Path::new("/"), 0)
    }
    pub(super) fn open_anchored(path: &Path, anchor: &Path, uid: u32) -> Result<Self> {
        let (directory, created) = open_directory(path, anchor, uid)?;
        if directory.metadata()?.mode() & 0o7777 != 0o700 {
            return Err(Error::Permission(
                "controller state directory requires root0700".into(),
            ));
        }
        let prior_lock = identity_at(&directory, LOCK, uid)?;
        if !created && prior_lock.is_none() {
            return Err(Error::State(
                "existing controller state lost its lock; refusing initialization".into(),
            ));
        }
        let lock = open_file(&directory, LOCK, uid, prior_lock.is_none())?;
        rustix::fs::flock(&lock, FlockOperation::NonBlockingLockExclusive)
            .map_err(std::io::Error::from)?;
        lock.sync_all()?;
        directory.sync_all()?;
        let identity = identity_at(&directory, DATA, uid)?;
        if !created && identity.is_none() {
            return Err(Error::State(
                "existing controller history is missing; refusing reset".into(),
            ));
        }
        let mut s = Self {
            directory,
            lock,
            uid,
            identity,
            bytes: Vec::new(),
            disk: Disk::empty(),
            poisoned: false,
            #[cfg(test)]
            fail_stage: 0,
        };
        if identity.is_some() {
            s.bytes = s.read_bytes()?;
            s.disk = serde_json::from_slice(&s.bytes)
                .map_err(|_| Error::State("malformed controller history".into()))?;
            s.disk.validate()?;
        } else {
            s.commit(Disk::empty())?;
        }
        s.healthy()?;
        Ok(s)
    }
    pub(super) fn disk(&self) -> &Disk {
        &self.disk
    }
    pub(super) fn poisoned(&self) -> bool {
        self.poisoned
    }
    fn read_bytes(&self) -> Result<Vec<u8>> {
        let mut bytes = Vec::new();
        open_file(&self.directory, DATA, self.uid, false)?
            .take(LIMIT as u64 + 1)
            .read_to_end(&mut bytes)?;
        if bytes.len() > LIMIT {
            return Err(Error::State("controller history exceeds 64KiB".into()));
        }
        Ok(bytes)
    }
    fn healthy(&mut self) -> Result<()> {
        let valid = (|| {
            validate_directory(&self.directory, self.uid)?;
            validate_file(&self.lock.metadata()?, self.uid)?;
            Ok::<_, Error>(
                self.directory.metadata()?.mode() & 0o7777 == 0o700
                    && identity_at(&self.directory, LOCK, self.uid)?
                        == Some((self.lock.metadata()?.dev(), self.lock.metadata()?.ino()))
                    && identity_at(&self.directory, DATA, self.uid)? == self.identity
                    && (self.identity.is_none() || self.read_bytes()? == self.bytes),
            )
        })();
        if self.poisoned || !matches!(valid, Ok(true)) {
            self.poisoned = true;
            return Err(Error::State(
                "controller history changed or is unconfirmed; mutations blocked".into(),
            ));
        }
        Ok(())
    }
    fn commit(&mut self, disk: Disk) -> Result<()> {
        self.healthy()?;
        disk.validate()?;
        let bytes = serde_json::to_vec(&disk)
            .map_err(|_| Error::State("controller history encoding failed".into()))?;
        if bytes.len() > LIMIT {
            return Err(Error::State("controller history capacity exceeded".into()));
        }
        let result = (|| {
            if identity_at(&self.directory, TEMP, self.uid)?.is_some() {
                rustix::fs::unlinkat(&self.directory, TEMP, AtFlags::empty())
                    .map_err(std::io::Error::from)?;
            }
            let mut f = open_file(&self.directory, TEMP, self.uid, true)?;
            f.write_all(&bytes)?;
            f.sync_all()?;
            let id = (f.metadata()?.dev(), f.metadata()?.ino());
            #[cfg(test)]
            if self.fail_stage == 1 {
                return Err(Error::State(
                    "injected controller commit failure before rename".into(),
                ));
            }
            rustix::fs::renameat(&self.directory, TEMP, &self.directory, DATA)
                .map_err(std::io::Error::from)?;
            #[cfg(test)]
            if self.fail_stage == 2 {
                return Err(Error::State(
                    "injected controller commit failure after rename".into(),
                ));
            }
            self.directory.sync_all()?;
            if identity_at(&self.directory, DATA, self.uid)? != Some(id) {
                return Err(Error::State(
                    "controller history changed during commit".into(),
                ));
            }
            Ok(id)
        })();
        match result {
            Ok(id) => {
                self.identity = Some(id);
                self.bytes = bytes;
                self.disk = disk;
                Ok(())
            }
            Err(e) => {
                self.poisoned = true;
                Err(e)
            }
        }
    }
    pub(super) fn replay(&mut self, change: &Change) -> Result<Option<Record>> {
        self.healthy()?;
        if let Some(r) = self
            .disk
            .records
            .iter()
            .find(|r| r.last_request.request_id == change.request_id)
        {
            if &r.last_request != change {
                return Err(Error::InvalidInput(
                    "retained controller request ID has different input".into(),
                ));
            }
            return Ok(Some(r.clone()));
        }
        Ok(None)
    }
    pub(super) fn selection(&mut self, change: &Change) -> Result<Selector> {
        self.healthy()?;
        change.validate()?;
        if change.expected_revision != self.disk.revision {
            return Err(Error::State(
                "controller revision conflict; inspect/query before issuing a new request".into(),
            ));
        }
        let record = self
            .disk
            .records
            .iter()
            .find(|r| r.policy_id == change.policy_id);
        let selector = match &change.mutation {
            Mutation::Enroll { selector, .. } => {
                if record.is_some_and(|r| !r.retired) {
                    return Err(Error::State(
                        "controller policy is already enrolled or pending".into(),
                    ));
                }
                if record.is_none() && self.disk.records.len() == 16 {
                    return Err(Error::State("controller policy capacity reached; retired slots may be explicitly reused".into()));
                }
                if self.disk.records.iter().any(|r| {
                    r.policy_id != change.policy_id
                        && !r.retired
                        && r.selector.address == selector.address
                }) {
                    return Err(Error::State(
                        "controller already selected by another policy".into(),
                    ));
                }
                selector.clone()
            }
            Mutation::Reconcile { .. } | Mutation::Retire { .. } => record
                .filter(|r| !r.retired)
                .map(|r| r.selector.clone())
                .ok_or_else(|| Error::State("controller policy is absent or retired".into()))?,
        };
        Ok(selector)
    }
    pub(super) fn begin(&mut self, change: &Change, selector: Selector) -> Result<()> {
        if self.selection(change)? != selector {
            return Err(Error::State(
                "controller selection changed before recording".into(),
            ));
        }
        let revision = self
            .disk
            .revision
            .checked_add(1)
            .ok_or_else(|| Error::State("controller revision exhausted".into()))?;
        let mut disk = self.disk.clone();
        disk.revision = revision;
        disk.records.retain(|r| r.policy_id != change.policy_id);
        disk.records.push(Record {
            policy_id: change.policy_id.clone(),
            revision,
            selector,
            retired: false,
            last_request: change.clone(),
            outcome: Outcome::Pending {},
        });
        disk.records.sort_by(|a, b| a.policy_id.cmp(&b.policy_id));
        // A bounded completion includes only two root observations, no target list.
        // Keep a 4KiB reserve before accepting any sysfs side effect.
        let size = serde_json::to_vec(&disk)
            .map_err(|_| Error::State("controller intent encoding failed".into()))?
            .len();
        if size > LIMIT - 4096 {
            return Err(Error::State(
                "controller history has no reserved completion capacity".into(),
            ));
        }
        self.commit(disk)
    }
    pub(super) fn complete(&mut self, change: &Change, outcome: Outcome) -> Result<Record> {
        self.healthy()?;
        let mut disk = self.disk.clone();
        let r = disk
            .records
            .iter_mut()
            .find(|r| {
                r.policy_id == change.policy_id
                    && r.last_request == *change
                    && matches!(r.outcome, Outcome::Pending {})
            })
            .ok_or_else(|| {
                Error::State("controller completion does not match original pending request".into())
            })?;
        r.retired = matches!(outcome, Outcome::Retired {});
        r.outcome = outcome;
        let record = r.clone();
        self.commit(disk)?;
        Ok(record)
    }
}
#[cfg(test)]
mod tests;

//! Bounded atomic application journal. Root cleanup proof remains in the helper.

use crate::{config::valid_label, model::*};
use rustusb_core::{Error, Result, application::*};
use serde::{Deserialize, Serialize};

pub(crate) const MAX_BYTES: usize = 2 * 1024 * 1024;
const MAX_AUDIT: usize = 1024;
const MAX_TERMINAL: usize = 128;
const MAX_REQUESTS: usize = 256;
const MAX_SESSIONS: usize = 512;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RequestRecord {
    pub id: RequestId,
    pub uid: u32,
    pub profile: String,
    pub job_id: JobId,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct State {
    pub schema_version: u32,
    pub sequence: u64,
    pub jobs: Vec<Job>,
    pub sessions: Vec<Session>,
    pub audit: Vec<AuditEvent>,
    pub requests: Vec<RequestRecord>,
}
impl Default for State {
    fn default() -> Self {
        Self {
            schema_version: 1,
            sequence: 0,
            jobs: vec![],
            sessions: vec![],
            audit: vec![],
            requests: vec![],
        }
    }
}
impl State {
    pub fn preflight(&mut self, admission: bool) -> Result<()> {
        self.prune();
        self.validate()?;
        let bytes = serde_json::to_vec(self)
            .map_err(|_| Error::State("agent state encoding failed".into()))?;
        // Reserve half the bounded journal for terminal fields, received IDs,
        // final counters and the full audit ring. These fields have fixed enums,
        // UUIDs and bounded labels; cleanup never competes for admission space.
        let limit = if admission { MAX_BYTES / 2 } else { MAX_BYTES };
        if bytes.len() > limit {
            return Err(Error::State(
                "agent journal admission capacity exhausted".into(),
            ));
        }
        Ok(())
    }
    pub fn audit(
        &mut self,
        uid: u32,
        profile: Option<String>,
        job_id: Option<JobId>,
        session_id: Option<SessionId>,
        action: AuditAction,
        failure: Option<Failure>,
    ) -> Result<()> {
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| Error::State("agent audit sequence exhausted".into()))?;
        let session = session_id
            .as_ref()
            .and_then(|id| self.sessions.iter().find(|s| s.id == *id));
        let state = session.map(|s| s.state);
        let evidence = session.map(|s| s.evidence);
        let cleanup = session.and_then(|s| s.cleanup);
        self.audit.push(AuditEvent {
            sequence: self.sequence,
            actor_uid: uid,
            profile,
            job_id,
            session_id,
            action,
            failure,
            state,
            evidence,
            cleanup,
        });
        if self.audit.len() > MAX_AUDIT {
            self.audit.remove(0);
        }
        Ok(())
    }
    pub fn prune(&mut self) {
        while self
            .sessions
            .iter()
            .filter(|s| s.terminal && s.cleanup != Some(Cleanup::Unconfirmed))
            .count()
            > MAX_TERMINAL
        {
            if let Some(index) = self
                .sessions
                .iter()
                .position(|s| s.terminal && s.cleanup != Some(Cleanup::Unconfirmed))
            {
                self.sessions.remove(index);
            } else {
                break;
            }
        }
        while self
            .jobs
            .iter()
            .filter(|j| j.state == JobState::Finished)
            .count()
            > MAX_TERMINAL
        {
            if let Some(index) = self.jobs.iter().position(|j| {
                j.state == JobState::Finished && !self.sessions.iter().any(|s| s.job_id == j.id)
            }) {
                let id = self.jobs.remove(index).id;
                self.requests.retain(|r| r.job_id != id);
            } else {
                break;
            }
        }
        while self.requests.len() > MAX_REQUESTS {
            self.requests.remove(0);
        }
    }
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != 1
            || self.audit.len() > MAX_AUDIT
            || self.requests.len() > MAX_REQUESTS
            || self.sessions.len() > MAX_SESSIONS
            || self.jobs.len() > MAX_SESSIONS
        {
            return Err(Error::InvalidInput("agent state bounds/version".into()));
        }
        let mut ids = std::collections::BTreeSet::new();
        for job in &self.jobs {
            valid_label(&job.profile)?;
            if !ids.insert(job.id.as_str()) {
                return Err(Error::InvalidInput("duplicate agent job".into()));
            }
        }
        let mut sessions = std::collections::BTreeSet::new();
        let mut attempts = std::collections::BTreeSet::new();
        for session in &self.sessions {
            valid_label(&session.profile)?;
            if !sessions.insert(session.id.as_str())
                || !self.jobs.iter().any(|j| {
                    j.id == session.job_id
                        && j.profile == session.profile
                        && j.instance_id == session.instance_id
                })
                || session
                    .principal
                    .as_ref()
                    .is_some_and(|p| valid_label(p).is_err())
                || session.attempts.len() > 32
            {
                return Err(Error::InvalidInput("agent session identity/bounds".into()));
            }
            for attempt in &session.attempts {
                attempt.correlation.validate()?;
                if attempt.correlation.instance_id != session.instance_id
                    || attempt.correlation.session_id != session.id
                    || !attempts.insert(attempt.correlation.attempt_id.as_str())
                {
                    return Err(Error::InvalidInput("agent attempt identity".into()));
                }
            }
            if session.terminal && session.cleanup.is_none()
                || session.state == rustusb_core::SessionState::Disconnected
                    && !matches!(
                        session.cleanup,
                        Some(Cleanup::NotStarted | Cleanup::Verified)
                    )
            {
                return Err(Error::InvalidInput("agent release without evidence".into()));
            }
        }
        let mut sequence = 0;
        for event in &self.audit {
            if event.sequence <= sequence
                || event.sequence > self.sequence
                || event
                    .profile
                    .as_ref()
                    .is_some_and(|p| valid_label(p).is_err())
            {
                return Err(Error::InvalidInput("agent audit ordering".into()));
            }
            sequence = event.sequence;
        }
        let mut requests = std::collections::BTreeSet::new();
        for request in &self.requests {
            valid_label(&request.profile)?;
            if !requests.insert(request.id.as_str())
                || !self
                    .jobs
                    .iter()
                    .any(|j| j.id == request.job_id && j.profile == request.profile)
            {
                return Err(Error::InvalidInput("agent request identity".into()));
            }
        }
        Ok(())
    }
}

#[cfg(target_os = "linux")]
mod platform {
    use super::*;
    use crate::fs::Directory;
    use std::{
        fs::File,
        io::{Read, Write},
        os::unix::fs::MetadataExt,
        path::Path,
    };

    pub(crate) struct Store {
        directory: Directory,
        _lock: File,
        identity: Option<(u64, u64)>,
        bytes: Vec<u8>,
        pub state: State,
        poisoned: bool,
    }
    impl Store {
        pub fn open(path: &Path, uid: u32) -> Result<Self> {
            Self::from_directory(Directory::open(path, uid, 0o700)?)
        }
        fn from_directory(directory: Directory) -> Result<Self> {
            let lock = directory.lock()?;
            if directory.identity("state.next")?.is_some() {
                rustix::fs::unlinkat(&directory.file, "state.next", rustix::fs::AtFlags::empty())
                    .map_err(std::io::Error::from)?;
                directory.file.sync_all()?;
            }
            let identity = directory.identity("state.json")?;
            let bytes = Self::read(&directory, identity)?;
            let state = if identity.is_none() {
                State::default()
            } else {
                serde_json::from_slice(&bytes).map_err(|_| {
                    Error::InvalidInput(
                        "corrupt agent state; administrator coordination required".into(),
                    )
                })?
            };
            state.validate()?;
            Ok(Self {
                directory,
                _lock: lock,
                identity,
                bytes,
                state,
                poisoned: false,
            })
        }
        fn read(directory: &Directory, identity: Option<(u64, u64)>) -> Result<Vec<u8>> {
            if directory.identity("state.json")? != identity {
                return Err(Error::State("agent state identity changed".into()));
            }
            let Some(identity) = identity else {
                return Ok(vec![]);
            };
            let mut file = directory.open_file("state.json", false)?;
            let before = file.metadata()?;
            if before.len() > MAX_BYTES as u64 {
                return Err(Error::InvalidInput("agent state exceeds bound".into()));
            }
            let mut bytes = vec![];
            (&mut file)
                .take((MAX_BYTES + 1) as u64)
                .read_to_end(&mut bytes)?;
            let after = file.metadata()?;
            if bytes.len() > MAX_BYTES
                || bytes.len() as u64 != before.len()
                || (before.dev(), before.ino()) != identity
                || (
                    before.len(),
                    before.mtime(),
                    before.mtime_nsec(),
                    before.ctime(),
                    before.ctime_nsec(),
                ) != (
                    after.len(),
                    after.mtime(),
                    after.mtime_nsec(),
                    after.ctime(),
                    after.ctime_nsec(),
                )
                || directory.identity("state.json")? != Some(identity)
            {
                return Err(Error::State("agent state changed during read".into()));
            }
            Ok(bytes)
        }
        pub fn commit(&mut self, mut next: State) -> Result<()> {
            if self.poisoned {
                return Err(Error::State(
                    "agent store unhealthy; admission refused".into(),
                ));
            }
            next.prune();
            next.validate()?;
            let bytes = serde_json::to_vec(&next)
                .map_err(|_| Error::State("agent state encoding failed".into()))?;
            if bytes.len() > MAX_BYTES {
                return Err(Error::State("agent journal capacity exhausted".into()));
            }
            if Self::read(&self.directory, self.identity)? != self.bytes {
                self.poisoned = true;
                return Err(Error::State("agent journal content changed".into()));
            }
            let result = (|| -> Result<(u64, u64)> {
                let mut file = self.directory.open_file("state.next", true)?;
                file.write_all(&bytes)?;
                file.sync_all()?;
                let metadata = file.metadata()?;
                let identity = (metadata.dev(), metadata.ino());
                self.directory.verify()?;
                if self.directory.identity("state.next")? != Some(identity)
                    || self.directory.identity("state.json")? != self.identity
                {
                    return Err(Error::State(
                        "agent journal changed before replacement".into(),
                    ));
                }
                rustix::fs::renameat(
                    &self.directory.file,
                    "state.next",
                    &self.directory.file,
                    "state.json",
                )
                .map_err(std::io::Error::from)?;
                self.directory.file.sync_all()?;
                if self.directory.identity("state.json")? != Some(identity) {
                    return Err(Error::State(
                        "agent journal replacement identity changed".into(),
                    ));
                }
                Ok(identity)
            })();
            match result {
                Ok(identity) => {
                    self.identity = Some(identity);
                    self.bytes = bytes;
                    self.state = next;
                    Ok(())
                }
                Err(error) => {
                    self.poisoned = true;
                    Err(error)
                }
            }
        }
        #[cfg(test)]
        pub(crate) fn fixture(path: &Path, anchor: &Path) -> Result<Self> {
            Self::from_directory(Directory::fixture(path, anchor)?)
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::os::unix::fs::{PermissionsExt, symlink};
        fn fixture() -> Result<(tempfile::TempDir, std::path::PathBuf)> {
            let root = tempfile::tempdir()?;
            std::fs::set_permissions(root.path(), std::fs::Permissions::from_mode(0o700))?;
            let path = root.path().join("data");
            std::fs::create_dir(&path)?;
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700))?;
            Ok((root, path))
        }
        #[test]
        fn atomic_restart_lock_and_sequence_are_real_filesystem_operations() -> Result<()> {
            let (root, path) = fixture()?;
            let mut store = Store::fixture(&path, root.path())?;
            assert!(Store::fixture(&path, root.path()).is_err());
            let mut next = store.state.clone();
            next.audit(
                1001,
                None,
                None,
                None,
                AuditAction::Denied,
                Some(Failure::Permission),
            )?;
            store.commit(next)?;
            drop(store);
            let mut store = Store::fixture(&path, root.path())?;
            assert_eq!(store.state.sequence, 1);
            let mut next = store.state.clone();
            next.audit(
                1001,
                None,
                None,
                None,
                AuditAction::Denied,
                Some(Failure::Permission),
            )?;
            store.commit(next)?;
            assert_eq!(store.state.sequence, 2);
            assert_eq!(
                std::fs::metadata(path.join("state.json"))?.mode() & 0o7777,
                0o600
            );
            Ok(())
        }
        #[test]
        fn corrupt_changed_symlink_and_hardlink_state_are_never_reset() -> Result<()> {
            let (root, path) = fixture()?;
            let mut store = Store::fixture(&path, root.path())?;
            store.commit(State::default())?;
            std::fs::write(path.join("state.json"), b"corrupt")?;
            assert!(store.commit(State::default()).is_err());
            drop(store);
            assert!(Store::fixture(&path, root.path()).is_err());
            assert_eq!(std::fs::read(path.join("state.json"))?, b"corrupt");
            std::fs::remove_file(path.join("state.json"))?;
            symlink("owner.lock", path.join("state.json"))?;
            assert!(Store::fixture(&path, root.path()).is_err());
            std::fs::remove_file(path.join("state.json"))?;
            std::fs::hard_link(path.join("owner.lock"), path.join("state.json"))?;
            assert!(Store::fixture(&path, root.path()).is_err());
            Ok(())
        }
        #[test]
        fn bounded_audit_preserves_monotonic_cursor_gap() -> Result<()> {
            let mut state = State::default();
            for _ in 0..1100 {
                state.audit(
                    1001,
                    None,
                    None,
                    None,
                    AuditAction::Denied,
                    Some(Failure::Permission),
                )?;
            }
            assert_eq!(state.audit.len(), 1024);
            assert_eq!(state.audit[0].sequence, 77);
            assert_eq!(state.sequence, 1100);
            state.validate()?;
            Ok(())
        }
    }
}
#[cfg(target_os = "linux")]
pub(crate) use platform::Store;

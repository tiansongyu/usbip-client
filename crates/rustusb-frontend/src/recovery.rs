//! Checked local retry metadata. These files neither authorize nor replay an edit.
use crate::{
    automation::{RuleDecisionReceipt, UnresolvedRule, matches_decision},
    preferences::{CheckedBytes, Directory},
    state::{Snapshot, Unresolved},
};
use rustusb_core::{Error, Result};
use rustusb_service::config::Manifest;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{ffi::OsString, fs::File, path::PathBuf};

const LIMIT: u64 = 16 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case", deny_unknown_fields)]
enum Slot<T> {
    Empty,
    Retained { value: T },
}
impl<T> From<Option<T>> for Slot<T> {
    fn from(value: Option<T>) -> Self {
        value.map_or(Self::Empty, |value| Self::Retained { value })
    }
}
impl<T> Slot<T> {
    fn into_option(self) -> Option<T> {
        match self {
            Self::Empty => None,
            Self::Retained { value } => Some(value),
        }
    }
}

#[derive(Debug, Clone, Default)]
pub(crate) struct Retained {
    start: Option<Unresolved>,
    rule: Option<UnresolvedRule>,
    last: Option<RuleDecisionReceipt>,
}
impl Retained {
    pub(crate) fn from_view(view: &Snapshot) -> Self {
        Self {
            start: view.unresolved.clone(),
            rule: view.unresolved_rule.clone(),
            last: view.last_rule_decision.clone(),
        }
    }
    pub(crate) fn restore(self, view: &mut Snapshot) {
        view.unresolved = self.start;
        view.unresolved_rule = self.rule;
        view.last_rule_decision = self.last;
    }
    fn validate(&self) -> Result<()> {
        fn profile(value: &str) -> Result<()> {
            if value.is_empty()
                || value.len() > 64
                || !value
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
            {
                return Err(Error::InvalidInput("recovery profile label".into()));
            }
            Ok(())
        }
        if let Some(intent) = &self.start {
            profile(&intent.profile)?;
        }
        if let Some(intent) = &self.rule {
            profile(&intent.profile)?;
        }
        if let Some(receipt) = &self.last {
            profile(&receipt.original.profile)?;
            if matches!(
                &receipt.decision,
                rustusb_service::automation::RuleDecision::Rejected { .. }
            ) && !receipt.original.settled_protocol
                || !matches_decision(&receipt.original, &receipt.decision)
                || self
                    .rule
                    .as_ref()
                    .is_some_and(|i| i.request_id == receipt.original.request_id)
            {
                return Err(Error::InvalidInput("recovery rule receipt/identity".into()));
            }
        }
        Ok(())
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Document {
    schema_version: u32,
    operator_uid: u32,
    broker_uid: u32,
    endpoint: String,
    start: Slot<Unresolved>,
    rule: Slot<UnresolvedRule>,
    last: Slot<RuleDecisionReceipt>,
}
impl Document {
    fn new(state: Retained, broker_uid: u32, endpoint: &str) -> Self {
        Self {
            schema_version: 1,
            operator_uid: rustix::process::geteuid().as_raw(),
            broker_uid,
            endpoint: endpoint.into(),
            start: state.start.into(),
            rule: state.rule.into(),
            last: state.last.into(),
        }
    }
    fn checked(self, broker_uid: u32, endpoint: &str) -> Result<Retained> {
        if self.schema_version != 1
            || self.operator_uid != rustix::process::geteuid().as_raw()
            || self.broker_uid != broker_uid
            || self.endpoint != endpoint
        {
            return Err(Error::Permission(
                "recovery version/operator/broker mismatch; original file preserved".into(),
            ));
        }
        let state = Retained {
            start: self.start.into_option(),
            rule: self.rule.into_option(),
            last: self.last.into_option(),
        };
        state.validate()?;
        Ok(state)
    }
}

/// One checked per-endpoint local writer, or a qualified read-only observation.
/// The actual lock OFD stays owned through every accepted blocking operation.
pub(crate) struct Recovery {
    directory: Directory,
    endpoint: String,
    lock_name: String,
    name: String,
    lock: Option<File>,
    lock_error: Option<String>,
    previous: Option<CheckedBytes>,
    broker_uid: u32,
    healthy: bool,
    pub(crate) retained: Retained,
}
impl Recovery {
    pub(crate) fn default_directory() -> Option<PathBuf> {
        directory_from(std::env::var_os("XDG_STATE_HOME"), std::env::var_os("HOME"))
    }
    pub(crate) fn open(directory: PathBuf, manifest: &Manifest) -> Result<Self> {
        manifest.validate()?;
        let directory = Directory::open(&directory, true)?
            .ok_or_else(|| Error::State("recovery directory missing after create".into()))?;
        let key = endpoint_key(manifest);
        let name = format!("intent-{key}.json");
        let lock_name = format!("intent-{key}.lock");
        // Read-only observers may display old identities, but never create new work.
        let (lock, lock_error) = match directory.lock_named(&lock_name) {
            Ok(lock) => (Some(lock), None),
            Err(error) => (None, Some(error.to_string())),
        };
        let previous = directory.read_bytes(&name, LIMIT)?;
        let retained = match &previous {
            Some(entry) => serde_json::from_slice::<Document>(&entry.bytes)
                .map_err(|_| {
                    Error::InvalidInput("recovery document schema; original file preserved".into())
                })?
                .checked(manifest.broker_uid, &key)?,
            None => Retained::default(),
        };
        directory.sync()?;
        Ok(Self {
            directory,
            endpoint: key,
            lock_name,
            name,
            lock,
            lock_error,
            previous,
            broker_uid: manifest.broker_uid,
            healthy: true,
            retained,
        })
    }
    pub(crate) fn writable(&self) -> bool {
        self.healthy && self.lock.is_some()
    }
    pub(crate) fn status(&self) -> String {
        if !self.healthy {
            "Local request recovery recording failed; original identity retained. Repair private state and restart this frontend; original queries and Stop remain available".into()
        } else if self.lock.is_none() {
            format!(
                "Local request recovery is read-only: {}. Close the other frontend or repair the private lock and restart; original queries and Stop remain available",
                self.lock_error.as_deref().unwrap_or("writer unavailable")
            )
        } else {
            "Local request recovery ready; confirm an original query after restart; inspect Connections for actual device release".into()
        }
    }
    pub(crate) fn save(&mut self, retained: Retained) -> Result<()> {
        let result = self.save_with(retained, || Ok(()));
        if result.is_err() {
            self.healthy = false;
        }
        result
    }
    fn save_with(
        &mut self,
        retained: Retained,
        before_commit: impl FnOnce() -> Result<()>,
    ) -> Result<()> {
        if !self.writable() {
            return Err(Error::State(self.status()));
        }
        let lock = self
            .lock
            .as_ref()
            .ok_or_else(|| Error::State("recovery writer missing".into()))?;
        self.directory.check_lock(&self.lock_name, lock)?;
        retained.validate()?;
        let bytes = serde_json::to_vec(&Document::new(
            retained.clone(),
            self.broker_uid,
            &self.endpoint,
        ))
        .map_err(|_| Error::InvalidInput("recovery encoding".into()))?;
        if self.previous.as_ref().is_some_and(|old| old.bytes == bytes) {
            if self.directory.read_bytes(&self.name, LIMIT)? != self.previous {
                return Err(Error::State("recovery previous file changed".into()));
            }
            return Ok(());
        }
        self.directory
            .replace_bytes(&self.name, LIMIT, &bytes, self.previous.as_ref(), || {
                before_commit()?;
                self.directory.check_lock(&self.lock_name, lock)
            })?;
        let published = self
            .directory
            .read_bytes(&self.name, LIMIT)?
            .ok_or_else(|| Error::State("recovery published file disappeared".into()))?;
        if published.bytes != bytes {
            return Err(Error::State("recovery published bytes changed".into()));
        }
        self.previous = Some(published);
        self.retained = retained;
        Ok(())
    }
}
fn endpoint_key(manifest: &Manifest) -> String {
    let mut hash = Sha256::new();
    hash.update(b"rustusb-frontend-recovery-v1\0");
    hash.update(manifest.socket_path().as_os_str().as_encoded_bytes());
    format!("{:x}", hash.finalize())
}
fn directory_from(state: Option<OsString>, home: Option<OsString>) -> Option<PathBuf> {
    state
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .map(|p| p.join("rustusb"))
        .or_else(|| {
            home.map(PathBuf::from)
                .filter(|p| p.is_absolute())
                .map(|p| p.join(".local/state/rustusb"))
        })
}

#[cfg(test)]
mod tests;

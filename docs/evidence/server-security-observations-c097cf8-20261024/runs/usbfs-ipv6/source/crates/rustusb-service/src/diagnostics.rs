//! Authorized, coherent observations of committed application metadata.
//!
//! These local IPC types are not the privacy-redacted export format. A collector
//! must project them into its closed artifact schema. No observation owns a USB
//! capability or substitutes for the original lifecycle/cleanup evidence.

use crate::{
    config::Backend,
    model::{AuditEvent, Job, Session},
};
use rustusb_core::application::InstanceId;
use serde::{Deserialize, Serialize};
use std::num::NonZeroU64;

/// One generated snapshot identity, never a bearer authorization capability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticToken {
    /// Current application epoch, distinct from each retained record's epoch.
    pub instance_id: InstanceId,
    /// Checked process-local sequence; zero and wrapping are forbidden.
    pub number: NonZeroU64,
}

/// A fixed section of one pinned, committed profile view.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagnosticKind {
    /// Retained profile jobs.
    Jobs,
    /// Retained profile sessions and their original attempt outcomes.
    Sessions,
    /// Profile audit plus this caller's unscoped denial events.
    Audit,
}

/// Counts after profile and caller authorization filtering.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticCounts {
    /// At most 512 retained jobs.
    pub jobs: u16,
    /// At most 512 retained sessions.
    pub sessions: u16,
    /// At most 1024 retained audit observations.
    pub audit: u16,
}

/// The stable commit and finite lifetime selected by a successful Begin.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticHeader {
    /// Identity bound privately to the actual caller UID and exact profile.
    pub snapshot: DiagnosticToken,
    /// Administrator-configured backend, without paths or credentials.
    pub backend: Backend,
    /// Original committed application sequence shared by every page.
    pub committed_sequence: u64,
    /// Monotonic age of this publication when Begin was accepted.
    pub publication_age_millis: u64,
    /// Complete retained section sizes for this caller/profile.
    pub counts: DiagnosticCounts,
    /// Global audit-ring floor, not proof that older profile events never existed.
    pub audit_oldest_retained_sequence: u64,
    /// Access lifetime from Begin; fixed to 30,000 milliseconds.
    pub expires_after_millis: u64,
}

/// Complete typed rows; no arbitrary JSON or raw journal serialization.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum DiagnosticItems {
    /// At most sixteen complete jobs.
    Jobs {
        /// Deterministic UUID-ordered profile jobs.
        jobs: Vec<Job>,
    },
    /// At most sixteen complete sessions.
    Sessions {
        /// Deterministic UUID-ordered sessions, retaining original evidence.
        sessions: Vec<Session>,
    },
    /// At most sixteen complete audit events.
    Audit {
        /// Original sequence-ordered, authorized audit events.
        events: Vec<AuditEvent>,
    },
}

/// One finite section page of the same immutable committed snapshot.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DiagnosticPage {
    /// Exact Begin token; no new snapshot is silently substituted.
    pub snapshot: DiagnosticToken,
    /// Original committed sequence, unchanged between pages.
    pub committed_sequence: u64,
    /// Number of filtered rows already consumed before this page.
    pub after: u16,
    /// Complete filtered section size, including rows on other pages.
    pub total: u16,
    /// Next consumed-row offset, or explicit null after the final page.
    #[serde(deserialize_with = "required_next")]
    pub next_after: Option<u16>,
    /// Closed section variant containing complete rows.
    pub items: DiagnosticItems,
}

fn required_next<'de, D: serde::Deserializer<'de>>(decoder: D) -> Result<Option<u16>, D::Error> {
    Option::<u16>::deserialize(decoder)
}

/// Fixed diagnostic failures, independent from durable journal health.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DiagnosticFailure {
    /// Four actual snapshots or this caller's one snapshot are still retained.
    Busy,
    /// Unknown, foreign, ended, old-instance or expired snapshot.
    Expired,
    /// Offset exceeds this authorized section's row count.
    InvalidCursor,
    /// Committed publication is unavailable, poisoned or its counter exhausted.
    Unavailable,
    /// A complete row or response cannot fit the fixed encoding budget.
    EncodingLimit,
}

#[cfg(target_os = "linux")]
mod registry {
    use super::*;
    use crate::{
        config::{Action, Manifest},
        model::AuditAction,
        rpc::{Request, Response},
        store::State,
    };
    use std::{
        collections::BTreeMap,
        sync::{Arc, Mutex, Weak},
        time::{Duration, Instant},
    };
    use tokio::sync::{OwnedSemaphorePermit, Semaphore};

    const LIFETIME: Duration = Duration::from_secs(30);
    const PAGE_BYTES: usize = 60_000;
    const PAGE_ROWS: usize = 16;
    type DiagnosticResult<T> = std::result::Result<T, DiagnosticFailure>;

    struct Committed {
        state: State,
        published: Instant,
    }
    struct Snapshot {
        token: DiagnosticToken,
        uid: u32,
        profile: String,
        backend: Backend,
        committed: Arc<Committed>,
        expires: Instant,
        // Metadata capacity only. Removing a registry entry cannot release this
        // charge while a page still holds the actual snapshot Arc.
        _quota: OwnedSemaphorePermit,
    }
    struct Inner {
        current: Arc<Committed>,
        next_number: u64,
        snapshots: BTreeMap<u64, Arc<Snapshot>>,
        owners: BTreeMap<u32, Weak<Snapshot>>,
    }

    /// Metadata publication independent from both Controls and the Store lock.
    pub(crate) struct Registry {
        instance: InstanceId,
        inner: Mutex<Inner>,
        quota: Arc<Semaphore>,
    }
    impl Registry {
        #[cfg(test)]
        pub(crate) fn poison_for_fixture(&self) {
            if let Ok(_guard) = self.inner.lock() {
                std::panic::resume_unwind(Box::new("diagnostic registry fixture poison"));
            }
        }

        pub(crate) fn new(instance: InstanceId, state: State) -> Self {
            Self {
                instance,
                inner: Mutex::new(Inner {
                    current: Arc::new(Committed {
                        state,
                        published: Instant::now(),
                    }),
                    next_number: 0,
                    snapshots: BTreeMap::new(),
                    owners: BTreeMap::new(),
                }),
                quota: Arc::new(Semaphore::new(4)),
            }
        }

        /// The caller supplies an already committed clone, made outside our lock.
        /// Poison leaves diagnostic access unavailable; it cannot change the WAL
        /// result, suppress an existing mirror, or publish uncommitted facts.
        pub(crate) fn publish(&self, state: State) {
            let committed = Arc::new(Committed {
                state,
                published: Instant::now(),
            });
            let previous = match self.inner.lock() {
                Ok(mut inner) => Some(std::mem::replace(&mut inner.current, committed)),
                Err(_) => None,
            };
            // Releasing a large old metadata view also happens outside the lock.
            drop(previous);
        }

        fn reap(&self, now: Instant) -> DiagnosticResult<()> {
            let retired: Vec<_> = {
                let mut inner = self
                    .inner
                    .lock()
                    .map_err(|_| DiagnosticFailure::Unavailable)?;
                let expired: Vec<_> = inner
                    .snapshots
                    .iter()
                    .filter(|(_, snapshot)| snapshot.expires <= now)
                    .map(|(number, _)| *number)
                    .collect();
                expired
                    .into_iter()
                    .filter_map(|number| inner.snapshots.remove(&number))
                    .collect()
            };
            drop(retired);
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| DiagnosticFailure::Unavailable)?;
            inner
                .owners
                .retain(|_, snapshot| snapshot.strong_count() > 0);
            Ok(())
        }

        fn begin(
            &self,
            uid: u32,
            profile: &str,
            backend: Backend,
            now: Instant,
        ) -> DiagnosticResult<DiagnosticHeader> {
            self.reap(now)?;
            let snapshot = {
                let mut inner = self
                    .inner
                    .lock()
                    .map_err(|_| DiagnosticFailure::Unavailable)?;
                inner
                    .owners
                    .retain(|_, snapshot| snapshot.strong_count() > 0);
                if inner.owners.len() >= 4 || inner.owners.contains_key(&uid) {
                    return Err(DiagnosticFailure::Busy);
                }
                let number = inner
                    .next_number
                    .checked_add(1)
                    .and_then(NonZeroU64::new)
                    .ok_or(DiagnosticFailure::Unavailable)?;
                let quota = self
                    .quota
                    .clone()
                    .try_acquire_owned()
                    .map_err(|_| DiagnosticFailure::Busy)?;
                let snapshot = Arc::new(Snapshot {
                    token: DiagnosticToken {
                        instance_id: self.instance.clone(),
                        number,
                    },
                    uid,
                    profile: profile.to_owned(),
                    backend,
                    committed: inner.current.clone(),
                    expires: now + LIFETIME,
                    _quota: quota,
                });
                inner.next_number = number.get();
                inner.owners.insert(uid, Arc::downgrade(&snapshot));
                inner.snapshots.insert(number.get(), snapshot.clone());
                snapshot
            };
            // Filtering/counting copies no private file and holds no registry lock.
            let state = &snapshot.committed.state;
            Ok(DiagnosticHeader {
                snapshot: snapshot.token.clone(),
                backend: snapshot.backend,
                committed_sequence: state.sequence,
                publication_age_millis: u64::try_from(
                    now.saturating_duration_since(snapshot.committed.published)
                        .as_millis(),
                )
                .unwrap_or(u64::MAX),
                counts: snapshot.counts()?,
                audit_oldest_retained_sequence: state
                    .audit
                    .first()
                    .map_or(state.sequence.saturating_add(1), |event| event.sequence),
                expires_after_millis: 30_000,
            })
        }

        fn pin(
            &self,
            uid: u32,
            profile: &str,
            token: &DiagnosticToken,
            now: Instant,
        ) -> DiagnosticResult<Arc<Snapshot>> {
            self.reap(now)?;
            let inner = self
                .inner
                .lock()
                .map_err(|_| DiagnosticFailure::Unavailable)?;
            if token.instance_id != self.instance {
                return Err(DiagnosticFailure::Expired);
            }
            inner
                .snapshots
                .get(&token.number.get())
                .filter(|snapshot| {
                    snapshot.uid == uid && snapshot.profile == profile && snapshot.token == *token
                })
                .cloned()
                .ok_or(DiagnosticFailure::Expired)
        }

        fn end(
            &self,
            uid: u32,
            profile: &str,
            token: &DiagnosticToken,
            now: Instant,
        ) -> DiagnosticResult<()> {
            self.reap(now)?;
            let retired = {
                let mut inner = self
                    .inner
                    .lock()
                    .map_err(|_| DiagnosticFailure::Unavailable)?;
                if token.instance_id == self.instance
                    && inner
                        .snapshots
                        .get(&token.number.get())
                        .is_some_and(|snapshot| {
                            snapshot.uid == uid
                                && snapshot.profile == profile
                                && snapshot.token == *token
                        })
                {
                    inner.snapshots.remove(&token.number.get())
                } else {
                    None
                }
            };
            drop(retired);
            // Unknown, expired and foreign identities have this same acknowledgement.
            // A Weak owner remains while an in-flight page retains its snapshot.
            self.reap(now)
        }
    }

    impl Snapshot {
        fn audit_visible(&self, event: &AuditEvent) -> bool {
            event.profile.as_deref() == Some(self.profile.as_str())
                || (event.profile.is_none()
                    && event.action == AuditAction::Denied
                    && event.actor_uid == self.uid)
        }
        fn counts(&self) -> DiagnosticResult<DiagnosticCounts> {
            let state = &self.committed.state;
            Ok(DiagnosticCounts {
                jobs: count(
                    state
                        .jobs
                        .iter()
                        .filter(|job| job.profile == self.profile)
                        .count(),
                )?,
                sessions: count(
                    state
                        .sessions
                        .iter()
                        .filter(|session| session.profile == self.profile)
                        .count(),
                )?,
                audit: count(
                    state
                        .audit
                        .iter()
                        .filter(|event| self.audit_visible(event))
                        .count(),
                )?,
            })
        }
        fn page(&self, kind: DiagnosticKind, after: u16) -> DiagnosticResult<DiagnosticPage> {
            let state = &self.committed.state;
            let (items, total, returned) = match kind {
                DiagnosticKind::Jobs => {
                    let mut rows: Vec<_> = state
                        .jobs
                        .iter()
                        .filter(|job| job.profile == self.profile)
                        .collect();
                    rows.sort_by(|a, b| a.id.cmp(&b.id));
                    let total = count(rows.len())?;
                    let page = page_rows(&rows, after)?;
                    let returned = count(page.len())?;
                    (DiagnosticItems::Jobs { jobs: page }, total, returned)
                }
                DiagnosticKind::Sessions => {
                    let mut rows: Vec<_> = state
                        .sessions
                        .iter()
                        .filter(|session| session.profile == self.profile)
                        .collect();
                    rows.sort_by(|a, b| a.id.cmp(&b.id));
                    let total = count(rows.len())?;
                    let page = page_rows(&rows, after)?;
                    let returned = count(page.len())?;
                    (
                        DiagnosticItems::Sessions { sessions: page },
                        total,
                        returned,
                    )
                }
                DiagnosticKind::Audit => {
                    let mut rows: Vec<_> = state
                        .audit
                        .iter()
                        .filter(|event| self.audit_visible(event))
                        .collect();
                    rows.sort_by_key(|event| event.sequence);
                    let total = count(rows.len())?;
                    let page = page_rows(&rows, after)?;
                    let returned = count(page.len())?;
                    (DiagnosticItems::Audit { events: page }, total, returned)
                }
            };
            let consumed = after
                .checked_add(returned)
                .ok_or(DiagnosticFailure::EncodingLimit)?;
            let page = DiagnosticPage {
                snapshot: self.token.clone(),
                committed_sequence: state.sequence,
                after,
                total,
                next_after: (consumed < total).then_some(consumed),
                items,
            };
            // Include section/token/continuation metadata, not just row payloads.
            if serde_json::to_vec(&page)
                .map_err(|_| DiagnosticFailure::EncodingLimit)?
                .len()
                > PAGE_BYTES
            {
                return Err(DiagnosticFailure::EncodingLimit);
            }
            Ok(page)
        }
    }

    fn count(value: usize) -> DiagnosticResult<u16> {
        u16::try_from(value).map_err(|_| DiagnosticFailure::EncodingLimit)
    }
    fn page_rows<T: Clone + Serialize>(rows: &[&T], after: u16) -> DiagnosticResult<Vec<T>> {
        if usize::from(after) > rows.len() {
            return Err(DiagnosticFailure::InvalidCursor);
        }
        let mut result = Vec::new();
        // Reserve finite room for the exact token/section/envelope metadata. The
        // complete page and IPC envelope are checked separately after encoding.
        let mut bytes = 512usize;
        for row in rows.iter().skip(usize::from(after)).take(PAGE_ROWS) {
            let size = serde_json::to_vec(*row)
                .map_err(|_| DiagnosticFailure::EncodingLimit)?
                .len();
            if bytes.saturating_add(size).saturating_add(1) > PAGE_BYTES {
                if result.is_empty() {
                    return Err(DiagnosticFailure::EncodingLimit);
                }
                break;
            }
            bytes += size + 1;
            result.push((**row).clone());
        }
        Ok(result)
    }

    /// Authorization precedes registry existence, capacity, token or offset lookup.
    pub(crate) fn respond(
        registry: Option<&Registry>,
        manifest: &Manifest,
        uid: u32,
        request: &Request,
    ) -> Option<rustusb_core::Result<Response>> {
        let profile = match request {
            Request::BeginDiagnostic { profile }
            | Request::DiagnosticPage { profile, .. }
            | Request::EndDiagnostic { profile, .. } => profile,
            _ => return None,
        };
        Some((|| {
            manifest.authorize(uid, profile, Action::Read)?;
            manifest.authorize(uid, profile, Action::Audit)?;
            let result = if let Some(registry) = registry {
                let now = Instant::now();
                match request {
                    Request::BeginDiagnostic { .. } => {
                        let backend = manifest
                            .profiles
                            .iter()
                            .find(|item| item.id == *profile)
                            .map(|item| item.backend);
                        match backend {
                            Some(backend) => registry
                                .begin(uid, profile, backend, now)
                                .map(|header| Response::DiagnosticStarted { header }),
                            None => Err(DiagnosticFailure::Unavailable),
                        }
                    }
                    Request::DiagnosticPage {
                        snapshot,
                        kind,
                        after,
                        ..
                    } => registry
                        .pin(uid, profile, snapshot, now)
                        .and_then(|snapshot| snapshot.page(*kind, *after))
                        .map(|page| Response::DiagnosticPage { page }),
                    Request::EndDiagnostic { snapshot, .. } => registry
                        .end(uid, profile, snapshot, now)
                        .map(|()| Response::DiagnosticEnded {}),
                    _ => Err(DiagnosticFailure::Unavailable),
                }
            } else {
                Err(DiagnosticFailure::Unavailable)
            };
            Ok(result.unwrap_or_else(|code| Response::DiagnosticError { code }))
        })())
    }

    #[cfg(test)]
    mod tests {
        include!("diagnostics_tests.rs");
    }
}

#[cfg(target_os = "linux")]
pub(crate) use registry::{Registry, respond};

//! Strict payload-free log records and bounded observer values.

use rustusb_core::{Error as CoreError, SessionState, application::*};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, fmt, path::PathBuf, str::FromStr};

/// Logging failure, independent of a USB operation and its cleanup result.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// Invalid bounded local input.
    #[error("invalid logging input: {0}")]
    InvalidInput(&'static str),
    /// The private log filesystem could not be trusted.
    #[error("logging permission: {0}")]
    Permission(&'static str),
    /// Preserved history is malformed or has ambiguous identity.
    #[error("logging history corrupt: {0}")]
    Corrupt(&'static str),
    /// An actual filesystem operation failed.
    #[error("logging I/O: {0}")]
    Io(#[from] std::io::Error),
    /// The finite reader/admission resource is busy.
    #[error("logging resource busy")]
    Busy,
    /// No file logger was configured or its owner has closed admission.
    #[error("logging is disabled or closed")]
    Disabled,
    /// The requested platform has no checked file implementation.
    #[error("checked file logging is unavailable on this platform")]
    Unsupported,
    /// Subscriber installation or an actual worker failed.
    #[error("logging worker: {0}")]
    Worker(&'static str),
    /// Accepted operation work may have succeeded, but log records were lost.
    #[error("logging lost records; the requested operation may have completed")]
    LostRecords,
}
/// Logging-specific fallible result; never a USB cleanup witness.
pub type Result<T> = std::result::Result<T, Error>;

/// Validated file-mode configuration supplied only at process startup.
#[derive(Debug, Clone)]
pub struct Options {
    /// Existing private mode0700 directory; never supplied through an RPC.
    pub directory: PathBuf,
    /// Maximum bytes including the header and complete record newlines.
    pub segment_bytes: u64,
    /// Total segment count, including the active segment.
    pub retained_segments: u8,
}
impl Options {
    /// Use 4MiB segments and eight retained segments.
    pub fn new(directory: PathBuf) -> Self {
        Self {
            directory,
            segment_bytes: 4 * 1024 * 1024,
            retained_segments: 8,
        }
    }
    /// Validate finite sizes and a nontraversing absolute directory path.
    pub fn validate(&self) -> Result<()> {
        if !(4096..=crate::MAX_SEGMENT_BYTES).contains(&self.segment_bytes)
            || !(2..=crate::MAX_SEGMENTS).contains(&self.retained_segments)
            || !self.directory.is_absolute()
            || self.directory.components().any(|c| {
                matches!(
                    c,
                    std::path::Component::ParentDir | std::path::Component::CurDir
                )
            })
            || self.directory.as_os_str().len() > 4096
        {
            return Err(Error::InvalidInput("directory or retention bounds"));
        }
        Ok(())
    }
}

/// Canonical UUID used for log epochs/segments, not authentication or ownership.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct LogId(String);
impl LogId {
    /// Validate one canonical lowercase UUID.
    pub fn new(value: impl Into<String>) -> Result<Self> {
        let value = value.into();
        if value.len() != 36
            || !value.bytes().enumerate().all(|(i, b)| {
                if matches!(i, 8 | 13 | 18 | 23) {
                    b == b'-'
                } else {
                    b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
                }
            })
        {
            return Err(Error::InvalidInput("log identity"));
        }
        Ok(Self(value))
    }
    /// Borrow the canonical representation.
    pub fn as_str(&self) -> &str {
        &self.0
    }
    /// Obtain a fresh Linux kernel UUID without using a process ID as identity.
    pub fn fresh() -> Result<Self> {
        #[cfg(target_os = "linux")]
        {
            use std::io::Read;
            let mut text = String::new();
            std::fs::File::open("/proc/sys/kernel/random/uuid")?
                .take(128)
                .read_to_string(&mut text)?;
            Self::new(text.trim_end())
        }
        #[cfg(not(target_os = "linux"))]
        {
            Err(Error::Unsupported)
        }
    }
}
impl TryFrom<String> for LogId {
    type Error = Error;
    fn try_from(v: String) -> Result<Self> {
        Self::new(v)
    }
}
impl From<LogId> for String {
    fn from(v: LogId) -> Self {
        v.0
    }
}
impl fmt::Display for LogId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// Opaque generated segment and complete-line offset; never a file path or authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct Cursor {
    pub(crate) segment: LogId,
    pub(crate) offset: u64,
}
impl Cursor {
    /// Validate a syntactically bounded cursor; the reader checks the real boundary.
    pub fn new(segment: LogId, offset: u64) -> Result<Self> {
        if offset > crate::MAX_SEGMENT_BYTES {
            return Err(Error::InvalidInput("log cursor offset"));
        }
        Ok(Self { segment, offset })
    }
    /// The generated segment identity, not a caller-provided filename.
    pub fn segment(&self) -> &LogId {
        &self.segment
    }
    /// Offset immediately following a complete header or record line.
    pub fn offset(&self) -> u64 {
        self.offset
    }
}
impl FromStr for Cursor {
    type Err = Error;
    fn from_str(v: &str) -> Result<Self> {
        if v.len() > 58 {
            return Err(Error::InvalidInput("log cursor length"));
        }
        let (id, offset) = v.split_once(':').ok_or(Error::InvalidInput("log cursor"))?;
        if offset.is_empty()
            || (offset.len() > 1 && offset.starts_with('0'))
            || !offset.bytes().all(|b| b.is_ascii_digit())
        {
            return Err(Error::InvalidInput("log cursor offset"));
        }
        Self::new(
            LogId::new(id)?,
            offset
                .parse()
                .map_err(|_| Error::InvalidInput("log cursor offset"))?,
        )
    }
}
impl TryFrom<String> for Cursor {
    type Error = Error;
    fn try_from(v: String) -> Result<Self> {
        v.parse()
    }
}
impl From<Cursor> for String {
    fn from(v: Cursor) -> Self {
        v.to_string()
    }
}
impl fmt::Display for Cursor {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}:{}", self.segment, self.offset)
    }
}

/// Fixed severity; arbitrary display strings are not stored.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Level {
    /// Trace diagnostic.
    Trace,
    /// Debug diagnostic.
    Debug,
    /// Informational observation.
    Info,
    /// Warning.
    Warn,
    /// Failure observation.
    Error,
}
/// Secret-free operation failure classification.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum FailureCategory {
    /// Invalid input.
    InvalidInput,
    /// Permission or authentication refusal.
    Permission,
    /// Protocol failure.
    Protocol,
    /// Actual I/O failure.
    Io,
    /// Deadline exceeded.
    Timeout,
    /// State/ownership uncertainty.
    State,
    /// Unsupported operation.
    Unsupported,
    /// Fixed native command failure.
    Command,
    /// Unsafe device refused.
    UnsafeDevice,
    /// Cooperative stop.
    Cancelled,
    /// Business journal failure.
    Persistence,
}
impl FailureCategory {
    /// Fixed token suitable for a tracing field, without formatting an internal error.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::InvalidInput => "invalid_input",
            Self::Permission => "permission",
            Self::Protocol => "protocol",
            Self::Io => "io",
            Self::Timeout => "timeout",
            Self::State => "state",
            Self::Unsupported => "unsupported",
            Self::Command => "command",
            Self::UnsafeDevice => "unsafe_device",
            Self::Cancelled => "cancelled",
            Self::Persistence => "persistence",
        }
    }
}
impl From<&CoreError> for FailureCategory {
    fn from(v: &CoreError) -> Self {
        match v {
            CoreError::InvalidInput(_) => Self::InvalidInput,
            CoreError::Permission(_) => Self::Permission,
            CoreError::Protocol(_) => Self::Protocol,
            CoreError::Io(_) => Self::Io,
            CoreError::Timeout(_) => Self::Timeout,
            CoreError::State(_) => Self::State,
            CoreError::Unsupported(_) => Self::Unsupported,
            CoreError::Command(_) => Self::Command,
            CoreError::UnsafeDevice(_) => Self::UnsafeDevice,
        }
    }
}
/// Closed diagnostic templates. No arbitrary message body is persisted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventCode {
    /// Device scan completed.
    DiscoveryCompleted,
    /// An operation began.
    OperationStarted,
    /// An operation completed.
    OperationCompleted,
    /// An operation failed.
    OperationFailed,
    /// Native import observation.
    NativeAttach,
    /// Native detach observation.
    NativeDetach,
    /// Native export observation.
    NativeExport,
    /// Native unexport observation.
    NativeUnexport,
    /// Network lifecycle observation.
    NetworkEnded,
    /// Resource release observation.
    ResourceReleased,
    /// A restricted numeric/boolean diagnostic.
    Diagnostic,
}
/// Approved scalar observations; string-valued generic fields are excluded.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Field {
    /// Generic bounded count.
    Count,
    /// Discovered devices.
    DeviceCount,
    /// Session count.
    SessionCount,
    /// Interface number.
    Interface,
    /// Operation boolean.
    Success,
    /// Joined network boolean.
    NetworkClosed,
    /// Actual application bytes to peer.
    BytesToPeer,
    /// Actual application bytes from peer.
    BytesFromPeer,
    /// Monotonic duration observation.
    ElapsedMillis,
}
/// Scalar values without buffers, error strings or credentials.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum Scalar {
    /// Boolean observation.
    Bool(bool),
    /// Nonnegative observation.
    Unsigned(u64),
    /// Signed observation.
    Signed(i64),
}
/// Payload-free diagnostic projection of an approved RustUSB tracing event.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Diagnostic {
    /// Closed static message template.
    pub code: EventCode,
    /// Approved static RustUSB target, never an arbitrary user path.
    pub target: String,
    /// Static source line when available.
    #[serde(deserialize_with = "required_option")]
    pub line: Option<u32>,
    /// Fixed failure category, without raw error text.
    #[serde(deserialize_with = "required_option")]
    pub failure: Option<FailureCategory>,
    /// Optional actual OS errno.
    #[serde(deserialize_with = "required_option")]
    pub errno: Option<i32>,
    /// Closed numeric/boolean fields.
    #[serde(deserialize_with = "strict_fields")]
    pub fields: BTreeMap<Field, Scalar>,
    /// Fields intentionally omitted by privacy policy.
    pub omitted_fields: u32,
}
/// Exact application audit category, separate from generic process diagnostics.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum AuditAction {
    /// Durable start.
    Start,
    /// Rejected request.
    Denied,
    /// Cooperative stop.
    Stop,
    /// Session admission.
    Admission,
    /// Attempt reservation.
    Attempt,
    /// Capability receipt.
    Resource,
    /// Actual lifecycle observation.
    Observe,
    /// Owner finish.
    Finish,
    /// Exact-proof coordination.
    Recovery,
    /// Remote catalog request.
    Discover,
}
/// Evidence copied from the committed application audit, never invented by the sink.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Evidence {
    /// Durable intent.
    Intent,
    /// Capability received.
    Prepared,
    /// Local VHCI receipt.
    LocalEnumeration,
    /// Export ready.
    ExportReady,
    /// IMPORT reply flushed.
    ImportReplyFlushed,
    /// Actual owner closing.
    Closing,
    /// Real release or no attempt.
    Released,
    /// Release unproven.
    Unconfirmed,
    /// Exact retained root proof.
    RootJournalProof,
}
/// Committed cleanup category; not inferred from log completion.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Cleanup {
    /// No resource attempt.
    NotStarted,
    /// Actual release verified by original owner/proof.
    Verified,
    /// Resource release remains unproven.
    Unconfirmed,
}
/// Mirror of one committed payload-free AuditEvent; this is not an admission receipt.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuditProjection {
    /// Current committing application epoch, distinct from the log process epoch.
    pub application_instance: InstanceId,
    /// Original committed audit sequence.
    pub audit_sequence: u64,
    /// Kernel-authenticated actor recorded by the application.
    pub actor_uid: u32,
    /// Original profile; absent for unscoped refusals.
    #[serde(deserialize_with = "required_option")]
    pub profile: Option<String>,
    /// Original job reference, when present.
    #[serde(deserialize_with = "required_option")]
    pub job_id: Option<JobId>,
    /// Original session reference, when present.
    #[serde(deserialize_with = "required_option")]
    pub session_id: Option<SessionId>,
    /// Original fixed action.
    pub action: AuditAction,
    /// Original fixed failure.
    #[serde(deserialize_with = "required_option")]
    pub failure: Option<FailureCategory>,
    /// Original actual state.
    #[serde(deserialize_with = "required_option")]
    pub state: Option<SessionState>,
    /// Original supporting evidence.
    #[serde(deserialize_with = "required_option")]
    pub evidence: Option<Evidence>,
    /// Original cleanup, independent of operation failure.
    #[serde(deserialize_with = "required_option")]
    pub cleanup: Option<Cleanup>,
}
/// Fixed kinds of process records; headers are separately validated by storage.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Event {
    /// Logger startup before device work.
    Started {
        /// Actual caller process.
        pid: u32,
        /// Actual file owner UID.
        uid: u32,
    },
    /// Sanitized tracing projection.
    Diagnostic(Diagnostic),
    /// Exact committed application mirror.
    Audit(AuditProjection),
    /// Actual drained worker shutdown, never USB cleanup evidence.
    Stopped {
        /// Final observer counters and health.
        status: LogStatus,
    },
}
/// One complete bounded NDJSON record.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LogRecord {
    /// Exactly one.
    pub schema_version: u32,
    /// Fresh process epoch; not a USB owner proof.
    pub epoch: LogId,
    /// Checked sequence within that process.
    pub sequence: u64,
    /// Local monotonic elapsed milliseconds.
    pub elapsed_millis: u64,
    /// Optional wall-clock time, never used for ownership or retention order.
    #[serde(deserialize_with = "required_option")]
    pub wall_unix_millis: Option<u64>,
    /// Fixed severity.
    pub level: Level,
    /// Closed typed event.
    pub event: Event,
}
/// Nonowning logger health and finite counter snapshot.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LogStatus {
    /// Whether the actual configured worker has no latched failure/loss.
    pub healthy: bool,
    /// New admissions have been closed.
    pub closing: bool,
    /// Actual accepted queue depth.
    pub queued: u32,
    /// Producer records accepted by the finite queue, excluding start/stop records.
    pub accepted: u64,
    /// Producer records actually synced, excluding start/stop records.
    pub written: u64,
    /// Producer records refused because admission was full/closed.
    pub dropped: u64,
    /// Producer records rejected by the encoded-size cap.
    pub oversized: u64,
    /// Invalid typed projections or recorder failures.
    pub projection_errors: u64,
    /// A checked sequence or counter exhausted its representation.
    pub overflowed: bool,
    /// Last complete actually synced record boundary, when known.
    #[serde(deserialize_with = "required_option")]
    pub last_written: Option<Cursor>,
    /// Latched fixed logger failure; it is not a USB failure category.
    #[serde(deserialize_with = "required_option")]
    pub failure: Option<FailureCategory>,
}
/// An authorized reader filter. RPC callers never supply this value directly.
#[derive(Debug, Clone)]
pub enum ReadFilter {
    /// Direct owner/admin local filesystem read, after OS/identity checks.
    All,
    /// Only exact typed audit profile and caller's unscoped denials; root may see unscoped admin audit.
    Audit {
        /// Already-authorized profile.
        profile: String,
        /// Kernel-reported requester UID.
        actor_uid: u32,
        /// Derived from actual UID0, never a request claim.
        root_admin: bool,
    },
}
/// Finite stored-record page. Missing live status is explicitly unavailable.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LogPage {
    /// Exactly one.
    pub schema_version: u32,
    /// At most sixteen validated records after scope filtering.
    pub records: Vec<LogRecord>,
    /// Complete scanned boundary, including progress across filtered records.
    #[serde(deserialize_with = "required_option")]
    pub next_after: Option<Cursor>,
    /// Earliest retained complete boundary, if available.
    #[serde(deserialize_with = "required_option")]
    pub oldest_available: Option<Cursor>,
    /// Requested history expired or preserved incomplete tail was encountered.
    pub gap: bool,
    /// An old incomplete final line was skipped, with original bytes preserved.
    pub partial_tail: bool,
    /// Bounded record bytes scanned, including filtered records.
    pub scanned_bytes: u32,
    /// More snapshot data remains after the finite scan/record window.
    pub more: bool,
    /// Current nonowning worker status only for an actual live monitor.
    #[serde(deserialize_with = "required_option")]
    pub status: Option<LogStatus>,
}
/// Successful actual writer/reader joins; disabled logging has no status.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ShutdownReport {
    /// Final status, absent in legacy stderr-only mode.
    #[serde(deserialize_with = "required_option")]
    pub status: Option<LogStatus>,
}

fn required_option<'de, D, T>(deserializer: D) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

fn strict_fields<'de, D>(deserializer: D) -> std::result::Result<BTreeMap<Field, Scalar>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    struct Fields;
    impl<'de> serde::de::Visitor<'de> for Fields {
        type Value = BTreeMap<Field, Scalar>;
        fn expecting(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            f.write_str("unique approved scalar log fields")
        }
        fn visit_map<A>(self, mut access: A) -> std::result::Result<Self::Value, A::Error>
        where
            A: serde::de::MapAccess<'de>,
        {
            let mut fields = BTreeMap::new();
            while let Some((key, value)) = access.next_entry::<Field, Scalar>()? {
                if fields.insert(key, value).is_some() {
                    return Err(serde::de::Error::custom("duplicate scalar logging field"));
                }
            }
            Ok(fields)
        }
    }
    deserializer.deserialize_map(Fields)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn untrusted_log_json_requires_nullable_fields_and_unique_scalar_keys()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let valid = r#"{"schema_version":1,"epoch":"00000000-0000-0000-0000-000000000001","sequence":2,"elapsed_millis":5,"wall_unix_millis":null,"level":"warn","event":{"kind":"diagnostic","code":"operation_failed","target":"rustusb_cli","line":null,"failure":"io","errno":null,"fields":{"count":3},"omitted_fields":2}}"#;
        let record: LogRecord = serde_json::from_str(valid)?;
        record.validate()?;
        let mut value: serde_json::Value = serde_json::from_str(valid)?;
        if let Some(object) = value.as_object_mut() {
            object.remove("wall_unix_millis");
        }
        assert!(serde_json::from_value::<LogRecord>(value).is_err());
        let mut value: serde_json::Value = serde_json::from_str(valid)?;
        if let Some(object) = value
            .get_mut("event")
            .and_then(serde_json::Value::as_object_mut)
        {
            object.remove("errno");
        }
        assert!(serde_json::from_value::<LogRecord>(value).is_err());
        let duplicate = valid.replace("\"count\":3", "\"count\":3,\"count\":4");
        assert!(serde_json::from_str::<LogRecord>(&duplicate).is_err());
        let unknown = valid.replace(
            "\"count\":3",
            "\"private_key\":\"SHOULD_NEVER_BECOME_A_LOG_FIELD\"",
        );
        assert!(serde_json::from_str::<LogRecord>(&unknown).is_err());
        Ok(())
    }

    #[test]
    fn opaque_cursor_rejects_paths_noncanonical_offsets_and_excessive_positions()
    -> std::result::Result<(), Box<dyn std::error::Error>> {
        let id = "00000000-0000-0000-0000-000000000001";
        for input in [
            "/etc/passwd".to_owned(),
            format!("{id}:01"),
            format!("{id}:-1"),
            format!("{id}:0x10"),
            format!("{id}:{}", crate::MAX_SEGMENT_BYTES + 1),
            format!("{id}:1/../../private"),
        ] {
            assert!(input.parse::<Cursor>().is_err());
        }
        let cursor: Cursor = format!("{id}:512").parse()?;
        assert_eq!(cursor.offset(), 512);
        assert_eq!(cursor.to_string(), format!("{id}:512"));
        Ok(())
    }
}

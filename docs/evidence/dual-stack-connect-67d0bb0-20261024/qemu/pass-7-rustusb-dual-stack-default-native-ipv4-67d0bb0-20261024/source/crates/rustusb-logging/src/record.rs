//! Strict, payload-free validation and rendering shared by writers and readers.

use crate::{
    AuditAction, AuditProjection, Diagnostic, Error, Event, EventCode, Field, Level, LogRecord,
    LogStatus, MAX_RECORD_BYTES, QUEUE_CAPACITY, Result, Scalar,
};

pub(crate) fn approved_target(target: &str) -> bool {
    const ROOTS: &[&str] = &[
        "rustusb",
        "rustusb_cli",
        "rustusb_daemon",
        "rustusb_service",
        "rustusb_native",
        "rustusb_linux",
        "rustusb_protocol",
        "rustusb_core",
        "rustusb_rust_backend",
        "rustusb_transport",
    ];
    target.len() <= 128
        && target
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || matches!(b, b'_' | b':'))
        && ROOTS.iter().any(|root| {
            target == *root
                || target
                    .strip_prefix(root)
                    .is_some_and(|suffix| suffix.starts_with("::"))
        })
}

fn profile_valid(profile: &str) -> bool {
    !profile.is_empty()
        && profile.len() <= 64
        && profile
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.'))
}

fn audit_valid(audit: &AuditProjection) -> Result<()> {
    if audit.audit_sequence == 0 || audit.profile.as_ref().is_some_and(|v| !profile_valid(v)) {
        return Err(Error::InvalidInput("audit sequence or profile"));
    }
    Ok(())
}

fn scalar_valid(field: Field, scalar: &Scalar) -> bool {
    if matches!(field, Field::Success | Field::NetworkClosed) {
        return matches!(scalar, Scalar::Bool(_));
    }
    let number = match scalar {
        Scalar::Unsigned(v) => *v,
        Scalar::Signed(v) => match u64::try_from(*v) {
            Ok(v) => v,
            Err(_) => return false,
        },
        Scalar::Bool(_) => return false,
    };
    match field {
        Field::Interface => number <= u64::from(u8::MAX),
        Field::Count | Field::DeviceCount | Field::SessionCount => number <= u64::from(u32::MAX),
        Field::BytesToPeer | Field::BytesFromPeer | Field::ElapsedMillis => true,
        Field::Success | Field::NetworkClosed => false,
    }
}

fn diagnostic_valid(diagnostic: &Diagnostic) -> Result<()> {
    if !approved_target(&diagnostic.target)
        || diagnostic.line == Some(0)
        || diagnostic
            .errno
            .is_some_and(|errno| !(1..=4095).contains(&errno))
        || diagnostic
            .fields
            .iter()
            .any(|(field, scalar)| !scalar_valid(*field, scalar))
    {
        return Err(Error::InvalidInput("diagnostic projection"));
    }
    Ok(())
}

pub(crate) fn status_valid(status: &LogStatus) -> bool {
    let loss_free = status.dropped == 0
        && status.oversized == 0
        && status.projection_errors == 0
        && !status.overflowed
        && status.failure.is_none();
    status.queued as usize <= QUEUE_CAPACITY
        && status.written <= status.accepted
        && (!status.healthy || loss_free)
}

impl LogRecord {
    /// Validate the closed record schema without interpreting an arbitrary message.
    ///
    /// This checks metadata only. A record never grants device access or proves cleanup.
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != 1 || self.sequence == 0 {
            return Err(Error::InvalidInput("record schema or sequence"));
        }
        match &self.event {
            Event::Started { pid, .. } if *pid == 0 => {
                Err(Error::InvalidInput("record process identity"))
            }
            Event::Started { .. } => Ok(()),
            Event::Diagnostic(diagnostic) => diagnostic_valid(diagnostic),
            Event::Audit(audit) => audit_valid(audit),
            Event::Stopped { status } => {
                if !status_valid(status) || !status.closing || status.queued != 0 {
                    return Err(Error::InvalidInput("stopped status"));
                }
                Ok(())
            }
        }
    }

    /// Encode a validated whole NDJSON line, including its bounded newline.
    pub fn encode_line(&self) -> Result<Vec<u8>> {
        encode_line(self)
    }

    /// Render a validated human observation using only static templates and safe metadata.
    ///
    /// No original tracing message, internal error, USB buffer or subprocess output exists
    /// in this value. Human rendering cannot recover omitted data.
    pub fn format_human(&self) -> Result<String> {
        self.validate()?;
        let level = match self.level {
            Level::Trace => "TRACE",
            Level::Debug => "DEBUG",
            Level::Info => "INFO",
            Level::Warn => "WARN",
            Level::Error => "ERROR",
        };
        let mut text = format!(
            "[{level}] epoch={} sequence={} elapsed_ms={} ",
            self.epoch, self.sequence, self.elapsed_millis
        );
        match &self.event {
            Event::Started { pid, uid } => {
                text.push_str(&format!("logger started pid={pid} uid={uid}"));
            }
            Event::Diagnostic(diagnostic) => {
                text.push_str(template(diagnostic.code));
                text.push_str(" target=");
                text.push_str(&diagnostic.target);
                if let Some(failure) = diagnostic.failure {
                    text.push_str(" failure=");
                    text.push_str(failure.as_str());
                }
                if let Some(errno) = diagnostic.errno {
                    text.push_str(&format!(" errno={errno}"));
                }
                if !diagnostic.fields.is_empty() {
                    let fields = serde_json::to_string(&diagnostic.fields)
                        .map_err(|_| Error::Worker("safe fields could not be encoded"))?;
                    text.push_str(" fields=");
                    text.push_str(&fields);
                }
                text.push_str(&format!(" omitted={}", diagnostic.omitted_fields));
            }
            Event::Audit(audit) => {
                text.push_str("committed application audit action=");
                text.push_str(action_name(audit.action));
                text.push_str(&format!(
                    " application_instance={} audit_sequence={} actor_uid={}",
                    audit.application_instance, audit.audit_sequence, audit.actor_uid
                ));
                if let Some(profile) = &audit.profile {
                    text.push_str(" profile=");
                    text.push_str(profile);
                }
                if let Some(job) = &audit.job_id {
                    text.push_str(" job=");
                    text.push_str(job.as_str());
                }
                if let Some(session) = &audit.session_id {
                    text.push_str(" session=");
                    text.push_str(session.as_str());
                }
                for (name, value) in [
                    ("state", serde_json::to_string(&audit.state)),
                    ("evidence", serde_json::to_string(&audit.evidence)),
                    ("cleanup", serde_json::to_string(&audit.cleanup)),
                ] {
                    let value = value
                        .map_err(|_| Error::Worker("safe audit category could not be encoded"))?;
                    if value != "null" {
                        text.push(' ');
                        text.push_str(name);
                        text.push('=');
                        text.push_str(&value);
                    }
                }
                if let Some(failure) = audit.failure {
                    text.push_str(" failure=");
                    text.push_str(failure.as_str());
                }
            }
            Event::Stopped { status } => text.push_str(&format!(
                "logger drained healthy={} accepted={} written={} dropped={} oversized={} projection_errors={}",
                status.healthy, status.accepted, status.written, status.dropped,
                status.oversized, status.projection_errors
            )),
        }
        text.push('\n');
        Ok(text)
    }
}

pub(crate) fn encode_line(record: &LogRecord) -> Result<Vec<u8>> {
    record.validate()?;
    let mut bytes = serde_json::to_vec(record)
        .map_err(|_| Error::Worker("safe log record could not be encoded"))?;
    if bytes.len() >= MAX_RECORD_BYTES {
        return Err(Error::InvalidInput("encoded record exceeds bound"));
    }
    bytes.push(b'\n');
    Ok(bytes)
}

fn template(code: EventCode) -> &'static str {
    match code {
        EventCode::DiscoveryCompleted => "device discovery completed",
        EventCode::OperationStarted => "operation started",
        EventCode::OperationCompleted => "operation completed",
        EventCode::OperationFailed => "operation failed",
        EventCode::NativeAttach => "Native import observation",
        EventCode::NativeDetach => "Native detach observation",
        EventCode::NativeExport => "Native export observation",
        EventCode::NativeUnexport => "Native unexport observation",
        EventCode::NetworkEnded => "network lifecycle ended",
        EventCode::ResourceReleased => "resource release observation",
        EventCode::Diagnostic => "restricted diagnostic observation",
    }
}

fn action_name(action: AuditAction) -> &'static str {
    match action {
        AuditAction::Start => "start",
        AuditAction::Denied => "denied",
        AuditAction::Stop => "stop",
        AuditAction::Admission => "admission",
        AuditAction::Attempt => "attempt",
        AuditAction::Resource => "resource",
        AuditAction::Observe => "observe",
        AuditAction::Finish => "finish",
        AuditAction::Recovery => "recovery",
        AuditAction::Discover => "discover",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{FailureCategory, LogId};
    use std::collections::BTreeMap;

    fn diagnostic() -> Result<LogRecord> {
        Ok(LogRecord {
            schema_version: 1,
            epoch: LogId::new("00000000-0000-0000-0000-000000000001")?,
            sequence: 1,
            elapsed_millis: 0,
            wall_unix_millis: None,
            level: Level::Error,
            event: Event::Diagnostic(Diagnostic {
                code: EventCode::OperationFailed,
                target: "rustusb_cli".into(),
                line: None,
                failure: Some(FailureCategory::Io),
                errno: Some(5),
                fields: BTreeMap::from([(Field::Count, Scalar::Unsigned(2))]),
                omitted_fields: 3,
            }),
        })
    }

    #[test]
    fn actual_whole_line_round_trip_and_safe_human_failure() -> Result<()> {
        let record = diagnostic()?;
        let bytes = record.encode_line()?;
        assert!(bytes.len() <= MAX_RECORD_BYTES);
        assert_eq!(bytes.last(), Some(&b'\n'));
        let decoded: LogRecord = serde_json::from_slice(&bytes)
            .map_err(|_| Error::Corrupt("test record did not decode"))?;
        decoded.validate()?;
        let human = decoded.format_human()?;
        assert!(human.contains("operation failed"));
        assert!(human.contains("failure=io errno=5"));
        assert!(!human.contains('\u{1b}'));
        Ok(())
    }

    #[test]
    fn rejects_target_spoof_control_text_and_wrong_scalar_types() -> Result<()> {
        let mut record = diagnostic()?;
        let Event::Diagnostic(ref mut diagnostic) = record.event else {
            return Err(Error::Worker("test expected diagnostic"));
        };
        diagnostic.target = "rustusb_cli_attacker".into();
        assert!(diagnostic_valid(diagnostic).is_err());
        diagnostic.target = "rustusb_cli\nsecret".into();
        assert!(diagnostic_valid(diagnostic).is_err());
        diagnostic.target = "rustusb_cli::safe_module".into();
        diagnostic
            .fields
            .insert(Field::Success, Scalar::Unsigned(1));
        assert!(diagnostic_valid(diagnostic).is_err());
        diagnostic.fields.insert(Field::Success, Scalar::Bool(true));
        diagnostic
            .fields
            .insert(Field::Interface, Scalar::Unsigned(256));
        assert!(diagnostic_valid(diagnostic).is_err());
        diagnostic
            .fields
            .insert(Field::Interface, Scalar::Unsigned(255));
        diagnostic_valid(diagnostic)
    }

    #[test]
    fn stopped_cannot_claim_healthy_with_loss_or_unwritten_success() -> Result<()> {
        let mut record = diagnostic()?;
        let mut status = LogStatus {
            healthy: true,
            closing: true,
            accepted: 1,
            written: 2,
            ..LogStatus::default()
        };
        record.event = Event::Stopped {
            status: status.clone(),
        };
        assert!(record.validate().is_err());
        status.written = 1;
        status.dropped = 1;
        record.event = Event::Stopped {
            status: status.clone(),
        };
        assert!(record.validate().is_err());
        status.healthy = false;
        record.event = Event::Stopped { status };
        record.validate()
    }
}

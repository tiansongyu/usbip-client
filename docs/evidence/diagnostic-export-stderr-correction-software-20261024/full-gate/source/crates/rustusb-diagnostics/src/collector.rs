//! Retained observation owners and the single bounded artifact lifecycle.

use crate::{
    artifact::{Builder, CaptureOutcome, CollectionFailure, SectionOutcome, Stage, Summary},
    native_probe,
    output::{FileFailure, FileFailureKind, FileReceipt, PreparedOutput, PublicationState},
};
use rustusb_core::Error;
use serde::Serialize;
use std::{
    path::PathBuf,
    time::{Duration, Instant},
};
use tokio::sync::watch;

#[cfg(all(feature = "secure-transport", target_os = "linux"))]
mod agent;

const BUDGET: Duration = Duration::from_secs(60);
const LOG_PAGES: usize = 32;

/// One protected public application manifest and exact authorized profile.
///
/// These inputs are never serialized in the artifact or receipt. This does not
/// load the broker's private credentials or grant access to its state files.
#[derive(Debug, Clone)]
pub struct AgentSource {
    /// Existing root-managed public manifest accepted by the application client.
    pub manifest: PathBuf,
    /// Exact configured profile, requiring both Read and Audit authorization.
    pub profile: String,
}

/// Explicit local inputs for one read-only diagnostic capture.
#[derive(Debug, Clone)]
pub struct ExportOptions {
    /// Absolute fresh final filename in an existing caller-owned 0700 directory.
    pub output: PathBuf,
    /// Existing root-owned helper socket, used only for its diagnostic query.
    pub helper_socket: PathBuf,
    /// Optional one-profile application history, health and projected logs.
    pub agent: Option<AgentSource>,
    /// Optional local private log history; mutually exclusive with `agent`.
    pub logs_directory: Option<PathBuf>,
}

/// Actual artifact work, separate from collection completeness or USB cleanup.
#[derive(Debug, Clone, Serialize)]
#[serde(tag = "result", rename_all = "snake_case")]
pub enum FileOutcome {
    /// Actual synchronized file work returned this receipt.
    Written {
        /// Private partial or final artifact result.
        receipt: FileReceipt,
    },
    /// Actual output failure, retaining known side effects.
    Failed {
        /// Fixed failure and observed publication state.
        failure: FileFailure,
    },
}

/// One bounded machine receipt emitted only after accepted work was joined.
#[derive(Debug, Clone, Serialize)]
pub struct ExportReceipt {
    /// Exactly one for this receipt schema.
    pub schema_version: u32,
    /// Fixed protocol event name.
    pub event: &'static str,
    /// Complete, partial or cooperatively cancelled capture.
    pub capture: CaptureOutcome,
    /// A stop request was observed, possibly after publication was accepted.
    pub cancel_observed: bool,
    /// Per-section observations; never a USB success or readiness assertion.
    pub summary: Summary,
    /// Actual output result, including failures after a possible rename.
    pub file: FileOutcome,
}

impl ExportReceipt {
    /// True only for a complete capture with confirmed final publication.
    pub fn is_complete(&self) -> bool {
        self.capture == CaptureOutcome::Complete
            && self.summary.complete
            && matches!(&self.file, FileOutcome::Written { receipt }
                if receipt.publication == PublicationState::Published)
    }
}

/// Collect private diagnostics without acquiring or stopping USB resources.
///
/// Set `stop` to true to stop accepting new stages/requests. The caller must keep
/// polling this future: accepted blocking work and Native child waits are always
/// joined. The soft collection budget cannot cancel an OS filesystem operation.
/// A cancellation observed before accepting the file write leaves a valid
/// cancelled partial; cancellation racing accepted publication preserves its
/// actual result. No arbitrary source error text is serialized.
pub async fn export(options: ExportOptions, stop: watch::Receiver<bool>) -> ExportReceipt {
    let started = Instant::now();
    let mut builder = requested_builder(&options);
    let supported_agent = cfg!(all(feature = "secure-transport", target_os = "linux"));
    if options.agent.is_some() && (options.logs_directory.is_some() || !supported_agent) {
        let failure = if options.logs_directory.is_some() {
            FileFailureKind::InvalidInput
        } else {
            FileFailureKind::Unsupported
        };
        return receipt(
            &builder,
            CaptureOutcome::Partial,
            *stop.borrow(),
            FileOutcome::Failed {
                failure: file_failure(failure, PublicationState::NotCreated),
            },
        );
    }
    let output_path = options.output.clone();
    let prepared =
        match tokio::task::spawn_blocking(move || PreparedOutput::prepare(&output_path)).await {
            Ok(Ok(prepared)) => prepared,
            Ok(Err(failure)) => {
                return receipt(
                    &builder,
                    CaptureOutcome::Partial,
                    *stop.borrow(),
                    FileOutcome::Failed { failure },
                );
            }
            Err(_) => {
                return receipt(
                    &builder,
                    CaptureOutcome::Partial,
                    *stop.borrow(),
                    FileOutcome::Failed {
                        failure: file_failure(FileFailureKind::Io, PublicationState::Unconfirmed),
                    },
                );
            }
        };

    if let Some(reason) = interruption(&stop, started) {
        remaining(&mut builder, reason, elapsed(started));
    } else {
        // This owner has its own runtime and explicitly reaps both fixed tools.
        // A stop request cannot drop its accepted work or its helper FS task.
        match native_probe::collect(options.helper_socket).await {
            Ok(observed) => builder.observe_local(observed, elapsed(started)),
            Err(error) => {
                let failure = core_failure(&error);
                for stage in [Stage::Native, Stage::Helper] {
                    builder.set_outcome(
                        stage,
                        SectionOutcome::Incomplete,
                        Some(failure),
                        elapsed(started),
                    );
                }
            }
        }
        if let Some(reason) = interruption(&stop, started) {
            remaining(&mut builder, reason, elapsed(started));
        } else if let Some(source) = options.agent {
            builder = agent_stage(builder, source, stop.clone(), started).await;
        } else if let Some(directory) = options.logs_directory {
            builder = local_logs(builder, directory, stop.clone(), started).await;
        }
    }

    let mut cancelled = *stop.borrow();
    let mut capture = if cancelled {
        CaptureOutcome::Cancelled
    } else if builder.summary().complete {
        CaptureOutcome::Complete
    } else {
        CaptureOutcome::Partial
    };
    let mut bytes = match builder.encode(capture) {
        Ok(bytes) => bytes,
        Err(_) => {
            // A bounded serialization failure discards projected payloads, not
            // the actual prepared owner. A minimal closed document explicitly
            // records every requested section as missing because of the limit.
            builder = limited_builder(builder.summary(), elapsed(started));
            capture = if cancelled {
                CaptureOutcome::Cancelled
            } else {
                CaptureOutcome::Partial
            };
            match builder.encode(capture) {
                Ok(bytes) => bytes,
                Err(_) => {
                    return receipt(
                        &builder,
                        capture,
                        cancelled,
                        finish_output(prepared, Vec::new(), false).await,
                    );
                }
            }
        }
    };
    if !cancelled && *stop.borrow() {
        cancelled = true;
        capture = CaptureOutcome::Cancelled;
        // Re-encode the same finite closed projection before accepting the
        // write, so the preserved partial cannot claim a completed capture.
        bytes.clear();
        bytes = match builder.encode(capture) {
            Ok(bytes) => bytes,
            Err(_) => {
                return receipt(
                    &builder,
                    capture,
                    true,
                    finish_output(prepared, Vec::new(), false).await,
                );
            }
        };
    }
    // No await between the cancellation decision and accepting this file owner.
    // Once accepted, a signal cannot undo its real rename or durability result.
    let publish = !cancelled;
    let file = finish_output(prepared, bytes, publish).await;
    receipt(&builder, capture, *stop.borrow(), file)
}

async fn finish_output(prepared: PreparedOutput, bytes: Vec<u8>, publish: bool) -> FileOutcome {
    match tokio::task::spawn_blocking(move || prepared.finish(&bytes, publish)).await {
        Ok(Ok(receipt)) => FileOutcome::Written { receipt },
        Ok(Err(failure)) => FileOutcome::Failed { failure },
        Err(_) => FileOutcome::Failed {
            failure: file_failure(FileFailureKind::Io, PublicationState::Unconfirmed),
        },
    }
}

fn requested_builder(options: &ExportOptions) -> Builder {
    let mut builder = Builder::new();
    for stage in [Stage::Native, Stage::Helper] {
        builder.set_outcome(
            stage,
            SectionOutcome::Incomplete,
            Some(CollectionFailure::Unavailable),
            0,
        );
    }
    if options.agent.is_some() {
        for stage in [Stage::Agent, Stage::Health, Stage::Logs] {
            builder.set_outcome(
                stage,
                SectionOutcome::Incomplete,
                Some(CollectionFailure::Unavailable),
                0,
            );
        }
    } else if options.logs_directory.is_some() {
        builder.set_outcome(
            Stage::Logs,
            SectionOutcome::Incomplete,
            Some(CollectionFailure::Unavailable),
            0,
        );
    }
    builder
}

fn limited_builder(summary: Summary, at: u64) -> Builder {
    let mut builder = Builder::new();
    for section in summary.sections {
        if section.outcome != SectionOutcome::NotRequested {
            builder.set_outcome(
                section.stage,
                SectionOutcome::Incomplete,
                Some(CollectionFailure::Limit),
                at,
            );
        }
    }
    builder
}

fn receipt(
    builder: &Builder,
    capture: CaptureOutcome,
    cancel_observed: bool,
    file: FileOutcome,
) -> ExportReceipt {
    ExportReceipt {
        schema_version: 1,
        event: "diagnostic_export",
        capture,
        cancel_observed,
        summary: builder.summary(),
        file,
    }
}

fn file_failure(kind: FileFailureKind, publication: PublicationState) -> FileFailure {
    FileFailure {
        kind,
        errno: None,
        publication,
        partial_name: None,
        written_bytes: 0,
        sha256: None,
    }
}

#[derive(Clone, Copy)]
enum Interrupted {
    Cancelled,
    Failed(CollectionFailure),
}

fn interruption(stop: &watch::Receiver<bool>, started: Instant) -> Option<Interrupted> {
    if *stop.borrow() {
        Some(Interrupted::Cancelled)
    } else if started.elapsed() >= BUDGET {
        Some(Interrupted::Failed(CollectionFailure::Timeout))
    } else {
        None
    }
}

fn elapsed(started: Instant) -> u64 {
    started.elapsed().as_millis().min(u128::from(u64::MAX)) as u64
}

fn finish_stage(builder: &mut Builder, stage: Stage, reason: Interrupted, started: Instant) {
    let (outcome, failure) = match reason {
        Interrupted::Cancelled => (SectionOutcome::Cancelled, None),
        Interrupted::Failed(failure) => (SectionOutcome::Incomplete, Some(failure)),
    };
    builder.set_outcome(stage, outcome, failure, elapsed(started));
}

fn remaining(builder: &mut Builder, reason: Interrupted, at: u64) {
    for section in builder.summary().sections {
        if section.outcome == SectionOutcome::Incomplete {
            let (outcome, failure) = match reason {
                Interrupted::Cancelled => (SectionOutcome::Cancelled, None),
                Interrupted::Failed(failure) => (SectionOutcome::Incomplete, Some(failure)),
            };
            builder.set_outcome(section.stage, outcome, failure, at);
        }
    }
}

fn core_failure(error: &Error) -> CollectionFailure {
    match error {
        Error::InvalidInput(_) => CollectionFailure::InvalidInput,
        Error::Permission(_) => CollectionFailure::Permission,
        Error::Io(_) => CollectionFailure::Io,
        Error::Protocol(_) => CollectionFailure::Corrupt,
        Error::Timeout(_) => CollectionFailure::Timeout,
        Error::Unsupported(_) => CollectionFailure::Unsupported,
        Error::State(_) | Error::UnsafeDevice(_) | Error::Command(_) => {
            CollectionFailure::Unconfirmed
        }
    }
}

fn log_failure(error: &rustusb_logging::Error) -> CollectionFailure {
    use rustusb_logging::Error as E;
    match error {
        E::InvalidInput(_) => CollectionFailure::InvalidInput,
        E::Permission(_) => CollectionFailure::Permission,
        E::Corrupt(_) => CollectionFailure::Corrupt,
        E::Io(_) => CollectionFailure::Io,
        E::Busy => CollectionFailure::Busy,
        E::Disabled => CollectionFailure::Disabled,
        E::Unsupported => CollectionFailure::Unsupported,
        E::Worker(_) => CollectionFailure::Worker,
        E::LostRecords => CollectionFailure::Unconfirmed,
    }
}

async fn local_logs(
    builder: Builder,
    directory: PathBuf,
    stop: watch::Receiver<bool>,
    started: Instant,
) -> Builder {
    // The entire synchronous reader loop has one retained actual owner. Its
    // fresh access OFDs and page allocations never occupy a Tokio worker.
    let task = tokio::task::spawn_blocking(move || {
        let mut builder = builder;
        let mut after = None;
        for index in 0..LOG_PAGES {
            if let Some(reason) = interruption(&stop, started) {
                finish_stage(&mut builder, Stage::Logs, reason, started);
                return builder;
            }
            let page = match rustusb_logging::read_directory(&directory, after.clone(), 16) {
                Ok(page) => page,
                Err(error) => {
                    finish_stage(
                        &mut builder,
                        Stage::Logs,
                        Interrupted::Failed(log_failure(&error)),
                        started,
                    );
                    return builder;
                }
            };
            if let Err(error) = builder.observe_logs(&page, elapsed(started)) {
                finish_stage(
                    &mut builder,
                    Stage::Logs,
                    Interrupted::Failed(core_failure(&error)),
                    started,
                );
                return builder;
            }
            if !page.more {
                builder.set_outcome(
                    Stage::Logs,
                    SectionOutcome::Complete,
                    None,
                    elapsed(started),
                );
                return builder;
            }
            if page.next_after.is_none() || page.next_after == after {
                finish_stage(
                    &mut builder,
                    Stage::Logs,
                    Interrupted::Failed(CollectionFailure::Corrupt),
                    started,
                );
                return builder;
            }
            after = page.next_after;
            if index + 1 == LOG_PAGES {
                finish_stage(
                    &mut builder,
                    Stage::Logs,
                    Interrupted::Failed(CollectionFailure::Limit),
                    started,
                );
            }
        }
        builder
    });
    match task.await {
        Ok(builder) => builder,
        Err(_) => failed_builder(CollectionFailure::Worker, started),
    }
}

// A panic in an owned observer cannot provide the lost builder's observations.
// Return explicit unavailable sections rather than inventing an empty success.
fn failed_builder(failure: CollectionFailure, started: Instant) -> Builder {
    let mut builder = Builder::new();
    for stage in [
        Stage::Native,
        Stage::Helper,
        Stage::Agent,
        Stage::Health,
        Stage::Logs,
    ] {
        builder.set_outcome(
            stage,
            SectionOutcome::Incomplete,
            Some(failure),
            elapsed(started),
        );
    }
    builder
}

#[cfg(all(feature = "secure-transport", target_os = "linux"))]
async fn agent_stage(
    builder: Builder,
    source: AgentSource,
    stop: watch::Receiver<bool>,
    started: Instant,
) -> Builder {
    let task = tokio::task::spawn_blocking(move || {
        let runtime = match tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .max_blocking_threads(4)
            .build()
        {
            Ok(runtime) => runtime,
            Err(_) => return failed_builder(CollectionFailure::Worker, started),
        };
        let builder = runtime.block_on(agent::collect(builder, source, stop, started));
        // Normal destruction joins any accepted manifest filesystem work even
        // when an IPC await was interrupted. No shutdown timeout/background.
        drop(runtime);
        builder
    });
    match task.await {
        Ok(builder) => builder,
        Err(_) => failed_builder(CollectionFailure::Worker, started),
    }
}

#[cfg(not(all(feature = "secure-transport", target_os = "linux")))]
async fn agent_stage(
    mut builder: Builder,
    _source: AgentSource,
    _stop: watch::Receiver<bool>,
    started: Instant,
) -> Builder {
    for stage in [Stage::Agent, Stage::Health, Stage::Logs] {
        finish_stage(
            &mut builder,
            stage,
            Interrupted::Failed(CollectionFailure::Unsupported),
            started,
        );
    }
    builder
}

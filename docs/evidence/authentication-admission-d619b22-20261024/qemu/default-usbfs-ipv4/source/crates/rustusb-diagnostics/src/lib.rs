//! Private, bounded diagnostic observations shared by RustUSB frontends.
//!
//! Collection never owns USB resources or supplies a cleanup/admission proof.
//! Only the closed artifact projection is serialized; credentials, identifiers,
//! paths, arbitrary messages and raw USB contents are excluded.

mod artifact;
mod collector;
mod native_probe;
mod output;

/// Maximum complete serialized diagnostic document, including metadata.
pub const MAX_EXPORT_BYTES: usize = 4 * 1024 * 1024;

pub use artifact::{
    CaptureOutcome, CollectionFailure, SectionOutcome, SectionSummary, Stage, Summary,
};
pub use collector::{AgentSource, ExportOptions, ExportReceipt, FileOutcome, export};
pub use output::{FileFailure, FileFailureKind, FileReceipt, PublicationState};

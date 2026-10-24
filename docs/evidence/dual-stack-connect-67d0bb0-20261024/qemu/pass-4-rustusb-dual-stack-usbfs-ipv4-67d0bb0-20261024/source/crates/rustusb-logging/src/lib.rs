//! Private bounded process logs; these records never authorize USB operations.

mod console;
mod record;
mod storage;
mod types;
mod worker;

pub use storage::read_directory;
pub use types::*;
pub use worker::{LogMonitor, LogOwner, initialize};

/// Maximum encoded complete NDJSON record including its newline.
pub const MAX_RECORD_BYTES: usize = 2048;
/// Fixed nonblocking record admission capacity.
pub const QUEUE_CAPACITY: usize = 256;
/// Maximum records returned by one page.
pub const MAX_PAGE_RECORDS: u8 = 16;
/// Maximum record bytes scanned by one page, including filtered observations.
pub const MAX_SCAN_BYTES: usize = 256 * 1024;
/// Maximum permitted individual segment size.
pub const MAX_SEGMENT_BYTES: u64 = 8 * 1024 * 1024;
/// Maximum number of retained segments, including the active slot.
pub const MAX_SEGMENTS: u8 = 16;

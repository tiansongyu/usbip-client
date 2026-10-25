//! Shared headless ordinary-user frontend worker, observations and checked preferences.
//! This module owns metadata observers and IPC work, never USB resources.
#![cfg(target_os = "linux")]

mod preferences;
pub use preferences::Settings;
/// Automatic-rule observations and separately retained edit identity.
pub mod automation;
/// Finite commands and latest metadata shared by native frontends.
pub mod state;
/// Actual observer/IPC worker, which callers must stop and join.
pub mod worker;

/// Frontend preferences; never administrator policy or automatic device actions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Options {
    /// Seconds after a completed refresh, in 2..=120.
    pub refresh_seconds: u64,
    /// Poll the selected profile automatically.
    pub auto_refresh: bool,
    /// Use text emphasis without application colors.
    pub monochrome: bool,
}
impl Default for Options {
    fn default() -> Self {
        Self {
            refresh_seconds: 2,
            auto_refresh: true,
            monochrome: false,
        }
    }
}

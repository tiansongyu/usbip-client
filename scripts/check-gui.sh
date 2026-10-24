#!/usr/bin/env bash
# Explicit separate-workspace gate; root core checks must not silently stand in for GUI checks.
set -euo pipefail
cd "$(dirname "$0")/../apps/rustusb-gui"
cargo fmt --all -- --check
cargo clippy --all-targets --locked -- -D warnings
cargo test --locked
cargo build --release --locked
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps --locked
node --check web/app.js

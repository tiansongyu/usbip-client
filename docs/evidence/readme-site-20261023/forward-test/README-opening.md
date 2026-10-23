# RustUSB

[![Build](https://github.com/tiansongyu/usbip-client/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/tiansongyu/usbip-client/actions/workflows/ci.yml)
[![Rust](https://img.shields.io/badge/Rust-1.97.1-orange?logo=rust)](rust-toolchain.toml)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Manage USB/IP devices from a Linux command line.** Discover devices, authorize exports and control connections with RustUSB's CLI and restricted privileged helper.

[Get started](#build-from-source) · [Native setup](docs/native-guide.md) · [Compatibility](docs/compatibility.md) · [Security](docs/security.md) · [中文](README.zh-CN.md)

**Development preview.** Linux Native is the default. Optional Rust services provide fixed virtual HID/CDC ACM test devices and an experimental USBFS adapter. QEMU validation uses simulated devices; physical hardware acceptance remains pending.

## Build from source

Use Linux with Git, native build tools and Rust through rustup. The repository pins Rust 1.97.1.

```sh
git clone https://github.com/tiansongyu/usbip-client.git
cd usbip-client
cargo build --workspace --release --locked
./target/release/rustusb --help
```

From this checkout, start with read-only discovery and diagnostics:

```sh
./target/release/rustusb devices --json
./target/release/rustusb doctor
```

`devices` reads local USB metadata. `doctor` reports tool, module and helper availability; a successful diagnostic exit does not certify a working USB connection.

Actual sharing requires administrator-configured helpers, matching Linux USB/IP tools and modules, and an externally protected network. RustUSB does not provide built-in TLS or remote authentication. Follow the [Native setup guide](docs/native-guide.md) before exporting a dedicated test device.

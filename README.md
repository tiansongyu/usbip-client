# RustUSB

[![Build](https://github.com/tiansongyu/usbip-client/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/tiansongyu/usbip-client/actions/workflows/ci.yml)
[![Rust](https://img.shields.io/badge/Rust-1.97.1-orange?logo=rust)](rust-toolchain.toml)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-development%20preview-yellow)](docs/roadmap.md)

**USB devices, across Linux machines.** RustUSB manages USB/IP discovery, device permissions and connections through a CLI and a restricted privileged helper.

[Website](https://tiansongyu.github.io/usbip-client/) · [Get started](#build-from-source) · [Native guide](docs/native-guide.md) · [Compatibility](docs/compatibility.md) · [中文](README.zh-CN.md)

[![RustUSB device flow: a USB device on the server reaches a Linux client through USB/IP over an administrator-managed network](website/assets/usbip-flow.svg)](https://tiansongyu.github.io/usbip-client/)

RustUSB is a **development preview**. Linux Native is the default backend; optional Rust services provide fixed virtual test devices and an experimental USBFS adapter. QEMU tests exercise real Linux USB/IP paths with simulated devices. Physical hardware acceptance is still pending.

## Build from source

Use Linux with Git, native build tools and [Rust via rustup](https://rustup.rs/). The repository pins Rust 1.97.1. Ubuntu 22.04/24.04 on x86_64 and aarch64 have native software CI; USB kernel integration has been tested in x86_64 QEMU guests.

```sh
git clone https://github.com/tiansongyu/usbip-client.git
cd usbip-client
cargo build --workspace --release --locked
./target/release/rustusb --help
```

This produces `rustusb` and `rustusb-helper` in `target/release/`. Start with read-only checks:

```sh
./target/release/rustusb devices
./target/release/rustusb devices --json
./target/release/rustusb doctor
```

`devices` lists local USB metadata without changing drivers. `doctor` reports installed tools, modules and helper availability; it does not load modules, start services or test remote USB I/O. Read its individual results: exit code 0 means the diagnostic completed, not that the machine is ready to share devices.

For Native sharing, administrators must install the distribution's `usbip` / `usbipd` and matching kernel modules. On Ubuntu these tools are usually provided by `linux-tools-common` and `linux-tools-$(uname -r)`. The [Native setup guide](docs/native-guide.md) covers binary installation, helper policy, directory permissions and service setup.

## Use

Run the CLI as an ordinary user. The root helper performs only policy-approved privileged operations. Before sharing, configure both helpers and an administrator-managed VPN or secure tunnel with firewall rules for the permitted peers. USB/IP itself has no authentication or encryption.

After completing the [Native setup](docs/native-guide.md), the basic workflow is:

```sh
# Server: use the approved fingerprint from `rustusb devices --json`.
rustusb export 'usb-<64-lowercase-hex-fingerprint>'

# Client: use the configured server and the Bus ID returned by discover.
rustusb discover 192.0.2.1 --port 3240 --json
rustusb attach 192.0.2.1 1-2 --port 3240
rustusb sessions
rustusb status
```

The address and Bus ID above are examples. Use your allowed endpoint and discovered device. Verify client-side USB enumeration and the device's actual function before treating the connection as working.

Stop applications using the device, then release the connection:

```sh
# Client: replace 0 with the port reported by `rustusb sessions`.
rustusb detach 0

# Server: after confirming the client has released the device.
rustusb unexport 'usb-<64-lowercase-hex-fingerprint>'
```

Each command has `--help`. Use `--json` for machine-readable output and `--log-json` for JSON tracing on stderr; `RUST_LOG` controls log verbosity. Device JSON may include serial numbers—redact it before sharing.

A device fingerprint is a candidate identity derived from descriptors, serial number when available and physical topology. A Bus ID is a current address. Neither is hardware authentication; a replacement without a serial number may have the same fingerprint.

## Choose a backend

| Backend | Device source and data path | Status |
| --- | --- | --- |
| **Linux Native** | Local devices through Linux `usbip_host` / `usbipd`; imports through `vhci_hcd` | Default build; QEMU I/O and official Linux tool interoperability tested |
| **Rust virtual** | Fixed virtual HID keyboard and CDC ACM echo serial profiles | Optional `rust-backend` feature; Linux client enumeration and I/O tested in QEMU |
| **Rust USBFS** | Authorized whole-device file descriptors passed by the helper to a userspace service | Experimental `physical-nusb` feature; simulated HID, FTDI and ECM tested through Linux USBFS |

All three paths use Linux VHCI on the importing side. The Rust services do not require `usbipd` or `usbip_host` on their server side. A physical device must use one export backend at a time.

To try the virtual service locally:

```sh
cargo build -p rustusb-cli --features rust-backend --release --locked
./target/release/rustusb virtual-server --control-stdin
```

It listens on `127.0.0.1:3240` by default and accepts JSONL controls on stdin. Enter `{"id":"done","op":"shutdown"}` to stop. Importing a device still requires a configured Linux client. See the [virtual server guide](docs/rust-virtual-server.md) for test input and network options.

For USBFS, build both the service and helper with their optional features:

```sh
cargo build --workspace --release --all-features --locked
./target/release/rustusb physical-server --help
```

Follow the [physical server guide](docs/rust-physical-server.md) to authorize exact device fingerprints. Its persistent grant journal preserves unresolved original state across helper restarts and blocks new grants until resolved. Root-only reconciliation verifies administrator-restored state. A separate, default-off policy enables original-driver recovery after the owner exits, limited to the saved generation, configuration and alternate settings.

## What is verified

The latest [validation report](docs/research/physical-driver-autorestore.md) records:

- **268 software test executions** across the default, all-features and vendored suites, plus formatting, Clippy, release builds and documentation checks.
- **Seven QEMU scenarios, fourteen successful guest exits**, covering opt-in recovery after service death and helper restart, the default manual lifecycle, HID/FTDI normal operation and hot unplug, ECM restoration, and SIGTERM cleanup.
- **Four native CI combinations**: Ubuntu 22.04/24.04 × x86_64/aarch64, each with 268 test executions, signal and output-backpressure checks.

Three additional [recovery boundary scenarios](docs/research/physical-driver-autorestore-negatives.md) passed on `4565f7a`, including real legacy-helper upgrade, revoked recovery policy and an external interface owner.

The new [TLS foundation library](docs/research/secure-transport-foundation.md) passes 18 transport tests; the full software suite now runs 304 test executions. It has not yet been connected to the USB/IP commands or device data path.

The report links the tested revision and raw evidence. [Native interoperability](docs/research/native-qemu-matrix.md) and [virtual device tests](docs/research/userspace-qemu.md) have their own recorded baselines. These results establish the stated simulated-device behavior; real-device compatibility and two-machine hardware acceptance remain unverified.

## Current boundaries

- **Network protection is external.** The USB/IP commands have no built-in TLS or remote identity authentication. `trusted_network: true` records an administrator's declaration; it does not create a secure channel. Restrict both IPv4 and IPv6 access before starting `usbipd`, which listens on wildcard addresses.
- **Use dedicated test devices.** Storage, hubs and incomplete or unsafe metadata are rejected. Do not export your active keyboard, management network adapter or production equipment.
- **Recovery has explicit limits.** The optional Rust services support cooperative shutdown; USBFS also protects unresolved grants. Opt-in recovery has two QEMU positive scenarios and three additional [upgrade, revocation and external-claim checks](docs/research/physical-driver-autorestore-negatives.md). These validate fixed simulated-device cases. General crash recovery, arbitrary FD revocation and power-loss durability testing remain unfinished.
- **Compatibility is device-specific.** The experimental USBFS backend does not support isochronous transfers. QEMU results do not establish arbitrary HID, serial, composite, camera or audio support, or aarch64 USB kernel behavior.
- **More product work remains.** TUI, GUI, packaged installers, Windows USB support and the full hardware/security acceptance gates are on the [roadmap](docs/roadmap.md).

Unknown or externally created sessions are not automatically adopted. Failed or cancelled operations may have side effects; inspect current device, session and kernel state before retrying. See the [security model](docs/security.md) and [Native guide](docs/native-guide.md) for ownership and recovery rules.

## Documentation

| Start here | Details |
| --- | --- |
| [Native setup and diagnostics](docs/native-guide.md) | Helper deployment, policy, sharing and troubleshooting |
| [Virtual server](docs/rust-virtual-server.md) / [USBFS server](docs/rust-physical-server.md) | Optional service setup, controls and lifecycle boundaries |
| [Compatibility](docs/compatibility.md) / [acceptance](docs/acceptance.md) | Tested environments, device scope and unfinished gates |
| [Testing](docs/testing.md) | Software checks, isolated QEMU runners and hardware procedures |
| [Architecture](docs/architecture.md) / [protocol](docs/protocol.md) | Component boundaries and USB/IP behavior |
| [Work log](docs/work-log.md) / [requirements](docs/requirements.md) | Development history and the full project scope |

Detailed guides and research notes are currently primarily in Chinese.

## Contributing

Read [AGENTS.md](AGENTS.md) and the [development skill](.agents/skills/rustusb-development/SKILL.md) before changing USB/IP, device or privileged code. Keep changes focused, document behavior and limits, and preserve reproducible evidence for device-related fixes.

```sh
scripts/check.sh
```

This runs default and all-features checks plus the vendored library tests. QEMU prerequisites and commands are in the [testing guide](docs/testing.md). Report the backend, OS, kernel, device model and reproduction steps in [an issue](https://github.com/tiansongyu/usbip-client/issues); remove serial numbers and private USB payloads from public logs.

## License

RustUSB's own code is [MIT licensed](LICENSE). External Linux tools and drivers retain their own GPL licenses; dependencies and borrowed material are documented in [third-party notices](docs/third-party.md).

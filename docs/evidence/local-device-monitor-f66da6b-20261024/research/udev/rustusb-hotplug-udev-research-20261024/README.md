# Linux hotplug library research — 2026-10-24

## Decision support

Retain the project's existing Rustix 1.1.5 for a safe kernel-event FD owner and authoritative sysfs re-scan. Do not add libudev merely to receive advisory events. This is a design recommendation, not a product or hotplug acceptance result.

## Fixed components and provenance

- `udev` **0.9.3**, published 2025-01-23, MIT; crate checksum `af4e37e9ea4401fc841ff54b9ddfc9be1079b1e89434c1a6a865dd68980f7e9f`, VCS `ba521299ca8462e4b09df577fc46fbcacb439c87`. Current crates.io metadata and source agree. No `rust-version` declared; observed compilation uses Rust 1.97.1, without a broader MSRV claim.
- `tokio-udev` **0.10.0**, published 2025-10-08, MIT OR Apache-2.0, VCS `4dcaf26bda5dcf2511988c9a452b4dd8c56400f7`. Source archive and both licenses retained.
- Systemd upstream **v249** = `f6278558da0304ec6b646bb172ce4688c7f162a5`; **v255** = `db11bab38ccf1ed257f310d29070843d4c58ea01`. Fixed monitor sources and LGPL-2.1 license included. These are upstream base versions, not audits of Ubuntu's complete patch sets.
- Actual host libudev1/libudev-dev **249.11-0ubuntu3.21 amd64**, pkg-config `249`. Ubuntu primary package search lists Jammy/Noble arm64 availability; direct package-page fetches timed out/returned 500 and are retained. Neither Noble nor arm64 execution is claimed.

## APIs and important rejection reasons

`MonitorBuilder::new()` selects processed udev messages; `new_kernel()` selects kernel messages. Apply `match_subsystem("usb")` before `listen()`; restricting to `usb_device` excludes interface bind/unbind observations. Listen before initial enumeration, then drain/re-scan to avoid a startup blind interval. Kernel events precede udev processing and sysfs settling: events are advisory triggers, never proof of complete device metadata.

Default monitor/device types are not Send/Sync. Crate `send`/`sync` features impose implementation assumptions; they are unnecessary when values are created, used and dropped on one dedicated thread. Adding libudev also adds a Linux development/link dependency (`libudev-dev`, pkg-config; libudev runtime).

`MonitorSocketIter::next` returns `Option<Event>` and collapses all null receives. Pinned source makes an immediate same-thread `io::Error::last_os_error()` possible: no libc call occurs between null result and return. Systemd propagates negative receive errors to errno. This source-dependent adapter is avoidable with direct Rustix. `tokio-udev` discards those errors and calls mutex `unwrap`; its error stream is insufficient for explicit ENOBUFS recovery.

Pinned Tokio **1.54.1** makes `AsyncFd::new` deprecated as unsound; replacement `AsyncFd::register` requires unsafe. Neither satisfies the project's warning-strict/no-core-unsafe requirement. Safe `rustix::event::{poll, PollFd, PollFlags, Timespec}` on the owner thread avoids this issue and existing dependency expansion.

Netlink can lose events; ENOBUFS requires re-synchronization. Filtered sequence gaps alone do not prove loss. Bound draining, queue storage and re-scan work; mark truncation/error/queue loss explicitly and recover from authoritative snapshots. Never log raw event properties/serials or use an event payload to authorize USB changes.

## Actual probes and limitations

UID1000 probe created both monitors with NONBLOCK/CLOEXEC (`02004002`), observed empty receive errno11/WouldBlock, bounded readiness timeouts and joined its owner thread. Deprecated AsyncFd warning retained.

Safe Rustix/kernel monitor probe passed without unsafe/warnings: three finite poll calls, zero observed events, explicit socket drop and actual owner-thread join; stop-to-join observed 10,246µs is not a guarantee. No host event was injected and no hotplug transition/overflow was tested. First preparation failed on unavailable Python3.10 tomllib (transcription); first poll compile failed on feature-gated `rustix::time` (raw retained), corrected to `event::Timespec`.

## Primary references

- [udev fixed monitor source](https://github.com/Smithay/udev-rs/blob/ba521299ca8462e4b09df577fc46fbcacb439c87/src/monitor.rs)
- [udev fixed README/dependencies/thread assumptions](https://github.com/Smithay/udev-rs/blob/ba521299ca8462e4b09df577fc46fbcacb439c87/README.md)
- [Systemd v249 libudev source](https://github.com/systemd/systemd/blob/f6278558da0304ec6b646bb172ce4688c7f162a5/src/libudev/libudev-monitor.c)
- [Netlink manual, loss and unprivileged receive](https://man7.org/linux/man-pages/man7/netlink.7.html)
- [Official libudev receive reference](https://www.freedesktop.org/software/systemd/man/259/udev_monitor_receive_device.html)

Full dependency source crates, provenance metadata, licenses, source-selected Tokio/Rustix files, original failures and actual probe stdout/stderr are in this directory. `handoff.json` excludes probe target/ELF bodies; it does not assert independent security review or product test counts.

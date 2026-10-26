# ARM64 virtual HID and CDC data validation

Status: proposed, 2026-10-26. Starting source: `d5b5ad47`.

The [import power validation](native-import-power-validation.md) covers ARM64
Native and USBFS traffic. Its virtual HID/CDC regressions execute x86_64 binaries.
This fixture will exercise the packaged ARM64 Rust virtual server and Linux VHCI
client in two full Ubuntu guests. It changes test infrastructure, not product
Rust, package policy or the existing physical-device assertions.

## Scope and existing contracts

Use the existing `scripts/qemu_arm64/arm_vm.py` platform and disposable,
product-free Jammy/Noble prepared disks. IPv4 runs Jammy server/Noble client;
IPv6 reverses those roles. Each VM retains its original owner through actual
poweroff and wait. Use the native ARM CI `ed1e8fef` Jammy package already verified
in E63, with its original artifact/package/ELF provenance. Reuse of these binaries
does not create a new compilation or software-test result.

No emulated source keyboard, FTDI adapter or xHCI controller is needed: the server
implements the two virtual profiles in Rust, and the client uses its actual
`vhci_hcd`, `usbhid` and `cdc_acm` drivers. Guest-root test setup provisions private
credentials and the existing helper policy. Actual product children run under
the package's dynamically resolved, unprivileged `rustusb` UID. A guest fixture
retains and waits for each child; test control travels over the private management
SSH connection. No host USB, module, account or system service changes are needed.

The foreground `secure-server --backend virtual --control-stdin` path exposes
the existing `hid_key`, `cdc_emit` and `snapshot` controls. `secure-attach` retains
the existing shared foreground owner and helper import channel. Unlike managed
application jobs, this mode has no durable application correlation. Verify its
actual channel/target/owner/receipt and kernel cleanup; do not invent an application
WAL or Root terminal correlation. Sources are `scripts/qemu/guest-secure-init.sh`,
`scripts/qemu/run-secure-qemu.py`, `crates/rustusb-service/src/jobs/rust_server.rs`
and `crates/rustusb-service/src/jobs/client.rs`.

## Required observations

1. Record actual aarch64 kernel, arm64 package, systemd PID1, installed and live
   executable hashes, broker UID, protected configuration and helper identity.
   Give each guest only its own private TLS key. Check IPv6 DAD and source route
   before starting the product capture window.
2. Require an authenticated catalog containing exactly `virtual:hid` and
   `virtual:cdc-acm`. Require plaintext port 3240 refusal on both address families.
3. Run two concurrent-device cycles under the same server process. Each import
   must return one observed USED receipt, a distinct channel and the expected
   VID/PID, interfaces, native drivers and device nodes. Sample pinned kernel
   generation and power attributes. A competing import must fail with unchanged
   existing channel records and VHCI rows.
4. Open the actual event and ACM nodes before injection. For each cycle, require
   the exact HID key press and release, 35 injected CDC bytes, 35 client-to-server
   bytes and their exact 35-byte echo. Check the corresponding virtual counters,
   configuration and leases. Bound reads and keep raw fixture bytes as evidence.
5. Signal and actually wait for both original client children. Require their
   successful cleanup events, no helper channel records/exports, all VHCI ports
   NULL and absence of both prior local generations. Require empty virtual queues,
   zero pending URBs, released leases and catalog reuse before the second cycle.
   Stop and wait for the same original server; check its final cleanup snapshot.
6. Keep boot and product-window PCAPs from both ends. Apply the existing strict
   packet classifier with framed-v2 ALPN, retaining its original TCP, TLS and
   plaintext USB/IP predicates. A failed assertion remains a failed trial even
   when both VMs subsequently exit zero.

Freeze HEAD, tracked inputs, selected package, tool/firmware and all three host
release ELFs for each live run. Preserve original failures, methods and actual
wait results. Archive an explicit public-file list; exclude private keys, signed
download URLs, VM disks, firmware, packages and executable binaries. Document
actual source identities separately from this fixture commit.

This is an ARM64 virtual-profile test, not real hardware G1/G2, managed-job or
package-upgrade coverage, a performance result, full fault coverage or completion
of Phase 2 or the full project.

## Installation readiness after the first IPv6 trial

At clean `1bb65475`, IPv4 passed the full fixture. The first IPv6 trial failed
before client installation: `dpkg` returned 2 because PID 1268 held its frontend
lock. Both VMs subsequently exited zero. The recorded output identifies the
conflicting PID, but not the process's executable or the reason it acquired the
lock; the later guest shutdown cannot reconstruct that evidence.

Add a guest-only readiness step before the unchanged `dpkg -i` command. Stop the
two package-maintenance timers to prevent a new scheduled invocation, then wait
for any already-running maintenance services and actual POSIX dpkg locks to
retire. Record each observation, the locks' current owners and process metadata.
Do not remove lock files, signal their owners, retry a failed installation or
change USB/product observation budgets. A separate 120-second readiness limit
is a failed precondition if exceeded. The original failed trial remains intact;
the new IPv6 run uses a new output directory and its own exact source identity.

# ARM64 virtual HID and CDC data validation

2026-10-26. Two paired QEMU trials pass actual virtual HID/CDC traffic over
framed-v2 mutual TLS on ARM64 Ubuntu 22.04 and 24.04. IPv4 and IPv6 reverse the
server/client roles. A separate first IPv6 trial failed during package installation
and remains failed. These results cover the two fixed virtual profiles, not real
USB hardware or completion of the full project.

[Design](arm64-virtual-data-design.md) ·
[Original summaries](../evidence/arm64-virtual-data-20261026/summary.json) ·
[File manifest](../evidence/arm64-virtual-data-20261026/originals-manifest.json) ·
[Reproduction](../../scripts/qemu_arm64/README.md)

## Executed source and environment

| Trial | Fixture commit | Server → client | Result |
| --- | --- | --- | --- |
| IPv4 | `1bb65475a642f08c48abe1ff696f3a73cac3063a` | Jammy → Noble | PASS |
| First IPv6 | `1bb65475a642f08c48abe1ff696f3a73cac3063a` | Noble → Jammy | FAIL before client installation |
| IPv6 with installation readiness | `3fc08f9c2366ea18deb76b5f25e2ff5c7bd9e317` | Noble → Jammy | PASS |

All trials use the original native ARM CI package
`rustusb_0.1.0~ci31+ged1e8fef84c0-1_arm64.deb`, built from clean
`ed1e8fef84c096feaa337484a4b75f145d6db278`. Its package SHA-256 is
`5908721749ad41dfcbfa66300edd3a8b46d82e75364b63625cc1054caca74980`.
The CLI hash is `fcd4b2d35c3ed4c88d1ee284302d44c117e555b995248f9254c63136d5795993`;
the helper hash is `a3b8f928c9e23e40b3d149a93ef419bb7a09ae5fb1d5d1b71ad9487a9b00e4f0`.
Installed ELF headers and live process hashes match that receipt. No product Rust,
Cargo or vendor source changed. The existing **1138 software test executions**
belong to the [E63 gates](native-import-power-validation.md); they were not rerun
or recounted for this fixture-only change.

The guests execute actual aarch64 kernels `5.15.0-198-generic` and
`6.8.0-147-generic` under QEMU 6.2, `virt-6.2`, cortex-a72 and TCG. Each has two
vCPUs and 2 GiB RAM. These are copy-on-write children of the previously verified
product-free prepared systems, not new cold-installation or physical-board tests.
The server needs no source USB device or xHCI controller. Its virtual profiles
are Rust implementations; the client uses its actual Linux VHCI and native drivers.

Each trial freezes 742 tracked non-evidence inputs, package/build receipts,
QEMU/firmware, prepared parents and all three host release ELFs. All guards pass.
The package's broker UID is read dynamically: 112 on Jammy and 108 on Noble.
Product children run under that UID; only the restricted helper and disposable
test setup run as root. Each guest receives only its own private TLS key.

## Observed data and cleanup

Each successful pair performs two cycles with HID and CDC imported concurrently
under the same original server process. The combined evidence contains:

- Four exact HID key press/release pairs through real `/dev/input/event*` nodes.
- 140 bytes injected into CDC, 140 bytes sent by the clients, and their exact
  140-byte echoes through real `/dev/ttyACM*` nodes; each transfer is 35 bytes.
- Eight distinct import channel IDs, with exact target, kernel receipt, broker
  UID, PID and start time. Independent VHCI rows show USED ports, and sysfs
  generation, VID/PID, interface count and native drivers match the receipts.
- Runtime-power samples of `autosuspend_delay_ms=-1` and `runtime_status=active`
  for both imported profiles. The HID driver is `usbhid`; both CDC interfaces
  bind to `cdc_acm`.
- Competing imports rejected with exit 1 and unchanged original channel records
  and VHCI rows. The complete authenticated catalog returns after each release.
- Actual TERM/wait of all ten original unprivileged product processes, each
  exiting 0. Every client reports verified cleanup; helper records and exports
  are empty, all VHCI ports are NULL, and both prior local generations are gone.
  Virtual leases, queues and pending URBs return to their required empty state.

This foreground mode has no durable application correlation. Its channel and
kernel observations are not relabeled as managed-job Root terminal proofs.

The original packet classifier is unchanged. Both ends retain complete boot and
product-window captures; each positive trial has 26 TLS stream directions.
IPv4 captures contain 715 packets per end; IPv6 captures contain 723. The
classifier checks contiguous TCP/TLS framing, TLS 1.3, framed-v2 ALPN, real
port-3240 refusal on both families, and absence of its specified clear USB/IP and
fixture signatures. It does not decrypt TLS or establish a general security proof.

Original host owners 24417 and 61147 actually exit 0. Their four guest controllers
and four QEMU processes also exit 0, with no forced termination.

## Preserved failure and fixture correction

The first IPv6 host owner 99768 exits 1. Client `dpkg -i` exits 2 because its
frontend lock is held by PID 1268. That output does not identify the executable;
the fixture had not captured the lock owner before shutdown. No virtual-server
traffic ran in that trial. Both QEMU exits are 0, which does not change its FAIL.

The later fixture stops only the two guest package-maintenance timers, then
observes the services and both actual POSIX dpkg locks before invoking the same
installer. It never removes locks, signals their owners or retries `dpkg`.
Both readiness observations in the successful IPv6 run find the locks available
immediately. This does not test a contended wait or prove that another unscheduled
installer cannot race the observation. USB budgets and assertions are unchanged.

## Evidence and remaining scope

The archive contains 603 members, 2,694,777 uncompressed bytes and 915,944 gzip
bytes. SHA-256: `9a2a98da6236408ac9226665e6b73d0333e165e31fea2f1d9c4a728290e72ff3`.
All members round-trip exactly; nested public guest archives match their extracted
files. Twenty-one actual private-key files are excluded and their bytes checked
against public members. VM disks, firmware, packages and ELF binaries are excluded.

The collector's first two attempts falsely matched literal private-key and
capability marker strings in its own Python source. Both failed before writing
an archive. Their methods and failure transcriptions are retained. The final
scanner requires material following a marker and still checks every actual
private-key byte sequence; it makes no exception for source files.

A separate same-author read-only method maps both sets of 742 inputs to their
exact Git blobs, compares 52 guest/host request-response pairs, and reconstructs
the raw HID events, serial bytes, original channel identities and releases.
It passes; this is neither another USB execution nor an independent security audit.

Real-device G1/G2, ARM virtual fault coverage and managed-job variants, full ARM
GUI actions/faults, storage, isochronous transfers, performance and Windows USB
support remain unvalidated. Earlier installation, import-power and boot failures
retain their original verdicts. The Phase 0–7 goal remains active.

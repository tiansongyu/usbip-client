# Application service: implementation and validation

2026-10-23. The nonroot application service owns actual USB/IP jobs, independent
of the CLI connection requesting them. **This is an incremental Phase 3 result,
not completion of Phase 3 or the full project.** Real hardware G1/G2, the TUI,
GUI, packaging, performance qualification and Windows remain unfinished.

Use the [English service guide](../application-service-guide.md) for setup and
commands, and the [implementation contract](application-service-design.md) for
ownership, persistence, trust and recovery rules.

## Delivered implementation

- `rustusb-service` directly owns client, Native server and optional Rust
  virtual/USBFS runners. It does not start CLI subprocesses or parse their stdout.
  Existing foreground secure commands remain separate implementations; their
  consolidation is unfinished.
- A root-managed manifest fixes profiles, TLS configuration references, helper
  endpoint and data/control directories. Each RPC uses kernel Unix peer UID
  authorization for an exact profile/action. The broker UID remains the trust
  domain for its full helper pool; it is separate from operator identities.
- Durable start/session/attempt intent precedes capability acquisition. Rust
  managed IMPORT waits for explicit application admission before its atomic
  claim. An individual incoming connection can stop while another device
  continues. Rust export-pool removal requires job stop and explicitly rejects
  unsupported individual export removal.
- Stop is a request. The actual owner retains its handle, relay and cleanup
  tasks until completion. A soft cleanup deadline never substitutes for the
  attempt's completion witness. Operation error and cleanup proof are separate.
- A bounded atomic journal commits sessions, minimal audit and retained request
  IDs together. Admission uses at most 1 MiB of the 2 MiB budget, reserving space
  for completion. Expired explicit retries start nothing. Capacity refusal before
  a write does not poison healthy recording.
- Helper tracked APIs retain an exact broker-scoped terminal proof in the same
  transaction that retires the original record. Native disk 1 migrates to 2;
  physical disk 1/2 migrates to 3 without granting old recovery authorization.
  Untracked legacy operations keep disk 1 until a tracked admission upgrades it.
- Startup and ended-owner recovery query exact kind/correlation/key/UID/resource
  proofs. Missing history, legacy records and out-of-pool records remain unknown.
  No FD adoption, PID-death inference, automatic storage resume or new root
  reconciliation authority is introduced.

Client `Connected` requires actual VHCI enumeration. Server `Connected` means
the complete IMPORT reply was flushed, not remote kernel enumeration. Counters
measure successful post-IMPORT USB/IP application reads/writes, including
headers and partial writes; they are not USB payload or TLS wire byte counts.
Raw-v1 heartbeat and RTT are unavailable.

## Software and CI

The full `scripts/check.sh` passed with **196 default + 279 all-features + 20
vendored = 495 test executions**, 39 result lines, no failed or ignored tests.
Formatting, both Clippy configurations, release builds and rustdoc checks passed.
The 150 selected Rust/Cargo/check inputs were unchanged before and after the run.
These exact inputs were subsequently verified against clean `eaa1936` Git blobs.

The first full gate failed eight new Native journal fixtures: the actual host
umask `0002` made their temporary ancestor writable by the group. Only private
test fixtures received explicit `0700`; product directory checks were retained.
The original failure and input hashes remain archived. Three old fixture source
files were reconstructed by reversing only that chmod change and verified
against the first run's exact SHA; they are labelled reconstructed.

The separate Native-only CLI dependency tree and `cargo check --features
secure-transport` passed, with the service present and Rust engine/nusb/rusb
absent. This is not an additional test count.

[CI run 39979747581](https://github.com/tiansongyu/usbip-client/actions/runs/39979747581)
passed on all four native Ubuntu 22.04/24.04 × x86_64/aarch64 jobs at `eaa1936`.
Each job's official logs contain 495 executions/39 results, two backpressure
scenarios and four signal scenarios. Connector-decoded logs equal the official
ZIP logs byte-for-byte after decoding. A separate 195-predicate audit verifies
those facts and the 150 exact software inputs; it is not a USB test count.

The same all-features CLI ELF also passed local four-signal/two-backpressure
regressions. Blocked stdout proves bounded exit and data-socket closure; only a
received stopped snapshot proves the associated virtual backend state.

Research, author handoffs, intermediate build failures and full software output:
[software manifest](../evidence/application-service-software-20261023/manifest.json).

## Original eight clean QEMU runs

All eight runs below used clean `eaa19366486b8b99412efbfce1f13d49f73fe0cc` and the
same locked all-features release artifacts:

| Artifact | SHA-256 |
| --- | --- |
| CLI | `7a7f07caa8d65d880a01b873a64e1c9b4fa267da97e90586f182066db143b79f` |
| Helper | `cc6525a4aa746fcfea5e05ee6a6cbc5975cf24bd82d13928da6e50a5fccb3c2a` |

| Entry point / backend | IPv4 | IPv6 | Scope |
| --- | --- | --- | --- |
| Application agent / Native | PASS | PASS | Actual task ownership, targeted stop, SIGKILL/root proof and restart |
| Foreground / virtual | PASS | PASS | Two HID/CDC I/O, cleanup and reuse cycles |
| Foreground / USBFS | PASS | PASS | Two HID/FTDI I/O, cleanup and reuse cycles |
| Foreground / Native | PASS | PASS | Two HID/FTDI I/O, cleanup and reuse cycles |

Six foreground runs retain 152 selected inputs each; the two agent runs include
both new scripts and retain 154. All source/binary/HEAD/clean guards are true;
all 16 guest exits are zero. Their original raw artifacts and the independent
audits are indexed in the [eaa1936 validation manifest](../evidence/application-service-validation-eaa1936-20261023/manifest.json).

The new agent scenarios verify:

1. Real UID-2000 agents and root helpers, with UID-1000 operators. A successful
   start RPC process exits before actual HID and serial I/O.
2. UID-1002 and unknown-profile starts are denied. Jobs, sessions, request IDs
   and root resources remain unchanged; only fixed denial audit entries appear.
3. Stopping the incoming HID session releases that actual stub while the
   original serial capability remains identical and performs another 35-byte
   exchange in each direction.
4. The client agent is actually killed and waited with status 137 while the
   serial session is Connected and owns its capability. Actual NULL/Gone state
   and the exact root terminal follow. Restart retains old identities and uses
   that root proof before new attempts perform real I/O. This does not prove an
   URB or kernel ioctl was active at the instant of SIGKILL.
5. Normal server TERM restores the full original driver baseline. The new server
   epoch reuses the devices and both endpoints complete actual final cleanup.

Each agent run retains exactly 8 server sessions/2 jobs and 4 client
sessions/4 jobs, with 12 corresponding root terminals. It performs three serial
rounds, 105 bytes each direction, and two HID key down/up pairs. The six
foreground runs add twelve serial rounds, 420 bytes each direction, plus their
virtual CDC echo checks. None of these byte totals is a throughput benchmark.

Independent lifecycle decoders do not call the runners' verifiers: 967 audit
comparisons per agent family and 504 across the foreground six. They check raw
base64 artifacts, immutable source/ELF receipts, actual identities, precise
resource ownership, phase-specific metadata and baseline restoration. These
comparison counts are not USB cases or unit tests.

Independent security parsing checks both directions of both external PCAPs,
TLS 1.3 ServerHello, ALPN offer/SNI, stream equivalence and matching dual-stack
3240 rejection. It does not decrypt the selected ALPN or handshake certificates.
The original agent collection lacks the actual TLS JSON/pin files and permission
snapshots: its configuration proof is limited to the executed init, manifest,
public DER and actual authorized receipts. That limitation is retained, not
filled with reconstructed deployment files. A narrow collector enhancement
requires fresh clean Native runs before stronger claims are made.

## Retained limitations

The old foreground API's disk-1 JSON omits correlation. `None` is a fixed decoder
and call-path constraint, not a dynamically observed raw field. USBFS foreground
runs have the final empty journal but lack intermediate immutable grant journal
snapshots. Each agent client kernel log retains eight speed-not-set/URB warnings;
the audit does not claim warning-free kernels or successful handling of every
USB request. A stopped HID operation can retain `io` while cleanup is verified.

Virtual export and both Rust backends' incoming sessions lack an independent
helper attempt. Crash recovery cannot retire these application records using
another grant's proof. Reserved-but-unsent attempts remain conservative unknowns.
Removed profiles remain in the protected journal but are not exposed by the
current profile-scoped RPC. Audit is a bounded ring, not permanent archival or
log rotation. The managed virtual/USBFS runners have software coverage; the
agent QEMU scenarios here validate Native only.

Real hardware, arbitrary devices, ISO, network certificate negative/rotation
matrices, complete restart recovery, data-path heartbeat/RTT, full foreground
runner consolidation and complete Phase 3 remain unfinished.

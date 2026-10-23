# Data-link health QEMU experiment

This fault-injection path is separate from the existing secure/agent/TERM runners.
The private disk2 compatibility verifier is maintained in the existing agent runner.
The product uses explicit TLS configuration schema 2 and
`data_link={protocol:"framed_v2",interval_millis:1000,response_timeout_millis:3000}`.
`agent health PROFILE` / `session_health` supplies actual per-session connection
identity and tuple. A responding transport does not prove USB completion.

The runner accepts `--backend native|usbfs|virtual` and
`--family ipv4|ipv6`. Source support is separate from actual run evidence. Each
backend has its own lifecycle verifier; the packet boundary is shared.

| Backend | Actual workload | Resource proof |
| --- | --- | --- |
| Native | QEMU HID key events and FTDI RX/TX | Original import/stub terminal proofs; held exports retire only after final stop and complete original baseline restoration |
| USBFS | QEMU HID key events and FTDI RX/TX | Per-session lease cleanup is separate from the export grant's final cooperative Finish and original baseline restoration |
| Virtual | HID enumeration and idle data connection; CDC 35-byte echo | Actual client Native import proof and server application ownership cleanup; no physical server device proof or injected HID key claim |

Implemented here:

- `packets.py`: finite Ethernet/IPv4/IPv6/TCP decoder and the fixed guest's
  ARP/RS/NS/NA/MLDv2 subset. Unknown protocols, fragments and excessive extension
  chains fail instead of bypassing inspection.
- `valve.py`: two private AF_UNIX socketpairs, one for each QEMU socket-netdev.
  The runner polls this object on its orchestration thread. Only a pre-observed exact TCP
  flow can be armed; each direction gets a fresh flow. `c2s`, `s2c`, and `both`
  keep both host endpoint FDs open and discard complete target Ethernet frames.
  No TCP abort, link-down, peer process kill, TLS proxy or pong injection occurs.
- `audit.py`: independently compares both raw PCAPs against all ingress and
  completed forwarding records, recomputes every drop decision, confirms actual
  pre-arm health tuple bindings and parses outgoing TLS ciphertext framing.
  It does **not** call an old runner's verifier or copy its verdict.
- `run.py` and `guest-init.sh`: actual Native dual-guest workload, source/ELF
  guards, schema2 trusted configurations, immediate raw health snapshots and
  bounded phase barriers. Native construction is preserved when dispatching
  the other backends. No runtime result is implied by this source.
- `verify.py`: independent lifecycle acceptance, exact original helper proof,
  original sibling capability, actual operation timeout and seven real I/O
  rounds. Three HID failures each require an explicit fresh attempt; the serial
  session stays the same. Both agents finally receive actual SIGTERM/reap0.
  Ethernet link-down occurs only after resource proof, for guest teardown; no
  assertion requires kernel TIME_WAIT/FIN_WAIT bookkeeping to disappear.
- `guest-rust-fragments.sh` and `verify_rust.py`: the managed Rust backends'
  setup, actual I/O and independent lifecycle requirements. Virtual CDC bytes
  are observed inside the client guest, without a QMP key or FTDI host fixture.

The limits are explicit: QEMU frame 14..69632 bytes, 64 queued frames per
receiving endpoint, 32 read/write steps per poll, poll at most one second,
100,000 journal records/64 MiB, 64 MiB per PCAP/100,000 packets and 16 MiB per
reconstructed source TCP stream. Overflow is an experiment failure, never a
silent packet drop. Arming/disarming requires empty partial-frame/forwarding
buffers. The retained FD number/dev/inode/mode are re-read at each barrier.

The framing source is fixed QEMU v6.2.0 `net/socket.c`, `net/net.c` and
`include/net/net.h`; e1000 socket-netdev uses no vnet header. Previous real runs
recorded QEMU 6.2.0 Ubuntu package 6.31. A future run must record its own actual
executable/version rather than inherit that history. Raw source hashes and the
scope-before-implementation record are in
[initial scope record](../../docs/evidence/data-link-first-failure-e1529ea-20261024/preflight-diagnosis/rustusb-qemu-data-link-health-initial-handoff-20261024.json).

The workload saves `bindings/{c2s,s2c,both}-{client,server}-health.json`
as exact actual RPC response bytes and `{phase}-terminal.json` as the collected
terminal evidence bundle before disarming. The auditor verifies their hashes and
pre-arm live health identities; the backend lifecycle verifier validates the
terminal bundle, actual same-attempt monotonic timeout, two-sided cleanup,
Catalog result and original sibling device's continued I/O. Ciphertext packets
cannot independently identify a pong or distinguish Catalog from IMPORT.

Only explicitly faulted source streams can have a reported incomplete TLS tail,
and only after their arm event. Other stream gaps/tails are errors. This is not
permission to skip receiving-side packet loss: every received/dropped/forwarded
frame is reconciled byte-for-byte against both captures first. `PACKET_AUDIT_PASS`
is scoped network evidence, never a heartbeat/USB success result.

Preparation, without builds or guests:

```sh
python3 -B scripts/qemu_data_link_health/run.py --plan --backend native \
  --family ipv4 --output /tmp/data-link-plan.json
```

Run as an ordinary host user from a clean, reviewed commit:

```sh
python3 -B scripts/qemu_data_link_health/run.py --backend native --family ipv4 \
  --evidence /tmp/rustusb-data-link-native-ipv4-NEW-CLEAN-SHA
```

The default performs the locked all-features release build. `--skip-build` is
only a development option and must remain explicit in evidence. Each direction
uses an actual 1000/3000 ms root-managed probe policy. A timed-out sample must be
3000..5000 ms after enqueue (the extra 2 s is this guest scheduling allowance,
not a changed response policy); both actual cleanup proofs must arrive within
the independent 15 s host observation window. No soft deadline creates proof.

No private key bytes, TLS session secrets or USB payload are emitted by the
valve/auditor. Fixed public fixture bytes are only checked for accidental clear
wire exposure. Real USB tests remain inside disposable guests. Native or managed
USBFS idle/connected is not pending-URB proof. The managed server API does not
expose actual `pending_urbs`/`usbfs_inflight`; this harness does not infer them
from link-buffer counters. ARM native software
checks are not ARM QEMU/USB evidence. Existing historical failures and all raw-v1
regressions retain their original versions and scopes.

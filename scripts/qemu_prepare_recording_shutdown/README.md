# Preparation / recording shutdown gates

This disposable two-guest experiment distinguishes accepted resources from a
listener or an IMPORT. It runs the real nonroot application service and helper.
The first server job prepares **both** fixed QEMU HID/FTDI devices in one pool.
No device from this preparing job is described as already doing I/O.

Run from a clean committed checkout with an ordinary x86_64 host user:

```sh
python3 scripts/qemu_prepare_recording_shutdown/run.py \
  --case native-resource --stop job --kernel 6.8.0-106-generic \
  --evidence /tmp/rustusb-prepare-native-resource-job-UNIQUE
```

Use the actually installed, source-recorded kernel. The runner defaults to a
locked all-features build; `--skip-build` is development-only. Each fresh output
folder retains copied inputs, generated init, exact guest artifact bytes, QEMU
commands, public certificates, actual process executable hashes and four
source/head/clean/binary guards. No private-key bytes are archived. The C tracer
is compiled by the runner with `cc -std=c11 -O2 -Wall -Wextra -Werror`; it is
executed only in the server guest. It refuses ordinary-host UID or missing
exclusive QEMU markers before ptrace/device work. It never opens a USB node.

Cases:

- `native-resource`: stop the actual `state.next` **file fsync syscall entry**
  whose draft contains the second accepted Native export Resource transition.
  The first Resource has already been acknowledged. Both actual handles must
  close, exact root proofs must be durable, and both complete original driver
  baselines must return while this original persistence operation remains held.
- `usbfs-resource`: the same second-Resource gate, requiring both actual received
  character FDs to close and both `physical_baseline_restored` terminals before
  resuming the original fsync. There has not yet been an interface claim.
- `usbfs-initializer`: both actual grants have been recorded; hold the real
  nusb `GET_CONFIGURATION` control ioctl entry on the fixed HID node. Stop must
  retain the actual worker and capabilities until this exact syscall is resumed
  and returns. No terminal proof may be fabricated while it is held. Afterwards
  total join and Finish must restore/retire both original grants. This is syscall
  **entry**, not a reproduced in-kernel active stall.

`--stop job` sends an authorized UID1000 StopJob RPC in the background; its
possible five-second durable-response timeout is recorded and never treated as
cleanup proof. `--stop term` delivers actual SIGTERM to the UID2000 agent and
records ptrace signal delivery. Neither mode kills the held owner to claim release.
The guest-only gate has a 30-second bound; the same 15-second resource observation
window applies to old and fixed products. Actual tracer held-before/held-after
receipts bracket root/sysfs/FD sampling. Only then does the root guest controller
release that same TID/FD/syscall. Other traced threads continue running.

A held-phase failure is latched. The guest still releases the exact operation,
joins the original agent, verifies restoration, starts a fresh agent/job, and
performs actual KEY1 down/up and 35 bytes each FTDI direction through TLS/VHCI.
Finally it cooperatively stops and checks the complete baseline and exact root
proof history. A successful reuse cannot turn the original failure into PASS.
The outer failure path sends ABORT; the guest always releases a held gate before
cooperative cleanup and artifact collection. Deadline failures remain failures.

IPv4 and IPv6 are selectable. This slice does not claim an independent running
sibling profile during preparation, a hard kernel stall, hardware G1/G2, ARM
execution, or additional coverage from the historical startup-TERM test whose
helper acceptance was false. Existing harness files are reused read-only for
boot, PKI, sysfs snapshots and artifact encoding; their verdicts are not reused.

## Stop one export while preparation is waiting

The two Native-only local cases use a different verdict from whole-job stop:

```sh
python3 scripts/qemu_prepare_recording_shutdown/run.py \
  --case native-resource --stop session --kernel 6.8.0-106-generic \
  --evidence /tmp/rustusb-prepare-local-resource-UNIQUE
python3 scripts/qemu_prepare_recording_shutdown/run.py \
  --case native-available --stop session --kernel 6.8.0-106-generic \
  --evidence /tmp/rustusb-prepare-local-available-UNIQUE
```

The selected serial export alone must reach its exact root terminal and original
driver baseline while the original HID grant, owner, registration and bound state
remain unchanged. Resource is held before bind; Available is held after the
listener has actually bound. The same held-before/after receipts and original
fsync release rules apply.

After release, the same server job must remain Running and publish only the
original HID. A real client serial job is rejected during catalog selection with
Permission, NotStarted and no helper attempt; this is not a stale raw IMPORT
probe. The original HID is imported and produces an actual KEY1 event. After
normal client detach, stopping that last export must leave the same job/listener
running with an actual authenticated empty catalog. Only then is the whole agent
stopped and a fresh two-device epoch exercised.

These cases preserve local cancellation, global failure, recording uncertainty
and actual cleanup as separate observations. They do not treat disappearance of
both resources as a successful local stop. Their source fixtures and static
checks are not QEMU acceptance until these commands actually run.

## Local Native export cancellation

`--case native-resource --stop session` and
`--case native-available --stop session` use a separate guest flow and verifier.
The serial export is the second device and exact local StopSession target. The
HID sibling retains its original root owner, lease, correlation, registration and
bound receipt. Only the serial export may receive `native_export_restored` while
the original target transaction remains held. Restoring both devices fails this
local test.

The Resource case has no listener yet. The Available case selects the serial
`observe / Available / export_ready` fsync after the first HID Available ACK and
both Resource ACKs; it separately records the already-bound TCP listener and a
real successful TCP connection. It does not call this connection a TLS handshake
or an IMPORT.

After releasing the original ACK successfully, the **same** job must still run.
Authenticated Catalog exposes only the original HID registration. An ordinary
serial start receives `permission`, finishes `not_started`, makes zero helper
attempts, and leaves the client root store unchanged. This is a catalog-selection
refusal, not a fabricated stale-IMPORT test. The original HID grant then performs
actual KEY1 down/up through the real Native stub, TLS and client VHCI. The client
then normally detaches that import. An authorized local StopSession closes the
last original HID export; both original baselines and exact root proofs must
return while the same server job stays Running and its listener remains bound.
A real mTLS Catalog exchange must return an empty list. Whole-agent SIGTERM
follows that empty catalog and is a separate event. Fresh two-device grants in the
next server epoch must still pass the original HID and 35-byte-per-direction
FTDI reuse checks.

Final retained histories are exact: server two jobs / seven sessions / seven root
terminals; client four jobs / four sessions / three root terminals plus the one
explicit no-start denial. Old global-stop flows and verdicts remain unchanged;
the local test does not apply their “both resources closed under the gate” rule.

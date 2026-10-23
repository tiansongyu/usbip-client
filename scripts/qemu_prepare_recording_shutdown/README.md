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

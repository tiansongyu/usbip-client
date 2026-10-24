# Installed agent process logging under a removable lock obstruction

This focused runner boots two disposable x86_64 Linux guests with the installed
Native helper, UID2000 application agent and UID1000 operator. It uses IPv4,
fresh mutually authenticated TLS, and the schema2 `framed_v2` data link. It does
not touch host USB or change the existing regression runners.

Run from a clean committed checkout as an ordinary host user:

```sh
python3 scripts/qemu_process_logging/run.py \
  --kernel 6.8.0-106-generic --family ipv4 \
  --evidence /tmp/rustusb-process-logging-native-ipv4-FRESH
```

The coordinator performs the locked all-features build and launches QEMU.
`--skip-build` is explicitly a development option; published evidence uses the
actual build and records all selected source files, tool versions, generated
init, payload hashes, actual guest ELF receipts and exit codes. The kernel
argument must name the installed kernel/module pair actually available on the
host. Default deadline is 600 seconds; the output directory must be new.

Three agent epochs share one privileged helper in each guest:

1. Native keyboard/FTDI enumeration and actual key down/up plus 35 bytes each
   direction. Authorized log pages contain typed audit entries for the exact
   profile plus the caller's own unscoped refusals. Different UID/profile and
   path-shaped cursor requests are rejected without changing the original USB
   capabilities. Sixty-four actual committed denials cause agent log rotation.
2. Repeat real I/O, then a separate root `flock` process holds the existing
   agent `access.lock` exclusively. A nonce-controlled FIFO bounds this gate to
   180 seconds. The product does not inherit the gate descriptor. More than256
   actual committed denials fill the finite writer queue; actual writer/reader
   syscall and lock inode observations distinguish this from a merely slow
   request. Five parallel authorized readers produce an actual Busy response;
   arrival order is not inferred from process launch order. SIGTERM must close
   all exact original Native resources and restore the full original driver
   baseline while the same gate remains held and the original agent lives.
   Client NULL/generation disappearance is separately observed. Only then is
   the same nonce released. The expected actual server agent exit is **1** with
   the fixed logging-loss error and a genuine `Stopped` record with dropped
   records. This logging result is separate from verified USB cleanup.
3. A fresh agent instance and fresh capabilities perform actual I/O and clean
   cooperative shutdown. Both privileged helpers then receive SIGTERM and exit
   normally; their actual private logs are collected.

The agent's 4096-byte/four-segment retention is exercised by actual audit data.
Both helper and agent directories/files must satisfy the actual ownership,
0700/0600 and byte/count bounds; helper rotation is not assumed merely because
these settings exist. Startup copies preserve the genuine Started record even
when later retention removes its segment. Per-record sequence is ordered;
concurrent producer elapsed timestamps need not be ordered by physical line.

Failure collectors preserve the first verdict, release only their own gate,
and capture the raw bounded files before guest poweroff. On failure the host
allows up to200 seconds for the180-second gate and final collection; this never
extends the normal600-second success deadline or turns a failure into success. Typed NDJSON log
segments are collected; public TLS configuration/DER and private-key metadata
are separate, and no private-key bytes are copied. Host controls travel over a
private serial channel, not the USB data TLS connection. PCAPs are retained,
but this focused test does not make a new packet-security or heartbeat fault
claim. It does not prove cancellation of a permanently blocked filesystem,
physical hardware acceptance, GUI/TUI logging, or completion of all Phase3.

The verifier's checks are diagnostic predicates over this one focused scenario,
not additional USB test cases. Prior QEMU matrices and their failures retain
their original source and result attribution.

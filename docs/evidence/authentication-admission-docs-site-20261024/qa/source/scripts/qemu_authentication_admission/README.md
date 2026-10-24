# Authentication admission with live USB imports

Run as an ordinary x86_64 host user from a clean committed checkout, using a new
evidence directory and the installed QEMU/kernel prerequisites:

```sh
python3 -B scripts/qemu_authentication_admission/run.py --backend native --family ipv4 \
  --kernel 6.8.0-106-generic --evidence /tmp/admission-NEW-CLEAN-CHECKPOINT
```

`native|virtual|usbfs` and `ipv4|ipv6` are separate paired runs. The default locked
all-features build, role-specific credentials, full original authentication/
retirement predicates and real HID/serial I/O are retained. Never execute a
generated PID1 or worker on the host.

The trusted server config selects burst32/one replenished cell per500ms. Eight
UID2000 shell workers repeatedly run the actual copied OpenSSL TLS1.3 client
using the valid but unmapped certificate for eight guest uptime seconds. Every
attempt has a real timeout/wait result; worker proc UID/start/ELF and final waits
are saved. Fast OpenSSL leaves lack independent per-executed-ELF receipts. Their
fixed source/config/role and copied executable/library hashes are recorded.

Optional host phase hooks delay original input until actual admission refusals
and original close until recovery sampling; their defaults preserve old suites.
Raw logs are never rewritten. Actual HID/serial I/O must finish while workers
are alive and later refusal counts must progress. After all workers exit, an
authorized discovery must succeed on the same configuration after refill. Its
empty catalog means the two devices remain busy; original I/O and ownership/
root terminal proofs/full driver baselines are independently required.

No PASS is implied by source, syntax checks or this README. Keep first failures,
new clean/fresh retries and actual sources/ELFs separate. This is raw-v1 only;
installed managed RPC, framed-specific exhaustion, per-source fairness, sustained
attack resistance, benchmark results, hardware G1/G2 and complete G3 are separate.

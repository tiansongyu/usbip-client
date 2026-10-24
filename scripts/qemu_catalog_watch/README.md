# Installed authenticated catalog watch

Run as an ordinary x86_64 host user from a clean tree, using a fresh evidence
directory. The normal locked all-feature release build is required:

```sh
python3 scripts/qemu_catalog_watch/run.py --family ipv4 --evidence /tmp/catalog-watch-v4-NEW
python3 scripts/qemu_catalog_watch/run.py --family ipv6 --evidence /tmp/catalog-watch-v6-NEW
```

This composes the existing managed Native two-guest fixture, retaining all its
original I/O, identity, configuration, ciphertext, root-proof, restart and cleanup
checks. No host USB is passed through or mutated. The extra host hook only sends
named serial controls; it does not change production RPC or device behavior.

The client starts the actual release CLI as UID1000 using a public fixed agent
profile. It observes current two-device inventory, a deliberately held local
agent outage, recovery with resynchronization, and real remote export sessions
stopped individually (2 -> 1 -> 0). A fresh response precedes a guest-only watcher
SIGSTOP gate around the existing client-agent SIGKILL. The watcher resumes while
the agent is absent and must publish stale retained data before restart. It
stops before the original server's second epoch; all three original USB I/O
rounds and full original driver/proof checks remain required.

Installed CLI cases additionally test the protected direct TLS loader as UID2000
with TERM, an actual full write-only stdout FIFO and INT, a closed reader and
exit1/Broken pipe, and UID1002 terminal denial. The full FIFO is filled by an
owned `dd` child; its actual blocked write is observed, then it is TERM/waited.
The watcher must complete an independently recorded remote discovery while its
stdout thread is blocked. No reader drains the FIFO before actual watcher wait.
The separate closed-reader case opens stdout write-only, so it cannot retain its
own read end and hide EPIPE. Root observes only its own disposable guest children.

Raw stdout, actual process UID/caps/starttime/ELF/FD/syscall/wchan, waits, point
projections and audit facts are retained. The fast denied child has actual exit
and kernel-UID denial audit but no separately sampled live ELF; do not invent one.
`point.jq` selects only complete matching begin/device/change/end batches; the
host verifier independently parses the original raw JSONL and compares every
saved projection. A failed collector, partial batch or original verifier remains
FAIL, even if a later guest or QEMU exit is zero. This validates polling snapshots,
not all transient events, physical generations, hardware or full G3.

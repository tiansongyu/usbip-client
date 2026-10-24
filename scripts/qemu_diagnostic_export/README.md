# Installed Native diagnostic export during a withheld commit

This focused paired-guest scenario implements diagnostic-export design validation
item 5. It is separate from the previous logging, prepare-stop and link-health
matrices. Its source and predicates are prepared; a runtime result exists only
when the coordinator runs the clean committed revision and archives its terminal
summary. Authoring/static checks are not USB test executions.

```sh
python3 scripts/qemu_diagnostic_export/run.py \
  --kernel 6.8.0-106-generic \
  --evidence /tmp/rustusb-diagnostic-export-FRESH
```

Run as an ordinary x86_64 host user. The runner performs the locked all-features
release build, compiles a generated guest-only `prepare-gate.c` variant with strict
C11 warnings, prepares separate server/client credential payloads, and starts exactly
two disposable Native IPv4 VMs. It never opens host USB nodes. Root-owned helper,
nonroot broker and operator are actual guest UIDs 0, 2000 and 1000.

The run uses two real QEMU USB devices (keyboard and FTDI serial). Both initial
IMPORTs and both HID/35-byte-each-direction I/O rounds use the same framed-v2
connections, sessions, helper receipts and local generations. The second round
runs after the complete private diagnostic export and before releasing the WAL
gate. Health/PING does not substitute for these input/output bytes.

Before the gate, read-only UID1002 and audit-only UID1003 each run the real exporter
and receive a permission-limited capture. Their committed audit entries establish
actual peer identity; these short-lived refusal processes are not claimed to have
independent `/proc/exe` samples. The successful UID1000 exporter has saved actual
process UID/starttime/ELF/FD observations. Twenty real unknown-profile denials make
the authorized retained audit and log histories exceed one page. Permission
refusals leave the original jobs and Root capabilities unchanged.

The original prepare gate's selectors, FD/ptrace/cleanup logic and time limits are
reused byte-for-byte. A generated variant changes only its strict15-argument
launch allowlist to21 arguments, adding the exact private file-log flags shown in
the guest source. Reverse substitution must reconstruct the original source
exactly; generated C/provenance are saved. The initial clean `bde3aa9` launch FAIL
from passing21 arguments to the unchanged15-argument gate remains separate.
After its actual `armed` receipt,
a new UID1000 unknown-profile request waits at the real `state.next` fsync syscall
entry. Candidate/draft/previous committed bytes are saved, and the selected
candidate must be exactly the next Denied audit. The gate retains the original
thread, FD, filesystem device/inode and nonce. It retains the existing 30-second
hold and 120-second tracer limits. This is a removable syscall-entry obstruction,
not a kernel-internal hard block or a change to production deadlines.

Inside that same held interval the exporter must exit0 with a complete <=4MiB
artifact. All jobs/sessions/audit pages must map through injective per-namespace
aliases to the original committed view, and the uncommitted audit must be absent.
Independent live health and multi-page typed logs must be present without leaking
raw IDs, UID, paths, endpoints, tokens, credentials, canaries or USB payloads.
The output parent is actual caller-owned0700 and the single-link file0600.

The complete CLI receipt requires the collector's original End acknowledgement.
A subsequent same-UID framed UDS Begin must succeed before gate release, proving
that the one-per-UID metadata slot is reusable; the harness explicitly Ends that
new token and saves its framed response. This is not a raw trace of the original
collector token. Direct harness requests contain only fixed read-only diagnostic
operations, not USB mutations or supplied file paths.

After the second real I/O round, another actual held receipt and unchanged saved
commit/Root identities precede explicit release of that exact syscall. The
original fsync must really return0, then its withheld audit may become committed.
An existing-output export must report AlreadyExists without replacing bytes or
inode. Client cooperative stop precedes server stop; the original two imports,
two stubs and two exports require exact Root terminal proofs, original VHCI
NULL/Gone and full original server driver/configuration/interface restoration.
Any actual State/Protocol incoming outcomes remain visible and qualify only under
the reused strict client-first peer-close correlation, never as clean EOF.

The runner composes read-only boot/config/USB helpers from the existing agent and
logging sources and reuses the unchanged epoch1 lifecycle predicates. This is
source-locked reuse, not an independently implemented auditor. Generated init,
all transitive source inputs, compiled ELF hashes, actual UID/process receipts,
public TLS/config files and private-key metadata are archived. Private key bytes
are never copied into evidence. Packet captures are retained as raw artifacts;
this scenario does not add a new TLS packet classifier verdict.

On failure, the primary FAIL is checkpointed before collection. Guest cleanup
releases/aborts the removable gate before signalling owners, and the bounded
150-second failure-collection window does not change a failure into PASS. Each
host collection/close is isolated so later evidence is still attempted. Both
kernel serial channels are checked again after guest exit; source, ELF, HEAD and
clean-worktree guards run on every terminal path. Hardware G1/G2, other platforms,
permanent filesystem blocking, signal cancellation and publication fault injection
remain outside this scenario; process/software fixtures own those separate scopes.

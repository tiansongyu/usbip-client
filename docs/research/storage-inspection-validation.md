# Storage inspection validation

The root-only `storage-inspect` command now reports observed LUNs, partitions, holders, visible-task mounts/references and swap without changing USB ownership. Two disposable full Ubuntu x86_64 guests pass the inspection scenarios. **Storage forwarding remains disabled**; requirement 4.6 and full Phase 0–7 acceptance are not complete. The [operator guide](../storage-inspection.md) and [design](storage-inspection-design.md) describe the scope.

## Source and software checks

| Version | Purpose | Result |
| --- | --- | --- |
| `2eca4522104eef7ccbf23dffba2ef1120bf4349c` | Starting point | Clean local/remote baseline; no new storage support. |
| `e09a9ff6` | Design before implementation | Fixed-source ownership/handoff findings and staged contract. |
| `7df329e0` / `ce840a81cf2bd9405b072afd707b75118a44018c` | Initial product / QEMU fixture | Full software checks pass; first Jammy mmap trial fails. |
| `c55a6c577d2479b77a27b3edf796d803c8b689f7` | Correct top-level task mapping lookup and mount-root parser | Full software checks, both fixed storage guests and original Native regression pass. |

Both clean software runs execute the original `scripts/check.sh` and `scripts/check-gui.sh`: **1,148 core + 18 GUI = 1,166 test executions**, with zero failed or ignored tests. This includes default/all-feature formatting, strict Clippy, tests, release builds, rustdoc and the existing vendored nusb tests. It is not 1,166 distinct tests or a newly measured count for each remote CI platform. All 752 tracked non-evidence inputs, HEAD and worktree guards remain unchanged during each run.

The new module contributes 14 tests, executed across the relevant default/all-feature gates. They cover strict numeric/mount/swap parsing, newline/parenthesis task names, block-vs-filesystem device identity, multiple LUNs/partitions/crypt holders, cycles/limits, generation replacement, incomplete mapping access, root/input refusal before work, strict request fields and actual-worker quota retention after losing an awaiter. Existing storage rejection tests remain intact.

Final host release artifacts at `c55a6c57`:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| CLI | 22,461,040 | `dc59c88029063953922c274d32ad18afa42759848e3c3a3027029fd4371523ec` |
| Helper | 7,810,672 | `1be8fdde8344418103007983718176a0cefa965a1a8bb8d37affd331c388eafb` |
| GUI | 17,916,696 | `3ae9159ecaebd9730d42a69fcbbd8da1076a82cd4c6bd830aba34cd181feed48` |

Inspection guests record the running helper's `/proc/PID/exe` hash and the CLI file hash; they do not capture a live executable receipt for every short CLI invocation. The separate Native regression retains its original live broker/helper receipts. No new GUI action or ARM64 execution is claimed by these runs.

## QEMU observations

The existing product-free, signed-image-derived Ubuntu parents are reused through new private COW disks. QEMU 6.2/KVM adds a synthetic BOT device backed only by fresh regular files: a read-only 64 MiB first LUN and disposable writable 64 MiB second LUN. The guest verifies exact USB ancestry, capacity and RO state before creating two partitions on the latter. No host storage device, mount, module, service or account is changed.

| Trial | Source | Kernel | Verdict |
| --- | --- | --- | --- |
| Initial Jammy | `ce840a81` | 5.15.0-198-generic | **FAIL**: bare-block mmap not observed. Actual helper 0, QEMU 0, all guards true; these do not override the failure. |
| Fixed Jammy | `c55a6c57` | 5.15.0-198-generic | PASS, actual host/helper/QEMU 0. |
| Fixed Noble | `c55a6c57` | 6.8.0-147-generic | PASS, actual host/helper/QEMU 0. |

Each fixed guest passes 12 scenario groups, producing ten inspection reports and two expected command refusals:

| Scenario | Required observation |
| --- | --- |
| Idle multi-LUN inventory | Two LUNs, two whole disks, two partitions; first disk's kernel RO is true. |
| Ordinary allowed UID | UID 1000 is denied by the root-only operation. |
| Legacy Native storage export | An explicitly allowlisted storage device still returns `unsafe device`; no binding change. |
| Raw block FD | Exact holding PID/TID and the second LUN's partition device number. |
| Bare block mmap | Mapping remains after the C fixture closes its original FD; `map_files` resolves block `rdev`. |
| Thread-local raw FD | A worker unshares `CLONE_FILES`; its TID is identified and the leader has no matching block FD. |
| Mounted file FD | Both mount and file reference are observed on the partition. |
| File mmap | The mapping is found by its actual target metadata. |
| Separate mount namespace | A child namespace's mount is observed, with real readiness and actual child exit. |
| Swap | The active second partition is observed as swap. |
| Plain dm-crypt holder | The partition has a holder; the mapped block node has a `crypt` prefix hint and backing slave number. |
| Released fixture | No positive use remains after cooperative fixture cleanup and explicit udev settling; coverage issues remain. |

Each fixed guest joins six actual holder/namespace children with exit 0. Every report also checks the original USB directory identity, address, configuration and driver baseline. Private filename/content sentinels do not appear in inspection reports or default helper logs. The random disposable dm-crypt key goes through stdin and is not retained. This is a plain active crypt-mapper test, not closed-LUKS signature detection, encryption verification or safe export permission.

The final reports retain `directory_unreadable_or_gone`, `fd_target_unreadable_or_gone` and `task_reference_unreadable_or_gone`. These categories do not identify a unique missing task/object or prove every such error has the same cause. Their presence is intentional uncertainty, not a passing “complete scan” verdict. No time, capacity or kernel integrity criterion was relaxed to obtain the passes.

## First failure and correction

The original implementation inspected nested `/proc/PID/task/TID/map_files`, which Linux does not expose. Fixed Linux source lists `map_files` in the top-level PID table; top-level numeric lookup accepts TIDs. The correction verifies that `/proc/TID/stat` matches the enumerated thread before reading mappings and checks identity again afterward. FD inspection remains per thread. Both final guests explicitly record direct `map_files` present and nested `map_files` absent. The initial failed report's empty mapping list remains in evidence.

A separate parser correction accepts opaque mount roots such as nsfs `mnt:[…]`. Jammy's final raw mountinfo contains such a root, and the final reports no longer contain the earlier mountinfo parse issue. The initial trial did not capture raw mountinfo, so the exact cause of every original parse issue is not retrospectively proven.

The first idle report also recorded two other raw-FD users. Their executable identities were not captured, so they cannot be assigned to udev. Later trials explicitly run `udevadm settle` after fixture partition/mapping changes; no busy record is filtered, and inspection is not retried until it becomes empty. Earlier work-log wording attributing those users to udev is corrected.

Development failures are retained separately: an initial formatting delimiter error, an Errno conversion compile error, two Clippy folding diagnostics, a research-collector path mistake and a read-only JSON-shape probe error. They are not QEMU or final software PASS results.

## Existing USB data regression

The unchanged `run-secure-qemu.py --backend native --family ipv4` passes at the same clean `c55a6c57`, reusing the exact full-gate release binaries with `--skip-build` and an additional outer HEAD/source/ELF guard. Its original two Linux 6.8.0-106 guests both exit 0. Two HID/FTDI cycles, four import channels, 70 serial bytes in each direction, original baseline/release checks and the original encrypted-link checks pass. This tests the existing non-storage Native path, not USB mass-storage forwarding. No new USBFS, IPv6 or hardware regression is claimed in this increment.

## Evidence and remaining work

[The public evidence manifest](../evidence/storage-inspection-20261026/manifest.json) hashes fixed-source research, original software logs, all three storage trials, the Native regression, executed fixture versions and collection/audit methods. The first FAIL remains separate. Private keys, backing disks, initramfs, packages and ELF payloads are excluded. Linux/QEMU research copies retain their original licensing; no implementation is copied into product Rust code.

The same author's separate read-only audit checks each run's source against its actual Git commit, 384 original Native artifacts, report/child results and executable receipts. It is not another USB execution or an independent security audit. Tool-observed terminal exits are explicitly labelled transcriptions, separate from subprocess logs.

This implementation does not enumerate all kernel references, taskless namespaces, multi-device filesystem ownership, hidden encrypted signatures or system-service policy. It does not implement writable handoff, checked per-LUN flush, client storage release acknowledgement or storage quarantine/recovery. UAS, ARM64 inspection, actual hot-unplug/limit exhaustion in QEMU, permanently blocked syscalls and real hardware remain unvalidated. Native/USBFS storage admission stays closed; the full project goal remains active.

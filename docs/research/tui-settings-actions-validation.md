# Terminal settings and confirmed actions: exact validation

The optional ordinary-user frontend now saves and loads checked user preferences.
Real installed TUI confirmation drives Native server/client starts, targeted
session stop and job stop. It still uses existing UID/profile application RPC;
no helper privilege, credential, TLS/wire or agent disk-schema change is added.
The [guide](../tui.md) explains use; the
[design](tui-settings-actions-design.md) records trust and cancellation boundaries.
This advances requirements 4.9/Phase 4 without declaring full G4/G3 or hardware
acceptance complete.

## Software and file behavior

Clean `5584c5b3307eb885d95b11f40571d48185e868cd` passed all ten commands in
`scripts/check.sh`. The [software receipt](../evidence/tui-settings-actions-5584c5b-20261024/software-summary.json)
retains exact arguments/exits, release hashes and 235 stable selected inputs.

| Scope | Passed | Failed / ignored |
| --- | ---: | --- |
| Default workspace | 417 | 0 / 0 |
| All-feature workspace | 513 | 0 / 0 |
| Locked nusb library | 20 | 0 / 0 |
| Total executions | 950 | 0 / 0 |

Six new preference tests cover real atomic save/load and modes, missing-load
noncreation, strict schema/version/size/unknown fields, protected ancestors,
symlink/hardlink/mode refusal, fresh-OFD lock contention and actual replacement
before publication. The replacement stays intact and only the original matching
temporary is removed. Pure XDG path selection does not mutate process environment.
One new CLI parser/precedence test checks saved values, explicit flags, conflicts
and bounds. These seven functions are counted once per applicable Cargo suite;
subscenarios are not additional USB cases.

Only interval, polling and monochrome options are stored. Schema 1 has no
profile, endpoint, device key, credential, automatic Start or authorization.
Missing load creates nothing. Save explicitly creates checked directories/files,
uses a nonblocking independent lock and exclusive temporary, sync/recheck/rename/
directory-sync, and reports uncertainty after publication errors. Unknown or
malformed existing files are preserved. Started blocking work is really joined;
no timeout pretends an OS syscall was cancelled. Same-UID hostile writers,
arbitrary filesystem power-loss behavior and permanent blocking remain limits.

The locked inventory remains 221 packages; only an existing tempfile dev-edge
was added to the frontend. No external version/checksum or new runtime library
was introduced. Normal default CLI dependency isolation is checked separately
from workspace feature unification; it does not promise identical binaries.

## Four actual QEMU pairs

Every run used the same clean `5584c5b3`, CLI
`5f2e67b6ae1aaf24136fdcbf44fd3c97657c34eaa1483b80b550a63d2f728a7c`
and helper
`83c53d522d5c89a9b3384b23c17242aa3e197217fc785d62706b520c1e1473e5`
as the all-feature software release. All source/head/binary/clean guards passed;
eight actual guests exited 0.

| Pair | Frozen inputs | Real PTY children | Receipt |
| --- | ---: | ---: | --- |
| Confirmed actions, Native IPv4 | 233 | 19 | [PASS](../evidence/tui-settings-actions-5584c5b-20261024/rustusb-tui-actions-ipv4-first-20261024-summary.json) |
| Confirmed actions, Native IPv6 | 233 | 19 | [PASS](../evidence/tui-settings-actions-5584c5b-20261024/rustusb-tui-actions-ipv6-first-20261024-summary.json) |
| Invocation-only old TUI, IPv4 | 232 | 10 | [PASS](../evidence/tui-settings-actions-5584c5b-20261024/rustusb-tui-settings-regression-ipv4-20261024-summary.json) |
| Invocation-only old TUI, IPv6 | 232 | 10 | [PASS](../evidence/tui-settings-actions-5584c5b-20261024/rustusb-tui-settings-regression-ipv6-20261024-summary.json) |

Each action pair runs 19 actual UID1000 product PTYs: eight settings cases across
two endpoints, six confirmed fixed-profile starts, one exact incoming HID stop
and four job stops. Actual child UID/capabilities, live executed ELF, real wait0,
raw mode, resize and saved before/after termios are checked. Complete actual
terminal frames are reconstructed with the existing pinned external decoder.
Key timing alone never decides success.

For each Start, before/after committed requests must show exactly one new
UID1000/profile/job identity. The fixture then performs a **real CLI RetryStart**
on only that retained UUID to read the original job metadata; the response is
actually `replayed: true` and cannot create work. Its request list must remain
unchanged. The original TUI also displays its normal acknowledged admission.
These are replayed readback receipts for already acknowledged TUI starts, not
fabricated Started JSON, a fresh CLI Start or an installed lost-reply UI test.

The stop PTY searches the exact returned UUID and confirms `x`. Original actual
target cleanup, continued serial I/O with the same receipt, final job/session
states, all exact retained root proofs and kernel/driver baselines remain
mandatory. Every pair retains the original 264 managed assertions and three
HID/FTDI I/O rounds, external TLS classification and blocked plaintext port.
The new action scenario changes the origin of operations; it does not claim the
old CLI-start path ran unchanged. Separate invocation-only regressions retain
that original path and browser/quit/INT/TERM/held-session PTYs, explicitly using
`--no-preferences`. Together these are 58 actual PTY children, not 58 USB cases.

Settings cases save a real 0600 file in a caller-owned 0700 directory, relaunch
and show stored values in one frame, then show explicit CLI overrides while the
saved bytes remain unchanged. A real caller chmod to0644 triggers visible Save
failure without changing bytes; the disposable fixture restores the mode. The
case verifies fresh-process reload, not a separate installed `l`/`d` key gate.
The operator cannot read the actually present broker key. No host USB is passed
through or mutated. Virtual/USBFS/v2 and the whole historical matrix are not
rerun here; neither are the older local signal/backpressure scripts independently.

## Failures and preserved originals

The first uncommitted preference suite had five failures: tempfile's default
fixture root was group-writable and correctly rejected by the product. Only the
fixture was set to0700; the product check was retained, then all eleven frontend
functions and the CLI precedence test passed. Original logs are retained; that
initial failed Rust fixture source was not separately frozen as a clean build.
The C parent first failed strict compilation on misleading indentation; its
exact failed source/log remain, followed by the explicit-brace fix. There were
no failed actual QEMU runs in this slice. Earlier E45/E44 verdicts are unchanged.

The [raw manifest](../evidence/tui-settings-actions-5584c5b-20261024/manifest.json)
closes 2,612 original files/methods, a 5,986,575-byte archive and 1,250 original
guest Base64 blocks redecoded byte-for-byte. Selected inputs were checked against
the actual Git source, release/child ELF and identities against original receipts,
and every archive member/original hash reread without importing the original
verifier. This same-author review is not a new USB run or independent security
audit. ELF, private keys, host-private device output and build targets are excluded.

## Remaining scope

Full G4 still needs dedicated UI outage/permission/hotplug and unknown-start-reply
scenarios and further usability review. Administrator TLS/profile policy remains
protected and is provisioned outside the ordinary-user preferences editor.
Hardware G1/G2, full G3, permanent blocking, GUI/installers, storage expansion,
performance and Windows remain unproven or later work. The full Phase0–7 goal
stays active.

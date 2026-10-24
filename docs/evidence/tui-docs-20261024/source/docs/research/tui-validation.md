# Ordinary-user terminal preview: validation

The optional frontend now provides six real Ratatui pages backed by the existing
application service and shared metadata observers. It adds no USB privilege,
private-key loading, RPC or persistent schema. This is a usable preview, not
complete G4 or a declaration that hardware/full G3 has passed. See the
[operator guide](../tui.md) and [ownership design](tui-design.md).

## Software

Clean `ba0cedc96b1c3d9764a036c7edb6352fe49fdc93` passed all ten commands in
`scripts/check.sh`, including default/all-feature Clippy with warnings denied,
release builds, warning-strict rustdoc and locked vendor tests. The
[software receipt](../evidence/tui-d181665-20261024/software-summary.json)
records 233 stable selected before/after inputs and exact release hashes.

| Test execution | Passed | Failed / ignored |
| --- | ---: | --- |
| Default workspace | 411 | 0 / 0 |
| All-feature workspace | 506 | 0 / 0 |
| Locked nusb library | 20 | 0 / 0 |
| Total | 937 | 0 / 0 |

Five new meaningful tests cover default-cancel confirmation, repeated key events
and pending requests, exact original start retry even after expired history,
stable selection across sort, bounded search, Unicode, control/bidi removal,
all six pages and actual TestBackend draw/resize down to 1x1 and up to 240x100.
These are model/rendering checks, not USB executions or confirmed installed UI
mutation coverage. Existing session/transport/helper checks remain intact.

Ratatui 0.30.2 / Crossterm 0.29.0 are pinned. The 221-package inventory includes
46 additions (one internal frontend). Old external versions/source/checksums did
not change, but features/multiple-version dependency edges did; no claim that
all old lock blocks are byte-identical. No missing license metadata was found;
this is not a full dependency security or distribution-license audit. Effective
Crossterm defaults remain enabled transitively. The actual default CLI normal
tree excludes TUI/Ratatui/Crossterm/transport. Workspace builds still build the
separate frontend crate.

## Installed QEMU

Clean `d181665b` ran both Native IPv4 and IPv6 pairs, using the same all-feature
CLI `ffc210d57a4e22974b6ed9cf355e2c89d8351fc89bccb1cf19b3851b0983bc3e`
and helper `83c53d522d5c89a9b3384b23c17242aa3e197217fc785d62706b520c1e1473e5`
as the software gate. Each pair's 230 source/harness inputs, source/head/binary/
clean guards, two real guest exits 0 and original 264 managed assertions pass:

- [IPv4 receipt](../evidence/tui-d181665-20261024/rustusb-tui-native-ipv4-second-20261024-summary.json)
- [IPv6 receipt](../evidence/tui-d181665-20261024/rustusb-tui-native-ipv6-second-20261024-summary.json)

Each pair has ten actual product PTYs, twenty total: four per endpoint before
jobs (q, raw keyboard Ctrl-C, external INT/TERM), then a TERM case per endpoint
while the original sessions are Connected. Actual child UID 1000, empty
capabilities, live executed ELF hash, raw mode, resize 120x30→60x18, wait0,
alternate-screen leave and saved termios before/after fields are checked.
Default-cancel dialogs leave jobs/sessions empty. The held screens show actual
Incoming Connected or Import Connected in the same decoded current-page frame.

The original CLI still performs all USB start/stop actions. All three original
HID/FTDI I/O rounds, exact retained root proofs, two agent epochs/recovery,
external ciphertext/plaintext-port checks and final kernel/driver baseline remain
required. Thus browsing and UI exit precede actual I/O with the same owners.
No host USB passthrough or host-device mutation occurs. These two pairs do not
rerun the whole historical backend/CRL/v2 matrix, hardware or installed confirmed
TUI Start/Stop. Original CLI signal/backpressure tests were not separately rerun
on the host this turn; remote CI conclusions are recorded separately.

## Original failures and review

First clean `f131468e` IPv4 remains
[FAIL](../evidence/tui-d181665-20261024/first-ipv4-FAIL-summary.json), despite two
guest exits 0 and all four guards. Ten real PTYs restored successfully. After
original managed verification returned, the new verifier incorrectly searched a
difference-rendered ANSI stream for contiguous `Connected` bytes. Both actual
screens reconstruct the required role/state/current-page text; the old raw bytes
lack that contiguous substring. Read-only diagnosis is not a new USB run or a
reclassification. Only the fixture changed: pinned external pyte 0.8.2 and
wcwidth 0.2.13 reconstruct cursor movements and the sole resize clear, then
assert the same-frame role/state. Both pairs were actually rerun after the fix.
Those Python tools are test-only, external, and not bundled with the Rust product.

Initial missing source/test module, TestBackend Infallible conversion and Clippy
errors are retained. Initial diagnostic import lacked wcwidth and was corrected.
The first archive attempt falsely matched its own private-key-check source
literal; its log/method are retained, and final checking detects actual PEM key
headers. None of these failures is relabeled PASS.

One independent read-only component task retained 57 explicitly closed public
research files, exact source/licenses/versions and standalone probes. Its PTY
probes are not product coverage. The
[raw manifest](../evidence/tui-d181665-20261024/manifest.json) indexes 1,944
original files/methods, a 4,809,464-byte compressed archive and 810 original guest
Base64 blocks independently redecoded against their extracted bytes. All
selected Git inputs, original file hashes and closed archive members were
checked without importing the original verifier. This same-author review is
not an independent security audit or new USB execution. No ELF, private keys,
research target tree or host-private device output is bundled.

The [README image](../assets/tui-qemu.png) reconstructs one recorded successful
IPv4 server PTY frame, preserving characters/positions with an explicitly chosen
font and presentation colors. It is not native terminal pixels, live telemetry
or a synthetic device demo. [Provenance](../assets/tui-qemu-provenance.json) links
raw/frame/image hashes to the tested revision. The generated image was viewed;
this does not establish all terminal themes or accessibility settings.

## Still open

Persistent user preferences, administrator policy editing, installed confirmed
TUI Start/Stop/unknown-response tests, dedicated UI outage/permission/hotplug
scenarios and complete G4 remain open. Existing API/observer tests do not replace
those UI-level scenarios. Permanent OS/terminal writes, full G3, hardware G1/G2,
GUI/installation, storage safety expansion, performance and Windows remain
unverified or future work. The complete Phase 0–7 goal stays active.

# GUI-driven Native actions and window-close validation

Status: test contract,2026-10-25; extends Phase5 without replacing its remaining
rules/packaging/install/hardware requirements. Baseline: [GUI contract](native-gui-design.md),
[prior preview evidence](native-gui-validation.md), and the existing Native agent/
[TUI confirmed-action fixture](../../scripts/qemu_tui_actions/README.md).

Compose a new independent paired runner around the original agent fixture. Keep
its264 identity/kernel/root-proof/audit checks, two agent epochs, targeted HID
stop, three actual HID/FTDI I/O rounds, private-key isolation and ciphertext checks.
Use the original fixed Native profiles and the actual UID2000 agents/helpers;
only the two private guests may mutate QEMU keyboard/FTDI devices. No host USB,
public listener, product test RPC, synthetic Started/StopAccepted or new USB engine.

Use the same locked native GUI ELF and WebKit runtime under Xvfb/UID1000. Factor
runtime payload installation into a reusable function without changing the old
single-guest semantics. Give the pair an explicit4096MiB per guest; preserve768MiB
as the original runner default. Every GUI process and infrastructure child has an
actual owner, recorded PID/start identity/UID/capabilities/executed ELF and wait.
Accepted Start uses the real UI default-cancel modal, then normal confirmation.
Before/after private state identifies one actual new request. A CLI RetryStart
is readback only; it must replay the UI request and leave requests unchanged.

After Start/actual readiness, request native window close. A tiny guest-only Xlib
client finds the sole mapped top-level window whose _NET_WM_PID equals the actual
GUI child; require advertised WM_DELETE_WINDOW. Send ICCCM WM_PROTOCOLS format32,
with that atom and an actual X server timestamp. Do not DestroyWindow/KillClient
or use a process signal as window-close evidence. Source:
[ICCCM2.0 §§4.2.8/4.2.8.1](https://xorg.freedesktop.org/releases/current/doc/xorg-docs/icccm/icccm.html).
The test program is original MIT fixture code; Xlib is an existing test-only system
dependency, never product RPC or a new Rust unsafe path.

Wait for the actual GUI child0 and local-worker-joined message. Query the still
same agent/job/profile identity, Running/not-stop-requested after the window
closes. The original kernel receipts and subsequent actual I/O prove continuity;
metadata alone does not. Every start/stop GUI is a separate native process and
WebDriver session. Fail/early session startup cleanup must still retain its real
launched child before stopping the driver; forced cleanup remains a failure.

The client also opens Remote with local eligibility checked: verify a complete
nonempty authenticated two-key catalog, search/selection and actual descriptors.
Do not infer physical unplug from a busy catalog entry disappearing. First use
raw-v1 and explicitly verify unavailable RTT; framed-v2 live RTT remains another
required GUI case. Stop only the exact displayed incoming HID session and selected
jobs through real confirmation buttons; retain default Cancel and service-side
authorization. Original session cleanup/root identity checks remain authoritative.

Freeze and commit before tests; guard source/head/worktree/all three actual ELFs.
Archive original failed and successful runs separately. Run both address families
and an original single-guest/managed regression as relevant. A copied host runtime
is not a clean Ubuntu installation or hardware test. Full Phase5/Phase0–7 stays open.

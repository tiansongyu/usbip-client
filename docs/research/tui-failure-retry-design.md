# Terminal start uncertainty and agent outage

Status: scoped implementation verified at clean `4ee6be3e`, 2026-10-25; see the
[validation report](tui-failure-retry-validation.md). This extends E45/E46; it does not complete
G4, hardware acceptance, GUI or installation. No new dependency or authority.

## Product behavior

An issued Start retains its original request UUID until an actual Started
response resolves it. A timeout, denied retry or expired history cannot justify
another Start. The existing confirmation must offer RetryStart for that exact
profile/UUID. Exiting still restores the terminal and joins metadata owners.

When the initial Profiles read fails with a transient transport error, keep the
previous finite job/session/audit pages but label each stale. Clear the sampled
journal-health indication and independently sampled health values; they cannot
still look current after this failed refresh. Permission/protocol failures keep
their existing clearing behavior. Local and remote observations remain separate
sources. A later successful read replaces only its own section's freshness.

Source review found that the initial Profiles-error branch currently returns
without changing the three section labels or journal/health samples. This is a
display defect, not evidence that service resources stopped or were released.

## Installed experiment

Use the existing disposable Native managed QEMU composition and its actual
UID1000 product PTYs, preserving all original 264 checks, three HID/FTDI I/O
rounds, two epochs, exact root proofs, kernel baselines and external encryption.
Only the first server and HID Start PTYs use a new guest-only controlled driver.

After initial metadata sampling, the root fixture SIGSTOPs the existing agent,
records its actual stopped state and unchanged process identity, then authorizes
the PTY to confirm Start. The five-second product IPC deadline expires while
the agent remains stopped. After a bounded observation window, record the
unchanged committed store, SIGCONT the same agent and wait for its real queued
Start to commit. The PTY then confirms the original retry. Require actual
decoded frames for unknown response, original-UUID confirmation and resolved
original job, one new durable request/job, and normal child exit. No response
JSON is invented and no second CLI Start is used. The existing exact RetryStart
readback remains metadata evidence after the UI has resolved its own ambiguity.

In the same PTY, sample the admitted job on Connections/Jobs. Pause the same
agent again, refresh, and require the retained exact job with a stale section
label. Resume it, refresh, and require that exact job in a current page. Record
phase files, monotonic timestamps, process status/stat, actual wait/UID/ELF,
capabilities, raw ANSI stream, saved termios and terminal restoration. Timing
drives the fixture only; decoded frames and real committed identities determine
the verdict. SIGSTOP is a reversible disposable-process fault, not a claim
about wire loss, a permanently blocked kernel/filesystem, or suspension during
an active USB operation.

Keep the original E46 composition runnable with its normal-ACK requirement.
Run the new dual-stack scenarios and that original dual-stack regression after
the full software gate. Preserve all failed runs, including fixture failures.
Installed UI hotplug and authoritative permission-denial scenarios remain
separate outstanding work; existing lower-layer tests do not complete them.

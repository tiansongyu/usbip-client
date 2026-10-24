# Terminal hotplug, real authorization refusal and settings reload

Status: scoped implementation verified at clean `2dc888ce`, 2026-10-25; see
[validation and G4 audit](tui-hotplug-permission-validation.md). Continues E45–E47 and requirements
4.9/Phase 4. No new dependency, RPC, USB privilege or administrator-policy editor.

Use a new single-guest composition of the existing local-device-monitor QEMU
fixture. Preserve its six completed inventory points, kernel-hint counters,
independent udevadm/sysfs observations, same observer/socket identity and final
empty baseline. Add an actual UID1000 product PTY alongside that observer.
QMP adds/removes/reinserts only a disposable keyboard; no host USB passthrough,
NIC, helper process, USB/IP driver or device redirection is needed in this test.

The UI searches the real keyboard VID/PID, displays its actual detail/address,
retains an open details panel while the device disappears, and shows the new
address after reinsertion at the same port. Complete decoded frames must agree
with independently recorded current inventories. Observe the UI's own kernel
group-1 socket and actual UID/ELF/PID before and after, not just the other
observer. Bounded scheduling windows coordinate events; actual frames/current
facts decide the result. This does not prove every transient event or a USB/IP
hot-unplug data path, which has separate prior evidence.

Source review identifies a related display risk: details keep their old scroll
offset when the selected row disappears or its generation changes. A short
empty-selection message can then be scrolled entirely out of view. Require the
open panel to show its actual empty-selection feedback after removal. If
reproduced, reset detail scrolling when effective stable selection changes,
while preserving scrolling across a reorder of the same identity. Add a real
TestBackend regression and retain the old-product negative before fixing it.

Run an ordinary UID2000 real application agent with a root-managed Native
profile and operator UID1000 Read/Audit only. The profile has a deliberately
unused fixture device key; no Start is authorized. The real UI confirms Start,
shows the service Permission result, offers only its original UUID retry, then
shows the second real refusal. Require two durable denied audit events, no
jobs/sessions/request admission and no helper or USB/IP drivers. Exiting with
that unresolved identity is expected child exit1 after real terminal restoration
and observer join; it must not be relabeled as a normal child exit0. The PTY
parent succeeds only when that exact expected failure was actually observed.

Additional PTYs use Settings `d` to restore in-memory defaults, then `l` to load
a changed, checked user file. Root changes only this known fixture file between
phases; it is not a product Save. A separate live mode change to0644 makes `l`
fail visibly while preserving effective options and bytes. Existing explicit
save/override and unknown Start/normal USB-action matrices remain separate.

Also verify actual root, JSON and redirected-terminal rejection commands with
returned exit1, empty stdout and the exact supported guard error. These fast
commands have no separate live /proc sample; do not imply that they do.

The G4 audit also checks the compound keyboard path through sort, attention
filter, bounded search and exact-target stop confirmation with the real App
handler/channel and TestBackend. Filtering an empty result must not issue a
stop; searching a UUID must not change refresh options. This proves UI command
selection, not authorization or actual release, which remain service concerns.

Retain actual PTY wait, saved termios, raw/resize/alternate screen, live ELF and
zero capabilities. Capture protected public configuration and private-key
metadata only. All accepted agent/observer/PTY children are actually joined;
bounded failure collectors preserve original failures before terminating the
owned disposable VM. Run the minimal `tui` selection and all-features selection
separately, recording their distinct executables. Run the full software gate and
current managed fault regression, record failures, update English documentation,
commit by meaningful units and normally push. Audit G4 against its actual
requirements after obtaining evidence; hardware/full G3 and Phase 5–7 remain open.

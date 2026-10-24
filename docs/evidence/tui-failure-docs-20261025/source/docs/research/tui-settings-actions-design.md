# Persistent frontend settings and installed terminal actions

Status: scoped implementation verified at clean5584c5b3, 2026-10-25; see the
[validation report](tui-settings-actions-validation.md). This continues the six-page frontend
without changing the original hardware/full G3/G4 gates.

## Settings

Store only refresh interval, automatic polling and monochrome preference in
`preferences.json`, strict schema 1, nested `options`, at most 4 KiB. No profile,
endpoint, device key, private credential, request ID or automatic start is stored.
A saved option never grants an agent/helper action. Use existing pinned serde,
Rustix 1.1.5, Tokio 1.54.1 and std filesystem primitives already used by
`rustusb-service/src/fs.rs`; no new component or external dependency is selected.

The [XDG Base Directory 0.8 specification](https://specifications.freedesktop.org/basedir/latest/)
places user configuration below XDG_CONFIG_HOME, falling back to HOME/.config.
Ignore relative/empty XDG paths, then require an absolute home fallback. The
frontend uses a rustusb subdirectory, supports an explicit
`--preferences-directory`, and offers `--no-preferences`. With no usable home,
preferences are visibly disabled. CLI flags override loaded values for this run;
only explicit Settings `w` saves. `l` reloads; `d` restores in-memory defaults.
Existing positive/negative flag pairs make boolean overrides explicit.

Load is read-only and does not create missing directories. Save walks descriptor
relative paths without symlinks, accepting protected root/caller ancestors (root
sticky temporary ancestors are allowed), creates missing directories 0700 and
requires the final directory to be caller-owned 0700. Files are owner-only 0600,
regular, single-linked, NOFOLLOW/NONBLOCK, with before/after identity/size/time
checks. A fresh OFD/nonblocking lock serializes cooperative saves. Reject an
unknown/malformed existing document instead of overwriting it. Write a unique
EXCL temporary, sync it, recheck directory and previous document, rename and
sync the directory. Remove only this operation's still-matching temporary.
Report uncertainty after publication failures; do not roll back by deleting the
published file. These checks do not defeat an arbitrary hostile process under
the same UID or guarantee power-loss behavior on every filesystem.

All startup load/save/reload filesystem work runs in accepted blocking tasks.
Signals stop new work but do not discard a started task; UI exit truly joins it
and both metadata observers. No frontend task owns USB resources. Filesystem
hard blocking remains an explicit limit. Settings errors stay visible and do
not imply any USB operation failed or was rolled back.

## Installed action evidence

Add a separately versioned QEMU composition using the Native managed fixture.
Actual UID1000 product PTYs confirm Start for each fixed server/client profile,
then really exit before original enumeration and HID/FTDI I/O. The root fixture
observes the newly committed request/job and queries only its exact retained
RetryStart to obtain a real, replayed metadata response for the original
identity/proof verifier. No synthetic Started/StopAccepted JSON is created.

For targeted stop, the PTY searches the exact returned session UUID, confirms
`x`, and the fixture waits for actual target cleanup while the original serial
session/receipt continues I/O. Original kernel, epoch, ciphertext and exact root
proof checks remain mandatory. This is a distinct UI-driven scenario, not a
claim that the old CLI-start regression ran unchanged. Run the unchanged old
TUI regression separately and retain original failures.

Additional PTYs save settings, relaunch/reload, exercise CLI overrides without
saving and show a real permission-change save error. Compare the actual file,
owner/mode and reconstructed complete terminal frames, retaining actual UID,
capabilities, executed ELF, wait and termios receipts. Unit tests cover filesystem
bounds/trust/atomic conflict and CLI precedence. No confirmed-action test uses
host USB or changes product authorization to simplify the fixture.

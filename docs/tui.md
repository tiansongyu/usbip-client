# Terminal frontend

RustUSB has an optional Linux Ratatui frontend for ordinary operators of an
already running application agent. It uses shared local discovery and the same
fixed-profile authenticated API as `rustusb agent`. It never starts the agent,
loads TLS keys, executes usbip or performs privileged operations directly.

```sh
cargo build -p rustusb-cli --features tui --release --locked
./target/release/rustusb tui --config /etc/rustusb/agent.json
```

Use the stable toolchain in `rust-toolchain.toml`. Run as a nonroot operator
with terminal stdin/stdout and permissions in the public root-managed manifest.
The broker/helper must already be configured; see the
[application service guide](application-service-guide.md). `--json` is rejected;
use existing `agent` commands for machine-readable output.

Six pages show Local devices, Remote authenticated catalogs, Connections,
System state, Logs and Settings. Connections distinguishes actual job/session,
operation error, cleanup and independently sampled link health. Logs reads typed
durable audit pages with separate Audit permission.

| Key | Action |
| --- | --- |
| `1`–`6`, Tab, Shift-Tab | Change page |
| `[` / `]` | Previous/next readable fixed profile |
| arrows, `j` / `k` | Move stable selection |
| `/`, Enter / Esc | Edit/finish bounded search; Esc outside editor clears filters |
| `s`, `f` | Reverse label sort; toggle attention filter |
| Enter, arrows, Esc | Open, scroll and close details |
| `v` on Connections | Switch session/job pages |
| `n`, `b` on Connections/Logs | Next service page; first page |
| `r` | Refresh agent metadata |
| `a`, `-` / `+`, `m` | Polling, 2–120 second interval, monochrome |
| `w`, `l`, `d` on Settings | Save, reload, restore in-memory defaults |
| `e` | Confirm whole-profile start or retry unresolved original start |
| `x` on Connections | Confirm stop of exact selected session/job |
| `?` | Help |
| `q`, Ctrl-C, external INT/TERM | Exit frontend; service jobs continue |

Confirmations initially select Cancel. Tab/arrows select Confirm, then Enter
submits. Starting applies to **all devices in the fixed profile**, regardless of
which device row is being browsed. Stopping a job may affect several sessions.
Accepted stop is not proof of release: inspect actual cleanup before reuse.
The [G4 software/QEMU audit](research/tui-hotplug-permission-validation.md#g4-requirement-audit)
links each documented terminal requirement to source, rendered tests and actual
ordinary-user product runs; hardware and full release gates remain separate.

A lost start response retains the original UUID and blocks new start intents.
`e` uses only `RetryStart`; expired retry history does not prove the old start
never ran. A real service Permission refusal also retains that identity
conservatively; a refused retry does not authorize a new intent. Exiting with an
unresolved start returns 1 after terminal restoration and metadata-owner joins,
reporting the retained profile/UUID for reconciliation.
[Installed fault validation](research/tui-failure-retry-validation.md) exercises
this with a paused/resumed QEMU agent and the real operator terminal.

Search/sort/filter cover only the displayed finite page (up to 16 service rows).
Local details follow the current selected identity. Removal or a new device
generation resets the details scroll, so empty-selection feedback remains
visible; reordering the same identity preserves scroll. Filtering a row out
does not prove a physical unplug. Section snapshots are independent samples.
Read failures retain visibly stale
data. Failure of the initial agent read marks job/session/audit pages stale,
clears the previous journal-health indication and drops old link samples. It
does not remove the retained jobs or prove any device was released. A successful
read makes only its own section current again. Permission/protocol failures
clear the affected view. Remote discovery
runs only on its page for a client profile with Discover permission, and joins
on pause/profile/page change. Local kernel observation continues while profile
polling is paused. Joining the local catalog observer does not cancel an already
accepted agent Discover request or resolver: the agent retains and joins its own
bounded work. Raw-v1 RTT is unavailable; catalog/PONG is not USB progress.

Settings persist only when you explicitly press `w` on Settings. The strict
schema-1 file stores refresh interval, automatic polling and monochrome alone.
The default location is `$XDG_CONFIG_HOME/rustusb/preferences.json`, or
`$HOME/.config/rustusb/preferences.json`. Relative/empty XDG values are ignored;
without an absolute home fallback, persistence is visibly disabled.

Use `--preferences-directory /absolute/user/directory` to choose another checked
location or `--no-preferences` for invocation-only behavior. Startup flags
`--refresh-seconds 10`, `--auto-refresh` / `--no-auto-refresh`, and
`--color` / `--monochrome` override loaded values without saving them. Later
keyboard changes apply in memory. `l` explicitly reloads the file/defaults and
replaces current values; `d` restores in-memory defaults. Only `w` writes.

The directory must be caller-owned0700; files must be0600 regular/single-link.
Ancestors must be protected root/caller directories (root sticky temporary
ancestors are allowed). Symlink paths are refused. Load creates nothing; Save
creates missing directories with requested0700, never relaxes existing modes,
and refuses an unknown/malformed file. A save error is visible; an error after
rename can mean publication is uncertain. Filesystem workers are really joined
on exit, so permanent OS blocking can delay return. Fix an unsuitable user path
or choose `--no-preferences`; do not run the UI as root to bypass this check.

No profile, endpoint, device key, credential, request ID, automatic Start or
permission is stored. Administrator TLS/profile policy is managed separately.
[Exact validation](research/tui-settings-actions-validation.md) records actual
confirmed TUI actions, persisted settings and separate old regressions. Complete
Phase4/G4, hardware G1/G2 and full G3 remain open.

The viewport is bounded to 240x100 cells, untrusted display controls/bidi marks
are stripped, and narrow terminals have a details overlay. Exit joins issued
frontend requests and actual metadata owners; permanent OS/terminal blocking is
not claimed cancellable. See the [ownership design](research/tui-design.md).

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
| `e` | Confirm whole-profile start or retry unresolved original start |
| `x` on Connections | Confirm stop of exact selected session/job |
| `?` | Help |
| `q`, Ctrl-C, external INT/TERM | Exit frontend; service jobs continue |

Confirmations initially select Cancel. Tab/arrows select Confirm, then Enter
submits. Starting applies to **all devices in the fixed profile**, regardless of
which device row is being browsed. Stopping a job may affect several sessions.
Accepted stop is not proof of release: inspect actual cleanup before reuse.

A lost start response retains the original UUID and blocks new start intents.
`e` uses only `RetryStart`; expired retry history does not prove the old start
never ran. Exit reports the retained profile/UUID for reconciliation.

Search/sort/filter cover only the displayed finite page (up to 16 service rows).
Section snapshots are independent samples. Read failures retain visibly stale
data; permission/protocol failures clear the affected view. Remote discovery
runs only on its page for a client profile with Discover permission, and joins
on pause/profile/page change. Local kernel observation continues while profile
polling is paused. Raw-v1 RTT is unavailable; catalog/PONG is not USB progress.

Settings currently apply only to this invocation. Repeat launch settings with
`--refresh-seconds 10 --no-auto-refresh --monochrome`. Persistent user preferences,
administrator policy editing and complete Phase 4 acceptance remain unfinished.
The frontend does not complete hardware G1/G2 or full G3.

The viewport is bounded to 240x100 cells, untrusted display controls/bidi marks
are stripped, and narrow terminals have a details overlay. Exit joins issued
frontend requests and actual metadata owners; permanent OS/terminal blocking is
not claimed cancellable. See the [ownership design](research/tui-design.md).

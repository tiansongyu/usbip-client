# Native desktop preview

The Linux Tauri application uses the same headless observation/action worker as
the TUI. Local and remote inventories, profile actions, jobs, sessions, audit and
preferences all use the existing libraries and authenticated application service.
This is an early preview; automatic sharing rules and packaged installation
remain unfinished.

## Build and open

Use an updated Ubuntu22.04/24.04 installation, Rust1.97.1 and GTK3/WebKitGTK4.1
development packages. From the repository root:

```sh
sudo apt-get install build-essential pkg-config libgtk-3-dev libwebkit2gtk-4.1-dev
cargo build --manifest-path apps/rustusb-gui/Cargo.toml --release --locked
apps/rustusb-gui/target/release/rustusb-gui --help
```

An administrator must first provision and run the
[application agent](application-service-guide.md). As its authorized ordinary user:

```sh
apps/rustusb-gui/target/release/rustusb-gui --manifest /etc/rustusb/agent.json
```

The manifest is protected public metadata. The GUI does not read private TLS keys,
start the agent/helper, run shell commands or acquire USB handles. Do not run it
as root. Native Linux GTK/WebKit libraries are separate system dependencies.

## Use the console

- **Local devices:** search by name, VID:PID or Bus ID, filter eligible candidates
  and select a row for descriptors/current generation. Eligibility is not tested
  compatibility. Unplug/re-enumeration clears the old selection.
- **Remote devices:** select a configured client profile with Discover permission.
  Opening this page starts the shared catalog observer; leaving it stops and joins
  the local observer. A completed local join does not cancel an agent's already
  accepted remote read.
- **Connections:** inspect bounded job/session pages, operation errors, evidence
  and independent cleanup. Request stop for an exact returned identity. Stop
  acceptance does not establish release. Link-health detail shows measured RTT
  only when an actual transport sample contains it; raw-v1 has no heartbeat RTT.
  Expansion, focus and metadata scroll survive updates for the same connection.
  Ended/previous-epoch samples are labeled retained history; recorded RTT is not
  a current responsiveness result. Read operation error and cleanup separately.
- **Activity / System:** inspect authorized audit pages, retained-history gaps,
  journal availability and independently sampled observer state.
- **Settings:** apply, explicitly save or reload checked user preferences. These
  share the TUI's `~/.config/rustusb/preferences.json` (or absolute XDG directory).
  `--settings-directory` chooses another trusted user location. No preference
  enables automatic device actions or changes administrator policy.

Start applies the complete fixed profile, regardless of the selected row. Both
Start and exact Stop show a default-cancel confirmation. Permission is checked
again by the actual agent. If a Start reply is refused/lost/unknown, its original
UUID remains in Rust across renderer reload. **Resolve original start** uses only
RetryStart; an expired/unknown retry never issues a new Start. Inspect jobs and
follow the displayed identity before administrator reconciliation.

At narrow window sizes, **Show details / Show list** switches the inventory pane.
Use Tab to focus controls and the scrollable metadata area. Closing the native
window or SIGINT/SIGTERM signals cooperative local shutdown and joins accepted
GUI work/observers. Service-owned USB jobs continue independently. An unresolved
Start makes the frontend exit1 after local joins and prints its safe retry ID;
that is separate from the agent's USB cleanup state.

## Validation and limits

[Recorded preview validation](research/native-gui-validation.md) includes real
WebKit IPC, hotplug, actual OS-peer denial/original retry, checked preferences and
wide/narrow geometry. [Confirmed-action validation](research/gui-native-actions-validation.md)
adds IPv4/IPv6 GUI Start, exact session/job Stop, a nonempty authenticated remote
catalog and actual USB I/O after native windows close. The fixed QEMU HID/FTDI
cases use raw-v1, which correctly shows RTT unavailable.
[Framed-v2 validation](research/gui-link-health-validation.md) separately covers
actual same-connection RTT, history/typed ends after three directional faults,
and preserved reading state on both address families. Automatic rules,
`.deb`/systemd, install/upgrade/uninstall/reboot, full GUI and hardware/Windows
acceptance remain unfinished.

Run `scripts/check-gui.sh` for the independent GUI software gate. GTK is excluded
from the root Native workspace; its root checks do not replace the GUI gate.
The [scoped GLib fix](../apps/rustusb-gui/vendor/README.md) preserves the published
version and licenses; version scanners can still report0.18.5. No whole-GTK or
complete dependency-security audit is claimed.

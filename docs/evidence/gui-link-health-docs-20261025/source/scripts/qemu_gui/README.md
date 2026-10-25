# Native GUI in QEMU

Run from an ordinary x86_64 Ubuntu user with the repository's Linux kernel/QEMU
prerequisites, GTK3/WebKitGTK4.1/Xvfb/curl/bwrap/xdg-dbus-proxy/dbus/xauth/xdotool
installed, plus a matching WebKitWebDriver. The driver may be extracted from the
Ubuntu `webkit2gtk-driver` package; pass its actual ELF path. No host package
installation is performed by this runner.

```sh
scripts/check.sh
scripts/check-gui.sh
python3 -B scripts/qemu_gui/run.py \
  --driver /path/to/WebKitWebDriver --evidence /tmp/rustusb-native-gui-acceptance
```

Requires clean committed source and already built root all-features and GUI
release binaries. Records source/ELF/kernel/initramfs hashes and guards, actual
QEMU wait and original ttyS0/ttyS1 logs. The payload copies host native libraries,
fonts and WebKit child programs into an ephemeral rootfs; this is a runtime test,
not a `.deb` or clean Ubuntu installation qualification. Only copying a protected
kernel image may use noninteractive sudo. QEMU has no NIC or host USB passthrough.

Guest PID1 alone provisions a synthetic read/audit-only real agent. Actual UID1000
WebKitWebDriver launches the real packaged Tauri GUI. It checks native bridge,
strict input, QMP keyboard hotplug/generation, search/detail removal, six pages,
default-cancel confirmation, actual permission-denied Start/original RetryStart
across renderer reload, checked preferences and narrow-window screenshots.
SIGTERM waits for the actual GUI child (expected exit1 because Start is unresolved),
local observer join, and independent agent read/shutdown. No Start is admitted;
no active-job continuation, TLS data redirection, latency benchmark, hardware,
installation or full Phase5 acceptance is inferred from this case.

Guest diagnostics have a separate bounded Base64 collector. Parent independently
verifies sizes/SHA, identities, zero effective/permitted capabilities, retained
request UUID and the agent's two denial records/no jobs. Failures and forced
cleanup remain failures; use a new evidence directory for every run.

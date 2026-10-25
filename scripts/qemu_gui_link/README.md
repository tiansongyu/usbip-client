# Native GUI same-data RTT and selective network loss

Use a clean committed checkout with built root all-features/GUI releases and
the [native runtime prerequisites](../qemu_gui/README.md):

```sh
python3 -B scripts/qemu_gui_link/run.py --driver /path/to/WebKitWebDriver \
  --family ipv4 --timeout 600 --evidence /tmp/FRESH-gui-link-ipv4
```

Repeat with IPv6 and a fresh output directory. This wrapper keeps the original
[framed-v2 network/lifecycle scenario](../qemu_data_link_health/README.md), its
three exact-flow directional blackholes,1000/3000ms probe policy, seven HID/FTDI
rounds, sibling and original root proof/baseline checks. Only private disposable
QEMU guests mutate synthetic USB devices. Guest memory is explicitly4096MiB;
the original runner retains its768MiB default.

Three real UID1000 native GUI processes observe the exact original HID sessions.
Rendered metadata and RTT text must match; expanded health details/keyboard focus
survive actual advancing reply samples and the fault. The same window then shows
retained ended history and independently verified cleanup. Actual WM_DELETE_WINDOW,
child wait0 and local-worker join precede the next original fresh attempt.

No fake RTT/PONG, snapshot injection or GUI-authorized new USB operation is used.
CLI/helper/GUI/driver/source/head/worktree guards stay frozen until actual terminal;
forced termination is recorded and never a pass. These copied-runtime guests
are not clean Ubuntu installation, full GUI/Phase5 or hardware qualification.

# Native GUI confirmed actions and real window close

From a clean committed checkout, with the root all-features release and independent
GUI release built and the same GTK/WebKit/Xvfb/Mesa runtime prerequisites as
[the original GUI runner](../qemu_gui/README.md):

```sh
python3 -B scripts/qemu_gui_actions/run.py --driver /path/to/WebKitWebDriver \
  --family ipv4 --timeout 600 --evidence /tmp/new-gui-actions-ipv4
```

Repeat with ipv6 and a fresh evidence directory. Only copying the protected kernel
image may use sudo. No host USB passthrough, public listener or host network bridge
is used. Two disposable4096MiB guests communicate over the original private QEMU
NIC socketpair; GUI automation is guest loopback only. Xlib is test-only; the small
original C client sends an advertised ICCCM WM_DELETE_WINDOW to the sole mapped
window of the recorded GUI PID, using an actual X server timestamp. It does not
DestroyWindow, KillClient, or substitute SIGTERM for a native close request.

Twelve real UID1000 WebKit/Tauri windows confirm six original profile starts,
one exact incoming HID stop and four job stops, plus a nonempty authenticated
remote catalog/search/detail flow with local eligibility selected. Each GUI is
actually waited0, its metadata worker joined, and the same agent job stays Running
after each Start window closes. Original subsequent kernel enumeration/I/O proves
continuity. CLI RetryStart is safe readback of the GUI's actual request, never a
second Start or fabricated acknowledgement. All original264 managed Native checks,
two agent epochs, three HID/FTDI I/O rounds, root terminal identity proofs, exact
baseline restoration and encrypted-only external transport remain authoritative.

Original errors and complete failure artifacts are retained; forced cleanup is
not a pass. These copied-runtime guests are not clean Ubuntu install/deb/systemd
qualification. This raw-v1 case has no measured heartbeat RTT and does not complete
automatic sharing rules, full GUI/accessibility/installation, hardware or Phase5.

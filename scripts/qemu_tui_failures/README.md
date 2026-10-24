# Terminal unknown Start and stale job pages

Use the pinned test-only decoder from `../qemu_tui/requirements.txt`:

```sh
python3 -B scripts/qemu_tui_failures/run.py --family ipv4 --kernel 6.8.0-106-generic --timeout 600 --evidence /tmp/new-tui-failure-evidence
```

This composes the E46 installed Native actions without host USB passthrough.
Two of its nineteen actual UID1000 product PTYs exercise a real five-second
Start timeout while the same guest agent is SIGSTOPped. Resume the original
process, wait for actual durable admission, then confirm the original UUID in
the UI. Require unknown and resolved frames, one real request/job, and wait0.
A second pause/refresh/resume in each PTY requires that exact retained job to
transition current/stale/current. The parent records actual process identity,
stopped states, committed stores, bounded phase timing and terminal restoration.

No fake Started JSON or alternate IPC server is used. All original 264 checks,
three HID/FTDI I/O rounds, two epochs, exact root proof and driver baselines
remain mandatory. Other starts keep their normal-ACK requirement. Run E46
separately as the normal-action regression. SIGSTOP here does not prove wire
loss, suspension during active USB I/O or permanent kernel/FS cancellation.
UI hotplug and service authority-denial scenarios remain outstanding.

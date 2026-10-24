# Terminal hotplug, settings reload and authoritative refusal

Use the pinned external decoder from `../qemu_tui/requirements.txt`:

```sh
python3 -B scripts/qemu_tui_interactions/run.py --features all --kernel 6.8.0-106-generic --timeout 300 --evidence /tmp/new-tui-interaction-evidence
```

`--features tui` builds the workspace with only `rustusb-cli/tui` enabled;
`all` builds the workspace with all features. Record each executable separately.
One VM has no NIC or host USB passthrough. A real TUI runs as UID1000 alongside
the original UID2000 local observer; all six original inventory/kernel/driver/
generation checks remain. The actual open detail panel must show disappearance
and the newly inserted address. No helper process or USB/IP driver is started.

Three further PTYs exercise Settings defaults, actual changed-file reload,
visible reload-permission failure, and a real Read/Audit-only agent policy.
The permission PTY confirms Start and the original retry; two durable denials
and zero requests/jobs/sessions are required. Its actual child exit is expected
**1**, after terminal restoration and metadata joins. Other children must exit0.
No Started/error response JSON or alternate IPC server is fabricated.

Timing only schedules the fixture. Actual decoded terminal frames, independent
kernel/sysfs observations, process/socket/ELF/termios identity and committed
agent state decide the verdict. This is UI observation/authorization evidence,
not a new USB/IP redirection, hardware or performance test.

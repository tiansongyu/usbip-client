# Local USB inventory in a disposable QEMU guest

Run as an ordinary x86_64 host user with the existing Native QEMU prerequisites,
plus `udevadm` for an independent kernel-event capture inside the guest:

```sh
python3 -B scripts/qemu_device_monitor/run.py --features default --evidence /tmp/monitor-default
python3 -B scripts/qemu_device_monitor/run.py --features all --evidence /tmp/monitor-all
```

Each invocation builds the selected locked release and boots **one** isolated
Linux guest without a NIC. Guest root loads USB modules and controls only its
QEMU keyboard. The actual ordinary UID2000 CLI observes an initially empty
inventory, add, interface unbind/rebind, remove and reinsert at the same port.
The guest independently samples sysfs generation/address and the running CLI's
UID/executable/socket. A separate `udevadm --kernel` capture records actual USB
actions. Notification counters must advance, so periodic-only behavior cannot
pass the event predicates. The observer receives SIGTERM and is actually waited;
QMP removes the final virtual device before guest shutdown.

Two UARTs keep kernel and program evidence separate. Raw base64 blocks, payload
hashes, selected sources, actual QMP operations, driver observations, process
receipts and complete JSON batches are retained. Any setup, guest, verifier,
timeout or integrity failure remains FAIL even if QEMU exits zero. Four source,
head, working-tree and binary guards must remain true. These guards do not claim
to sample host USB state. The copied helper binary is not executed.

The two feature selections are separate single-guest runs, **not** paired USB/IP
redirection, real hardware, induced ENOBUFS, guaranteed transient capture, VPN or
performance acceptance. See the [design](../../docs/research/local-device-monitor-design.md).

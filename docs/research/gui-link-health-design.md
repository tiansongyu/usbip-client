# Native GUI same-data-link RTT and fault presentation

Status: test/implementation contract,2026-10-25. Continues original Phase5;
automatic rules, packaging/install lifecycle and hardware gates remain required.
Read with [GUI ownership](native-gui-design.md),
[confirmed actions](gui-native-actions-design.md) and the existing
[data-link contract](data-link-health-design.md).

Reuse the original Native framed-v2 directional-blackhole runner, lifecycle
verifier and byte-exact packet audit. Keep the1000/3000ms policy, private valve,
actual tuple/connection binding, all three directions, seven HID/FTDI rounds,
persistent sibling, fresh Catalog, exact original root proofs and full baseline.
Keep its768MiB default; a new native GUI wrapper explicitly uses4096MiB. Record
forced guest termination and actual waits without changing any verdict assertion.

In each fault phase launch one real UID1000 client GUI, using the existing
WebKit runtime/launcher and original WM_DELETE_WINDOW client. Before arm, open
Connections for the real HID profile and exact session. Its actual DOM metadata
must identify the same instance/session/data-connection as the original agent
health receipt, with a real nonnull RTT/reply sequence. Compare the displayed
enqueue-to-reply microseconds to that exact DOM metadata, not a separately timed
snapshot. RTT includes queueing/scheduling; it is not wire/USB latency.

First reproduce the suspected refresh defect with the unchanged product ELF:
open/focus the real native health disclosure, await an actual advancing reply
sequence in the rendered metadata, then capture whether expansion/focus survived.
Keep any FAIL as an original, not a post-hoc PASS. Source shows renderPage rebuilds
the Connections subtree on every update; implementation must preserve disclosure
state and focus using stable profile/session identities, without retaining old
health samples as live. Memory must stay bounded to the current sampled page.

While the same GUI/agent/session is alive, arm the original exact flow drop.
After the original actual cleanup barrier, capture the same GUI's ended link,
historical availability, typed end/operation result and independent verified
cleanup. At least one endpoint timeout remains the original transport assertion;
an asymmetric client may see another actual end reason. Never relabel last RTT
as a current successful probe or network timeout as USB release. User-visible
summary should distinguish live versus retained history and show typed phase/end.
Preserve expansion/focus through actual updates; a vanished identity/profile
must not transfer it to another connection.

The native close must actually wait0/join local work. Then original sibling I/O,
fresh HID reuse and final owner/root proofs proceed unchanged. GUI only observes
existing read-authorized IPC; no injected snapshot, fake RTT/PONG, extra Start,
new USB capability or product test RPC is permitted. Capture actual PID/start,
UID/capabilities/ELF, GUI/driver waits and screenshots. Keys/rootfs/ELFs stay private;
host USB metadata is never part of this fixture.

Commit/freeze source/head/worktree/CLI/helper/GUI/driver until actual terminal.
Run new GUI pairs for both address families, an original framed-v2 regression,
and relevant original GUI/action regressions after shared fixture changes. Full
root and separate GUI gates stay mandatory. Archive failures, methods and final
raw originals with exact source attribution; copied runtime is not clean Ubuntu
package/hardware or complete GUI/Phase5 acceptance.

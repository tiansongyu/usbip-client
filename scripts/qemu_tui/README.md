# Installed TUI PTY regression

`run.py --family ipv4|ipv6 --evidence /tmp/unique-directory` composes the unchanged
Native managed two-guest verifier: three HID/FTDI I/O rounds, two owner epochs,
root proofs, final kernel baseline and ciphertext classification all remain.
No host USB passthrough or mutation is performed.

Each guest first runs four actual product PTYs as UID 1000: q, keyboard Ctrl-C,
external INT and TERM. The C parent drives real input, resizes 120x30 to 60x18,
observes raw mode, samples actual UID/capabilities/executed ELF hash, truly waits
and compares original termios flags, control characters and speeds. Raw output
and alternate-screen bytes are retained. Default-cancel confirmation must leave
jobs/sessions empty. Remote discovery uses the real authenticated agent catalog.

This is metadata/navigation/shutdown coverage, not installed confirmed TUI USB
start/stop, every setup failure, permanent terminal blocking or full Phase 4.
Original CLI actions still own all actual USB starts/stops in this regression.
No timeout is accepted as a successful child wait.

A fifth TERM-driven PTY in each guest reads actual Connected sessions in the
original held epoch, then returns before the original I/O. Saved before/after
termios numeric fields are independently compared, including Linux line
control. Thus each pair has ten real PTY children, without extra USB sessions.

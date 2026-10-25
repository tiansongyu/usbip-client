# Native GUI automatic rules in disposable QEMU

Run the complete root and separate GUI gates first. The host must be an ordinary
x86_64 user with KVM, the documented native GUI runtime and WebKitWebDriver.

```sh
python3 scripts/qemu_gui_rules/run.py --family ipv4 \
  --driver /absolute/path/to/WebKitWebDriver --evidence /tmp/unique-gui-rules-evidence
```

The wrapper retains the original paired Native verifier, all264 assertions,
three HID/FTDI I/O rounds, exact driver identities and full Root proofs. Only its
server guest installs native WebKit/Xvfb (4096MiB per guest). It first observes the
real no-policy agent, then the existing explicit root-policy upgrade. Three real
UID1000 GUI processes test disabled Create, two Replace edits, stale draft
preflight, explicit Enable/rearm, future-only Disable/Delete, events and native
WM_DELETE_WINDOW/actual wait/local-worker join while agent jobs continue.

A real SIGSTOP of the guest agent delays the original disabled Create past the
frontend's five-second IPC deadline. Reload the actual renderer while its Rust
worker still owns the admitted slot. Resume the real agent, collect its actual
late commit and query only the original UUID from the native GUI. No relay,
synthetic service receipt, product test API or host USB operation is used. The
failure trap resumes a stopped agent before original owned cleanup.

This tests a real unknown outcome and native frontend ownership, not process-crash
UUID persistence, settled-negative recovery, physical hardware or installation.
Do not change source, HEAD or ELFs until all running fixture children terminate.
Failures remain separate original evidence. Run both families and original GUI,
Native actions and framed-v2 regressions; preserve their existing verifiers.

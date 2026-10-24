# Native GUI originals

`originals.tar.gz` contains3745 original source/command/log/result files (53683154B),
including failed runs, frozen root/GUI gates, seven native QEMU attempts, the
original-parent-failure/final TUI runs, and development methods. `manifest.json`
lists every member's byte length/SHA; `receipt.json` pins the archive. The archiver
inside `methods/` re-read every member and verified exact contents. No private
keys, host USB inventory/screenshots, ELF, target trees or runtime rootfs are
published. Host read-only inventories remain in private temporary locations.

The separate `glib-evidence.tar.gz` has500 source/probe/method/raw-output originals;
`glib-manifest.json` and `glib-receipt.json` pin that independent component evidence.
These are same-author integrity checks, not a new independent security audit.

Tested revisions/results and interpretation are in
[the native GUI report](../../research/native-gui-validation.md). Failed originals
retain their original status; a later verifier diagnosis does not rewrite a run.

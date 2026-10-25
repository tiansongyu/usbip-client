# Native GUI confirmed-action originals

`originals.tar.gz` contains4934 original source/command/log/result files,
57902451 bytes. The compressed archive is13141249 bytes, SHA256
`be5d7496891005aae35698574c212696a42003f9410ebdfcdb3132a77c12d1a2`.
[manifest.json](manifest.json) records every member; [receipt.json](receipt.json)
pins the archive. The method inside `methods/` rereads and checks every member.

Included: three failed GUI action pairs, the intermediate IPv4 PASS, final
same-clean-source core/GUI gates, IPv4/IPv6 action pairs, original Native agent
and single-GUI regressions, generated guest scripts and development methods.
An archive-marker false positive and its correction are retained separately.
No private keys, ELF, target/runtime rootfs or host USB metadata is published.
These are same-author integrity checks, not an independent security audit.

Results and the explicit correction of an early mistaken failure diagnosis are
in [the E50 report](../../research/gui-native-actions-validation.md). Each original
run retains its original status and revision; guest exit0 never changes a failed
scenario into a pass.

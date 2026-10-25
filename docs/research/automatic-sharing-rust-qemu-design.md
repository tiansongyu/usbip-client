# Automatic rules with the Rust engines: QEMU contract

2026-10-25, starting clean `7de3c242`. Proposed validation work extending the
[fixed-profile runtime](automatic-sharing-runtime-validation.md). The previous
goal turn made actual implementation/validation/publication progress. The full
Phase0–7 objective stays active; this does not replace dynamic pools, GUI editing,
installation, physical remote safety receipts or hardware acceptance.

The fixture was subsequently implemented and validated at clean `42e98ab3`;
see the [actual report](automatic-sharing-rust-qemu-validation.md). The contract
below records its intended scope and does not declare the overall architecture accepted.

Reuse the existing `qemu_data_link_health` owner, valve, packet auditor and Rust
lifecycle checker. No new component, dependency, external version choice or
parallel agent is needed. Read the current agent/rule implementation and its
authorization/owner audits before changing the fixture. No host USB writes.

## Intended matrix and original invariants

- Virtual: client rules for the exact fixed HID and CDC profiles automatically
  admit the persistent serial job and four successive HID jobs. The server's
  actual agent/manual Start remains unchanged. Each post-fault HID reuse requires
  explicit CAS Enable of the old rule; the failed Once occurrence cannot loop.
- USBFS: one local complete-pool rule automatically starts the real physical
  server job. Client Starts remain original/manual, because physical remote
  automatic import still lacks authenticated all-configuration safety evidence.
- Run each new branch on IPv4 and IPv6. Keep framed-v2 explicit, the original
  three directional exact-flow blackholes, full15-second windows, seven real I/O
  rounds, sustained serial sibling, kernel observations, same-flow health and
  byte-exact packet/lifecycle proofs. Virtual retains real HID enumeration and
  CDC echo, without claiming injected HID keys. USBFS retains actual keyboard/
  FTDI traffic, both original grants, exact Finish proofs and restored baseline.
- Preserve original no-policy denials before stopping/joining that idle agent.
  Then load a separately captured root-owned policy and restart the participating
  agent in its existing UID2000 role. Keep the original UID1002 denials. Add
  explicit manual ACLs for UID1001 without an automation grant, then capture its
  real rule-creation denial
  before enabling any rule, with unchanged jobs/sessions/rules/origins/mutations
  and root resource observations. Never fabricate a successful mutation or Start.
- Rule calls retain actual intent/response frames and original mutation UUIDs.
  Obtain each Start file only from actual RetryStart of the committed origin.
  Verify exact rule/revision/UID/profile/job/request/Fired/legacy Start audit
  bindings in saved snapshots, one admission per explicit revision and no extra
  jobs. All actual owners must join; root release is independently proven.

## Disk expectations and regressions

The existing checker keeps disk2 as its default. Add an explicit per-role expected
schema parameter, including strict whole field sets: only the new virtual client
or USBFS server may require disk3 with a nonnull complete automation object.
Live pre-disarm phase proofs and final proofs must use the same expectation and
the same saved bytes. Do not strip automation fields, rewrite evidence, relax
cleanup/packet assertions or accept a new epoch's zero resources as old release.

Run the original no-policy virtual/USBFS v2 branches independently after the new
matrix, plus the E53 Native automatic branch. Preserve first failures and each
actual source/ELF/guest exit; never restart a live handle because observation
timed out. Root and separate GUI gates remain required. New test-only commits
may reuse unchanged product inputs only with an exact source/hash mapping;
old run results remain attached to their original revisions.

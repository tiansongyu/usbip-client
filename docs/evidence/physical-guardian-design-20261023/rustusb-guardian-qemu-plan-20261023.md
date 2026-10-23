# USBFS independent-open mechanism experiment — proposed execution plan

2026-10-23. Product source remains `60be074`; no product recovery implementation is proposed here. This is a guest-root kernel-mechanism experiment, followed by existing read-only administrator reconciliation and fresh real I/O. Original journal/crash/TERM runners and historic verdicts remain unchanged. This draft was prepared without compiling or running Cargo/QEMU.

## Minimum dependencies and topology

Reuse the existing x86_64 two-disposable-guest journal fixture, private socketpair, QMP keyboard and FTDI socket, root0600 nodes, ordinary UID1000 physical server, UID1001 denial, client VHCI and all three real KEY1/2/3 + 35-byte RX/TX cycles. Each direction totals 105 bytes. Add one original C test probe compiled by the already available host `cc`; no guest Python, strace, new Rust dependency, production API or network service. Existing native.copy_binary handles its ELF dependencies. Host compiler/version, UAPI header hashes, probe bytes and generated init bytes become evidence. /usr/bin/cc and glibc libc.a were observed present; no compiler execution has occurred.

Before any USB node open/ioctl, the probe checks getuid/geteuid=0 and exact space-delimited /proc/cmdline tokens `RUSTUSB_QEMU_GUEST=1` and `rustusb.journal_test=1`. Only QEMU's two fixed HID 0627:0001 and FTDI 0403:6001 root0600 character nodes, major189 and exact sysfs/node receipt, configuration1 and complete sole interface0/alternate0 are accepted. It never detaches, resets, unbinds or changes configuration/alternate. This bounded fixture is not a general administrator tool.

## What the three handles mean

- A: the ordinary service's USBFS open file description, delivered by the existing helper.
- B: an independent `open(O_RDWR|O_NOFOLLOW|O_CLOEXEC|O_NONBLOCK)` before killed-epoch IMPORT, retained by the test probe across worker death and helper restart.
- D: `pidfd_getfd` duplicate of A, intentionally retained across worker SIGKILL for the negative control. Opening `/proc/PID/fd/N` is not a substitute.
- C: another independent open in the same probe process. It demonstrates another OFD's exclusive claims, **not another process**.

Record PID/starttime, source A FD, B/D/C FD, fstat/node/sysfs receipts and actual `kcmp(KCMP_FILE)` returns. Require A↔D=0, A↔B and B↔D nonzero, B↔C nonzero. Syscall permission/unsupported errors fail the experiment, not skip it. Local B/D comparison avoids depending solely on equal fstat (equal device inode cannot distinguish OFDs).

## Sequence using the existing journal lifecycle

1. Complete original normal cycle1 and no-IMPORT reopen unchanged. Save original full interfaces/config/alt/driver and journal baseline.
2. Start probe B immediately before `start_ready killed`. After original cycle2 real I/O and actual_pending, identify the exact ordinary server and USB FDs, retain D for both devices, and save comparisons. Only one SIGKILL is sent to that server.
3. Reap the same child once, immediately record real wait137. While D remains open, capture full sysfs=usbfs and B plain CLAIMINTERFACE/CONNECT errno EBUSY for both devices. Neither operation may displace the owner. Killed-epoch pending/reaped counts remain unknown; do not manufacture a stopped event.
4. Close D explicitly (record close and subsequent EBADF). B stays open. Observe same-generation null drivers, then B plain CLAIM and checked RELEASE for both devices succeed. Record each ioctl's true rc/errno and full before/after state. This establishes interface availability in this fixture, not absence of all FD copies/EP0 or all kernel activity.
5. Open C independently, prove different OFD and claim both interfaces. B CLAIM and CONNECT must return EBUSY while C remains intact. Only C releases its own claims and closes; no forced takeover. Save all temporary states under distinct GUARDIAN markers, not extra journal records.
6. Return to the original 20-second read-only observation, actual killed-post/null state, both-primary-saved barrier, helper restart, retained records, new-server refusal and UID/root-unrestored refusals. The original 184 host assertions remain unchanged. The original late wait uses the previously saved true child result, never calls wait a second time.
7. Replace only the two guest-root sysfs bind writes with B CONNECT for each device. Preserve real nonnegative return value (CONNECT may return 1) and errno, then require full original config/interface/alt/driver + same receipt independently. CONNECT selects a matching kernel driver; equality to the exact original driver remains mandatory.
8. Close B and reap probe exit0 before original root read-only reconcile. Original journal retirement, fresh grant, third actual HID/FTDI I/O, normal stop and empty records remain mandatory.

## Minimal repository implementation points (authorized separately)

- `scripts/qemu/run-guardian-qemu.py`: import journal runner and compose initramfs/source-fingerprint/verify functions; do not copy its host main loop or alter source files. Add only the new sources to recorded script list. Save original/generated init, exact replacement anchors, generated SHA and new binary/header provenance.
- `scripts/qemu/guest-guardian-fragments.sh`: small functions loaded only by generated server init, plus explicitly named insertion fragments. Keep old script bytes immutable. Each replacement needle must occur exactly once in the expected old SHA. Error on drift rather than regex-guessing or silently accepting a new old harness.
- `scripts/qemu/usbfs-guardian-probe.c`: bounded root guest fixture with independent open, pidfd duplication, kcmp, plain claim/release/CONNECT only; commands through a root-owned local FIFO and JSONL stdout. No raw payloads or journal writes.

Patch anchors: before killed launch (B startup), just before KILL identity (D arm), after cycle-2-killed notification (true wait and D/C controls), old KILL wait line (use saved result), two admin sysfs bind writes (B CONNECT + close/reap). These are small snippets, not changes to the full journal assertions or timing. Source guards include imported host and guest files, new runner/probe/fragments, Cargo.lock, build inputs and generated init.

## Evidence and verdict

Keep kernel ttyS0 and structured program ttyS1 separate; guardian records are immediate and cannot depend on successful final blocks. Save first failure reason and all real wait facts. Every phase includes original/current full interface set, configuration, alt, driver, node/sysfs/devnum receipt, pending API+disk bytes/hash, root guardian PID/starttime and FD inventory. Extend journal.verify's returned checks with separate guardian checks; preserve base check order/content and report base count separately. Overall timeout and 30-second failure collection are not increased to hide coordination errors.

Separate `collection_complete`, `ofd_lifetime_mechanism`, `dup_retains_old_claim`, `external_ofd_refusal`, `B_driver_restore`, `journal_reconcile`, `third_real_io`, and `product_automatic_recovery=false`. Any actual missing evidence is FAIL/incomplete, never inferred from process death. Expected EBUSY is a tested refusal, not recovery success. Product/QEMU/CI baselines are not reassigned to the later test/documentation commit.

Expected future command, only after design commit/freeze and root authorization:

```sh
python3 scripts/qemu/run-guardian-qemu.py --evidence /tmp/rustusb-guardian-<clean-sha>-20261023
```

The wrapper keeps the base locked all-features build. Root runs all builds and QEMU serially and validates clean/source/ELF guards. Required follow-up before any product automation: trusted operation journal and completion acknowledgments, unknown configuration/alternate changes, helper/guardian failure, external replacement driver, hotplug generation, retained arbitrary capabilities, kernel internal blocking and hardware G1/G2. A claim gate is not a race-free multi-interface transaction; releasing B before CONNECT leaves an external-admin race, which this experiment does not eliminate.

## Sources examined

- Local architecture, crash design, journal report; exact existing host SHA `1cefbc14bc45c9e9b5c9f6f0d9784be834fe3ee5856f8bc2937b7baabbbc01aa`, guest SHA `2236c3080d17e447c4d9cdec495609ab6c7d3976d6c92306e3b1286e9d94d8d4`.
- [Linux v6.8 devio.c](https://github.com/torvalds/linux/blob/e8f897f4afef0031fe618a8e94127a0934896aba/drivers/usb/core/devio.c): inspected prior archived source SHA256 `8aee2c8210aa6bb28029ccdf6a75bd1cf1239abcc192afe5587ebf7301f40627` (fresh raw retrieval timed out). usbdev_open has per-open state; final release frees its claims/async; CONNECT refuses an occupied interface. Ubuntu 6.8.0-106 behavior still requires this experiment.
- [pidfd_getfd(2)](https://man7.org/linux/man-pages/man2/pidfd_getfd.2.html): same-OFD duplicate and ptrace permission, inspected 2026-10-23.
- [kcmp(2)](https://man7.org/linux/man-pages/man2/kcmp.2.html): OFD comparison and permissions, inspected 2026-10-23. Keep the inspected foreign FDs alive under the existing pending barrier; archive before/after identity checks. It is not a substitute for a trusted lifetime protocol.

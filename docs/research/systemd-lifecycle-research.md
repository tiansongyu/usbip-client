# RustUSB systemd lifecycle research

Status: **proposal / not accepted / not installed or executed**. 2026-10-25. Read-only repository review at `412b40ff941d74ff80f1ca0cc4d86aade207a2d4`. No repository edits, package installation, host systemd mutations or host USB operations. These findings do not supply installation or USB acceptance evidence.

## Versions and primary references

The official Ubuntu manpages identify Jammy as `249.11-0ubuntu3.23` and Noble as `255.4-1ubuntu8.18` when fetched. These are observed documentation-provider versions, **not an assertion that every image or architecture has that precise update installed**. Guest validation must record `dpkg-query -W systemd`, `systemctl --version`, architecture, kernel and image digest. The public Launchpad Jammy page can lag the manpage provider; do not relabel the discrepancy as an installed version.

- [Ubuntu 22.04 systemd.kill](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.kill.5.html), [service](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.service.5.html), [exec](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.exec.5.html), [unit](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.unit.5.html).
- [Ubuntu 24.04 systemd.kill](https://manpages.ubuntu.com/manpages/noble/man5/systemd.kill.5.html), [service](https://manpages.ubuntu.com/manpages/noble/man5/systemd.service.5.html), [exec](https://manpages.ubuntu.com/manpages/noble/man5/systemd.exec.5.html), [unit](https://manpages.ubuntu.com/manpages/noble/man5/systemd.unit.5.html).
- Upstream stable [`v249.11`, peeled commit `5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4`](https://github.com/systemd/systemd-stable/tree/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4) and [`v255.4`, peeled commit `387a14a7b67b8b76adaed4175e14bb7e39b2f738`](https://github.com/systemd/systemd-stable/tree/387a14a7b67b8b76adaed4175e14bb7e39b2f738). Both resolved with actual `git ls-remote`, not inferred from tag names. Ubuntu has distribution patches: upstream source is a mechanism cross-check, not an assertion that Ubuntu binaries equal these trees.
- [`249` parser table](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/src/core/load-fragment-gperf.gperf.in), [`255` parser table](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/src/core/load-fragment-gperf.gperf.in).
- [`249` service state machine](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/src/core/service.c), [`255` service state machine](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/src/core/service.c).
- [Official Jammy package metadata](https://launchpad.net/ubuntu/jammy/+package/systemd). The web fetch of the Noble Launchpad link was rejected; the successfully fetched official Noble manpage carries its provider version. Direct `packages.ubuntu.com/{jammy,noble}/systemd` fetches also failed and are not evidence.

Local public-reference snapshots, source hashes, excerpt line mappings and research fetch failures: `/tmp/rustusb-systemd-research-20261025/`. The immutable upstream links above are the authoritative pin; Ubuntu suite manpages are mutable and saved hashes identify the observed bytes.

## Load-bearing project facts

Read: root `AGENTS.md`, project rustusb-development skill, `docs/work-log.md`, `docs/architecture.md`, `docs/application-service-guide.md`, `docs/native-guide.md`, relevant application-service design and current source. Ordinary operator CLI/TUI/GUI use metadata and authenticated application IPC. Only the dedicated nonzero broker loads private TLS configuration or owns jobs; root helper applies its independent policy and owns privileged kernel operations. Static broker UID must agree across the manifest, TLS profiles and helper policy; account naming alone does not grant that agreement.

Actual existing commands (not hypothetical packaging APIs):

```text
rustusb-helper --config /etc/rustusb/policy.json \
  --socket /run/rustusb/control.sock \
  --physical-state-dir /var/lib/rustusb/physical \
  --native-state-dir /var/lib/rustusb/native
rustusb agent --config /etc/rustusb/agent.json run
# Explicit administrator opt-in alternatives after `run`:
--automation-policy /etc/rustusb/automation.json
--automation-decisions-policy /etc/rustusb/automation-decisions.json
```

The two automation options conflict. Absence leaves automation disabled; disk3/disk4 must not be silently downgraded or reset. Agent requires a CLI build with `secure-transport`; the default CLI cannot parse `agent`. Physical delegation requires a matching helper built with `physical-usb` and agent with `physical-nusb`.

Source anchors at reviewed HEAD:

- `crates/rustusb-daemon/src/main.rs`: exact helper arguments, logger before Tokio and logger shutdown after serve.
- `crates/rustusb-daemon/src/linux.rs:1274`: policy and existing journals load before listener. Existing journals reload even after relevant permissions are removed. Existing helper socket causes startup rejection; there is no automatic unsafe stale unlink. SIGTERM registration currently occurs **after** journal initialization and socket bind/chmod (`:1364`). Recovery scheduling starts after registration. Stop drops listener, requests native/recovery stop and joins accepted connection/recovery tasks; legacy native kernel sessions deliberately survive helper shutdown.
- `crates/rustusb-cli/src/agent.rs:460`: agent registers INT/TERM before blocking profile/policy load, retains that load on cancellation, and repeatedly sets sticky stop while continuing to await the actual service.
- `crates/rustusb-service/src/ipc.rs`, `fs.rs`: exact broker-owned `0711` control and `0700` state leaf; protected root-owned ancestors; `0666` endpoint with kernel UID authorization; real directory locks and safe stale-agent socket classification. State leaf must not be nested under another broker-owned directory, because every ancestor is required to be root-owned.
- `crates/rustusb-native/src/lib.rs:517`: fixed `/usr/bin/usbip`, fixed environment, bounded drain/timeout and actual kill/reap. Native channel sources access `/proc/self/fd`, `/sys/devices/platform/vhci_hcd.*`, `/sys/bus/usb` and kernel socket attachment. Helper recovery reads `/proc/<pid>/stat` and boot UUID; USBFS paths require `/dev/bus/usb`, sysfs generation checks and real ioctls. Current helper does not automatically load kernel modules.

`stop_accepted`, process death, a new PID or a new epoch's zero counters cannot retire an old USB record. Even a clean helper exit is not a general USB release receipt. The package must preserve original helper and agent state across upgrades, removal, reboot and failures; it must not issue reconcile/write/recovery operations merely to make uninstall succeed.

## Service semantics verified for 249 and 255

1. **Type=exec** is the compatible truthful starting point: it detects inability to execute the binary or select `User=`. It acknowledges successful execution, not successful configuration or socket readiness. Neither process currently implements `sd_notify` or consumes systemd socket-activation FDs. `Type=notify` without product support waits/fails; a `.socket` unit would collide with the product's own bind. Readiness stronger than exec must be a separately designed change, not invented unit behavior. [249 service XML](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/man/systemd.service.xml)
2. With no `ExecStop=`, systemd sends the configured initial signal itself. **KillMode=mixed** sends initial TERM to the Rust main, leaving subprocess control to that actual owner. `control-group` sends TERM to helper tools too, which can interrupt the parent's verified mutation/cleanup sequence; `process`/`none` escape cgroup lifecycle and should not be used. `SendSIGKILL=no` disables final automatic KILL and refuses a later start when old cgroup processes remain. It is not protection from OOM, administrator kill, force shutdown or power loss. [249 kill XML](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/man/systemd.kill.xml)
3. **TimeoutStopSec=infinity plus SendSIGKILL=no** is the proposed cooperative policy. The 249 state machine's `service_arm_timer` handles infinity; `service_dispatch_timer` otherwise advances a timed-out TERM to stop-post even with KILL disabled. Therefore finite timeout plus `SendSIGKILL=no` does **not** mean the stop job waits until cleanup. Infinity can block package operations or normal reboot indefinitely on a real FS/kernel hard block. No claim is made that unit settings cancel such work. [249 service.c](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/src/core/service.c), [255 service XML](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.service.xml)
4. **After=helper** reverses at stop: broker stops before helper, leaving helper available for broker Finish/proof requests. Ordering alone does not pull units in. **BindsTo=helper** includes Requires-like activation and also stops the broker when helper becomes unexpectedly inactive. **PartOf=helper** explicitly propagates helper stop/restart to the broker. Broker stop does not stop helper. Helper failure cannot preserve an already-dead helper or manufacture broker cleanup proof; it merely stops additional broker work and retains uncertainty. No StopWhenUnneeded or symmetrical dependency should pull helper down while broker cleanup is pending. [249 unit XML](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/man/systemd.unit.xml), [255 unit XML](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.unit.xml)
5. **Restart=no** is the proposed default. Repeated crashes, logging exit1 after cleanup and unknowable USB crash recovery have different meanings; a generic automatic restart policy cannot distinguish them. Manual restart still loads retained state and configuration. PartOf restart propagation must be exercised on the real distro managers, including an agent that was inactive and explicit automation policy; do not infer safe rule resumption from process launch alone.
6. Alternative systemd-managed directory semantics (not used in the strict draft below): RuntimeDirectory is volatile; StateDirectory is retained on service stop and reboot. Explicit modes avoid systemd's `0755` default. `RuntimeDirectoryPreserve=yes` would also retain runtime leaves on stop/restart; the application removes its own verified socket on clean exit. Strict missing-only provisioning likewise retains a stale helper socket after crash, causing safe startup refusal until an administrator classifies/removes it. It cannot preserve `/run` over reboot. **Existing directory ownership mismatch causes systemd to recursively chown its contents; existing correct owners avoid that recursion, while leaf mode is adjusted.** Thus never change/recycle the broker UID or casually repoint StateDirectory at unknown contents. If preserving wrong-owner/mode evidence before application checks is required, use missing-only package provisioning and omit StateDirectory/RuntimeDirectory ownership repair; if ProtectSystem=strict is later adopted, explicit ReadWritePaths are also required. The strict draft below uses ProtectSystem=full and needs its own lifecycle tests. [249 exec XML](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/man/systemd.exec.xml)

## Compatible draft units

Names and installed paths below are **proposals**, not an accepted account/package layout. The parent design selects missing-only, exact-owner/mode fixed-path package provisioning instead of systemd directory-management repair. Both units should ship disabled and not be started by package installation. Place samples outside active policy/manifest paths. `ConditionPathExists=` only distinguishes absent configuration from a start attempt; product loaders remain responsible for trust/schema validation. Conditions do not validate or authorize configuration.

`rustusb-helper.service`:

```ini
[Unit]
Description=RustUSB restricted privileged helper
ConditionPathExists=/etc/rustusb/policy.json
Before=rustusb-agent.service

[Service]
Type=exec
User=root
Group=root
ExecStartPre=/usr/bin/python3 -I /usr/libexec/rustusb/package-control prepare-helper
ExecStart=/usr/libexec/rustusb/rustusb-helper --config /etc/rustusb/policy.json --socket /run/rustusb/control.sock --physical-state-dir /var/lib/rustusb/physical --native-state-dir /var/lib/rustusb/native
UMask=0077
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=no
TimeoutStopSec=infinity
Restart=no
NoNewPrivileges=yes
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes
StandardInput=null
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

`rustusb-agent.service`:

```ini
[Unit]
Description=RustUSB dedicated nonroot application service
ConditionPathExists=/etc/rustusb/policy.json
ConditionPathExists=/etc/rustusb/agent.json
Requires=rustusb-helper.service
After=rustusb-helper.service
# Proposed additions if crash stop and explicit restart propagation are required:
# BindsTo=rustusb-helper.service
# PartOf=rustusb-helper.service

[Service]
Type=exec
User=rustusb
Group=rustusb
ExecStartPre=+/usr/bin/python3 -I /usr/libexec/rustusb/package-control prepare-agent
ExecStart=/usr/bin/rustusb agent --config /etc/rustusb/agent.json run
UMask=0077
KillMode=mixed
KillSignal=SIGTERM
SendSIGKILL=no
TimeoutStopSec=infinity
Restart=no
NoNewPrivileges=yes
CapabilityBoundingSet=
ProtectSystem=full
ProtectHome=yes
PrivateTmp=yes
StandardInput=null
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

All runtime directive keys above exist in the pinned 249/255 parser tables; `WantedBy` is documented Install metadata. This is source compatibility research, not `systemd-analyze verify` or real unit execution. `WantedBy` is installation metadata; the package must explicitly suppress automatic enable/start. Static `rustusb` needs a persistent unshared UID/GID and no login shell. Never add operators to the broker account/group for convenience. Administrator configuration must substitute the account's **actual numeric UID**, without installation-generated USB/TLS/automation grants.

The pre-start helper names are **proposed package commands that do not exist in reviewed product source**. The root-owned Python script must expose only fixed operations and fixed paths; it must not accept an arbitrary user-supplied path, UID, program, shell fragment or chmod target. `python3 -I` avoids environment/import-search injection. Proposed exact leaves: `/run/rustusb` root `0755`, `/var/lib/rustusb` root `0700`, helper state leaves root `0700`; `/run/rustusb-agent` broker `0711`, `/var/lib/rustusb-agent` broker `0700`. Create only missing entries beneath verified descriptors; exact-existing entries remain unchanged; any mismatched inode/type/owner/mode/symlink refuses startup. The ordinary broker cannot create under `/run`/`/var/lib`, so `+` is necessary if this work is done in its unit rather than before service activation. Existing locks/files/journals and private key ownership must not be repaired by recursive chown.

**249 `+` semantics:** the command prefix works on ExecStartPre and bypasses User/Group, capability bounding and filesystem namespace restrictions for only that command. It is therefore a root trust boundary even if the main agent is hardened. No `-` prefix: a nonzero preparation result must prevent ExecStart. Pre-start commands must finish rather than spawn a background worker. Conditions run before this and only skip truly absent configuration. [249 service command-prefix reference](https://github.com/systemd/systemd-stable/blob/5d125aae0cc9be0ac5abb8ab8e5081d27e1377a4/man/systemd.service.xml#L365)

The baseline Requires/After plus helper Before handles explicit stop ordering. **It does not guarantee stop on helper's unexpected exit**. BindsTo is the proposed addition for that; PartOf makes helper restart propagation explicit, both supported in249. Their effects, including inactive-agent behavior, still need real distro-manager tests. The scenario table below states the stronger proposed BindsTo/PartOf outcome; it is not established by the minimal draft without those commented additions.

The selected baseline avoids broad syscall/device filtering. `ProtectSystem=full` protects `/usr`, boot/EFI and `/etc` while preserving sysfs/device access and `/run`/`/var` writes. `ProtectHome=yes` requires installed profiles/credentials/state to live outside `/home`, `/root`, `/run/user`. `NoNewPrivileges=yes` does not remove already-held root privileges. The broker capability set can be empty because the original design delegates opened FDs and privileged operations to the helper. These protections still need real backend regressions. [255 exec XML](https://github.com/systemd/systemd-stable/blob/387a14a7b67b8b76adaed4175e14bb7e39b2f738/man/systemd.exec.xml)

Do not blindly add:

- `PrivateDevices=yes` or closed device policies: helper USBFS opens and broker validation/nusb need actual device visibility; passed descriptors alone are insufficient for the existing receipt checks.
- `ProtectKernelTunables=yes` to helper: it makes `/sys` read-only and breaks real USB/VHCI writes. Do not replace it with a partial path exception without auditing resolved `/sys/devices` paths.
- `ProcSubset=pid`, restrictive ProtectProc, private PID/user namespaces or `/proc` masks: boot UUID, process start identity and FD identity checks must remain real and visible.
- `ProtectKernelModules=yes` while also promising unit-based module loading/complete module-file diagnostics. Current helper does not load modules; keep module provisioning an explicit administrator prerequisite rather than a hidden ExecStartPre mutation.
- `PrivateNetwork=yes` or a filter lacking AF_UNIX/AF_INET/AF_INET6/AF_NETLINK: application IPC, TLS, dual stack, resolver and hotplug source need those mechanisms.
- Generic seccomp `@system-service`/ioctl bans, aggressively reduced root capabilities or arbitrary low resource limits without measuring real USB operations. `SystemCallFilter`, `DeviceAllow` and capability options are mechanisms, not a verified permission inventory.

## Startup, restart, upgrade and removal behavior

| Trigger | Required observable behavior / caveat |
|---|---|
| Fresh install with samples only | Both disabled; no active policy or manifest, no broker listener/TLS listener, no module load/USB operation. Manual start with absent conditions is skipped rather than reported ready; inspect ConditionResult. |
| Configuration exists but invalid/untrusted/mismatched UID | Real loader exits nonzero, journal unchanged except legitimate checked migrations; Restart=no prevents loop. Type=exec start acknowledgement can precede this failure. No grants guessed from device presence. |
| Valid configuration, no automation opt-in | Agent loads complete fixed profiles, binds local IPC, starts no profile just because it was installed. Type=exec/After do not wait for helper bind; operations may see transient not-listening. |
| Explicit automation policy | Administrator drop-in must replace the exact ExecStart with a permitted option. Current startup race with helper readiness must be resolved/tested before promising automatic startup reliability. No whole-profile autostart workaround. |
| Agent restart/CRL renewal | Agent TERM, actual owner/worker/logger join and process exit before new agent. Original journal/UUIDs retained; existing credentials reload only in the new process. Helper stays live. New epoch does not retire old unknown ownership. |
| Helper stop or restart while broker active | BindsTo/PartOf plus reverse After order must keep old helper alive until broker has actually exited. After helper restart, an intentionally restarted agent uses retained state. Do not stop helper and broker in unrelated concurrently launched shell commands. |
| Upgrade/remove during active work | Complete checked stop before replacement/removal proceeds; no `--no-block`, external stop timeout, ignored stop errors or cleanup script deleting journals. Preserve unresolved records even on purge. A hard block may leave package operation pending; failure must be reported, never converted into release. |
| Agent/helper crash or leftover cgroup process | No automatic restart by default. Preserve stores and original owner/operation IDs. Stale helper socket is an intentional fail-closed obstacle. A live leftover child blocks restart under mixed+SendSIGKILL=no. Do not `rm -rf /run/...` or `systemctl clean --what=state`. |
| System reboot/power loss | Normal shutdown follows ordered TERM/wait; abrupt shutdown supplies no release proof. `/run` disappears, `/var/lib` remains. Old boot/session records stay subject to original recovery rules; do not reset journal, adopt ports, or rerun old mutation IDs as new requests. |

There is a genuine helper early-start signal gap in the current source: TERM during Store initialization precedes signal registration. A unit cannot repair that. It must be represented as a product lifecycle issue/test, especially if readiness or automatic startup is added. The helper code also explicitly permits legacy native kernel sessions to survive helper shutdown; a package cannot generalize helper exit into all-device release.

## Full Ubuntu QEMU validation still required

Use full booted Ubuntu guests with PID1 systemd, real dpkg/apt/deb state and kernel modules, not the current BusyBox initramfs or bundler output. First exercise both supported Ubuntu manager generations, then intended x86_64/aarch64 package architectures; preserve exact image/package/source/build/ELF inputs and separate not-run cells. Minimal acceptance cases:

1. Install/dependency failure, absent configuration/manual start, disabled defaults, valid/invalid/wrong-owner configuration, wrong broker UID, missing credentials/backend feature, module/tool mismatch. Save unit files, parser diagnostics and actual manager effective properties. Confirm no module/USB side effects from installation or no-policy startup.
2. Verify generated modes/ancestors/socket identities/locks and real `/proc` peer UID; ordinary operator can use only authorized agent RPC, cannot read private keys/state or mutate helper. Probe wrong UID, changed directory/symlink/hardlink, duplicate agent/helper and stale sockets. Separately test ownership-repair behavior of managed directories against the intended policy.
3. Actual Native two-ended HID/FTDI I/O, target session stop with sibling preserved, whole broker SIGTERM, helper restart cascade and normal guest reboot. Capture old agent/helper PIDs/start times/cgroups, signals, syscalls, exact proof tuples, complete original driver/VHCI baseline and process wait outcomes. Demonstrate helper remains callable through final broker Finish/Root-proof recording, not just task ordering logs.
4. Delay original WAL/resource ACK and actual accepted initializer/Finish, then `systemctl stop/restart` and upgrade/remove. Show the service is still deactivating and package step not complete while the real worker is held; release the bounded test gate and require actual join/exit/retained transaction. Test repeated TERM and a child subprocess. A bounded delay is not evidence of cancellation of permanent FS/kernel blocking.
5. Isolated controlled negative: finite timeout + SendSIGKILL=no demonstrates leftover/failure risk; production infinity case must not auto-KILL. Test restart refusal with a live leftover process. Never reinterpret stopped/failed manager state as cleanup proof. Any forced QEMU termination is a failed/incomplete lifecycle result, not a clean shutdown.
6. Upgrade old package to new with active Native and USBFS owners; configured/unconfigured and inactive/active unit states; rollback/unpack/configure/start failure; remove and reinstall; purge preserving unresolved journals. Hash/snapshot old original state and verify original UUID/kind/key/UID/resource/boot scope after each. If package architecture or schema downgrade cannot read newer state, fail closed without rewriting/deleting it.
7. Crash agent/helper in separate guests with existing active records; retain fail-closed unknown states and permissions. Verify helper crash causes broker cooperative stop, does not invent old proof, and no restart loop or new automatic claim occurs. Run explicit allowed recovery separately; no data from a new epoch substitutes for original release.
8. Explicit automation/settled disk4 and GUI/TUI frontend recovery across package restart/reboot: retained original mutation/start UUIDs remain query-only until exact settlement; ordinary renderer not root, no duplicate helper acquisition or automatic GUI submit. This is additional coverage, not supplied by unit syntax.
9. Regress all enabled backend operations under chosen hardening: Native sysfs/VHCI/FD identity and actual tools, virtual TLS, USBFS FD validation/ioctl/original-driver restore, hotplug AF_NETLINK, DNS dual stack/CRL, stdout/journal backpressure and file logging if enabled. Do not infer USBFS/module/sysfs compatibility from a process reaching active.

Not proven by this research: actual service execution, package lifecycle, timely shutdown under arbitrary hard blocks, automatic crash recovery, FD revocation, hardware G1/G2, complete G3/G4/Phase5 or release readiness.

Final read-only recheck: parent advanced HEAD to `5f352e995438aa67178dfe55de1055feec3169ff` (documentation design commit); `git diff --stat 412b40ff HEAD -- crates/rustusb-daemon crates/rustusb-cli/src/agent.rs crates/rustusb-service crates/rustusb-native` was empty. Product-source findings therefore remain pinned to the originally reviewed `412b40ff`. Final worktree was clean; this agent made no repository changes. Static parser lookup passed 48 runtime-key checks across the two pinned upstream versions. References were reduced to 25 files / 271205 bytes, including proposal units and original-vs-excerpt hash metadata.

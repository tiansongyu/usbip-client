# RustUSB Debian lifecycle research

Date: 2026-10-25. Status: research/proposed, not an accepted architecture or installation result. Requested read-only delegation; initial repository HEAD `412b40ff`, initially clean. Read root instructions, rustusb-development and writing-technical-design workflows, existing Native/application-service guides and historical GUI installation research. No repository edits, installation, package-script execution, host USB operations, accounts or services changed. Public packages below were downloaded and **extracted only**.

The parent's selected base+GUI split is viable. Package payload must contain no live `/etc/rustusb` policy, manifest or credential files and no persistent journals; ship examples for explicit administrator provisioning. Stop the original agent, await actual exit, then stop helper and await actual exit before replacing/removing base files. Keep account, credential files and all unresolved records through upgrade/remove/purge. A package-manager result is never USB cleanup evidence.

## Versions checked, with independent architecture records

Official Launchpad Published binary history was queried separately for amd64 and arm64 on 2026-10-25. Current Published Release/Updates/Security entries and publication dates are preserved as `*-published.json` and `*-arm64-published.json` under the raw directory. These are archive metadata, not deployed VM observations.

| Component | Ubuntu 22.04 Jammy | Ubuntu 24.04 Noble |
|---|---|---|
| dpkg | 1.21.1ubuntu2.6; Updates published 2025-09-24 | 1.22.6ubuntu6.6; Updates 2026-05-07 |
| systemd | 249.11-0ubuntu3.23; Updates 2026-10-21 | 255.4-1ubuntu8.18; Updates 2026-10-21 |
| debhelper | 13.6ubuntu1 | 13.14.1ubuntu5 |
| init-system-helpers | 1.62 | 1.66ubuntu1 |
| adduser | 3.118ubuntu5 | 3.137ubuntu1 |
| GTK runtime package | libgtk-3-0 3.24.33-1ubuntu2.2 | libgtk-3-0t64 3.24.41-4ubuntu1.3 |
| WebKitGTK 4.1 | libwebkit2gtk-4.1-0 2.50.4-0ubuntu0.22.04.1 | libwebkit2gtk-4.1-0 2.52.6-0ubuntu0.24.04.1 |

Queries used `GET https://api.launchpad.net/1.0/ubuntu/+archive/primary?ws.op=getPublishedBinaries&binary_name=NAME&exact_match=true&status=Published&distro_arch_series=https%3A%2F%2Fapi.launchpad.net%2F1.0%2Fubuntu%2FSERIES%2FARCH&ws.size=10`. All listed versions appeared for both architectures. Jammy original Release WebKit is 2.36.0; it does not satisfy the existing Tauri minimum 2.40. Native build/runtime acceptance therefore needs an **updated** Jammy image.

[Jammy dpkg](https://manpages.ubuntu.com/manpages/jammy/man1/dpkg.1.html), [Noble dpkg](https://manpages.ubuntu.com/manpages/noble/man1/dpkg.1.html), [Jammy systemd](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.kill.5.html), [Noble systemd](https://manpages.ubuntu.com/manpages/noble/man5/systemd.kill.5.html). Exact API request URLs, publication timestamps, response bytes and SHA256 are in the capture indexes.

## Maintainer-script ordering and failure gates

Debian Policy 4.7.4.1, released 2026-03-31, is the authoritative procedure reference; dpkg's own manpage explicitly says its install/removal description is inadequate for maintainers. Compressed sequence:

| Case | Calls / irreversible boundary |
|---|---|
| First install | new `preinst install`; unpack; new `postinst configure ""` |
| Upgrade | old `prerm upgrade NEW`; new `preinst upgrade OLD NEW`; unpack; old `postrm upgrade NEW`; new `postinst configure OLD` |
| Failed old prerm | new `prerm failed-upgrade OLD NEW` may replace it; returning success here permits upgrade to continue |
| Failed configure | half-configured; no automatic unwind of application state |
| Remove | `prerm remove`; remove payload except conffiles; `postrm remove` |
| Purge | removal if needed; remove conffiles/backups; **then** `postrm purge` |

Scripts must be idempotent, work without a terminal and handle `abort-install`, `abort-upgrade`, `abort-remove`, `failed-upgrade` and deconfiguration cases deliberately. Preinst/new failed-upgrade cannot depend on new payload; postrm cannot depend on removed payload. A disappearing package can lose all its files without prerm; avoid future Replaces/rename transitions without a separate stop design. [Policy chapter 6](https://www.debian.org/doc/debian-policy/ch-maintainerscripts.html).

**RustUSB inference:** a guard only in old prerm is insufficient: new failed-upgrade must not silently return 0 after a genuine stop failure, and new preinst must reject unsafe upgrades from older packages that lacked the guard. A new package's Python file is unavailable before unpack; inline minimal checks in preinst/failed-upgrade, use the previous installed control file only after verifying it exists, or make a small compatible lifecycle dependency a genuine Pre-Depends. Do not silently continue when the checker is missing. Cross-package split/rename is a later explicit migration.

Parent candidate: base package owns installed Python root control, helper+agent units and both Rust executables; base prerm invokes installed control for `upgrade` and `remove`. Keep critical checks before any generated `#DEBHELPER#` fragment. Abort handlers retain stopped state and journals, never auto-start or reset old state. Postinst failure is repaired by an explicit `dpkg --configure` after the operator resolves the cause; it is not a transactional rollback.

## Service lifecycle: commands that can falsely appear successful

Actual Ubuntu 1.62 and 1.66ubuntu1 `deb-systemd-invoke` source confirms policy-rc.d denial code 101 prints a message and exits **0 without issuing stop**. Its documented purpose is maintscript service management respecting local policy. Both support `deb-systemd-invoke stop UNIT...`; Noble adds `--no-dbus` and daemon-reload/daemon-reexec handling unavailable in Jammy. Use ordinary `systemctl --system daemon-reload` after confirming a live target manager, rather than relying on Noble-only syntax. [Jammy API](https://manpages.ubuntu.com/manpages/jammy/man1/deb-systemd-invoke.1p.html), [Noble API](https://manpages.ubuntu.com/manpages/noble/man1/deb-systemd-invoke.1p.html).

The systemctl stop documentation also says a failed ExecStop command need not make `systemctl stop` fail. `is-active` false is not an actual-owner proof. Inspect machine-readable `systemctl show` fields: `ActiveState`, `SubState`, `MainPID`, `ControlPID`, `ControlGroup`, `Result`, `ExecMainCode`, `ExecMainStatus`, `KillMode`, `KillSignal`, `SendSIGKILL`, `TimeoutStopUSec`, `Restart`. Correlate the same unit/original invocation and actual cgroup emptiness. A new PID, an empty new process, or missing socket does not prove prior USB cleanup. [Jammy systemctl](https://manpages.ubuntu.com/manpages/jammy/man1/systemctl.1.html).

Unit candidate constraints:

```ini
[Service]
Type=exec
KillSignal=SIGTERM
KillMode=control-group
SendSIGKILL=no
TimeoutStopSec=infinity
Restart=no
```

Both versions support these. Type=exec reports exec/user failures more usefully than simple, but is not readiness. Do not invent sd_notify readiness without implementing it. Infinite stop wait is the parent's chosen tradeoff: dpkg may wait indefinitely on actual FS/kernel work. Explicitly document that, and never let a harness timeout kill the owner or count it as successful removal. Finite stop timeout plus SendSIGKILL=no and a refusing pre-unpack gate is a possible alternative, but requires separate timeout/remaining-process validation. Systemd defaults SendSIGKILL=yes; replacing FinalKillSignal with another fatal signal is not cooperation. No WatchdogSec or abort/kill timeout failure mode. [Jammy service](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.service.5.html), [Jammy kill](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.kill.5.html), [Noble kill](https://manpages.ubuntu.com/manpages/noble/man5/systemd.kill.5.html).

Ordering candidate: agent `After=rustusb-helper.service` gives reverse stop order when systemd stops both. The installed control must still explicitly stop/verify agent first and only then helper. No ExecStop callback that only sends a signal then returns; service manager must await the real process. Current helper source registers TERM after persistent journal initialization, and a comment explicitly says legacy native kernel sessions can survive helper shutdown. Therefore helper wait0 alone cannot be advertised as all USB resources released. Package lifecycle retains original records and reports unresolved/reconciliation requirements; independent original Rootproof/device-baseline evidence is still required where claimed.

**Debhelper trap, verified from exact downloaded packages:** compat >=10 defaults to restart after upgrade. `--no-enable` does not imply no start; `--no-start` does not imply no enable. Worse, `--no-start --no-enable` still emits upgrade `preinst-systemd-stop`, with `deb-systemd-invoke stop ... || true`. Standard prerm stop snippets also suppress failure. Candidate when using own control is `dh_installsystemd --no-start --no-enable --no-stop-on-upgrade`, followed by inspection of final DEBIAN scripts. Stock Jammy postrm masks on remove/unmasks on purge; avoid that generated block for the parent's mask-preservation requirement. [Jammy helper](https://manpages.ubuntu.com/manpages/jammy/man1/dh_installsystemd.1.html), [Noble helper](https://manpages.ubuntu.com/manpages/noble/man1/dh_installsystemd.1.html). Hand-built dpkg-deb scripts are also possible, but need the same audit and dependency calculation.

## Disable on removal without deleting masks

Cross-release source-supported sequence for **fixed units without Alias= or Also=**:

```sh
# postinst configure, while vendor unit files exist; records potential links only
# Never call after removal, because it would overwrite the retained link list.
deb-systemd-helper update-state rustusb-agent.service rustusb-helper.service

# postrm remove, including after vendor files have been deleted
deb-systemd-helper disable rustusb-agent.service rustusb-helper.service

# postrm purge: discard package enable bookkeeping and links, not RustUSB state
deb-systemd-helper purge rustusb-agent.service rustusb-helper.service
```

These actions do not invoke mask/unmask. In exact versions examined, purge maps to disable with an additional bookkeeping flag. `remove_links` reads the pre-recorded `.dsh-also` entries; it tolerates missing vendor unit files. Thus an administrator's ordinary `multi-user.target.wants/UNIT` link is known even if it was enabled after installation; `update-state` records that path without creating it. A main `/etc/systemd/system/UNIT -> /dev/null` mask is not in that fixed WantedBy-only link set and remains. Audit symlink results: remove_links can print unlink errors while returning success. Do not claim complete disabling from helper exit status alone. Neither action touches `/var/lib/rustusb*`.

`deb-systemd-helper` requires `DPKG_MAINTSCRIPT_PACKAGE`, which dpkg provides. It can perform enable-link bookkeeping without running systemd, but will issue daemon-reload when `/run/systemd/system` exists and links changed. Exact 1.62 has no DPKG_ROOT support; 1.66ubuntu1 does. Reject nonempty DPKG_ROOT before calling 1.62 against an alternate root. [Jammy command map](https://manpages.ubuntu.com/manpages/jammy/man1/deb-systemd-helper.1p.html).

Fresh install in a genuine offline chroot can create the system account/bookkeeping within that chroot and skip reload, with services disabled; never use host systemctl accidentally through a shared /run. For upgrade/remove of a deployment whose manager/owners cannot be inspected, fail closed with a precise supported-environment message. `/run/systemd/system` absent is not proof that no manually launched RustUSB process exists. If standalone installs or process ownership outside the fixed units are supported, design a migration/owner exclusion mechanism rather than grepping names/PIDs. These are design boundaries, not validated implementation.

## Accounts, protected files and retention

Common account API for both releases:

```sh
adduser --system --group --no-create-home \
  --home /var/lib/rustusb-agent --shell /usr/sbin/nologin rustusb
```

Do not hard-code UID2000, use DynamicUser, give operators membership in the broker account/group, or silently reuse an unexpected existing account. Verify resolved UID/GID are nonzero and match retained file ownership and administrator configs. Never change a retained UID or recursively chown state to make an upgrade pass. Keep account and group on remove/purge to avoid UID reuse granting another principal access to preserved credentials/journals. Account creation is idempotent but adduser returning successfully for an existing account does not validate RustUSB's trust requirements. [Policy system IDs](https://www.debian.org/doc/debian-policy/ch-opersys.html#users-and-groups), [Jammy adduser](https://manpages.ubuntu.com/manpages/jammy/man8/adduser.8.html), [Noble adduser](https://manpages.ubuntu.com/manpages/noble/man8/adduser.8.html).

Existing product contract: root-owned protected ancestors `/etc/rustusb`0755; public manifest0644; private DER key0600, root or broker owner and actually broker-readable. Helper `/run/rustusb` root0755; agent `/run/rustusb-agent` broker0711; `/var/lib/rustusb-agent` broker0700; root helper journals remain separate `/var/lib/rustusb/native` and `/var/lib/rustusb/physical`. Package must not create real device grants, keys, TLS pins, operator ACLs or broad module/autostart rules. No setuid/setcap; helper started only by root system service.

Beware systemd `RuntimeDirectory=`/`StateDirectory=`: existing wrong-owner directories cause recursive ownership changes; mode adjustments also occur. Avoid those options on preserved state until identity/ownership has been checked without mutation. RuntimeDirectory defaults to removal on stop; use preserve semantics or a validated exact cleanup policy, never recursive runtime deletion as proof no actual owner remains. Persistent StateDirectory is not removed on stop, but that fact does not protect it from a package script or `systemctl clean`. [Jammy execution/ownership semantics](https://manpages.ubuntu.com/manpages/jammy/man5/systemd.exec.5.html).

A dpkg-managed conffile preserves edits on normal upgrade but is removed on purge **before postrm purge**. Maintscripts may not rewrite shipped conffiles. Thus the parent's examples-only `/usr/share/doc/rustusb/examples` scheme avoids clobbering or purge erasure of administrator-created private files. Do not copy defaults over an existing/deleted config, migrate permissions silently, or run rm_conffile/mv_conffile against retained credentials. A standard package-generated required configuration normally must be removed on purge under Policy; RustUSB's files are explicitly provisioned administrator data, not maintscript-generated required defaults. Document retained state clearly. [Configuration policy](https://www.debian.org/doc/debian-policy/ch-files.html#configuration-files), [dpkg-maintscript-helper](https://manpages.ubuntu.com/manpages/jammy/man1/dpkg-maintscript-helper.1.html).

## Proposed package/control layout and ELF dependencies

Base package `rustusb`, Architecture amd64/arm64: `/usr/bin/rustusb`, `/usr/sbin/rustusb-helper` or `/usr/libexec/rustusb/rustusb-helper` with unit matching the chosen path; root-owned installed package-control script; two vendor units; docs/examples/licenses. Includes existing CLI+TUI+agent in the same Rust binary. GUI package `rustusb-gui`: `/usr/bin/rustusb-gui`, desktop/icon resources and GUI license notices. GUI must remain ordinary-user with no direct private-key/helper authority. A versioned base Depends is a product policy choice for matching frontend/broker compatibility; the native GUI itself statically includes shared Rust frontend logic and does not exec the CLI. Exposing separate runtime components later needs a deliberate third package, not duplicate ownership of `/usr/bin/rustusb`.

Candidate base control: `Depends: ${shlibs:Depends}, ${misc:Depends}, adduser, init-system-helpers (>= 1.62), python3`. If the installed lifecycle checker is Python, Python3 is a real runtime dependency; new preinst/failed-upgrade still cannot assume a new Depends is usable. GUI control uses its independently generated `${shlibs:Depends}` and selected base relation. `Priority: optional`; no Multi-Arch:same for these executable packages. Build dependencies include compatible debhelper13 if used, dpkg-dev/binutils, Rust stable1.97 and existing GTK/WebKit development dependencies for GUI only. Packages may not install under `/usr/local`; existing manual examples there are not a package target. [Policy hierarchy](https://www.debian.org/doc/debian-policy/ch-opersys.html#site-specific-programs).

Generate per-package dependencies from actual staged ELF using `dh_shlibdeps -pPACKAGE` or `dpkg-shlibdeps -O -ePATH`. It uses library symbols/shlibs plus package ownership, not merely SONAME guessing. Do not omit unresolved library errors with --ignore-missing-info. Keep GUI's GTK/WebKit/libdbus/libsoup dependencies out of base. Loader/plugin/runtime resources loaded indirectly require runtime inspection too. [dh_shlibdeps](https://manpages.ubuntu.com/manpages/jammy/man1/dh_shlibdeps.1.html), [dpkg-shlibdeps](https://manpages.ubuntu.com/manpages/jammy/man1/dpkg-shlibdeps.1.html).

Read-only inspection of currently present x86_64 release ELF (not a new build): CLI/helper NEEDED sets contain libgcc_s, libm, libc and ld-linux; GUI additionally links gdk3, gdk_pixbuf, cairo, gobject/glib/gio, dbus, webkit2gtk4.1, gtk3, soup3 and javascriptcoregtk4.1. This does not establish minimum glibc/symbol versions or arm64 equivalence. Save package-generated shlibdeps per build and use readelf `-d -V -l`, architecture and hashes. Noble t64 package names make hard-coded Jammy runtime dependency lists brittle; do not assume cross-release Provides correctness without actual apt resolution. Prefer four distro/arch build-and-install cells or build on updated Jammy per arch then prove Noble installation/runtime explicitly; never claim a Noble-built ELF supports Jammy merely because the SONAME exists.

## Verification API map and required lifecycle observations

Commands are an execution plan, not results from this research:

```sh
# Inspect/extract without executing maintscripts
dpkg-deb --info ./PACKAGE.deb
dpkg-deb --contents ./PACKAGE.deb
dpkg-deb --control ./PACKAGE.deb ./control-inspect
dpkg-deb --field ./PACKAGE.deb Package Version Architecture Depends

# In each disposable Ubuntu/systemd VM, after approved install/upgrade actions
dpkg-query -W -f='${Package} ${Version} ${Architecture} ${db:Status-Abbrev}\n' rustusb rustusb-gui
dpkg --audit rustusb rustusb-gui
dpkg --verify --verify-format=rpm rustusb rustusb-gui
systemctl show rustusb-agent.service rustusb-helper.service
systemctl is-enabled rustusb-agent.service rustusb-helper.service
```

`dpkg --verify` presently verifies recorded content MD5, not ownership/mode/security authenticity; independently stat/lstat binaries, scripts, parent paths, sockets, state and credentials. `--audit` checks database/partial-install consistency and is not USB release evidence. Do not globally force-confnew/force-overwrite to silence lifecycle problems. [Jammy dpkg](https://manpages.ubuntu.com/manpages/jammy/man1/dpkg.1.html), [dpkg-query](https://manpages.ubuntu.com/manpages/jammy/man1/dpkg-query.1.html).

Minimum matrix: fresh default-disabled install without live config; explicit provision/start with real UID identities; idle and active cooperative upgrade; identical upgrade; failed stop with original package files retained; malformed new config failure without destructive reset; retry configure; remove then reinstall; direct purge and removed-then-purge; preexisting admin masks/disabled/enabled state; unavailable manager/alternate root rejection; preserved config/key/state/account inode/hash/UID/mode; unresolved old USB records carried through reinstall and refused automatic takeover. Measure actual original process wait and cgroup exit, original root receipts and kernel baseline independently. Active user GUI close/recovery must preserve E57 UUIDs; package scripts must not delete any operator's XDG recovery state. Rollback to an older binary is not automatically safe for newer disk schemas; reject downgrade or document/test exact compatibility before running old owners.

## Distribution and remaining boundaries

Use `/usr/share/doc/PACKAGE/copyright` with complete notices and source references; DEP5 is a useful machine-readable format. Root/project and patched glib metadata are MIT; Tauri licensing/other Rust dependency obligations must come from the actual resolved license inventory, not only the project's top-level MIT label. Keep the existing two-line glib backport, upstream notices and patch attribution. Prefer distro GTK/WebKit dependencies instead of rebundling their libraries/resources; installing via distro dependency does not make their licenses MIT. Shipping statically linked Rust dependency code still needs its notices. Debian packaging metadata is not proof of Debian/Ubuntu archive acceptance, reproducibility or signature authenticity. [Debian copyright format1.0](https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/), [Policy documentation/copyright](https://www.debian.org/doc/debian-policy/ch-docs.html#copyright-information).

Unresolved before delivery: root-control pre-unpack/fallback implementation and old-package compatibility; precise cgroup-v1/v2 validation and unit override handling; unsupported standalone owners/offline roots; startup journal hard blocks; downgrade schema policy; distribution-specific runtime resolution; real amd64+arm64 installation/upgrade/uninstall VM results; actual active USB cleanup/unknown retained-record semantics. Infinite cooperation cannot promise bounded stop for permanent kernel/FS waits. Hardware G1/G2, fullG3 and fullPhase5–7 remain open.

## Raw evidence and retrieval limitations

Raw directory: `/tmp/rustusb-debian-research-20261025/`. Indexes `sources-initial.json`, `sources-followup.json`, `sources-packages.json`, `sources-libdebhelper.json` preserve successful response bytes/SHA and original retrieval failures; wrong Launchpad binaryFileUrls operations returned400 and are retained. packages.ubuntu.com Web opens timed out/500; no facts rely on them. Several Ubuntu manpage TLS handshakes timed out, some succeeded on an explicit later attempt; no failure was rewritten as success. git.launchpad/source.debian.org Web paths were unavailable; final script facts instead use exact Ubuntu archive packages and extracted source scripts.

Selected source references and metadata in `selected-public/` are suitable for parent review/selective archival; the remaining extracted package trees are research workspace, not requested repository contents. No whole upstream component skill was created.

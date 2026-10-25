# Install the experimental Ubuntu packages

The source builder produces separate native `rustusb` and optional `rustusb-gui`
preview packages. The base includes the CLI/TUI, root helper, nonroot agent units
and package lifecycle scripts. Building a package is not installation acceptance;
see the [full Ubuntu validation](research/debian-installation-validation.md) and
[design](research/debian-installation-design.md) for the actual scope.
Ubuntu 22.04/24.04 amd64 and arm64 are targets. Build on the oldest updated
distribution you intend to support for each architecture; dependencies are derived
from the actual binaries, including GTK/WebKit for GUI. Do not use an amd64 package
on arm64. Initial unupdated Jammy WebKit 2.36 is insufficient for the current GUI.

## Build and inspect

Install the distribution's Rust build prerequisites, `dpkg-dev`, `binutils` and
the native GUI dependencies described in the [GUI guide](gui.md). Run the
complete original checks before packaging:

```sh
scripts/check.sh
scripts/check-gui.sh
cargo fetch --locked
cargo fetch --locked --manifest-path apps/rustusb-gui/Cargo.toml
python3 packaging/debian/build.py --output /tmp/rustusb-debs \
  --version '0.1.0~preview1-1' \
  --gui apps/rustusb-gui/target/release/rustusb-gui
dpkg-deb --info /tmp/rustusb-debs/rustusb_0.1.0~preview1-1_amd64.deb
dpkg-deb --contents /tmp/rustusb-debs/rustusb_0.1.0~preview1-1_amd64.deb
```

Omit `--gui` for a base-only package. The builder does not compile, install,
enable or start services. It requires the CLI's actual agent/TUI features and
records source status, architecture, ELF/package hashes and derived dependencies.
Artifacts are unsigned local previews, not a signed APT repository or a release.

## Install and provision

On the intended Ubuntu system, review the packages and run `sudo apt install`
with their explicit local paths (install both together when using GUI). A fresh
installation creates a non-login `rustusb` account, installs disabled units and
creates an empty protected `/etc/rustusb` directory. It does not start services,
authorize devices or install live configuration. Obtain its actual numeric UID:

```sh
id rustusb
systemctl is-enabled rustusb-helper.service rustusb-agent.service
systemctl is-active rustusb-helper.service rustusb-agent.service
```

`is-enabled` and `is-active` normally return nonzero for disabled/inactive units.
Provision the explicit [helper policy](native-guide.md) and
[secure TLS profiles](secure-guide.md), then follow the
[application manifest guide](application-service-guide.md). Set every broker UID
in the policy, public manifest and TLS profile to the actual dedicated account.
Keep operators on separate ordinary accounts. Package units require these paths:

| Purpose | Path | Owner/mode |
| --- | --- | --- |
| Helper policy | `/etc/rustusb/policy.json` | root, not group/other writable |
| Public manifest | `/etc/rustusb/agent.json` | root, usually 0644 |
| Helper socket parent | `/run/rustusb` | root:root 0755 |
| Helper retained journals | `/var/lib/rustusb` | root:root 0700 |
| Agent socket parent | `/run/rustusb-agent` | rustusb:rustusb 0711 |
| Agent retained data | `/var/lib/rustusb-agent` | rustusb:rustusb 0700 |

The service preparation tool creates absent runtime/data leaves only. It refuses
symlinks, unsafe ancestors, existing wrong ownership/modes and manifest path/UID
mismatches, preserving the original evidence. It never repairs recursively.
Product loaders perform the actual bounded policy/TLS/ACL validation. Load the
approved kernel modules and install the running kernel's matching USB/IP tools
as described in the Native guide; the package does not guess a kernel flavor,
load modules, start plaintext usbipd or change network/firewall policy.

After provisioning, explicitly start the helper and agent. Enable at boot only
when that is the administrator's intended policy:

```sh
sudo systemctl start rustusb-helper.service rustusb-agent.service
# Optional administrator decision:
sudo systemctl enable rustusb-helper.service rustusb-agent.service
rustusb agent --config /etc/rustusb/agent.json profiles
rustusb-gui --manifest /etc/rustusb/agent.json
```

The GUI desktop entry uses that same public manifest. Run GUI/TUI as an ordinary
authorized operator, never as root. Units do not enable automatic rules; an
administrator may explicitly replace the agent ExecStart in a systemd drop-in to
use one of the documented automation policy options. Preserve the complete actual
command and the cooperative stop settings. Existing disk3/disk4 policy must not
be silently downgraded. `Type=exec` acknowledges execution, not listener readiness;
use the actual profile query and service logs to verify readiness.

## Upgrade, stop and remove

Upgrade with explicit reviewed local packages, as for installation. The old prerm,
new preinst and failed-upgrade fallback require the installed lifecycle gate. It
stops/verifies the agent first, then the helper, before unpack/removal. Services
stay stopped after upgrade; existing administrator enable links remain for the
next intentional boot. Review configuration and old records before explicit
restart. Failed/unknown stop prevents file replacement. Inspect the actual result:

```sh
systemctl show rustusb-agent.service rustusb-helper.service \
  -p ActiveState -p SubState -p Result -p ExecMainCode -p ExecMainStatus -p ControlGroup
sudo apt remove rustusb-gui rustusb
# Optional: remove package bookkeeping too; retained deployment data stays.
sudo apt purge rustusb-gui rustusb
```

Both units send TERM to the actual Rust main, disable automatic SIGKILL and wait
without a stop timeout. A real kernel/filesystem block can therefore block dpkg
or ordinary reboot indefinitely. Do not call that a successful stop. The gate
requires the actual booted systemd manager, unified cgroup v2, matching safe unit
settings, successful main exit and empty original/current cgroup. An already
inactive administrator mask is verified without unmasking it. Alternate
`DPKG_ROOT` and offline upgrade/removal are unsupported and fail closed. These
packages manage their fixed units; migrate separately installed/manual processes
explicitly before adopting them. Unit exit0 is a process lifecycle observation,
not a USB release proof; legacy kernel sessions and unresolved old records still
require the original root coordination interfaces.

Remove/purge disable recorded WantedBy links but preserve administrator masks,
configuration, keys, broker account/UID and both services' persistent journals.
No live configuration is shipped as a dpkg conffile. Do not delete retained data
or recycle the UID to make removal appear clean. Reinstall uses the retained
identity and evidence. A stale helper socket is not automatically removed; inspect
the old owner and retained state. `/run` is volatile across real reboot, whereas
`/var/lib` records remain. Crash/power loss and the existing helper startup signal
registration gap have no new recovery guarantee from these units.

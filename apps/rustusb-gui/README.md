# RustUSB native GUI preview

An ordinary-user Linux Tauri frontend for the existing authorized application
service. It shares the headless worker, discovery, fixed-profile IPC and checked
preferences with the TUI. Closing the GUI joins its local work; service-owned jobs
continue independently. Selecting a device row does not alter a profile's devices.

Build on an updated Ubuntu22.04/24.04 with Rust1.97.1, GTK3>=3.24 and
WebKitGTK4.1>=2.40 development packages:

```sh
scripts/check-gui.sh
apps/rustusb-gui/target/release/rustusb-gui --manifest /etc/rustusb/agent.json
```

Provision the protected public manifest and agent using the repository's existing
[application-service guide](../../docs/research/application-service-design.md). Run the
GUI as the authorized ordinary user; private service keys must remain unreadable.
It does not start the agent, install a helper, load TLS private keys or take USB
ownership. No shell/fs/http/process plugins are exposed. The packaged main WebView
has only three enumerated commands, restrictive CSP and denied popups/remote
navigation/frames. See the [design contract](../../docs/research/native-gui-design.md).

Pages: local/remote inventories, exact job/session stops, independent link health,
audit, system observations and checked user preferences. All Start/Stop actions
have a default-cancel confirmation. Start means the entire configured profile.
A lost/refused Start retains its original request UUID in Rust; only RetryStart
can resolve it. Accepted stop and transport RTT are not USB release/completion.

This separate workspace keeps GTK out of default Native builds and has a mandatory
[GUI gate](../../scripts/check-gui.sh) and four-platform CI workflow. The
[scoped GLib backport](vendor/README.md) retains the published version and license;
version-only scanners may still flag0.18.5. Native GTK/WebKit and dependencies have
their own licenses; the application MIT license does not relicense them.

Automatic sharing rules, `.deb`/systemd packaging, install/upgrade/uninstall/reboot
qualification and complete Phase5 acceptance remain required work. QEMU procedure:
[native WebView test](../../scripts/qemu_gui/README.md). Hardware and Windows gates
remain open.

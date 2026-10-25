# Full Ubuntu package lifecycle

This runner boots signed-checksum official Ubuntu cloud disks with real systemd
PID1. It is separate from the existing USB initramfs suites and does not replace
their Rootproof/driver/data-path assertions. The test starts a real virtual HID
export job, but does not import it or claim new USB I/O acceptance.

Host prerequisites: KVM/QEMU x86_64, qemu-img, genisoimage, OpenSSH client,
OpenSSL, a native C compiler and Python 3.10+. Use dated official amd64 qcow2 images. Verify their
SHA256SUMS.gpg with the Ubuntu cloud-image keyring and actual image SHA256; supply
a JSON receipt with `signature_verified: true`, `sha256`, URL/date/size. The
runner independently rehashes the actual image; the supplied receipt is not a
substitute for performing signature verification. Keep the originals.

Build two strictly increasing preview versions with `packaging/debian/build.py`,
each in its own directory containing exactly one base and GUI `.deb`. Then:

```sh
python3 scripts/qemu_package/run.py \
  --image /private/ubuntu-22.04-server-cloudimg-amd64.img \
  --image-receipt /private/receipt.json \
  --first /tmp/deb-v1 --second /tmp/deb-v2 \
  --evidence /tmp/rustusb-package-jammy
```

Repeat with the actual Noble image and a different evidence directory. Network
uses QEMU user-mode networking and a loopback-only ephemeral SSH forward. Guest
APT downloads require network access. No host USB passthrough or host root/service
change occurs. The guest is disposable and explicitly provisions only a loopback
TLS virtual profile. GUI is launched under ordinary UID1000 with actual Xvfb and
openbox, then receives WM_DELETE_WINDOW; its actual exit must be 0.

The runner records package/source/ELF hashes, actual command bytes/exits, native
GUI lifecycle, failed old-prerm/new-failed-upgrade refusal, an actual broker
journal fsync holding the real dpkg upgrade, helper availability during that wait,
cooperative completion after the real fsync returns, retained old job, real guest reboot through
QEMU exit0/relaunch and changed boot UUID, enable semantics, remove/purge, retained
UID/config/credentials/journal, and administrator-mask preservation. It never
uses an observation timeout as proof that a service stopped. Cleanup still waits
for actual QEMU termination; any forced QEMU cleanup is explicitly a failed case.

`.private/` contains the disk, NoCloud seed, SSH/TLS private keys and uploaded
packages. Exclude the entire directory from public evidence. Export only the
explicit public evidence files and guest-evidence tar; inspect its members before
publication. Configuration/key *hashes* are retained, never private key bytes.
The acceptance method is by the same author as the package, not an independent
implementation or a full hardware/architecture/desktop compatibility audit.

The waiting case explicitly installs a guest-only LD_PRELOAD test library and
administrator drop-in outside the product package. It records original PID/UID
and the actual journal FD's inode before waiting for one FIFO byte, then calls the
real libc fsync and records its actual result. It does not return synthetic IO
success, alter Rust binaries or prove an arbitrary permanent kernel block is
cancellable. The controller releases the gate in finally and waits for the real
dpkg child before propagating an assertion failure. SIGSTOP cannot implement this
wait: systemd deliberately sends SIGCONT immediately after its initial stop
signal; the original failed diagnostic observations remain preserved.

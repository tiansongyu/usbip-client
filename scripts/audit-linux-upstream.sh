#!/usr/bin/env bash
# Download or read a pinned, bounded source evidence set. Never changes USB state.
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo 'usage: scripts/audit-linux-upstream.sh AUDIT_CACHE_DIRECTORY' >&2
  exit 2
fi
python3 - "$1" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

commit = '7be4bc52fd9e0e55dc9a2e0232b89427d2c4b614'
paths = [
    'tools/usb/usbip/configure.ac',
    'tools/usb/usbip/src/usbip.c',
    'tools/usb/usbip/src/usbip_bind.c',
    'tools/usb/usbip/src/usbip_unbind.c',
    'tools/usb/usbip/src/usbip_attach.c',
    'tools/usb/usbip/src/usbip_detach.c',
    'tools/usb/usbip/src/usbipd.c',
    'tools/usb/usbip/src/usbip_list.c',
    'tools/usb/usbip/src/usbip_network.c',
    'tools/usb/usbip/src/usbip_network.h',
    'tools/usb/usbip/libsrc/usbip_common.h',
    'tools/usb/usbip/libsrc/vhci_driver.c',
    'drivers/usb/usbip/stub_main.c',
    'drivers/usb/usbip/stub_dev.c',
    'drivers/usb/usbip/stub_rx.c',
    'drivers/usb/usbip/stub_tx.c',
    'drivers/usb/usbip/vhci_hcd.c',
    'drivers/usb/usbip/vhci_sysfs.c',
    'drivers/usb/usbip/usbip_common.c',
    'drivers/usb/usbip/usbip_common.h',
    'drivers/usb/usbip/usbip_event.c',
    'include/uapi/linux/usbip.h',
    'Documentation/usb/usbip_protocol.rst',
    'COPYING',
    'LICENSES/exceptions/Linux-syscall-note',
]
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
marker = root / 'COMMIT'
if marker.exists() and marker.read_text().strip() != commit:
    raise SystemExit('cache COMMIT differs from pinned audit baseline; choose another directory')
marker.write_text(commit + '\n')
manifest = []
for name in paths:
    target = root / name
    if not target.exists():
        request = urllib.request.Request(
            f'https://raw.githubusercontent.com/torvalds/linux/{commit}/{name}',
            headers={'User-Agent': 'RustUSB-pinned-source-audit'},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            content = response.read(1_048_577)
        if len(content) > 1_048_576:
            raise SystemExit(f'unexpected source size: {name}')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    content = target.read_bytes()
    manifest.append({'path': name, 'sha256': hashlib.sha256(content).hexdigest(),
                     'bytes': len(content)})
print(json.dumps({'repository': 'torvalds/linux', 'commit': commit,
                  'cache': str(root), 'files': manifest}, indent=2))
PY

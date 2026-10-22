#!/usr/bin/env bash
# Development-only nightly tooling; the production workspace stays on stable.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p fuzz/corpus/devlist
python3 - <<'PY'
from pathlib import Path
import struct
corpus = Path('fuzz/corpus/devlist')
header = bytes.fromhex('0111000500000000')
(corpus/'empty').write_bytes(header + struct.pack('>I', 0))
(corpus/'oversized').write_bytes(header + struct.pack('>I', 0xffffffff))
device = bytearray(312)
device[:4] = b'/usb'
device[256:259] = b'1-2'
device[288:300] = struct.pack('>III', 1, 2, 3)
device[300:306] = struct.pack('>HHH', 0x1234, 0x5678, 0x100)
device[311] = 1
(corpus/'one-device').write_bytes(header + struct.pack('>I', 1) + device + bytes([3, 1, 2, 0]))
PY
cargo +nightly fuzz run devlist -- -max_total_time=20 -rss_limit_mb=512 -max_len=131072

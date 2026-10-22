#!/usr/bin/env python3
"""Create small, deterministic USB/IP management/data seeds for server_frame."""
import argparse
from pathlib import Path
import struct


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, default=Path("fuzz/corpus/server_frame"))
    target = parser.parse_args().directory
    target.mkdir(parents=True, exist_ok=True)
    import_hid = bytes.fromhex("0111800300000000") + b"1-1" + bytes(29)
    seeds = {"devlist": bytes.fromhex("0111800500000000"), "import-hid": import_hid}
    for name, direction, endpoint, flags, length, packets, setup, payload in [
        ("device-descriptor", 1, 0, 0, 18, 0, bytes.fromhex("8006000100001200"), b""),
        ("hid-pending", 1, 1, 0x200, 8, 0, bytes(8), b""),
        ("short-not-ok", 1, 0, 1, 64, -1, bytes.fromhex("8006000100001200"), b""),
        ("out-small", 0, 0, 0, 3, 0, bytes.fromhex("2109000200000300"), b"abc"),
        ("out-oversized", 0, 0, 0, 65_537, 0, bytes(8), b""),
        ("out-negative", 0, 0, 0, 0xffff_ffff, 0, bytes(8), b""),
        ("iso-reject", 1, 1, 2, 8, 1, bytes(8), b""),
    ]:
        seeds[name] = struct.pack("!IIIIIIIIiI", 1, 7, 0x10001, direction, endpoint, flags, length, 0, packets, 0) + setup + payload
    seeds["unlink"] = struct.pack("!IIIIII", 2, 8, 0x10001, 0, 0, 7) + bytes(24)
    for name, data in seeds.items():
        (target / name).write_bytes(data)
    print(f"Created {len(seeds)} fixed seeds in {target}")


if __name__ == "__main__":
    main()

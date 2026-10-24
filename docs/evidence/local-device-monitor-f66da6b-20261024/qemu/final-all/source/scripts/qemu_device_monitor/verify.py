"""Inspect real guest command output; never call a product parser or rewrite results."""
import base64
import hashlib
import json
from pathlib import Path
import re


def extract(evidence):
    text = (evidence / "guest-evidence.log").read_text(errors="strict")
    pattern = r"RUSTUSB_MONITOR_FILE_BEGIN name=([A-Za-z0-9_.-]+) bytes=(\d+) sha256=([0-9a-f]{64})\n(.*?)RUSTUSB_MONITOR_FILE_END name=\1\n"
    out = evidence / "guest-artifacts"
    out.mkdir(exist_ok=False)
    records = []
    total = 0
    for match in re.finditer(pattern, text, re.S):
        name, size, digest, encoded = match.groups()
        assert name not in {row["name"] for row in records} and len(records) < 64
        data = base64.b64decode("".join(encoded.split()), validate=True)
        assert len(data) == int(size) and len(data) <= 8 * 1024 * 1024
        assert hashlib.sha256(data).hexdigest() == digest
        total += len(data)
        assert total <= 16 * 1024 * 1024
        (out / name).write_bytes(data)
        records.append({"name": name, "bytes": len(data), "sha256": digest})
    assert records, "no complete guest artifacts"
    (evidence / "decoded-files.json").write_text(json.dumps(records, indent=2) + "\n")
    return out


def stream(path):
    frames = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    batches, active, devices, changes = [], None, [], []
    stopped = []
    for frame in frames:
        assert frame["schema_version"] == 1
        assert len(json.dumps(frame, separators=(",", ":")).encode()) < 65536
        kind = frame["event"]
        if kind == "stopped":
            assert active is None and frame["observation_worker_joined"] is True
            stopped.append(frame)
            continue
        if kind == "snapshot_begin":
            assert active is None
            active, devices, changes = frame, [], []
        else:
            assert active and frame["instance"] == active["instance"] and frame["revision"] == active["revision"]
            if kind == "device":
                devices.append(frame["observation"])
            elif kind == "device_change":
                changes.append(frame["change"])
            elif kind == "snapshot_end":
                assert frame["device_count"] == active["device_count"] and frame["change_count"] == active["change_count"]
                assert len(devices) == (active["device_count"] or 0) and len(changes) == active["change_count"]
                if active["device_count"] is None:
                    assert active["state"] in ("initializing", "stale")
                batches.append({"begin": active, "devices": devices, "changes": changes, "end": frame})
                active = None
            else:
                raise AssertionError(f"unexpected event {kind}")
    assert active is None and len(stopped) == 1 and frames[-1]["event"] == "stopped"
    assert len({batch["begin"]["instance"] for batch in batches}) == 1
    assert all(left["end"]["revision"] < right["end"]["revision"] for left, right in zip(batches, batches[1:]))
    return batches


def verify(evidence, summary):
    evidence = Path(evidence)
    text = (evidence / "guest-evidence.log").read_text()
    kernel = (evidence / "guest-kernel.log").read_text(errors="replace")
    assert text.count("RUSTUSB_MONITOR_CHANNEL: ttyS1 kernel=ttyS0") == 1
    assert text.count("RUSTUSB_MONITOR_RESULT: PASS") == 1 and "RUSTUSB_MONITOR_RESULT: FAIL" not in text
    assert not any(marker in kernel for marker in ["Kernel panic", "BUG:", "Oops:"])
    out = extract(evidence)
    batches = stream(out / "watch.jsonl")
    names = ["empty", "added", "unbound", "rebound", "removed", "readded"]
    points = {name: json.loads((out / f"point-{name}.json").read_text()) for name in names}
    for point in points.values():
        assert point in batches and point["begin"]["state"] == "current" and point["begin"]["failure"] is None
    assert [points[name]["begin"]["device_count"] for name in names] == [0, 1, 1, 1, 0, 1]
    assert all(points[a]["end"]["revision"] < points[b]["end"]["revision"] for a, b in zip(names, names[1:]))
    for previous, name in zip(names, names[1:]):
        assert points[name]["begin"]["counters"]["usb_hints"] > points[previous]["begin"]["counters"]["usb_hints"], name
        assert points[name]["begin"]["reason"] in ("usb_hint", "resync"), name
    first = points["added"]["devices"][0]
    unbound = points["unbound"]["devices"][0]
    rebound = points["rebound"]["devices"][0]
    second = points["readded"]["devices"][0]
    assert first["device"]["id"] == second["device"]["id"] and first["device"]["bus_id"] == second["device"]["bus_id"]
    assert first["stamp"] != second["stamp"] and first["stamp"]["sysfs_inode"] != second["stamp"]["sysfs_inode"]
    assert first["stamp"] == unbound["stamp"] == rebound["stamp"]
    assert any(row["class"] == 3 and row["driver"] == "usbhid" for row in first["device"]["interfaces"])
    assert any(row["class"] == 3 and row["driver"] is None for row in unbound["device"]["interfaces"])
    assert any(row["class"] == 3 and row["driver"] == "usbhid" for row in rebound["device"]["interfaces"])
    assert any(change["kind"] == "removed" and change["device_id"] == first["device"]["id"] for change in points["removed"]["changes"])
    for name, record in [("first", first), ("second", second)]:
        stat = (out / f"stat-{name}.txt").read_text()
        observed = {key: int(value) for key, value in re.findall(r"(STAT_DEV|STAT_INODE|BUSNUM|DEVNUM)=(\d+)", stat)}
        assert observed == {"STAT_DEV": record["stamp"]["sysfs_device"], "STAT_INODE": record["stamp"]["sysfs_inode"], "BUSNUM": record["stamp"]["bus_number"], "DEVNUM": record["stamp"]["device_number"]}
    actors = []
    for name in ["initial", "before-stop"]:
        actor = (out / f"actor-{name}.txt").read_text()
        uid = re.search(r"^Uid:\s+(.+)$", actor, re.M)
        assert uid and [int(value) for value in uid.group(1).split()] == [2000] * 4
        assert re.search(r"^CapEff:\s+0000000000000000$", actor, re.M)
        assert f"{summary['binary_sha256']['rustusb']}  /proc/" in actor
        assert "devices --watch --json" in actor and "/dev/bus/usb/" not in actor
        pid = int(re.search(r"^PID=(\d+)$", actor, re.M).group(1))
        stat = re.search(rf"^{pid} \([^\n]+\) (.+)$", actor, re.M).group(1).split()
        sockets = set(re.findall(r"TARGET=socket:\[(\d+)\]", actor))
        netlink = []
        for line in actor.split("NETLINK_TABLE\n", 1)[1].splitlines()[1:]:
            row = line.split()
            if len(row) >= 10 and row[-1] in sockets and row[1] == "15" and int(row[3], 16) == 1:
                netlink.append(row[-1])
        assert len(netlink) == 1
        actors.append({"pid": pid, "start_ticks": int(stat[19]), "uid": 2000, "executable_sha256": summary["binary_sha256"]["rustusb"], "actual_group1_socket_inode": netlink[0]})
    assert actors[0]["pid"] == actors[1]["pid"] and actors[0]["start_ticks"] == actors[1]["start_ticks"]
    assert (out / "watch-wait.txt").read_text().strip() == "0"
    raw = (out / "kernel-events.log").read_text()
    for action in ["add", "remove", "bind", "unbind"]:
        assert f"ACTION={action}\n" in raw, action
    assert "SUBSYSTEM=usb\n" in raw
    final = (out / "independent-final.txt").read_text().splitlines()
    assert not any(re.search(r"/[1-9][0-9]*-[1-9][0-9]*(?:[.:][0-9]+)*$", line) for line in final)
    commands = [row["command"] for row in json.loads((evidence / "qmp-commands.json").read_text())]
    assert commands == ["device_add", "device_del", "device_add", "device_del"]
    return {"result": "PASS", "point_revisions": {name: points[name]["end"]["revision"] for name in names},
            "actual_actor_receipts": actors, "observed_batches": len(batches), "actual_cli_wait": 0,
            "kernel_actions_present": ["add", "remove", "bind", "unbind"],
            "same_candidate_and_bus_new_sysfs_generation": True, "complete_batch_end_and_worker_join_receipt": True,
            "final_guest_usb_devices_empty": True,
            "scope": "One disposable Linux guest, actual UID2000 CLI, actual kernel USB notifications and sysfs/driver changes; no USB/IP I/O or real-hardware qualification. No induced ENOBUFS, guaranteed transient capture or performance claim."}

#!/usr/bin/env python3
"""Actual Rust-engine automatic admission under the original framed-v2 faults."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/qemu_data_link_health"))
spec = importlib.util.spec_from_file_location("automatic_rust_link", ROOT / "scripts/qemu_data_link_health/run.py")
link = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = link
spec.loader.exec_module(link)
old_inputs, old_guest, old_lifecycle = link.source_inputs, link.guest_source, link.lifecycle


def role(backend):
    if backend not in ("virtual", "usbfs"):
        raise RuntimeError("automatic Rust fixture supports virtual/usbfs only")
    return "client" if backend == "virtual" else "server"


def extras(backend):
    return {role(backend): [{"uid": 1001, "profiles": ["hid", "serial"] if backend == "virtual" else ["server"], "actions": ["read", "start", "stop", "discover", "audit"]}]}


def inputs():
    result = old_inputs()
    for p in sorted(Path(__file__).parent.iterdir()):
        if not p.is_file() or p.suffix not in (".py", ".sh"):
            continue
        result[str(p.relative_to(ROOT))] = {"bytes": p.stat().st_size, "sha256": link.sha(p)}
    return dict(sorted(result.items()))


def replace(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError("automatic Rust composition anchor changed: " + repr(before))
    return text.replace(before, after, 1)


def guest(backend="virtual"):
    role(backend)
    text = old_guest(backend)
    text = replace(text, "manifest() {", "plain_manifest() {")
    text = replace(text, "start_profile() {", "plain_start_profile() {")
    text = replace(text, "exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run >", "automatic_agent_exec >")
    anchor = '\nif [ "$ROLE" = server ]; then\n    rust_server_setup\n'
    text = replace(text, anchor, '\n' + Path(__file__).with_name("guest.sh").read_text() + anchor)
    for profile in ["server", "hid"]:
        anchor = "    manifest; agent_start 1; denied " + profile + "\n"
        text = replace(text, anchor, anchor + "    automatic_setup\n")
    return text


def automatic_facts(evidence, backend):
    participant = role(backend)
    folder = evidence / (participant + "-artifacts")
    def data(name):
        return json.loads((folder / name).read_text())
    checks = []
    def require(value, label):
        if not value:
            raise RuntimeError("automatic Rust verification: " + label)
        checks.append(label)
    final = data("agent-final.json")
    journal = final["automation"]
    expected = [("serial", "persistent", 1)] + [("hid", mark, n) for n, mark in enumerate(["c2s", "s2c", "both", "final"], 1)] if backend == "virtual" else [("server", "listener", 1)]
    require(len(journal["rules"]) == (2 if backend == "virtual" else 1)
            and len(journal["origins"]) == len(journal["mutations"]) == len(final["jobs"]) == len(expected), "exact rules/origins/mutations/jobs without duplicate firing")
    require(all(r["operator_uid"] == 1000 and r["enabled"] and not r["paused_by_user"] and not r["deleted"] for r in journal["rules"]), "actual delegated rules retain explicit intent")
    origins, tokens = {}, {}
    for profile, mark, revision in expected:
        snapshot = data(f"automatic-admitted-{profile}-{mark}.json")
        view = data(f"automatic-fired-{profile}-{mark}.json")["response"]["rules"]
        rule = next(r for r in journal["rules"] if r["profile"] == profile)
        origin = next(o for o in snapshot["automation"]["origins"] if o["rule_id"] == rule["id"] and o["revision"] == revision)
        job = next(j for j in snapshot["jobs"] if j["id"] == origin["job_id"])
        request = next(r for r in snapshot["requests"] if r["id"] == origin["request_id"])
        receipt = data(f"start-{profile}-{mark}.json")["response"]
        require(origin["operator_uid"] == job["operator_uid"] == request["uid"] == 1000
                and origin["profile"] == job["profile"] == request["profile"] == profile
                and request["job_id"] == job["id"], f"same saved origin/job/Start request {profile}/{mark}")
        fired = [e for e in snapshot["automation"]["events"] if e["kind"] == "fired" and e["job_id"] == job["id"]]
        require(len(fired) == 1 and fired[0]["sequence"] == origin["sequence"] and fired[0]["request_id"] == origin["request_id"], f"same transaction Fired {profile}/{mark}")
        require(sum(e["action"] == "start" and e["job_id"] == job["id"] for e in snapshot["audit"]) == 1, f"one original Start audit {profile}/{mark}")
        require(receipt["result"] == "started" and receipt["replayed"] is True and receipt["job"]["id"] == job["id"]
                and any(r["last_origin"] == origin for r in view), f"actual rule view and RetryStart response {profile}/{mark}")
        mutations = [json.loads(line) for line in (folder / f"automatic-mutation-{profile}-{mark}.jsonl").read_text().splitlines()]
        retries = [json.loads(line) for line in (folder / f"automatic-mutation-retry-{profile}-{mark}.jsonl").read_text().splitlines()]
        require(len(mutations) == len(retries) == 2 and mutations[0]["event"] == "rule_mutation_intent"
                and retries[0]["event"] == "rule_mutation_query" and mutations[0]["request_id"] == retries[0]["request_id"]
                and mutations[1]["response"]["outcome"] == retries[1]["response"]["outcome"] == {"rule_id": rule["id"], "revision": revision, "deleted": False}
                and mutations[1]["response"]["replayed"] is False and retries[1]["response"]["replayed"] is True, f"actual original mutation identity/outcome {profile}/{mark}")
        if revision > 1:
            old = data(f"automatic-before-enable-state-{profile}-{mark}.json")
            previous = origins[(profile, revision - 1)]
            old_job = next(j for j in old["jobs"] if j["id"] == previous["job_id"])
            require(old_job["state"] == "finished" and all(s["cleanup"] == "verified" for s in old["sessions"] if s["job_id"] == old_job["id"]), f"old actual release precedes explicit rearm {mark}")
            require(len(old["automation"]["origins"]) == revision and all(o["revision"] < revision for o in old["automation"]["origins"] if o["profile"] == profile), f"Once cannot automatically loop before Enable {mark}")
        origins[(profile, revision)] = origin
        tokens.setdefault(profile, set()).add(origin["observation"])
    require(all(len(v) == 1 for v in tokens.values()), "same advertisement/local observation explicitly rearmed, no generation claim")
    before, after = data("automatic-before-denied.json"), data("automatic-after-denied.json")
    require(before["schema_version"] == after["schema_version"] == 3
            and all(before[k] == after[k] == [] for k in ["jobs", "sessions", "requests"])
            and before["automation"] == after["automation"]
            and len(after["audit"]) == len(before["audit"]) + 1
            and after["audit"][-1]["actor_uid"] == 1001 and after["audit"][-1]["action"] == "denied" and after["audit"][-1]["failure"] == "permission", "real manual-ACL/no-grant UID denied without resource/rule mutation")
    require((folder / "automatic-denied.wait").read_text().strip() == "1", "actual denied CLI exit1")
    for kind in ["native", "physical"]:
        before_status = (folder / f"automatic-root-{kind}-before-denied.status").read_bytes()
        require(before_status == (folder / f"automatic-root-{kind}-after-denied.status").read_bytes(), "root presence unchanged by denial " + kind)
        if before_status == b"present\n":
            require((folder / f"automatic-root-{kind}-before-denied.json").read_bytes() == (folder / f"automatic-root-{kind}-after-denied.json").read_bytes(), "actual root journal bytes unchanged " + kind)
        else:
            require(before_status == b"absent\n", "absence retained as observation " + kind)
    meta = data("automation-policy-metadata.json")
    require(meta == {"uid": 0, "mode": "644", "links": 1, "canonical": "/etc/rustusb/automation.json"}, "actual root policy metadata")
    policy = data("automation-policy.json")
    keys = data("keys.json")
    grants = [{"operator_uid": 1000, "profile": profile, "devices": [key], "triggers": ["remote_device_available"], "rearm": ["once"], "max_rules": 1} for profile, key in zip(["hid", "serial"], keys, strict=True)] if backend == "virtual" else [{"operator_uid": 1000, "profile": "server", "devices": keys, "triggers": ["local_profile_ready"], "rearm": ["once"], "max_rules": 1}]
    require(policy == {"schema_version": 1, "broker_uid": 2000, "grants": grants}, "entire exact loaded pool and UID1000-only delegation")
    require((folder / "wait-agent-no-policy.txt").read_text().strip() == "0", "actual idle no-policy agent joined before upgrade")
    return {"role": participant, "checks": checks, "assertion_count": len(checks), "automatic_jobs": len(expected), "scope": "Exact automatic admission/original mutation receipts; original v2 actual cleanup/I/O/packet proof remains independently required. No dynamic pools, GUI, physical remote import or hardware claim."}


def lifecycle(backend):
    checker = old_lifecycle(backend)
    schemas = {role(backend): 3}
    def verify(evidence, args):
        result = checker.verify(evidence, args, application_schemas=schemas, extra_operators=extras(backend))
        result["automatic_rules"] = automatic_facts(evidence, backend)
        return result
    return SimpleNamespace(compose_guest=checker.compose_guest, phase_proof=lambda evidence, phase, live=False: checker.phase_proof(evidence, phase, live=live, application_schemas=schemas), verify=verify)


link.source_inputs, link.guest_source, link.lifecycle = inputs, guest, lifecycle
link.BACKENDS = ("virtual", "usbfs")
if __name__ == "__main__":
    if "--help" not in sys.argv and not any(a == "--backend" or a.startswith("--backend=") for a in sys.argv):
        raise SystemExit("choose explicit --backend virtual or --backend usbfs")
    raise SystemExit(link.main())

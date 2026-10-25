#!/usr/bin/env python3
"""Real local-rule Native Start, preserving the original paired agent matrix."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("automatic_agent", ROOT / "scripts/qemu/run-agent-qemu.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)
original_inputs, original_source, original_verify = agent.inputs, agent.guest_source, agent.verify


def inputs():
    values = original_inputs()
    for name in ["scripts/qemu_automatic_rules/run.py", "scripts/qemu_automatic_rules/guest.sh"]:
        path = ROOT / name
        values[name] = {"sha256": agent.digest(path), "bytes": path.stat().st_size}
    return dict(sorted(values.items()))


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise RuntimeError(f"automatic fixture anchor changed: {before!r}")
    return text.replace(before, after, 1)


def guest_source():
    source = original_source()
    source = replace_once(source,
        'exec /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb agent --config /etc/rustusb/agent.json run >',
        'automatic_agent_exec >')
    extension = (ROOT / "scripts/qemu_automatic_rules/guest.sh").read_text()
    source = replace_once(source, '\nif [ "$ROLE" = server ]; then\n', '\n' + extension + '\nif [ "$ROLE" = server ]; then\n')
    source = replace_once(source, 'manifest; agent_start 1; denied server\n', 'manifest; agent_start 1; denied server\n    automatic_policy\n')
    return replace_once(source, '        start_job server\n', '        automatic_start_job server\n')


def verify(evidence, args):
    # Preserve every original denial, kernel, Root proof and three-I/O assertion.
    facts = original_verify(evidence, args, {"server final": 3, "server held0": 3, "server held1": 3})
    def get(name):
        return json.loads((evidence / "server-artifacts" / name).read_text())
    checks = []
    def require(condition, label):
        if not condition:
            raise RuntimeError("automatic rule verification: " + label)
        checks.append(label)
    final = get("agent-stopped-2.json")
    journal = final["automation"]
    require(set(journal) == {"sequence", "rules", "mutations", "origins", "events"}, "strict independent journal fields")
    require(len(journal["rules"]) == 1 and len(journal["origins"]) == 2 and len(final["jobs"]) == 2, "one rule/two actual jobs, no duplicate firing")
    rule = journal["rules"][0]
    require(rule["enabled"] and not rule["paused_by_user"] and not rule["deleted"] and rule["revision"] == 3, "explicit rearm survives, no stop invented by shutdown")
    require([origin["revision"] for origin in journal["origins"]] == [1, 3], "exact original and explicitly rearmed occurrences")
    require(len({origin["observation"] for origin in journal["origins"]}) == 1, "same physical generation requires explicit revision rearm")
    for epoch, revision in [(1, 1), (2, 3)]:
        state = get(f"agent-auto-admitted-{epoch}.json")
        require(state["schema_version"] == 3, f"actual disk3 epoch{epoch}")
        origin = next(origin for origin in state["automation"]["origins"] if origin["revision"] == revision)
        job = next(job for job in state["jobs"] if job["id"] == origin["job_id"])
        request = next(request for request in state["requests"] if request["id"] == origin["request_id"])
        require(origin["rule_id"] == rule["id"] and origin["operator_uid"] == job["operator_uid"] == request["uid"] == 1000 and origin["profile"] == job["profile"] == request["profile"] == "server" and request["job_id"] == job["id"], f"same committed origin/job/Start request epoch{epoch}")
        fired = [event for event in state["automation"]["events"] if event["kind"] == "fired" and event["job_id"] == job["id"]]
        require(len(fired) == 1 and fired[0]["request_id"] == origin["request_id"] and fired[0]["sequence"] == origin["sequence"], f"same transaction firing event epoch{epoch}")
        require(sum(event["action"] == "start" and event["job_id"] == job["id"] for event in state["audit"]) == 1, f"original Start audit once epoch{epoch}")
        require(get(f"start-server-{epoch}.json")["response"]["replayed"] is True, f"actual RetryStart readback epoch{epoch}")
    paused = get("agent-paused-restart.json")
    require(len(paused["jobs"]) == len(paused["automation"]["origins"]) == 1 and paused["automation"]["rules"][0]["paused_by_user"] and paused["automation"]["rules"][0]["revision"] == 2, "manual Stop pause retained after real restart and settle wait")
    policy = get("automation-policy.json")
    require(policy == {"schema_version": 1, "broker_uid": 2000, "grants": [{"operator_uid": 1000, "profile": "server", "devices": get("keys.json"), "triggers": ["local_profile_ready"], "rearm": ["once"], "max_rules": 4}]}, "actual root policy delegates entire immutable pool")
    facts["automatic_rules"] = {"checks": checks, "assertion_count": len(checks), "scope": "actual local complete-pool Native only; no remote physical, dynamic pool or hardware claim"}
    return facts


agent.inputs, agent.guest_source, agent.verify = inputs, guest_source, verify
if __name__ == "__main__":
    raise SystemExit(agent.main())

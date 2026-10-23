"""Native lifecycle proof for the new actual data-link experiment.

This verifier is separate from the packet auditor. Assertions are evidence
checks, never a count of USB cases. It does not call an old runner's verifier.
"""
import hashlib
import json
import re
import struct
from pathlib import Path

import audit as packet_audit

PHASES = ("c2s", "s2c", "both")
DATA_LINK = {"protocol": "framed_v2", "interval_millis": 1000, "response_timeout_millis": 3000}
PROOFS = {"native_import": "native_import_released", "native_stub": "native_stub_available",
          "native_export": "native_export_restored"}


def require(value, message):
    if not value:
        raise RuntimeError("data-link lifecycle: " + message)


def one(rows, predicate, label):
    values = [row for row in rows if predicate(row)]
    require(len(values) == 1, "one exact " + label)
    return values[0]


def proof(root, session):
    require(len(session["attempts"]) == 1, "one owned capability per Native session")
    attempt = session["attempts"][0]
    result = one([row["terminal"] for row in root["terminals"]],
                 lambda t: t["kind"] == attempt["kind"] and t["correlation"] == attempt["correlation"], "retained root terminal")
    require(result["owner_uid"] == 2000 and result["device_key"] == session["device_key"]
            and result["resource_id"] == attempt["resource_id"] and result["proof"] == PROOFS[attempt["kind"]], "full root proof identity/type")
    require(attempt["proof"] in (None, result["proof"]), "root proof echo")
    return result


def phase_proof(evidence, phase, live=False):
    require(phase in PHASES, "known fault phase")
    evidence = Path(evidence)
    files = {}
    def original(role, name):
        path = evidence / "live" / role / name if live else evidence / f"{role}-artifacts" / name
        raw = path.read_bytes()
        require(len(raw) <= 1048576, "bounded phase original")
        files[f"{role}/{name}"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        return raw
    def data(role, name):
        return json.loads(original(role, name))
    keys = json.loads((evidence / "keys.json").read_text())
    target_key, sibling_key = keys
    outcomes = []
    for role, wanted_role in (("client", "import"), ("server", "incoming")):
        before = data(role, f"agent-pre-{phase}.json")
        after = data(role, f"agent-fault-{phase}.json")
        target = one(before["sessions"], lambda s: s["device_key"] == target_key and s["role"] == wanted_role and not s["terminal"], "pre-fault target")
        ended = one(after["sessions"], lambda s: s["id"] == target["id"], "same terminal target")
        for field in ("id", "instance_id", "job_id", "device_key", "profile", "role"):
            require(ended[field] == target[field], "target identity retained")
        require(ended["terminal"] and ended["cleanup"] == "verified" and ended["state"] == "Disconnected", "target actually released")
        before_sibling = one(before["sessions"], lambda s: s["device_key"] == sibling_key and s["role"] == wanted_role and not s["terminal"], "original live sibling")
        after_sibling = one(after["sessions"], lambda s: s["id"] == before_sibling["id"], "retained sibling")
        for field in ("id", "instance_id", "job_id", "device_key", "attempts", "terminal", "cleanup", "state"):
            require(after_sibling[field] == before_sibling[field], "sibling lifecycle/capability unchanged")
        require(after_sibling["state"] == "Connected" and after_sibling["operation_error"] is None, "sibling remains connected")
        health = data(role, f"health-{phase}-{role}-terminal.json")
        sample = one(health["response"]["sessions"], lambda h: h["session_id"] == target["id"], "actual ended health")
        prior = data(role, f"health-{phase}-{role}-pre.json")
        prior = one(prior["response"]["sessions"], lambda h: h["session_id"] == target["id"], "actual pre-arm health")
        link = sample["link"]
        require(sample["instance_id"] == target["instance_id"] and link["identity"] == prior["link"]["identity"]
                and link["protocol"] == "framed_v2" and link["phase"] == "ended" and link["end"] is not None,
                "same data connection actual driver ended")
        require(link["sample_sequence"] >= prior["link"]["sample_sequence"]
                and link["elapsed_millis"] >= prior["link"]["elapsed_millis"], "monotonic sample progression")
        if link["end"] == "probe_timeout":
            require(link["probe"] == "timed_out" and ended["operation_error"] == "timeout", "timeout remains operation failure after cleanup")
            elapsed = link["elapsed_millis"] - link["last_probe_enqueued_millis"]
            require(3000 <= elapsed <= 5000, "actual 3-second probe deadline plus bounded 2-second guest scheduling observation")
            require(link["last_reply_sequence"] < link["last_probe_sequence"], "outstanding probe was not matched")
        root_before = data(role, f"root-pre-{phase}.json")
        root_after = data(role, f"root-fault-{phase}.json")
        require(root_after["schema_version"] == 2 and len(root_after["terminals"]) == PHASES.index(phase) + 1, "exact retired target count")
        terminal = proof(root_after, ended)
        if role == "client":
            serial = one(root_before["records"], lambda r: r["target"]["device_key"] == sibling_key, "serial import receipt")
            target_record = one(root_before["records"], lambda r: r["target"]["device_key"] == target_key, "HID import receipt")
            require(root_after["exports"] == [] and root_after["records"] == [serial], "only original target import retired")
            require(terminal["resource_id"] == target_record["channel_id"], "proof matches original target capability")
            local = data(role, f"local-fault-{phase}.json")
            require(local["old_local_exists"] is False and local["old_local_bus_id"] == target_record["state"]["receipt"]["session"]["local_bus_id"], "actual old target local generation gone")
            serial_session = serial["state"]["receipt"]["session"]
            generation = serial_session["local_device_generation"]
            require(local["sibling_local_bus_id"] == serial_session["local_bus_id"]
                    and local["sibling_generation"] == f"{generation['filesystem_device']}:{generation['inode']}",
                    "actual original sibling generation remains")
            rows = [line.split() for line in original(role, f"vhci-fault-{phase}.txt").decode().splitlines() if line.startswith(("hs ", "ss "))]
            require(rows and sum(int(row[2]) == 6 for row in rows) == 1 and all(int(row[2]) in (4, 6) for row in rows), "actual one USED and remaining NULL at disarm")
            serial_row = one(rows, lambda row: int(row[1]) == serial_session["port"], "original serial port")
            target_row = one(rows, lambda row: int(row[1]) == target_record["state"]["receipt"]["session"]["port"], "original target port")
            require(int(target_row[2]) == 4 and int(serial_row[2]) == 6 and serial_row[6] == serial_session["local_bus_id"]
                    and int(serial_row[4], 16) == serial_session["remote_device_id"], "exact target NULL and original serial mapping")
        else:
            require(root_before["records"] == root_after["records"] == [] and len(root_after["exports"]) == 2, "Native exports remain held")
            old = {r["registration"]["device_key"]: r for r in root_before["exports"]}
            new = {r["registration"]["device_key"]: r for r in root_after["exports"]}
            require(set(old) == set(new) == set(keys), "same original exports")
            require(new[sibling_key] == old[sibling_key], "original serial stub retained byte-for-byte")
            require(new[target_key]["stub"] is None and {k: v for k, v in new[target_key].items() if k != "stub"} == {k: v for k, v in old[target_key].items() if k != "stub"}, "only target stub retired, export unchanged")
            require(terminal["resource_id"] == old[target_key]["stub"]["channel_id"], "stub proof matches original capability")
        outcomes.append({"role": role, "session_id": target["id"], "connection": link["identity"],
                         "end": link["end"], "operation_error": ended["operation_error"], "cleanup": ended["cleanup"],
                         "last_probe_enqueued_millis": link["last_probe_enqueued_millis"],
                         "ended_elapsed_millis": link["elapsed_millis"], "root_terminal": terminal})
    require(any(row["end"] == "probe_timeout" for row in outcomes), "at least one actual same-data probe timeout")
    return {"schema_version": 1, "phase": phase, "scope": "Actual observed Native phase proof before disarm, not pending-URB proof", "outcomes": outcomes, "source_artifacts": files}


def verify(evidence, args):
    evidence = Path(evidence)
    def raw(role, name):
        return (evidence / f"{role}-artifacts" / name).read_bytes()
    def data(role, name):
        return json.loads(raw(role, name))
    def vhci(name, count):
        rows = [line.split() for line in raw("client", name).decode().splitlines() if line.startswith(("hs ", "ss "))]
        require(rows and sum(int(r[2]) == 6 for r in rows) == count and all(int(r[2]) in (4, 6) for r in rows), "actual VHCI USED/NULL " + name)
    require(args.backend == "native", "first verifier is Native-only")
    keys = data("client", "keys.json")
    require(keys == data("server", "keys.json") == json.loads((evidence / "keys.json").read_text()) and len(set(keys)) == 2, "actual common two keys")
    phase_facts = []
    for phase in PHASES:
        current = phase_proof(evidence, phase)
        require(current == json.loads((evidence / "bindings" / f"{phase}-terminal.json").read_text()), "late raw artifacts equal pre-disarm proof")
        phase_facts.append(current)
        for role in ("client", "server"):
            require(raw(role, f"health-{phase}-{role}-pre.json") == (evidence / "bindings" / f"{phase}-{role}-health.json").read_bytes(), "arm exact actual health bytes")
            for path in (evidence / "live" / role).iterdir():
                require(path.read_bytes() == raw(role, path.name), "live source identical to final original " + path.name)
        catalog = data("client", f"catalog-during-{phase}.json")
        require(catalog["schema_version"] == 1 and catalog["journal_healthy"] is True and catalog["response"]["result"] == "catalog", "independent Catalog succeeds during target fault")
        vhci(f"vhci-pre-{phase}.txt", 2)
        vhci(f"vhci-fault-{phase}.txt", 1)
    baseline = data("server", "host-before.json")
    require(data("server", "host-restored.json") == baseline, "full original device/configuration/interfaces/drivers restored")
    dynamic = {"configuration", "num_interfaces", "configuration_bytes", "count_bytes", "interfaces", "device_driver", "usbip_status"}
    hardware = lambda rows: [{k: v for k, v in row.items() if k not in dynamic} for row in rows]
    for phase in PHASES:
        for stage in ("pre", "fault"):
            current = data("server", f"host-{stage}-{phase}.json")
            require(hardware(current) == hardware(baseline), "physical generation fixed throughout fault")
            for before, row in zip(baseline, current, strict=True):
                require(row["device_driver"] == "usbip-host" and int(row["configuration"]) == 1 and int(row["num_interfaces"]) == 1
                        and row["interfaces"] == [dict(i, driver="") for i in before["interfaces"]], "actual Native imported phase profile")
                devices = data("server", "devices.json")
                hid_bus = one(devices, lambda d: d["vendor_id"] == 1575, "actual HID") ["bus_id"]
                expected = 1 if stage == "fault" and row["bus"] == hid_bus else 2
                require(row["usbip_status"] == expected, "target AVAILABLE with sibling USED")
    expected_tx = b""
    previous_serial = None
    hid_ids, hid_generations = set(), set()
    held_ids = {"server": set(), "client": set()}
    for round_id in range(1, 8):
        rx = f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
        tx = f"RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n".encode()
        require(raw("client", f"rx-{round_id}.bin") == rx and raw("client", f"tx-{round_id}.bin") == tx
                and (evidence / f"ftdi-host-injected-{round_id}.bin").read_bytes() == rx, "real FTDI 35 bytes each way")
        expected_tx += tx
        if round_id % 2:
            events = raw("client", f"input-{round_id}.bin")
            require(events and len(events) % 24 == 0, "complete x86_64 evdev records")
            values = [struct.unpack("<qqHHi", events[offset:offset + 24])[2:] for offset in range(0, len(events), 24)]
            require((1, round_id + 1, 1) in values and (1, round_id + 1, 0) in values, "actual HID press/release")
            records = data("client", f"client-receipts-{round_id}.json")["records"]
            require(len(records) == 2, "two actual local import receipts")
            serial = one(records, lambda r: r["target"]["device_key"] == keys[1], "retained serial receipt")
            hid = one(records, lambda r: r["target"]["device_key"] == keys[0], "new HID receipt")
            if previous_serial is not None:
                require(serial == previous_serial, "same serial capability across all three faults")
            previous_serial = serial
            hid_ids.add(hid["channel_id"])
            for record in records:
                receipt = record["state"]["receipt"]
                require(record["owner"]["uid"] == 2000 and record["owner"]["starttime"] > 0 and record["correlation"] is not None
                        and record["state"]["phase"] == "observed" and receipt["session"]["status"] == 6
                        and receipt["kernel_socket_inode"] > 0 and receipt["profile"] is not None
                        and receipt["session"]["local_device_generation"] is not None, "actual complete strong local receipt")
            hid_generations.add(json.dumps(hid["state"]["receipt"]["session"]["local_device_generation"], sort_keys=True))
    require((evidence / "ftdi-host-rx.bin").read_bytes() == expected_tx and len(expected_tx) == 245, "actual host receives 245 ordered FTDI bytes")
    require(len(hid_ids) == len(hid_generations) == 4, "fresh HID capabilities and local generations after each fault")
    for role in ("server", "client"):
        log = (evidence / f"{role}-evidence.log").read_text()
        kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
        require(log.count(f"RUSTUSB_LINK_RESULT: PASS role={role}") == 1 and all(mark not in log for mark in ("RUSTUSB_LINK_RESULT: FAIL", "SECURE_ARTIFACT_ERROR", "SECURE_ARTIFACT_OVERSIZE", "SECURE_POWEROFF_RETURNED")), "complete successful guest evidence")
        require("Kernel panic" not in kernel and "Oops:" not in kernel, "final complete S0 recheck")
        require(f"SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false" in log and "SECURE_RNG_RTC_READY crng=true " in log, "real clock/RNG and isolated keys")
        status = raw(role, "identity-agent-1.txt").decode()
        require(re.search(r"^Uid:\s+2000\s+2000\s+2000\s+2000$", status, re.M) is not None
                and all(re.search(rf"^{cap}:\s+0000000000000000$", status, re.M) for cap in ("CapInh", "CapPrm", "CapEff", "CapAmb")), "actual unprivileged agent identity")
        require(re.search(r"^Uid:\s+0\s+0\s+0\s+0$", raw(role, "identity-helper.txt").decode(), re.M) is not None, "actual root helper identity")
        require(raw(role, "wait-agent-1.txt").strip() == b"0", "actual agent TERM/reap0")
        final, root = data(role, "agent-final.json"), data(role, "root-final.json")
        expected_sessions, expected_jobs = (7, 1) if role == "server" else (5, 5)
        require(final["schema_version"] == 2 and len(final["sessions"]) == expected_sessions and len(final["jobs"]) == expected_jobs, "exact complete retained session/job history")
        require(len({s["id"] for s in final["sessions"]}) == expected_sessions and len({j["id"] for j in final["jobs"]}) == expected_jobs, "unique history identities")
        require(all(s["terminal"] and s["cleanup"] == "verified" and s["state"] == "Disconnected" for s in final["sessions"])
                and all(j["state"] == "finished" and j["operator_uid"] == 1000 for j in final["jobs"]), "actual final task/resource retirement")
        require(root["schema_version"] == 2 and root["records"] == root["exports"] == [] and len(root["terminals"]) == expected_sessions, "exact final Root terminals and no active capabilities")
        for session in final["sessions"]:
            proof(root, session)
            require(any(j["id"] == session["job_id"] and j["instance_id"] == session["instance_id"] for j in final["jobs"]), "original actual job/session linkage")
        history = final["link_history"]
        require(len(history) == expected_sessions and len({h["session_id"] for h in history}) == expected_sessions, "one final health history per real session")
        for session in final["sessions"]:
            item = one(history, lambda h: h["session_id"] == session["id"] and h["instance_id"] == session["instance_id"], "health history identity")
            if session["role"] == "export":
                require(item["link"] is None, "export is not a data connection")
            else:
                require(item["link"]["protocol"] == "framed_v2" and item["link"]["phase"] == "ended"
                        and item["link"]["end"] is not None and item["link"]["last_reply_sequence"] is not None,
                        "actual ended v2 samples, never fabricated raw RTT")
        for phase in PHASES:
            held = data(role, f"agent-pre-{phase}.json")
            held_ids[role].update(s["id"] for s in held["sessions"])
        held_ids[role].update(s["id"] for s in data(role, "agent-final-held.json")["sessions"])
        require(held_ids[role] == {s["id"] for s in final["sessions"]}, "final history retains every actual held session")
        after = data(role, "agent-after-denied.json")
        require(all(after[name] == [] for name in ("jobs", "sessions", "requests")) and raw(role, "root-before-denied.json") == raw(role, "root-after-denied.json"), "unauthorized request has no resource effect")
        for reason in ("uid", "profile"):
            require(data(role, f"denied-{reason}.json")["response"] == {"result": "error", "code": "permission"}, "real typed authorization refusal")
    vhci("vhci-final.txt", 0)
    configuration = verify_collected_configuration(evidence, args.family)
    wire = packet_audit.audit(evidence)
    return {"scope": "Native same-data TLS blackhole/cleanup/reuse; no Native pending-URB or hardware claim",
            "phases": phase_facts, "actual_configuration": configuration, "packet_audit": wire,
            "io_rounds": 7, "hid_key_codes": [2, 4, 6, 8], "serial_each_direction_bytes": 245}


def verify_collected_configuration(evidence, family):
    """Exact observed public configuration/DER and permission metadata only."""
    observations=[]
    def get(role,name):return (evidence/f'{role}-artifacts'/name).read_bytes()
    def require(ok,description):
        if not ok:raise RuntimeError('actual configuration evidence: '+description)
    public={name:(evidence/f'{name}-public.der').read_bytes() for name in ['ca','server','client']}
    pin=hashlib.sha256(public['client']).hexdigest()
    keys=json.loads(get('server','keys.json'))
    for role in ['server','client']:
        policy=json.loads(get(role,'helper-policy.json'))
        require(policy['allowed_uids']==[1000] and policy['secure_broker_uid']==2000 and policy['trusted_network'] is False and policy['export_device_ids']==[] and policy['peers']==[],f'{role} actual helper broker/legacy isolation')
        if role=='server':
            require(policy['secure_native_device_ids']==[key.split(':',1)[1] for key in keys],f'{role} actual approved Native pool matches public TLS devices')
        else:
            require(policy['secure_imports']==[{'server_id':'qemu-server','device_keys':keys}],f'{role} actual helper logical server/device pool matches client profiles')
        profiles=[{'id':'server','backend':'native','config':'/etc/rustusb/server.json'}] if role=='server' else [{'id':profile,'backend':'client','config':f'/etc/rustusb/client-{profile}.json'} for profile in ['hid','serial']]
        manifest={'schema_version':1,'broker_uid':2000,'helper_socket':'/run/rustusb/control.sock','control_directory':'/run/agent','data_directory':'/var/lib/rustusb/agent','profiles':profiles,'operators':[{'uid':1000,'profiles':[p['id'] for p in profiles],'actions':['read','start','stop','discover','audit']}]}
        expected={'agent.json':manifest,'policy.json':policy}
        if role=='server':
            expected['server.json']={'schema_version':2,'data_link':DATA_LINK,'broker_uid':2000,'listen':'192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241','devices':keys,'principals':[{'id':'qemu-client','pins':[pin],'devices':keys}],'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/server.der'],'private_key':'/etc/rustusb/server-key.der'}
        else:
            for profile,key in zip(['hid','serial'],keys,strict=True):
                expected[f'client-{profile}.json']={'schema_version':2,'data_link':DATA_LINK,'broker_uid':2000,'server_id':'qemu-server','endpoint':{'host':'192.0.2.1' if family=='ipv4' else 'fd42:7275:7374::1','port':3241},'server_name':'rustusb-qemu.test','device':key,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/client.der'],'private_key':'/etc/rustusb/client-key.der'}
        directories={'/','/etc','/etc/rustusb','/run','/run/agent','/var','/var/lib','/var/lib/rustusb','/var/lib/rustusb/agent'}
        public_paths={'/etc/rustusb/'+name for name in expected}|{'/etc/rustusb/'+name+'.der' for name in public}|{'/etc/rustusb/client-pin.txt'}
        private='/etc/rustusb/'+role+'-key.der'
        for epoch in [1]:
            prefix=f'actual-config-{epoch}-'
            for name,value in expected.items():
                require(json.loads(get(role,prefix+name))==value,f'{role} epoch{epoch} actual {name} exact semantic fields')
                require(get(role,prefix+name)==get(role,'actual-config-1-'+name),f'{role} {name} bytes unchanged over restart')
            require(get(role,prefix+'agent.json')==get(role,'agent-manifest.json'),f'{role} actual startup manifest matches previously captured public manifest')
            require(get(role,prefix+'client-pin.txt')==(pin+'\n').encode(),f'{role} epoch{epoch} actual public pin text equals exact raw client DER SHA256')
            for name,der in public.items():
                require(get(role,prefix+name+'.der')==der,f'{role} epoch{epoch} actual {name} public DER equals retained host DER')
            metadata=json.loads(get(role,f'config-metadata-{epoch}.json'));by={row['path']:row for row in metadata}
            require(len(metadata)==len(by) and set(by)==directories|public_paths|{private},f'{role} epoch{epoch} exact fixed permission coverage')
            for path,row in by.items():
                require(row['canonical']==path and row['inode']>0 and row['device']>=0,f'{role} exact nonsymlink path {path}')
                mode=int(row['mode'],8)
                if path in directories:
                    broker=path in ['/run/agent','/var/lib/rustusb/agent']
                    require(row['kind']=='directory' and row['captured_file'] is None and row['uid']==row['gid']==(2000 if broker else 0) and (mode==(0o711 if path=='/run/agent' else 0o700) if broker else mode&0o022==0),f'{role} actual protected ancestor {path}')
                elif path==private:
                    require(row['kind']=='private_key_metadata_only' and row['uid']==row['gid']==2000 and mode==0o600 and row['links']==1 and row['captured_file'] is None and row['bytes']>0,f'{role} own key metadata only')
                else:
                    capture=prefix+path.rsplit('/',1)[1]
                    require(row['kind']=='public_file' and row['uid']==row['gid']==0 and mode==0o644 and row['links']==1 and row['captured_file']==capture and row['bytes']==len(get(role,capture)),f'{role} actual root-controlled public file {path}')
            require(not any(p.name.endswith('-key.der') for p in (evidence/f'{role}-artifacts').iterdir()),f'{role} no key bytes in artifacts')
            observations.append({'role':role,'epoch':epoch,'fixed_metadata_entries':len(metadata),'json_files':sorted(expected),'public_der_sha256':{name:hashlib.sha256(der).hexdigest() for name,der in public.items()},'private_key_bytes_captured':False})
    return observations

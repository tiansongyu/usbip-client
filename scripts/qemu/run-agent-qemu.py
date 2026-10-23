#!/usr/bin/env python3
"""Two disposable guests: real nonroot agent ownership, Native TLS USB and recovery.

This first slice supports Native only. It uses QEMU keyboard/FTDI input, never a
production agent input RPC. Virtual and USBFS matrices remain separate followups.
"""
import argparse
import datetime
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import shutil
import socket
import struct
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("agent_secure_support", REPO / "scripts/qemu/run-secure-qemu.py")
secure = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secure)
physical = secure.physical
digest, git, command = secure.digest, secure.git, secure.command
credentials, connect_unix, artifacts = secure.credentials, secure.connect_unix, secure.artifacts


def inputs():
    result = secure.inputs()
    for name in ["run-agent-qemu.py", "guest-agent-init.sh"]:
        path = REPO / "scripts/qemu" / name
        result[str(path.relative_to(REPO))] = {"sha256": digest(path), "bytes": path.stat().st_size}
    return dict(sorted(result.items()))


def unique_before(text, anchor):
    if text.count(anchor) != 1:
        raise RuntimeError(f"shared guest composition anchor changed: {anchor!r}")
    return text.split(anchor, 1)[0]


def guest_source():
    """Reuse only boot/network/diagnostics and read-only sysfs capture helpers."""
    common = (REPO / "scripts/qemu/guest-secure-init.sh").read_text()
    prefix = unique_before(common, "native_server_journal() {\n")
    snapshot = common.split("native_snapshot() {\n", 1)
    if len(snapshot) != 2 or common.count("native_snapshot() {\n") != 1:
        raise RuntimeError("shared Native snapshot anchor changed")
    snapshot = "native_snapshot() {\n" + unique_before(snapshot[1], "native_empty() {\n")
    prefix = prefix.replace("RUSTUSB_SECURE_RESULT", "RUSTUSB_AGENT_RESULT")
    prefix = prefix.replace("rustusb.secure=1", "rustusb.agent=1")
    prefix = prefix.replace("    for attempt in $(seq 1 100);", "    # shellcheck disable=SC2034\n    for attempt in $(seq 1 100);")
    anchor = "        failure_journal native-channels /var/lib/rustusb/native/channels.json\n"
    if prefix.count(anchor) != 1: raise RuntimeError("failure journal anchor changed")
    prefix = prefix.replace(anchor, anchor + "        failure_journal agent-state /var/lib/rustusb/agent/state.json\n")
    guard = '\ncase " $(cat /proc/cmdline) " in *\' rustusb.agent=1 \'*) ;; *) exit 2 ;; esac\nAGENT_COMMON=1\n'
    return prefix + snapshot + guard + (REPO / "scripts/qemu/guest-agent-init.sh").read_text()


def payload(work, kernel, certificates, role):
    _, provenance = secure.payload(work, kernel, certificates, role)
    root = work / "root"
    # This public fixture is created by the shared builder using host umask.
    # Normalize its mode explicitly before packaging; its bytes are unchanged.
    (root / "etc/rustusb/client-pin.txt").chmod(0o644)
    (root / "init").write_text(guest_source())
    (root / "init").chmod(0o755)
    # Only this immutable complete generated init executes as PID1.
    shutil.copyfile(root / "init", work / "generated-agent-init.sh")
    subprocess.run(["dash", "-n", str(root / "init")], check=True)
    provenance["init"] = digest(root / "init")
    names = b".\0" + b"\0".join(str(path.relative_to(root)).encode() for path in root.rglob("*")) + b"\0"
    packed = subprocess.run(["cpio", "--null", "-o", "--format=newc", "--owner=0:0"], input=names, cwd=root, capture_output=True, check=True)
    archive = work / "agent-initramfs.cpio.gz"
    archive.write_bytes(gzip.compress(packed.stdout))
    return archive, provenance


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
            expected['server.json']={'schema_version':1,'broker_uid':2000,'listen':'192.0.2.1:3241' if family=='ipv4' else '[fd42:7275:7374::1]:3241','devices':keys,'principals':[{'id':'qemu-client','pins':[pin],'devices':keys}],'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/server.der'],'private_key':'/etc/rustusb/server-key.der'}
        else:
            for profile,key in zip(['hid','serial'],keys,strict=True):
                expected[f'client-{profile}.json']={'schema_version':1,'broker_uid':2000,'server_id':'qemu-server','endpoint':{'host':'192.0.2.1' if family=='ipv4' else 'fd42:7275:7374::1','port':3241},'server_name':'rustusb-qemu.test','device':key,'ca_certificates':['/etc/rustusb/ca.der'],'certificate_chain':['/etc/rustusb/client.der'],'private_key':'/etc/rustusb/client-key.der'}
        directories={'/','/etc','/etc/rustusb','/run','/run/agent','/var','/var/lib','/var/lib/rustusb','/var/lib/rustusb/agent'}
        public_paths={'/etc/rustusb/'+name for name in expected}|{'/etc/rustusb/'+name+'.der' for name in public}|{'/etc/rustusb/client-pin.txt'}
        private='/etc/rustusb/'+role+'-key.der'
        for epoch in [1,2]:
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


def history_identity(final, starts, held_snapshots, role):
    """Require every actually observed job/session in this bounded two-epoch run."""
    expected_jobs = 2 if role == "server" else 4
    expected_sessions = 8 if role == "server" else 4
    expected_roles = {"export": 2, "incoming": 2} if role == "server" else {"import": 1}
    if len(final["jobs"]) != expected_jobs or len(final["sessions"]) != expected_sessions or len(starts) != expected_jobs:
        raise RuntimeError("agent final history cardinality differs")
    jobs = {job["id"]: job for job in final["jobs"]}
    sessions = {session["id"]: session for session in final["sessions"]}
    if len(jobs) != expected_jobs or len(sessions) != expected_sessions:
        raise RuntimeError("duplicate final job/session identity")
    expected = {}
    for start, held in zip(starts, held_snapshots, strict=True):
        job = start["response"]["job"]
        if job["id"] not in jobs or any(jobs[job["id"]][key] != job[key] for key in ["instance_id","profile","operator_uid"]):
            raise RuntimeError("final job differs from actual start receipt")
        observed = [session for session in held["sessions"] if session["job_id"] == job["id"]]
        if len(observed) != sum(expected_roles.values()) or any(sum(s["role"] == kind for s in observed) != count for kind, count in expected_roles.items()):
            raise RuntimeError("actual held job lacks complete role observations")
        for old in observed:
            if old["instance_id"] != job["instance_id"] or old["profile"] != job["profile"] or old["id"] in expected:
                raise RuntimeError("held session/job/instance mapping differs")
            expected[old["id"]] = old
            now = sessions.get(old["id"])
            if now is None or any(now[key] != old[key] for key in ["instance_id","job_id","profile","role","device_key","principal"]):
                raise RuntimeError("final session dropped or rewrote actual held identity")
            kind = {"export":"native_export","incoming":"native_stub","import":"native_import"}[old["role"]]
            if len(old["attempts"]) != 1 or len(now["attempts"]) != 1 or old["attempts"][0]["kind"] != kind or any(now["attempts"][0][key] != old["attempts"][0][key] for key in ["kind","correlation","resource_id"]):
                raise RuntimeError("final attempt differs from actual held capability")
    if set(expected) != set(sessions) or set(jobs) != {s["response"]["job"]["id"] for s in starts}:
        raise RuntimeError("final history does not exactly cover observed epochs")
    return expected_sessions


def verify(evidence, args):
    """Independent agent facts: raw application/root journals, kernel rows and bytes."""
    checks = []
    def require(condition, name):
        if not condition: raise RuntimeError(f"agent verification: {name}")
        checks.append(name)
    def data(role, name):
        return json.loads((evidence / f"{role}-artifacts" / name).read_text())
    def raw(role, name):
        return (evidence / f"{role}-artifacts" / name).read_bytes()
    def vhci(name, used):
        rows = [line.split() for line in raw("client", name).decode().splitlines() if line.startswith(("hs ", "ss "))]
        require(bool(rows) and sum(int(r[2]) == 6 for r in rows) == used and all(int(r[2]) in [4, 6] for r in rows), f"VHCI exact USED/NULL {name}")
    def terminals(root, state, kinds=None):
        count = 0
        for session in state["sessions"]:
            for attempt in session["attempts"]:
                if kinds and attempt["kind"] not in kinds: continue
                matches = [t["terminal"] for t in root["terminals"] if t["terminal"]["kind"] == attempt["kind"] and t["terminal"]["correlation"] == attempt["correlation"]]
                require(len(matches) == 1, f"exact retained root terminal {session['id']}/{attempt['kind']}")
                terminal = matches[0]
                require(terminal["owner_uid"] == 2000 and terminal["device_key"] == session["device_key"] and terminal["resource_id"] == attempt["resource_id"], f"root terminal identity {session['id']}")
                expected_proof={"native_import":"native_import_released","native_export":"native_export_restored","native_stub":"native_stub_available"}[attempt["kind"]]
                require(terminal["proof"]==expected_proof,f"same-boot no-hotplug exact release criterion {session['id']}/{attempt['kind']}")
                if attempt["proof"] is not None: require(attempt["proof"] == terminal["proof"], f"root proof echo {session['id']}")
                count += 1
        return count
    def private_link_history(state, label):
        require(set(state) == {"schema_version", "sequence", "jobs", "sessions", "audit", "requests", "link_history"}
                and state["schema_version"] == 2, f"{label} strict private disk2 shape")
        sessions = {item["id"]: item for item in state["sessions"]}
        history = {item["session_id"]: item for item in state["link_history"]}
        require(len(sessions) == len(state["sessions"]) == len(history) == len(state["link_history"])
                and set(sessions) == set(history), f"{label} exact one-to-one retained link identities")
        raw_v1 = {"protocol": "raw_v1", "identity": None, "sample_sequence": 0,
                  "elapsed_millis": 0, "phase": "ended", "probe": "unavailable_raw_v1",
                  "last_probe_sequence": None, "last_probe_enqueued_millis": None,
                  "last_reply_sequence": None, "last_reply_millis": None,
                  "last_round_trip_micros": None, "stats": None, "end": None}
        for session_id, item in history.items():
            session = sessions[session_id]
            require(set(item) == {"session_id", "instance_id", "link"}
                    and item["instance_id"] == session["instance_id"], f"{label} link epoch {session_id}")
            if session["role"] == "export":
                require(item["link"] is None, f"{label} export is not a data link {session_id}")
            else:
                require(item["link"] is None or item["link"] == raw_v1,
                        f"{label} raw v1 never fabricates RTT or probe {session_id}")
                if session["state"] == "Connected":
                    require(item["link"] == raw_v1, f"{label} observed raw data mode {session_id}")

    for role in ["server", "client"]:
        log = (evidence / f"{role}-evidence.log").read_text()
        kernel_log=(evidence / f"{role}-serial.log").read_text(errors="replace")
        require(all(mark not in kernel_log for mark in ["Kernel panic","Oops:"]),f"{role} final complete S0 no kernel panic/Oops")
        require(log.count(f"RUSTUSB_AGENT_RESULT: PASS role={role}") == 1 and all(mark not in log for mark in ["RUSTUSB_AGENT_RESULT: FAIL","SECURE_ARTIFACT_ERROR","SECURE_ARTIFACT_OVERSIZE","SECURE_POWEROFF_RETURNED"]), f"{role} terminal and complete artifacts")
        require(f"SECURE_CREDENTIAL_ISOLATION role={role} own_key=true opposite_key=false ca_key=false" in log and "SECURE_RNG_RTC_READY crng=true " in log,f"{role} role-isolated credentials and real entropy/clock")
        manifest = data(role, "agent-manifest.json")
        require(manifest["broker_uid"] == 2000 and manifest["operators"] == [{"uid":1000,"profiles":[p["id"] for p in manifest["profiles"]],"actions":["read","start","stop","discover","audit"]}], f"{role} fixed operator and broker")
        require(data(role,"helper-policy.json")["trusted_network"] is False, f"{role} no legacy trusted-network claim")
        helper_identity=raw(role,"identity-helper.txt").decode()
        require(re.search(r"^Uid:\s+0\s+0\s+0\s+0$",helper_identity,re.M) is not None,f"{role} actual privileged helper OS identity")
        after = data(role,"agent-after-denied.json")
        private_link_history(after, f"{role} denied")
        require(raw(role,"agent-before-denied.status").strip()==b"absent" and data(role,"jobs-before-denied.json")["response"]=={"result":"jobs","jobs":[],"next_after":None} and data(role,"sessions-before-denied.json")["response"]=={"result":"sessions","sessions":[],"next_after":None},f"{role} actual new-store absence and public empty resources")
        require(all(after[k] == [] for k in ["jobs","sessions","requests"]) and raw(role,"root-before-denied.json") == raw(role,"root-after-denied.json"), f"{role} denied resource invariance")
        for reason in ["uid", "profile"]:
            require(data(role,f"denied-{reason}.json")["response"] == {"result":"error","code":"permission"},f"{role} typed denial {reason}")
        for epoch in [1,2]:
            identity = raw(role,f"agent-identity-{epoch}.txt").decode()
            require("control=2000:711" in identity and "data=2000:700" in identity and "socket=2000:666" in identity,f"{role} actual ownership modes epoch {epoch}")
            status = raw(role,f"identity-agent-{epoch}.txt").decode()
            require(re.search(r"^Uid:\s+2000\s+2000\s+2000\s+2000$",status,re.M) is not None,f"{role} actual nonroot process epoch {epoch}")
            require(all(re.search(rf"^{cap}:\s+0000000000000000$",status,re.M) for cap in ["CapInh","CapPrm","CapEff","CapAmb"]),f"{role} no ambient/effective capability epoch {epoch}")
        final = data(role,"agent-stopped-2.json" if role == "server" else "agent-final.json")
        profiles=["server"] if role=="server" else ["hid","serial"]
        starts=[data(role,f"start-{profile}-{epoch}.json") for epoch in [1,2] for profile in profiles]
        held=[data(role,f"agent-held-{1 if epoch==1 else 3}.json") for epoch in [1,2] for _ in profiles]
        private_link_history(final, f"{role} final")
        for number, snapshot in enumerate(held):
            private_link_history(snapshot, f"{role} held{number}")
        expected_count=history_identity(final,starts,held,role)
        require(expected_count==(8 if role=="server" else 4),f"{role} exact two-epoch history from original start/held identities")
        require(all(s["terminal"] and s["cleanup"] == "verified" and s["state"] == "Disconnected" for s in final["sessions"]),f"{role} actual final session cleanup")
        require(all(j["state"] == "finished" and j["operator_uid"] == 1000 for j in final["jobs"]),f"{role} jobs really finished")
        require(len({j["instance_id"] for j in final["jobs"]}) == 2,f"{role} two distinct service epochs")
        root = data(role,"root-stopped-2.json" if role == "server" else "root-final.json")
        require(root["schema_version"] == 2 and root["records"] == [] and root["exports"] == [],f"{role} root durable active empty")
        require(len(root["terminals"])==expected_count and terminals(root,final)==expected_count,f"{role} every actual attempt has exactly one retained proof")
    keys = data("client","keys.json")
    require(keys == data("server","keys.json") and len(set(keys)) == 2 and all(k.startswith("native:") for k in keys),"exact Native device keys")
    expected_tx = b""
    for round_id in [1,2,3]:
        rx = f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
        tx = f"RUSTUSB-TX-{round_id}-0001:fedcba9876543210\n".encode()
        require(raw("client",f"rx-{round_id}.bin") == rx and raw("client",f"tx-{round_id}.bin") == tx and (evidence / f"ftdi-host-injected-{round_id}.bin").read_bytes() == rx,f"actual FTDI exact 35B each way round {round_id}")
        expected_tx += tx
    require((evidence / "ftdi-host-rx.bin").read_bytes() == expected_tx,"actual FTDI host received all 105B")
    for round_id in [1,3]:
        events = raw("client",f"input-{round_id}.bin")
        require(len(events) % 24 == 0 and len(events)>0,f"complete x86_64 evdev records round {round_id}")
        values = [struct.unpack("<qqHHi",events[o:o+24])[2:] for o in range(0,len(events),24)]
        require((1,round_id+1,1) in values and (1,round_id+1,0) in values,f"real HID down/up round {round_id}")
        receipts=data("client",f"client-receipts-{round_id}.json")["records"]
        require(len(receipts)==2 and all(r["state"]["phase"]=="observed" and r["correlation"] is not None for r in receipts),f"actual complete Native receipts round {round_id}")
        for r in receipts:
            receipt=r["state"]["receipt"]
            require(receipt["session"]["status"]==6 and receipt["session"]["vendor_id"]==r["target"]["remote"]["vendor_id"] and receipt["session"]["product_id"]==r["target"]["remote"]["product_id"] and receipt["session"]["local_device_generation"] is not None and receipt["profile"] is not None,f"local kernel identity {r['channel_id']}")
            epoch=1 if round_id==1 else 2
            pid=int(re.search(r"^pid=(\d+)$",raw("client",f"agent-identity-{epoch}.txt").decode(),re.M)[1])
            require(r["owner"]["uid"]==2000 and r["owner"]["pid"]==pid and r["owner"]["starttime"]>0 and receipt["kernel_socket_inode"]>0,f"actual helper owner and retained K {r['channel_id']}")
            app=data("client",f"agent-held-{round_id}.json")
            attempt=[(s,a) for s in app["sessions"] for a in s["attempts"] if a["resource_id"]==r["channel_id"]]
            require(len(attempt)==1 and attempt[0][1]["correlation"]==r["correlation"] and attempt[0][0]["device_key"]==r["target"]["device_key"],f"live app/helper exact capability {r['channel_id']}")
    vhci("vhci-held-1.txt",2); vhci("vhci-target-stopped.txt",1); vhci("vhci-after-kill.txt",0); vhci("vhci-held-3.txt",2); vhci("vhci-final.txt",0)
    before = data("client","agent-before-kill.json")
    after = data("client","agent-after-kill.json")
    old_serial_before=next(s for s in before["sessions"] if s["profile"]=="serial")
    old_serial_after=next(s for s in after["sessions"] if s["profile"]=="serial")
    require(all(old_serial_before[k] == old_serial_after[k] for k in ["id","instance_id","job_id","device_key","attempts","terminal","cleanup","state"]),"SIGKILL retained old active application lifecycle (metrics may advance before signal)")
    require(raw("client","wait-agent-kill.txt").strip()==b"137","actual single agent SIGKILL reap137")
    recovered=data("client","client-serial-recovered.json")["response"]["sessions"]
    require(len(recovered)==1 and recovered[0]["evidence"]=="root_journal_proof" and recovered[0]["cleanup"]=="verified","old serial recovered only from exact Root proof")
    old=next(s for s in before["sessions"] if s["profile"]=="serial")
    require(old["state"]=="Connected" and old["terminal"] is False and old["cleanup"] is None,"actual live old serial before SIGKILL")
    require(all(recovered[0][k]==old[k] for k in ["id","instance_id","job_id","device_key"]),"recovery preserved old application identity")
    terminals(data("client","root-after-kill.json"),{"sessions":recovered})
    final=data("client","agent-final.json")
    require(len(final["sessions"])==4 and len({a["correlation"]["attempt_id"] for s in final["sessions"] for a in s["attempts"]})==4,"new attempts after restart are distinct")
    old_receipts=data("client","client-receipts-1.json")["records"]
    new_receipts=data("client","client-receipts-3.json")["records"]
    require({r["channel_id"] for r in old_receipts}.isdisjoint(r["channel_id"] for r in new_receipts),"helper capabilities never reused across epochs")
    baseline=data("server","host-before.json")
    dynamic={"configuration","num_interfaces","configuration_bytes","count_bytes","interfaces","device_driver","usbip_status"}
    hardware=lambda rows:[{k:v for k,v in row.items() if k not in dynamic} for row in rows]
    for epoch in [1,2]:
        initial=data("server",f"root-exported-{epoch}.json")
        require(initial["schema_version"]==2 and initial["records"]==[] and len(initial["exports"])==2,f"tracked export records epoch {epoch}")
        require({r["registration"]["device_key"] for r in initial["exports"]}==set(keys),f"exact export registration keys epoch {epoch}")
        for r in initial["exports"]:
            pid=int(re.search(r"^pid=(\d+)$",raw("server",f"agent-identity-{epoch}.txt").decode(),re.M)[1])
            require(r["owner"]["uid"]==2000 and r["owner"]["pid"]==pid and r["correlation"] is not None and r["stub"] is None and r["bound"]["generation"]==r["registration"]["generation"] and r["bound"]["bound_profile"]=={"configuration":None,"interfaces":[]},f"original tracked export owner/bound {r['lease_id']}")
            require(r["registration"]["original_device_driver"]=="usb" and r["registration"]["published_device"]["configuration"]==1 and len(r["registration"]["published_device"]["interfaces"])==1,f"published pre-export descriptor {r['lease_id']}")
        round_id=1 if epoch==1 else 3
        held=data("server",f"root-held-{round_id}.json")
        clean=lambda rows:sorted(({k:v for k,v in row.items() if k!="stub"} for row in rows),key=lambda r:r["lease_id"])
        require(clean(initial["exports"])==clean(held["exports"]),f"immutable live export across stub use epoch {epoch}")
        require(all(r["stub"] is not None and r["stub"]["correlation"] is not None and r["stub"]["state"]["phase"]=="observed" and r["stub"]["state"]["receipt"]["usbip_status"]==2 and r["stub"]["state"]["receipt"]["export"]==r["bound"] for r in held["exports"]),f"actual Native stub observed USED epoch {epoch}")
        for mark,occupied in [(f"exported-{epoch}",1),(f"held-{round_id}",2)]:
            host=data("server",f"host-{mark}.json")
            require(hardware(host)==hardware(baseline),f"server physical generation unchanged {mark}")
            require(all(r["device_driver"]=="usbip-host" and r["usbip_status"]==occupied for r in host),f"actual Native occupancy {mark}")
            if occupied==1:
                require(all(r["configuration"]=="" and r["num_interfaces"]=="" and r["configuration_bytes"]==0 and r["count_bytes"]==0 and r["interfaces"]==[] for r in host),f"true export unconfigured lifecycle {mark}")
            else:
                require(all(int(r["configuration"])==1 and int(r["num_interfaces"])==1 and r["interfaces"]==[dict(i,driver="") for i in baseline[n]["interfaces"]] for n,r in enumerate(host)),f"actual imported full IF profile {mark}")
        require(data("server",f"host-restored-{epoch}.json")==baseline,f"complete original driver/configuration/IF generation restored epoch {epoch}")
        require(raw("server",f"wait-agent-{epoch}.txt").strip()==b"0",f"server actual TERM wait0 epoch {epoch}")
    require(raw("client","wait-agent-2.txt").strip()==b"0","client final actual TERM wait0")
    targeted=data("server","server-target-stopped.json")["response"]["sessions"]
    incoming=[s for s in targeted if s["role"]=="incoming"]
    require(len(incoming)==2 and next(s for s in incoming if s["device_key"]==keys[0])["cleanup"]=="verified" and next(s for s in incoming if s["device_key"]==keys[1])["state"]=="Connected","targeted HID stop preserves serial incoming")
    require(all(not s["terminal"] and s["state"]=="Available" for s in targeted if s["role"]=="export"),"incoming stop preserves live published exports")
    held_root=data("server","root-held-1.json")
    target_root=data("server","root-target-stopped.json")
    require(clean(held_root["exports"])==clean(target_root["exports"]),"targeted stop does not replace original export registration")
    held_by={r["registration"]["device_key"]:r for r in held_root["exports"]}
    target_by={r["registration"]["device_key"]:r for r in target_root["exports"]}
    require(target_by[keys[0]]["stub"] is None and target_by[keys[1]]["stub"]==held_by[keys[1]]["stub"],"only requested HID stub retires; serial capability stays exact")
    target_client=data("client","root-target-stopped.json")["records"]
    serial_before=next(r for r in old_receipts if r["target"]["device_key"]==keys[1])
    require(target_client==[serial_before],"targeted stop retains same serial client receipt and owner")
    for role in ["server","client"]:
        final_state=data(role,"agent-stopped-2.json" if role=="server" else "agent-final.json")
        forwarded=[s for s in final_state["sessions"] if s["role"] in ["import","incoming"]]
        require(all(s["metrics"]["app_rx_bytes"]>0 and s["metrics"]["app_tx_bytes"]>0 and not s["metrics"]["saturated"] for s in forwarded),f"{role} actual application byte metrics retained, not USB payload counts")
    catalog=data("client","catalog-initial.json")["response"]["devices"]
    device_ids={(entry["device"]["bus_number"] << 16) | entry["device"]["device_number"] for entry in catalog}
    external=secure.pcap_review(evidence / "server-external.pcap",args.family,device_ids)
    configuration=verify_collected_configuration(evidence,args.family)
    return {"actual_configuration":configuration,"scope":"Native-only actual agent ownership/recovery; assertions are audit checks, not USB cases","checks":checks,"assertion_count":len(checks),"io_rounds":3,"hid_key_codes":[2,4],"serial_each_direction_bytes":105,"external_link":external}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", default=os.uname().release)
    parser.add_argument("--backend", choices=["native"], default="native")
    parser.add_argument("--family", choices=["ipv4", "ipv6"], required=True)
    parser.add_argument("--timeout", type=int, default=420)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--skip-build", action="store_true", help="development only; final evidence uses the locked all-features build")
    args = parser.parse_args()
    if os.geteuid() == 0 or os.uname().machine != "x86_64": raise RuntimeError("ordinary x86_64 host user required")
    if not 60 <= args.timeout <= 900: raise RuntimeError("timeout must be 60..900 seconds")
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    summary = {"schema_version": 1, "result": "FAIL", "backend": args.backend, "family": args.family,
               "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "head": git("rev-parse", "HEAD"),
               "git_status_before": git("status", "--porcelain"), "source_inputs": inputs(),
               "scope": "two disposable QEMU guests, actual UID2000 application agent plus UID1000 operators, Native HID/FTDI only; no hardware, virtual-input or full Phase3 claim",
               "build_features": "--workspace --all-features --release --locked", "build_skipped": args.skip_build}
    processes, handles, connections = {}, [], {}
    qmp = ftdi = None
    sent = set()
    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    try:
        if summary["git_status_before"]: raise RuntimeError("clean committed source required")
        for name in summary["source_inputs"]:
            destination = evidence / "source" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, destination)
        if not args.skip_build:
            command(["cargo", "build", "--workspace", "--all-features", "--release", "--locked"], evidence / "build.log")
        if inputs() != summary["source_inputs"]: raise RuntimeError("build changed frozen inputs")
        summary["binary_sha256"] = {name: digest(REPO / "target/release" / name) for name in ["rustusb", "rustusb-helper"]}
        command(["qemu-system-x86_64", "--version"], evidence / "tool-versions.log")
        command(["rustc", "--version", "--verbose"], evidence / "tool-versions.log")
        with tempfile.TemporaryDirectory(prefix="rustusb-agent-") as temporary:
            work = Path(temporary)
            certs = credentials(work, evidence)
            archives = {}
            summary["payload_sha256"] = {}
            summary["initramfs_sha256"] = {}
            summary["credential_payloads"] = {}
            for role in ["server", "client"]:
                role_work = work / f"{role}-payload"
                role_work.mkdir()
                archives[role], summary["payload_sha256"][role] = payload(role_work, args.kernel, certs, role)
                shutil.copyfile(role_work / "generated-agent-init.sh", evidence / f"{role}-generated-agent-init.sh")
                shutil.copyfile(role_work / "payload-dependencies.json", evidence / f"{role}-payload-dependencies.json")
                summary["initramfs_sha256"][role] = digest(archives[role])
                summary["credential_payloads"][role] = sorted(path.name for path in (role_work / "root/etc/rustusb").iterdir())
                for name, sha in summary["binary_sha256"].items():
                    if summary["payload_sha256"][role][f"usr/bin/{name}"] != sha:
                        raise RuntimeError("ELF changed during role-specific payload copy")
            image = work / "vmlinuz"
            try: shutil.copyfile(Path("/boot") / f"vmlinuz-{args.kernel}", image)
            except PermissionError:
                subprocess.run(["sudo", "-n", "cp", f"/boot/vmlinuz-{args.kernel}", str(image)], check=True)
                subprocess.run(["sudo", "-n", "chmod", "644", str(image)], check=True)
            summary["kernel_image_sha256"] = digest(image)
            kernel_config = Path("/boot") / f"config-{args.kernel}"
            shutil.copyfile(kernel_config, evidence / "kernel.config")
            summary["kernel_config_sha256"] = digest(kernel_config)
            summary["kernel"] = args.kernel
            links = socket.socketpair()
            acceleration = ["-enable-kvm", "-cpu", "host"] if os.access("/dev/kvm", os.R_OK | os.W_OK) else ["-cpu", "max"]
            deadline = time.monotonic() + args.timeout
            for index, role in enumerate(["server", "client"]):
                for channel in ["serial", "evidence"]: (evidence / f"{role}-{channel}.log").touch()
                output = (evidence / f"{role}-qemu.stderr").open("wb")
                handles.append(output)
                cmd = ["qemu-system-x86_64", "-m", "768", "-smp", "2", *acceleration, "-rtc", "base=utc,clock=host", "-kernel", str(image), "-initrd", str(archives[role]),
                       "-append", f"console=ttyS0 panic=-1 RUSTUSB_QEMU_GUEST=1 rustusb.secure=1 rustusb.agent=1 rustusb.role={role} rustusb.backend={args.backend} rustusb.family={args.family}",
                       "-display", "none", "-serial", f"file:{evidence / (role+'-serial.log')}", "-serial", f"file:{evidence / (role+'-evidence.log')}",
                       "-chardev", f"socket,id=control,path={work / (role+'-control.sock')},server=on,wait=off", "-serial", "chardev:control",
                       "-monitor", "none", "-no-reboot", "-nic", "none", "-netdev", f"socket,id=external,fd={links[index].fileno()}",
                       "-device", f"e1000,netdev=external,mac=52:54:00:00:00:{61+index}",
                       "-object", f"filter-dump,id=capture,netdev=external,file={evidence / (role+'-external.pcap')}"]
                if role == "server" and args.backend != "virtual":
                    cmd += ["-device", "qemu-xhci,id=usb", "-device", "usb-kbd,id=secure-kbd,bus=usb.0,port=1",
                            "-chardev", f"socket,id=ftdi,path={work / 'ftdi.sock'},server=on,wait=off",
                            "-device", "usb-serial,id=secure-ftdi,bus=usb.0,port=2,chardev=ftdi,always-plugged=on",
                            "-qmp", f"unix:{work / 'qmp.sock'},server=on,wait=off"]
                summary.setdefault("qemu_commands", {})[role] = cmd
                processes[role] = subprocess.Popen(cmd, pass_fds=[links[index].fileno()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=output)
            for link in links: link.close()
            for role in processes: connections[role] = connect_unix(work / f"{role}-control.sock", processes[role], deadline)
            if args.backend != "virtual":
                qmp = physical.Qmp(work / "qmp.sock", deadline, processes["server"])
                ftdi = connect_unix(work / "ftdi.sock", processes["server"], deadline)
                ftdi.setblocking(False)
            tx = bytearray()
            control_log = evidence / "host-control.jsonl"

            def send_once(mark, role, message):
                if mark in sent: return
                connections[role].sendall(message.encode() + b"\n")
                with control_log.open("a") as log:
                    log.write(json.dumps({"monotonic": time.monotonic(), "mark": mark, "role": role, "command": message}) + "\n")
                sent.add(mark)

            while time.monotonic() < deadline:
                logs = {role: (evidence / f"{role}-evidence.log").read_text(errors="replace") for role in processes}
                markers = [{"role": role, "line": line} for role, log in logs.items() for line in log.splitlines() if "RUSTUSB_AGENT_RESULT: FAIL" in line]
                if markers:
                    summary.setdefault("observed_failure_markers", markers)
                    (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
                    raise RuntimeError("guest reported actual failure")
                for role in processes:
                    kernel = (evidence / f"{role}-serial.log").read_text(errors="replace")
                    if "Kernel panic" in kernel or "Oops:" in kernel: raise RuntimeError(f"{role} kernel fault")
                keys = re.search(r"^AGENT_KEYS (\[.*\])$", logs["server"], re.M)
                if keys: send_once("keys", "client", keys[1])
                if "AGENT_SERVER_READY epoch=1\n" in logs["server"] and "keys" in sent:
                    send_once("start", "client", "START")
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                    (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                    if len(tx) > 105: raise RuntimeError("unexpected extra FTDI output")
                for round_id in [1, 2, 3]:
                    if f"AGENT_CLIENT_WAIT_INPUT round={round_id} " in logs["client"]:
                        if round_id in [1, 3]:
                            send_once(f"held-{round_id}", "server", f"HELD{round_id}")
                        ready = round_id == 2 or f"AGENT_SERVER_HELD{round_id}\n" in logs["server"]
                        if ready and f"usb-input-{round_id}" not in sent:
                            if round_id in [1, 3]:
                                arguments = {"keys": [{"type": "qcode", "data": str(round_id)}], "hold-time": 100}
                                response = qmp.call("send-key", arguments)
                                with (evidence / "qmp-input.jsonl").open("a") as log:
                                    log.write(json.dumps({"round": round_id, "command": "send-key", "arguments": arguments, "return": response}) + "\n")
                            fixture = f"RUSTUSB-RX-{round_id}-0001:0123456789abcdef\n".encode()
                            ftdi.settimeout(2); ftdi.sendall(fixture); ftdi.setblocking(False)
                            (evidence / f"ftdi-host-injected-{round_id}.bin").write_bytes(fixture)
                            sent.add(f"usb-input-{round_id}")
                if "AGENT_CLIENT_IO round=1 " in logs["client"] and len(tx) >= 35:
                    send_once("stop-hid", "server", "STOP_HID")
                if "AGENT_SERVER_HID_STOPPED\n" in logs["server"]:
                    send_once("hid-stopped", "client", "HID_STOPPED")
                if "AGENT_CLIENT_IO round=2 " in logs["client"] and len(tx) >= 70:
                    send_once("kill-restart", "client", "KILL_RESTART")
                if "AGENT_CLIENT_RECOVERED\n" in logs["client"]:
                    send_once("client-recovered", "server", "CLIENT_RECOVERED")
                if "AGENT_SERVER_RESTARTED\n" in logs["server"]:
                    send_once("restart-server", "server", "RESTART2")
                if "AGENT_SERVER_READY epoch=2\n" in logs["server"]:
                    send_once("restart-client", "client", "START2")
                if "AGENT_CLIENT_IO round=3 " in logs["client"] and len(tx) >= 105:
                    send_once("close-client", "client", "CLOSE")
                if "AGENT_CLIENT_CLOSED\n" in logs["client"]:
                    send_once("closed-client", "server", "CLIENT_CLOSED")
                if "AGENT_SERVER_FINISHED\n" in logs["server"]:
                    send_once("finish", "client", "FINISH")
                if all(process.poll() is not None for process in processes.values()): break
                if any(process.poll() is not None and f"RUSTUSB_AGENT_RESULT: PASS role={role}" not in logs[role] for role, process in processes.items()):
                    raise RuntimeError("guest exited without terminal PASS")
                time.sleep(0.05)
            else: raise RuntimeError("two-guest agent run deadline exceeded")
            if any(process.wait() != 0 for process in processes.values()): raise RuntimeError("nonzero actual QEMU exit")
            if ftdi:
                while select.select([ftdi], [], [], 0)[0]:
                    chunk = ftdi.recv(4096)
                    if not chunk: break
                    tx.extend(chunk)
                (evidence / "ftdi-host-rx.bin").write_bytes(tx)
                (evidence / "qmp-events.json").write_text(json.dumps(qmp.events, indent=2) + "\n")
            summary["guest_artifacts"] = artifacts(evidence)
            execution_hashes = []
            helper_hashes = []
            for role in ["server", "client"]:
                for path in (evidence / f"{role}-artifacts").glob("executable-*.txt"):
                    actual = path.read_text().split()[0]
                    helper = path.name == "executable-helper.txt"
                    program = "rustusb-helper" if helper else "rustusb"
                    if actual != summary["binary_sha256"][program]:
                        raise RuntimeError("actual guest broker executable differs from frozen ELF")
                    (helper_hashes if helper else execution_hashes).append(str(path.relative_to(evidence)))
            if len(execution_hashes) != 4:
                raise RuntimeError("missing actual broker executable/identity observations")
            if len(helper_hashes) != 2:
                raise RuntimeError("missing actual privileged helper executable observations")
            summary["actual_broker_executable_receipts"] = execution_hashes
            summary["actual_helper_executable_receipts"] = helper_hashes
            summary["facts"] = verify(evidence, args)
            summary["result"] = "PASS"
    except Exception as error:
        summary["error"] = f"{type(error).__name__}: {error}"
    finally:
        # Preserve the first failure and give PID1's failure trap time to finish
        # its raw artifact dump. ABORT is deliberately never an accepted command.
        # Killing QEMU at the first FAIL marker would discard the following dump.
        if summary["result"] != "PASS" and connections:
            for role, connection in connections.items():
                if processes[role].poll() is None:
                    try: connection.sendall(b"ABORT\n")
                    except OSError: pass
            capture_deadline = time.monotonic() + 25
            while any(process.poll() is None for process in processes.values()) and time.monotonic() < capture_deadline:
                time.sleep(0.05)
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
        for connection in connections.values(): connection.close()
        if qmp: qmp.close()
        if ftdi: ftdi.close()
        for handle in handles: handle.close()
        summary["guest_exit_codes"] = {role: process.returncode for role, process in processes.items()}
        summary["head_after"] = git("rev-parse", "HEAD")
        summary["git_status_after"] = git("status", "--porcelain")
        summary["source_guard"] = inputs() == summary["source_inputs"]
        summary["binary_guard"] = all(digest(REPO / "target/release" / name) == sha for name, sha in summary.get("binary_sha256", {}).items())
        summary["head_guard"] = summary["head_after"] == summary["head"]
        summary["clean_guard"] = not summary["git_status_before"] and not summary["git_status_after"]
        if not all(summary[key] for key in ["source_guard", "binary_guard", "head_guard", "clean_guard"]): summary["result"] = "FAIL"
        if processes and "guest_artifacts" not in summary:
            try: summary["guest_artifacts"] = artifacts(evidence)
            except Exception as error: summary["artifact_error"] = str(error)
        summary["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        summary["artifact_manifest"] = {str(path.relative_to(evidence)): {"bytes": path.stat().st_size, "sha256": digest(path)}
                                        for path in evidence.rglob("*") if path.is_file() and path.name != "summary.json"}
        (evidence / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({"result": summary["result"], "backend": args.backend, "family": args.family,
                      "evidence": str(evidence), "error": summary.get("error")}))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

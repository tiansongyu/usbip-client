#!/usr/bin/env python3
"""Read source bytes/Git objects only; no product process or tests are executed."""
from pathlib import Path
import hashlib,json,subprocess,datetime
repo=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-diagnostic-export-native-preflight-20261024')
paths=['AGENTS.md','.agents/skills/rustusb-development/SKILL.md','docs/requirements.md','docs/architecture.md','docs/security.md','docs/research/application-service-design.md','docs/research/rotating-logs-design.md','crates/rustusb-cli/Cargo.toml','crates/rustusb-cli/src/main.rs','crates/rustusb-cli/src/logs.rs','crates/rustusb-cli/src/event_output.rs','crates/rustusb-native/Cargo.toml','crates/rustusb-native/src/diagnostics.rs','crates/rustusb-native/src/lib.rs','crates/rustusb-daemon/Cargo.toml','crates/rustusb-daemon/src/diagnostics.rs','crates/rustusb-daemon/src/lib.rs','crates/rustusb-daemon/src/linux.rs','crates/rustusb-core/src/lib.rs','crates/rustusb-core/src/application.rs','crates/rustusb-core/src/link_health.rs','crates/rustusb-linux/src/lib.rs','crates/rustusb-service/Cargo.toml','crates/rustusb-service/src/config.rs','crates/rustusb-service/src/model.rs','crates/rustusb-service/src/rpc.rs','crates/rustusb-transport/Cargo.toml','crates/rustusb-transport/src/config.rs','crates/rustusb-transport/src/config/platform/tests.rs','crates/rustusb-transport/src/lib.rs','crates/rustusb-logging/src/record.rs']
head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
rows=[]
for name in paths:
    data=(repo/name).read_bytes()
    committed=subprocess.check_output(['git','show',head+':'+name],cwd=repo)
    dst=out/'sources'/name
    dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(data)
    rows.append({'path':name,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'git_blob':hashlib.sha1(b'blob '+str(len(committed)).encode()+b'\0'+committed).hexdigest(),'matches_head_git_blob':data==committed,'copy':'sources/'+name})
result={'schema_version':1,'kind':'read_only_source_preflight_not_acceptance','head':head,'captured_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source_count':len(rows),'sources':rows,'worktree_status_at_capture':subprocess.check_output(['git','status','--porcelain'],cwd=repo,text=True),'all_captured_bytes_match_head':all(x['matches_head_git_blob'] for x in rows)}
(out/'sources.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k!='sources'},indent=2))

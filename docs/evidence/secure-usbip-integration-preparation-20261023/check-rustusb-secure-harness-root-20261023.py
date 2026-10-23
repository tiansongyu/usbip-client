from pathlib import Path
import ast,hashlib,json,subprocess
report={'scope':'Root static syntax/source-handoff preflight only; no QEMU or USB execution','checks':[],'files':{}}
handoff=json.loads(Path('/tmp/rustusb-secure-harness-author-handoff-20261023.json').read_text())
for name,expected in handoff['files'].items():
 b=Path(name).read_bytes(); actual={'sha256':hashlib.sha256(b).hexdigest(),'bytes':len(b)};assert actual==expected;report['files'][name]=actual
ast.parse(Path('scripts/qemu/run-secure-qemu.py').read_text());report['checks'].append({'name':'python AST','exit_code':0})
for cmd in [['dash','-n','scripts/qemu/guest-secure-init.sh'],['shellcheck','-s','ash','scripts/qemu/guest-secure-init.sh'],['python3','scripts/qemu/run-secure-qemu.py','--help'],['git','diff','--check']]:
 p=subprocess.run(cmd,capture_output=True,text=True);report['checks'].append({'command':cmd,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr});assert p.returncode==0
Path('/tmp/rustusb-secure-harness-root-preflight-20261023.json').write_text(json.dumps(report,indent=2)+'\n');print(len(report['checks']))

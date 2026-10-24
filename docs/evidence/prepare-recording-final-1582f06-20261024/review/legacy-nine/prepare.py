#!/usr/bin/env python3
"""Freeze parser/provenance only; deliberately does not read runtime artifacts or run audits."""
from pathlib import Path
import ast,difflib,hashlib,json,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client')
OLD=Path('/tmp/rustusb-data-link-final-lifecycle-review-20261024/compat')
OUT=Path(__file__).resolve().parent
BASE=Path('/tmp/rustusb-prepare-stop-final-1582f06-20261024')
HEAD='1582f06776f4c1ea50e9b5109a9ad7d0b7cdbe16'
PREVIOUS='531e0c32a4b0e7252ca771ab4f10042c71c8c195'
SOFTWARE=Path('/tmp/rustusb-prepare-stop-software-ab-first-20261024')
def sha(b):return hashlib.sha256(b).hexdigest()
def dump(n,j):(OUT/n).write_text(json.dumps(j,indent=2,sort_keys=True)+'\n')
start=json.loads((BASE/'start.json').read_bytes());inputs=json.loads((BASE/'inputs-before.json').read_bytes())
assert start['head']==HEAD and start['clean'] is True and start['input_count']==len(inputs)==222
hashes={row['path']:{'bytes':row['bytes'],'sha256':row['sha256']}for row in inputs}
for row in inputs:
 b=subprocess.check_output(['git','cat-file','blob',HEAD+':'+row['path']],cwd=ROOT)
 assert len(b)==row['bytes'] and sha(b)==row['sha256'],row['path']
software=(SOFTWARE/'inputs-before.json').read_bytes();rows=json.loads(software)
assert rows==json.loads((SOFTWARE/'inputs-after.json').read_bytes()) and len(rows)==167
for row in rows:assert hashes[row['path']]=={'bytes':row['bytes'],'sha256':row['sha256']},row['path']
(OUT/'software-inputs.json').write_bytes(software)
(OUT/'software-result.json').write_bytes((SOFTWARE/'result.json').read_bytes())
names=subprocess.check_output(['git','ls-tree','-r','--name-only',HEAD],cwd=ROOT,text=True).splitlines()
scriptnames={'scripts/qemu/'+n for n in ['run-secure-qemu.py','guest-secure-init.sh','run-native-qemu.py','guest-init.sh','run-physical-qemu.py']}
secure=sorted(n for n in names if n in {'Cargo.toml','Cargo.lock'} or ((n.startswith('crates/')or n.startswith('vendor/'))and(n.endswith('.rs')or n.endswith('Cargo.toml')))or n in scriptnames)
agent=sorted(set(secure)|{'scripts/qemu/run-agent-qemu.py','scripts/qemu/guest-agent-init.sh'})
assert len(secure)==169 and len(agent)==171 and all(n in hashes for n in agent)
dump('expected-binaries.json',start['expected_binaries'])
dump('source-lock.json',{'head':HEAD,'secure_inputs':secure,'agent_inputs':agent,'source_hashes':hashes,'software_input_count':167,'software_manifest_git_path':str(OUT/'software-inputs.json'),'software_manifest_sha256':sha(software),'scope':'Actual667 dirtyb1 software inputs mapped individually to1582 Gitblobs. No new clean software test execution. Selector independently reconstructed from fixed Git tree and audited secure/agent input rules.'})
changes=[]
for name in ['secure.py','agent.py','sigterm.py','review.py']:
 before=(OLD/name).read_text();after=before.replace(PREVIOUS,HEAD).replace(str(OLD),str(OUT)).replace('/tmp/rustusb-data-link-ready-531e0c3-20261024',str(BASE)).replace('531e0c3','1582f06')
 after=after.replace('actual completed data-link candidate source manifest','actual completed prepare-recording667 software source manifest')
 if name=='review.py':
  after=after.replace("if not r['case'].startswith('health-')","if r['case'] in NAMES")
  after=after.replace('==213','==222').replace('global_source_inputs=213','global_source_inputs=222')
  after=after.replace("for path in sorted(BASE.rglob('*')):", "for path in sorted([p for n in NAMES for p in (BASE/n).rglob('*')] + [BASE/n for n in ['start.json','result.json','results.json','inputs-before.json','inputs-after.json']]):")
  after=after.replace('New framed-v2 heartbeat matrix is separate.','The eight new preparation and six framed-v2 health runs are separately reviewed; they are not counted here.')
 ast.parse(after);(OUT/name).write_text(after)
 delta=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=str(OLD/name),tofile=str(OUT/name)))
 (OUT/(name+'.diff')).write_text(delta)
 changes.append({'name':name,'original_sha256':sha(before.encode()),'new_sha256':sha(after.encode()),'diff':name+'.diff'})
dump('preparation.json',{'status':'PREPARED_NOT_EXECUTED','head':HEAD,'global_inputs':222,'software_inputs':167,'secure_inputs':169,'agent_inputs':171,'binary_sha256':start['expected_binaries'],'source':'Prior independent lifecycle parsers retained. Only immutable head/paths/software attribution,222 global inputs and exact nine-case selection/inventory changed. No lifecycle assertion deleted or relaxed.','author_disclosure':'Reviewer contributed service and earlier Rust harness work; this is independent method, not independent implementation. Historical parsers are preserved in their original directories.','parsers':changes,'not_executed':['Cargo','QEMU','probe','production binary','harness verifier','new or copied raw lifecycle parsers']})
print(json.dumps({'status':'PREPARED_NOT_EXECUTED','global_inputs':222,'software_inputs':167,'secure_inputs':169,'agent_inputs':171,'parsers':len(changes)}))

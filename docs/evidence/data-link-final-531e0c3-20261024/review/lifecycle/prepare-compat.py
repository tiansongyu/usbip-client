from pathlib import Path
import ast,difflib,hashlib,json,subprocess
REPO=Path('/home/ubuntu/github/usbip-client');OLD=Path('/tmp/rustusb-data-link-compat-independent-review-20261024');BASE=Path('/tmp/rustusb-data-link-ready-531e0c3-20261024');OUT=Path('/tmp/rustusb-data-link-final-lifecycle-review-20261024');HERE=OUT/'compat';HERE.mkdir(parents=True,exist_ok=False)
HEAD='531e0c32a4b0e7252ca771ab4f10042c71c8c195';SOFTWARE=Path('/tmp/rustusb-data-link-management-software-first-20261024')
def sha(b):return hashlib.sha256(b).hexdigest()
def put(n,b):(HERE/n).write_bytes(b)
inputs=json.loads((BASE/'inputs-before.json').read_bytes());assert inputs==json.loads((BASE/'inputs-after.json').read_bytes())
for r in inputs:
 b=subprocess.check_output(['git','show',HEAD+':'+r['path']],cwd=REPO);assert len(b)==r['bytes'] and sha(b)==r['sha256'],r['path']
software=(SOFTWARE/'inputs-before.json').read_bytes();rows=json.loads(software);assert rows==json.loads((SOFTWARE/'inputs-after.json').read_bytes())
for r in rows:
 b=subprocess.check_output(['git','show',HEAD+':'+r['path']],cwd=REPO);assert len(b)==r['bytes'] and sha(b)==r['sha256'],r['path']
for n in ['result.json','toolchain-binaries.json']:put('software-'+n,(SOFTWARE/n).read_bytes())
put('software-inputs.json',software)
elf={Path(x['path']).name:x['sha256']for x in json.loads((SOFTWARE/'toolchain-binaries.json').read_bytes())['binaries']};put('expected-binaries.json',(json.dumps(elf,indent=2)+'\n').encode())
secure=json.loads((BASE/'secure-virtual-ipv4/summary.json').read_bytes())['source_inputs'];agent=json.loads((BASE/'agent-native-ipv4/summary.json').read_bytes())['source_inputs']
lock={'head':HEAD,'secure_inputs':sorted(secure),'agent_inputs':sorted(agent),'source_hashes':{r['path']:{'bytes':r['bytes'],'sha256':r['sha256']}for r in inputs},'software_input_count':len(rows),'software_manifest_git_path':str(HERE/'software-inputs.json'),'software_manifest_sha256':sha(software),'scope':'Actual 626 software inputs mapped individually to final531 Gitblobs; not a new clean software execution.'};put('source-lock.json',(json.dumps(lock,indent=2)+'\n').encode())
changes=[]
for name in ['secure.py','agent.py','sigterm.py','review.py']:
 before=(OLD/name).read_text();after=before.replace('e29cc1163ff9ce3cd73833c15eb24611941bac34',HEAD).replace(str(OLD),str(HERE)).replace('/tmp/rustusb-data-link-compat-e29cc11-20261024',str(BASE)).replace('e29cc11','531e0c3')
 if name=='review.py':
  after=after.replace("runs=load(BASE/'results.json')", "runs=[r for r in load(BASE/'results.json') if not r['case'].startswith('health-')]")
  after=after.replace('==204','==213').replace('global_source_inputs=204','global_source_inputs=213')
 ast.parse(after);put(name,after.encode());put(name+'.diff',''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=str(OLD/name),tofile=str(HERE/name))).encode());changes.append({'parser':name,'original_sha256':sha(before.encode()),'sha256':sha(after.encode()),'delta':name+'.diff'})
report={'status':'PREPARED_NOT_EXECUTED','head':HEAD,'global_inputs':len(inputs),'software_inputs':len(rows),'secure_inputs':len(secure),'agent_inputs':len(agent),'source':'Unmodified old assertions; provenance/head/paths changed only. Final parent batch contains six health and nine compatibility runs; this driver selects only the nine compatibility receipts.','reviewer':'Current reviewer authored Rust heartbeat shell/verifier and related product code; independent parsing is not independent author identity.','parsers':changes};put('preparation.json',(json.dumps(report,indent=2)+'\n').encode());print(json.dumps(report))

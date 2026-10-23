#!/usr/bin/env python3
"""Prepare disclosed derivative parsers; never execute a QEMU/harness verifier."""
from pathlib import Path
import ast, difflib, hashlib, json, subprocess
HERE=Path(__file__).resolve().parent
REPO=Path('/home/ubuntu/github/usbip-client')
OLD=REPO/'docs/evidence/shared-owner-final-b45c2e8-20261024/reviews/lifecycle'
BASE=Path('/tmp/rustusb-data-link-compat-e29cc11-20261024')
SOFTWARE=Path('/tmp/rustusb-data-link-software-first-20261024')
HEAD='e29cc1163ff9ce3cd73833c15eb24611941bac34'
def sha(b): return hashlib.sha256(b).hexdigest()
def put(name,data):
 p=HERE/name
 if p.exists(): raise FileExistsError(p)
 p.write_bytes(data)
def replace_once(text,old,new):
 assert text.count(old)==1,(old,text.count(old))
 return text.replace(old,new,1)
inputs=json.loads((BASE/'inputs-before.json').read_bytes())
assert len(inputs)==204
for entry in inputs:
 b=subprocess.check_output(['git','show',HEAD+':'+entry['path']],cwd=REPO)
 assert len(b)==entry['bytes'] and sha(b)==entry['sha256'],entry['path']
source={r['path']:{'bytes':r['bytes'],'sha256':r['sha256']} for r in inputs}
names=subprocess.check_output(['git','ls-tree','-r','--name-only',HEAD],cwd=REPO,text=True).splitlines()
secure_scripts={'scripts/qemu/'+n for n in ['run-secure-qemu.py','guest-secure-init.sh','run-native-qemu.py','guest-init.sh','run-physical-qemu.py']}
secure=sorted(n for n in names if n in {'Cargo.toml','Cargo.lock'} or ((n.startswith('crates/') or n.startswith('vendor/')) and (n.endswith('.rs') or n.endswith('Cargo.toml'))) or n in secure_scripts)
agent=sorted(set(secure)|{'scripts/qemu/run-agent-qemu.py','scripts/qemu/guest-agent-init.sh'})
software_bytes=(SOFTWARE/'inputs-before.json').read_bytes();software=json.loads(software_bytes)
assert software==json.loads((SOFTWARE/'inputs-after.json').read_bytes()) and len(software)==164
for r in software:
 b=subprocess.check_output(['git','show',HEAD+':'+r['path']],cwd=REPO)
 assert len(b)==r['bytes'] and sha(b)==r['sha256'],r['path']
put('software-inputs.json',software_bytes)
put('software-run-result.json',(SOFTWARE/'result.json').read_bytes())
put('software-toolchain-binaries.json',(SOFTWARE/'toolchain-binaries.json').read_bytes())
elf={Path(x['path']).name:x['sha256'] for x in json.loads((SOFTWARE/'toolchain-binaries.json').read_bytes())['binaries']}
put('expected-binaries.json',(json.dumps(elf,indent=2)+'\n').encode())
lock={'head':HEAD,'secure_inputs':secure,'agent_inputs':agent,'source_hashes':source,'software_input_count':164,'software_manifest_git_path':str(HERE/'software-inputs.json'),'software_manifest_sha256':sha(software_bytes),'scope':'Exact immutable e29cc11 Git blobs. Software candidate ran at dirty031; this is input/ELF attribution only, not a new test execution or a clean software-run claim.'}
put('source-lock.json',(json.dumps(lock,indent=2)+'\n').encode())
changes=[]
for name in ['secure.py','agent.py','sigterm.py']:
 before=(OLD/name).read_text();after=before.replace('b45c2e8c67a06396360fee0af6c0a092a950a6ff',HEAD).replace('/tmp/rustusb-shared-owner-fixed-review-20261024',str(HERE))
 if name=='agent.py':
  after=replace_once(after,"software_manifest_bytes=subprocess.check_output(['git','show',EXPECTED+':'+software_manifest_path],cwd=REPO);software_manifest=json.loads(software_manifest_bytes)","software_manifest_bytes=pathlib.Path(software_manifest_path).read_bytes();software_manifest=json.loads(software_manifest_bytes)")
  after=after.replace('actual completed shared-owner source manifest','actual completed data-link candidate source manifest mapped to e29cc11')
  anchor="  self.facts['actual_processes']=all_receipt_ids"
  addition='''  disk_captures=[]
  session_fields={'id','instance_id','job_id','profile','role','device_key','principal','state','evidence','stop_requested','terminal','operation_error','cleanup','metrics','attempts'}
  raw_v1={'protocol':'raw_v1','identity':None,'sample_sequence':0,'elapsed_millis':0,'phase':'ended','probe':'unavailable_raw_v1','last_probe_sequence':None,'last_probe_enqueued_millis':None,'last_reply_sequence':None,'last_reply_millis':None,'last_round_trip_micros':None,'stats':None,'end':None}
  for role,files in self.raw.items():
   for filename,body in files.items():
    if not filename.endswith('.json'):continue
    document=json.loads(body)
    if not isinstance(document,dict) or not {'jobs','sessions','sequence','audit','requests'}<=set(document):continue
    self.check('private_disk2',role+'/'+filename+' exact private shape',set(document)=={'schema_version','sequence','jobs','sessions','audit','requests','link_history'} and document['schema_version']==2)
    sessions={s['id']:s for s in document['sessions']};history=document['link_history']
    self.check('private_disk2',role+'/'+filename+' unique complete session association',len(sessions)==len(document['sessions'])==len(history)==len({x['session_id'] for x in history}) and {x['session_id'] for x in history}==set(sessions))
    for row in history:
     session=sessions[row['session_id']]
     self.check('private_disk2',role+'/'+filename+'/'+row['session_id']+' immutable epoch and exact null/raw projection',set(row)=={'session_id','instance_id','link'} and row['instance_id']==session['instance_id'] and (row['link'] is None if session['role']=='export' else row['link'] in (None,raw_v1)))
     self.check('legacy_ipc',role+'/'+filename+'/'+row['session_id']+' Session/Metrics shape unchanged',set(session)==session_fields and set(session['metrics'])=={'app_rx_bytes','app_tx_bytes','elapsed_millis','saturated'})
     if session['role']!='export' and (session['state']=='Connected' or session['terminal'] and session['cleanup']=='verified'):
      self.check('private_disk2',role+'/'+filename+'/'+row['session_id']+' observed data session explicitly raw unavailable',row['link']==raw_v1)
    disk_captures.append({'role':role,'file':filename,'sessions':len(sessions),'sha256':sha(body)})
  self.check('private_disk2','nonvacuous state captures from both guests',len(disk_captures)>=4 and {x['role'] for x in disk_captures}=={'server','client'})
  self.facts['private_disk2']={'captures':disk_captures,'scope':'Private application disk2 only. Existing helper tracked disk2 is separately audited; TLS configs/manifest/IPC remain1. No v2 heartbeat observation is inferred from raw-v1 runs.'}
'''
  after=replace_once(after,anchor,addition+anchor)
  after=after.replace('Only source/current software and ELF attribution plus output preservation; actual lifecycle/configuration assertions retained.','e29cc11 source/ELF and local software-manifest provenance; added strict private application disk2/association/raw-v1/unchanged DTO checks; all prior lifecycle/configuration assertions retained.')
 after=after.replace('shared-owner completed legacy SIGTERM group only','data-link product raw-v1 compatibility legacy SIGTERM group only')
 ast.parse(after)
 put(name,after.encode())
 diff=''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile=str(OLD/name),tofile=str(HERE/name)))
 put(name+'.diff',diff.encode())
 changes.append({'parser':name,'original_path':str(OLD/name),'original_sha256':sha(before.encode()),'derived_sha256':sha(after.encode()),'delta':name+'.diff'})
report={'status':'PREPARED_NOT_EXECUTED','head':HEAD,'global_input_count':len(inputs),'secure_input_count':len(secure),'agent_input_count':len(agent),'software_input_count':len(software),'parsers':changes,'method':'Inherited independent raw lifecycle assertions; current reviewer /root/journal_integration_review contributed related product/harness code. This is independent raw parsing, not an independent author identity. No runner module, verifier, Cargo, product executable or QEMU invoked.','pending':'Do not issue PASS until all nine terminal receipts and final batch guard exist. Heartbeat six-run matrix is separate.'}
put('preparation.json',(json.dumps(report,indent=2)+'\n').encode())
print(json.dumps({k:report[k] for k in ['status','global_input_count','secure_input_count','agent_input_count','software_input_count']}))

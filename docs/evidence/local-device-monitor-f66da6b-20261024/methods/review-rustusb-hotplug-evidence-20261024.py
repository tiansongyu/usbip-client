from pathlib import Path
import base64,hashlib,json,re,subprocess
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-hotplug-readonly-review-20261024');out.mkdir(exist_ok=False)
software=Path('/tmp/rustusb-hotplug-software-third-20261024');s=json.loads((software/'start.json').read_text());inputs=json.loads((software/'inputs-before.json').read_text());assert s['head']=='c0346d61d01b6a5f1605f55a6591d87fedd0d615' and not s['status'];assert inputs==json.loads((software/'inputs-after.json').read_text())
for row in inputs:
 b=(software/'source'/row['path']).read_bytes();assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'];assert b==subprocess.check_output(['git','show',s['head']+':'+row['path']],cwd=root)==subprocess.check_output(['git','show','f66da6b:'+row['path']],cwd=root)
runs=[Path('/tmp/rustusb-hotplug-qemu-default-third-20261024'),Path('/tmp/rustusb-hotplug-qemu-all-first-20261024')];runs_report=[];raw_files={};blocks=0
for directory in runs:
 d=json.loads((directory/'summary.json').read_text());assert d['result']=='PASS' and d['head']=='f66da6bd64b960aa54a2878ffab0bbe232e8c85d' and d['actual_qemu_wait']==0 and not d['forced_termination'] and all(d['guards'].values());assert len(d['source_inputs'])==213 and d['source_inputs_after']==d['source_inputs']
 for name,row in d['source_inputs'].items():
  b=(directory/'source'/name).read_bytes();assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'];assert b==subprocess.check_output(['git','show',d['head']+':'+name],cwd=root)
 assert all(d['source_inputs'][r['path']]['sha256']==r['sha256'] for r in inputs)
 text=(directory/'guest-evidence.log').read_text();seen=[]
 for m in re.finditer(r'RUSTUSB_MONITOR_FILE_BEGIN name=([A-Za-z0-9_.-]+) bytes=(\d+) sha256=([0-9a-f]{64})\n(.*?)RUSTUSB_MONITOR_FILE_END name=\1\n',text,re.S):
  name,size,sha,encoded=m.groups();b=base64.b64decode(''.join(encoded.split()),validate=True);assert len(b)==int(size) and hashlib.sha256(b).hexdigest()==sha and b==(directory/'guest-artifacts'/name).read_bytes();seen.append(name);blocks+=1
 assert len(seen)==len(set(seen)) and set(seen)=={p.name for p in (directory/'guest-artifacts').iterdir()}
 frames=[json.loads(line) for line in (directory/'guest-artifacts/watch.jsonl').read_text().splitlines() if line.strip()];assert frames[-1]['event']=='stopped' and frames[-1]['observation_worker_joined'] is True
 points={name:json.loads((directory/f'guest-artifacts/point-{name}.json').read_text()) for name in ['added','unbound','rebound','removed','readded']}
 a=points['added']['devices'][0];b=points['readded']['devices'][0];assert a['device']['id']==b['device']['id'] and a['device']['bus_id']==b['device']['bus_id'] and a['stamp']!=b['stamp']
 assert points['removed']['begin']['device_count']==0
 assert (directory/'guest-artifacts/watch-wait.txt').read_text().strip()=='0'
 for name in ['actor-initial.txt','actor-before-stop.txt']:
  actor=(directory/'guest-artifacts'/name).read_text();assert re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',actor,re.M);assert d['binary_sha256']['rustusb']+'  /proc/' in actor
 runs_report.append({'path':str(directory),'features':d['features'],'selected_inputs':213,'b64_blocks':len(seen),'binary_sha256':d['binary_sha256']})
 for p in directory.rglob('*'):
  if p.is_file():raw_files[str(p)]=hashlib.sha256(p.read_bytes()).hexdigest()
local_report=[]
for n,feature in [('all-third','all'),('default-first','default')]:
 directory=Path('/tmp/rustusb-hotplug-local-'+n+'-20261024');d=json.loads((directory/'summary.json').read_text());listing=json.loads((directory/'archive-files.json').read_text());assert d['result']=='PASS' and d['count']==3
 expected=next(r['binary_sha256']['rustusb'] for r in runs_report if r['features']==feature);assert d['binary_sha256']==expected
 assert [r['actual_wait_code'] for r in d['cases']]==[0,0,1] and all(r['proc_absent'] for r in d['cases'])
 for row in listing['files']:
  p=Path(row['path']);assert '.private' not in p.parts;data=p.read_bytes();assert len(data)==row['bytes'] and hashlib.sha256(data).hexdigest()==row['sha256']
 # Check only the closed public projection schema, without retaining host device bodies.
 projection=json.loads((directory/'interrupt/batch-projection.json').read_text())
 for frame in projection['frames_without_device_or_change_payloads']:
  assert frame['event'] in ['snapshot_begin','snapshot_end'] and not any(key in frame for key in ['device','observation','changes','serial'])
 local_report.append({'path':str(directory),'features':feature,'cases':3,'public_listed_files':len(listing['files']),'private_raw_host_output_excluded':True})
old=Path('/tmp/rustusb-hotplug-local-all-second-old-20261024');d=json.loads((old/'interrupt/summary.json').read_text());assert d['result']=='FAIL' and d['actual_wait_code']==1
method=(root/'scripts/test-device-monitor.py').read_text().replace('"RUST_LOG": "error"','"RUST_LOG": "off"').replace('assert process.returncode == 0, f"actual child wait {process.returncode}"','assert process.returncode == 0').replace('assert process.returncode == 1, f"actual child wait {process.returncode}"','assert process.returncode == 1')
selection=json.loads((old/'selection.json').read_text());assert hashlib.sha256(method.encode()).hexdigest()==selection['script_sha256'];(out/'second-old-method-reconstructed.py').write_text(method)
assert selection['sha256']=='0c09d19292bb65f39d95ba43bfcbc1cc022bcb33aad1bd98a4c5885dfb0d6178'
for p,sha in raw_files.items():assert hashlib.sha256(Path(p).read_bytes()).hexdigest()==sha
(out/'original-file-hashes.json').write_text(json.dumps(raw_files,indent=2)+'\n')
report={'result':'PASS','software_head':s['head'],'software_selected_inputs':206,'software_executions':872,'qemu':runs_report,'actual_redecoded_blocks':blocks,'qemu_original_files_stable':len(raw_files),'local':local_report,'old_sigint_counterfactual':{'actual_wait':1,'binary':selection['sha256'],'method_reconstruction_sha_matches_original_selection':True},'scope':'Same-author read-only source/raw method, no original parser/runner/verifier imports. Not a new execution or independent security audit. Historical first missing child wait is not reconstructed; only second-old method bytes are reconstructed and checked against actual selection SHA. All private host metadata excluded.'};(out/'review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({k:v for k,v in report.items() if k not in ['scope','qemu','local','old_sigint_counterfactual']},indent=2))

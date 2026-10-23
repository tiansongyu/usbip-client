import json,pathlib,hashlib,shutil,subprocess,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');tmp=pathlib.Path('/tmp');dest=root/'docs/evidence/shared-owner-ci-fixture-2638a8c-20261024';dest.mkdir(parents=True,exist_ok=False);entries=[]
def archive(p,rel):
 p=pathlib.Path(p);q=dest/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);raw=q.read_bytes();assert raw==p.read_bytes();entries.append({'path':rel,'original':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
head='2638a8c4700d79233d283de1a168f574be5f8ee4';run=tmp/'rustusb-shared-owner-software-fourth-20261024';r=json.loads((run/'result.json').read_text());assert r['exit_code']==0 and r['inputs_unchanged']
inputs=json.loads((run/'inputs-after.json').read_text());assert len(inputs)==152
for i in inputs:
 raw=subprocess.check_output(['git','show',head+':'+i['path']],cwd=root);assert len(raw)==i['bytes'] and hashlib.sha256(raw).hexdigest()==i['sha256']
for p in sorted(run.iterdir()):archive(p,'software/'+p.name)
for p in sorted((tmp/'rustusb-secure-foreground-tests-dneqei').rglob('*')):
 if p.is_file():archive(p,'local-secure/'+str(p.relative_to(tmp/'rustusb-secure-foreground-tests-dneqei')))
for p in sorted((tmp/'rustusb-secure-foreground-wchan-fix-20261024').rglob('*')):
 if p.is_file():archive(p,'author/'+str(p.relative_to(tmp/'rustusb-secure-foreground-wchan-fix-20261024')))
for p in sorted(tmp.glob('rustusb-shared-owner-ci-*20261024*')):
 if p.is_file() and p.suffix in ['.json','.log','.sh']:archive(p,'ci-and-review/'+p.name)
for p in sorted(tmp.glob('rustusb-shared-owner-first-ci-summary-20261024.json')):archive(p,'ci-and-review/'+p.name)
for p in sorted(tmp.glob('rustusb-secure-foreground-wchan-independent-review-20261024.json')):archive(p,'reviews/'+p.name)
for p in sorted(tmp.glob('rustusb-secure-foreground-fourth-runtime-independent-review-20261024.*')):archive(p,'reviews/'+p.name)
for name in ['rustusb-shared-owner-wchan-final-software-scope-20261024.json','archive-rustusb-shared-owner-ci-fixture-20261024.py']:archive(tmp/name,name)
scope={'software_commit_inputs':head,'software':r,'actual_gate_head':'b45c2e8 plus test-only repair in modified worktree; 152 inputs verified individually against2638 Git blobs','qemu':'all original9 remain clean b45; prod source/CLI/helper ELF unchanged by cfg(test) module plus CI diagnostics','old_ci':'run39997678019 original two Ubuntu24 FAIL and two Ubuntu22 PASS retained in full official connector logs. Original runner /tmp per-case artifacts unavailable, not reconstructed. Old wchan not recorded, source diagnosis only.','new_ci':'39999926362 in progress at this archive preparation; later final status/actual observations stored separately','source_reference':'https://raw.githubusercontent.com/torvalds/linux/v6.17/fs/pipe.c','scope':'only exact two known wait symbols, original no-drain/4096/lease/timeout/cleanup assertions unchanged; diagnostic step does not hide quality failure or read keys/USB payload'}
p=tmp/'rustusb-shared-owner-ci-fixture-scope-20261024.json';p.write_text(json.dumps(scope,indent=2)+'\n');archive(p,'scope.json')
(dest/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':entries},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(i['bytes'] for i in entries),'manifest':str(dest/'manifest.json')}))

from pathlib import Path
import json,hashlib,shutil
root=Path('/home/ubuntu/github/usbip-client')
source=Path('/tmp/rustusb-automation-foundation-publication-1b332c5d-20261025')
dest=root/'docs/evidence/automatic-sharing-foundation-publication-1b332c5d-20261025'
head='1b332c5d611d3db9a68c8f1e9da34af1e7668f46'
runs=json.loads(json.loads((source/'runs-final.json').read_text())['structuredContent']['content'])['workflow_runs']
assert len(runs)==3 and all(r['head_sha']==head and r['status']=='completed' and r['conclusion']=='success' for r in runs)
expected={'check (ubuntu-22.04, x86_64)','check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04-arm, aarch64)'}
workflows=[]
for run in runs:
    jobs=json.loads((source/('jobs-'+str(run['id'])+'-final.json')).read_text())['structuredContent']['jobs']
    assert all(j['status']=='completed' and j['conclusion']=='success' for j in jobs)
    if run['name'] in ['RustUSB software checks','RustUSB native GUI checks']:
        assert len(jobs)==4 and {j['name'] for j in jobs}==expected
        arch='Verify native architecture and record the software environment' if run['name']=='RustUSB software checks' else 'Verify architecture and actual GTK/WebKit versions'
        gate='Formatting, lint, tests, release and documentation' if run['name']=='RustUSB software checks' else 'GUI formatting, Clippy, tests, release and rustdoc'
        for job in jobs:
            for needed in [arch,gate]:
                matches=[s for s in job['steps'] if s['name']==needed]
                assert len(matches)==1 and matches[0]['status']=='completed' and matches[0]['conclusion']=='success'
    else:assert {j['name'] for j in jobs}=={'check','deploy'}
    workflows.append({'id':run['id'],'name':run['name'],'status':run['status'],'conclusion':run['conclusion'],'jobs':[{'id':j['id'],'name':j['name'],'status':j['status'],'conclusion':j['conclusion']} for j in jobs]})
http=json.loads((source/'http-and-source.json').read_text());assert http['result']=='PASS' and http['content_head']==head
assert len(http['http'])==7 and all(r['status']==200 and r['exact_git_bytes'] for r in http['http'])
shutil.copyfile('/tmp/check-rustusb-automation-foundation-publication-20261025.py',source/'check-publication.py')
shutil.copyfile(__file__,source/'collect-publication.py')
receipt={'result':'PASS','content_head':head,'local_gate_head':http['local_gate_head'],'workflows':workflows,'canonical_http':http['http'],'frozen_inputs_preserved':http['frozen_inputs_preserved'],'global_skill_files_preserved':http['global_skill_files'],'site_files_preserved':http['site_files'],'scope':'Original GitHub connector responses and canonical HTTP bodies. CI status and required architecture/full-gate steps checked; no CI log/ELF/per-platform test recount or new QEMU/hardware/browser test. Final receipt commit is documentation only.'}
(source/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
dest.mkdir(exist_ok=False);entries=[]
for p in sorted(source.rglob('*')):
    if not p.is_file():continue
    rel=p.relative_to(source);assert not any(part=='.private' for part in rel.parts)
    assert p.suffix not in ['.pem','.der','.key','.cpio','.gz']
    target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    assert target.read_bytes()==p.read_bytes()
    entries.append({'path':str(rel),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(dest/'manifest.json').write_text(json.dumps({'files':entries},indent=2)+'\n')
print(json.dumps({'result':'PASS','files':len(entries),'bytes':sum(x['bytes'] for x in entries),'workflows':[{k:r[k] for k in ['id','name','conclusion']} for r in workflows]}))

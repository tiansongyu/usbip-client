import hashlib,json,pathlib,shutil,subprocess
root=pathlib.Path('/home/ubuntu/github/usbip-client');src=pathlib.Path('/tmp/rustusb-shared-owner-qemu-final-20261024');dest=root/'docs/evidence/shared-owner-compat-failure-4d700c2-20261024';dest.mkdir(parents=True,exist_ok=False)
summary=json.loads((src/'secure-virtual-ipv4/summary.json').read_text());assert summary['result']=='FAIL' and summary['head']=='4d700c22754fc5e4efb37fee391e00ff340d5829'
assert all(summary[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']);assert len(json.loads((src/'results.json').read_text()))==1
entries=[]
for p in sorted(src.rglob('*')):
 if not p.is_file():continue
 rel=str(p.relative_to(src));q=dest/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);raw=q.read_bytes();assert raw==p.read_bytes();entries.append({'path':rel,'original':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
for rel,info in summary['source_inputs'].items():
 raw=subprocess.check_output(['git','show',summary['head']+':'+rel],cwd=root)
 expected=info if isinstance(info,str) else info['sha256'];assert hashlib.sha256(raw).hexdigest()==expected,rel
scope={'result':'FAIL','scope':'one secure virtual IPv4 QEMU; other eight planned runs not executed','cause':'foreground contender denial used managed historical message without is; original precise guest grep rejected it; not an environment failure','observed':'two actual Connected VHCI receipts and schema1 observed import records, contender denied before any USB I/O cycle; server waiting for host control then failed','guest_exit_codes':summary['guest_exit_codes'],'inputs_at_clean_git_blob_verified':len(summary['source_inputs']),'guards':{k:summary[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']},'repair_scope':'restore foreground exact original error string while keeping managed original; do not loosen original harness'}
(dest/'scope.json').write_text(json.dumps(scope,indent=2)+'\n')
(dest/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':entries},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(e['bytes'] for e in entries),'manifest':str(dest/'manifest.json')}))

import pathlib,hashlib,json,shutil,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');tmp=pathlib.Path('/tmp');dest=root/'docs/evidence/shared-owner-software-20261024';dest.mkdir(parents=True,exist_ok=False)
entries=[]
def archive(source,relative,kind='original'):
 source=pathlib.Path(source);assert source.is_file(),source
 target=dest/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target);raw=target.read_bytes();assert raw==source.read_bytes()
 entries.append({'path':relative,'original':str(source),'kind':kind,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
names=set()
for prefix in ['rustusb-client-native-foreground-unification-design-','rustusb-rust-foreground-unification-design-','rustusb-foreground-service-contract-review-','rustusb-client-native-shared-owner-author-handoff-','rustusb-shared-rust-owner-author-handoff-','rustusb-shared-rust-owner-stageerror-handoff-','rustusb-shared-rust-owner-cli-cross-review-initial-','rustusb-shared-foreground-independent-review-','rustusb-secure-foreground-tests-handoff-','rustusb-secure-foreground-tests-independent-review-','rustusb-shared-owner-fixes-matrix-review-','rustusb-shared-owner-qemu-raw-review-plan-','rustusb-native-lock-recording-fix-author-handoff-','rustusb-shared-owner-final-semantic-review-','rustusb-shared-owner-root-preflight-']:
 names.update(p for p in tmp.glob(prefix+'*20261024*') if p.is_file() and p.suffix in ['.json','.md'])
for p in sorted(names):archive(p,'research/'+p.name)
for p in sorted(tmp.glob('rustusb-shared-owner-*20261024.log')):archive(p,'integration/'+p.name)
for p in sorted(tmp.glob('rustusb-shared-owner-native-only-*20261024.txt')):archive(p,'integration/'+p.name)
for stage in ['first','second']:
 directory=tmp/f'rustusb-shared-owner-software-{stage}-20261024'
 for p in sorted(directory.iterdir()):
  if p.is_file():archive(p,stage+'/'+p.name)
for p in sorted((tmp/'rustusb-shared-owner-first-gate-source-20261024').rglob('*')):
 if p.is_file():archive(p,'first-gate-source/'+str(p.relative_to(tmp/'rustusb-shared-owner-first-gate-source-20261024')))
final=json.loads((dest/'second/result.json').read_text());assert final['exit_code']==0 and final['inputs_unchanged'];assert all(g['failed']==0 and g['ignored']==0 for g in final['tests'].values())
inputs=json.loads((dest/'second/inputs-after.json').read_text())
for i in inputs:assert hashlib.sha256((root/i['path']).read_bytes()).hexdigest()==i['sha256'],i['path']
start=json.loads((dest/'second/start.json').read_text());started=datetime.datetime.fromisoformat(start['utc']).timestamp()
evidence=[p for p in tmp.glob('rustusb-secure-foreground-tests-*') if p.is_dir() and p.stat().st_mtime>=started];assert len(evidence)==1,evidence
summary=json.loads((evidence[0]/'summary.json').read_text());assert summary['result']=='PASS' and len(summary['cases'])==5
for p in sorted(evidence[0].rglob('*')):
 if p.is_file():archive(p,'secure-foreground-runtime/'+str(p.relative_to(evidence[0])))
for p in sorted((tmp/'rustusb-shared-owner-local-signals-20261024').rglob('*')):
 if p.is_file():archive(p,'local-signals/'+str(p.relative_to(tmp/'rustusb-shared-owner-local-signals-20261024')))
for name in ['run-rustusb-shared-owner-software-20261024.py','archive-rustusb-shared-owner-software-20261024.py','run-rustusb-shared-owner-matrix-20261024.py']:archive(tmp/name,name)
scope={'base':'9314aa08c41d84672467b06df8d0cf51e5d7ecf2','software':final,'native_only':'see separately archived check/tree; no additional test count','first_full_gate':'FAIL: default-feature is_managed dead-code, no tests run; two original source files saved before cfg/invalid fixture-key repairs, exact pre-run SHA verified','initial_development':'first cargo check success with obsolete shim warnings, first all-feature clippy FAIL unused import/large enum, second all-feature clippy success; no complete initial-source guard captured, author per-file handoffs retained','secure_runtime_scope':summary['scope'],'not_proven':'trusted root file loader/helper/USB by memory-DER local test; accepted initializer gate; arbitrary WAL stall bounded cleanup; hardware G1/G2/full Phase3/TUI/GUI/Windows','review_scope':'author and independent source snapshots belong to their recorded revisions, not automatically the final formatted tree'}
p=tmp/'rustusb-shared-owner-software-scope-20261024.json';p.write_text(json.dumps(scope,indent=2)+'\n');archive(p,'scope.json')
(dest/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':entries},indent=2)+'\n')
print(json.dumps({'files':len(entries),'bytes':sum(i['bytes'] for i in entries),'runtime':str(evidence[0]),'manifest':str(dest/'manifest.json')}))

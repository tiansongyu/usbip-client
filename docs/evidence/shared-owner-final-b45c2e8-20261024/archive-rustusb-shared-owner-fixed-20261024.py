import pathlib,hashlib,json,shutil,subprocess
root=pathlib.Path('/home/ubuntu/github/usbip-client');tmp=pathlib.Path('/tmp');src=tmp/'rustusb-shared-owner-qemu-fixed-20261024';dest=root/'docs/evidence/shared-owner-final-b45c2e8-20261024';dest.mkdir(parents=True,exist_ok=False);entries=[]
def archive(p,rel):
 p=pathlib.Path(p);q=dest/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);raw=q.read_bytes();assert raw==p.read_bytes();entries.append({'path':rel,'original':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
result=json.loads((src/'result.json').read_text());assert result['result']=='PASS' and len(result['runs'])==9 and result['inputs_unchanged'];head=result['head'];assert head=='b45c2e8c67a06396360fee0af6c0a092a950a6ff'
before=json.loads((src/'inputs-before.json').read_text());assert len(before)==192 and before==json.loads((src/'inputs-after.json').read_text())
for i in before:
 raw=subprocess.check_output(['git','show',head+':'+i['path']],cwd=root);assert len(raw)==i['bytes'] and hashlib.sha256(raw).hexdigest()==i['sha256'],i['path']
for run in result['runs']:
 assert run['exit_code']==0 and run['unchanged'] and run['summary_result']=='PASS'
 d=json.loads((pathlib.Path(run['directory'])/'summary.json').read_text());assert d['guest_exit_codes']=={'server':0,'client':0}
 if run['case']=='sigterm':assert d['source_guard_passed'] and d['binary_guard_passed'] and not d['working_tree_dirty'] and not d['working_tree_dirty_after']
 else:assert all(d[k] for k in ['source_guard','binary_guard','head_guard','clean_guard'])
for p in sorted(src.rglob('*')):
 if p.is_file():archive(p,str(p.relative_to(src)))
review=tmp/'rustusb-shared-owner-fixed-review-20261024'
for p in sorted(review.rglob('*')):
 if p.is_file():archive(p,'reviews/lifecycle/'+str(p.relative_to(review)))
for p in sorted(tmp.glob('rustusb-shared-owner*20261024*')):
 if p.is_file() and p.suffix in ['.py','.json'] and any(x in p.name for x in ['security','packet-decoder']):archive(p,'reviews/security/'+p.name)
for p in sorted(tmp.glob('rustusb-secure-foreground-third-runtime-independent-review-20261024.*')):archive(p,'reviews/local/'+p.name)
for name in ['rustusb-shared-owner-validation-draft-20261024.md','rustusb-shared-owner-validation-draft-sources-20261024.json']:archive(tmp/name,'research/'+name)
for name in ['archive-rustusb-shared-owner-fixed-20261024.py','run-rustusb-shared-owner-matrix-20261024.py']:archive(tmp/name,name)
scope={'head':head,'matrix':'9 existing QEMU runs PASS / 18 guest exit0','software':'530 executions belong to final 152 guarded software inputs individually verified against b45 Git blobs; actual gate recorded HEAD4d plus repair worktree','global_input_count':192,'same_cli_sha256':'ee66f1c9af551f603ad0cd5cbaf3b7e25ce13329819728802defe2632528df01','same_helper_sha256':'cc6525a4aa746fcfea5e05ee6a6cbc5975cf24bd82d13928da6e50a5fccb3c2a','scope':'six secure foreground + two Native managed agent + plaintext legacy USBFS TERM. Diagnostic counts not new USB cases. Agent actual-config metadata is not attributed to older foreground collector. First clean4d compatibility FAIL remains separate FAIL. No hardware, initializer gate/kernel hard-block, arbitrary WAL-stall bound, heartbeat/RTT or full Phase3 claim','independent_tools':'Historical parsers reused with exact new revision/ELF/inputs; H authored a harness, independent method not wholly independent harness authorship. Preparatory parser-generation/index errors retained; no product/harness assertions relaxed.'}
p=tmp/'rustusb-shared-owner-final-scope-20261024.json';p.write_text(json.dumps(scope,indent=2)+'\n');archive(p,'scope.json')
(dest/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':entries},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(i['bytes'] for i in entries),'manifest':str(dest/'manifest.json')}))

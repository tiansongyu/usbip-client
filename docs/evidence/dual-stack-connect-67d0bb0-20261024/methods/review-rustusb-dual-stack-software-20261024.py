from pathlib import Path
import hashlib,json,re,subprocess
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-dual-stack-software-first-20261024')
d=json.loads((out/'result.json').read_text());start=json.loads((out/'start.json').read_text());assert d['result']=='PASS' and start['status']=='' and start['head']=='f3bf579c79d361c96c9381c74a5a151557a5756a'
before=json.loads((out/'inputs-before.json').read_text());assert before==json.loads((out/'inputs-after.json').read_text()) and len(before)==198
for r in before:
 b=(out/'source'/r['path']).read_bytes();assert len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['sha256'];assert subprocess.check_output(['git','show',start['head']+':'+r['path']],cwd=root)==b
current=None;counts={name:dict(rows=0,passed=0,failed=0,ignored=0) for name in ['default','all_features','vendor']}
for line in (out/'full-gate.log').read_text().splitlines():
 if line.startswith('+ cargo test --workspace --locked'):current='default'
 elif line.startswith('+ cargo test --workspace --all-features --locked'):current='all_features'
 elif line.startswith('+ cargo test --manifest-path vendor/nusb'):current='vendor'
 m=re.match(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored;',line)
 if m:
  assert current is not None;v=counts[current];v['rows']+=1
  for key,value in zip(['passed','failed','ignored'],m.groups()):v[key]+=int(value)
assert [counts[x]['passed'] for x in ['default','all_features','vendor']]==[365,457,20]
assert all(v['failed']==v['ignored']==0 for v in counts.values())
tree=(out/'native-only-tree.log').read_text();assert not any(x in tree for x in ['rustusb-rust-backend','nusb','rusb','libusb1-sys'])
review=dict(result='PASS',actual_clean_head=start['head'],counts=counts,total_executions=sum(x['passed'] for x in counts.values()),selected_inputs=len(before),inputs_stable=True,artifacts=d['artifacts'],scope='Actual clean full software execution and fixed-Git input mapping; research/runtime/QEMU counts remain separate.')
(out/'review.json').write_text(json.dumps(review,indent=2)+'\n');print({k:v for k,v in review.items() if k not in ['artifacts']})

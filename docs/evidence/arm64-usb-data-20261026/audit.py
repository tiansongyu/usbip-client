"""Same-author read-only archive/source consistency audit; no runner imports."""
from pathlib import Path
import hashlib,json,subprocess,tarfile
ROOT=Path(__file__).resolve().parents[3];OUT=Path(__file__).parent
sha=lambda b:hashlib.sha256(b).hexdigest()
def git(*a):return subprocess.check_output(['git',*a],cwd=ROOT)
summary=json.loads((OUT/'summary.json').read_text());manifest=json.loads((OUT/'originals-manifest.json').read_text())
checks=0
with tarfile.open(OUT/'originals.tar.gz') as archive:
 raw={m.name:archive.extractfile(m).read() for m in archive.getmembers()}
 assert set(raw)=={m['path'] for m in manifest['members']}
 for m in manifest['members']:
  b=raw[m['path']];assert len(b)==m['bytes'] and sha(b)==m['sha256'];checks+=1
 for n,b in raw.items():
  if n.startswith('source/'):
   _,head,path=n.split('/',2);assert b==git('show',head+':'+path);checks+=1
 for kind in ['initial','fixed']:
  s=json.loads(raw['software/'+kind+'/summary.json'])
  assert s['result']=='PASS' and all(s['guards'].values()) and len(s['inputs'])==734
  assert [(c['passed'],c['failed'],c['ignored'],c['exit']) for c in s['commands']]==[(1096,0,0,0),(18,0,0,0)]
  for n,r in s['inputs'].items():
   b=git('show',s['head']+':'+n);assert sha(b)==r['sha256'] and len(b)==r['bytes'];checks+=1
 for run in summary['matrix']:
  prefix='runs/'+run['run']+'/'
  original=json.loads(raw[prefix+'summary.json']);outer=json.loads(raw[prefix+'arm-verdict.json'])
  assert not original['passed'] and all(original[k] for k in ['head_guard','clean_guard','frozen_file_guard'])
  assert list(original['qemu_exits'].values())==[0,0]
  assert all(outer['guards'].values()) and not outer['passed'] and outer['inherited_summary_sha256']==sha(raw[prefix+'summary.json'])
  method=json.loads(raw['methods/'+run['run']+'.composition.json']);head=run['head']
  source=raw['source/'+head+'/scripts/qemu_installed_usb/run.py'].decode()
  generated=source.replace('from vm import Vm, sha','from arm_vm import ArmVm as Vm, sha',1).encode()
  assert generated==raw['methods/'+run['run']+'.generated.py'] and sha(generated)==method['generated_sha256'];checks+=1
 power='diagnostics/rustusb-arm64-local-hid-power-20261026/'
 for method,expected in [('auto-original-send-key',False),('on-original-send-key',True)]:
  name=next(n for n in raw if n.startswith(power+'guest/') and n.endswith('-probe-'+method+'.stdout'))
  result=json.loads(raw[name]);assert result['passed'] is expected
  if expected:assert [1,2,1] in result['events'] and [1,2,0] in result['events']
  else:assert result['events']==[]
  name=next(n for n in raw if n.startswith(power+'guest/') and n.endswith('-power-'+method+'.stdout'))
  text=raw[name].decode();path='/sys/devices/platform/vhci_hcd.0/usb3/3-1/power/'
  assert path+'runtime_status\n'+('active' if expected else 'suspended')+'\n' in text
  assert path+'control\n'+('on' if expected else 'auto')+'\n' in text;checks+=1
assert len(summary['matrix'])==9 and summary['acceptance_paired_runs']==8 and summary['diagnostic_paired_runs']==1
assert summary['passed']==0 and summary['failed_retained']==9 and summary['actual_QEMU_exits0']==18
assert len(summary['local_diagnostics'])==5 and all(d['qemu_exit']==0 and all(d['guards'].values()) for d in summary['local_diagnostics'])
assert not git('diff','--name-only','0316de7638c68b9360ec719194e9e0c4ae0fe454',summary['software']['head'],'--','crates','apps','vendor','Cargo.toml','Cargo.lock','packaging').strip()
expected=json.loads((ROOT/'docs/evidence/tui-interactions-docs-20261025/summary.json').read_text())['global_skill_unchanged']
base=Path('/home/ubuntu/.agents/skills/readme-site')
for n,r in expected.items():
 b=(base/n).read_bytes();assert sha(b)==r['sha256'] and len(b)==r['bytes'];checks+=1
assert Path('/home/ubuntu/.codex/skills/readme-site').resolve()==base.resolve()
for n,r in summary['software']['binaries'].items():
 b=(ROOT/n).read_bytes();assert sha(b)==r['sha256'] and len(b)==r['bytes'];checks+=1
result={'result':'PASS','scope':'Same-author archive/source consistency, not another execution or independent security audit','member_count':len(raw),'checks':checks,'acceptance_failed_pairs':8,'diagnostic_failed_pairs':1,'paired_guest_exits0':18,'local_diagnostic_guest_exits0':5,'same_source_power_control':{'auto_suspended_no_events':True,'only_imported_on_active_exact_events':True},'product_source_unchanged_from_CI0316':True,'global_readme_site_four_files_and_symlink_unchanged':True,'host_ELFs_unchanged':True}
(OUT/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))

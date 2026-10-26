"""Read-only audit of exact archived observations; no verdict rewriting."""
from pathlib import Path
import ast,hashlib,json,re,tarfile
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
def load(p):return json.loads(p.read_text())
def sha(b):return hashlib.sha256(b).hexdigest()
raw={}
with tarfile.open(HERE/'originals.tar.gz','r:gz') as tar:
 for row in load(HERE/'originals-manifest.json')['members']:
  b=tar.extractfile(row['path']).read();assert len(b)==row['bytes'] and sha(b)==row['sha256'];raw[row['path']]=b
summary=load(HERE/'summary.json')
def value(name):return json.loads(raw[name])
source=raw['sources/1b59ee74f7a0e71ef5e2c4a1aa2ae1710ed17a8d/scripts/qemu_installed_usb/run.py']
nodes=[n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name in ['require','check_terminal','check_empty_vhci','check_held']]
assert len(nodes)==4
checks={};exec(compile(ast.Module(body=nodes,type_ignores=[]),'archived-original-checks','exec'),checks)
rows=[]
for run in summary['matrix']:
 prefix='runs/'+run['run']+'/'
 def data(name):return value(prefix+name)
 scope={'run':run['run'],'operation_passed':run['passed'],'source_head':run['head']}
 uids={};terminal_counts={};pcaps={}
 for role in ['client','server']:
  setup=data(role+'-setup-'+role+'-'+run['backend']+'-'+str(run['family'])+'-1.json')
  uid=setup['uid'];assert uid in [109,112];uids[role]=uid
  for unit,body in setup['units'].items():assert body[body.index('[Unit]'):].strip()==(ROOT/'packaging/systemd'/unit).read_text().strip()
  stops=sorted([n[len(prefix):] for n in raw if n.startswith(prefix+role+'-stop-services-') and n.endswith('.json')],key=lambda n:int(n.rsplit('-',1)[1][:-5]))
  snap=data(stops[-1]);terminal_counts[role]=checks['check_terminal'](snap)
  if role=='server':assert snap['host']==setup['baseline']
  else:checks['check_empty_vhci'](snap,snap)
  if not run['passed']:continue
  for cycle in [1,2,3]:
   ready=data('client-ready-client-'+str(cycle)+'.json')['snapshot'] if role=='client' else data('server-snapshot-'+str(cycle)+'.json')
   for unit in ['rustusb-agent.service','rustusb-helper.service']:
    process=ready['units'][unit]['process'];fields=dict(l.split(':',1) for l in process['status'].splitlines() if ':' in l)
    assert set(fields['Uid'].split())=={str(uid if unit.startswith('rustusb-agent') else 0)}
    if unit.startswith('rustusb-agent'):assert all(int(fields[n].strip(),16)==0 for n in ['CapEff','CapPrm','CapAmb','CapInh'])
    version=6 if 'coordinated' in run['run'] and cycle==1 else 10
    kind='cli' if unit.startswith('rustusb-agent') else 'helper'
    assert process['exe_sha256']==value('package-builds/v'+str(version)+'.json')['binaries'][kind]['sha256']
   if role=='client':checks['check_held'](ready,setup['device_keys'])
  upgrade=next(json.loads(b) for n,b in raw.items() if n.startswith(prefix+role+'-upgrade-') and n.endswith('.json'))
  assert upgrade['dpkg_exit']==0 and 'preview20261026.10-1' in upgrade['version']
  checks['check_terminal'](upgrade['after'])
  assert all(not o['original_pid_exists'] and not o['cgroup_processes'] for o in upgrade['original_owners'].values())
  assert 'process_stop_verified' in upgrade['dpkg_stderr']+upgrade['dpkg_stdout']
  events=[json.loads(line) for line in upgrade['journal'].splitlines()];times={}
  for unit,old in upgrade['before'].items():
   base=[e for e in events if e.get('_PID')=='1' and e.get('UNIT')==unit and e.get('INVOCATION_ID')==old['InvocationID']]
   stops=[e for e in base if e.get('MESSAGE_ID')=='9d1aaa27d60140bd96365438aad20286' and e.get('JOB_TYPE')=='stop' and e.get('JOB_RESULT')=='done']
   assert len(stops)==1 and len([e for e in base if e.get('MESSAGE_ID')=='7ad2d189f7e94e70a38c781354912448'])==1
   times[unit]=int(stops[0]['__MONOTONIC_TIMESTAMP'])
  assert times['rustusb-agent.service']<times['rustusb-helper.service']
  review=data(role+'-pcap-review.json');assert review['flows'];pcaps[role]={'packets':review['packets'],'flows':len(review['flows'])}
 if run['passed']:
  assert terminal_counts=={'client':run['client_root_proofs'],'server':run['server_root_proofs']}
  for cycle in [1,2,3]:
   proof=data('client-probe-'+str(cycle)+'-'+str(cycle)+'.json');assert proof['rx_bytes']==proof['tx_bytes']==35
   assert [1,cycle+1,0] in proof['events'] and [1,cycle+1,1] in proof['events']
   ftdi=data('ftdi-host-'+str(cycle)+'.json');assert ftdi['equal'] and ftdi['actual_hex']==ftdi['expected_hex']
 scope.update({'broker_uids':uids,'exact_terminal_counts':terminal_counts,'original_baseline_verified':True,'pcap_observations':pcaps});rows.append(scope)
failed_log=raw['software/failed-candidate/core.log'].decode()
counts=[tuple(map(int,m)) for m in re.findall(r'test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored',failed_log)]
correction={'raw_summary_preserved':True,'original_reported_passed':260,'derived_passed':sum(c[0] for c in counts),'derived_failed':sum(c[1] for c in counts),'derived_ignored':sum(c[2] for c in counts),'actual_exit':101,'reason':'Historical wrapper counted only ok segments; FAILED service segment adds 133 passed and 1 failed. No verdict changed.'}
assert correction['derived_passed']==393 and correction['derived_failed']==1
stability=value('development/peer-drain-stability/summary.json')
assert stability['passed']==96 and stability['failed']==0 and all(c['exit']==0 and c['exactly_one_test'] for c in stability['commands'])
baseline=load(ROOT/'docs/evidence/tui-interactions-docs-20261025/summary.json')['global_skill_unchanged']
for n,record in baseline.items():
 b=(Path('/home/ubuntu/.agents/skills/readme-site')/n).read_bytes();assert len(b)==record['bytes'] and sha(b)==record['sha256']
assert Path('/home/ubuntu/.codex/skills/readme-site').resolve()==Path('/home/ubuntu/.agents/skills/readme-site')
result={'archive_members_verified':len(raw),'matrix':rows,'passing_root_proofs':sum(sum(r['exact_terminal_counts'].values()) for r in rows if r['operation_passed']),'retained_failed_gate_count_correction':correction,'global_skill_unchanged':baseline,'codex_skill_symlink_verified':True,'stability_actual_processes':96,'stability_catalog_exchanges':8192}
assert result['passing_root_proofs']==70
(HERE/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'members':len(raw),'passing_pairs':5,'failed_pairs':1,'passing_root_proofs':70,'corrected_failed_gate_passed':393,'global_skill_unchanged':True}))

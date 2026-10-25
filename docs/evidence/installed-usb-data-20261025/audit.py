"""Read-only audit of exact archived successful observations; failures stay failures."""
from pathlib import Path
import hashlib,json,tarfile
ROOT=Path(__file__).resolve().parents[3]
HERE=Path(__file__).resolve().parent
def load(p):return json.loads(p.read_text())
manifest=load(HERE/'originals-manifest.json')
with tarfile.open(HERE/'originals.tar.gz','r:gz') as tar:
 raw={}
 for row in manifest['members']:
  b=tar.extractfile(row['path']).read()
  assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256']
  raw[row['path']]=b
def data(run,name):return json.loads(raw['runs/'+run+'/'+name])
rows=[]
for run in load(HERE/'summary.json')['matrix']:
 if not run['passed']:continue
 name=run['run'];uids={};scope={'run':name,'source_head':run['head'],'backend':run['backend'],'family':run['family'],'io_rounds':3}
 for role in ['server','client']:
  setup=data(name,role+'-setup-'+role+'-'+run['backend']+'-'+str(run['family'])+'-1.json')
  uids[role]=setup['uid'];assert setup['uid'] in [109,112] and setup['uid']>0
  for unit,body in setup['units'].items():
   assert body[body.index('[Unit]'):].strip()==(ROOT/'packaging/systemd'/unit).read_text().strip()
  route=setup['ipv6_route'][0];assert route['prefsrc'].endswith('::1' if role=='server' else '::2')
  if role=='client':
   for cycle in [1,2,3]:
    snap=data(name,'client-ready-client-'+str(cycle)+'.json')['snapshot']
    pid=snap['units']['rustusb-agent.service']['MainPID']
    for rec in snap['native']['records']:
     assert rec['owner']['uid']==setup['uid'] and str(rec['owner']['pid'])==pid
    status=snap['units']['rustusb-agent.service']['process']['status']
    fields=dict(l.split(':',1) for l in status.splitlines() if ':' in l)
    assert set(fields['Uid'].split())=={str(setup['uid'])}
    assert all(int(fields[n].strip(),16)==0 for n in ['CapEff','CapPrm','CapAmb','CapInh'])
    proof=data(name,'client-probe-'+str(cycle)+'-'+str(cycle)+'.json')
    assert proof['rx_bytes']==proof['tx_bytes']==35
    assert [1,cycle+1,0] in proof['events'] and [1,cycle+1,1] in proof['events']
    ftdi=data(name,'ftdi-host-'+str(cycle)+'.json')
    assert ftdi['equal'] and ftdi['actual_hex']==ftdi['expected_hex']
  upgrade=next(json.loads(b) for key,b in raw.items() if key.startswith('runs/'+name+'/'+role+'-upgrade-') and key.endswith('.json'))
  assert upgrade['dpkg_exit']==0 and 'preview20261025.8-1' in upgrade['version']
  assert all(not o['original_pid_exists'] and not o['cgroup_processes'] for o in upgrade['original_owners'].values())
  # The real package gate must report actual process stopping, never USB proof by itself.
  assert 'process_stop_verified' in upgrade['dpkg_stderr'] or 'process_stop_verified' in upgrade['dpkg_stdout']
  for name2,old in upgrade['before'].items():
   fields=dict(l.split(':',1) for l in old['process']['status'].splitlines() if ':' in l)
   assert set(fields['Uid'].split())=={str(setup['uid'] if name2.startswith('rustusb-agent') else 0)}
 scope['broker_uids']=uids
 scope['client_root_proofs']=run['client_root_proofs'];scope['server_root_proofs']=run['server_root_proofs'];rows.append(scope)
baseline=load(ROOT/'docs/evidence/tui-interactions-docs-20261025/summary.json')['global_skill_unchanged']
for name,record in baseline.items():
 b=(Path('/home/ubuntu/.agents/skills/readme-site')/name).read_bytes()
 assert len(b)==record['bytes'] and hashlib.sha256(b).hexdigest()==record['sha256']
assert Path('/home/ubuntu/.codex/skills/readme-site').resolve()==Path('/home/ubuntu/.agents/skills/readme-site')
result={'archive_members_verified':len(raw),'successful_matrix':rows,'global_skill_unchanged':baseline,'codex_skill_symlink_verified':True}
(HERE/'audit.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'archive_members_verified':len(raw),'passing_rows':len(rows),'root_proofs':sum(r['client_root_proofs']+r['server_root_proofs'] for r in rows)}))


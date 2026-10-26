"""Read-only consistency audit; no product verdict or original member changes."""
from pathlib import Path,PurePosixPath
import hashlib,io,json,subprocess,tarfile
ROOT=Path(__file__).resolve().parents[3];HERE=Path(__file__).resolve().parent
def load(p):return json.loads(p.read_text())
def sha(b):return hashlib.sha256(b).hexdigest()
raw={}
with tarfile.open(HERE/'originals.tar.gz','r:gz') as tar:
 for row in load(HERE/'originals-manifest.json')['members']:
  assert not PurePosixPath(row['path']).is_absolute() and '..' not in PurePosixPath(row['path']).parts
  b=tar.extractfile(row['path']).read();assert len(b)==row['bytes'] and sha(b)==row['sha256'];raw[row['path']]=b
rows=[]
for release in ['jammy','noble']:
 prefix='guest/'+release+'/'
 def value(name):return json.loads(raw[prefix+name])
 with tarfile.open(fileobj=io.BytesIO(raw['runs/'+release+'/guest-evidence.tar'])) as tar:
  for member in tar.getmembers():assert not PurePosixPath(member.name).is_absolute() and '..' not in PurePosixPath(member.name).parts and (member.isfile() or member.isdir())
 uid=value('uid.json')['uid'];blocked=value('blocked-package-state.json');executed=value('executed-fsync-library.json');assert uid>0 and executed['uid']==uid
 events=[json.loads(l) for l in raw[prefix+'fsync-gate.jsonl'].decode().splitlines()]
 entered=[r for r in events if r['phase']=='entered'];returned=[r for r in events if r['phase']=='returned']
 assert len(entered)==len(returned)==1 and entered[0]['uid']==uid and entered[0]['pid']==returned[0]['pid']==blocked['broker_pid'] and returned[0]['result']==0
 assert blocked['version']=='0.1.0~ci25+g0316de7638c6-1'
 library=json.loads(raw['runs/'+release+'/fsync-gate-library.json']);assert library['sha256']==executed['sha256'] and library['source_sha256']==sha(raw['source/76b4e7a35c5fae0308c24c538c66617745247137/scripts/qemu_package/fsync-gate.c'])
 checks=[json.loads(l) for l in raw[prefix+'checks.jsonl'].decode().splitlines()];assert all(r['passed'] for r in checks)
 assert value('gui-exit.json')=={'exit':0,'wm_delete_window':True}
 rows.append({'release':release,'actual_broker_uid':uid,'original_broker_pid':blocked['broker_pid'],'actual_fsync_fd_inode':entered[0]['inode'],'actual_fsync_return':0,'actual_executed_interposer_sha256':executed['sha256'],'original_source_sha256':library['source_sha256'],'check_records':len(checks),'distinct_check_labels':len({r['label'] for r in checks})})
assert not subprocess.check_output(['git','diff','--name-only','0316de7638c68b9360ec719194e9e0c4ae0fe454','76b4e7a35c5fae0308c24c538c66617745247137','--','crates','apps','vendor','packaging','Cargo.toml','Cargo.lock','rust-toolchain.toml','.github'],cwd=ROOT,text=True).strip()
baseline=load(ROOT/'docs/evidence/tui-interactions-docs-20261025/summary.json')['global_skill_unchanged']
for n,record in baseline.items():
 b=(Path('/home/ubuntu/.agents/skills/readme-site')/n).read_bytes();assert len(b)==record['bytes'] and sha(b)==record['sha256']
assert Path('/home/ubuntu/.codex/skills/readme-site').resolve()==Path('/home/ubuntu/.agents/skills/readme-site')
result={'archive_members_verified':len(raw),'runs':rows,'product_source_and_package_scripts_unchanged_CI0316_to_fixture76b4':True,'global_skill_unchanged':baseline,'codex_skill_symlink_verified':True,'scope':'Actual UID/PID/C-source/library/check consistency; not per-live-GUI /proc/ELF attestation, USB release proof or independent implementation review.'}
(HERE/'audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'members':len(raw),'broker_uids':[r['actual_broker_uid'] for r in rows],'check_records':[r['check_records'] for r in rows],'distinct_labels_each':42,'source_bridge':True,'global_skill_unchanged':True}))

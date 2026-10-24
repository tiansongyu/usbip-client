#!/usr/bin/env python3
"""Read source/cache bytes and emit provenance; no builds or application execution."""
from pathlib import Path
import hashlib,json,re,tarfile
HERE=Path(__file__).resolve().parent
ROOT=Path('/home/ubuntu/github/usbip-client')
CACHE=Path('/home/ubuntu/.cargo/registry')
REG='index.crates.io-1949cf8c6b5b557f'
def digest(path):
 b=path.read_bytes()
 return {'path':str(path),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'content_git_blob_sha1':hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()}
repository_files=['AGENTS.md','.agents/skills/rustusb-development/SKILL.md','Cargo.lock','Cargo.toml','rust-toolchain.toml','docs/requirements.md','docs/architecture.md','docs/security.md','docs/research/rotating-logs-design.md','docs/research/diagnostic-export-design.md','crates/rustusb-logging/src/storage/linux.rs','crates/rustusb-service/src/fs.rs','crates/rustusb-service/src/store.rs','crates/rustusb-native/src/diagnostics.rs','crates/rustusb-daemon/src/diagnostics.rs']
repo=[digest(ROOT/p) for p in repository_files if (ROOT/p).is_file()]
lock=(ROOT/'Cargo.lock').read_text()
packages=[]
for name,version,consulted in [('serde_json','1.0.154',['src/ser.rs']),('rustix','1.1.5',['src/fs/at.rs','src/fs/fd.rs','src/fs/openat2.rs','src/backend/linux_raw/fs/types.rs']),('tokio','1.54.1',['src/task/blocking.rs','src/runtime/task/join.rs','src/runtime/runtime.rs','src/runtime/blocking/pool.rs','src/fs/mod.rs','src/process/mod.rs'])]:
 pkg=f'{name}-{version}'; source=CACHE/'src'/REG/pkg;archive=CACHE/'cache'/REG/f'{pkg}.crate'
 block=re.search(r'\[\[package\]\]\nname = "'+name+r'"\nversion = "'+re.escape(version)+r'".*?(?=\n\[\[package\]\]|\Z)',lock,re.S).group()
 expected=re.search(r'checksum = "([^"]+)"',block).group(1)
 archive_data=digest(archive)
 item={'name':name,'version':version,'license':re.search(r'^license = "([^"]+)"',(source/'Cargo.toml').read_text(),re.M).group(1),'cargo_lock_archive_sha256':expected,'archive':archive_data,'archive_matches_lock':archive_data['sha256']==expected,'vcs':json.loads((source/'.cargo_vcs_info.json').read_text()),'files':[]}
 with tarfile.open(archive) as tf:
  for rel in consulted+['Cargo.toml','.cargo_vcs_info.json']+[p.name for p in sorted(source.glob('LICENSE*'))]:
   f=source/rel;d=digest(f);d['matches_locked_archive']=tf.extractfile(pkg+'/'+rel).read()==f.read_bytes();item['files'].append(d)
 packages.append(item)
std=[digest(p) for p in sorted((HERE/'upstream').iterdir()) if p.is_file()]
manifest={'schema_version':1,'kind':'read_only_fixed_source_research','reported_baseline':'46cd950 (parent supplied; no Git executed)','repository_files':repo,'packages':packages,'std':{'version':'1.97.1','ref':json.loads((HERE/'rust-fixed-ref.json').read_text()),'files':std},'boundaries':{'application_runs':0,'cargo_or_builds':0,'qemu_runs':0,'repository_edits':0,'git_commands':0,'new_dependencies':0,'filesystem_test_results':None},'notes':['Source equality is checked against the locked .crate archives; .cargo-checksum.json is absent in this installed cache and is not claimed as evidence.','Std exact-source tag fetch succeeded; earlier versioned API-doc requests failed, and current unversioned std1.99.1 docs are excluded from exact-version conclusions.','content_git_blob_sha1 is calculated from consulted bytes; it is not an assertion about the current Git HEAD.']}
(HERE/'provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
assert all(p['archive_matches_lock'] and all(f['matches_locked_archive'] for f in p['files']) for p in packages)
print(json.dumps({'repository_files':len(repo),'packages':len(packages),'locked_source_files':sum(len(p['files']) for p in packages),'all_lock_and_source_comparisons':True}))

from pathlib import Path
import hashlib,json,re,subprocess
repo=Path('/home/ubuntu/github/usbip-client')
base=Path('/tmp/rustusb-secure-foundation-software-20261023')
metadata=json.loads((base/'metadata.json').read_text())
features=(base/'transport-features.txt').read_text()
packages={p['id']:p for p in metadata['packages']}
nodes={n['id']:n for n in metadata['resolve']['nodes']}
transport=next(p for p in packages.values() if p['name']=='rustusb-transport')
checks=[]
def check(name,ok,detail=None):checks.append({'check':name,'pass':bool(ok),'detail':detail})
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def lock_packages(text):
 out={}
 for block in text.split('[[package]]')[1:]:
  fields=dict(re.findall(r'^(name|version|source|checksum) = "([^"]+)"$',block,re.M))
  if 'name' in fields and 'version' in fields:out[(fields['name'],fields['version'])]=fields
 return out
lock=lock_packages((repo/'Cargo.lock').read_text())
old=lock_packages(subprocess.check_output(['git','show','a7406d7080f6b3d9a73d1ea36a16d300eee0eb55:Cargo.lock'],cwd=repo,text=True))
changed=sorted(set(lock)-set(old))
source_files=[]
package_records=[]
registry_cache=Path('/home/ubuntu/.cargo/registry/cache/index.crates.io-1949cf8c6b5b557f')
for p in sorted(packages.values(),key=lambda x:(x['name'],x['version'])):
 key=(p['name'],p['version']);f=Path(p['manifest_path']);node=nodes.get(p['id'],{})
 if key not in changed and p['name'] not in ['rustls','tokio-rustls','ring','rcgen','rustls-webpki','rustls-pki-types']:continue
 check('lock matches metadata '+p['name']+' '+p['version'],key in lock)
 check('SPDX present '+p['name']+' '+p['version'],bool(p['license'] or p['license_file']),p['license'])
 licenses=[]
 if f.exists():
  paths=[x for x in f.parent.iterdir() if x.is_file() and any(s in x.name.lower() for s in ['license','copying','notice'])]
  if p['name']=='ring':paths+=list((f.parent/'src/polyfill/once_cell').glob('LICENSE-*'))
  for x in sorted(set(paths)):
   licenses.append({'path':str(x),'sha256':sha(x),'bytes':x.stat().st_size})
  source_files.append({'path':str(f),'sha256':sha(f)})
 archive=registry_cache/(p['name']+'-'+p['version']+'.crate')
 archive_fact=None
 if p['source']:
  checksum=lock.get(key,{}).get('checksum')
  if archive.exists():
   actual=sha(archive);check('archive checksum '+p['name']+' '+p['version'],actual==checksum)
   archive_fact={'path':str(archive),'sha256':actual,'lock_checksum':checksum}
  else:archive_fact={'available':False}
 package_records.append({'name':p['name'],'version':p['version'],'license':p['license'],'rust_version':p['rust_version'],'resolve_features':node.get('features'),'licenses':licenses,'archive':archive_fact})
expected={'rustls':('0.23.46',['ring','std'],'Apache-2.0 OR ISC OR MIT'),'tokio-rustls':('0.26.6',['ring'],'MIT OR Apache-2.0'),'ring':('0.17.14',['alloc','default','dev_urandom_fallback'],'Apache-2.0 AND ISC'),'rustls-webpki':('0.103.16',['alloc','ring','std'],'ISC'),'rustls-pki-types':('1.15.1',['alloc','default','std'],'MIT OR Apache-2.0'),'rcgen':('0.14.10',['crypto','ring'],'MIT OR Apache-2.0')}
for name,(version,enabled,license) in expected.items():
 p=next(x for x in packages.values() if x['name']==name)
 check(name+' exact version/license',p['version']==version and p['license']==license)
 check(name+' exact resolved features',nodes[p['id']]['features']==enabled,nodes[p['id']]['features'])
check('no aws-lc locked',[x for x in lock if x[0] in ['aws-lc-rs','aws-lc-sys']]==[])
check('rcgen only direct dev edge',all(d['kind']=='dev' for d in transport['dependencies'] if d['name']=='rcgen') and any(d['name']=='rcgen' for d in transport['dependencies']))

def closure(root):
 seen=set();stack=[root]
 while stack:
  k=stack.pop()
  if k in seen:continue
  seen.add(k)
  for dep in nodes[k]['deps']:
   if any(x['kind']!='dev' for x in dep['dep_kinds']):stack.append(dep['pkg'])
 return seen
production=closure(transport['id'])
check('rcgen absent normal/build graph',all(packages[k]['name']!='rcgen' for k in production))
check('certificate generation helpers absent normal/build graph',all(packages[k]['name'] not in ['time','yasna','x509-parser'] for k in production))
normal_tree=features.split('[dev-dependencies]')[0]
check('feature tree dev boundary',features.count('[dev-dependencies]')==1)
check('feature tree rcgen only dev','rcgen v0.14.10' not in normal_tree and 'rcgen v0.14.10' in features)
check('feature tree no excluded crypto features',not any(s in features for s in ['feature "tls12"','feature "aws_lc_rs"','feature "aws-lc-rs"','feature "fips"','feature "early-data"','feature "brotli"','feature "zlib"']))
feature_tree_packages=sorted(set(re.findall(r'\b([A-Za-z0-9_-]+) v([0-9][A-Za-z0-9_.+-]*)',features)))
normal_tree_packages=sorted(set(re.findall(r'\b([A-Za-z0-9_-]+) v([0-9][A-Za-z0-9_.+-]*)',normal_tree)))
check('source manifest dev dependency matches',re.search(r'\[dev-dependencies\]\s*rcgen\.workspace = true',(repo/'crates/rustusb-transport/Cargo.toml').read_text()) is not None)
third=(repo/'docs/third-party.md').read_text()
for name,terms in {'rustls':['0.23.46 / 0.26.6','Apache-2.0 OR ISC OR MIT','MIT OR Apache-2.0'],'ring':['0.17.14 / 0.103.16 / 1.15.1','Apache-2.0 AND ISC','BoringSSL','unsafe','FIPS'],'rcgen':['0.14.10','仅新transport测试','不作为产品证书签发工具']}.items():
 line=next(x for x in third.splitlines() if x.startswith('| '+name+' '))
 check('third-party row '+name,all(s in line for s in terms),line)
critical=['ring-0.17.14/LICENSE','ring-0.17.14/LICENSE-BoringSSL','ring-0.17.14/LICENSE-other-bits','ring-0.17.14/src/polyfill/once_cell/LICENSE-APACHE','ring-0.17.14/src/polyfill/once_cell/LICENSE-MIT','ring-0.17.14/src/rand.rs','ring-0.17.14/build.rs','getrandom-0.2.17/src/lib.rs','rustls-0.23.46/src/conn.rs','rustls-0.23.46/src/msgs/deframer/handshake.rs']
registry=Path('/home/ubuntu/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f')
source_files.extend({'path':str(registry/x),'sha256':sha(registry/x)} for x in critical)
inventory=json.loads((repo/'docs/dependency-inventory.json').read_text())
keys=['name','version','license','license_file','source','repository','rust_version']
expected_inventory=sorted(({key:p.get(key) for key in keys} for p in packages.values()),key=lambda p:(p['name'],p['version']))
check('148-entry inventory matches metadata fields',sorted(inventory,key=lambda p:(p['name'],p['version']))==expected_inventory,{'inventory_count':len(inventory),'metadata_count':len(packages)})
inputs=[repo/'Cargo.toml',repo/'Cargo.lock',repo/'docs/third-party.md',repo/'docs/dependency-inventory.json',repo/'crates/rustusb-transport/Cargo.toml',base/'metadata.json',base/'transport-features.txt']
report={'status':'PASS' if all(c['pass'] for c in checks) else 'FAIL','check_count':len(checks),'passed':sum(c['pass'] for c in checks),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),'review_type':'Read-only dependency/source review; no Cargo/QEMU/USB/network execution by reviewer','inputs':[{'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p)} for p in inputs],'checks':checks,'lock_comparison_baseline':'a7406d7080f6b3d9a73d1ea36a16d300eee0eb55','new_lock_entries':changed,'package_records':package_records,'normal_build_metadata_graph_all_targets_count':len(production),'normal_build_metadata_graph_all_targets':sorted((packages[k]['name'],packages[k]['version']) for k in production),'recorded_host_feature_tree_normal_packages':normal_tree_packages,'recorded_host_feature_tree_all_packages':feature_tree_packages,'source_files':source_files,'findings':[
 {'severity':'none','statement':'Three new third-party rows accurately state pinned versions, Cargo SPDX metadata, ring mixed-source license boundary and dev-only rcgen scope.'},
 {'severity':'boundary','statement':'ring is not pure Rust: C/assembly, build-time cc and platform-specific crypto paths remain transitive dependencies. No FIPS claim or arbitrary target compatibility is established.'},
 {'severity':'boundary','statement':'ring default alloc/dev_urandom_fallback features are enabled transitively. Current Rustls/Tokio defaults are disabled; this is not a claim that all transitive defaults are disabled.'},
 {'severity':'boundary','statement':'ring SystemRandom delegates to OS getrandom, and its first fill may synchronously block. Tokio five-second timeout is an asynchronous deadline, not preemption of synchronous crypto/RNG work inside a poll.'},
 {'severity':'boundary','statement':'set_buffer_limit(65536) bounds the two write-direction Rustls buffers separately. It is not a 64KiB total-connection RSS cap, does not cap all peer-certificate/crypto allocations, and does not replace application backpressure/deadlines.'},
 {'severity':'boundary','statement':'Rustls fixed handshake reassembly MAX_HANDSHAKE_SIZE=0xffff is source evidence. No load/fuzz/RSS or timing measurement was executed by this reviewer.'},
 {'severity':'boundary','statement':'Production normal/build metadata graph excludes rcgen/time/yasna/x509-parser. Metadata/lock include all-target and optional edges; recorded host feature tree is narrower and excludes x509-parser. Neither lock presence nor metadata superset alone proves a package was compiled into a production artifact.'},
 {'severity':'boundary','statement':'Source versions have MSRV metadata (ring1.66, rustls/tokio1.71, pki1.60, zeroize1.85, rcgen/time1.88); workspace1.97 exceeds them. Native GNU/musl x86_64/aarch64 builds still need actual per-target CI. This review is not a new platform PASS.'},
 {'severity':'boundary','statement':'No binary package is being distributed by this review. Full source license notices (including ring once_cell/BoringSSL portions) remain relevant to a later redistribution audit; metadata SPDX is not a substitute for per-file notices.'}
],'not_performed':['Cargo metadata/tree/build/test by reviewer','Dependency installation or network fetch','QEMU/USB or TLS runtime execution','Security vulnerability advisory audit','Full per-file licensing/legal determination','Repository edits']}
out=Path('/tmp/rustusb-transport-dependency-review-20261023.json');out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'path':str(out),'status':report['status'],'checks':len(checks),'passed':report['passed'],'bytes':out.stat().st_size,'sha256':sha(out),'failed':[c for c in checks if not c['pass']]},ensure_ascii=False))

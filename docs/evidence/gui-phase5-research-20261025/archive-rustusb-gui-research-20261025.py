from pathlib import Path
import json,hashlib,shutil,subprocess
repo=Path('/home/ubuntu/github/usbip-client');target=repo/'docs/evidence/gui-phase5-research-20261025';assert not subprocess.check_output(['git','status','--porcelain'],cwd=repo).strip();target.mkdir(exist_ok=False)
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
roles=[('tauri',Path('/tmp/rustusb-gui-tauri-research-20261025.IOkCL9')),('linux',Path('/tmp/rustusb-gui-linux-runtime-research-20261025'))]
rows=[];copies=[]
for label,root in roles:
 manifest=json.loads((root/'manifest.json').read_text());items=manifest.get('files',manifest.get('payload'));assert items
 for item in items:
  p=(root/item['path']).resolve();assert p.is_relative_to(root.resolve())and p.is_file()and p.stat().st_size==item['bytes']and sha(p)==item['sha256']
 selected=['manifest.json','SHA256SUMS']
 if label=='tauri':
  selected+=['DIGEST.md','API.md','method/README.md','upstream/LICENSE.spdx','upstream/LICENSE-MIT','upstream/LICENSE-APACHE-2.0','public/release-tauri-2.12.5.json','public/gitref-tauri-2.12.5.json','public/github-core-commit.json','public/crate-tauri-2.12.5.json']
  selected +=[str(p.relative_to(root))for p in sorted((root/'method').glob('*fetch.json'))]
 else:selected+=['README.md','digest.md','source-index.json','reference-pins.json','ubuntu-published-matrix.json']
 for name in selected:
  p=root/name;data=p.read_bytes();assert not data.startswith(b'\x7fELF')
  dest=target/label/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
  copies.append({'path':str(dest.relative_to(target)),'original':str(p),'bytes':len(data),'sha256':sha(p)})
 rows.append({'component':label,'original_directory':str(root),'payload_files_verified':len(items),'payload_bytes_verified':sum(x['bytes']for x in items),'original_manifest_sha256':sha(root/'manifest.json'),'original_checksum_file_sha256':sha(root/'SHA256SUMS'),'copied_files':len(selected)})
shutil.copyfile(__file__,target/Path(__file__).name);copies.append({'path':Path(__file__).name,'original':__file__,'bytes':Path(__file__).stat().st_size,'sha256':sha(Path(__file__))})
for row in copies:assert sha(target/row['path'])==row['sha256']and sha(Path(row['original']))==row['sha256']
result={'result':'PASS','components':rows,'files':copies,'scope':'Same-author integrity review of two independently delegated read-only component research tasks. Selected digests/API map/metadata/licenses copied. Full public source snapshots remain in the original directories and are NOT included in this repository evidence folder; original manifests enumerate them. No GUI build, installation, execution, security audit or hardware proof. No new dependency or accepted design.'}
(target/'review.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'result':'PASS','components':rows,'copied_files':len(copies),'copied_bytes':sum(r['bytes']for r in copies)}))

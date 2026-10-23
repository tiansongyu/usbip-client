from pathlib import Path
import hashlib,json,shutil
base=Path('docs/evidence/secure-usbip-final-b83c931-20261023');base.mkdir(exist_ok=False);entries=[];runs=[]
for backend in ['virtual','usbfs','native']:
 for family in ['ipv4','ipv6']:
  label=f'{backend}-{family}';source=Path(f'/tmp/rustusb-secure-qemu-final-{label}-20261023');d=json.loads((source/'summary.json').read_text());assert d['result']=='PASS' and d['completed_utc'];assert d['head']=='b83c931afb1789ae178bf49c6402f996fc9fb3c5';assert d['guest_exit_codes']=={'server':0,'client':0};assert all(d[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']);assert len(d['source_inputs'])==125
  runs.append({'backend':backend,'family':family,'summary':f'{label}/summary.json','result':d['result'],'head':d['head'],'guest_exit_codes':d['guest_exit_codes'],'source_input_count':len(d['source_inputs']),'binary_sha256':d['binary_sha256'],'guards':{k:d[k] for k in ['source_guard','binary_guard','head_guard','clean_guard']}})
  sources=[(p,Path(label)/p.relative_to(source)) for p in sorted(source.rglob('*')) if p.is_file()];sources.append((Path(f'/tmp/rustusb-secure-qemu-final-{label}-20261023.log'),Path(label)/'driver.log'))
  for p,rel in sources:
   assert not p.name.endswith(('-key.der','-key.pem'))
   q=base/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);b=p.read_bytes();assert b==q.read_bytes();entries.append({'path':rel.as_posix(),'original':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
for p in [Path('/tmp/archive-rustusb-secure-final-b83c931-20261023.py'),Path('/tmp/rustusb-secure-final-doc-author-review-20261023.json')]:
 q=base/p.name;shutil.copyfile(p,q);b=p.read_bytes();entries.append({'path':p.name,'original':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
manifest={'scope':'Six actual cleanb83c931 paired-QEMU secure USB/IP runs, 12 guest exits0. Normal cooperative I/O/cleanup/reuse, not physical hardware/fullPhase3/crash/ISO acceptance. Only public DER retained; temporary keys and initramfs images removed. Independent raw reviews archived separately after completion.','runs':runs,'files':entries}
(base/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n');print({'runs':len(runs),'files':len(entries),'bytes':sum(f['bytes'] for f in entries)})

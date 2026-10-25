from pathlib import Path
import datetime,hashlib,json,shutil,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client');HEAD='84b751d2d9d1bd2c0eb4ceba8dc65855e66d8fa4'
DEST=ROOT/'docs/evidence/debian-publication-84b751d2-20261025'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT)
assert git('rev-parse','HEAD').decode().strip()==HEAD and not git('status','--porcelain').strip()
runs=json.load(open('/tmp/rustusb-debian-final-runs-03-20261025.json'))['structuredContent']['content'];runs=json.loads(runs)['workflow_runs']
assert len(runs)==3 and all(r['head_sha']==HEAD and r['status']=='completed' and r['conclusion']=='success' for r in runs)
jobs={}
for rid in [40192148876,40192148818,40192148853]:
 rows=json.load(open(f'/tmp/rustusb-debian-final-jobs-{rid}-03-20261025.json'))['structuredContent']['jobs']
 assert len(rows)==(2 if rid==40192148853 else 4)
 assert all(j['run_id']==rid and j['status']=='completed' and j['conclusion']=='success' for j in rows)
 for j in rows:
  required=(['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation'] if rid==40192148876 else ['Verify architecture and actual GTK/WebKit versions','GUI formatting, Clippy, tests, release and rustdoc','Build matching base and native preview Debian packages','Upload native preview packages and build receipts'] if rid==40192148818 else ['Check site assets and documentation links'] if j['name']=='check' else ['Deploy website'])
  for name in required:assert any(s['name']==name and s['status']=='completed' and s['conclusion']=='success' for s in j['steps'])
 jobs[str(rid)]=[{'id':j['id'],'name':j['name'],'conclusion':j['conclusion']} for j in rows]
inspection=json.load(open('/tmp/rustusb-debian-final-artifact-inspection-20261025.json'))
assert inspection['passed'] and len(inspection['artifacts'])==4 and all(a['source_head']==HEAD and a['passed'] for a in inspection['artifacts'])
http=Path('/tmp/rustusb-debian-publication-84b751d2-20261025');live=json.loads((http/'http-summary.json').read_text());assert live['passed'] and live['content_head']==HEAD
soft=json.load(open('/tmp/rustusb-debian-signals-software-20261025/summary.json'))
qa=json.loads((ROOT/'docs/evidence/debian-stop-signals-20261025/qa.json').read_text())
for n,meta in soft['binaries'].items():assert sha(ROOT/('apps/rustusb-gui/target/release/rustusb-gui' if n=='rustusb-gui' else 'target/release/'+n))==meta['sha256']
for n,meta in soft['inputs'].items():
 original=git('show',soft['head']+':'+n);assert hashlib.sha256(original).hexdigest()==meta['sha256']
 if n not in qa['changed_inputs_since_gate']:assert (ROOT/n).read_bytes()==original,n
for n,h in qa['site_files'].items():assert sha(ROOT/n)==h
for n,meta in qa['global_skill_unchanged'].items():assert sha(Path('/home/ubuntu/.agents/skills/readme-site')/n)==meta['sha256']
assert Path('/home/ubuntu/.codex/skills/readme-site').resolve()==Path('/home/ubuntu/.agents/skills/readme-site')
DEST.mkdir(parents=True,exist_ok=False)
def copy(source,target):
 source=Path(source);dest=DEST/target;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,dest)
 if not target.startswith('methods/'):
  assert b'-----BEGIN PRIVATE KEY-----' not in dest.read_bytes() and b'?se=' not in dest.read_bytes(),dest
for pattern in ['rustusb-debian-final-runs-*.json','rustusb-debian-final-jobs-*.json','rustusb-debian-final-artifacts-*.json','rustusb-debian-publication-runs-*.json','rustusb-debian-publication-jobs-*.json','rustusb-debian-publication-artifacts-e875*.json']:
 for p in sorted(Path('/tmp').glob(pattern)):copy(p,'api/'+p.name)
for p in sorted(http.rglob('*')):
 if p.is_file():copy(p,'live/'+str(p.relative_to(http)))
copy('/tmp/rustusb-debian-final-artifact-inspection-20261025.json','artifact-inspection.json')
copy('/tmp/rustusb-debian-final-downloads-20261025/download-receipts.json','download-receipts.json')
copy('/tmp/rustusb-debian-intermediate-artifact-probe-20261025.json','intermediate-e875-artifact-probe.json')
for name in ['download-rustusb-final-artifacts-20261025.py','inspect-rustusb-ci-debian-artifacts-20261025.py','check-rustusb-debian-live-20261025.py','collect-rustusb-debian-publication-84b751d2-20261025.py']:
 copy(Path('/tmp')/name,'methods/'+name)
receipt={'schema_version':1,'content_head':HEAD,'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'workflows':[{'id':r['id'],'name':r['name'],'conclusion':r['conclusion'],'html_url':r['html_url']} for r in runs],'jobs':jobs,'four_ci_artifacts_eight_debs_read_only_inspected':True,'artifacts':inspection['artifacts'],'canonical_https_assets_exact':len(live['http']),'local_software_head':soft['head'],'local_source_inputs_mapped':len(soft['inputs']),'local_three_gate_elfs_unchanged':True,'site_files_unchanged':len(qa['site_files']),'global_skill_files_unchanged':len(qa['global_skill_unchanged']),'scope':'Actual CI native architecture/full-gate/package/upload step metadata, downloaded ZIP/deb/ELF architecture and license identity, canonical HTTPS/Git bytes. CI ELFs were not executed or installed; no per-platform test recount or new hardware/arm64 installation acceptance. Temporary download capabilities, binaries and VM images excluded.'}
(DEST/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
manifest={str(p.relative_to(DEST)):{'bytes':p.stat().st_size,'sha256':sha(p)} for p in sorted(DEST.rglob('*')) if p.is_file()}
(DEST/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({'content_head':HEAD,'manifest_members':len(manifest),'bytes':sum(v['bytes'] for v in manifest.values()),'workflows':len(runs),'native_ci_jobs':8,'inspected_zip_artifacts':4,'inspected_debs':8,'https_assets':len(live['http'])}))

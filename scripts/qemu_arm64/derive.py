"""Derive same-source higher revisions, proving the only control-byte changes."""
from pathlib import Path
import argparse,hashlib,io,json,subprocess,tarfile
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
BASE=args.source.resolve()
OUT=args.output.resolve();OUT.mkdir()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def members(package,control=False):
 b=subprocess.check_output(['dpkg-deb','--ctrl-tarfile' if control else '--fsys-tarfile',str(package)])
 with tarfile.open(fileobj=io.BytesIO(b),mode='r:') as tar:
  return {m.name:{'mode':m.mode,'uid':m.uid,'gid':m.gid,'type':m.type.decode(),'linkname':m.linkname,'data':tar.extractfile(m).read() if m.isfile() else None} for m in tar.getmembers()}
for release in ['jammy','noble']:
 source=BASE/release;dest=OUT/release;dest.mkdir();build=next(source.glob('build-*.json'));original=json.loads(build.read_text());old=original['version'];new=old+'+armtest1'
 assert original['architecture']=='arm64' and not original['git_status'] and len(original['packages'])==2
 assert subprocess.run(['dpkg','--compare-versions',new,'gt',old]).returncode==0
 rows=[]
 for record in original['packages']:
  assert Path(record['file']).name==record['file']
  package=source/record['file'];assert sha(package)==record['sha256'];name=subprocess.check_output(['dpkg-deb','-f',str(package),'Package'],text=True).strip();work=dest/('.work-'+name)
  assert name in ['rustusb','rustusb-gui']
  subprocess.run(['dpkg-deb','-R',str(package),str(work)],check=True,stdout=subprocess.DEVNULL)
  control=work/'DEBIAN/control';text=control.read_text();anchor='Version: '+old+'\n';assert text.count(anchor)==1;text=text.replace(anchor,'Version: '+new+'\n')
  if name=='rustusb-gui':
   anchor='rustusb (= '+old+')';assert text.count(anchor)==1;text=text.replace(anchor,'rustusb (= '+new+')')
  control.write_text(text);derived=dest/(name+'_'+new+'_arm64.deb')
  subprocess.run(['dpkg-deb','--root-owner-group','--build',str(work),str(derived)],check=True,stdout=subprocess.DEVNULL)
  data_old=members(package);data_new=members(derived);assert data_old==data_new,'payload identity'
  controls_old=members(package,True);controls_new=members(derived,True);assert set(controls_old)==set(controls_new)
  differences=[]
  for n,a in controls_old.items():
   b=controls_new[n];assert {k:v for k,v in a.items() if k!='data'}=={k:v for k,v in b.items() if k!='data'}
   if a['data']!=b['data']:
    assert n=='./control';assert b['data']==text.encode();differences.append(n)
  assert differences==['./control']
  rows.append({'original_file':package.name,'original_sha256':sha(package),'derived_file':derived.name,'derived_sha256':sha(derived),'derived_bytes':derived.stat().st_size,'data_members_identical':len(data_old),'only_control_member_changed':differences,'payload_bytes_modes_owners_links_identical':True,'all_maintainer_scripts_identical':True})
 receipt={'schema_version':1,'generator_sha256':sha(Path(__file__)),'derivation':'control-only Debian revision increase; not a native rebuild','original_source_head':original['git_head'],'original_CI_build_receipt_sha256':sha(build),'original_version':old,'derived_version':new,'modifications':['control Version','GUI exact rustusb Depends version'],'unchanged_binaries':original['binaries'],'packages':rows,'executed_by_derivation':False}
 (dest/'derivation.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps({'release':release,'derived_version':new,'packages':len(rows),'all_payload_and_scripts_identical':True}),flush=True)

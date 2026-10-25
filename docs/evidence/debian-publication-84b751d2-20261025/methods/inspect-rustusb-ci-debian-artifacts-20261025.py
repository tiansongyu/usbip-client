import argparse, hashlib, io, json, pathlib, subprocess, tarfile, tempfile, zipfile
parser=argparse.ArgumentParser();parser.add_argument('--metadata',type=pathlib.Path,required=True);parser.add_argument('--downloads',type=pathlib.Path,required=True);parser.add_argument('--head',required=True);parser.add_argument('--output',type=pathlib.Path,required=True);args=parser.parse_args()
ROOT=pathlib.Path('/home/ubuntu/github/usbip-client')
raw=json.loads(args.metadata.read_text());artifacts=raw['structuredContent']['artifacts'];assert len(artifacts)==4;results=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def git(path):return subprocess.check_output(['git','show',args.head+':'+path],cwd=ROOT)
for item in artifacts:
 archive=args.downloads/(item['name']+'.zip');body=archive.read_bytes()
 assert sha(body)==item['digest'].removeprefix('sha256:') and len(body)==item['size_in_bytes']
 with zipfile.ZipFile(io.BytesIO(body)) as z:
  names=z.namelist();assert len(names)==3 and all(pathlib.PurePosixPath(n).name==n for n in names)
  receipt=json.loads(z.read(next(n for n in names if n.endswith('.json'))))
  assert receipt['git_head']==args.head and not receipt['git_status'] and len(receipt['packages'])==2
  arch=receipt['architecture'];assert arch in ['amd64','arm64']
  triple={'amd64':'x86_64-unknown-linux-gnu','arm64':'aarch64-unknown-linux-gnu'}[arch]
  machine={'amd64':'Advanced Micro Devices X86-64','arm64':'AArch64'}[arch]
  checked=[]
  for package in receipt['packages']:
   data=z.read(package['file']);assert sha(data)==package['sha256'] and len(data)==package['bytes']
   with tempfile.TemporaryDirectory(prefix='rustusb-ci-inspection-') as tmp:
    deb=pathlib.Path(tmp)/'package.deb';deb.write_bytes(data)
    name=subprocess.check_output(['dpkg-deb','-f',str(deb),'Package'],text=True).strip()
    assert subprocess.check_output(['dpkg-deb','-f',str(deb),'Architecture'],text=True).strip()==arch
    assert subprocess.check_output(['dpkg-deb','-f',str(deb),'Version'],text=True).strip()==receipt['version']
    payload=subprocess.check_output(['dpkg-deb','--fsys-tarfile',str(deb)])
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
     members=tar.getmembers();files={m.name.removeprefix('./'):tar.extractfile(m).read() for m in members if m.isfile()}
    assert all(m.uid==0 and m.gid==0 and not m.mode&0o7000 for m in members)
    assert not any(n.startswith(('etc/','var/','home/','usr/local/')) for n in files)
    elfs={'usr/bin/rustusb-gui':'gui'} if name=='rustusb-gui' else {'usr/bin/rustusb':'cli','usr/libexec/rustusb/rustusb-helper':'helper'}
    for path,key in elfs.items():
     actual=files[path];expected=receipt['binaries'][key];assert sha(actual)==expected['sha256'] and len(actual)==expected['bytes']
     p=pathlib.Path(tmp)/'inspect.elf';p.write_bytes(actual)
     header=subprocess.check_output(['readelf','-h',str(p)],text=True)
     assert any(line.split(':',1)[0].strip()=='Machine' and line.split(':',1)[1].strip()==machine for line in header.splitlines() if ':' in line)
    if name=='rustusb':
     assert files['usr/libexec/rustusb/package-control']==git('packaging/debian/package-control')
     for unit in ['rustusb-helper.service','rustusb-agent.service']:assert files['lib/systemd/system/'+unit]==git('packaging/systemd/'+unit)
    else:assert files['usr/share/applications/rustusb-gui.desktop']==git('packaging/debian/rustusb-gui.desktop')
    prefix='usr/share/doc/'+name+'/third-party/';inventory=json.loads(files[prefix+'inventory.json'])
    assert inventory['source_head']==args.head and inventory['target']==triple
    for p in inventory['packages']:
     for license in p['files']:assert sha(files[prefix+p['name']+'-'+p['version']+'/'+license['file']])==license['sha256']
    checked.append({'name':name,'sha256':sha(data),'members':len(members),'license_nodes':len(inventory['packages']),'dependencies':package['depends']})
 results.append({'artifact_id':item['id'],'artifact_name':item['name'],'zip_sha256':sha(body),'zip_bytes':len(body),'source_head':args.head,'architecture':arch,'version':receipt['version'],'binaries':receipt['binaries'],'packages':checked,'passed':True})
args.output.write_text(json.dumps({'passed':True,'scope':'Read-only actual CI ZIP/deb/ELF architecture, source and license identity; no execution or installation of CI ELFs.','artifacts':results},indent=2)+'\n')
print(json.dumps({'passed':True,'artifacts':len(results),'packages':sum(len(x['packages']) for x in results),'source_head':args.head}))

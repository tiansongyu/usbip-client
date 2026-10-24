from pathlib import Path
import subprocess,json,hashlib,sys
paths=[]
for name in sys.argv[1:]:
    root=Path('docs/evidence')/name
    for r in json.loads((root/'manifest.json').read_text())['files']:
        p=root/r['path'];b=p.read_bytes()
        assert len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['sha256'],p
        if r['original']!='generated archive scope':assert Path(r['original']).read_bytes()==b,p
        paths.append(str(p))
    paths.append(str(root/'manifest.json'))
subprocess.run(['git','add','-f','--pathspec-from-file=-','--pathspec-file-nul'],input=('\0'.join(paths)+'\0').encode(),check=True)
data=subprocess.check_output(['git','cat-file','--batch'],input=''.join(':'+p+'\n' for p in paths).encode())
offset=0
for p in paths:
    end=data.index(b'\n',offset);header=data[offset:end].split();assert header[1]==b'blob',p
    size=int(header[2]);offset=end+1;b=data[offset:offset+size];offset+=size+1
    assert data[offset-1:offset]==b'\n' and b==Path(p).read_bytes(),p
assert offset==len(data)
print('PASS original/size/SHA/staged blob',len(paths))

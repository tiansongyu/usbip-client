"""Exercise the real payload function with an inert dependency scaffold; no guest run."""
from pathlib import Path
import ast, gzip, hashlib, json, lzma, shutil, subprocess, tempfile, time
REPO=Path('/home/ubuntu/github/usbip-client')
source=REPO/'scripts/qemu/run-secure-qemu.py'
class Scaffold:
    @staticmethod
    def initramfs(work,kernel):
        root=work/'root'
        for directory in ['etc','modules','usr/bin']: (root/directory).mkdir(parents=True)
    @staticmethod
    def copy_binary(source,target,root):
        # Inert placeholders: this static case tests role packing, not executable provenance.
        path=root/target.lstrip('/');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'STATIC_NOT_EXECUTABLE')
native=Scaffold()
validate_payload=lambda root: {"scope":"role-packing-only inert dependency scaffold; actual dependency check is separate"}
wanted={'digest','payload'}
tree=ast.parse(source.read_text())
exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in wanted],type_ignores=[]),str(source),'exec'))
def entries(archive):
    data=gzip.decompress(archive.read_bytes());offset=0;rows={}
    while True:
        header=data[offset:offset+110];assert header[:6]==b'070701'
        numbers=[int(header[6+i*8:14+i*8],16) for i in range(13)]
        offset+=110;name=data[offset:offset+numbers[11]-1].decode();offset=(offset+numbers[11]+3)&~3
        if name=='TRAILER!!!':break
        size=numbers[6];rows[name]=data[offset:offset+size];offset=(offset+size+3)&~3
    return rows
report={'scope':'real payload code and cpio, inert binary-builder dependency and dummy credential bytes; neither usable credentials nor guest/QEMU/build evidence','roles':{}}
with tempfile.TemporaryDirectory(prefix='rustusb-role-static-') as temp:
    root=Path(temp);certs=root/'certs';certs.mkdir()
    for name in ['ca.der','server.der','client.der','server-key.der','client-key.der']:(certs/name).write_bytes(('STATIC-'+name).encode())
    for role in ['server','client']:
        work=root/role;work.mkdir();archive,provenance=payload(work,'6.8.0-106-generic',certs,role)
        packed=entries(archive);other='client' if role=='server' else 'server'
        assert packed[f'etc/rustusb/{role}-key.der']==('STATIC-'+role+'-key.der').encode()
        assert f'etc/rustusb/{other}-key.der' not in packed
        assert all(f'etc/rustusb/{name}.der' in packed for name in ['ca','server','client'])
        assert not any('ca-key' in name for name in packed)
        report['roles'][role]={'passed':True,'archive_sha256':digest(archive),'credential_names':sorted(name for name in packed if name.startswith('etc/rustusb/'))}
report['source_sha256']=digest(source)
Path('/tmp/rustusb-secure-role-payload-static-20261023.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'role_packing_static':'PASS','roles':list(report['roles'])}))

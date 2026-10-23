"""Actual temporary initramfs dependency/credential inspection; no guest/product execution."""
from pathlib import Path
import gzip,hashlib,importlib.util,json,shutil,tempfile
repo=Path('/home/ubuntu/github/usbip-client')
source=repo/'scripts/qemu/run-secure-qemu.py'
spec=importlib.util.spec_from_file_location('secure_payload_preflight',source)
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
evidence=Path('/tmp/rustusb-secure-payload-dependencies-final-preflight-20261023')
evidence.mkdir(exist_ok=False)
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def entries(archive):
    data=gzip.decompress(archive.read_bytes()); offset=0; output={}
    while True:
        header=data[offset:offset+110]
        if header[:6]!=b'070701':raise RuntimeError('unexpected cpio encoding')
        words=[int(header[6+i*8:14+i*8],16) for i in range(13)]
        offset+=110; name=data[offset:offset+words[11]-1].decode();offset=(offset+words[11]+3)&~3
        if name=='TRAILER!!!':return output
        size=words[6];output[name]={'data':data[offset:offset+size],'mode':words[1],'uid':words[2],'gid':words[3]};offset=(offset+size+3)&~3
initial={n:digest(repo/'target/release'/n) for n in ['rustusb','rustusb-helper']}
report={'scope':__doc__,'kernel_modules':'6.8.0-106-generic','source_sha256':digest(source),'guest_source_sha256':digest(repo/'scripts/qemu/guest-secure-init.sh'),'product_elf_before':initial,'roles':{},'excluded':['Cargo','QEMU','USB syscalls','guest init execution','product/probe execution']}
with tempfile.TemporaryDirectory(prefix='rustusb-actual-payload-preflight-') as temporary:
    work=Path(temporary)
    certs=runner.credentials(work,evidence)
    for role in ['server','client']:
        role_work=work/role;role_work.mkdir()
        archive,provenance=runner.payload(role_work,'6.8.0-106-generic',certs,role)
        parsed=entries(archive); other='client' if role=='server' else 'server'
        key=f'etc/rustusb/{role}-key.der'
        assert key in parsed and parsed[key]['data']==(certs/f'{role}-key.der').read_bytes()
        assert parsed[key]['uid']==0 and parsed[key]['gid']==0 and parsed[key]['mode']&0o777==0o600
        assert f'etc/rustusb/{other}-key.der' not in parsed
        assert all('ca-key' not in name for name in parsed)
        assert 'usr/bin/base64' in parsed and parsed['usr/bin/base64']['data'][:4]==b'\x7fELF'
        assert parsed['init']['data']==(repo/'scripts/qemu/guest-secure-init.sh').read_bytes()
        assert all(provenance[f'usr/bin/{name}']==sha for name,sha in initial.items())
        dependencies=json.loads((role_work/'payload-dependencies.json').read_text())
        shutil.copyfile(role_work/'payload-dependencies.json',evidence/f'{role}-payload-dependencies.json')
        report['roles'][role]={'archive_sha256':digest(archive),'entry_count':len(parsed),'credential_files':sorted(name for name in parsed if name.startswith('etc/rustusb/')),'role_key_root0600':True,'opposite_and_ca_private_keys_absent':True,'base64_ELF_present':True,'required_busybox_applets':len(dependencies['required_applets']),'actual_ELF_count':len(dependencies['executables']),'payload_sha256':provenance}
        # Exercise rejection using actual staged files without executing products.
        saved=role_work/'root/bin/awk'; saved.unlink()
        try:runner.validate_payload(role_work/'root')
        except RuntimeError as error:
            assert 'required guest applet link' in str(error)
            report['roles'][role]['missing_awk_link_rejected']=True
        else:raise AssertionError('missing required applet link accepted')
report['product_elf_after']={name:digest(repo/'target/release'/name) for name in initial}
assert report['product_elf_after']==initial
report['product_elf_guard']=True
report['status']='ACTUAL_PAYLOAD_DEPENDENCY_PREFLIGHT_PASS_NOT_QEMU'
(evidence/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'status':report['status'],'report':str(evidence/'report.json'),'roles':{role:{key:value for key,value in details.items() if key in ['required_busybox_applets','actual_ELF_count','role_key_root0600','opposite_and_ca_private_keys_absent','base64_ELF_present','missing_awk_link_rejected']} for role,details in report['roles'].items()}}))

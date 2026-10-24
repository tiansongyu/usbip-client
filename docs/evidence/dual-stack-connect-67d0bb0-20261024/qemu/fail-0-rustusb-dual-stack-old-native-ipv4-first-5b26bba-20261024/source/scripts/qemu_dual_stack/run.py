#!/usr/bin/env python3
"""Two disposable guests: stale preferred address, healthy opposite-family USB/IP."""
import gzip
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result

base = module('dual_stack_secure_base', ROOT / 'scripts/qemu/run-secure-qemu.py')
checker = module('dual_stack_checker', HERE / 'verify.py')
original_payload = base.payload
original_inputs = base.inputs
original_verify = base.verify
original_pcap = base.pcap_review
run_evidence = None

def inputs():
    result = original_inputs()
    for p in [ROOT/'rust-toolchain.toml', ROOT/'scripts/check.sh', *sorted(HERE.glob('*'))]:
        if p.is_file() and (p.suffix in ['.py','.md'] or p.name in ['rust-toolchain.toml','check.sh']):
            result[str(p.relative_to(ROOT))] = {'sha256':base.digest(p),'bytes':p.stat().st_size}
    return dict(sorted(result.items()))

def generated_init():
    text = (ROOT/'scripts/qemu/guest-secure-init.sh').read_text()
    anchor = 'broker() { /usr/bin/setpriv --reuid=2000 --regid=2000 --clear-groups /usr/bin/rustusb "$@"; }'
    addition = '''if [ "$ROLE" = client ]; then
    STAGE=dual-stack-hostname
    if [ "$FAMILY" = ipv4 ]; then
        BAD=fd42:7275:7374::99 GOOD=192.0.2.1
        printf 'precedence ::/0 100\\nprecedence ::ffff:0:0/96 10\\n' > /etc/gai.conf
        ip -6 neigh replace "$BAD" lladdr 52:54:00:00:00:99 nud permanent dev eth0
    else
        BAD=192.0.2.99 GOOD=fd42:7275:7374::1
        printf 'precedence ::/0 10\\nprecedence ::ffff:0:0/96 100\\n' > /etc/gai.conf
        ip -4 neigh replace "$BAD" lladdr 52:54:00:00:00:99 nud permanent dev eth0
    fi
    printf '127.0.0.1 localhost\\n::1 localhost\\n%s rustusb-dual-stack.test\\n%s rustusb-dual-stack.test\\n' "$BAD" "$GOOD" > /etc/hosts
    printf 'hosts: files\\n' > /etc/nsswitch.conf
    cp /etc/hosts /tmp/dual-hosts.txt
    cp /etc/gai.conf /tmp/dual-gai.txt
    cp /etc/nsswitch.conf /tmp/dual-nsswitch.txt
    ip -4 neigh show dev eth0 > /tmp/dual-neighbors4.txt
    ip -6 neigh show dev eth0 > /tmp/dual-neighbors6.txt
    HOST=rustusb-dual-stack.test
    echo "DUAL_STACK_READY healthy=$FAMILY stale=$BAD mac=52:54:00:00:00:99"
fi
'''
    if text.count(anchor)!=1:
        raise RuntimeError('unique hostname setup anchor absent')
    return text.replace(anchor, addition+anchor, 1)

def payload(work, kernel, credentials, role):
    global run_evidence
    if run_evidence is None:
        raise RuntimeError('evidence owner absent')
    original_payload(work,kernel,credentials,role)
    root=work/'root'
    (root/'init').write_text(generated_init())
    (run_evidence/f'{role}-generated-dual-init.sh').write_bytes((root/'init').read_bytes())
    names=b'.\0'+b'\0'.join(str(p.relative_to(root)).encode() for p in root.rglob('*'))+b'\0'
    packed=subprocess.run(['cpio','--null','-o','--format=newc','--owner=0:0'],input=names,cwd=root,capture_output=True,check=True)
    archive=work/'dual-stack-initramfs.cpio.gz';archive.write_bytes(gzip.compress(packed.stdout))
    provenance={str(p.relative_to(root)):base.digest(p) for p in root.rglob('*') if p.is_file() and not p.is_symlink() and (str(p.relative_to(root)).startswith(('usr/bin/','modules/')) or p==root/'init')}
    return archive,provenance

def verify(evidence,args):
    # The original verifier retains every USB/root/baseline requirement. Only its
    # network classifier receives a named, strict, byte-preserving projection.
    facts=original_verify(evidence,args)
    facts['dual_stack']=checker.verify(evidence,args)
    return facts

def pcap_review(path,family,device_ids):
    derived, facts=checker.project(path,family)
    result=original_pcap(derived,family,device_ids)
    return {'healthy_projection':result,'projection':facts}

def main():
    global run_evidence
    if sys.argv.count('--evidence')!=1:
        raise RuntimeError('one explicit fresh evidence directory required')
    run_evidence=Path(sys.argv[sys.argv.index('--evidence')+1]).resolve()
    base.inputs=inputs;base.payload=payload;base.verify=verify;base.pcap_review=pcap_review
    return base.main(scope='Two original secure USB cycles with a protected dual-stack hostname: preferred stale candidate uses an isolated nonexistent permanent MAC; healthy opposite-family TLS wins. Raw captures unchanged, strict named healthy projection and full original USB/root cleanup. Hostname files are not wire-DNS, VPN, hardware, performance or fullG3 acceptance.')

if __name__=='__main__':
    sys.exit(main())

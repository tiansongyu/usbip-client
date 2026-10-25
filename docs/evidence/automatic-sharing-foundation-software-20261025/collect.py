from pathlib import Path
import hashlib,json,shutil
root=Path('/home/ubuntu/github/usbip-client')
source=Path('/tmp/rustusb-automation-foundation-software-20261025')
dest=root/'docs/evidence/automatic-sharing-foundation-software-20261025'
summary=json.loads((source/'summary.json').read_text())
assert summary['result']=='PASS' and all(summary['guards'].values())
assert [(v['exit'],v['passed'],v['failed'],v['ignored']) for v in summary['commands']]==[(0,978,0,0),(0,11,0,0)]
dest.mkdir(exist_ok=False)
for name in ['summary.json','core.log','gui.log']:
    shutil.copyfile(source/name,dest/name)
shutil.copyfile('/tmp/run-rustusb-automation-foundation-gates-20261025.py',dest/'run-gates.py')
shutil.copyfile(__file__,dest/'collect.py')
for p in dest.iterdir():
    if p.suffix=='.log':
        assert b'-----BEGIN PRIVATE KEY-----' not in p.read_bytes()
        assert b'-----BEGIN RSA PRIVATE KEY-----' not in p.read_bytes()
print(json.dumps({'files':len(list(dest.iterdir())),'bytes':sum(p.stat().st_size for p in dest.iterdir()),'source_head':summary['head']}))

from pathlib import Path
import subprocess,json,datetime
root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-authentication-ipv6-matrix-25870c6-20261024');out.mkdir(mode=0o700)
rows=[]
for backend in ['native','virtual','usbfs']:
    evidence=Path(f'/tmp/rustusb-authentication-{backend}-ipv6-first-25870c6-20261024')
    command=['python3','-B','scripts/qemu_authentication/run.py','--backend',backend,'--family','ipv6','--kernel','6.8.0-106-generic','--evidence',str(evidence)]
    with (out/f'{backend}.log').open('wb') as log: result=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    summary=json.loads((evidence/'summary.json').read_text())
    rows.append({'backend':backend,'command':command,'actual_exit':result.returncode,'summary_result':summary['result'],'evidence':str(evidence),'guest_exits':summary.get('guest_exit_codes'),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat()})
    (out/'progress.json').write_text(json.dumps(rows,indent=2)+'\n')
    print(backend,'actual exit',result.returncode,'result',summary['result'],flush=True)
    if result.returncode or summary['result']!='PASS': raise SystemExit(1)
print('PASS three distinct IPv6 paired runs',flush=True)

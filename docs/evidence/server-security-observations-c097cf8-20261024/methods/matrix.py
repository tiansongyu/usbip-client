from pathlib import Path
import subprocess,json,datetime
root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-security-observations-matrix-c097cf8-20261024');out.mkdir(exist_ok=False)
results=[]
for backend,family in [('virtual','ipv4'),('usbfs','ipv4'),('native','ipv6'),('virtual','ipv6'),('usbfs','ipv6')]:
    evidence=Path(f'/tmp/rustusb-security-observations-{backend}-{family}-first-c097cf8-20261024')
    command=['python3','-B','scripts/qemu_authentication/run.py','--backend',backend,'--family',family,'--kernel','6.8.0-106-generic','--evidence',str(evidence)]
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with (out/f'{backend}-{family}.log').open('wb') as log:
        done=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
    summary=json.loads((evidence/'summary.json').read_text())
    row={'backend':backend,'family':family,'command':command,'started_utc':started,'exit_code':done.returncode,'result':summary['result'],'error':summary.get('error'),'evidence':str(evidence)}
    results.append(row);(out/'progress.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(row),flush=True)
    if done.returncode:break
raise SystemExit(0 if len(results)==5 and all(row['exit_code']==0 and row['result']=='PASS' for row in results) else 1)

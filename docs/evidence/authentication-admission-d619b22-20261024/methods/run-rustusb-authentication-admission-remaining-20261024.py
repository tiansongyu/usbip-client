from pathlib import Path
import concurrent.futures,subprocess,json,datetime,threading
root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-authentication-admission-remaining-d619b22-20261024');out.mkdir(exist_ok=False)
work=[('exhaustion',b,'ipv6') for b in ['virtual','usbfs']]
work += [('default',b,f) for b in ['native','virtual','usbfs'] for f in ['ipv4','ipv6']]
work += [('v2-diagnostic','native','ipv4')]
lock=threading.Lock();results=[]
def run(item):
 kind,backend,family=item
 if kind=='exhaustion':script='scripts/qemu_authentication_admission/run.py';stem=f'{backend}-{family}'
 elif kind=='default':script='scripts/qemu/run-secure-qemu.py';stem=f'default-{backend}-{family}'
 else:script='scripts/qemu_diagnostic_export/run.py';stem='v2-diagnostic'
 evidence=Path(f'/tmp/rustusb-authentication-admission-{stem}-d619b22-20261024')
 command=['python3','-B',script,'--backend',backend,'--family',family,'--kernel','6.8.0-106-generic','--evidence',str(evidence)]
 start=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (out/f'{stem}.log').open('wb') as log:done=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
 s=json.loads((evidence/'summary.json').read_text())
 row=dict(kind=kind,backend=backend,family=family,command=command,started_utc=start,exit_code=done.returncode,result=s['result'],error=s.get('error'),evidence=str(evidence))
 with lock:
  results.append(row);(out/'progress.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(row),flush=True)
 return row
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(run,work))
raise SystemExit(0 if all(r['exit_code']==0 and r['result']=='PASS' for r in rows) else 1)

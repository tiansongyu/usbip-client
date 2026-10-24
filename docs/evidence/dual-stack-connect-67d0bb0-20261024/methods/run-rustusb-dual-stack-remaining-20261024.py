from pathlib import Path
import subprocess,json,datetime
root=Path('/home/ubuntu/github/usbip-client')
out=Path('/tmp/rustusb-dual-stack-remaining-67d0bb0-20261024');out.mkdir(exist_ok=False)
assert subprocess.check_output(['git','status','--porcelain'],cwd=root)==b''
work=[('hostname',b,f) for b in ['native','virtual','usbfs'] for f in ['ipv4','ipv6'] if (b,f)!=('native','ipv4')]
work += [('hostname-framed','native','ipv4')]
work += [('default',b,f) for b in ['native','virtual','usbfs'] for f in ['ipv4','ipv6']]
work += [('v2-diagnostic','native','ipv4')]
rows=[]
for kind,backend,family in work:
 if kind=='hostname':script='scripts/qemu_dual_stack/run.py';stem=f'{backend}-{family}'
 elif kind=='hostname-framed':script='scripts/qemu_dual_stack/run.py';stem='native-ipv4-framed'
 elif kind=='default':script='scripts/qemu/run-secure-qemu.py';stem=f'default-{backend}-{family}'
 else:script='scripts/qemu_diagnostic_export/run.py';stem='v2-diagnostic'
 evidence=Path('/tmp/rustusb-dual-stack-'+stem+'-67d0bb0-20261024')
 command=['python3','-B',script]+(['--framed-v2'] if kind=='hostname-framed' else [])+['--backend',backend,'--family',family,'--kernel','6.8.0-106-generic','--evidence',str(evidence)]
 started=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (out/f'{stem}.log').open('wb') as log:done=subprocess.run(command,cwd=root,stdout=log,stderr=subprocess.STDOUT)
 s=json.loads((evidence/'summary.json').read_text());row=dict(kind=kind,backend=backend,family=family,command=command,started_utc=started,exit_code=done.returncode,result=s['result'],error=s.get('error'),evidence=str(evidence));rows.append(row)
 (out/'progress.json').write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps(row),flush=True)
 if done.returncode:break
raise SystemExit(0 if len(rows)==len(work) and all(r['exit_code']==0 and r['result']=='PASS' for r in rows) else 1)

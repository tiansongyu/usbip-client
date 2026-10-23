import datetime,json,pathlib,subprocess,sys
root=pathlib.Path('/home/ubuntu/github/usbip-client');logs=pathlib.Path('/tmp/rustusb-agent-regression-orchestration-20261023');logs.mkdir(exist_ok=False)
runs=[('agent-native-ipv6','run-agent-qemu.py','native','ipv6')]
runs += [(f'foreground-{backend}-{family}','run-secure-qemu.py',backend,family) for backend in ['virtual','usbfs','native'] for family in ['ipv4','ipv6']]
results=[]
for name,script,backend,family in runs:
 evidence=pathlib.Path('/tmp')/f'rustusb-qemu-{name}-eaa1936-20261023'
 command=['python3',str(root/'scripts/qemu'/script),'--backend',backend,'--family',family,'--evidence',str(evidence)]
 print(json.dumps({'event':'start','name':name,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'command':command}),flush=True)
 with (logs/(name+'.log')).open('wb') as output:result=subprocess.run(command,cwd=root,stdout=output,stderr=subprocess.STDOUT)
 summary=json.loads((evidence/'summary.json').read_text()) if (evidence/'summary.json').exists() else {}
 row={'name':name,'exit_code':result.returncode,'result':summary.get('result'),'evidence':str(evidence),'head':summary.get('head'),'guards':{key:summary.get(key) for key in ['source_guard','binary_guard','head_guard','clean_guard']},'guest_exit_codes':summary.get('guest_exit_codes')};results.append(row)
 (logs/'results.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps({'event':'complete',**row}),flush=True)
 if result.returncode!=0 or summary.get('result')!='PASS':sys.exit(1)

import json,pathlib,subprocess,sys
root=pathlib.Path('/home/ubuntu/github/usbip-client');logs=pathlib.Path('/tmp/rustusb-agent-config-final-orchestration-20261023');logs.mkdir(exist_ok=False);results=[]
for family in ['ipv4','ipv6']:
 evidence=pathlib.Path('/tmp')/f'rustusb-qemu-agent-native-{family}-config-cb08672-20261023'
 cmd=['python3',str(root/'scripts/qemu/run-agent-qemu.py'),'--backend','native','--family',family,'--evidence',str(evidence)]
 print(json.dumps({'event':'start','family':family,'command':cmd}),flush=True)
 with (logs/(family+'.log')).open('wb') as output:result=subprocess.run(cmd,cwd=root,stdout=output,stderr=subprocess.STDOUT)
 summary=json.loads((evidence/'summary.json').read_text()) if (evidence/'summary.json').exists() else {}
 row={'family':family,'exit_code':result.returncode,'result':summary.get('result'),'head':summary.get('head'),'error':summary.get('error'),'guest_exit_codes':summary.get('guest_exit_codes'),'evidence':str(evidence)};results.append(row)
 (logs/'results.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps({'event':'complete',**row}),flush=True)
 if result.returncode or summary.get('result')!='PASS':sys.exit(1)

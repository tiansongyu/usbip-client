import datetime,json,pathlib,subprocess
root=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path('/tmp/rustusb-gui-rules-matrix-20261025');out.mkdir(exist_ok=False)
driver='/tmp/rustusb-gui-native-tools-20261025/extracted/usr/bin/WebKitWebDriver'
runs=[]
for name,script,family in [('new-ipv4','qemu_gui_rules/run.py','ipv4'),('new-ipv6','qemu_gui_rules/run.py','ipv6'),('original-actions-ipv4','qemu_gui_actions/run.py','ipv4'),('original-actions-ipv6','qemu_gui_actions/run.py','ipv6'),('original-link-ipv4','qemu_gui_link/run.py','ipv4'),('original-single','qemu_gui/run.py',None)]:
 evidence=pathlib.Path('/tmp/rustusb-gui-rules-'+name+'-20261025')
 argv=['python3','scripts/'+script,'--driver',driver,'--evidence',str(evidence),'--timeout','600']
 if family:argv+=['--family',family]
 item={'name':name,'argv':argv,'evidence':str(evidence),'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()};runs.append(item)
 (out/'summary.json').write_text(json.dumps({'result':'RUNNING','runs':runs},indent=2)+'\n')
 with(out/(name+'.log')).open('wb')as stream:child=subprocess.run(argv,cwd=root,stdout=stream,stderr=subprocess.STDOUT)
 item['exit']=child.returncode;item['ended_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
 (out/'summary.json').write_text(json.dumps({'result':'FAIL'if child.returncode else 'RUNNING','runs':runs},indent=2)+'\n')
 if child.returncode:raise SystemExit(child.returncode)
(out/'summary.json').write_text(json.dumps({'result':'PASS','runs':runs},indent=2)+'\n')

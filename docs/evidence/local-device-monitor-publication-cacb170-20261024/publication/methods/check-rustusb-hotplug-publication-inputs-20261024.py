from pathlib import Path
import json,subprocess,hashlib
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-hotplug-publication-cacb170-20261024');content='cacb1704fe3a69ddc3ed3a66e671494f7a5ba9ac';software='c0346d61d01b6a5f1605f55a6591d87fedd0d615'
rows=json.loads(Path('/tmp/rustusb-hotplug-software-third-20261024/inputs-before.json').read_text())
def blob(commit,name):return subprocess.check_output(['git','show',commit+':'+name],cwd=root)
for row in rows:
 b=(root/row['path']).read_bytes();assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'];assert b==blob(software,row['path'])==blob(content,row['path'])
qadir=Path('/tmp/rustusb-hotplug-docs-site-20261024');qa=json.loads((qadir/'doc-links.json').read_text())['source']
for name,sha in qa.items():
 b=(qadir/'source'/name).read_bytes();assert hashlib.sha256(b).hexdigest()==sha;assert b==blob(content,name)
qemu=json.loads(Path('/tmp/rustusb-hotplug-qemu-all-first-20261024/summary.json').read_text())
for name,row in qemu['source_inputs'].items():assert blob(content,name)==blob(qemu['head'],name)
method=Path('/tmp/rustusb-hotplug-final-local-method-20261024/script-source.py').read_bytes();assert method==blob(content,'scripts/test-device-monitor.py')
site={}
for name in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 b=(root/'website'/name).read_bytes();assert b==blob(content,'website/'+name);site[name]=hashlib.sha256(b).hexdigest()
result={'result':'PASS','content_commit':content,'software_commit':software,'selected_software_inputs':len(rows),'software_inputs':rows,'qemu_commit':qemu['head'],'qemu_inputs':len(qemu['source_inputs']),'prepublication_dirty_qa_sources_mapped_to_content':qa,'process_method_source_sha256':hashlib.sha256(method).hexdigest(),'site':site,'scope':'Byte/source mapping, no software/QEMU rerun or clean relabel of local documentation/final method checks.'};(out/'source-map.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS 206 software/213 QEMU/40 QA/current process method/6 site inputs mapped to content')

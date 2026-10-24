from pathlib import Path
import json,subprocess,hashlib
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-dual-stack-publication-2aa9337-20261024');content='2aa9337411ed67f4626c04425703fe09a5bcc5c7';software='f3bf579c79d361c96c9381c74a5a151557a5756a'
rows=json.loads(Path('/tmp/rustusb-dual-stack-software-first-20261024/inputs-before.json').read_text())
for row in rows:
 b=(root/row['path']).read_bytes();assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'];assert b==subprocess.check_output(['git','show',software+':'+row['path']],cwd=root)==subprocess.check_output(['git','show',content+':'+row['path']],cwd=root)
qadir=Path('/tmp/rustusb-dual-stack-docs-site-20261024');qa=json.loads((qadir/'doc-links.json').read_text())['source']
for n,h in qa.items():
 b=(qadir/'source'/n).read_bytes();assert hashlib.sha256(b).hexdigest()==h;assert b==subprocess.check_output(['git','show',content+':'+n],cwd=root)
site={}
for n in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 b=(root/'website'/n).read_bytes();assert b==subprocess.check_output(['git','show',content+':website/'+n],cwd=root);site[n]=hashlib.sha256(b).hexdigest()
result={'result':'PASS','content_commit':content,'actual_software_commit':software,'software_selected_inputs':len(rows),'software_mapping':rows,'local_dirty_qa_sources_mapped_to_committed_content':qa,'site_six_sources':site,'scope':'Byte/source mapping, not software/QEMU rerun or clean relabel of local documentation QA.'}
(out/'source-map.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS',len(rows),'software inputs,',len(qa),'QA sources, 6 site bytes')

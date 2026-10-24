from pathlib import Path
import json,subprocess,hashlib
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-dual-stack-delivery-checks-20261024')
for n,cmd in [('links',['python3','-B',str(out/'doc-links.py')]),('site',['python3','scripts/check-site.py']),('javascript',['node','--check','website/app.js']),('whitespace',['git','diff','--check'])]:
 r=subprocess.run(cmd,cwd=root,capture_output=True);(out/(n+'.stdout')).write_bytes(r.stdout);(out/(n+'.stderr')).write_bytes(r.stderr);(out/(n+'.command.json')).write_text(json.dumps({'argv':cmd,'exit_code':r.returncode},indent=2)+'\n');assert r.returncode==0
report=json.loads((out/'doc-links.json').read_text());assert not report['failures'] and not report['table_width_errors'] and not report['table_spacing_errors']
for n,h in report['source'].items():
 b=(root/n).read_bytes();assert hashlib.sha256(b).hexdigest()==h;p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b)
# Keep this scope separate from the frozen 198 software input and source-map original.
rows=json.loads(Path('/tmp/rustusb-dual-stack-software-first-20261024/inputs-before.json').read_text())
for row in rows:
 b=(root/row['path']).read_bytes();assert len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'];assert b==subprocess.check_output(['git','show','2aa9337:'+row['path']],cwd=root)
site={}
for n in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']:
 b=(root/'website'/n).read_bytes();assert b==subprocess.check_output(['git','show','2aa9337:website/'+n],cwd=root);site[n]=hashlib.sha256(b).hexdigest()
(out/'product-site-stable.json').write_text(json.dumps({'result':'PASS','publication_content_commit':'2aa9337411ed67f4626c04425703fe09a5bcc5c7','software_inputs':198,'site':site,'scope':'Final documentation-only receipt; no software rerun.'},indent=2)+'\n')
print(json.dumps({'result':'PASS','sources':len(report['source']),'links':len(report['local_or_repo_mapped_links']),'external_not_fetched':report['external_links_not_fetched'],'software_inputs_unchanged':198,'site_sources_unchanged':6}))

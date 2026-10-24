from pathlib import Path
import json,hashlib,subprocess,shutil,os
root=Path('/home/ubuntu/github/usbip-client'); out=Path('/tmp/rustusb-dual-stack-docs-site-20261024')
def write(n,d): (out/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
for name,cmd in [('doc-links',['python3','-B',str(out/'doc-links.py')]),('site-check',['python3','scripts/check-site.py']),('javascript',['node','--check','website/app.js']),('whitespace',['git','diff','--check'])]:
 r=subprocess.run(cmd,cwd=root,capture_output=True);(out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr);write(name+'-command.json',{'argv':cmd,'exit_code':r.returncode});assert r.returncode==0
report=json.loads((out/'doc-links.json').read_text());assert not report['failures'] and not report['table_spacing_errors'] and not report['table_width_errors']
base=json.loads((root/'docs/evidence/authentication-admission-docs-site-20261024/qa/unchanged-runtime-assets-and-global-skill.json').read_text()); assets={}; skill={}
for n,old in base['runtime_assets'].items():
 b=(root/'website'/n).read_bytes(); assets[n]=hashlib.sha256(b).hexdigest()
 if n!='index.html': assert assets[n]==old and b==subprocess.check_output(['git','show','9c9506f:website/'+n],cwd=root)
for n,old in base['global_skill_sources'].items():
 p=Path('/home/ubuntu/.agents/skills/readme-site')/n;b=p.read_bytes();skill[n]=hashlib.sha256(b).hexdigest();assert skill[n]==old
 target=out/'global-skill'/n;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
link=Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink() and str(link.resolve())==base['codex_link']
write('unchanged-runtime-assets-and-global-skill.json',{'runtime_assets':assets,'global_skill_sources':skill,'codex_link':str(link.resolve()),'scope':'Five runtime assets unchanged from 9c9506f; global four sources unchanged from prior receipt; index content updated.'})
for n in list(report['source'])+['docs/work-log.md','.github/workflows/ci.yml','scripts/test-hostname-discovery.py']+['website/'+x for x in assets]:
 p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/n).read_bytes())
write('cua-transcription.json',{'kind':'parent transcription of actual CUA outputs, not raw browser screenshot','local_url':'http://127.0.0.1:38325/usbip-client/','desktop':{'width':1280,'height':720,'scroll_width':1265,'metrics':['842','14','28'],'heading':'Hostname fallback, real emulated I/O','images_complete':True,'flow_natural_width':1120},'keyboard_right':{'selected':'tab-virtual','focus':'tab-virtual','hash':''},'keyboard_home':{'selected':'tab-native','focus':'tab-native','hash':''},'mobile':{'width':390,'height':844,'scroll_width':375,'metrics':['842','14','28'],'initial_flow':'usbip-flow.svg','after_networkidle_flow':'usbip-flow-mobile.svg','after_natural_width':360,'images_complete':True},'nojs':{'mechanism':"HTTP CSP script-src 'none'",'width':1280,'scroll_width':1265,'enhanced_class':'','metrics':['842','14','28'],'panels':[{'id':'panel-'+x,'visible':True,'hidden':False,'has_commands':True} for x in ['native','virtual','physical']]},'console_queries':{'normal':[],'nojs':[]},'screenshot_error':'Error: Unable to capture screenshot','cleanup':{'closed_tabs':['21','22'],'viewport_reset':True},'limits':['No pixel review','Clipboard and reduced motion not rerun','No separate local HTTP exact-byte fetch in this pass']})
pid=json.loads((out/'preview.json').read_text())['pid'];assert not Path('/proc',str(pid)).exists();write('preview-cleanup.json',{'pid':pid,'actual_session':61594,'actual_wait_exit_code':0,'proc_absent':True,'method':'Ctrl-C through original owned exec session; observed exit 0'})
print('PASS captured documentation, site, global skill and preview cleanup')

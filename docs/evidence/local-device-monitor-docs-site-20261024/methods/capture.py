from pathlib import Path
import json,hashlib,subprocess,shutil,os
root=Path('/home/ubuntu/github/usbip-client'); out=Path('/tmp/rustusb-hotplug-docs-site-20261024')
def write(n,d): (out/n).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
for name,cmd in [('doc-links',['python3','-B',str(out/'doc-links.py')]),('site-check',['python3','scripts/check-site.py']),('javascript',['node','--check','website/app.js']),('whitespace',['git','diff','--check'])]:
 r=subprocess.run(cmd,cwd=root,capture_output=True);(out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr);write(name+'-command.json',{'argv':cmd,'exit_code':r.returncode});assert r.returncode==0
report=json.loads((out/'doc-links.json').read_text());assert not report['failures'] and not report['table_spacing_errors'] and not report['table_width_errors']
base=json.loads((root/'docs/evidence/authentication-admission-docs-site-20261024/qa/unchanged-runtime-assets-and-global-skill.json').read_text()); assets={}; skill={}
for n,old in base['runtime_assets'].items():
 b=(root/'website'/n).read_bytes(); assets[n]=hashlib.sha256(b).hexdigest()
 if n!='index.html': assert assets[n]==old and b==subprocess.check_output(['git','show','43f3ff4:website/'+n],cwd=root)
for n,old in base['global_skill_sources'].items():
 p=Path('/home/ubuntu/.agents/skills/readme-site')/n;b=p.read_bytes();skill[n]=hashlib.sha256(b).hexdigest();assert skill[n]==old
 target=out/'global-skill'/n;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b)
link=Path('/home/ubuntu/.codex/skills/readme-site');assert link.is_symlink() and str(link.resolve())==base['codex_link']
write('unchanged-runtime-assets-and-global-skill.json',{'runtime_assets':assets,'global_skill_sources':skill,'codex_link':str(link.resolve()),'scope':'Five runtime assets unchanged from 43f3ff4; global four sources unchanged from prior receipt; index content updated.'})
for n in list(report['source'])+['docs/work-log.md','.github/workflows/ci.yml','scripts/test-hostname-discovery.py']+['website/'+x for x in assets]:
 p=out/'source'/n;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/n).read_bytes())
write('cua-transcription.json',{'kind':'Parent transcription of actual CUA outputs, not a screenshot','url':'http://127.0.0.1:41995/usbip-client/','desktop':{'width':1280,'height':720,'scroll_width':1265,'heading':'Live local USB inventory','metrics':['872','2','6'],'captions':['software test executions','QEMU guest runs','local observer process cases'],'report':'https://github.com/tiansongyu/usbip-client/blob/main/docs/research/local-device-monitor-validation.md','images_complete':True,'flow_width':1120},'right':{'selected':'tab-virtual','focus':'tab-virtual','url':'http://127.0.0.1:41995/usbip-client/?backend=virtual'},'home':{'selected':'tab-native','focus':'tab-native','url':'http://127.0.0.1:41995/usbip-client/?backend=native'},'mobile_initial':{'width':390,'height':844,'scroll_width':375,'metrics':['872','2','6'],'flow':'usbip-flow.svg','images_complete':True},'mobile_settled':{'width':390,'scroll_width':375,'flow':'usbip-flow-mobile.svg','flow_width':360,'images_complete':True},'nojs':{'mechanism':"HTTP CSP script-src 'none'",'width':1280,'scroll_width':1265,'enhanced_class':'','metrics':['872','2','6'],'panels':[{'id':'panel-'+name,'hidden':False,'visible':True,'commands':True} for name in ['native','virtual','physical']]},'console_queries':{'normal':[],'nojs':[]},'screenshot_error':'Error: Unable to capture screenshot','cleanup':{'tabs_closed':['23','24'],'viewport_reset_observed':{'width':1280,'height':720}},'limits':['DOM/layout evidence only, no pixel review','Clipboard and reduced-motion not rerun']})
pid=json.loads((out/'preview.json').read_text())['pid'];assert not Path('/proc',str(pid)).exists();write('preview-cleanup.json',{'pid':pid,'actual_session':61655,'actual_wait_exit_code':0,'proc_absent':True,'method':'Ctrl-C through original owned exec session; observed exit 0'})
print('PASS captured documentation, site, global skill and preview cleanup')

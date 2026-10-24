#!/usr/bin/env python3
"""Saved publication receipts only: no network/browser/build/program execution."""
from pathlib import Path
import argparse,hashlib,json,subprocess,urllib.parse
HEAD='7640efbbe2f57c2a791b38da339695976e80205d';SOFTWARE=40056501727;PAGES=40056501893
BASE=Path('/tmp/rustusb-logging-publication-7640efb-20261024');REPO=Path('/home/ubuntu/github/usbip-client')
SOFT=Path('/tmp/rustusb-logging-software-second-20261024')
def digest(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def obj(p):return json.loads(p.read_bytes())
def structured(p):
 d=obj(p);s=d.get('structuredContent',d)
 if isinstance(s,dict) and isinstance(s.get('content'),str):
  try:return json.loads(s['content'])
  except ValueError:pass
 return s

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--phase',choices=['initial','final'],default='initial');ap.add_argument('--software-jobs',default='jobs-40056501727-second.response.json');ap.add_argument('--runs',default='runs-initial.response.json');ap.add_argument('--pages-jobs',default='jobs-40056501893-first.response.json');a=ap.parse_args()
 out=Path('/tmp/rustusb-logging-publication-7640efb-method-review-20261024')/a.phase;out.mkdir(parents=True,exist_ok=False)
 consulted={};checks=[]
 def consult(p):consulted[str(p)]=digest(p);return p
 def ck(name,v,detail=None):checks.append({'assertion':name,'pass':bool(v),**({'detail':detail} if detail is not None else {})})
 def gitbytes(path):return subprocess.check_output(['git','show',HEAD+':'+path],cwd=REPO)
 receipt=obj(consult(BASE/'source-and-push-receipt.json'));cua=obj(consult(BASE/'cua-live-observations.json'));live=obj(consult(BASE/'live-first/report.json'))
 ck('recorded publication exact head and successful normal push/remote transcription',receipt['content_head']==receipt['root_head']==HEAD and receipt['working_tree']=='' and receipt['actual_push_exit_code']==receipt['actual_ls_remote_exit_code']==0 and receipt['actual_ls_remote']==HEAD+'\trefs/heads/main\n')
 ck('recorded push scope says transcription not backdated process files','transcribe' in receipt['scope'] and 'not backdated' in receipt['scope'])
 expected={'index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg'};resources=[]
 ck('six exact saved live canonical resources',len(live['resources'])==6 and {r['path'] for r in live['resources']}==expected and live['head']==HEAD)
 for r in live['resources']:
  p=consult(BASE/'live-first'/r['path']);b=p.read_bytes();gb=gitbytes(r['git_path']);u=urllib.parse.urlparse(r['url'])
  ck('canonical HTTPS200 '+r['path'],r['status']==200 and r['url']==r['final_url'] and u.scheme=='https' and u.netloc=='tiansongyu.github.io' and u.path=='/usbip-client/'+('' if r['path']=='index.html' else r['path']))
  ck('saved raw bytes and exact published Gitblob '+r['path'],len(b)==r['bytes'] and hashlib.sha256(b).hexdigest()==r['sha256']==r['git_sha256'] and r['equal_git_blob'] and b==gb and r['git_path']=='website/'+r['path'])
  resources.append({'path':r['path'],'url':r['url'],'status':r['status'],'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest(),'Git_blob_equal':b==gb,'fetched_utc':r['fetched_utc']})
 manifest=obj(consult(SOFT/'inputs-before.json'));after=obj(consult(SOFT/'inputs-after.json'));source_details=[]
 ck('177 saved software manifest before-after same and preserved dirty provenance',manifest==after and len(manifest)==177)
 for r in manifest:
  p=consult(SOFT/'source'/r['path']);current=consult(REPO/r['path']);saved=p.read_bytes();gb=gitbytes(r['path']);now=current.read_bytes();expected_digest={'bytes':r['bytes'],'sha256':r['sha256']}
  ok=digest(p)==expected_digest and now==saved==gb;ck('saved/current/Git7640 input '+r['path'],ok);source_details.append({'path':r['path'],'bytes':r['bytes'],'sha256':r['sha256'],'all_equal':ok})
 start=obj(consult(SOFT/'start.json'));ck('software remains actual dirty48a4 run not content-publication rerun',start['head']=='48a4b013fceb4493cf07c10cac999b8c41f76dae' and bool(start['status']))
 runs=structured(consult(BASE/a.runs))['workflow_runs'];software=next(r for r in runs if r['id']==SOFTWARE);pages=next(r for r in runs if r['id']==PAGES)
 ck('official run exact pushhead software and Pages',all(r['head_sha']==HEAD and r['head_branch']=='main' and r['event']=='push' for r in [software,pages]))
 jobs=structured(consult(BASE/a.software_jobs))['jobs'];pjobs=structured(consult(BASE/a.pages_jobs))['jobs']
 ck('four actual named native software jobs and exact runid',len(jobs)==4 and {j['id'] for j in jobs}=={120760539311,120760539410,120760539417,120760539489} and {j['name'] for j in jobs}=={'check (ubuntu-22.04-arm, aarch64)','check (ubuntu-24.04, x86_64)','check (ubuntu-24.04-arm, aarch64)','check (ubuntu-22.04, x86_64)'} and all(j['run_id']==SOFTWARE for j in jobs))
 ck('Pages check+deploy actual terminalsuccess',len(pjobs)==2 and {j['name'] for j in pjobs}=={'check','deploy'} and all(j['run_id']==PAGES and j['status']=='completed' and j['conclusion']=='success' for j in pjobs))
 if a.phase=='final':
  ck('final official software run success',software['status']=='completed' and software['conclusion']=='success')
  ck('final four official software jobs success',all(j['status']=='completed' and j['conclusion']=='success' for j in jobs))
  ck('final official Pages run success',pages['status']=='completed' and pages['conclusion']=='success')
 # Initial saved run metadata may still say in_progress even after the separately
 # fetched Pages jobs succeeded. Preserve each actual saved observation.
 ck('CUA record explicitly transcription and exact head',cua['content_head']==HEAD and 'transcription' in cua['scope'])
 ck('CUA failed screenshot and no pixel claim',cua['screenshot']['attempted'] and not cua['screenshot']['available'] and cua['screenshot']['actual_error']=='Error: Unable to capture screenshot' and any('No pixel screenshot' in x for x in cua['limits']))
 ck('CUA no new noJS/clipboard/reducedmotion claim',any('new clipboard or no-JS' in x for x in cua['limits']) and any('reduced-motion' in x for x in cua['limits']))
 ck('recorded desktop/mobile no page overflow and settled responsive images',cua['desktop']['scroll_width']==cua['desktop']['client_width']==1265 and cua['mobile']['scroll_width']==cua['mobile']['client_width']==375 and cua['desktop']['width']==1280 and cua['mobile']['width']==390 and cua['desktop']['settled_favicon_loaded'] and cua['desktop']['flow_loaded'] and cua['mobile']['settled_flow_loaded'])
 ck('recorded keys preserve focus/selection/shareablequery',cua['keys']['ArrowRight']=={'focus':'Virtual devices','selection':'Virtual devices','query':'?backend=virtual'} and cua['keys']['Home']=={'focus':'Linux Native','selection':'Linux Native','query':'?backend=native'} and cua['console_warnings_and_errors']==[])
 ck('recorded display stats and old/new scope boundaries',cua['desktop']['statistics']==cua['mobile']['statistics']==['723','6','4'] and cua['desktop']['new_qemu_head']=='48c9794' and cua['desktop']['historical_qemu_head']=='1582f06' and cua['desktop']['historical_runs']==23)
 doc=gitbytes('docs/research/rotating-logs-validation.md').decode();readme=gitbytes('README.md').decode();site=gitbytes('website/index.html').decode()
 ck('published firstFAIL retained independently',all(x in doc for x in ['44b1ef9','remains **FAIL**','not used to relabel the first run','41fce5f','before any new logging case']))
 ck('published oldbaseline separate no single-source24-run claim',all(x in doc for x in ['1582f06','23 paired runs / 46 guest exits','No combined 24-run single-source result']) and 'separate baseline' in readme and 'earlier' in site)
 ck('published current detailed CI refers to prior89 not this status-only run','40053603593' in doc and '40053603593' in readme and '40056501727' not in readme)
 ck('published incomplete scope explicit','not completion of Phase 3' in doc and 'real-device G1/G2' in doc and 'Physical hardware acceptance is still pending' in readme)
 after_consulted={n:digest(Path(n)) for n in consulted};ck('only actually consulted fixed files byte-stable during review',consulted==after_consulted)
 report={'schema_version':1,'phase':a.phase,'review_result':'PASS' if all(c['pass'] for c in checks) else 'FAIL','publication_head':HEAD,'software_terminal_pending':not all(j['status']=='completed' for j in jobs),'scope':'Read-only review of saved official API/livebytes/source correspondence and CUA transcription. No network/API/browser/build/CLI/QEMU executed. No test count recomputation or new USB evidence.','resources':resources,'selected_software_inputs':len(manifest),'saved_software_origin':{'head':start['head'],'dirty':bool(start['status']),'later_mapping':'content equality only, not clean-run reassignment'},'official_software_run':{k:software[k] for k in ['id','head_sha','status','conclusion','event','html_url']},'official_pages_run':{k:pages[k] for k in ['id','head_sha','status','conclusion','event','html_url']},'software_jobs':[{k:j[k] for k in ['id','name','run_id','status','conclusion']} for j in jobs],'pages_jobs':[{k:j[k] for k in ['id','name','run_id','status','conclusion']} for j in pjobs],'CUA_scope':'Structured transcription from root actual CUA, not raw screenshot or independent browser reproduction. Screenshot attempted and failed; no pixel/noJS/clipboard/reducedmotion new evidence. Initial pending favicon/image and settled observations remain distinct.','checks':checks,'limits':['Push/lsremote values are saved root tool-result transcriptions, not a new network verification by reviewer.','Only workflow statuses and exact source head are reviewed for publication CI; detailed723/test/runtime evidence remains prior89ef420 and its official logs, not recomputed or reattributed.','No missing runner temporary evidence is manufactured.','Current working tree after publication may gain documentation receipts;177 selected source bytes are separately checked.','Consulted original fixed filenames are hashed before/after; root may add new final API files, which is not treated as mutation of old originals.','First44b1 QEMUFAIL and41fce CIcache failures remain original failures; old1582 23-runbaseline remains separate.'],'consulted_artifacts':'consulted-artifacts.json','source_details':'source-checks.json','script':str(Path(__file__))}
 (out/'consulted-artifacts.json').write_text(json.dumps(consulted,indent=2)+'\n');(out/'source-checks.json').write_text(json.dumps(source_details,indent=2)+'\n');(out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({'phase':a.phase,'review_result':report['review_result'],'checks':len(checks),'failures':[c for c in checks if not c['pass']],'software_terminal_pending':report['software_terminal_pending'],'software_jobs':[(j['name'],j['status'],j['conclusion']) for j in jobs],'report':str(out/'report.json'),'integrity':digest(out/'report.json')},indent=2))
if __name__=='__main__':main()

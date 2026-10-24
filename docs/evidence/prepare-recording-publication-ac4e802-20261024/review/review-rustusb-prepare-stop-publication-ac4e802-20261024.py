#!/usr/bin/env python3
"""Offline publication audit; saved HTTP/API receipts and local Git objects only."""
import argparse,hashlib,json,re,subprocess
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('--base',type=Path,default=Path('/tmp/rustusb-prepare-stop-publication-ac4e802-20261024'));ap.add_argument('--repo',type=Path,default=Path('/home/ubuntu/github/usbip-client'));ap.add_argument('--output',type=Path,default=Path('/tmp/rustusb-prepare-stop-publication-initial-independent-review-20261024'));args=ap.parse_args();args.output.mkdir(exist_ok=False)
HEAD='ac4e802848257e1a36767137e2e56bef4dda6683';checks=[]
def sha(b):return hashlib.sha256(b).hexdigest()
def info(p):
 b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':sha(b)}
def load(p):return json.loads(p.read_text())
def check(name,ok,detail=None):checks.append({'check':name,'passed':bool(ok),'detail':detail})
def gitblob(name):return subprocess.check_output(['git','show',HEAD+':'+name],cwd=args.repo)
def api(name):
 outer=load(args.base/name);check('connector '+name+' not error',not outer.get('isError',False));x=outer['structuredContent']
 if 'content'in x:
  check('official saved API origin '+name,x['url'].startswith('https://api.github.com/repos/tiansongyu/usbip-client/actions/'),x['url']);return json.loads(x['content'])
 return x
files=['source-and-remote.json','push.response.json','browser-live.json','live/report.json','runs-initial.response.json','pages-final.response.json','jobs-40045915200-initial.response.json','jobs-40045915202-initial.response.json','software-progress.response.json']
paths=[args.base/n for n in files]+[args.base/'live'/n for n in ['index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg']]
before={str(p):info(p) for p in paths}
origin=load(args.base/'source-and-remote.json');push=load(args.base/'push.response.json');live=load(args.base/'live/report.json')
check('recorded exact published head/remote/clean',origin['head']==HEAD and origin['remote_main']==HEAD+'\trefs/heads/main' and origin['clean'],'Record of publication time, not a new network/currentclean observation')
check('saved ordinary push succeeded without force',push['exit_code']==0 and '5ffdc6c..ac4e802  main -> main' in push['output'])
runset=api('runs-initial.response.json')['workflow_runs'];check('exact2push workflows for head',len(runset)==2 and {x['id'] for x in runset}=={40045915200,40045915202} and all(x['head_sha']==HEAD and x['event']=='push' and x['head_branch']=='main' for x in runset))
pages=api('pages-final.response.json');check('official Pages final success exacthead',pages['id']==40045915200 and pages['head_sha']==HEAD and pages['event']=='push' and pages['status']=='completed' and pages['conclusion']=='success' and pages['path']=='.github/workflows/pages.yml')
pagejobs=api('jobs-40045915200-initial.response.json')['jobs'];check('official Pages both check/deploy complete success',len(pagejobs)==2 and {x['name'] for x in pagejobs}=={'check','deploy'} and all(x['run_id']==40045915200 and x['status']=='completed' and x['conclusion']=='success' for x in pagejobs),[{k:x[k] for k in ['id','name','status','conclusion','run_id']} for x in pagejobs])
initial_jobs=api('jobs-40045915202-initial.response.json')['jobs'];progress=api('software-progress.response.json')['jobs'];software=next(x for x in runset if x['id']==40045915202)
expected={'check (ubuntu-24.04, x86_64)','check (ubuntu-22.04-arm, aarch64)','check (ubuntu-22.04, x86_64)','check (ubuntu-24.04-arm, aarch64)'}
check('official software4native job identities exactrun',len(progress)==4 and {x['name'] for x in progress}==expected and all(x['run_id']==40045915202 for x in progress) and {x['id'] for x in progress}=={x['id'] for x in initial_jobs})
check('saved software remains progress, no final pass invented',software['status']=='in_progress' and software['conclusion'] is None and all(x['status']=='in_progress' and x['conclusion'] is None for x in progress),'Later final response must be reviewed separately; no API/log fetch in this script.')
resources=[]
check('saved live sixresource report exacthead',live['result']=='PASS' and live['head']==HEAD and len(live['resources'])==6)
check('exact six expected paths',set(x['path'] for x in live['resources'])=={'index.html','styles.css','app.js','assets/favicon.svg','assets/usbip-flow.svg','assets/usbip-flow-mobile.svg'})
for item in live['resources']:
 p=args.base/'live'/item['path'];data=p.read_bytes();blob=gitblob('website/'+item['path']);current=(args.repo/'website'/item['path']).read_bytes()
 check('actual stored live HTTP200 '+item['path'],item['http_status']==200 and item['url']=='https://tiansongyu.github.io/usbip-client/'+item['path'])
 check('independent live length/SHA/Gitblob/current '+item['path'],len(data)==item['bytes'] and sha(data)==item['sha256'] and data==blob==current)
 check('saved HTTP Content-Length '+item['path'],int(item['headers']['Content-Length'])==len(data))
 resources.append({'path':item['path'],'http_status':item['http_status'],'bytes':len(data),'sha256':sha(data),'exact_ac4_git_blob':True})
html=(args.base/'live/index.html').read_text();check('live HTML new667/23/46/18 report and explicit scope',all(x in html for x in ['<dt>667</dt>','<dt>23</dt>','46 guests','<dt>18</dt>','1582f06','prepare-recording-shutdown-validation.md','Transport replies do not prove USB completion.','A stuck filesystem or kernel call can still delay exit.']))
manifest=Path('/tmp/rustusb-prepare-stop-software-ab-first-20261024/inputs-before.json');old=load(manifest);originals=[manifest];mapping=[]
check('actual dirtysoftware167 index valid',len(old)==167 and len({x['path'] for x in old})==167)
for row in old:
 blob=gitblob(row['path']);path=args.repo/row['path'];copy=manifest.parent/'source'/row['path'];copied=copy.read_bytes();current=path.read_bytes();originals.append(copy)
 mapping.append({'path':row['path'],'bytes':row['bytes'],'sha256':row['sha256'],'actual_run_copy':len(copied)==row['bytes'] and sha(copied)==row['sha256'],'published_git_blob':len(blob)==row['bytes'] and sha(blob)==row['sha256'],'current':current==blob})
check('all167 build inputs map actualsourcecopy/ac4/current',all(all(r[k] for k in ['actual_run_copy','published_git_blob','current']) for r in mapping))
browser=load(args.base/'browser-live.json');check('saved rootbrowser record scoped DOM not screenshot',browser['result']=='PASS_DOM_LAYOUT' and browser['head']==HEAD and 'no pixel screenshot' in browser['scope'] and 'no new no-JS/clipboard policy or reduced-motion emulation claim' in browser['scope'])
check('recorded desktop/mobile nohorizontaloverflow + loadedresponsiveimages',browser['desktop']['document_client_width']==browser['desktop']['document_scroll_width']==1265 and browser['mobile']['document_client_width']==browser['mobile']['document_scroll_width']==375 and browser['desktop']['images_loaded']==['favicon.svg','usbip-flow.svg'] and browser['mobile']['images_loaded']==['favicon.svg','usbip-flow-mobile.svg'])
check('recorded realkeyboard and console0 scope',browser['keyboard']==[{'key':'ArrowRight','selected':'Virtual devices','url_query':'backend=virtual'},{'key':'Home','selected':'Linux Native','url_query':'backend=native'}] and browser['console_warnings_errors']==[])
check('167mapping report does not relabel historical software/QEMU/CI',all(x in origin['scope'] for x in ['dirtyb1','1582','5ff','does not relabel']))
after={str(p):info(p) for p in paths};check('all15saved publication originals stable through audit',len(before)==15 and before==after)
sourceinfo=[info(p) for p in originals]
for row in sourceinfo:check('actualsoftware original still matches independent map '+Path(row['path']).name,info(Path(row['path']))==row)
(args.output/'source-input-map.json').write_text(json.dumps(mapping,indent=2)+'\n')
(args.output/'originals-sha256.json').write_text(json.dumps({'publication_before':list(before.values()),'publication_after_unchanged':before==after,'software_index_and_source_copies':sourceinfo},indent=2)+'\n')
status={'head':HEAD,'pages':{k:pages[k] for k in ['id','head_sha','status','conclusion','html_url']},'pages_jobs':[{k:x[k] for k in ['id','name','status','conclusion','run_id']} for x in pagejobs],'software_observation':{k:software[k] for k in ['id','head_sha','status','conclusion','html_url']},'software_jobs':[{k:x[k] for k in ['id','name','status','conclusion','run_id']} for x in progress]}
(args.output/'official-status-summary.json').write_text(json.dumps(status,indent=2)+'\n')
report={'schema_version':1,'review_result':'PASS' if all(x['passed'] for x in checks) else 'FAIL','stage':'initial_saved_publication_review','head':HEAD,'counts':{'live_resources':6,'mapped_build_inputs':167,'new_qemu_cases':0,'new_software_test_executions_recounted':0},'official_status':status,'resources':resources,'checks':checks,'diagnostic_check_count':len(checks),'scope':['Offline read-only use of saved official connector/HTTP/browser records and local Git object bytes; no browser/network/API/build/QEMU action.','Pages completed/success applies to this contenthead. Software run40045915202 remains inprogress in reviewed snapshot; only status, not final CI success or667details, is claimed.','Detailed actual667CI remains exact5ff40044605684; local actualsoftware remains dirtyb1 and QEMU remains clean1582.167source mapping is not a relabelled execution.','Browser observations are the root actual CUA record. This reviewer did not rerun browser or independently capture screenshot/pixels/no-JS/clipboard/reducedmotion.','Current Git/source byte equality does not newly assert current completeworkingtree cleanliness or remote status; remote/head are preserved publication-time observations.','Later softwareterminal snapshots require a new append-only report; this initial report stays unchanged.'],'source_manifest':{'path':str(manifest),**{k:info(manifest)[k] for k in ['bytes','sha256']}},'script':info(Path(__file__)),'artifact_sha256':{p.name:info(p) for p in args.output.iterdir() if p.is_file()}}
p=args.output/'report.json';p.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':report['review_result'],'checks':len(checks),'failed':[x for x in checks if not x['passed']],'report':info(p)}));raise SystemExit(0 if report['review_result']=='PASS' else 1)

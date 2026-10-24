#!/usr/bin/env python3
"""Append-only offline final status review. No network, browser, build, or job logs."""
from pathlib import Path
import hashlib,json
B=Path('/tmp/rustusb-prepare-stop-publication-ac4e802-20261024')
O=Path('/tmp/rustusb-prepare-stop-publication-final-independent-review-20261024')
INITIAL=Path('/tmp/rustusb-prepare-stop-publication-initial-independent-review-20261024/report.json')
HEAD='ac4e802848257e1a36767137e2e56bef4dda6683'
EXPECTED_INITIAL='811a3820d1fa046793421d3d989dcc3f755b25f798133335e4570b77217ebed7'
O.mkdir(exist_ok=False);(O/'raw').mkdir();checks=[]
def digest(b):return hashlib.sha256(b).hexdigest()
def meta(p):
 b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':digest(b)}
def check(name,ok,detail=None):checks.append({'check':name,'passed':bool(ok),'detail':detail})
def load(p):return json.loads(p.read_text())
names=['software-final.response.json','software-jobs-final.response.json','pages-final.response.json','jobs-40045915200-initial.response.json','jobs-40045915202-initial.response.json','runs-initial.response.json']
paths=[B/n for n in names]+[INITIAL];before={str(p):meta(p) for p in paths}
def api(name):
 raw=(B/name).read_bytes();x=json.loads(raw);check(name+' connector success',x.get('isError') is False);s=x['structuredContent']
 if 'content' in s:
  check(name+' official API endpoint',s['url'].startswith('https://api.github.com/repos/tiansongyu/usbip-client/actions/'),s['url']);return json.loads(s['content'])
 return s
initial=load(INITIAL);check('initial report retained unchanged and still progress-scoped',before[str(INITIAL)]['sha256']==EXPECTED_INITIAL and initial['review_result']=='PASS' and initial['official_status']['software_observation']['status']=='in_progress' and initial['official_status']['software_observation']['conclusion'] is None)
run=api('software-final.response.json');jobs=api('software-jobs-final.response.json')['jobs'];prior_jobs=api('jobs-40045915202-initial.response.json')['jobs']
check('final software exact pushhead/main/workflow',run['id']==40045915202 and run['head_sha']==HEAD and run['head_branch']=='main' and run['event']=='push' and run['path']=='.github/workflows/ci.yml' and run['run_attempt']==1)
check('final software actually completed success',run['status']=='completed' and run['conclusion']=='success',{'created_at':run['created_at'],'updated_at':run['updated_at'],'url':run['html_url']})
expected={120727480755:'check (ubuntu-24.04, x86_64)',120727480929:'check (ubuntu-22.04-arm, aarch64)',120727480952:'check (ubuntu-22.04, x86_64)',120727480965:'check (ubuntu-24.04-arm, aarch64)'}
check('same exact four native matrix job identities as saved initial',len(jobs)==4 and {j['id']:j['name'] for j in jobs}==expected and {j['id'] for j in prior_jobs}==set(expected))
for job in jobs:
 check('actual job completed success '+str(job['id']),job['run_id']==40045915202 and job['status']=='completed' and job['conclusion']=='success',{'name':job['name'],'status':job['status'],'conclusion':job['conclusion']})
 # A job can legitimately have skipped always/conditional steps. No per-test
 # execution total is inferred from these step status observations.
 check('no failed/cancelled step hidden in successful job '+str(job['id']),all(s['conclusion'] not in ('failure','cancelled','timed_out','action_required') for s in job['steps']))
pages=api('pages-final.response.json');pagejobs=api('jobs-40045915200-initial.response.json')['jobs']
check('Pages remains exact publishedhead completed success',pages['id']==40045915200 and pages['head_sha']==HEAD and pages['status']=='completed' and pages['conclusion']=='success' and pages['event']=='push')
check('Pages check and deploy actually completed success',len(pagejobs)==2 and {j['name'] for j in pagejobs}=={'check','deploy'} and all(j['run_id']==40045915200 and j['status']=='completed' and j['conclusion']=='success' for j in pagejobs))
check('finalhead matches independently reviewed live and source mapping',initial['head']==HEAD and initial['counts']['live_resources']==6 and initial['counts']['mapped_build_inputs']==167)
for name in ['software-final.response.json','software-jobs-final.response.json','pages-final.response.json']:
 raw=(B/name).read_bytes();(O/'raw'/name).write_bytes(raw);check('saved exact official wrapper bytes '+name,meta(O/'raw'/name)['sha256']==before[str(B/name)]['sha256'])
after={str(p):meta(p) for p in paths};check('all7 consulted official/initial originals unchanged',before==after)
status={'head':HEAD,'software':{k:run[k] for k in ['id','head_sha','status','conclusion','run_attempt','event','html_url','created_at','updated_at']},'software_jobs':[{k:j[k] for k in ['id','name','run_id','status','conclusion']} for j in jobs],'pages':{k:pages[k] for k in ['id','head_sha','status','conclusion','html_url']},'pages_jobs':[{k:j[k] for k in ['id','name','run_id','status','conclusion']} for j in pagejobs]}
(O/'official-final-summary.json').write_text(json.dumps(status,indent=2)+'\n')
(O/'originals-before-after.json').write_text(json.dumps({'before':list(before.values()),'after':list(after.values()),'unchanged':before==after},indent=2)+'\n')
(O/'reviewer.py').write_bytes(Path(__file__).read_bytes())
report={'schema_version':1,'review_result':'PASS' if all(c['passed'] for c in checks) else 'FAIL','stage':'final_saved_status_review','head':HEAD,'actual_status':status,'initial_review_preserved':meta(INITIAL),'checks':checks,'diagnostic_check_count':len(checks),'scope':['Saved official GitHub connector/API responses only; all4native jobs and software workflow completed/success on exact ac4head. Pages exactac4 check/deploy success is separately preserved.','No new log fetch/testcount reconstruction: detailed each667/39 CI validation remains5ffdc6c run40044605684. This ac4 review claims status only, not a new667 detailed-logs audit.','No new software/QEMU/USB cases, network/remote fetch, browser, Cargo, binaries, Gitcommands/mutation or repository write.','Six saved live bytes and167actualsoftware source mapping remain the independently reviewed initial report; original dirtyb1 and clean1582 attribution is unchanged.','Root actual CUA record remains DOM/layout/keyboard/console evidence, no screenshot/pixel/newNoJS/clipboard/reducedmotion claim by this review.','Initial inprogress report and its SHA stay unchanged; this final report is additional evidence rather than rewriting the earlier observation.'],'script':meta(Path(__file__)),'output_files':[{**meta(p)} for p in sorted(O.rglob('*')) if p.is_file()]}
p=O/'report.json';p.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':report['review_result'],'checks':len(checks),'failed':[c for c in checks if not c['passed']],'report':meta(p)}));raise SystemExit(0 if report['review_result']=='PASS' else 1)

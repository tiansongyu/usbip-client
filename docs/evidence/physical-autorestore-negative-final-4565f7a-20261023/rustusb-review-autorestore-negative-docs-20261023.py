#!/usr/bin/env python3
"""Final read-only documentation lock, local links and GFM table structure."""
from pathlib import Path
from urllib.parse import urlsplit,unquote
from markdown_it import MarkdownIt
import datetime,hashlib,json,subprocess
REPO=Path('/home/ubuntu/github/usbip-client')
NAMES=['AGENTS.md','README.md','README.zh-CN.md','docs/acceptance.md','docs/architecture.md','docs/research/physical-driver-autorestore-negative-design.md','docs/research/physical-driver-autorestore.md','docs/research/physical-driver-autorestore-negatives.md','docs/roadmap.md','docs/rust-physical-server.md','docs/testing.md','docs/work-log.md']
def meta(p):
 b=p.read_bytes();return {'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def read(p):return json.loads(p.read_text())
files={n:meta(REPO/n) for n in NAMES};checks=[]
def check(k,b,detail=None):checks.append({'id':k,'result':'PASS' if b else 'FAIL','detail':detail})
md=MarkdownIt('commonmark').enable('table');links=[];tables=[]
for name in NAMES:
 path=REPO/name;tokens=md.parse(path.read_text())
 current=None
 for token in tokens:
  if token.type=='table_open':current=[]
  elif token.type=='table_close':
   if name=='docs/acceptance.md':tables.append(current)
   current=None
  if token.type=='inline':
   if current is not None:current.append(token.content)
   for child in token.children or []:
    if child.type not in ['link_open','image']:continue
    url=child.attrGet('href' if child.type=='link_open' else 'src');parts=urlsplit(url)
    if parts.scheme or url.startswith('//') or not parts.path:continue
    target=(path.parent/unquote(parts.path)).resolve() if not parts.path.startswith('/') else REPO/parts.path.lstrip('/')
    links.append({'source':name,'target':url,'kind':child.type,'exists':target.exists()})
check('all_local_link_targets_exist',all(x['exists'] for x in links),{'count':len(links),'missing':[x for x in links if not x['exists']]})
e28=[t for t in tables if 'E28' in t]
check('E28_one_real_table_row',len(e28)==1)
check('E01_to_E28_same_evidence_table',len(e28)==1 and [x for x in e28[0] if len(x)==3 and x.startswith('E')]==[f'E{i:02}' for i in range(1,29)])
texts={n:(REPO/n).read_text() for n in NAMES};main=texts['docs/research/physical-driver-autorestore-negatives.md']
check('L_manual_P_C_automatic_explicit','L的尾部是**人工恢复与只读协调**，不能计为自动恢复成功' in main and 'P/C的尾部才由产品自动恢复' in main)
check('startup_Status_before_pure_boundary','启动doctor/Status仍会先调用' in main and '纯观察primary仅保证早于pending API与新grant' in main)
check('FIFO_parent_source_constraint','父shell不常驻打开控制FIFO为冻结源码约束，不能当作完整系统FD审计' in main)
check('no_CONNECT_or_bind_tracing_claim','没有逐人工bind或产品CONNECT syscall跟踪' in main)
check('historical_failures_retained','旧1342a6a恢复FAIL、43d6d43脚本FAIL' in main)
check('no_phantom_nine_L_count','九个相关' not in main and '窗口及三轮实际数据核验' in main)
check('testing_historical_scope_explicit','该2ab9976矩阵未覆盖' in texts['docs/testing.md'] and '后续独立运行见下一节' in texts['docs/testing.md'])
check('full_Phase2_and_hardware_unfinished','完整Phase2' in main and '真实G1/G2' in main and '该建议不等于Phase3实现或验收' in main)
check('old_helper_only_and_ELF_limit','只把helper称为旧producer，不声称整套旧前端升级测试' in main and '索引没有保存ELF本体' in main)
check('counts_not_USB_cases','不是USB测试case或设备类型数量' in main and '不是更多USB case' in main)
check('source_freeze_vs_later_docs','运行后才写本轮交付文档' in main)
pubpath=REPO/'docs/evidence/publication-autorestore-negative-4565f7a-20261023.json';pub=read(pubpath)
ciroot=REPO/'docs/evidence/physical-autorestore-negative-ci-4565f7a-20261023';ci=read(ciroot/'review.json')
check('CI_exact_head_and_counts',pub['git_commit']==ci['head_sha']=='4565f7aaddb5a9377c15261a288ca9c3a57eebe5' and pub['result']==ci['result']=='PASS' and len(pub['artifacts'])==48 and pub['independent_audit_checks']==ci['check_count']==219 and ci['totals']['jobs']==4 and ci['totals']['source_inputs_per_job']==187)
check('CI_four_native_job_evidence_summary',len(ci['jobs'])==4 and all(j['unit_test_executions']==268 and j['test_result_rows']==31 and j['default_passed']==90 and j['allfeatures_passed']==158 and j['vendor_passed']==20 and j['status']=='completed' and j['conclusion']=='success' and len(j['legacy_backpressure']['checks'])==2 and len(j['signals_cases'])==4 for j in ci['jobs']) and {(j['native_environment']['ubuntu_version'],j['native_environment']['arch']) for j in ci['jobs']}=={('22.04','x86_64'),('22.04','aarch64'),('24.04','x86_64'),('24.04','aarch64')})
check('CI_and_Pages_attempt1',ci['run']['id']==39928383971 and ci['pages']['run']['id']==39928383815 and all(r['run_attempt']==1 and r['status']=='completed' and r['conclusion']=='success' for r in [ci['run'],ci['pages']['run']]))
check('Pages_versioned_counts_and_six_members',ci['pages']['site_checks']==115 and len(ci['pages']['archive_members'])==6 and ci['pages']['artifact_id']==12489134583 and '115检查' in main and '119项' in main)
check('CI_review_correction_explicit',bool(ci['review_corrections']) and (ciroot/'review-initial.json').exists() and '不是CI失败或重跑' in main and '隐藏`.nojekyll`' in main)
check('website_HTML_unchanged',subprocess.check_output(['git','diff','HEAD','--','website/index.html'],cwd=REPO)==b'')
check('twelve_docs_stable_while_reviewing',files=={n:meta(REPO/n) for n in NAMES})
result={'schema_version':1,'result':'PASS' if all(c['result']=='PASS' for c in checks) else 'FAIL','at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head_at_review':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),'scope':'Final semantic/evidence-attribution review of12 delivery documents, local target existence and actual Markdown table parse; no build, USB, QEMU, network or repository changes.','checks_count':len(checks),'checks':checks,'final_documents':files,'local_links':links,'acceptance_table_parser':'Installed markdown-it-py CommonMark parser with built-in table extension; E01-E28 found once in same parsed table.','review_history':[{'issue':'E28 separated from table by blank line','resolution':'Root removed separating blank; parser confirms all28 evidence rows in one table.'},{'issue':'Unsupported nine-count wording for L','resolution':'Root replaced with window/three-cycle wording consistent with actual evidence.'},{'issue':'Old testing paragraph read as still-current unexecuted negative scope','resolution':'Root made2ab9976 historical limitation explicit and linked the subsequent section.'}],'semantic_review':['Runtime318/293/313 checks and6guests belong exactly to4565f7a; historical2ab9976 software/QEMU and older FAIL versions are retained.','L tail is explicitly administrator restoration+read-only reconciliation; P/C require new helper for automatic tail.','Current policy startup behavior and immutable grant authorization remain separate; no FD revocation/Phase2 completion/hardware extrapolation.','Raw and source constraints are distinguished for parent FIFO, no CONNECT/bind syscall trace, Status before pure primary, and bounded windows.','CI219 review assertions,187 source inputs,4native jobs each268 tests/2backpressure/4signals, Pages115 vs root-reported local119 remain separate counts.','CI review-initial nojekyll expectation correction is retained as parser correction rather than CI failure/rerun.'],'evidence_crosschecked':{'publication_index':{'path':str(pubpath),**meta(pubpath)},'independent_CI_report':{'path':str(ciroot/'review.json'),**meta(ciroot/'review.json')},'runtime_negative_index':{'path':str(REPO/'docs/evidence/physical-autorestore-negative-final-4565f7a-20261023/manifest.json'),**meta(REPO/'docs/evidence/physical-autorestore-negative-final-4565f7a-20261023/manifest.json')}},'boundaries':['CI section checked against existing independent audit summary and archived publication index; this review did not rerun its219 checks or fetch live Actions.','Local119 website/README checker count is the root-reported current result, not a checker reexecution here.','Local links checked for existing file targets only; external URLs and same-page fragments were not network-validated.','Documentation is intentionally dirty after completed clean runtime; no current git-clean assertion is applied to historical runs.'],'repo_writes':False,'Cargo_QEMU_execution':False,'parser':{'path':str(Path(__file__)),**meta(Path(__file__))}}
out=Path('/tmp/rustusb-autorestore-negative-docs-final-review-20261023.json');out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'path':str(out),'result':result['result'],'checks':len(checks),'local_links':len(links),'failures':[x['id'] for x in checks if x['result']=='FAIL'],**meta(out)},ensure_ascii=False))

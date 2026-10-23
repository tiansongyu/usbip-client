import datetime,hashlib,json,pathlib,subprocess
R=pathlib.Path('/home/ubuntu/github/usbip-client');B=R/'docs/evidence/shared-owner-ci-fixture-2638a8c-20261024';C=R/'docs/evidence/shared-owner-ci-2638a8c-20261024'
def read(p):return json.loads(pathlib.Path(p).read_text())
def sha(p):return hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest()
files=read('/tmp/rustusb-shared-owner-doc-links-final-20261024.json')['source']
sources=[{'path':x,'bytes':(R/x).stat().st_size,'sha256':sha(R/x)} for x in files]
inputs=read(B/'software/inputs-before.json');mismatches=[]
for x in inputs:
 blob=subprocess.check_output(['git','show','2638a8c4700d79233d283de1a168f574be5f8ee4:'+x['path']],cwd=R)
 if len(blob)!=x['bytes'] or hashlib.sha256(blob).hexdigest()!=x['sha256']:mismatches.append(x['path'])
assert len(inputs)==152 and not mismatches
ci=read(C/'rustusb-shared-owner-ci-testfix-independent-summary-20261024.json');scope=read(B/'rustusb-shared-owner-wchan-final-software-scope-20261024.json');dom=read('/tmp/rustusb-shared-owner-site-dom-qa-20261024.json')
assert sha(R/'website/index.html')==dom['html_sha256']
assert '匹配2638a8c输入的完整530软件通过；clean b45c2e8' in (R/'docs/testing.md').read_text()
assert 'The final [final software archive]' not in (R/'docs/research/shared-owner-validation.md').read_text()
assert ci['run_id']==39999926362 and len(ci['jobs'])==4
jobs=[]
for j in ci['jobs']:
 assert j['result']=='PASS' and j['tests']=={'passed':530,'failed':0,'ignored':0,'result_lines':39}
 expected='anon_pipe_write' if '24.04' in j['name'] else 'pipe_write'
 obs=[x['blocked']['blocked_writer_wchan'] for x in j['secure_five_cases']['cases'] if x['blocked']];assert obs==[expected]*3
 jobs.append({'id':j['job_id'],'name':j['name'],'tests':j['tests'],'blocked_waits':obs})
for name,expected in scope['release_elf_same_as_qemu_b45'].items():assert sha(R/'target/release'/name)==expected
refs=[C/'rustusb-shared-owner-ci-testfix-independent-summary-20261024.json',C/'scope.json',B/'software/start.json',B/'software/result.json',B/'rustusb-shared-owner-wchan-final-software-scope-20261024.json']
d={'schema_version':1,'result':'PASS_NARROW_FINAL_DOCUMENT_DELTA','utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip(),'sources':sources,'evidence':[{'path':str(p.relative_to(R)),'sha256':sha(p)} for p in refs],'confirmed':{'fourth_software':'b45 HEAD modified test worktree;530/39/zero failure or ignored;152 inputs independently matched2638 Git blobs in this review','preceding_software':'4d HEAD modified foreground diagnostic worktree;530 retained with original inputs matchingb45','qemu':'nine original runs remain cleanb45/18guest0; not relabelled2638','release_elf_sha256':scope['release_elf_same_as_qemu_b45'],'current_release_elf_matches_archived_values':True,'new_ci':{'run_id':ci['run_id'],'head':ci['head'],'jobs':jobs,'scope':'Read archived independent parser summary, not an additional parse of official CI logs. FiveTLS and original2backpressure4signal scope retained.'},'old_ci':'b45 39997678019 two success/two failure remains; missing old rawwchan not reconstructed','readme_and_site':'530local,9QEMU18guest,4nativeCI valid with differing precise source attributions linked by report','dom_qa':'Current HTML SHA equals archived root DOM-QA hash; no new browser/pixel QA by this reviewer','remaining_limits':'Unchanged WALpreparationACKstall, acceptedinitializer source-only, no new hardware/fullPhase3 claims'},'closed_findings':[{'id':'DOC-TESTING-FINAL-SOFTWARE-ATTRIBUTION','resolution':'Root corrected testing E32 to2638 input software vs cleanb45 QEMU'},{'id':'DOC-FINAL-ARCHIVE-WORDING','resolution':'Root removed duplicate final wording'}],'mustfix':[],'root_static_scope':'Parent16doc339 local-link/table checks and136site checks reported; this delta review locks16 current sources but does not count those as its own new test executions.','reviewer_diagnostic':'Initial in-memory report script overly rejected the link label final software archive while root correctly removed only duplicated preceding final. Corrected exact duplicated phrase predicate; no source/evidence change or runtime failure.','actions':{'repo_write':False,'cargo_build_or_tests':False,'qemu_or_product':False,'browser_or_screenshot':False,'live_ci_or_web':False}}
p=pathlib.Path('/tmp/rustusb-shared-owner-doc-final-delta-review-20261024.json');p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(p);print(sha(p));print('html',sha(R/'website/index.html'));print('report',sha(R/'docs/research/shared-owner-validation.md'))

#!/usr/bin/env python3
"""Read-only independent xtrace/test/input audit; no Cargo or verifier import."""
import collections,hashlib,json,re,shlex,subprocess
from pathlib import Path
ROOT=Path('/home/ubuntu/github/usbip-client');BASE=Path('/tmp/rustusb-data-link-management-software-first-20261024');OLD=Path('/tmp/rustusb-data-link-short-reply-regression-20261024');OUT=Path('/tmp/rustusb-data-link-management-software-first-independent-review-20261024.json')
def require(v,s):
 if not v:raise ValueError(s)
def artifact(p):
 p=Path(p);b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
files=sorted(p for p in BASE.iterdir() if p.is_file())+sorted(p for p in OLD.iterdir() if p.is_file());originals=[artifact(p) for p in files]
start=json.loads((BASE/'start.json').read_text());result=json.loads((BASE/'result.json').read_text());before=json.loads((BASE/'inputs-before.json').read_text());after=json.loads((BASE/'inputs-after.json').read_text());require(before==after and len(before)==165,'165 exact before/after inputs');require(len({r['path'] for r in before})==165,'unique paths')
current=[]
for row in before:
 p=ROOT/row['path'];b=p.read_bytes();require(len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'],'changed source '+row['path']);current.append(row)
indexed={r['path'] for r in before};required={str(p.relative_to(ROOT)) for p in (ROOT/'crates').rglob('*.rs')}|{str(p.relative_to(ROOT)) for p in (ROOT/'crates').rglob('Cargo.toml')}|{'Cargo.toml','Cargo.lock','scripts/check.sh'};require(required<=indexed,'crate source missing from scope')
require(result['command']==['bash','-x','scripts/check.sh'] and result['exit']==0 and result['rust_inputs_unchanged'] is True,'actual gate completion');require(result['head_before']==result['head_after']==start['head']=='3ca4947083e06fbdf13d2448601c55848ac9fbde','baseline head');require(start['clean'] is False,'must preserve candidate dirty attribution')
log=(BASE/'check.log').read_text();lines=log.splitlines();commands=[];expected=[]
for line in (ROOT/'scripts/check.sh').read_text().splitlines():
 if line.startswith('cargo ') or line.startswith('RUSTDOCFLAGS='):expected.append(line)
for number,line in enumerate(lines,1):
 if line.startswith('+ cargo '):commands.append({'line':number,'argv':shlex.split(line[2:]),'env':{}})
 if line.startswith('+ RUSTDOCFLAGS='):
  require(line=="+ RUSTDOCFLAGS='-D warnings'",'rustdoc warning env');require(number<len(lines) and lines[number].startswith('+ cargo doc '),'env immediately precedes doc')
  # Applied below to each exact doc command.
require(len(commands)==10,'10 traced cargo commands')
for row,text in zip(commands,expected):
 words=shlex.split(text)
 if words[0].startswith('RUSTDOCFLAGS='):row['env']={'RUSTDOCFLAGS':'-D warnings'};words=words[1:]
 require(row['argv']==words,'actual xtrace argv mismatch')
require(any('+ set -euo pipefail'==line for line in lines),'script stop-on-error')
rows=[];label=None;running=None;names=[];group='default';qualified=set();profiles=[]
for number,line in enumerate(lines,1):
 if 'Finished `' in line:profiles.append(re.search(r'Finished `([^`]+)`',line).group(1))
 if line.startswith('     Running ') or line.startswith('   Doc-tests '):
  require(label is None,'unfinished result');label=line.strip();running=None;names=[]
  if 'target/nusb-tests/' in line:group='vendor'
  elif line.startswith('     Running ') and 'deps/rustusb-' in line and rows:group='all_features'
 if label and re.fullmatch(r'running \d+ tests?',line):running=int(line.split()[1])
 m=re.fullmatch(r'test (.+) \.\.\. (ok|FAILED|ignored)(?:.*)?',line)
 if label and m:names.append({'name':m.group(1),'status':m.group(2)})
 m=re.match(r'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',line)
 if m:
  require(label is not None,'missing package');state,*values=m.groups();passed,failed,ignored,measured,filtered=map(int,values);require(state=='ok' and failed==ignored==measured==filtered==0,'nonpassing test summary');require(running==passed==len(names),'test name/running/summary mismatch');require(all(x['status']=='ok' for x in names),'named failing test')
  if label.startswith('Doc-tests'):package=label.split()[-1];target='doctest'
  else:target=label.split(' (')[0];package=re.sub(r'-[0-9a-f]+$','',Path(re.search(r'\(([^)]+)\)',label).group(1)).name)
  for name in names:qualified.add((package,target,name['name']))
  rows.append({'group':group,'label':label,'passed':passed,'failed':failed,'ignored':ignored,'line':number,'names':names});label=None
require(label is None and len(rows)==39,'39 complete result rows');counts={g:sum(r['passed'] for r in rows if r['group']==g) for g in ('default','all_features','vendor')};require(counts=={'default':260,'all_features':346,'vendor':20},'exact counts');require(sum(counts.values())==626,'626 total')
require(profiles==['dev','test','release','dev','dev','test','release','dev','test'],'9 visible command finish stages');require(not re.search(r'^error:|^warning:',log,re.M),'unexpected compiler diagnostics')
claimed=json.loads((BASE/'test-counts.json').read_text());require({k:claimed[k] for k in ('default','all_features','vendor','executions','failed','ignored')}=={'default':260,'all_features':346,'vendor':20,'executions':626,'failed':0,'ignored':0},'parent count index inconsistent');require([{'label':r['label'],'passed':r['passed'],'failed':r['failed'],'ignored':r['ignored']} for r in rows]==claimed['rows'],'parent package rows inconsistent')
regression='server::tls_tests::framed_mtls_catalog_completes_full_reply_and_both_half_closes_without_claims'
oldlog=(OLD/'first.log').read_text();oldmeta=json.loads((OLD/'first.json').read_text());require(oldmeta['exit']!=0 and '0 passed; 1 failed' in oldlog and regression+' ... FAILED' in oldlog and 'ConnectionAborted' in oldlog,'old runtime failure not present');require('could not compile' not in oldlog,'old failure was compilation')
for group in ('default','all_features'):require(any(r['group']==group and any(t['name']==regression for t in r['names']) for r in rows),'same regression missing '+group)
def function(text,name):
 a=text.index('async fn '+name+'(');body=text.index('{',a);depth=1;i=body+1;quote=escape=comment=False
 while depth:
  ch=text[i]
  if comment:
   if ch=='\n':comment=False
  elif quote:
   if escape:escape=False
   elif ch=='\\':escape=True
   elif ch=='"':quote=False
  elif text[i:i+2]=='//':comment=True;i+=1
  elif ch=='"':quote=True
  elif ch=='{':depth+=1
  elif ch=='}':depth-=1
  i+=1
 return text[a:i].encode()
oldsource=(OLD/'test-source.rs').read_bytes();require(hashlib.sha256(oldsource).hexdigest()==oldmeta['test_source_sha256'],'old testsource hash');name=regression.split('::')[-1];oldfn=function(oldsource.decode(),name);newfn=function((ROOT/'crates/rustusb-rust-backend/src/server_tls_tests.rs').read_text(),name);require(oldfn==newfn,'original failing test function changed')
regressions=['secure::tests::framed_management_fin_follows_complete_response_and_keeps_both_borrows_alive','secure::tests::framed_management_rejects_trailing_application_data','secure::tests::framed_management_peer_without_eof_hits_the_single_production_deadline','server::tls_tests::managed_framed_catalog_and_acl_refusal_keep_fin_eof_without_device_admission','server::tls_tests::framed_busy_reply_is_complete_and_clean_while_original_import_stays_owned','jobs::management_tests::discovery_stop_during_peer_fin_wait_retains_and_joins_the_driver','jobs::management_tests::actual_client_refusal_keeps_original_failure_without_helper_attempt','jobs::native::tests::actual_native_catalog_and_short_refusal_finish_on_the_same_tls_owner']
for name in regressions:
 for group in ('default','all_features'):require(any(r['group']==group and any(t['name']==name for t in r['names']) for r in rows),'missing new test '+group+' '+name)
tool=json.loads((BASE/'toolchain-binaries.json').read_text());require(tool['rustc']['exit']==tool['cargo']['exit']==tool['host']['exit']==0,'tool command failed');require('rustc 1.97.1 ' in tool['rustc']['stdout'] and 'host: x86_64-unknown-linux-gnu' in tool['rustc']['stdout'],'actual compiler identity');require('cargo 1.97.1 ' in tool['cargo']['stdout'],'actual cargo version');binaries=[]
for row in tool['binaries']:
 p=ROOT/row['path'];b=p.read_bytes();require(len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'],'current actual ELF mismatch');binaries.append(row)
require({r['path']:r['sha256'] for r in binaries}=={'target/release/rustusb':'bb6fb276c0cbfbb73c2cdb8af0f73d87045ac7235b4943cf096c0643d570814a','target/release/rustusb-helper':'b4af4b5f04fd5ac67fd09d2ff176e7ed2c3613ae950266dcd74b3c7be61f3fe5'},'wrong finalELF provenance')
comparisons=[]
for name in ['crates/rustusb-protocol/src/secure.rs','crates/rustusb-rust-backend/src/link.rs','crates/rustusb-rust-backend/src/server.rs','crates/rustusb-rust-backend/src/managed.rs']:
 old=subprocess.check_output(['git','show',f'{oldmeta["head"]}:{name}'],cwd=ROOT);new=(ROOT/name).read_bytes();comparisons.append({'path':name,'baseline_head':oldmeta['head'],'old_git_blob_sha256':hashlib.sha256(old).hexdigest(),'current_frozen_sha256':hashlib.sha256(new).hexdigest(),'changed':old!=new})
require(all(r['changed'] for r in comparisons),'expected production lifetime changes absent')
require(originals==[artifact(p) for p in files],'raw artifacts changed during review')
report={'schema_version':1,'result':'PASS','scope':'Independent read-only audit of second distinct full software gate after framed-management fix. No compiler/test/QEMU invoked by reviewer; old608/oldQEMUFAIL and oldregressionFAIL retained separately.','baseline_head':start['head'],'run_start_clean':False,'source_scope':start['scope'],'actual_commands':commands,'test_execution_counts':counts,'total_test_executions':626,'result_rows':39,'failed':0,'ignored':0,'observed_distinct_qualified_test_names':len(qualified),'counting_scope':'626 executions, not626independentUSBcases. Default/allfeatures repeated names separately executed;16zero-countdoctest rows add no executions.','packages':[{'group':r['group'],'label':r['label'],'passed':r['passed'],'line':r['line']} for r in rows],'original_runtime_regression':{'name':regression,'old_runtime':'0pass1fail ConnectionAborted, notcompilefailure','old_log':artifact(OLD/'first.log'),'old_metadata':oldmeta,'original_function_unchanged':True,'function_bytes':len(oldfn),'function_sha256':hashlib.sha256(oldfn).hexdigest(),'current_actual_default_and_all_features':'PASS both'},'production_source_delta_from_old_baseline':comparisons,'source_logic_change':'Added5sborrowedFIN+EOF helper; backendshortbranches retainIOthroughnaturalPeerEofjoin instead ofdroppinghalvesthenforcedStop. Existingfirstfailingtestbodyexactlyunchanged.','new_management_regressions_both_feature_runs':regressions,'input_guards':{'count':165,'before_equals_after':True,'all_current_sha_match':True,'all_current_crate_rs_toml_root_lock_check_indexed':True,'rows':current},'actual_toolchain':tool,'feature_provenance':['Actualbash-x records exact default/allfeatures Clippy/test/release/doc and vendortokio commands; rustdoc -Dwarnings env precedes bothdoccommands.','FinalELF receipt is actual allfeatures release stage output and currentbytehash match; no same-bytehash isclaimedfor earlierdefaultrelease whichcanbeoverwritten.','Native-onlybuild/dependencytree andlocalruntime signal/backpressure checks are outside this10-step evidence scope.','This is software overdirtycandidate3ca4947; sourcehashes—notunchangedGitHEADalone—bindthe actualfix. No failedQEMUrun promotedtoPASS.'],'artifacts_before_after_unchanged':True,'artifacts':originals,'reviewer_parser':artifact(__file__)}
OUT.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'path':str(OUT),'bytes':OUT.stat().st_size,'sha256':artifact(OUT)['sha256'],'counts':counts,'qualified_names':len(qualified),'same_failing_test_unchanged':True,'inputs':165}))

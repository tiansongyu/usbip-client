#!/usr/bin/env python3
"""Independent read-only software-gate parser; no Cargo/test/verifier invocation."""
from pathlib import Path
import hashlib,json,re,subprocess
ROOT=Path('/home/ubuntu/github/usbip-client')
BASE=Path('/tmp/rustusb-data-link-software-first-20261024')
OUTPUT=Path('/tmp/rustusb-data-link-software-first-independent-review-20261024.json')
def raw(path):
 b=Path(path).read_bytes();return {'path':str(path),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def need(condition,what):
 if not condition:raise ValueError(what)
originals=sorted(BASE.iterdir());before_artifacts=[raw(p) for p in originals if p.is_file()]
start=json.loads((BASE/'start.json').read_text());result=json.loads((BASE/'result.json').read_text())
pre=json.loads((BASE/'inputs-before.json').read_text());post=json.loads((BASE/'inputs-after.json').read_text());before_format=json.loads((BASE/'inputs-before-format.json').read_text())
need(len(pre)==len(post)==164,'input count164');need(pre==post,'before/after byte indexes differ')
need(len({x['path'] for x in pre})==len(pre),'duplicate source input path')
indexed_paths={x['path'] for x in pre}
crate_inputs={str(p.relative_to(ROOT)) for p in (ROOT/'crates').rglob('*.rs')} | {str(p.relative_to(ROOT)) for p in (ROOT/'crates').rglob('Cargo.toml')} | {'Cargo.toml','Cargo.lock','scripts/check.sh'}
need(crate_inputs<=indexed_paths,'missing crate Rust/TOML/root/check input')
current=[]
for row in pre:
 p=ROOT/row['path'];b=p.read_bytes();need(len(b)==row['bytes'] and hashlib.sha256(b).hexdigest()==row['sha256'],'current source mismatch '+row['path']);current.append({'path':row['path'],'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()})
script=(ROOT/'scripts/check.sh').read_text();commands=[]
for line in script.splitlines():
 if line.startswith('cargo ') or line.startswith('RUSTDOCFLAGS='):commands.append(line)
expected=['cargo fmt --all -- --check','cargo clippy --workspace --all-targets --locked -- -D warnings','cargo test --workspace --locked','cargo build --workspace --release --locked','RUSTDOCFLAGS="-D warnings" cargo doc --workspace --no-deps --locked','cargo clippy --workspace --all-targets --all-features --locked -- -D warnings','cargo test --workspace --all-features --locked','cargo build --workspace --all-features --release --locked','RUSTDOCFLAGS="-D warnings" cargo doc --workspace --all-features --no-deps --locked','cargo test --manifest-path vendor/nusb/Cargo.toml --lib --features tokio --locked --target-dir target/nusb-tests']
need(commands==expected,'10 exact command definitions differ');need('set -euo pipefail' in script,'missing script error propagation');need(any(x['path']=='scripts/check.sh' for x in pre),'script absent from input freeze')
need(result['command']==['bash','scripts/check.sh'] and result['exit']==0,'not successful actual script completion');need(result['input_count']==164 and result['rust_inputs_unchanged'] is True,'run input guards')
need(start['head']==result['head_before']==result['head_after']=='031f63d9b27fa8712a13089f95aa61c1c2c223aa','wrong baseline head');need(start['clean'] is False,'must retain dirty candidate attribution')
log=(BASE/'check.log').read_text();lines=log.splitlines();rows=[];label=None;tests=[];running=None;group='default';qualified=set();finished=[];doc_generated=[]
for number,line in enumerate(lines,1):
 if re.match(r'\s+Finished `',line):finished.append({'line':number,'text':line.strip(),'profile':re.search(r'Finished `([^`]+)`',line).group(1)})
 if line.strip().startswith('Generated '):doc_generated.append(number)
 if line.startswith('     Running ') or line.startswith('   Doc-tests '):
  need(label is None,'previous test result missing');label=line.strip();tests=[];running=None
  if 'target/nusb-tests/' in line:group='vendor'
  elif line.startswith('     Running ') and 'deps/rustusb-' in line and rows:group='all_features'
 if label and re.fullmatch(r'running \d+ tests?',line):running=int(line.split()[1])
 match=re.fullmatch(r'test (.+) \.\.\. (ok|FAILED|ignored)(?:.*)?',line)
 if label and match:tests.append({'name':match.group(1),'status':match.group(2)})
 match=re.match(r'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',line)
 if match:
  need(label is not None,'unlabeled test result');state,passed,failed,ignored,measured,filtered=match.groups();passed,failed,ignored,measured,filtered=map(int,(passed,failed,ignored,measured,filtered))
  need(state=='ok' and failed==ignored==measured==filtered==0,'failed/ignored/filtered test result')
  need(running==passed+failed+ignored,'running total mismatch');need(len(tests)==passed,'test name rows mismatch');need(all(t['status']=='ok' for t in tests),'non-ok named test')
  if 'Doc-tests' in label:package=label.split()[-1];target='doctest'
  else:
   path=re.search(r'\(([^)]+)\)',label).group(1);artifact=Path(path).name
   package=re.sub(r'-[0-9a-f]+$','',artifact);target=label.split(' (')[0]
  for test in tests:qualified.add((package,target,test['name']))
  rows.append({'group':group,'label':label,'result_line':number,'passed':passed,'failed':failed,'ignored':ignored,'test_names':tests})
  label=None
need(label is None,'unfinished test group');need(len(rows)==39,'expected39 result rows')
counts={g:sum(r['passed'] for r in rows if r['group']==g) for g in ('default','all_features','vendor')}
need(counts=={'default':251,'all_features':337,'vendor':20},'execution counts differ');need(sum(counts.values())==608,'total608')
need([r['profile'] for r in finished]==['dev','test','release','dev','dev','test','release','dev','test'],'wrong nine visible terminal stages')
need(len(doc_generated)==2,'two rustdoc completions missing')
claimed=json.loads((BASE/'test-packages.json').read_text());need([{'label':r['label'],'passed':r['passed'],'failed':r['failed'],'ignored':r['ignored']} for r in rows]==claimed,'parent test-packages index differs from independent reconstruction')
claimed_counts=json.loads((BASE/'test-counts.json').read_text());need({k:claimed_counts[k] for k in ['result_lines','passed','failed','ignored']}=={'result_lines':39,'passed':608,'failed':0,'ignored':0},'parent count index differs')
need('error: ' not in log and not re.search(r'^warning:',log,re.M),'unexpected compiler error/warning')
package_counts={g:[{'label':r['label'],'passed':r['passed']} for r in rows if r['group']==g] for g in counts}
# Execution evidence is the exact sequential set-e script + actual successful
# invocation; quiet fmt does not magically acquire an output marker.
stage_evidence=[None,finished[0],finished[1],finished[2],finished[3],finished[4],finished[5],finished[6],finished[7],finished[8]]
steps=[{'step':i+1,'command':c,'result':'PASS','terminal_output':e,'provenance':'Frozen scripts/check.sh, sequential set-e invocation exit0; fmt is intentionally silent.' if i==0 else 'Ordered actual Cargo completion plus frozen command source.'} for i,(c,e) in enumerate(zip(commands,stage_evidence))]
preformat={x['path']:x for x in before_format};format_changes=[x['path'] for x in pre if preformat.get(x['path'])!=x]
after_artifacts=[raw(p) for p in originals if p.is_file()];need(after_artifacts==before_artifacts,'raw artifacts changed during review')
report={'schema_version':1,'result':'PASS','scope':'Independent read-only software execution/input audit; no Cargo/CLI/QEMU invocation. This is a dirty candidate software gate, not committed-source/hardware/network evidence.','head':start['head'],'run_start_clean':start['clean'],'source_scope':start['scope'],'execution_counts':counts,'total_test_executions':608,'result_rows':39,'failed':0,'ignored':0,'observed_distinct_qualified_test_names':len(qualified),'counting_boundary':'608 executions include repeated default/allfeatures names; distinct qualified names are a textual observation, not independent USB cases or semantic coverage.16 zero-count doctest rows add no test executions.','steps':steps,'packages':package_counts,'input_guards':{'count':len(pre),'before_equals_after':pre==post,'current_matches_all':True,'all_current_crate_rs_toml_root_lock_check_indexed':True,'before_format_to_gate_changes':format_changes,'indexed_input_evidence':current},'feature_provenance':{'default':'Exact stable command source uses workspace defaults, not no-default-features.','all_features':'Separate Clippy/test/release/doc commands explicitly --all-features --locked; actual second binary-hash rows and package counts agree.','vendor':'Separate manifest lib/tokio/locked command and target/nusb-tests binary row.','not_present':['No explicit compiler version/hosttarget command in this run metadata; no independent platform claim.','No cargo -vv per-rustc feature argv dump. Source+successful sequential command invocation is feature linkage, not per-object rustc trace.','No independent Native-only dependency-tree verification in these10 steps.','No per-step releaseELF hash freeze here; last allfeatures build may overwrite default release artifacts. Future runtime guards must bind the actualELF.']},'raw_artifacts_before_after_identical':True,'artifacts':before_artifacts,'scripts_check':raw(ROOT/'scripts/check.sh'),'reviewer_parser':raw(__file__)}
OUTPUT.write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'path':str(OUTPUT),'bytes':OUTPUT.stat().st_size,'sha256':raw(OUTPUT)['sha256'],'counts':counts,'observed_qualified_names':len(qualified),'inputs':len(pre),'format_changes':format_changes}))

#!/usr/bin/env python3
"""Read original completed gate evidence; never import/execute project checkers."""
from pathlib import Path
import argparse, collections, hashlib, json, re, subprocess

def digest(path):
    data=path.read_bytes()
    return {'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',type=Path,default=Path('/tmp/rustusb-logging-software-second-20261024'))
    ap.add_argument('--repo',type=Path,default=Path('/home/ubuntu/github/usbip-client'))
    ap.add_argument('--commit',default=None)
    ap.add_argument('--output',type=Path,default=Path('/tmp/rustusb-logging-software-second-independent-review-corrected-20261024'))
    a=ap.parse_args(); a.output.mkdir(exist_ok=True)
    rawfiles=sorted(p for p in a.input.rglob('*') if p.is_file())
    before_raw={str(p.relative_to(a.input)):digest(p) for p in rawfiles}
    checks=[]
    def check(name,value,detail=None):
        checks.append({'assertion':name,'pass':bool(value),**({'detail':detail} if detail is not None else {})})
    start=json.loads((a.input/'start.json').read_text())
    result=json.loads((a.input/'result.json').read_text())
    log=(a.input/'full-gate.log').read_text(); lines=log.splitlines()
    manifest=json.loads((a.input/'inputs-before.json').read_text())
    after=json.loads((a.input/'inputs-after.json').read_text())
    check('actual wrapper terminal PASS',result['result']=='PASS')
    check('actual initial head is dirty48a4 and never clean-labelled',start['head']=='48a4b013fceb4493cf07c10cac999b8c41f76dae' and bool(start['status'].strip()))
    check('initial invocation was bash-x actual check script',start['command']==['bash','-x','scripts/check.sh'])
    check('before and after manifest exact bytes identical',(a.input/'inputs-before.json').read_bytes()==(a.input/'inputs-after.json').read_bytes())
    check('selected input count exactly177',len(manifest)==len(after)==177 and result['selected_inputs']==177)
    source=[]; manifest_paths=set()
    for record in manifest:
        rel=Path(record['path'])
        check('safe selected source path '+str(rel),not rel.is_absolute() and '..' not in rel.parts)
        manifest_paths.add(str(rel))
        expected={'bytes':record['bytes'],'sha256':record['sha256']}
        original=digest(a.input/'source'/rel); current=digest(a.repo/rel)
        entry={'path':str(rel),'expected':expected,'copy':original,'current':current,'copy_matches':original==expected,'current_matches':current==expected}
        source.append(entry)
        check('original source copy and current exact bytes '+str(rel),entry['copy_matches'] and entry['current_matches'])
    git_mapping=[]
    if a.commit:
        for entry in source:
            data=subprocess.check_output(['git','show',a.commit+':'+entry['path']],cwd=a.repo)
            value={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
            git_mapping.append({'path':entry['path'],'commit':a.commit,'git_blob':value,'matches_run':value==entry['expected']})
            check('later Git blob matches original dirty-run input '+entry['path'],value==entry['expected'])
    actual_copies={str(p.relative_to(a.input/'source')) for p in (a.input/'source').rglob('*') if p.is_file()}
    check('177 original source copies exactly cover manifest',actual_copies==manifest_paths)
    check('source manifest includes logging crate and new runtime logging tests',{'crates/rustusb-logging/src/record.rs','crates/rustusb-logging/src/worker.rs','crates/rustusb-service/src/runtime_logging_tests.rs'}<=manifest_paths)
    check('process logging Python harness is explicitly outside177-input gate','scripts/test-process-logging.py' not in manifest_paths)
    expected_commands=[
      'cargo fmt --all -- --check',
      'cargo clippy --workspace --all-targets --locked -- -D warnings',
      'cargo test --workspace --locked',
      'cargo build --workspace --release --locked',
      'cargo doc --workspace --no-deps --locked',
      'cargo clippy --workspace --all-targets --all-features --locked -- -D warnings',
      'cargo test --workspace --all-features --locked',
      'cargo build --workspace --all-features --release --locked',
      'cargo doc --workspace --all-features --no-deps --locked',
      'cargo test --manifest-path vendor/nusb/Cargo.toml --lib --features tokio --locked --target-dir target/nusb-tests',
    ]
    commands=[{'line':i,'command':l[2:]} for i,l in enumerate(lines,1) if l.startswith('+ cargo ')]
    check('ten actual cargo command trace entries in intended order',[c['command'] for c in commands]==expected_commands)
    check('two doc invocations each have strict rustdoc warnings directly before',all(lines[c['line']-2]=="+ RUSTDOCFLAGS='-D warnings'" for c in commands if c['command'].startswith('cargo doc ')))
    command_sections=[]
    for i,c in enumerate(commands):
        stop=commands[i+1]['line']-1 if i+1<len(commands) else len(lines)
        body=lines[c['line']:stop]
        command_sections.append({**c,'next_command_or_eof_line':stop+1,'finished_lines':[x.strip() for x in body if 'Finished `' in x],'result_rows':sum(x.startswith('test result:') for x in body)})
    for section in command_sections:
        c=section['command']
        if not c.startswith('cargo fmt '):
            check('command has actual completion evidence '+c,bool(section['finished_lines']))
    check('no build/test/doc error markers in successful gate',not re.search(r'(?m)^(?:error(?:\[|:)|test result: FAILED)',log))
    phase=None; package=None; kind=None; current_tests=[]; rows=[]; occurrences=[]
    for i,l in enumerate(lines,1):
        if l.startswith('+ cargo test '):
            phase='vendor' if '--manifest-path vendor/' in l else 'all_features' if '--all-features' in l else 'default'
        if l.startswith('     Running '):
            path=re.search(r'\(([^)]+)\)',l).group(1)
            binary=Path(path).name
            package=re.sub(r'-[0-9a-f]+$','',binary)
            if package=='discovery': package='rustusb_linux::integration_discovery'
            kind='unit_or_integration';current_tests=[]
        elif l.startswith('   Doc-tests '):
            package=l.split()[-1];kind='doctest';current_tests=[]
        m=re.match(r'test (.+) \.\.\. (ok|FAILED|ignored)$',l)
        if m:
            qualified=package+'::'+m.group(1)
            current_tests.append({'name':m.group(1),'qualified':qualified,'result':m.group(2),'line':i})
            occurrences.append({'phase':phase,'package':package,'qualified':qualified,'result':m.group(2),'line':i})
        m=re.match(r'test result: (\S+)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',l)
        if m:
            row={'phase':phase,'package':package,'kind':kind,'line':i,'result':m[1],'passed':int(m[2]),'failed':int(m[3]),'ignored':int(m[4]),'measured':int(m[5]),'filtered':int(m[6]),'printed_qualified_tests':len(current_tests)}
            check('summary matches actual qualified name occurrences line'+str(i),row['passed']==sum(t['result']=='ok' for t in current_tests))
            rows.append(row);current_tests=[]
    sums={phase:sum(r['passed'] for r in rows if r['phase']==phase) for phase in ['default','all_features','vendor']}
    check('actual result rows exactly43',len(rows)==43)
    check('actual successful test executions 308+395+20=723',sums=={'default':308,'all_features':395,'vendor':20} and sum(sums.values())==723)
    check('zero failed ignored measured filtered',all(r['result']=='ok' and not any(r[k] for k in ['failed','ignored','measured','filtered']) for r in rows))
    check('actual printed qualified test occurrences exactly723',len(occurrences)==723 and all(t['result']=='ok' for t in occurrences))
    unique={t['qualified'] for t in occurrences}
    check('qualified union415, repeated feature executions not relabelled distinct',len(unique)==415)
    default={t['qualified'] for t in occurrences if t['phase']=='default'}
    all_features={t['qualified'] for t in occurrences if t['phase']=='all_features'}
    check('default308 functions are subset of allfeatures395',len(default)==308 and len(all_features)==395 and default<=all_features)
    check('18 doctest rows execute zero examples',sum(r['kind']=='doctest' for r in rows)==18 and all(r['passed']==0 for r in rows if r['kind']=='doctest'))
    special={k:[t for t in occurrences if term in t['qualified']] for k,term in [('record_tests','rustusb_logging::record::tests::'),('worker_tests','rustusb_logging::worker::tests::'),('strict_types_tests','rustusb_logging::types::tests::'),('service_logging_tests','runtime::tests::logging::')]}
    check('authored record3 and worker4 each execute default+allfeatures',len(special['record_tests'])==6 and len(special['worker_tests'])==8)
    check('root added two strict types tests execute twice',len(special['strict_types_tests'])==4)
    check('service four real logging tests execute twice',len(special['service_logging_tests'])==8)
    native_tree=(a.input/'native-only-tree.log').read_text()
    excluded=['rustusb-rust-backend','nusb','rusb','libusb1-sys']
    tree_exclusions={name:not re.search(r'(?m)(?:^|[ ├└│─])'+re.escape(name)+r' v[0-9]',native_tree) for name in excluded}
    check('native only tree excludes engine/nusb/rusb/libusb1sys',all(tree_exclusions.values()))
    check('native only check actually completed','Finished `dev` profile' in (a.input/'native-only-check.log').read_text())
    for command in result['commands']:
        check('recorded auxiliary command terminal '+command['log'],command['exit_code']==0)
    actual_artifacts=[]
    for record in result['artifacts']:
        p=Path(record['path']);actual=digest(p)
        entry={'path':str(p),'expected':{'bytes':record['bytes'],'sha256':record['sha256']},'actual':actual,'ELF_magic':p.open('rb').read(4).hex()}
        check('actual current ELF exact '+p.name,entry['expected']==actual and entry['ELF_magic']=='7f454c46')
        actual_artifacts.append(entry)
    check('expected release CLI hash',next(x for x in actual_artifacts if Path(x['path']).name=='rustusb')['actual']['sha256']=='6cc828f3769d1ebd4c2e7b566707b7788f107151bc21155843c315a818b8b474')
    check('expected release helper hash',next(x for x in actual_artifacts if Path(x['path']).name=='rustusb-helper')['actual']['sha256']=='bb0ea543bf6cc1af840c4e793b26906a6f9062b534c00b0c6549dce3fce834a1')
    after_raw={str(p.relative_to(a.input)):digest(p) for p in rawfiles}
    check('all original raw and source artifact bytes unchanged during read',before_raw==after_raw)
    check('original file set unchanged during read',set(before_raw)=={str(p.relative_to(a.input)) for p in a.input.rglob('*') if p.is_file()})
    current_head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=a.repo,text=True).strip()
    summary={'schema_version':1,'result':'PASS' if all(c['pass'] for c in checks) else 'FAIL','source_run':str(a.input),'scope':'independent parsing and byte checks; reviewer authored record/worker, so not an independent review of their entire implementation','actual_run_head':start['head'],'actual_run_dirty_status':start['status'],'current_head_read_only_observed':current_head,'later_exact_git_mapping':{'commit':a.commit,'files':len(git_mapping),'all_match':all(x['matches_run'] for x in git_mapping),'scope':'Content equality only; does not relabel actual dirty48a4 run as clean'},'parser_correction':{'original_script':'/tmp/review-rustusb-logging-software-second-20261024.py','original_report':'/tmp/rustusb-logging-software-second-independent-review-20261024/review.json','original_failure':'Wrong assumed service test module name runtime_logging_tests; actual printed module runtime::tests::logging. Product and raw unchanged.'},'test_counts':{'by_build':sums,'executions':sum(sums.values()),'result_rows':len(rows),'qualified_functions':len(unique),'default_subset_of_allfeatures':default<=all_features,'zero_example_doctest_rows':18},'commands':command_sections,'result_rows':rows,'native_only':{'tree_exclusions':tree_exclusions,'check_command':['cargo','check','-p','rustusb-cli','--no-default-features','--features','secure-transport','--locked'],'tree_command':['cargo','tree','-p','rustusb-cli','--no-default-features','--features','secure-transport','--edges','normal','--locked']},'actual_release_artifacts':actual_artifacts,'tools':{'rustc':(a.input/'rustc.log').read_text(),'cargo':(a.input/'cargo.log').read_text()},'selected_source_count':len(source),'source_equality':all(x['copy_matches'] and x['current_matches'] for x in source),'assertions':checks,'limitations':['Actual gate ran on dirty48a4 candidate; no later commit/clean mapping is inferred.','723 is executions including repeated default/allfeatures tests;415 is qualified function union, neither USB hardware cases nor QEMU evidence.','No added diagnostics-export or hardware acceptance is inferred.','Raw log printed completion and external wrapper terminal exit0 are evidence; this parser does not rerun commands or project verifier.','177 selected inputs include Rust/TOML/lock/check sources, not scripts/test-process-logging.py or a later six-run wrapper; those need separate receipts.','Only recorded release artifacts are independently hashed; no missing per-test child /proc ELF or temporary runtime artifacts are invented.','Earlier developer gate22logger/4service and two style-only Clippy failures retain their original source/result attribution.','New strict types tests belong to this24logger suite, not the earlier22-test run.','Native-only check/tree are read-only auxiliary provenance; release remains actual prior allfeatures release ELF, not a Native-only release.'],'raw_artifact_manifest':'raw-artifacts.json','selected_source_details':'source-checks.json','qualified_tests':'qualified-tests.json','prior_artifact_scope':'No original evidence modified; no build/CLI/QEMU/USB command run.'}
    (a.output/'git-mapping.json').write_text(json.dumps(git_mapping,indent=2)+'\n')
    (a.output/'source-checks.json').write_text(json.dumps(source,indent=2)+'\n')
    (a.output/'qualified-tests.json').write_text(json.dumps(occurrences,indent=2)+'\n')
    (a.output/'raw-artifacts.json').write_text(json.dumps(before_raw,indent=2)+'\n')
    (a.output/'review.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps({'result':summary['result'],'checks':len(checks),'failures':[c for c in checks if not c['pass']],'tests':summary['test_counts'],'report':str(a.output/'review.json'),'report_integrity':digest(a.output/'review.json')},indent=2))

if __name__=='__main__':main()

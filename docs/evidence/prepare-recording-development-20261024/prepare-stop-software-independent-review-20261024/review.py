#!/usr/bin/env python3
"""Read-only standard-library review of four completed development test runs.
No Cargo/Git/QEMU, harness verifier, product import, or source mutation.
"""
from pathlib import Path
import argparse,hashlib,json,re,difflib

def sha(data):return hashlib.sha256(data).hexdigest()
def check(value,message):
    if not value:raise AssertionError(message)
def function(source,name):
    found=re.search(r'(?ms)^(?:async )?fn '+re.escape(name)+r'\b.*?^}',source)
    check(found is not None,'function absent '+name)
    return found.group()
def tokens(source):
    # The three inspected functions use ordinary strings, no raw strings.
    source=re.sub(r'//[^\n]*','',source)
    value=re.findall(r'"(?:[^"\\]|\\.)*"|\w+|[^\s]',source)
    return [x for i,x in enumerate(value) if not(x==',' and i+1<len(value) and value[i+1] in [')','}',']'])]

def main(output):
    output.mkdir(parents=True,exist_ok=True)
    definitions={
      'red':(101,(1,3,0,0,41),165,['cargo','test','-p','rustusb-service','shutdown_','--locked','--','--nocapture']),
      'root-fixed':(0,(4,0,0,0,41),165,['cargo','test','-p','rustusb-service','shutdown_','--locked','--','--nocapture']),
      'native-red':(101,(0,1,0,0,50),165,['cargo','test','-p','rustusb-service','--all-features','--locked','jobs::native::tests::received_export_stop_closes_before_original_resource_ack','--','--exact','--nocapture']),
      'rust-client-first':(0,(28,0,0,0,29),166,['cargo','test','-p','rustusb-service','--all-features','--locked','jobs::','--','--nocapture']),
    }
    allraw=[];reports={};sources={};input_maps={}
    for name,(exitcode,counts,inputcount,command) in definitions.items():
        base=Path('/tmp/rustusb-prepare-stop-'+name+'-20261024')
        for p in sorted(base.rglob('*')):
            if p.is_file():allraw.append({'path':str(p),'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())})
        start=json.loads((base/'start.json').read_bytes());result=json.loads((base/'result.json').read_bytes())
        before=json.loads((base/'inputs-before.json').read_bytes());after=json.loads((base/'inputs-after.json').read_bytes());log=(base/'cargo.log').read_text()
        check(start['command']==command,name+' actual command mismatch')
        check(start['head']=='c2464ed1108fda555acac2512f01a19a5e9bf31c',name+' recorded base HEAD')
        check(bool(start['status'].strip()),name+' must preserve dirty fixture checkout status')
        check(result['exit_code']==exitcode,name+' actual exit')
        check(before==after and len(before)==inputcount,name+' before/after source inventory')
        check(result['inputs_stable'] is True and result['selected_inputs']==len(before),name+' inventory metadata')
        check(len({x['path'] for x in before})==len(before),name+' duplicate input')
        rows=re.findall(r'^test (\S+) \.\.\. (ok|FAILED|ignored)$',log,re.M)
        summary=re.findall(r'^test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out; finished in ([0-9.]+)s$',log,re.M)
        check(len(summary)==1,name+' actual test result summary')
        check(tuple(map(int,summary[0][:5]))==counts,name+' actual test counts')
        check(len(rows)==counts[0]+counts[1]+counts[2],name+' executed named row count')
        check(len({x[0] for x in rows})==len(rows),name+' duplicate executed test')
        check(sum(v=='ok' for _,v in rows)==counts[0] and sum(v=='FAILED' for _,v in rows)==counts[1],name+' named row results')
        check('Finished `test` profile' in log and 'Running unittests src/lib.rs' in log,name+' actual binary execution absent')
        check(not re.search(r'^error\[E\d+\]|could not compile',log,re.M),name+' compilation failed instead of assertions')
        checkout=Path(re.search(r'Compiling rustusb-service v[^ ]+ \((.*)/crates/rustusb-service\)',log).group(1))
        mapped=[];imap={x['path']:x for x in before};input_maps[name]=imap
        for item in before:
            p=checkout/item['path'];data=p.read_bytes()
            check(len(data)==item['bytes'] and sha(data)==item['sha256'],name+' selected checkout bytes '+item['path'])
            mapped.append({'path':item['path'],'sha256':item['sha256'],'bytes':item['bytes']})
        snap=[]
        for p in sorted(base.rglob('*.rs')):
            rel=str(p.relative_to(base));path='crates/rustusb-service/src/'+rel
            check(path in imap,name+' snapshot outside inventory '+path)
            data=p.read_bytes();check(sha(data)==imap[path]['sha256'] and len(data)==imap[path]['bytes'],name+' snapshot mismatch '+path)
            snap.append({'path':path,'snapshot':str(p),'sha256':sha(data)})
        check(all('shutdown_poison_' not in n for n,_ in rows),name+' unexpected poison execution')
        reports[name]={'command':command,'recorded_start_utc':start['utc'],'recorded_base_head':start['head'],'dirty_status':start['status'],'exit_code':exitcode,'named_tests':[{'name':n,'result':v} for n,v in rows],'counts':dict(zip(['passed','failed','ignored','measured','filtered'],counts)),'test_elapsed_seconds':float(summary[0][5]),'inputs':len(before),'actual_checkout':str(checkout),'all_selected_checkout_bytes_match':True,'before_after_equal':True,'snapshots':snap,'compile_success_then_test_binary_ran':True,'raw_log_sha256':sha(log.encode())}
        (output/(name+'-input-map.json')).write_text(json.dumps(mapped,indent=2)+'\n')
        sources[name]=(base,log)
    assertions={
      'runtime::tests::shutdown_late_registration_inherits_the_same_sticky_stop':'late job missed service shutdown',
      'runtime::tests::shutdown_late_session_inherits_its_already_stopped_job':'late session missed authorized job stop',
      'runtime::tests::shutdown_fanout_reaches_owned_stream_before_stalled_store_ack':'actual owned endpoint remained open while journal ACK was stalled',
    }
    for name,message in assertions.items():
        check((name,'FAILED') in [(x['name'],x['result']) for x in reports['red']['named_tests']],name+' RED missing')
        check(message in sources['red'][1],name+' failed assertion text missing')
        check((name,'ok') in [(x['name'],x['result']) for x in reports['root-fixed']['named_tests']],name+' fixed execution missing')
    check('actual received-owner cleanup was blocked by the original Resource ACK' in sources['native-red'][1],'Native actual failure message')
    red=(sources['red'][0]/'runtime_tests.rs').read_text();fixed=(sources['root-fixed'][0]/'runtime_tests.rs').read_text();comparison=[]
    for qualified in assertions:
        name=qualified.split('::')[-1];a=function(red,name);b=function(fixed,name)
        check(tokens(a)==tokens(b),name+' substantive test change between RED and FIX')
        (output/(name+'.diff')).write_text(''.join(difflib.unified_diff(a.splitlines(True),b.splitlines(True),fromfile='RED',tofile='FIX')))
        comparison.append({'name':name,'raw_bytes_identical':a==b,'same_tokens_except_whitespace_and_optional_trailing_commas':True,'red_sha256':sha(a.encode()),'fixed_sha256':sha(b.encode())})
    first=sources['rust-client-first'][0]
    for relative,snapshot in [('jobs/client.rs','client.rs.final'),('jobs/rust_server.rs','rust_server.rs.final'),('jobs/recording.rs','recording.rs.final')]:
        original=Path('/tmp/rustusb-prepare-recording-rust-client-author-20261024')/snapshot
        check((first/relative).read_bytes()==original.read_bytes(),'first subset author frozen snapshot '+relative)
    client=(first/'jobs/client.rs').read_text()
    check('for framed in [false, true]' in client and 'sender_disappears' not in client,'first subset is pre-C1 only')
    check('shutdown_poison_still_fans_out' not in (sources['root-fixed'][0]/'runtime_tests.rs').read_text(),'poison must be absent from first fixed source')
    changes={}
    for pair in [('red','root-fixed'),('root-fixed','rust-client-first')]:
        a,b=map(input_maps.get,pair);changes[' -> '.join(pair)]=[p for p in sorted(set(a)|set(b)) if a.get(p)!=b.get(p)]
    final={'status':'PASS: evidence classification and source consistency only; original RED results remain FAIL','runs':reports,'red_to_fixed_assertion_comparison':comparison,'input_deltas':changes,'boundaries':['All four are development dirty checkouts based on recorded c2464ed, not clean revision acceptance. Selected byte maps do not imply a new immutable commit.','RED has three actually executed failing assertions and one prior passing test; root-fixed executes the same three predicates plus that prior test. Only formatting and optional trailing commas differ in those function bodies.','Native RED compiled and executed its actual received-owner phase plus real Resource oneshot/ordinary Unix-stream close receipt; no fake root handle or kernel claim.','Root store test holds an actual Store mutex while production Runtime::persist waits; it is not a directly instrumented fsync stall.','Rust/client subset executes real TCP/mTLS v1/v2 and real virtual listener/IMPORT lease plus real JobMessage ACK gates. It is not hardware, helper-root, USBFS kernel initialization, or a full software gate.','C1 sender-loss variant and new poison fixture are not covered by these four runs; no PASS reconstructed for them.','Shared target_dir test binaries can be overwritten by later Cargo invocations. Logs identify executed binary paths, but no contemporaneous binary SHA artifact exists in these four directories; this review does not hash current target bytes and retrospectively attribute them.','Reviewer authored Rust/client/recording changes and some older related service code. This is an independent parser/method cross-review, not independent implementation authorship.'],'execution':{'cargo':False,'qemu':False,'git':False,'repo_edits':False,'harness_verifier_called':False},'raw_originals':len(allraw),'selected_source_comparisons':sum(x['inputs'] for x in reports.values()),'source_snapshot_comparisons':sum(len(x['snapshots']) for x in reports.values()),'counts_are':'record/test execution observations, not USB compatibility cases'}
    for x in allraw:
        data=Path(x['path']).read_bytes();check(sha(data)==x['sha256'] and len(data)==x['bytes'],'raw changed during review '+x['path'])
    (output/'raw-originals-manifest.json').write_text(json.dumps({'files':allraw,'unchanged_after_review':True},indent=2)+'\n')
    (output/'report.json').write_text(json.dumps(final,indent=2)+'\n')
    print(json.dumps({'status':final['status'],'runs':4,'raw_originals':len(allraw),'selected_source_comparisons':final['selected_source_comparisons'],'source_snapshot_comparisons':final['source_snapshot_comparisons'],'report_sha256':sha((output/'report.json').read_bytes())}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('/tmp/rustusb-prepare-stop-software-independent-review-20261024'));main(parser.parse_args().output)

#!/usr/bin/env python3
"""Independent completed-CI log/API reader; invokes only local read-only Git."""
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT = Path('/home/ubuntu/github/usbip-client')
BASE = Path('/tmp/rustusb-secure-ci-39950528568-20261023')
HEAD = '41e2c67a71a0257cd1e0aec109477b93869e9cc0'
RUN = 39950528568
EXPECTED = {120403594444:('22.04','x86_64'),120403594599:('24.04','x86_64'),120403594630:('24.04','aarch64'),120403594639:('22.04','aarch64')}
COUNTS = [1,4,33,0,0,8,32,11,35,27,0,0,0,0,0,0,0,9,4,78,0,0,8,32,11,56,27,0,0,0,0,0,0,0,20]
checks = []
observed = {}


def blob(path):
    path = Path(path)
    value = path.read_bytes()
    observed[str(path)] = {'path':str(path),'length':len(value),'sha256':hashlib.sha256(value).hexdigest()}
    return value


def load(path):
    return json.loads(blob(path))


def check(name, passed, detail=None):
    entry = {'name':name,'passed':bool(passed)}
    if detail is not None:
        entry['detail'] = detail
    checks.append(entry)


run = load(BASE / 'run.json')
jobs = load(BASE / 'jobs.json')
artifacts = load(BASE / 'artifacts.json')
pages = load(BASE / 'pages-run.json')
connector_jobs = load(BASE / 'connector-jobs.json')
connector_artifacts = load(BASE / 'connector-artifacts.json')
check('actual push run exact head/attempt1 completed success',
      run['id'] == RUN and run['head_sha'] == HEAD and run['run_attempt'] == 1
      and run['event'] == 'push' and run['status'] == 'completed' and run['conclusion'] == 'success')
check('four actual job IDs and connector agree',
      jobs['total_count'] == 4 and {j['id'] for j in jobs['jobs']} == set(EXPECTED)
      and {j['id'] for j in connector_jobs['value']['structuredContent']['jobs']} == set(EXPECTED))
check('no workflow artifact upload exists; no absent artifact is fabricated',
      artifacts['total_count'] == 0 and artifacts['artifacts'] == []
      and connector_artifacts['value']['structuredContent']['artifacts'] == [])
check('Pages exact same commit completed success (metadata only)',
      pages['id'] == 39950528577 and pages['head_sha'] == HEAD and pages['status'] == 'completed' and pages['conclusion'] == 'success')
source_api = {}
for name, destination in [('workflow','workflow.yml'),('check-script','check-script.source'),('signals-script','signals-script.source')]:
    doc = load(BASE / (name + '-api.json'))
    contents = base64.b64decode(doc['content'])
    gitsha = hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest()
    local = subprocess.check_output(['git','show',HEAD + ':' + doc['path']],cwd=ROOT)
    check('fixed official GitHub content/Gitblob/local commit: ' + doc['path'],
          doc['sha'] == gitsha and contents == local)
    (BASE / destination).write_bytes(contents)
    blob(BASE / destination)
    source_api[name] = contents.decode()
workflow = source_api['workflow']
check('workflow preserves all-feature software build then old backpressure/new four-signal skip-build step',
      'run: scripts/check.sh' in workflow and 'python3 scripts/test-virtual-backpressure.py' in workflow
      and 'python3 scripts/test-server-signals.py --skip-build --evidence "$RUNNER_TEMP/server-signals" || status=$?' in workflow
      and 'cat "$RUNNER_TEMP/server-signals/summary.json" || true' in workflow and 'exit "$status"' in workflow
      and 'cargo build --workspace --all-features --release --locked' in source_api['check-script'])

# Reconstruct exactly the signal harness's tracked source selection at CI HEAD.
names = subprocess.check_output(['git','ls-tree','-r','--name-only',HEAD],cwd=ROOT).decode().splitlines()
names = sorted(n for n in names if n == 'Cargo.lock' or Path(n).suffix in {'.rs','.toml'} or n.startswith('vendor/') or n == 'scripts/test-server-signals.py')
raw = subprocess.check_output(['git','cat-file','--batch'],cwd=ROOT,input=''.join(HEAD + ':' + name + '\n' for name in names).encode())
cursor = 0
expected_sources = []
for name in names:
    end = raw.index(b'\n',cursor)
    header = raw[cursor:end].split()
    size = int(header[2])
    content = raw[end+1:end+1+size]
    cursor = end+1+size+1
    expected_sources.append({'path':name,'length':size,'sha256':hashlib.sha256(content).hexdigest()})
check('reconstructed 313 fixed-commit sources from local immutable Git blobs', len(expected_sources) == 313 and cursor == len(raw))
expected_fingerprint = hashlib.sha256()
for item in expected_sources:
    expected_fingerprint.update(item['path'].encode()+b'\0'+str(item['length']).encode()+b'\0'+item['sha256'].encode()+b'\n')
fingerprint = expected_fingerprint.hexdigest()
(BASE / 'expected-source-hashes.json').write_text(json.dumps({'head':HEAD,'source_sha256':fingerprint,'files':expected_sources},indent=2)+'\n')
blob(BASE / 'expected-source-hashes.json')


def parse_objects(clean):
    found = []
    decoder = json.JSONDecoder()
    for match in re.finditer(r'^\{$',clean,re.M):
        try:
            value, end = decoder.raw_decode(clean[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value,dict) and 'scope' in value:
            found.append((value,clean[match.start():match.start()+end]))
    return found


records = []
for job in jobs['jobs']:
    jid = job['id']
    ubuntu, arch = EXPECTED[jid]
    rawlog = blob(BASE / f'raw-{jid}.log')
    clean = '\n'.join(re.sub(r'^\d{4}-\d\d-\d\dT[^ ]+Z ','',line) for line in rawlog.decode('utf-8-sig').splitlines())
    check(f'{jid} actual job and every actual step succeeded on exact head',
          job['run_id'] == RUN and job['head_sha'] == HEAD and job['status'] == 'completed' and job['conclusion'] == 'success'
          and all(s['status'] == 'completed' and s['conclusion'] == 'success' for s in job['steps']))
    check(f'{jid} raw checkout actual git log exact commit',
          re.search(r'git log -1 --format=%H\n'+HEAD+r'\n',clean) is not None)
    rows = [list(map(int,r)) for r in re.findall(r'test result: ok\. (\d+) passed; (\d+) failed; (\d+) ignored; (\d+) measured; (\d+) filtered out;',clean)]
    named = re.findall(r'^test .+ \.\.\. ok$',clean,re.M)
    check(f'{jid} 35 rows and exact 151default225allfeatures20vendor=396 executions',
          [r[0] for r in rows] == COUNTS and len(named) == 396)
    check(f'{jid} all failures/ignored/measured/filtered fields zero', all(all(x == 0 for x in r[1:]) for r in rows))
    check(f'{jid} actual native Ubuntu release/Rust host/toolchain',
          re.findall(r'^VERSION_ID="([^"]+)"$',clean,re.M) == [ubuntu]
          and re.findall(r'^host: (.+)$',clean,re.M) == [arch+'-unknown-linux-gnu']
          and re.findall(r'^rustc (.+)$',clean,re.M)[0].startswith('1.97.1 '))
    uname = next(line for line in clean.splitlines() if line.startswith('Linux ') and line.endswith('GNU/Linux'))
    check(f'{jid} actual uname native architecture matches matrix',uname.endswith(f'{arch} {arch} {arch} GNU/Linux'))
    check(f'{jid} actual two release and two rustdoc completions', clean.count('Finished `release` profile') == 2 and len(re.findall(r'^\s+Generated .+/target/doc/',clean,re.M)) == 2)
    objects = parse_objects(clean)
    legacy = [(d,t) for d,t in objects if d.get('scope') == 'local CLI output backpressure; no physical USB or kernel changes']
    modern = [(d,t) for d,t in objects if d.get('cases')]
    check(f'{jid} one actual legacy two-case and one full four-case summary',len(legacy) == len(modern) == 1)
    old = legacy[0][0]
    summary, original_text = modern[0]
    restored = (original_text+'\n').encode()
    printed_sha = re.findall(r'"summary_sha256": "([0-9a-f]{64})"',clean)
    check(f'{jid} full printed JSON reconstructed original summary file SHA exactly', printed_sha == [hashlib.sha256(restored).hexdigest()])
    (BASE/f'derived-signals-summary-{jid}.json').write_bytes(restored)
    blob(BASE/f'derived-signals-summary-{jid}.json')
    check(f'{jid} old backpressure actual INT0/deadline1/4096/nodrain/socketEOF bounded',
          old['result'] == 'PASS' and old['host_uid'] != 0 and len(old['checks']) == 2
          and old['checks'][0]['mode'] == 'sigint' and old['checks'][0]['exit'] == 0 and old['checks'][0]['elapsed_seconds'] < 2
          and old['checks'][1]['mode'] == 'deadline' and old['checks'][1]['exit'] == 1 and 4 <= old['checks'][1]['elapsed_seconds'] <= 7
          and all(c['stdout_capacity'] == 4096 and c['stdout_drained_after_fill'] is False and c['lease_socket_closed'] is True for c in old['checks']))
    check(f'{jid} real skip-build features retain explicit external/not inferred provenance',
          summary['build'] == {'skipped':True,'command':None,'features':'external prebuilt; not inferred'}
          and '--skip-build' in summary['command'])
    for phase in ['before','after']:
        state = summary['source_'+phase]
        check(f'{jid} {phase} 313 clean sources equal immutable checkout Git blobs',
              state['head'] == HEAD and state['dirty'] is False and state['git_status'] == ''
              and state['source_sha256'] == fingerprint and state['files'] == expected_sources)
    check(f'{jid} actual source/binary unchanged and runtime host UID/arch agree',
          summary['source_unchanged'] is True and summary['binary_unchanged'] is True and summary['host_uid'] != 0
          and summary['host'][-1] == arch and summary['result'] == 'PASS'
          and summary['binary_before']['sha256'] == summary['binary_after_sha256'])
    check(f'{jid} four actual mode/signal combinations',
          len(summary['cases']) == 4 and {(c['mode'],c['signal']) for c in summary['cases']} == {(m,s) for m in ['ready','blocked'] for s in ['sigint','sigterm']})
    cases = []
    for case in summary['cases']:
        key = f"{jid} {case['mode']}/{case['signal']}"
        pre = case['before']['snapshot']['devices']
        post = case['after']
        check(key+' actual two configured leased pending profiles before signal',
              {(d['bus_id'],d['profile']) for d in pre} == {('1-1','hid_keyboard'),('1-2','cdc_acm')}
              and all(d['leased'] is True and d['configuration'] == 1 and d['pending_urbs'] == 1 for d in pre))
        check(key+' signal identity same ordinary child and actual INT/TERM number',
              case['sent']['identity'] == case['child'] and case['sent']['pid'] == case['child']['pid']
              and all(u == summary['host_uid'] for u in case['child']['uids'])
              and case['sent']['signal'] == (2 if case['signal'] == 'sigint' else 15))
        check(key+' captured actual child /proc ELF SHA and inode match selected before/after executable',
              case['executed_binary']['sha256'] == summary['binary_before']['sha256']
              and case['executed_binary']['device'] == summary['binary_before']['device']
              and case['executed_binary']['inode'] == summary['binary_before']['inode'])
        check(key+' actual exit0 within8s/no forced cleanup/waiterror, two exact socket EOFs',
              case['result'] == 'PASS' and case['forced_cleanup'] is False and case['final_returncode'] == post['returncode'] == 0
              and post['signal_exit_seconds'] < 8 and post['wait_error'] is None
              and set(post['data_sockets']) == {'1-1','1-2'}
              and all(v['eof'] is True and v['unexpected_hex'] == '' for v in post['data_sockets'].values()))
        if case['mode'] == 'ready':
            stopped = post['stopped_events']
            check(key+' real stopped snapshot resource cleanup beyond socket EOF',
                  post['backend_cleanup_observed'] is True and len(stopped) == 1
                  and all(d['leased'] is False and d['pending_urbs'] == d['configuration'] == d['hid_queued_reports'] == d['cdc_queued_bytes'] == 0 for d in stopped[0]['snapshot']['devices']))
        else:
            pipe = case['pipe']
            check(key+' actual full4096 independentOFD EAGAIN, child staysblocking and writerpipe_write before signal',
                  pipe['capacity'] == pipe['bytes_after_fill'] == pipe['synthetic_padding_bytes'] == 4096
                  and pipe['independent_writer_eagain'] is True and pipe['blocked_writer']['wchan'] == 'pipe_write'
                  and pipe['blocked_writer']['monotonic_ns'] < case['sent']['monotonic_ns']
                  and all(pipe[k] & 2048 == 0 for k in ['original_writer_flags_before','original_writer_flags_after','child_stdout_flags_before','child_stdout_flags_after'])
                  and pipe['independent_writer_flags'] & 2048 != 0)
            check(key+' no stdout drain or invented backend stopped receipt',
                  case['stdout_drained_after_fill'] is False and post['stdout_read_after_fill'] is False
                  and post['stdout_unread_bytes'] == 4096 and post['stopped_events'] == [] and post['backend_cleanup_observed'] is False)
        cases.append({'mode':case['mode'],'signal':case['signal'],'actual_exit':post['returncode'],'signal_exit_seconds':post['signal_exit_seconds'],'backend_cleanup_snapshot_observed':post['backend_cleanup_observed']})
    records.append({'id':jid,'name':job['name'],'url':job['html_url'],'actual_head':job['head_sha'],'runner_name':job['runner_name'],
                    'ubuntu':ubuntu,'architecture':arch,'uname':uname,'rustc':re.findall(r'^rustc (.+)$',clean,re.M)[0],
                    'test_executions':396,'test_result_rows':35,'groups':{'default':151,'all_features':225,'vendor_pure':20},
                    'source_count':313,'source_sha256':fingerprint,'reported_selected_ELF':summary['binary_before'],
                    'summary_sha256':hashlib.sha256(restored).hexdigest(),'local_signal_cases':cases,'legacy_backpressure':old,
                    'artifact_scope':'Complete summary recovered from timestamped raw job log with original printed summary-file SHA match; no uploaded raw local artifacts or ELF available.'})

for path in sorted(BASE.glob('*.stderr.txt')):
    check('read-only API stderr empty: '+path.name,len(blob(path)) == 0)
check('all inspected fetched original/derived bytes unchanged during independent review',
      all(len(Path(path).read_bytes()) == item['length'] and hashlib.sha256(Path(path).read_bytes()).hexdigest() == item['sha256'] for path,item in observed.items()))
report = {'schema_version':1,'result':'PASS' if all(c['passed'] for c in checks) else 'FAIL','head':HEAD,
          'run':{k:run[k] for k in ['id','html_url','event','head_sha','run_attempt','status','conclusion','created_at','updated_at']},
          'collection':'GitHub connector jobs/logs/artifacts first; official gh GET API captures full raw redirected job bytes, run/jobs/contents/artifact/Pages responses. No trigger/rerun/build/device operation.',
          'jobs':records,'total_test_executions_across_four_platforms':1584,'test_result_rows_across_four_platforms':140,
          'uploaded_workflow_artifacts':0,'check_count':len(checks),'checks':checks,
          'limits':['This exact software CI commit does not cover or reverse the first secure QEMU FAIL; later product fixes require their own validation/CI.',
                    'These are native-architecture software checks and ordinary-user loopback virtual lifecycle tests; no QEMU/kernel USB interoperability, physical hardware, TLS fullUSB acceptance or entire Phase3 conclusion.',
                    'Signal harness --skip-build remains external/not inferred; all-features association comes from fixed workflow/check script ordering and successful actual build steps, not from the skip flag.',
                    'Per-child ELF device/inode/SHA values are actual observations printed by the harness and internally cross-checked. CI uploaded no ELF, so this reviewer cannot independently rehash the remote executable bytes.',
                    'No local protocol.bin/stdout.bin/after.json artifact was uploaded; hashes indexed in printed summary are retained observations, not independently downloaded raw files. Complete printed summary is exactly reconstructed and matches its original recorded SHA.',
                    'Legacy two-backpressure runner contains no per-child source/ELF proof; no such guard is invented from the modern four-case runner.',
                    'Pages is checked only by completed/success API metadata at this commit; its output site and deployment content are outside this review.'],
          'artifacts':[observed[p] for p in sorted(observed)]}
out = Path('/tmp/rustusb-secure-ci-independent-review-20261023.json')
out.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'result':report['result'],'checks':len(checks),'failed':[c['name'] for c in checks if not c['passed']],'path':str(out),'length':out.stat().st_size,'sha256':hashlib.sha256(out.read_bytes()).hexdigest()}))

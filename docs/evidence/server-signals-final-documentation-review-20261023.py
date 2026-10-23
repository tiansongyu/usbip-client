"""Read-only documentation/evidence consistency check; report only in /tmp."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote

repo = Path('/home/ubuntu/github/usbip-client')
names = ['README.md', 'AGENTS.md', 'docs/architecture.md', 'docs/testing.md',
         'docs/compatibility.md', 'docs/acceptance.md', 'docs/roadmap.md',
         'docs/rust-virtual-server.md', 'docs/rust-physical-server.md',
         'docs/research/server-signal-shutdown.md', 'docs/research/server-signal-shutdown-design.md']
docs = {name: (repo/name).read_bytes() for name in names}
checks = []
evidence_inputs = {}

def check(name, success, observed=None):
    item = {'name': name, 'result': 'PASS' if success else 'FAIL'}
    if observed is not None:
        item['observed'] = observed
    checks.append(item)

def load(name):
    path = repo/name
    data = path.read_bytes()
    evidence_inputs[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    return json.loads(data)

link_count = 0
for name, data in docs.items():
    text = data.decode('utf-8')
    for match in re.finditer(r'(?<!!)\[[^\]]*\]\(([^)]+)\)', text):
        target = match.group(1).strip().strip('<>')
        if target.startswith(('https://','http://','mailto:','#')):
            continue
        relative = unquote(target.split('#',1)[0])
        path = (repo/name).parent/relative
        link_count += 1
        check(f'local_link:{name}:{text[:match.start()].count(chr(10))+1}:{relative}', path.exists())

software = load('docs/evidence/server-signals-software-934358f-20261023/software.json')
check('software_exact_freeze222_and138', software['git_commit']=='934358fa457b71c5e3436cc544ca4031b656f147' and software['working_tree_clean'] is True and software['exit_code']==0 and software['test_executions']==222 and software['result_rows']==31 and software['source_files_verified']==138 and software['source_unchanged'] is True)
local = load('docs/evidence/local-server-signals-fixed-934358f-20261023/summary.json')
check('local_four_actual_pass_default_allfeatures_build', local['result']=='PASS' and len(local['cases'])==4 and local['build']['skipped'] is False and local['build']['returncode']==0 and local['build']['command']==['cargo','build','--workspace','--release','--locked','--all-features'])
check('local_source_binary_guards', local['source_unchanged'] is True and local['binary_unchanged'] is True and local['binary_before']['sha256']==local['binary_after_sha256']==software['binary_sha256']['rustusb'])
sequence = load('docs/evidence/qemu-server-signals-fixed-934358f-20261023/sequence.json')
check('four_qemu_sequence_same_clean934', sequence['result']=='PASS' and sequence['git_commit']==software['git_commit'] and sequence['working_tree_clean'] is True and sequence['source_after_guard_passed'] is True and sequence['remaining']==[] and [run['name'] for run in sequence['runs']]==['sigterm','virtual','physical-normal','physical-hot'] and all(run['exit_code']==0 and run['result']=='PASS' for run in sequence['runs']))
qemu = {}
for run in sequence['runs']:
    name = run['name']
    relative = 'docs/evidence/qemu-server-signals-fixed-934358f-20261023/'+name+'/summary.json'
    summary = qemu[name] = load(relative)
    check(f'{name}:original_summary_matches_sequence_hash', evidence_inputs[relative]['sha256']==run['summary_sha256'])
    check(f'{name}:actual_pass_clean_freeze_two_guest0', summary['result']=='PASS' and summary['git_commit']==software['git_commit'] and summary['working_tree_dirty'] is False and summary['guest_exit_codes']=={'server':0,'client':0} and summary['build_performed'] is True)
    expected_command = ['cargo','build','--workspace','--release','--locked'] + (['--features','rustusb-cli/rust-backend'] if name=='virtual' else ['--all-features'])
    check(f'{name}:exact_feature_build_attribution', summary['build_command']==expected_command)
    if name!='virtual':
        check(f'{name}:allfeatures_elf_actual', summary['binary_sha256']==software['binary_sha256']==summary['guest_binary_sha256'])
check('virtual_elf_distinct_from_allfeatures', qemu['virtual']['binary_sha256'] != software['binary_sha256'])
report_text = docs['docs/research/server-signal-shutdown.md'].decode()
for name, digest in qemu['virtual']['binary_sha256'].items():
    check(f'doc_virtual_{name}_full_elf_digest', digest in report_text)
for name, digest in software['binary_sha256'].items():
    check(f'doc_allfeatures_{name}_full_elf_digest', digest in report_text)
check('term_actual52checks_pass', len(qemu['sigterm']['verification']['checks'])==52 and all(item['result']=='PASS' for item in qemu['sigterm']['verification']['checks']))
for name, wanted in [('virtual',19),('physical-normal',15),('physical-hot',15)]:
    cases = qemu[name].get('protocol_probe_cases',qemu[name].get('probe_cases',[]))
    check(f'{name}:documented_probe_execution_count', len(cases)==wanted, len(cases))
index = load('docs/evidence/server-signals-fixed-validation-934358f-20261023.json')
publication = load('docs/evidence/publication-server-signals-934358f-20261023.json')
for label, data in [('fixed_validation_index',index),('publication_index',publication)]:
    check(label+':declared_count', data['indexed_artifact_count']==len(data['artifacts']))
    for artifact in data['artifacts']:
        path = repo/artifact['path']
        actual = path.read_bytes()
        check(label+':artifact:'+artifact['path'], len(actual)==artifact['bytes'] and hashlib.sha256(actual).hexdigest()==artifact['sha256'])
check('legacy_guard_serialization_limit_explicit', any('three legacy' in text and 'no serialized after-guard' in text for text in index['limitations']))
check('publication_actual_four_completed_success222_2_4', publication['git_commit']==software['git_commit'] and publication['run_id']==39899971271 and publication['run_status']=='completed' and publication['run_conclusion']=='success' and len(publication['jobs'])==4 and all(job['status']=='completed' and job['conclusion']=='success' and job['test_executions']==222 and job['legacy_backpressure_cases']==2 and job['new_signal_cases']==4 for job in publication['jobs']))
ci_review = load('docs/evidence/server-signals-ci-independent-review-20261023.json')
check('CI190_actual_raw_review', ci_review['result']=='PASS' and ci_review['check_count']==190 and ci_review['head_sha']==software['git_commit'] and ci_review['failures']==[])
regression_review = load('docs/evidence/server-signals-fixed-regressions-independent-review-20261023.json')
check('legacy227_review_complete_and_linked', regression_review['result']=='PASS_REVIEW' and regression_review['check_count']==227 and regression_review['failures']==[] and regression_review['frozen_commit']==software['git_commit'] and 'server-signals-fixed-regressions-independent-review-20261023.json' in report_text and '独立raw复核待完成' not in report_text)
check('CI_unavailable_raw_not_reconstructed_claim', 'CI未上传各case独立stdout/protocol原文件' in report_text and '不据此重建缺失原件' in report_text)
check('CI_reviewer_authorship_disclosed_in_evidence', 'local harness authored by reviewer' in ci_review['scope'] and 'not independent harness implementation validation' in ci_review['scope'])
for filename, count in [('server-sigterm-fixed-local-independent-review-20261023.json',138),('server-sigterm-old-local-independent-review-20261023.json',125),('server-sigterm-old-qemu-independent-review-20261023.json',177),('server-sigterm-fixed-qemu-author-raw-review-20261023.json',181)]:
    data = load('docs/evidence/'+filename)
    check(filename+':actual_count', data['check_count']==count)
check('local_author_review_disclosed', '本地离线138项复核' in report_text and 'harness作者' in report_text and '不称独立harness实现审查' in report_text)
check('term_author_review_disclosed', 'TERM作者raw复核181项' in report_text and '明确披露harness作者身份' in report_text and '未调用runner.verify' in report_text)
check('host_sink_not_fabricated_raw_capture', '未单独保存host chardev原始payload' in report_text and '不能把离线复核写成重放原始TX字节' in report_text)
old = load('docs/evidence/qemu-server-sigterm-old-dev01-20261023/summary.json')
check('old_negative_stays143_no_restoration_no_stopped', old['result']=='FAIL' and old['working_tree_dirty'] is True and old['verification']['startup_outcome']['wait_code']==143 and old['verification']['outcomes']['server']['wait_code']==143 and old['verification']['outcomes']['server']['restored_sysfs'] is False and not any(event.get('event')=='stopped' for event in old['verification']['server_events']))
check('old_negative_limits_documented', all(text in report_text for text in ['8FAIL','20.04秒','该有界观察不证明永久泄漏','没有stopped就没有末reap/预算/请求数证明']))
patch_paths = subprocess.check_output(['git','show','--format=','--name-only','d39517b'],cwd=repo,text=True).splitlines()
check('product_patch_only_four_CLI_files', set(patch_paths)=={'crates/rustusb-cli/src/main.rs','crates/rustusb-cli/src/physical_server.rs','crates/rustusb-cli/src/shutdown_signals.rs','crates/rustusb-cli/src/virtual_server.rs'})
check('unfinished_scope_explicit', all(text in report_text for text in ['SIGKILL','abort/panic','helper丢失','持久重启','重复信号运行矩阵','kernel内部硬阻塞','Windows','真实G1/G2','ISO','无原驱动非零alternate','远端认证/TLS','完整Phase2/3']))
check('startup_backlog_not_acceptance_claim', 'helper接受/授权未被观察' in report_text)
check('blocking_prepare_cancel_limit', '取消prepare不终止已运行的spawn_blocking/nusb初始化系统调用' in report_text)
check('blocked_resources_not_invented', '未观察的backend末资源不补为0' in report_text)
check('README_exact_934_and_CI', all(text in docs['README.md'].decode() for text in ['934358f','8个guest','CI39899971271','各构建feature和ELF来源分别记录','SIGKILL']))
check('acceptance_E23_exact_934_and_CI', all(text in docs['docs/acceptance.md'].decode() for text in ['| E23 |','clean934358f','QEMU4PASS/8guest0','CI39899971271','不证明重复信号']))
check('testing_two_entry_default_and_feature_boundary', all(text in docs['docs/testing.md'].decode() for text in ['scripts/test-server-signals.py --evidence','scripts/qemu/run-sigterm-qemu.py --evidence','两入口默认执行locked all-features','原虚拟回归使用`--features rustusb-cli/rust-backend`']))
result = {'schema_version':1,'decision':'PASS_NO_MUST_FIX' if all(item['result']=='PASS' for item in checks) else 'MUST_FIX',
          'scope':'Read-only consistency review of the 11 requested pending documents against exact archived934358f software/local/QEMU/CI evidence; no runtime tests/build/QEMU or repository writes.',
          'reviewer_role':'QEMU SIGTERM harness author, documentation consistency review only; not independent product or legacy-regression raw implementation validation.',
          'document_inputs':{name:{'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()} for name,data in docs.items()},
          'evidence_inputs':evidence_inputs,'local_links_checked':link_count,'check_count':len(checks),'checks':checks,
          'must_fix':[item['name'] for item in checks if item['result']!='PASS'],
          'manual_source_and_scope_review':['Both Rust service entries synchronously register ShutdownSignals before first await/helper I/O/listener. EventOutput precedes bind; prepare cancellation and blocking initialization limits match source.', 'After acquiring ServerHandle, cleanup is awaited before operation errors; final output is separately interruptible. Repeated-signal cleanup is only a source-ownership claim, not a runtime claim.', 'Same934358f source does not imply one ELF: virtual rust-backend-only and three all-features runs are attributed separately.', 'The222 unit executions, four local signal cases and two older backpressure cases remain separate categories;52 TERM host checks are not described as52 wire cases.', 'Legacy QEMU summaries do not serialize post-guard fields. Sequence source138 recheck and runner PASS guard ordering are not rewritten into missing original fields.', 'Historical derived indexes/reviews with CI-pending text retain their capture-time status; final publication and current document CI claims point to actual four-success39899971271.', 'Existing virtual-service introduction is scoped to that fixed-profile service, not a claim that the separate physical-nusb adapter is absent.'],
          'explicit_pending_exceptions':[],
          'limits':['Local link target existence checked; external URLs and Markdown anchors were not network-revalidated.', 'No original missing CI per-case captures or FTDI sink payload files were reconstructed.', 'No worktree-clean requirement imposed after evidence/document archiving; clean checks use original runtime and sequence records.']}
output = Path('/tmp/rustusb-sigterm-final-documentation-review-20261023.json')
output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'decision':result['decision'],'checks':len(checks),'must_fix':result['must_fix'],'bytes':output.stat().st_size,'sha256':hashlib.sha256(output.read_bytes()).hexdigest()},indent=2))

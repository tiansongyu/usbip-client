from pathlib import Path
import hashlib,json,shutil,subprocess
out=Path('/home/ubuntu/github/usbip-client/docs/evidence/data-link-software-20261024');out.mkdir(exist_ok=False)
records=[]
def copy(source,dest):
 source=Path(source);assert source.is_file() and not source.is_symlink(),source
 raw=source.read_bytes();target=out/dest;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
 assert target.read_bytes()==raw
 records.append(dict(path=dest,original=str(source),bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
for label,directory in [('development','/tmp/rustusb-data-link-dev-gate-20261024'),('gate','/tmp/rustusb-data-link-software-first-20261024'),('local-runtime','/tmp/rustusb-data-link-local-runtime-20261024'),('flush-author','/tmp/rustusb-flush-fence-tests-20261024')]:
 root=Path(directory)
 for file in sorted(root.rglob('*')):
  if file.is_file():copy(file,label+'/'+str(file.relative_to(root)))
files=['rustusb-phase3-data-link-design-20261024.json','rustusb-phase3-data-link-design-20261024.md','rustusb-phase3-link-api-skeleton-20261024.rs','rustusb-phase3-link-health-service-design-20261024.md','rustusb-phase3-link-health-service-design-sources-20261024.json','rustusb-phase3-link-health-owner-wakeup-addon-20261024.md','rustusb-phase3-link-health-owner-wakeup-sources-20261024.json','rustusb-heartbeat-rtt-qemu-design-20261024.md','rustusb-heartbeat-rtt-qemu-sources-20261024.json','rustusb-link-author-handoff-20261024.json','rustusb-link-service-author-handoff-20261024.json','rustusb-data-link-root-source-review-20261024.json','rustusb-link-adversarial-candidate-20261024.patch','rustusb-link-adversarial-candidate-20261024.json','rustusb-data-link-health-guide-source-review-20261024.json','rustusb-data-link-software-first-independent-review-20261024.json','review-rustusb-data-link-software-first-20261024.py']
for name in files:copy(Path('/tmp')/name,'research/'+name)
copy('/tmp/rustusb-heartbeat-research-20261024/source-manifest.json','research/upstream-source-manifest.json')
copy(__file__,'archive.py')
scope=dict(scope='Research, first compile/Clippy failures, subsequent development tests, full software gate and ordinary-UID local signal/backpressure runtime; no QEMU acceptance in this archive.',software_executions=608,qualified_test_names=357,result_rows=39,failed=0,ignored=0,actual_gate_head='031f63d9b27fa8712a13089f95aa61c1c2c223aa',actual_gate_clean=False,git_blob_mapping_commit='e29cc1163ff9ce3cd73833c15eb24611941bac34',mapped_inputs=164,notes=['Individual input mapping does not relabel the original dirty gate as a clean run.','Initial handoffs and design sheets preserve their captured source versions; final implementation is in committed source.','QEMU harness and blackhole acceptance remain separate work.','Final toolchain/ELF and Native-only check/tree were collected separately after the full gate, not as per-step compiler traces.'])
(out/'scope.json').write_text(json.dumps(scope,indent=2)+'\n');copy_scope=(out/'scope.json').read_bytes();records.append(dict(path='scope.json',original='generated archive scope',bytes=len(copy_scope),sha256=hashlib.sha256(copy_scope).hexdigest()))
(out/'manifest.json').write_text(json.dumps(dict(schema_version=1,files=records),indent=2)+'\n')
print('files',len(records)+1,'bytes',sum(x['bytes'] for x in records),'archive',out)

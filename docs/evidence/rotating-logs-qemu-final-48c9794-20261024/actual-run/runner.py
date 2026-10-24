from pathlib import Path
import subprocess,json,hashlib,datetime,sys
root=Path('/tmp/rustusb-logging-qemu-final-checkout-20261024')
out=Path('/tmp/rustusb-process-logging-qemu-final-coordinator-20261024');out.mkdir(exist_ok=False)
expected_head="48c9794d4a5f1e8116e504b7396937e808e5b32f"
selected=json.loads(Path('/tmp/rustusb-logging-software-second-20261024/inputs-after.json').read_text())
def git(*args):return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
def inputs():return [dict(path=r['path'],bytes=(root/r['path']).stat().st_size,sha256=hashlib.sha256((root/r['path']).read_bytes()).hexdigest()) for r in selected]
def save(n,v):(out/n).write_text(json.dumps(v,indent=2)+'\n')
assert git('rev-parse','HEAD')==expected_head and not git('status','--porcelain') and inputs()==selected
save('inputs-before.json',inputs())
save('start.json',dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=expected_head,clean=True,scope='Second actual installed logging QEMU after scoped peer-stop expectation correction; first44b1 remains FAIL, default locked all-features release build from clean checkpoint. Product177 exact matches723 source; new189 harnesssource distinct. Three epochs are one paired run, no historical23 relabel.'))
(out/'runner.py').write_bytes(Path(__file__).read_bytes())
cmd=['python3','-B','scripts/qemu_process_logging/run.py','--kernel','6.8.0-106-generic','--family','ipv4','--evidence',str(out/'qemu')]
print(json.dumps(dict(start='fresh installed process logging QEMU after scoped expectation correction',head=expected_head)),flush=True)
with (out/'run.log').open('wb') as log: process=subprocess.run(cmd,cwd=root,stdout=log,stderr=subprocess.STDOUT)
row=dict(command=cmd,exit_code=process.returncode,source177_stable=inputs()==selected,head_guard=git('rev-parse','HEAD')==expected_head,clean_guard=not git('status','--porcelain'))
summary_path=out/'qemu/summary.json'
if summary_path.exists():
 q=json.loads(summary_path.read_text());row['actual_qemu_result']=q['result'];row['actual_guest_exits']=q.get('guest_exit_codes');row['actual_binary_sha256']=q.get('binary_sha256');row['actual_harness_inputs']=len(q.get('source_inputs',{}));row['actual_error']=q.get('error')
save('inputs-after.json',inputs());save('result.json',row);print(json.dumps(row),flush=True)
sys.exit(0 if process.returncode==0 and all(row[k] for k in ['source177_stable','head_guard','clean_guard']) else 1)


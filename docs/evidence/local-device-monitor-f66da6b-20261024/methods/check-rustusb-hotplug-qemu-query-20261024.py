from pathlib import Path
import subprocess,json,ast
out=Path('/tmp/rustusb-hotplug-qemu-fixture-check-20261024');out.mkdir(exist_ok=False)
begin={'event':'snapshot_begin','schema_version':1,'revision':1,'instance':'query-fixture','device_count':0,'change_count':0,'state':'current'}
end={'event':'snapshot_end','schema_version':1,'revision':1,'instance':'query-fixture','device_count':0,'change_count':0}
for name,frames,expected in [('complete',[begin,end],True),('partial',[begin],False),('mixed-epoch',[begin,{**end,'instance':'foreign'}],False),('wrong-count',[{**begin,'device_count':1},{**end,'device_count':1}],False)]:
 raw=''.join(json.dumps(frame)+'\n' for frame in frames);(out/(name+'.input')).write_text(raw)
 cmd=['jq','-s','-f','scripts/qemu_device_monitor/point.jq'];r=subprocess.run(cmd,input=raw.encode(),capture_output=True)
 (out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr);(out/(name+'.command.json')).write_text(json.dumps({'argv':cmd,'exit_code':r.returncode,'expected_success':expected},indent=2)+'\n');assert (r.returncode==0)==expected
for n in ['run.py','verify.py']:ast.parse(Path('scripts/qemu_device_monitor',n).read_text())
subprocess.run(['sh','-n','scripts/qemu_device_monitor/guest-init.sh'],check=True)
print('PASS four pure query cases plus Python/shell syntax; not USB execution')

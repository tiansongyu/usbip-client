from pathlib import Path
import subprocess,json,hashlib
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-hotplug-final-local-method-20261024');out.mkdir(exist_ok=False)
rows=json.loads(Path('/tmp/rustusb-hotplug-software-third-20261024/inputs-before.json').read_text())
def check():
 for row in rows:assert hashlib.sha256((root/row['path']).read_bytes()).hexdigest()==row['sha256']
check();results=[]
commands=[('all-cases',['python3','-B','scripts/test-device-monitor.py','--binary','target/release/rustusb','--evidence','/tmp/rustusb-hotplug-local-all-final-20261024']),('default-build',['cargo','build','--workspace','--release','--locked']),('default-cases',['python3','-B','scripts/test-device-monitor.py','--binary','target/release/rustusb','--evidence','/tmp/rustusb-hotplug-local-default-final-20261024']),('all-restore-build',['cargo','build','--workspace','--all-features','--release','--locked'])]
for name,argv in commands:
 with (out/(name+'.stdout')).open('wb') as stdout,(out/(name+'.stderr')).open('wb') as stderr:r=subprocess.run(argv,cwd=root,stdout=stdout,stderr=stderr)
 row={'name':name,'argv':argv,'exit_code':r.returncode,'actual_cli_sha256_after':hashlib.sha256((root/'target/release/rustusb').read_bytes()).hexdigest()};results.append(row);print(name,r.returncode,flush=True);assert r.returncode==0
check();assert results[0]['actual_cli_sha256_after']==results[-1]['actual_cli_sha256_after']=='d048e41c7dcdc71a2643a9e7ff2c454560838560fd174c9fb2f460333ca7b96d';assert results[1]['actual_cli_sha256_after']==results[2]['actual_cli_sha256_after']=='e85fa916acc9379f58598922c469092aba53726830809c382cbdd9f62e945295'
(out/'script-source.py').write_bytes((root/'scripts/test-device-monitor.py').read_bytes());(out/'summary.json').write_text(json.dumps({'result':'PASS','commands':results,'unchanged_compilation_inputs':206,'scope':'New process method with actual returned pipe capacity; unchanged prebuilt Rust source. Worktree has documentation/method edits; not relabelled clean software or QEMU execution.'},indent=2)+'\n')

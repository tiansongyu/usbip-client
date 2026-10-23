from pathlib import Path
import subprocess,json,hashlib,os,datetime
root=Path('/tmp/rustusb-prepare-stop-rust-client-checkout-20261024')
out=Path('/tmp/rustusb-prepare-stop-rust-client-first-20261024');out.mkdir(exist_ok=False)
selected=json.loads(Path('/tmp/rustusb-data-link-management-software-first-20261024/inputs-before.json').read_text())
selected.append({'path':'crates/rustusb-service/src/jobs/recording.rs'})
def inputs():
    return [dict(path=r['path'],bytes=(root/r['path']).stat().st_size,sha256=hashlib.sha256((root/r['path']).read_bytes()).hexdigest()) for r in selected]
before=inputs();(out/'inputs-before.json').write_text(json.dumps(before,indent=2)+'\n')
for name in ['ipc.rs','runtime.rs','runtime_tests.rs','jobs/recording.rs','jobs/client.rs','jobs/rust_server.rs','jobs/mod.rs']:
    b=(root/'crates/rustusb-service/src'/name).read_bytes();(out/name).parent.mkdir(parents=True,exist_ok=True);(out/name).write_bytes(b)
cmd=['cargo','test','-p','rustusb-service','--all-features','--locked','jobs::','--','--nocapture']
env=dict(os.environ,CARGO_TARGET_DIR='/home/ubuntu/github/usbip-client/target')
(out/'start.json').write_text(json.dumps(dict(utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),status=subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True),command=cmd,target_dir=env['CARGO_TARGET_DIR'],scope='First Rust/client fixed development subset, ordinary actual listeners and mTLS/oneshot fixtures; Native remains baseline; not USB/helper/QEMU or complete software acceptance; no USB/helper/QEMU or whole software acceptance.'),indent=2)+'\n')
with (out/'cargo.log').open('wb') as f:
    result=subprocess.run(cmd,cwd=root,env=env,stdout=f,stderr=subprocess.STDOUT)
after=inputs();(out/'inputs-after.json').write_text(json.dumps(after,indent=2)+'\n')
(out/'result.json').write_text(json.dumps(dict(exit_code=result.returncode,inputs_stable=before==after,selected_inputs=len(after)),indent=2)+'\n')
print('RUST CLIENT FIRST exit',result.returncode,'inputs_stable',before==after)
print('\n'.join((out/'cargo.log').read_text().splitlines()[-28:]))

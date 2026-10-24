from pathlib import Path
import subprocess,json
out=Path('/tmp/rustusb-hotplug-development-fifth-20261024');out.mkdir(exist_ok=False)
for name,cmd in [('format',['cargo','fmt','--all']),('discovery',['cargo','test','--offline','-p','rustusb-discovery']),('cli-frame',['cargo','test','--offline','-p','rustusb-cli','device_watch::']),('clippy',['cargo','clippy','--offline','--workspace','--all-targets','--all-features','--','-D','warnings'])]:
 r=subprocess.run(cmd,capture_output=True);(out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr);(out/(name+'.json')).write_text(json.dumps({'argv':cmd,'exit_code':r.returncode},indent=2)+'\n');print(name,r.returncode,flush=True)
 if r.returncode:print(r.stderr.decode()[-7000:]);print(r.stdout.decode()[-3000:]);break

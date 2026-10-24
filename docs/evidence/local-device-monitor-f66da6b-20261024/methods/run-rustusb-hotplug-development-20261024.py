from pathlib import Path
import subprocess,json,hashlib
out=Path('/tmp/rustusb-hotplug-development-first-20261024');out.mkdir(exist_ok=False)
commands=[('format',['cargo','fmt','--all']),('linux',['cargo','test','--offline','-p','rustusb-linux']),('service',['cargo','test','--offline','-p','rustusb-service','discovery::']),('cli-check',['cargo','check','--offline','-p','rustusb-cli','--all-targets'])]
for name,cmd in commands:
 r=subprocess.run(cmd,capture_output=True);(out/(name+'.stdout')).write_bytes(r.stdout);(out/(name+'.stderr')).write_bytes(r.stderr);(out/(name+'.json')).write_text(json.dumps({'argv':cmd,'exit_code':r.returncode},indent=2)+'\n');print(name,r.returncode,flush=True)
 if r.returncode: print(r.stderr.decode()[-5000:]);break

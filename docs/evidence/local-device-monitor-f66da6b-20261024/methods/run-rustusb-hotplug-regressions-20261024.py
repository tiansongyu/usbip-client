from pathlib import Path
import concurrent.futures,subprocess,json,hashlib
root=Path('/home/ubuntu/github/usbip-client');out=Path('/tmp/rustusb-hotplug-regressions-f66da6b-20261024');out.mkdir(exist_ok=False)
binary=root/'target/release/rustusb';before=hashlib.sha256(binary.read_bytes()).hexdigest();assert before=='d048e41c7dcdc71a2643a9e7ff2c454560838560fd174c9fb2f460333ca7b96d'
commands=[('signals',['python3','scripts/test-server-signals.py','--skip-build','--evidence',str(out/'signal-evidence')]),('backpressure',['python3','scripts/test-virtual-backpressure.py','--binary',str(binary)])]
def run(row):
 name,argv=row
 with (out/(name+'.stdout')).open('wb') as stdout,(out/(name+'.stderr')).open('wb') as stderr:
  result=subprocess.run(argv,cwd=root,stdout=stdout,stderr=stderr)
 receipt={'argv':argv,'exit_code':result.returncode};(out/(name+'.command.json')).write_text(json.dumps(receipt,indent=2)+'\n');return name,receipt
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 results=list(pool.map(run,commands))
after=hashlib.sha256(binary.read_bytes()).hexdigest();r={'result':'PASS' if all(v['exit_code']==0 for _,v in results) and before==after else 'FAIL','head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),'binary_before':before,'binary_after':after,'commands':dict(results),'scope':'Original four signal/two backpressure cases against the same prebuilt all-features CLI, not additional Cargo or USB qualification. Old backpressure lacks per-child executed-ELF/raw-wire samples.'};(out/'summary.json').write_text(json.dumps(r,indent=2)+'\n');print(r['result'],[(name,v['exit_code']) for name,v in results]);assert r['result']=='PASS'

import pathlib,subprocess,json,datetime,sys
repo=pathlib.Path('/home/ubuntu/github/usbip-client');out=pathlib.Path(sys.argv[1]);out.mkdir(exist_ok=False)
def git(*args):return subprocess.check_output(['git',*args],cwd=repo).decode().strip()
head=git('rev-parse','HEAD');status=git('status','--porcelain');remote=git('remote','get-url','--push','origin')
assert not status and remote=='git@github.com:tiansongyu/usbip-client.git'
(out/'head-before.txt').write_text(head+'\n');(out/'status-before.txt').write_text(status+'\n')
run=subprocess.run(['git','push','origin','HEAD:main'],cwd=repo,capture_output=True)
(out/'push.stdout.txt').write_bytes(run.stdout);(out/'push.stderr.txt').write_bytes(run.stderr)
actual=git('ls-remote','origin','refs/heads/main');after=git('status','--porcelain')
receipt={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'head':head,'remote':remote,'argv':['git','push','origin','HEAD:main'],'exit_code':run.returncode,'remote_main':actual,'status_after':after,'verified':run.returncode==0 and actual.split()[0]==head and not after}
(out/'receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt));raise SystemExit(0 if receipt['verified']else 1)

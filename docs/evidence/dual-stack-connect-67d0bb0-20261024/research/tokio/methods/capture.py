from pathlib import Path
import json,hashlib,urllib.request,subprocess,datetime,re
out=Path('/tmp/rustusb-dual-stack-tokio-research-20261024');assert out.exists()
(out/'capture-first-observation.txt').write_text('Initial capture helper exited1 before network fetch or probes because registry extraction has no .cargo-checksum.json. The corrected helper obtains expected checksum from project Cargo.lock and hashes the cached published .crate. No product failure or source change occurred. This records the observed tool failure, not a captured raw stderr file.\n')
root=Path('/home/ubuntu/.cargo/registry/src/index.crates.io-1949cf8c6b5b557f/tokio-1.54.1')
for name in ['.cargo_vcs_info.json','Cargo.toml','Cargo.toml.orig','LICENSE','CHANGELOG.md','src/net/tcp/stream.rs','src/net/addr.rs','src/task/blocking.rs','src/sync/semaphore.rs','src/runtime/task/join.rs']:
 p=out/'fixed-source'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((root/name).read_bytes())
urls={'registry.json':'https://crates.io/api/v1/crates/tokio/1.54.1','tcp-doc.html':'https://docs.rs/tokio/1.54.1/tokio/net/struct.TcpStream.html','blocking-doc.html':'https://docs.rs/tokio/1.54.1/tokio/task/fn.spawn_blocking.html','semaphore-doc.html':'https://docs.rs/tokio/1.54.1/tokio/sync/struct.Semaphore.html','fixed-addr.rs':'https://raw.githubusercontent.com/tokio-rs/tokio/b53b2001ed2ccfd9b87e32a0c6da1a1eebdc80b8/tokio/src/net/addr.rs'}
receipts=[]
for name,url in urls.items():
 try:
  with urllib.request.urlopen(url,timeout=20) as r:raw=r.read();status=r.status;final=r.url
  (out/name).write_bytes(raw);receipts.append(dict(path=name,url=url,final_url=final,status=status,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
 except Exception as e:receipts.append(dict(path=name,url=url,error=str(e)))
fixed=out/'fixed-addr.rs'
if fixed.exists():assert fixed.read_bytes()==(root/'src/net/addr.rs').read_bytes()
registry=json.loads((out/'registry.json').read_text())['version'];package=re.search(r'name = "tokio"\nversion = "1.54.1"\nsource = [^\n]+\nchecksum = "([a-f0-9]+)"',Path('/home/ubuntu/github/usbip-client/Cargo.lock').read_text()).group(1)
cache=Path('/home/ubuntu/.cargo/registry/cache/index.crates.io-1949cf8c6b5b557f/tokio-1.54.1.crate');actual=hashlib.sha256(cache.read_bytes()).hexdigest();assert registry['checksum']==package==actual
identity=dict(version='1.54.1',published=registry['created_at'],registry_checksum=package,cached_archive_sha256=actual,vcs=json.loads((root/'.cargo_vcs_info.json').read_text()),license='MIT',declared_rust_version='1.85',actual_rustc=subprocess.check_output(['rustc','-vV'],text=True),read_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),primary_fetches=receipts)
(out/'identity.json').write_text(json.dumps(identity,indent=2)+'\n');print('sources copied; primary fetches',[(r['path'],r.get('status'),r.get('error')) for r in receipts])
probe=out/'probe';(probe/'src').mkdir(parents=True,exist_ok=True)
(probe/'Cargo.toml').write_text('[package]\nname = "rustusb-tokio-dial-research-probe"\nversion = "0.0.0"\nedition = "2024"\n\n[dependencies]\ntokio = { version = "=1.54.1", features = ["rt-multi-thread", "macros", "net", "sync", "time"] }\n')

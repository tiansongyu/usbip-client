import concurrent.futures,hashlib,json,pathlib,urllib.request
root=pathlib.Path('/tmp/rustusb-debian-final-downloads-20261025');root.mkdir(mode=0o700,exist_ok=True)
metadata=json.load(open('/tmp/rustusb-debian-final-artifacts-02-20261025.json'))['structuredContent']['artifacts']
def fetch(item):
 raw=json.load(open('/tmp/rustusb-debian-final-download-'+str(item['id'])+'.private.json'))
 url=raw['structuredContent']['file_uri']['download_url'];target=root/(item['name']+'.zip')
 with urllib.request.urlopen(url,timeout=120) as src,target.open('wb') as dest:
  while chunk:=src.read(1024*1024):dest.write(chunk)
 h=hashlib.sha256(target.read_bytes()).hexdigest()
 assert h==item['digest'].removeprefix('sha256:') and target.stat().st_size==item['size_in_bytes']
 return {'artifact_id':item['id'],'file':target.name,'bytes':target.stat().st_size,'sha256':h}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(fetch,metadata))
(root/'download-receipts.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps({'downloaded':len(results),'bytes':sum(x['bytes'] for x in results)}))

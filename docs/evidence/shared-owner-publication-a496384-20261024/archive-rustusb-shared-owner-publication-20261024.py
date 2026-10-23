import pathlib,json,hashlib,shutil,subprocess,datetime
root=pathlib.Path('/home/ubuntu/github/usbip-client');tmp=pathlib.Path('/tmp');dest=root/'docs/evidence/shared-owner-publication-a496384-20261024';dest.mkdir(parents=True,exist_ok=False);entries=[]
def archive(p,rel):
 p=pathlib.Path(p);q=dest/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);raw=q.read_bytes();assert raw==p.read_bytes();entries.append({'path':rel,'original':str(p),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
head='a496384f9bdeb38e46ccb1c6fca4458b49e42eb8'
for p in sorted(tmp.glob('rustusb-shared-owner-content-*20261024.connector.json')):archive(p,'official/'+p.name)
for p in sorted((tmp/'rustusb-shared-owner-live-a496384-20261024').rglob('*')):
 if p.is_file():archive(p,'live/'+str(p.relative_to(tmp/'rustusb-shared-owner-live-a496384-20261024')))
for name in ['rustusb-shared-owner-live-dom-a496384-20261024.json','verify-rustusb-shared-owner-live-site-20261024.py','archive-rustusb-shared-owner-publication-20261024.py']:archive(tmp/name,name)
runs={}
for label in ['software','pages']:
 response=json.loads((tmp/f'rustusb-shared-owner-content-{label}-run-final-20261024.connector.json').read_text());run=json.loads(response['structuredContent']['content']);assert run['head_sha']==head and run['status']=='completed' and run['conclusion']=='success'
 jobfile=tmp/f'rustusb-shared-owner-content-{label}-jobs'+pathlib.Path('-final-20261024.connector.json') if False else None
 filename=f'rustusb-shared-owner-content-{label}-jobs'+('-final' if label=='software' else '')+'-20261024.connector.json'
 jobs=json.loads((tmp/filename).read_text())['structuredContent']['jobs'];assert all(j['status']=='completed' and j['conclusion']=='success' for j in jobs)
 runs[label]={'run_id':run['id'],'jobs':[{'id':j['id'],'name':j['name'],'conclusion':j['conclusion']} for j in jobs]}
remote=subprocess.check_output(['git','ls-remote','origin','refs/heads/main'],cwd=root,text=True).split()[0];assert remote==head
live=json.loads((tmp/'rustusb-shared-owner-live-a496384-20261024/report.json').read_text());assert live['result']=='PASS' and all(x['matches_git_blob'] for x in live['resources'])
source=json.loads((root/'docs/evidence/shared-owner-ci-fixture-2638a8c-20261024/software/inputs-after.json').read_text())
for i in source:assert hashlib.sha256((root/i['path']).read_bytes()).hexdigest()==i['sha256']
record={'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'published_content_head':head,'remote_main_before_receipt_commit':remote,'workflows':runs,'live_resources_exact_git_bytes':6,'live_dom':'530/9/18 visible, new report link, loaded images, console0, tab8 deliverable; not pixel screenshot','software_inputs_still_match2638':len(source),'detailed_530_ci':'exact2638 run39999926362 has complete decoded logs/117-predicate independent review in separateCI archive; a496 checks here are final delivery status, not new USB cases','final_receipt_scope':'subsequent commit only records publication and late immutable independent CI review; product152, site bytes and QEMU attribution unchanged','full_goal':'active; hardwareG1/G2, accepted initializer gate/kernel hardblock, arbitrary preparationWAL stall, fullPhase3 and later stages remain incomplete'}
p=tmp/'rustusb-shared-owner-publication-a496384-20261024.json';p.write_text(json.dumps(record,indent=2)+'\n');archive(p,'publication.json')
(dest/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':entries},indent=2)+'\n');print(json.dumps({'files':len(entries),'bytes':sum(i['bytes'] for i in entries),'manifest':str(dest/'manifest.json')}))

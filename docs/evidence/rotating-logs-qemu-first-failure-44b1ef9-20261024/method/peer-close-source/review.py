from pathlib import Path
import json,hashlib
REPO=Path('/home/ubuntu/github/usbip-client');BASE=Path('/tmp/rustusb-process-logging-qemu-first-coordinator-20261024/qemu');OUT=Path('/tmp/rustusb-process-logging-peer-close-source-review-20261024');seen={}
def read(p):
 b=p.read_bytes();seen[str(p)]={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()};return json.loads(b)
def health(d):return {x['session_id']:x['link']for x in d['link_history']}
def proof(root,s):
 a=s['attempts'][0]
 return [r for r in root['terminals']if r['terminal']=={'resource_id':a['resource_id'],'kind':a['kind'],'device_key':s['device_key'],'owner_uid':2000,'correlation':a['correlation'],'proof':'native_stub_available'}and r['server_id']is None]
def peers(server,client):
 sh,ch=health(server),health(client);out=[]
 for s in server['sessions']:
  link=sh.get(s['id'])
  if s['role']!='incoming'or not link or link['end']!='protocol':continue
  found=[]
  for c in client['sessions']:
   cl=ch.get(c['id'])
   if c['role']=='import'and cl and cl['end']=='local_stop'and c['device_key']==s['device_key']and link['identity']['tcp']=={'local':cl['identity']['tcp']['peer'],'peer':cl['identity']['tcp']['local']}:found.append(c)
  if len(found)==1:out.append({'server':s,'server_link':link,'client':found[0],'client_link':ch[found[0]['id']]})
 return out
summary=read(BASE/'summary.json');checks=[]
def ck(n,v):checks.append({'predicate':n,'pass':bool(v)})
current=[]
for epoch in [1,3]:
 server=read(BASE/'server-artifacts'/f'agent-stopped-{epoch}.json');client=read(BASE/'client-artifacts'/f'agent-stopped-{epoch}.json');held=read(BASE/'server-artifacts'/f'agent-held-{epoch}.json');root=read(BASE/'server-artifacts'/f'root-stopped-{epoch}.json');hs={s['id']:s for s in held['sessions']if not s['terminal']and s['role']=='incoming'};hh=health(held)
 pairs=[r for r in peers(server,client)if r['server']['id']in hs]
 ck(f'epoch{epoch} exactly2 original incoming State/Protocol same tuple LocalStop peers',len(pairs)==2 and all(r['server']['operation_error']=='state'and r['client']['operation_error']is None for r in pairs))
 for r in pairs:
  s=r['server'];h=hs[s['id']];ident=['id','instance_id','job_id','profile','role','device_key','principal'];original=lambda x:[{k:v for k,v in a.items()if k!='proof'}for a in x['attempts']]
  ck('original identity and exact NativeStub root proof '+s['id'],all(s[k]==h[k]for k in ident)and original(s)==original(h)and len(s['attempts'])==1 and s['attempts'][0]['kind']=='native_stub'and len(proof(root,s))==1)
  ck('held Connected/open and independent verified terminal '+s['id'],h['state']=='Connected'and h['operation_error']is None and hh[s['id']]['phase']=='open'and hh[s['id']]['end']is None and s['terminal']and s['state']=='Disconnected'and s['evidence']=='released'and s['cleanup']=='verified')
  r['epoch']=epoch;r['held_link']=hh[s['id']];r['root_terminal']=proof(root,s)[0]
 current.extend(pairs)
history=[]
for family in ['ipv4','ipv6']:
 b=REPO/'docs/evidence/prepare-recording-final-1582f06-20261024/runs'/f'health-native-{family}'
 s=read(b/'server-artifacts/agent-final.json');c=read(b/'client-artifacts/agent-final.json');ps=peers(s,c)
 ck('historical1582 '+family+' same final close classification in2peers',len(ps)==2 and all(p['server']['operation_error']=='state'and p['server']['cleanup']=='verified'and p['server']['state']=='Disconnected'and p['client']['operation_error']is None for p in ps))
 history.append({'family':family,'pairs':ps})
sources=[]
for name in ['crates/rustusb-service/src/jobs/native.rs','crates/rustusb-service/src/jobs/link.rs','crates/rustusb-service/src/jobs/client.rs','crates/rustusb-service/src/model.rs','crates/rustusb-service/src/runtime.rs','crates/rustusb-transport/src/link.rs','scripts/qemu_process_logging/guest-init.sh','scripts/qemu_process_logging/run.py']:
 p=REPO/name;b=p.read_bytes();m={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()};seen[str(p)]=m;ck('reviewed source matches actual44b1 input '+name,summary['source_inputs'].get(name)==m);sources.append({'path':name,**m})
control=read(BASE/'host-control.jsonl') if False else [json.loads(l)for l in (BASE/'host-control.jsonl').read_text().splitlines()]
p=BASE/'host-control.jsonl';b=p.read_bytes();seen[str(p)]={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
for ep in [1,3]:
 close=next(r for r in control if r['command']==f'CLOSE{ep}');stop=next(r for r in control if r['command']==f'STOP{ep}');ck(f'host commands closeclient{ep} beforestopserver{ep}',close['role']=='client'and stop['role']=='server'and close['monotonic']<stop['monotonic'])
ck('original run remains FAIL with first original owner assertion',summary['result']=='FAIL'and 'actual successful owner cleanup'in str(summary['error']))
after={p:{'bytes':len(Path(p).read_bytes()),'sha256':hashlib.sha256(Path(p).read_bytes()).hexdigest()}for p in seen};ck('all examined source and original bytes unchanged',seen==after)
r={'schema_version':1,'review_result':'PASS'if all(x['pass']for x in checks)else'FAIL','actual_run_result':'FAIL preserved, not rerun or reclassified','actual_head':summary['head'],'scope':'Narrow read-only source and current/historical raw state cross-review by logging storage/QEMU verifier author; not independent implementation and not full QEMU acceptance. No original verifier invoked.','checks':checks,'current_pairs':current,'historical_pairs':history,'sources':sources,'source_basis':[{'path':'crates/rustusb-service/src/jobs/client.rs','lines':'237-258','fact':'On caller stop, abort and join pure relay then join link with local stop before actual helper close. No successful management FIN handshake is promised for a live cancelled IMPORT.'},{'path':'crates/rustusb-service/src/jobs/link.rs','lines':'116-126,196-219','fact':'Protocol driver end maps to Error::Protocol; local_stop/peer_eof map to no join error; join is actual retained owner.'},{'path':'crates/rustusb-service/src/jobs/native.rs','lines':'1196-1218,1234-1255','fact':'Final LinkOwner error is retained in operation; actual stub close completes independently and Finish derives Verified/Unconfirmed from actual cleanup.'},{'path':'crates/rustusb-service/src/model.rs','lines':'64-74','fact':'Error::Protocol falls through to Failure::State; State is not by itself proof of an ownership failure.'},{'path':'crates/rustusb-service/src/runtime.rs','lines':'1155-1168','fact':'Verified cleanup leads to Disconnected/released while retaining non-null operation_error.'},{'path':'crates/rustusb-transport/src/link.rs','lines':'710-720,785-850','fact':'RecvStream errors collapse to a fixed failure and the driver may classify pump failure as Protocol; local stop terminates owner without proving peer graceful EOF. This raw run cannot identify the exact h2 failure branch or wire reset code.'}],'candidate':'Only original held Incoming IDs in normal epoch1 and fresh epoch3, tied to exact original instance/key/principal/NativeStub correlation/resource and same TCP tuple on client. Require held Connected/open/endNone + actual HID/FTDI I/O independently checked, client successful local_stop and completed cleanup before server STOP command. Permit State only when same server link has terminal Protocol and paired client link has terminal LocalStop; retain State and Protocol verbatim. Existing None remains its own outcome. No blanket Io/State/Timeout admission; no change to exports, epoch2 incoming, helper proof or final fullbaseline. Prior epoch history must preserve original values.','limits':['Some held serial samples are awaiting_first_reply; do not claim every preclose held sample observed PONG or use later history to backfill that observation.','This is documented cancellation/error-vs-cleanup behavior with matching historical evidence, not clean PeerEof or proof of a specific on-wire reset cause.','A product requirement for graceful peer-visible shutdown would need a separately specified data-session FIN/drain change; not inferred from this logging scenario.','Host command ordering is directly read here; full guest marker/I/O/Rootproof/global evidence remains separately reviewed by H/N.','The first exploratory read assumed top-level instance_id and raised KeyError before any verdict; corrected to exact session instance IDs; no source/raw edits.'],'originals_before':seen,'originals_after':after}
(OUT/'report.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({'result':r['review_result'],'checks':len(checks),'failed':[x for x in checks if not x['pass']],'report':str(OUT/'report.json'),'sha256':hashlib.sha256((OUT/'report.json').read_bytes()).hexdigest()},indent=2))

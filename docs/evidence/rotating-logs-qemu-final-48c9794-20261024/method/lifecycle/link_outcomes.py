#!/usr/bin/env python3
"""Preserve peer-close dimensions from original journals, without classifying them as clean EOF."""
import hashlib,json,sys
from pathlib import Path
p=Path(sys.argv[1]);out=Path(sys.argv[2]);sources={};pairs=[]
def read(role,kind,epoch):
 name=f'{role}-artifacts/agent-{kind}-{epoch}.json';raw=(p/name).read_bytes();sources[name]={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()};return json.loads(raw)
for epoch in [1,2,3]:
 owners={}
 for role in ['server','client']:
  held=read(role,'held',epoch);final=read(role,'stopped',epoch);ids={r['id'] for r in held['sessions'] if not r['terminal'] and r['role'] in ['incoming','import']};sessions=[r for r in final['sessions'] if r['id'] in ids]
  assert len(sessions)==2
  owners[role]={}
  for s in sessions:
   h=[x for x in final['link_history'] if x['session_id']==s['id'] and x['instance_id']==s['instance_id']];assert len(h)==1 and h[0]['link'] is not None
   owners[role][s['device_key']]={'session_id':s['id'],'instance_id':s['instance_id'],'operation_error':s['operation_error'],'state':s['state'],'cleanup':s['cleanup'],'evidence':s['evidence'],'attempts':s['attempts'],'link':h[0]['link']}
 assert set(owners['server'])==set(owners['client'])
 for key in owners['server']:
  s,c=owners['server'][key],owners['client'][key];st=s['link']['identity']['tcp'];ct=c['link']['identity']['tcp'];assert st['local']==ct['peer'] and st['peer']==ct['local'];pairs.append({'epoch':epoch,'device_key':key,'same_original_tcp_tuple':True,'server':s,'client':c})
r={'scope':'Direct original same-session outcome/health observations and reverse TCP endpoint correlation. No clean-EOF inference; no original verifier or product execution. Source-code attribution is separate.','pairs':pairs,'source_files':sources};out.write_text(json.dumps(r,indent=2)+'\n')

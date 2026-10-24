"""Strict raw JSONL/point/process evidence, in addition to the original verifier."""
import json
import re

def batches(path):
    rows=[json.loads(line) for line in path.read_text().splitlines()]
    result=[];active=None;devices=[];changes=[]
    for index,row in enumerate(rows):
        if row.get('event')=='stopped':
            if active is not None:raise RuntimeError('stopped receipt interrupted a batch')
            if row!={'event':'stopped','schema_version':1,'catalog_observer_joined':True,'observation_only':True}:raise RuntimeError('unexpected stopped receipt')
            continue
        if row.get('event')=='catalog_begin':
            if active is not None:raise RuntimeError('nested catalog batch')
            active=dict(row);active['begin_index']=index;devices=[];changes=[];continue
        if active is None or row.get('schema_version')!=1 or any(row.get(k)!=active[k] for k in ['instance','revision']):raise RuntimeError('catalog frame outside original batch')
        if row['event']=='catalog_device':devices.append(row['catalog'])
        elif row['event']=='catalog_change':changes.append(row['change'])
        elif row['event']=='catalog_end':
            if row['device_count']!=active['device_count'] or row['change_count']!=active['change_count'] or len(devices)!=(active['device_count'] or 0) or len(changes)!=active['change_count']:raise RuntimeError('incomplete catalog batch')
            result.append({'begin_index':active.pop('begin_index'),'end_index':index,'head':active,'devices':devices,'changes':changes});active=None
        else:raise RuntimeError('unexpected catalog frame')
    if active is not None:raise RuntimeError('unfinished final catalog batch')
    return rows,result

def verify(evidence,args):
    client=evidence/'client-artifacts';server=evidence/'server-artifacts';checks=[]
    def require(ok,name):
        if not ok:raise RuntimeError('catalog watch evidence: '+name)
        checks.append(name)
    def get(root,name):return json.loads((root/name).read_text())
    # The outer runner already matched this actual broker receipt to its frozen
    # ELF before invoking verify. Its final summary is not written until later.
    keys=get(server,'keys.json');elf=(client/'executable-agent-1.txt').read_text().split()[0]
    rows,closed=batches(client/'catalog-watch.stdout.txt')
    names=['ready','before-pause','stale','recovered','one','empty'];points=[]
    for name in names:
        point=get(client,'catalog-'+name+'.json');require(point in closed,'saved projection equals original full raw batch '+name);points.append(point)
    prior=get(client,'catalog-latest-before-pause.json')
    require(prior in closed and points[1]['head']['revision']>prior['head']['revision']==get(client,'catalog-before-pause-revision.json'),'pause follows a fresh completed publication, not a racy raw prefix read')
    require(len({p['head']['instance'] for p in points})==1,'same original observer through outage and list changes')
    require(all(a['head']['revision']<b['head']['revision'] for a,b in zip(points,points[1:])),'strictly increasing actual point revisions')
    for point,count in zip(points,[2,2,2,2,1,0]):require(point['head']['device_count']==count,'actual count '+str(count))
    require(points[2]['head']['state']=='stale' and points[2]['devices']==points[1]['devices'] and points[2]['changes']==[],'local-agent outage retains actual prior inventory without removal')
    require(points[3]['head']['state']=='current' and points[3]['head']['resync_required'] and {e['device_key'] for e in points[3]['devices']}==set(keys),'same fixed source successfully resynchronizes')
    require([e['device_key'] for e in points[4]['devices']]==[keys[1]],'HID export removal leaves serial visible')
    require(points[5]['head']['state']=='current' and points[5]['devices']==[],'successful empty catalog distinct from unavailable')
    require(any(c=={'kind':'removed','device_key':keys[0]} for c in points[4]['changes']),'HID stable-key removal')
    require(any(c=={'kind':'removed','device_key':keys[1]} for c in points[5]['changes']),'serial stable-key removal')
    require(rows[-1]=={'event':'stopped','schema_version':1,'catalog_observer_joined':True,'observation_only':True},'actual final observer receipt')
    for label,uid in [('watch-start',1000),('watch-paused',1000),('watch-recovered',1000),('watch-empty',1000),('direct',2000),('full',1000),('closed',1000)]:
        text=(client/('catalog-'+label+'.status.txt')).read_text();match=re.search(r'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$',text,re.M)
        require(match is not None and [int(x) for x in match.groups()]==[uid]*4,'actual UID '+label)
        require(all(re.search(r'^'+name+r':\s+0+$',text,re.M) is not None for name in ['CapInh','CapPrm','CapEff','CapAmb']),'no process capabilities '+label)
        require((client/('catalog-'+label+'.sha256.txt')).read_text().split()[0]==elf,'live executed CLI hash '+label)
    stats=[(client/('catalog-'+label+'.stat.txt')).read_text() for label in ['watch-start','watch-paused','watch-recovered','watch-empty']]
    identity=lambda line:(line.split(' ',1)[0],line.rsplit(') ',1)[1].split()[19])
    require(len({identity(line) for line in stats})==1,'watch PID and kernel starttime unchanged')
    require(re.search(r'^State:\s+T\b',(client/'catalog-watch-paused.status.txt').read_text(),re.M) is not None,'actual guest-only paused process gate')
    for label,code in [('watch',0),('direct',0),('full',0),('closed',1),('denied',1),('prefill',143)]:require((client/('catalog-'+label+'.wait.txt')).read_text().strip()==str(code),'actual child wait '+label)
    direct_rows,direct=batches(client/'catalog-direct.stdout.txt');require(any(b['head']['state']=='current' and b['head']['device_count']==2 and b['head']['origin']=='loaded_tls_client' for b in direct),'actual protected direct TLS watcher')
    require(direct_rows[-1].get('catalog_observer_joined') is True,'direct actual join receipt')
    denied_rows,denied=batches(client/'catalog-denied.stdout.txt');require(denied[-1]['head']['state']=='failed' and denied[-1]['head']['failure']=='permission' and denied[-1]['head']['device_count'] is None and denied[-1]['devices']==[],'UID1002 terminal denial is not empty success')
    before=get(client,'catalog-before-denied-agent.json');after=get(client,'catalog-after-denied-agent.json');events=[e for e in after['audit'] if e['sequence']>before['sequence']]
    require(len(events)==1 and events[0]['action']=='denied' and events[0]['actor_uid']==1002,'actual independent kernel-UID denial audit')
    require((client/'catalog-operator-private-key-unreadable.txt').read_text().strip()=='0','operator cannot read broker private key')
    require((client/'catalog-full.syscall.txt').read_text().startswith('1 0x1 ') and (client/'catalog-full.wchan.txt').read_text().strip()=='pipe_write','actual blocked stdout write syscall')
    require('stdout_drained_before_wait=false' in (client/'catalog-full.observation.txt').read_text(),'full output not drained to manufacture exit')
    require('1 -> /tmp/catalog-full.fifo' in (client/'catalog-full.fds.txt').read_text(),'actual stdout descriptor is full FIFO')
    for name in ['full','closed']:
        flags=re.search(r'^flags:\s+([0-7]+)$',(client/('catalog-'+name+'.stdout-fdinfo.txt')).read_text(),re.M)
        require(flags is not None and int(flags[1],8)&3==1,'actual write-only stdout '+name)
    before=get(client,'catalog-before-full-agent.json');after=get(client,'catalog-during-full-agent.json');events=[e for e in after['audit'] if e['sequence']>before['sequence']]
    require(len(events)==1 and events[0]['action']=='discover' and events[0]['actor_uid']==1000,'single completed original remote read while stdout is blocked')
    require(before['jobs']==after['jobs']==[] and before['sessions']==after['sessions']==[],'catalog observer created no USB job or session')
    require('Broken pipe' in (client/'catalog-closed.stderr.txt').read_text(),'closed reader preserves actual IO error')
    for name,key in [('hid',keys[0]),('serial',keys[1])]:
        before=get(server,'catalog-export-'+name+'-before.json')['response']['sessions'];after=get(server,'catalog-export-'+name+'-after.json')['response']['sessions']
        old=[s for s in before if s['role']=='export' and s['device_key']==key and not s['terminal']];require(len(old)==1,'original export selected '+name)
        now=[s for s in after if s['id']==old[0]['id']];require(len(now)==1 and now[0]['terminal'] and now[0]['cleanup']=='verified','same actual export terminated '+name)
    return {'scope':'actual operator/direct installed watchers and original Native USB fixture; polling metadata, not remote hardware generation or full G3','checks':checks,'assertion_count':len(checks),'main_watch_complete_batches':len(closed),'installed_cli_cases':4,'fast_denial_live_elf_receipt':False}

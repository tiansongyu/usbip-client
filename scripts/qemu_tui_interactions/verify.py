"""Verify real product frames/OS identity and original independent monitor evidence."""
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
original=module('tui_interactions_original_monitor_verifier',ROOT/'scripts/qemu_device_monitor/verify.py')
decoder=module('tui_interactions_real_frame_decoder',ROOT/'scripts/qemu_tui/decode.py')

def extract(evidence):
    text=(evidence/'guest-evidence.log').read_text();out=evidence/'tui-artifacts';out.mkdir(exist_ok=False)
    records=[];total=0
    pattern=r'RUSTUSB_TUI_FILE_BEGIN name=([A-Za-z0-9_.-]+) bytes=(\d+) sha256=([0-9a-f]{64})\n(.*?)RUSTUSB_TUI_FILE_END name=\1\n'
    for m in re.finditer(pattern,text,re.S):
        name,size,sha,encoded=m.groups();data=base64.b64decode(''.join(encoded.split()),validate=True)
        assert name not in {r['name']for r in records}and len(records)<128
        assert len(data)==int(size)and len(data)<=1048576 and hashlib.sha256(data).hexdigest()==sha
        total+=len(data);assert total<=16*1024*1024
        (out/name).write_bytes(data);records.append({'name':name,'bytes':len(data),'sha256':sha})
    assert records
    (evidence/'tui-decoded-files.json').write_text(json.dumps(records,indent=2)+'\n');return out

def verify(evidence,summary):
    base=original.verify(evidence,summary)
    log=(evidence/'guest-evidence.log').read_text()
    assert 'RUSTUSB_TUI_FORCED_CHILD'not in log and 'TUI_NO_USB_OWNERS modules_absent=true helper_socket_absent=true admitted_requests=0' in log
    out=extract(evidence);read=lambda n:json.loads((out/n).read_text());rendered={};checks=[]
    for mode in ['hotplug','preferences','reload-error','deny']:
        prefix='tui-'+mode
        fields=dict(line.split('=',1)for line in(out/(prefix+'.result')).read_text().splitlines())
        expected=1 if mode=='deny'else 0
        assert fields['uid']=='1000'and fields['expected_exit']==str(expected)and fields['exit']==str(expected)
        assert all(fields[k]=='1'for k in ['waited','raw_observed','resized','restored','entered','left'])
        status=(out/(prefix+'.status')).read_text()
        assert re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',status,re.M)
        assert re.search(r'^Pid:\s+'+fields['pid']+'$',status,re.M)
        assert all(re.search(r'^'+c+r':\s+0000000000000000$',status,re.M)for c in ['CapInh','CapPrm','CapEff','CapAmb'])
        assert(out/(prefix+'.exe-sha256')).read_text().split()[0]==summary['binary_sha256']['rustusb']
        tty=dict(line.split('=',1)for line in(out/(prefix+'.termios')).read_text().splitlines())
        assert all(tty['before_'+k]==tty['after_'+k]for k in ['flags','speeds','cc'])
        rendered[mode]=decoder.frames((out/(prefix+'.raw')).read_bytes())
        (evidence/(prefix+'-frames.txt')).write_text('\n\n--- FRAME ---\n\n'.join(rendered[mode]))
        checks.append(f'{mode}: actual UID1000/zero caps/ELF/wait{expected}/raw/resize/saved termios restored')
    # The original verifier also checks all six current snapshots, hint counts and independent sysfs stamps.
    old=evidence/'guest-artifacts';first=json.loads((old/'point-added.json').read_text())['devices'][0]
    second=json.loads((old/'point-readded.json').read_text())['devices'][0]
    frames=rendered['hotplug'];a=first['device']['device_address'];b=second['device']['device_address'];assert a!=b
    before=[i for i,f in enumerate(frames)if f'"device_address": {a},'in f]
    missing=[i for i,f in enumerate(frames)if 'Details'in f and 'No selected metadata'in f]
    after=[i for i,f in enumerate(frames)if f'"device_address": {b},'in f]
    assert missing,'removed selection feedback scrolled out of the actual open details panel'
    assert any(x<y<z for x in before for y in missing for z in after),'actual first address / visible disappearance / new address sequence missing'
    actors=[]
    for phase in ['initial','before-stop']:
        text=(out/f'tui-actor-{phase}.txt').read_text()
        assert re.search(r'^Uid:\s+1000\s+1000\s+1000\s+1000$',text,re.M)and '/dev/bus/usb/'not in text
        assert summary['binary_sha256']['rustusb']+'  /proc/'in text and 'tui --config /etc/rustusb/agent.json'in text
        pid=re.search(r'^PID=(\d+)$',text,re.M).group(1)
        stat=re.search(r'^'+pid+r' \([^\n]+\) (.+)$',text,re.M).group(1).split()
        sockets=set(re.findall(r'TARGET=socket:\[(\d+)\]',text));group=[]
        for line in text.split('NETLINK_TABLE\n',1)[1].splitlines()[1:]:
            row=line.split()
            if len(row)>=10 and row[-1]in sockets and row[1]=='15'and int(row[3],16)==1:group.append(row[-1])
        assert len(group)==1;actors.append((pid,stat[19],group[0]))
    assert actors[0]==actors[1]
    def options(frame,seconds,auto,mono):return f'Refresh interval: {seconds} seconds'in frame and f'Auto refresh: {str(auto).lower()}'in frame and f'Monochrome: {str(mono).lower()}'in frame
    frames=rendered['preferences']
    initial=[i for i,f in enumerate(frames)if options(f,3,False,True)]
    changed=[i for i,f in enumerate(frames)if options(f,4,True,False)]
    defaults=[i for i,f in enumerate(frames)if options(f,2,True,False)]
    loaded=[i for i,f in enumerate(frames)if options(f,5,False,True)and 'Reloaded checked preferences'in f]
    assert any(a<b<c<d for a in initial for b in changed for c in defaults for d in loaded),'actual changed/defaults/reloaded settings sequence'
    assert read('tui-preferences-initial.json')['options']=={'refresh_seconds':3,'auto_refresh':False,'monochrome':True}
    assert read('tui-preferences-changed.json')['options']=={'refresh_seconds':5,'auto_refresh':False,'monochrome':True}
    assert(out/'tui-preferences-changed-stat.txt').read_text().strip()=='1000:600:1'
    assert(out/'tui-preferences-error-stat.txt').read_text().strip()=='1000:644:1'
    assert any('Reload failed:'in f and options(f,5,False,True)for f in rendered['reload-error']),'actual reload failure changed effective options or was hidden'
    policy=read('tui-actual-manifest.json');assert policy['operators']==[{'uid':1000,'profiles':['observe'],'actions':['read','audit']}]
    assert policy['broker_uid']==2000 and policy['profiles']==[{'id':'observe','backend':'native','config':'/etc/rustusb/server.json'}]
    assert(out/'tui-config-stat.txt').read_text().splitlines()==['0:644:1','0:644:1','2000:600:1']
    assert(out/'tui-operator-key-unreadable.txt').read_text().strip()=='0'
    state=read('tui-agent-after-denials.json');assert state==read('tui-agent-final.json')
    assert state['jobs']==state['sessions']==state['requests']==[]and len(state['audit'])==2
    assert all(e['actor_uid']==1000 and e['action']=='denied'and e['failure']=='permission'for e in state['audit'])
    assert any('Agent refused: Permission'in f for f in rendered['deny'])and any('Denied'in f and 'Permission'in f for f in rendered['deny'])
    raw=(out/'tui-deny.raw').read_text();ids=set(re.findall(r'profile observe request_id ([0-9a-f-]{36})',raw));assert len(ids)==1
    original_id=next(iter(ids));assert any('Resolve ONLY original start'in f and original_id in f for f in rendered['deny'])
    assert not any('Admitted job'in f or 'Resolved original job'in f for f in rendered['deny'])
    agent=(out/'agent-identity.txt').read_text();assert re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',agent,re.M)
    assert all(re.search(r'^'+c+r':\s+0000000000000000$',agent,re.M)for c in ['CapInh','CapPrm','CapEff','CapAmb'])
    assert(out/'agent-exe-sha256.txt').read_text().split()[0]==summary['binary_sha256']['rustusb']
    assert(out/'agent-wait.txt').read_text().strip()=='0'
    checks +=['same actual TUI PID/starttime/group1 kernel socket across real hotplug','actual d defaults and l changed-file reload; denied reload preserves effective options','two real authoritative Start/retry denials; original UUID retained, zero admitted work; actual child exit1 and agent wait0']
    return {'original_monitor':base,'actual_tui_children':4,'normal_exit0':3,'expected_unresolved_exit1':1,'checks':checks,'scope':'One no-NIC guest: actual UI observation/settings/OS authorization, not USB/IP redirection or hardware proof'}

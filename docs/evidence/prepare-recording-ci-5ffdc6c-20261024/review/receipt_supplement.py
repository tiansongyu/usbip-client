"""Additional actual process/pipe receipt checks from printed official logs."""
import hashlib
import json
import re
from pathlib import Path
B=Path('/tmp/rustusb-prepare-stop-ci-5ffdc6c-20261024')
O=Path('/tmp/rustusb-prepare-stop-ci-independent-5ffdc6c-20261024')
checks=[];facts=[]
def ck(ok,name):
 checks.append({'check':name,'passed':bool(ok)})
 if not ok:raise RuntimeError(name)
for jid in [120723319604,120723319703,120723319780,120723319787]:
 original=(B/f'job-{jid}.log').read_bytes()
 text=re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]','',re.sub(r'^\ufeff?\d{4}-\d\d-\d\dT\S+Z ?','',original.decode(),flags=re.M))
 decoder=json.JSONDecoder();signals=None
 for m in re.finditer(r'(?m)^\{',text):
  try:v,n=decoder.raw_decode(text[m.start():])
  except json.JSONDecodeError:continue
  if isinstance(v,dict) and 'binary_before' in v:signals=v;break
 if signals is None:raise RuntimeError('full signal summary missing')
 binary=signals['binary_before']
 for case in signals['cases']:
  label=str(jid)+' '+case['mode']+'/'+case['signal'];ex=case['executed_binary'];child=case['child']
  ck(ex['proc_exe']==f"/proc/{child['pid']}/exe" and ex['resolved']==binary['path'] and all(ex[k]==binary[k] for k in ['sha256','device','inode']),label+' exact actual proc-exe path/device/inode/hash')
  if case['mode']=='blocked':
   w=case['pipe']['blocked_writer']
   ck(w['wchan'] in {'pipe_write','anon_pipe_write'} and w['comm'].startswith('virtual-test-') and w['monotonic_ns']<case['sent']['monotonic_ns'],label+' actual owned stdout task wait precedes sent signal')
   facts.append({'job':jid,'case':case['signal'],'blocked_writer':w,'sent_monotonic_ns':case['sent']['monotonic_ns']})
 ck((B/f'job-{jid}.log').read_bytes()==original,str(jid)+' original log unchanged')
result={'result':'PASS','checks':checks,'check_count':len(checks),'actual_blocked_plain_signal_receipts':facts,'scope':'Printed original process/pipe evidence only; unavailable individual runner files/ELF payloads are not reconstructed.'}
p=O/'receipt-supplement.json'
if p.exists():raise RuntimeError('Refusing overwrite')
p.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({'passed':True,'checks':len(checks),'report_sha256':hashlib.sha256(p.read_bytes()).hexdigest()}))

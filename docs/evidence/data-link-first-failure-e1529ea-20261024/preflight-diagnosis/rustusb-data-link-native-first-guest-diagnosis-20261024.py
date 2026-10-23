"""Read only first-run fixture/command audit, independent of harness verifiers."""
import base64
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path('/tmp/rustusb-data-link-native-ipv4-e1529ea-20261024')
OUT = Path('/tmp/rustusb-data-link-native-first-guest-diagnosis-20261024.json')
checks, originals = [], {}
def require(name, value):
    checks.append({'name':name, 'passed':bool(value)})
    if not value:
        raise RuntimeError(name)
def raw(name):
    p = ROOT / name
    value = p.read_bytes()
    originals[name] = {'bytes':len(value), 'sha256':hashlib.sha256(value).hexdigest()}
    return value
def data(name):
    return json.loads(raw(name))
summary = data('summary.json')
require('original overall FAIL and first client marker retained', summary['result']=='FAIL' and summary['error']=='RuntimeError: guest reported actual failure' and summary['observed_failure_markers']==[{'role':'client','line':'RUSTUSB_LINK_RESULT: FAIL role=client stage=client-prepare exit=1'}])
require('actual source e152 and terminal guest exits independently scoped', summary['head']=='e1529eafd56b7e3c4f2b0a450c25afd8e427efd1' and summary['guest_exit_codes']=={'client':0,'server':0} and summary.get('completed_utc'))
require('reported four source guards true, not a test PASS', all(summary[x] is True for x in ('source_guard','binary_guard','head_guard','clean_guard')))
response = data('client-artifacts/catalog-initial.json')
require('actual initial Catalog returned structured io', response=={'journal_healthy':True,'response':{'code':'io','result':'error'},'schema_version':1})
require('client had not started either device job', not list((ROOT/'client-artifacts').glob('start-*.json')))
for family in (4,6):
    require(f'plaintext IPv{family} fixture got actual refusal', b'Connection refused' in raw(f'client-artifacts/plaintext-{family}.log'))
keys = data('keys.json')
client = data('client-artifacts/actual-config-1-client-hid.json')
server = data('server-artifacts/actual-config-1-server.json')
policy = data('client-artifacts/actual-config-1-policy.json')
expected={'protocol':'framed_v2','interval_millis':1000,'response_timeout_millis':3000}
require('both actual TLS config schema2 and same bounded data-link policy', client['schema_version']==server['schema_version']==2 and client['data_link']==server['data_link']==expected)
require('actual endpoint/name/device map valid for fixed guest', client['endpoint']=={'host':'192.0.2.1','port':3241} and server['listen']=='192.0.2.1:3241' and client['server_name']=='rustusb-qemu.test' and client['device']==keys[0] and server['devices']==keys)
require('current client helper broker/pool grants exact expected server and keys', policy['secure_broker_uid']==2000 and policy['trusted_network'] is False and policy['secure_imports']==[{'server_id':'qemu-server','device_keys':keys}])
for public in ('ca.der','client.der','server.der'):
    require('actual public '+public+' identical across guests', raw('client-artifacts/actual-config-1-'+public)==raw('server-artifacts/actual-config-1-'+public))
pin=hashlib.sha256(raw('client-artifacts/actual-config-1-client.der')).hexdigest()
require('actual public pin exact client DER digest', raw('server-artifacts/actual-config-1-client-pin.txt')==(pin+'\n').encode() and server['principals']==[{'id':'qemu-client','pins':[pin],'devices':keys}])
require('actual CA/chain references exact fixed public files', client['ca_certificates']==server['ca_certificates']==['/etc/rustusb/ca.der'] and client['certificate_chain']==['/etc/rustusb/client.der'] and server['certificate_chain']==['/etc/rustusb/server.der'])
ready = data('server-artifacts/server-ready.json')['response']['sessions']
root = data('server-artifacts/root-initial-exported.json')
require('server really published two Available export sessions only', len(ready)==2 and all(s['role']=='export' and s['state']=='Available' and s['evidence']=='export_ready' and not s['terminal'] for s in ready))
require('root real export grants exist but no client import or server stub', len(root['exports'])==2 and root['records']==[] and all(x['stub'] is None for x in root['exports']))
blocks=0
for role in ('server','client'):
    log=raw(role+'-evidence.log').decode()
    for m in re.finditer(r'^SECURE_ARTIFACT_BEGIN ([^\n]+) (\d+)\n([A-Za-z0-9+/=\n]+)\nSECURE_ARTIFACT_END \1\n',log,re.M):
        name,size,encoded=m.groups();value=base64.b64decode(''.join(encoded.split()),validate=True)
        require(role+' complete raw base64 '+name, len(value)==int(size) and (ROOT/(role+'-artifacts')/name).read_bytes()==value)
        blocks+=1
    status=raw(role+'-artifacts/identity-agent-1.txt').decode()
    require(role+' actual agent UID2000 zero effective caps', re.search(r'^Uid:\s+2000\s+2000\s+2000\s+2000$',status,re.M) and re.search(r'^CapEff:\s+0000000000000000$',status,re.M))
    require(role+' actual ELF observed matches frozen summary', raw(role+'-artifacts/executable-agent-1.txt').decode().split()[0]==summary['binary_sha256']['rustusb'] and raw(role+'-artifacts/executable-helper.txt').decode().split()[0]==summary['binary_sha256']['rustusb-helper'])
    require(role+' no actual agent detailed diagnostic was collected', raw(role+'-artifacts/agent-1.log')==b'')
require('complete artifact base64 originals found',blocks>0)
valve=[json.loads(row) for row in raw('valve.jsonl').splitlines()]
counts=Counter(row['event'] for row in valve)
require('44 actual ingress packets all forwarded; no arm/drop',counts['packet']==counts['forward']==44 and counts['arm']==0 and not any(row.get('action')=='drop' for row in valve))
controls=[json.loads(row) for row in raw('host-control.jsonl').splitlines()]
require('only keys and START preceded first actual failure', [r['mark'] for r in controls]==['keys','start'])
result={
 'result':'READ_ONLY_GUEST_DIAGNOSIS_COMPLETE_ORIGINAL_RUN_FAIL',
 'scope':'Actual first Native-v4 guest command/fixture boundary only; not a full packet/lifecycle acceptance. No runner verify or packet_audit invoked, no product or harness edits, no Cargo/QEMU.',
 'first_command':'agent discover hid > /tmp/catalog-initial.json',
 'first_result':response,
 'finding':'Initial v2-configured Catalog call failed with typed Io before either client job, any IMPORT, actual USB I/O, or valve arm. The fixed guest endpoint/config/key/pin/public DER and two Available server exports were present. This audit does not locate the inner product IO operation or prove the proposed h2 close/drain mechanism.',
 'secondary':'Server fail at server-c2s follows root collector ABORT; actual guest exit0 is the disposable PID1 poweroff result, not an operation PASS. The valve failure on later guest EOF is after the original failure and no deliberate fault had been armed.',
 'check_count':len(checks),'checks':checks,'raw_base64_blocks':blocks,'valve_counts':dict(counts),'originals':originals,
 'limitations':['Encrypted packets cannot identify Catalog application bytes or PONGs without endpoint evidence.','Empty actual agent logs do not reveal the inner IO kind/path.','No Native data-session, heartbeat timeout, resource proof, or hardware success is claimed.'],
}
require('all inspected originals unchanged',all((ROOT/name).stat().st_size==meta['bytes'] and hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==meta['sha256'] for name,meta in originals.items()))
result['check_count']=len(checks)
OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
print(json.dumps({'report':str(OUT),'sha256':hashlib.sha256(OUT.read_bytes()).hexdigest(),'checks':len(checks),'blocks':blocks}))

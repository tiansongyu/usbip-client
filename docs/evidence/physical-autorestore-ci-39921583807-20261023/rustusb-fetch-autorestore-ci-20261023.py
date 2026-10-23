from pathlib import Path
import base64, concurrent.futures, datetime, hashlib, json, subprocess

D = Path('/tmp/rustusb-autorestore-ci-39921583807-20261023')
D.mkdir(exist_ok=True)
HEAD = '2ab997673a5894099f8df770707a060da3735154'
RUN = 39921583807
REPO = 'tiansongyu/usbip-client'

def fetch(name, endpoint):
    command = ['gh', 'api', '--method', 'GET', endpoint]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    (D/name).write_bytes(result.stdout)
    (D/(name+'.stderr')).write_bytes(result.stderr)
    return {'command':command, 'path':name, 'returncode':result.returncode,
            'length':len(result.stdout), 'sha256':hashlib.sha256(result.stdout).hexdigest(),
            'stderr_length':len(result.stderr), 'fetched_utc':datetime.datetime.now(datetime.timezone.utc).isoformat()}

stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S')
tasks = [(f'run-{stamp}.json', f'repos/{REPO}/actions/runs/{RUN}'),
         (f'jobs-{stamp}.json', f'repos/{REPO}/actions/runs/{RUN}/jobs?per_page=100')]
responses = []
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
    responses.extend(pool.map(lambda task:fetch(*task), tasks))
run=json.loads((D/tasks[0][0]).read_bytes()); jobs=json.loads((D/tasks[1][0]).read_bytes())
assert run['id']==RUN and run['head_sha']==HEAD
complete=all(job['status']=='completed' for job in jobs['jobs'])
if complete:
    for old,new in [(tasks[0][0],'run-final.json'),(tasks[1][0],'jobs-final.json')]:
        (D/new).write_bytes((D/old).read_bytes())
    tasks=[(f"job-{job['id']}.log",f"repos/{REPO}/actions/jobs/{job['id']}/logs") for job in jobs['jobs']]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        responses.extend(pool.map(lambda task:fetch(*task),tasks))
    for row in responses[-4:]:assert row['returncode']==0, row
else:
    print(json.dumps({'complete':False,'jobs':[{k:j[k] for k in ['id','name','status','conclusion']} for j in jobs['jobs']]}))
provenance=D/f'fetch-provenance-{stamp}.json'
provenance.write_text(json.dumps({'responses':responses,'complete':complete},indent=2)+'\n')
if complete:
    (D/'fetch-provenance.json').write_bytes(provenance.read_bytes())
    print(json.dumps({'complete':True,'run':run['conclusion'],'jobs':[j['conclusion'] for j in jobs['jobs']], 'directory':str(D)}))

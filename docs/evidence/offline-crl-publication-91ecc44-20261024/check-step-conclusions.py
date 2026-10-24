import hashlib,json,pathlib
root=pathlib.Path(__file__).parent;source=root/'jobs-40121403621-final.json';data=json.loads(source.read_text())['structuredContent']['jobs'];rows=[]
assert len(data)==4
for job in data:
 assert job['run_id']==40121403621 and job['status']=='completed' and job['conclusion']=='success'
 for name in ['Verify native architecture and record the software environment','Formatting, lint, tests, release and documentation']:
  matches=[step for step in (job.get('steps') or []) if step['name']==name];assert len(matches)==1 and matches[0]['status']=='completed' and matches[0]['conclusion']=='success';rows.append({'job_id':job['id'],'job_name':job['name'],'step':matches[0]})
result={'result':'PASS','source':source.name,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'steps':rows,'scope':'Actual primary API architecture/full-gate step conclusions; not full CI logs/per-platform executed ELF or927 recount.'}
(root/'step-check.json').write_text(json.dumps(result,indent=2)+'\n');print('PASS',len(rows),'primary step conclusions')

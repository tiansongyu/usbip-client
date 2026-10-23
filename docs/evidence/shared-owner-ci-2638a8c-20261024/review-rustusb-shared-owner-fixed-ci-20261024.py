import pathlib,json,re,hashlib,subprocess
root=pathlib.Path('/home/ubuntu/github/usbip-client');tmp=pathlib.Path('/tmp');head='2638a8c4700d79233d283de1a168f574be5f8ee4'
meta=json.loads((tmp/'rustusb-shared-owner-ci-testfix-jobs-final-20261024.connector.json').read_text())['structuredContent']['jobs'];run=json.loads(json.loads((tmp/'rustusb-shared-owner-ci-testfix-run-final-20261024.connector.json').read_text())['structuredContent']['content']);assert run['status']=='completed' and run['conclusion']=='success' and run['head_sha']==head
rows=[]
for job in meta:
 assert job['status']=='completed' and job['conclusion']=='success'
 assert all(s['conclusion']=='success' for s in job['steps']),job
 p=tmp/f"rustusb-shared-owner-ci-testfix-job-{job['id']}-20261024.connector.json";log=json.loads(p.read_text())['structuredContent']['content']
 tests=[m.groups() for m in re.finditer(r'test result: (\w+)\. (\d+) passed; (\d+) failed; (\d+) ignored;',log)];counts={'passed':sum(int(m[1]) for m in tests),'failed':sum(int(m[2]) for m in tests),'ignored':sum(int(m[3]) for m in tests),'result_lines':len(tests)};assert counts=={'passed':530,'failed':0,'ignored':0,'result_lines':39},counts
 records=[]
 for line in log.splitlines():
  value=re.sub(r'^\d{4}-\d\d-\d\dT[^ ]+Z ','',line)
  if value.startswith('{'):
   try:records.append(json.loads(value))
   except json.JSONDecodeError:pass
 summary=next(x for x in records if x.get('result')=='PASS' and len(x.get('cases',[]))==5 and 'mutual_tls' in x['cases'][0])
 observations=[x for x in records if 'blocked_writer_observation' in x];assert len(observations)==3
 for c in summary['cases']:
  assert c['mutual_tls'] and c['authenticated_imports']==2 and c['pending_in_per_import']==1 and c['result']=='PASS'
  assert len(c['data_connection_closure'])==2
  if c['case'].startswith('ready'):
   assert c['exit']==0 and c['backend_cleanup_snapshot_observed'] and c['blocked'] is None
  else:
   assert not c['backend_cleanup_snapshot_observed'] and c['blocked']['capacity']==4096 and c['blocked']['synthetic_fill']==4096 and c['blocked']['stdout_drained_after_fill'] is False
  assert c['exit']==(1 if c['case']=='blocked-deadline' else 0)
 for record in observations:
  o=record['blocked_writer_observation'];assert o['error'] is None
  c=next(c for c in summary['cases'] if c['case']==record['case']);matched=o['matched'];assert matched['tid']==c['blocked']['blocked_writer_task']
  assert matched['comm']==c['blocked']['blocked_writer_comm'] and matched['wchan']==c['blocked']['blocked_writer_wchan'];assert matched['wchan'].strip() in ['pipe_write','anon_pipe_write']
 kernel=next(l for l in log.splitlines() if re.search(r'Z Linux .* GNU/Linux',l));expected='anon_pipe_write' if 'ubuntu-24.04' in job['name'] else 'pipe_write';assert all(x['blocked_writer_observation']['matched']['wchan'].strip()==expected for x in observations)
 assert all(word in log for word in ['"source_unchanged": true','"binary_unchanged": true','"stdout_drained_after_fill": false','"mode": "sigint"','"mode": "deadline"'])
 rows.append({'job_id':job['id'],'name':job['name'],'result':'PASS','tests':counts,'kernel':kernel,'secure_five_cases':summary,'actual_writer_observations':observations,'old_two_backpressure_and_four_signals':'successful original named steps plus logged summaries/guards','official_connector_log':str(p),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
inputs=json.loads((root/'docs/evidence/shared-owner-ci-fixture-2638a8c-20261024/software/inputs-after.json').read_text())
for i in inputs:assert hashlib.sha256(subprocess.check_output(['git','show',head+':'+i['path']],cwd=root)).hexdigest()==i['sha256']
report={'result':'PASS','run_id':run['id'],'head':head,'source_inputs_matched':len(inputs),'jobs':rows,'scope':'four actual native Ubuntu22/24 x x86_64/aarch64 CI; 530 executions and five real mTLS fixture scenarios per job, old2backpressure/4signal; direct official connector decoded logs, no missing raw runner files reconstructed. No QEMU/hardware/throughput proof. New 24 actual anon_pipe_write does not retroactively provide old failure wchan.'}
(tmp/'rustusb-shared-owner-ci-testfix-independent-summary-20261024.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps({'result':'PASS','run_id':run['id'],'jobs':[{'id':x['job_id'],'tests':x['tests'],'actual_wchan':x['actual_writer_observations'][0]['blocked_writer_observation']['matched']['wchan']} for x in rows]}))

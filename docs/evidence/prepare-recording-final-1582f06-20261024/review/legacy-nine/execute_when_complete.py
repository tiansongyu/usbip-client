#!/usr/bin/env python3
"""Run only frozen independent parsers after all nine original groups are terminal."""
from pathlib import Path
import argparse,hashlib,json,subprocess,sys
HERE=Path(__file__).resolve().parent
BASE=Path('/tmp/rustusb-prepare-stop-final-1582f06-20261024')
NAMES=[f'secure-{b}-{f}'for b in ['virtual','usbfs','native']for f in ['ipv4','ipv6']]+['agent-native-ipv4','agent-native-ipv6','sigterm']
def load(p):return json.loads(p.read_text())
def sha(b):return hashlib.sha256(b).hexdigest()
def dump(n,j):(HERE/n).write_text(json.dumps(j,indent=2,sort_keys=True)+'\n')
def ready():
 if not(BASE/'result.json').exists():return False,'batch terminal record absent'
 rows=[x for x in load(BASE/'results.json')if x['case']in NAMES]
 if [x['case']for x in rows]!=NAMES:return False,'nine ordered terminal process receipts absent'
 for name in NAMES:
  if not(BASE/name/'summary.json').exists():return False,'summary absent: '+name
  summary=load(BASE/name/'summary.json')
  if name!='sigterm'and not summary.get('completed_utc'):return False,'summary not terminal: '+name
 return True,'all nine original process receipts and summaries present'
def inventory():
 paths=[p for n in NAMES for p in(BASE/n).rglob('*')if p.is_file()]
 paths += [BASE/n for n in ['start.json','result.json','results.json','inputs-before.json','inputs-after.json']]
 return {str(p.relative_to(BASE)):{'bytes':p.stat().st_size,'sha256':sha(p.read_bytes())}for p in sorted(paths)}
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='store_true');args=p.parse_args();ok,reason=ready()
 print(json.dumps({'ready':ok,'reason':reason,'executed':False}))
 if not args.run:return 0
 if not ok:return 2
 if(HERE/'report.json').exists()or(HERE/'execution.json').exists():raise FileExistsError('retain prior reports; use a separately reviewed revision/output path')
 before=inventory();dump('raw-sha-before.json',before)
 argv=[sys.executable,'-B',str(HERE/'review.py'),'--run']
 result=subprocess.run(argv,capture_output=True,text=True)
 (HERE/'execution.stdout').write_text(result.stdout);(HERE/'execution.stderr').write_text(result.stderr)
 after=inventory();dump('raw-sha-after.json',after)
 receipt={'argv':argv,'exit_code':result.returncode,'raw_file_count':len(before),'raw_before_after_equal':before==after,'scope':'Only nine legacy directories plus5 batch provenance files; other14 groups not independently reviewed here.','no_product_or_harness_execution':True}
 dump('execution.json',receipt)
 if(HERE/'report.json').exists():
  report=load(HERE/'report.json');receipt.update(review_result=report['result'],group_predicates=report.get('recorded_group_comparisons'),batch_predicates=len(report.get('batch_checks',[])),guest_exit_zero=report.get('guest_exit_zero'))
 receipt['result']='PASS'if result.returncode==0 and before==after and receipt.get('review_result')=='PASS'else'FAIL'
 receipt.update(report_sha256=sha((HERE/'report.json').read_bytes())if(HERE/'report.json').exists()else None,parser_sha256=sha(Path(__file__).read_bytes()),raw_before_sha256=sha((HERE/'raw-sha-before.json').read_bytes()),raw_after_sha256=sha((HERE/'raw-sha-after.json').read_bytes()))
 dump('handoff.json',receipt);print(json.dumps(receipt));return int(receipt['result']!='PASS')
if __name__=='__main__':raise SystemExit(main())

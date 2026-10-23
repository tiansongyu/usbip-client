#!/usr/bin/env python3
"""Replay frozen independent methods into a fresh output tree; originals stay read-only.
The evidence paths and immutable HEAD remain fixed in the reviewed parsers.
Requires the retained evidence and source-lock paths from this handoff to exist.
"""
import argparse,json,shutil,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent
p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);args=p.parse_args()
args.output.mkdir(parents=True,exist_ok=False);compat=args.output/'compat';compat.mkdir()
shutil.copyfile(HERE/'health_review.py',args.output/'health_review.py')
for n in ['secure.py','agent.py','sigterm.py','review.py','source-lock.json','preparation.json','expected-binaries.json','software-inputs.json']:
 shutil.copyfile(HERE/'compat'/n,compat/n)
commands=[[sys.executable,'-B',str(compat/'review.py'),'--run'],[sys.executable,'-B',str(args.output/'health_review.py')]]
receipts=[]
for argv in commands:
 result=subprocess.run(argv,capture_output=True,text=True);name='compat'if '--run'in argv else'health'
 (args.output/(name+'.stdout')).write_text(result.stdout);(args.output/(name+'.stderr')).write_text(result.stderr)
 receipts.append({'argv':argv,'exit_code':result.returncode})
(args.output/'replay-results.json').write_text(json.dumps(receipts,indent=2)+'\n')
raise SystemExit(0 if all(x['exit_code']==0 for x in receipts)else 1)

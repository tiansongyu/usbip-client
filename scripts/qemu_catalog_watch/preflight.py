#!/usr/bin/env python3
"""Rootless checks of the actual guest point function, including empty jq output."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE=Path(__file__).resolve().parent
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=False)
    text=(HERE/'guest-functions.sh').read_text()
    assert text.count('catalog_point() {\n')==text.count('catalog_wait_code() {\n')==1
    function=text.split('catalog_point() {\n',1)[1].split('catalog_wait_code() {\n',1)[0]
    function='catalog_point() {\n'+function
    assert function.count('$(seq 1 240)')==1
    head={'event':'catalog_begin','schema_version':1,'instance':'fixture','revision':1,'state':'current','device_count':0,'change_count':0,'observation_only':True}
    tail={'event':'catalog_end','schema_version':1,'instance':'fixture','revision':1,'device_count':0,'change_count':0}
    one=[dict(head,device_count=1),{'event':'catalog_device','schema_version':1,'instance':'fixture','revision':1,'catalog':{'device_key':'fixture'}},dict(tail,device_count=1)]
    cases=[('empty',[],0,-1,False),('complete',[head,tail],0,-1,True),('partial',[head],0,-1,False),
        ('mixed',[head,dict(tail,instance='other')],0,-1,False),('bad-count',[dict(head,device_count=1),dict(tail,device_count=1)],1,-1,False),
        ('not-new',[head,tail],0,1,False),('new-complete',[head,tail,dict(head,revision=2),dict(tail,revision=2)],0,1,True),
        ('visible-one',one,1,-1,True),('visible-count-mismatch',one,2,-1,False)]
    results=[]
    for name,rows,count,after,expected in cases:
        directory=output/name;directory.mkdir();source=directory/'source.jsonl';source.write_text(''.join(json.dumps(x)+'\n' for x in rows))
        # Only the fixed test path and retry count differ. The real parsing and
        # acceptance body is executed unchanged, under an ordinary host shell.
        adapted=function.replace('/etc/rustusb/catalog-point.jq',str(HERE/'point.jq')).replace('/tmp/catalog-',str(directory/'catalog-')).replace('$(seq 1 240)','$(seq 1 1)')
        script=directory/'actual-guest-point-adapted.sh';script.write_text('#!/bin/sh\nset -eu\n'+adapted+'\ncatalog_point "$1" check current "$2" "$3"\n')
        argv=['dash',str(script),str(source),str(count),str(after)];run=subprocess.run(argv,capture_output=True)
        (directory/'stdout.txt').write_bytes(run.stdout);(directory/'stderr.txt').write_bytes(run.stderr)
        point=directory/'catalog-check.json';accepted=run.returncode==0
        assert accepted==expected,(name,run.returncode,run.stderr.decode())
        if accepted:
            decoded=json.loads(point.read_text());assert decoded['head']['revision']>after and decoded['head']['device_count']==count
        else:assert not point.exists(),name
        results.append({'case':name,'argv':argv,'actual_exit_code':run.returncode,'accepted':accepted})
    summary={'result':'PASS','scope':'Nine pure fixture checks; actual guest point function with temporary paths and one poll, no product/USB execution','guest_function_sha256':hashlib.sha256((HERE/'guest-functions.sh').read_bytes()).hexdigest(),'point_filter_sha256':hashlib.sha256((HERE/'point.jq').read_bytes()).hexdigest(),'cases':results}
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps({'result':'PASS','cases':len(results),'output':str(output)}))
if __name__=='__main__':main()

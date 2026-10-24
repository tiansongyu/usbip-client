#!/usr/bin/env python3
"""Actual ordinary-user hostname DEVLIST/deadline processes, with no USB operations."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
REQUEST=bytes.fromhex('0111800500000000')
REPLY=struct.pack('!HHII',0x0111,5,0,0)

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def case(binary,out,withhold):
    out.mkdir()
    listeners=[];threads=[];observations=[];errors=[]
    ready=threading.Event();release=threading.Event();stop=threading.Event()
    child=None
    def serve(listener):
        try:
            while not stop.is_set():
                try:connection,remote=listener.accept()
                except socket.timeout:continue
                except OSError:
                    if stop.is_set():return
                    raise
                with connection:
                    connection.settimeout(6);data=b''
                    while len(data)<8:
                        chunk=connection.recv(8-len(data))
                        if not chunk:break
                        data+=chunk
                    if not data:
                        observations.append({'empty_tcp_candidate':True});return
                    if data!=REQUEST:raise RuntimeError('unexpected actual DEVLIST request')
                    observations.append({'request_hex':data.hex(),'peer_loopback':remote[0] in ['127.0.0.1','::1']})
                    ready.set()
                    if not release.wait(6):raise RuntimeError('observer did not release the bounded response gate')
                    if not withhold:connection.sendall(REPLY)
                    if connection.recv(1)!=b'':raise RuntimeError('unexpected bytes after actual DEVLIST request')
                    observations.append({'peer_eof_observed':True});return
        except Exception as error:errors.append(type(error).__name__+': '+str(error));ready.set()
    try:
        ipv6=socket.socket(socket.AF_INET6,socket.SOCK_STREAM);listeners.append(ipv6)
        ipv6.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,1);ipv6.bind(('::1',0));port=ipv6.getsockname()[1]
        ipv4=socket.socket(socket.AF_INET,socket.SOCK_STREAM);listeners.append(ipv4);ipv4.bind(('127.0.0.1',port))
        for listener in listeners:
            listener.listen(4);listener.settimeout(0.1)
            thread=threading.Thread(target=serve,args=(listener,));thread.start();threads.append(thread)
        command=[str(binary),'--json','--timeout','2' if withhold else '3','discover','localhost','--port',str(port)]
        started=time.monotonic();child=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        if not ready.wait(6) or errors:raise RuntimeError('actual request/observer gate failed: '+repr(errors))
        proc=Path('/proc')/str(child.pid)
        status=(proc/'status').read_text();stat=(proc/'stat').read_text();executed=digest(proc/'exe')
        (out/'actual.status').write_text(status);(out/'actual.stat').write_text(stat)
        uid=next(line for line in status.splitlines() if line.startswith('Uid:')).split()[1:]
        if uid!=[str(os.geteuid())]*4 or executed!=digest(binary):raise RuntimeError('actual ordinary-user executable/identity differs')
        release.set();stdout,stderr=child.communicate(timeout=8)
        (out/'stdout').write_bytes(stdout);(out/'stderr').write_bytes(stderr)
        (out/'wait.json').write_text(json.dumps({'command':command,'actual_exit':child.returncode,'actual_executable_sha256':executed})+'\n')
        if withhold:
            if child.returncode!=1 or json.loads(stdout)!={'ok':False,'error':'operation timed out: USB/IP discovery'} or b'failure="timeout"' not in stderr:raise RuntimeError('actual original discovery deadline not reported')
        elif child.returncode!=0 or json.loads(stdout)!=[]:raise RuntimeError('actual hostname DEVLIST output differs')
        result=dict(case='withheld-response' if withhold else 'hostname-catalog',command=command,result='PASS',exit=child.returncode,actual_executable_sha256=executed,actual_uid=int(uid[0]),elapsed_seconds=time.monotonic()-started,scope='Loopback synthetic management reply/timeout only; no USB/kernel or multi-address-set claim.')
    finally:
        release.set();stop.set()
        if child is not None and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=2)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=2)
        for listener in listeners:listener.close()
        for thread in threads:thread.join(timeout=7)
        (out/'cleanup.json').write_text(json.dumps({'actual_child_final_exit':child.returncode if child is not None else None,'server_threads_alive':sum(thread.is_alive() for thread in threads),'stop_requested':stop.is_set()})+'\n')
    if any(thread.is_alive() for thread in threads) or errors:raise RuntimeError('actual server ownership did not finish: '+repr(errors))
    if sum('request_hex' in row for row in observations)!=1 or not any(row.get('peer_eof_observed') for row in observations):raise RuntimeError('one actual request/peer EOF required')
    result['server_observations']=observations;result['all_server_threads_joined']=True
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--binary',type=Path,default=ROOT/'target/release/rustusb');p.add_argument('--evidence',type=Path,required=True);args=p.parse_args()
    if os.geteuid()==0 or not Path('/proc/self/status').exists():raise RuntimeError('ordinary Linux user required')
    binary=args.binary.resolve();before=digest(binary);args.evidence.mkdir(parents=True,exist_ok=False)
    (args.evidence/'method.py').write_bytes(Path(__file__).read_bytes())
    rows=[case(binary,args.evidence/'hostname-catalog',False),case(binary,args.evidence/'withheld-response',True)]
    if digest(binary)!=before:raise RuntimeError('prebuilt executable changed')
    summary=dict(result='PASS',head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),binary_sha256=before,cases=rows,scope='Two actual prebuilt CLI hostname/total-read-deadline cases, separate from Cargo execution and USB/QEMU counts.')
    (args.evidence/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    return 0

if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""Coordinated Native/IPv4 upgrade retaining the original installed USB checks."""
import argparse
import hashlib
import json
from pathlib import Path
import textwrap
HERE=Path(__file__).resolve().parent
ORIGINAL=HERE/'run.py'
def once(source,old,new):
    if source.count(old)!=1:raise RuntimeError('original fixture composition anchor changed')
    return source.replace(old,new,1)
def compose():
    source=ORIGINAL.read_text()
    start="                before=upgraded['before'];after=upgraded['after']['units']\n"
    end="                if role=='client':\n"
    if source.count(start)!=1 or source.count(end)!=1:raise RuntimeError('upgrade proof anchors changed')
    checks="before=upgraded['before'];after=upgraded['after']['units']\n"+textwrap.dedent(source.split(start,1)[1].split(end,1)[0])
    helper='def coordinated_upgrade_owners(upgraded):\n'+textwrap.indent(checks,'    ')+'\n'
    source=once(source,'def one(args):\n',helper+'def one(args):\n')
    source=once(source,'            if cycle in [1,2]:\n','            if cycle == 1:\n')
    old="                    guest('server','server-idle')\n                    guest('client','start-services')\n"
    new="""                    guest('server','server-idle')
                    # Complete the remaining server upgrade before the new client
                    # opens a stream. Do not retry the incompatible mixed case.
                    coordinated=guest('server','upgrade',args.upgrade.name)
                    check_terminal(coordinated['after'])
                    coordinated_upgrade_owners(coordinated)
                    require(coordinated['after']['host']==server_setup['baseline'],'coordinated old server baseline')
                    guest('server','start-services')
                    guest('server','start-jobs','server')
                    guest('server','ready','server')
                    guest('client','start-services')
"""
    source=once(source,old,new)
    old="                guest('client','stop-services');check_terminal(data['client']['stop-services'][-1])\n"
    new=old+"""                stopped_server=guest('server','stop-services')
                check_terminal(stopped_server)
                require(stopped_server['host']==server_setup['baseline'],'coordinated reuse server baseline')
"""
    return once(source,old,new)
def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['prepared','output','base','upgrade']:parser.add_argument('--'+name,type=Path,required=True)
    args=parser.parse_args();args.backend='native';args.family=4
    source=compose();generated=args.output.resolve().with_suffix('.generated.py');generated.write_text(source)
    method={'original_run_sha256':hashlib.sha256(ORIGINAL.read_bytes()).hexdigest(),
        'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'generated_sha256':hashlib.sha256(source.encode()).hexdigest(),
        'scope':'original assertions retained; coordinated server upgrade before client restart; explicit both-end reuse'}
    namespace={'__name__':'coordinated_installed_fixture','__file__':str(ORIGINAL)}
    exec(compile(source,str(generated),'exec'),namespace)
    result=namespace['one'](args)
    assert hashlib.sha256(generated.read_bytes()).hexdigest()==method['generated_sha256']
    (args.output/'generated-run.py').write_text(source)
    (args.output/'composition.json').write_text(json.dumps(method,indent=2)+'\n')
    raise SystemExit(result)
if __name__=='__main__':main()

from pathlib import Path
import shlex, subprocess, re, json, hashlib
path=Path('/home/ubuntu/github/usbip-client/scripts/qemu/guest-secure-init.sh')
source=path.read_text().replace('\\\n',' ')
programs=[]
tokens=shlex.split(source,comments=True)
for start,token in enumerate(tokens):
    if token!='jq':continue
    pos=start+1
    while pos<len(tokens):
        option=tokens[pos]
        if option in ['--arg','--argjson','--slurpfile','--rawfile']:pos+=3
        elif option.startswith('-'):pos+=1
        else:break
    expression=tokens[pos]
    variables=sorted(set(re.findall(r'\$([A-Za-z_][A-Za-z_0-9]*)',expression)))
    args=['jq','-n']
    for variable in variables:args+=['--argjson',variable,'null']
    args+=['try ('+expression+') catch null']
    proc=subprocess.run(args,text=True,capture_output=True)
    if 'compile error' in proc.stderr or 'syntax error' in proc.stderr:raise RuntimeError(f'token {start}: {expression}: {proc.stderr}')
    programs.append({'shell_token_index':start,'filter':expression,'compile_exit':proc.returncode})
result={'scope':'actual jq compiler with null variable fixtures; syntax only, not guest assertions','source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'programs':programs}
Path('/tmp/rustusb-secure-harness-jq-static-20261023.json').write_text(json.dumps(result,indent=2)+'\n')
print('jq filters actually compiled:',len(programs))

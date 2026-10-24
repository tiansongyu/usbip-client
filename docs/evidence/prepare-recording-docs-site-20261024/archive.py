from pathlib import Path
import json, hashlib

repo = Path('/home/ubuntu/github/usbip-client')
def archive(name, trees, singles, listed, scope):
    out = repo / 'docs/evidence' / name
    out.mkdir(exist_ok=False)
    records = []
    seen = set()
    def copy(source, dest, expected=None):
        source = Path(source)
        assert source.is_file() and not source.is_symlink(), source
        b = source.read_bytes()
        digest = hashlib.sha256(b).hexdigest()
        if expected:
            assert len(b) == expected['bytes'] and digest == expected['sha256'], source
        assert dest not in seen, dest
        seen.add(dest)
        p = out / dest
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
        assert p.read_bytes() == b
        records.append(dict(path=dest, original=str(source), bytes=len(b), sha256=digest))
    for root, prefix in trees:
        root = Path(root)
        for p in sorted(root.rglob('*')):
            if p.is_file():
                copy(p, prefix + '/' + str(p.relative_to(root)))
    for source, dest in singles:
        copy(source, dest)
    for handoff, key, prefix in listed:
        for row in json.loads(Path(handoff).read_text())[key]:
            copy(row['path'], prefix + '/' + str(Path(row['path']).relative_to('/tmp')), row)
    copy(__file__, 'archive.py')
    b = (json.dumps(scope, indent=2) + '\n').encode()
    (out/'scope.json').write_bytes(b)
    records.append(dict(path='scope.json', original='generated archive scope', bytes=len(b), sha256=hashlib.sha256(b).hexdigest()))
    (out/'manifest.json').write_text(json.dumps(dict(schema_version=1, files=records), indent=2)+'\n')
    print(name, 'files',len(records)+1,'bytes',sum(r['bytes'] for r in records))

archive('prepare-recording-docs-site-20261024',[
('/tmp/rustusb-prepare-docs-site-20261024','qa'),
],[(str(Path('/tmp')/n),'review/'+n) for n in [
'rustusb-prepare-recording-doc-consistency-review-20261024.json',
'rustusb-prepare-validation-doc-handoff-20261024.json',
'rustusb-prepare-validation-doc-final-handoff-20261024.json',
'update-rustusb-prepare-final-docs-20261024.py',
'preview-rustusb-prepare-site-20261024.py',
]],[],dict(result='PASS_LOCAL',software_reference='dirtyb1 inputs167 mapped8ec/1582/5ff',qemu_reference='clean1582f06 23/46',ci_reference='5ffdc6c 40044605684 each667',
notes=['Seventeen maintained documents396 local/repo-mapped targets and tables pass; external37 URLs counted but not fully crawled.',
'Site145 local static checks, Node syntax and diffcheck pass.',
'Actual CUA projectprefix desktop1280x800/mobile390x844 text667/23/46/18, image load, no horizontal page overflow, keyboardArrowRight/Home/URL and console0 observed.',
'CSS/JS/assets equal priorf095; noJS/clipboard previous checks are not relabeled new executions. Screenshot unavailable, reducedmotion preference not emulated.',
'Preview tab closed, viewport reset, actual owned preview process waited afterCtrlC and gone.',
'Installedglobal readme-site four sources and discovery symlink rechecked, exact source snapshots retained; no reinstall or source change.',
'Independent14-document consistency method excludes new report while its author drafted; rootfinal17-targetcheck covers final newreport.',
'Publication is pending here; later exacthead workflow/livebytes/browser receipts remain separate.']))

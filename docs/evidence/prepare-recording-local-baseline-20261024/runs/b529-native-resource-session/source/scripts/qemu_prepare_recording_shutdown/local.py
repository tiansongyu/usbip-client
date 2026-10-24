"""Local Native StopSession composition; global-stop init/verdict remain distinct."""
from pathlib import Path

OWN = Path(__file__).resolve().parent


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise RuntimeError("local prepare composition anchor changed: " + old[:100])
    return text.replace(old, new)


def section(name):
    text = (OWN / "guest-local-fragments.sh").read_text()
    start, end = f"# BEGIN {name}\n", f"# END {name}\n"
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError("local prepare fragment marker differs")
    return text.split(start, 1)[1].split(end, 1)[0]


def compose(template):
    template = replace_once(template,
        'native-resource:job|native-resource:term|usbfs-resource:job|usbfs-resource:term|usbfs-initializer:job|usbfs-initializer:term)',
        'native-resource:session|native-available:session)')
    predicate = '''if jq -e '(.audit[-1].action=="resource") and (.audit[-1].profile=="pool") and ([.sessions[]|select(.profile=="pool" and .role=="export" and .terminal==false and .attempts[-1].resource_id!=null)]|length)==2' "/tmp/draft-$CANDIDATE.json" > /dev/null; then'''
    template = replace_once(template, predicate, 'if local_candidate "/tmp/draft-$CANDIDATE.json"; then')
    template = replace_once(template, '# Actual link state, not a fixed startup sleep.\n', section('FUNCTIONS') + '# Actual link state, not a fixed startup sleep.\n')
    start = '    host_expect STOP_TARGET\n'
    end = '    echo PREPARE_TARGET_CLOSED\n'
    if template.count(start) != 1 or template.count(end) != 1:
        raise RuntimeError('local server tail anchors changed')
    before, tail = template.split(start, 1)
    _, after = tail.split(end, 1)
    template = before + section('SERVER') + after
    start = '    echo PREPARE_CLIENT_READY\n'
    end = '    host_expect START_REUSE\n'
    if template.count(start) != 1 or template.count(end) != 1:
        raise RuntimeError('local client tail anchors changed')
    before, tail = template.split(start, 1)
    _, after = tail.split(end, 1)
    template = before + section('CLIENT') + end + after
    # The rejected serial history is retained alongside its later real import.
    template = replace_once(template,
        "wait_sessions serial /tmp/client-serial-final.json '.response.sessions|all(.terminal and .cleanup==\"verified\")'",
        "wait_sessions serial /tmp/client-serial-final.json '.response.sessions|length==2 and all(.terminal) and any(.cleanup==\"verified\" and (.attempts|length)==1) and any(.cleanup==\"not_started\" and .attempts==[])'")
    return template

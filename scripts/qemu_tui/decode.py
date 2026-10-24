"""Decode actual Ratatui diff output; do not search a terminal stream as plain text."""
import importlib.metadata
import re


def frames(raw):
    import pyte
    for name, version in [('pyte','0.8.2'),('wcwidth','0.2.13')]:
        if importlib.metadata.version(name)!=version:
            raise RuntimeError('TUI screen decoder requires pinned '+name+' '+version)
    if raw.count(b'\x1b[2J')!=1:
        raise RuntimeError('PTY fixture must have exactly one real resize clear')
    screen=pyte.Screen(120,30)
    stream=pyte.Stream(screen)
    result=[]
    for token in re.split('(\x1b\\[2J|\x1b\\[\\?25l)',raw.decode('utf-8')):
        if token=='\x1b[2J':
            # This fixed-viewport product/driver performs its sole clear on resize.
            screen.resize(lines=18,columns=60)
        stream.feed(token)
        if token=='\x1b[?25l':
            result.append('\n'.join(screen.display))
    return result

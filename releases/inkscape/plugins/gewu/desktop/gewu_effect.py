"""Inkscape executes this effect against its own temporary, unsaved snapshot."""
import hashlib
import json
import os
from pathlib import Path
import sys

from lxml import etree as E


G = '{https://gewu.dev/ns/figure-layout/1}'
SOD = '{http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd}'
INK = '{http://www.inkscape.org/namespaces/inkscape}'


def fingerprint(data):
    root=E.fromstring(data,E.XMLParser(resolve_entities=False,no_network=True,remove_blank_text=False))
    for attr in (SOD+'docname',INK+'version'):
        root.attrib.pop(attr,None)
    view=root.find(SOD+'namedview')
    if view is not None:
        for attr in ('zoom','cx','cy','window-width','window-height','window-x','window-y','window-maximized','current-layer'):
            view.attrib.pop(INK+attr,None)
    return hashlib.sha256(E.tostring(root,method='c14n',with_comments=False)).hexdigest()


def main():
    target=os.environ.get('GEWU_INKSCAPE_SESSION')
    if not target: return
    session=Path(target); request=session/'request.json'; claimed=session/'claimed.json'
    try: request.rename(claimed)
    except FileNotFoundError: return
    cmd=json.loads(claimed.read_text()); key=cmd['id']; response={'id':key,'ok':False}
    snapshot=session/(key+'.svg')
    try:
        native_path=os.environ.get('DOCUMENT_PATH')
        if not native_path or Path(native_path).resolve()!=Path(cmd['path']):
            response.update(code='WRONG_DOCUMENT',message='Active desktop document does not match the bound path')
            return
        source=Path(sys.argv[-1]); raw=source.read_bytes()
        if len(raw)>32*1024*1024: raise ValueError('Native snapshot is too large')
        root=E.fromstring(raw,E.XMLParser(resolve_entities=False,no_network=True))
        selection=[arg.split('=',1)[1] for arg in sys.argv[1:-1] if arg.startswith('--id=')]
        revision=fingerprint(raw)
        response.update(revision=revision,selection=selection,commit=root.get(G+'nativeCommit'))
        if cmd['mode']=='inspect':
            snapshot.write_bytes(raw); response['ok']=True; return
        if revision!=cmd['expected']:
            response.update(code='STALE_REVISION',message='Native document changed; refresh before editing'); return
        candidate=(session/(key+'.candidate')).read_bytes()
        result=E.fromstring(candidate,E.XMLParser(resolve_entities=False,no_network=True))
        if result.get(G+'figure')!=root.get(G+'figure'):
            response.update(code='WRONG_DOCUMENT',message='Candidate belongs to a different figure'); return
        result.set(G+'nativeCommit',key)
        sys.stdout.buffer.write(E.tostring(result,xml_declaration=True,encoding='UTF-8')); sys.stdout.buffer.flush()
        response['ok']=True
    except (OSError,ValueError,E.XMLSyntaxError) as e:
        response.update(code='NATIVE_FAILED',message=str(e)[:350])
    finally:
        tmp=session/(key+'.tmp'); tmp.write_text(json.dumps(response)); tmp.replace(session/(key+'.json'))
        claimed.unlink(missing_ok=True)


if __name__=='__main__': main()

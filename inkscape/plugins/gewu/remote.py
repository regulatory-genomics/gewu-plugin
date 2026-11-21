"""Transfer only the bound checkout's state and edits over an authenticated SSH channel."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile

from jsonschema import Draft202012Validator

from contract import TOOLS
from live import Live
from native import Engine, Failure
import svg


FRAME = 46 * 1024 * 1024
EDIT = Draft202012Validator(next(t['inputSchema'] for t in TOOLS if t['name']=='live_edit'))


class Remote:
    def __init__(self,settings,origin):
        self.origin=origin
        host=settings['host']
        if not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.@-]{0,190}',host):
            raise Failure('INVALID_ARGUMENT','Use a configured OpenSSH host alias or user@host')
        if any(not Path(settings[name]).is_absolute() for name in ('python','script','session')):
            raise Failure('INVALID_ARGUMENT','Remote desktop command and session paths must be absolute')
        options=['-F',settings['sshConfig']] if 'sshConfig' in settings else []
        self.argv=['ssh',*options,'-T','-oBatchMode=yes','-oStrictHostKeyChecking=yes','-oConnectTimeout=5',host,
                   shlex.join([settings['python'],settings['script'],'--session',settings['session']])]

    def request(self,mode,**args):
        try:
            with tempfile.TemporaryFile() as out:
                call=subprocess.run(self.argv,input=json.dumps({'mode':mode,'originHash':self.origin,**args}).encode(),
                                    stdout=out,stderr=subprocess.DEVNULL,timeout=36)
                out.seek(0); data=out.read(FRAME+1)
            if call.returncode or len(data)>FRAME:
                raise ValueError('SSH transport did not return a complete response')
            answer=json.loads(data)
            if not isinstance(answer,dict) or not isinstance(answer.get('ok'),bool): raise ValueError('Invalid reply')
        except (OSError,ValueError,subprocess.TimeoutExpired) as error:
            if mode=='edit': raise Failure('OUTCOME_UNKNOWN','SSH disconnected; observe the native canvas before retrying a write') from error
            raise Failure('BRIDGE_UNAVAILABLE','Could not contact the verified desktop over SSH') from error
        if not answer['ok']:
            raise Failure(answer['code'],answer['message'],answer['code']=='STALE_REVISION')
        return answer['result']

    def capture(self,known=None):
        result=self.request('capture',known=known)
        raw=result.pop('svg',None)
        return result,base64.b64decode(raw,validate=True) if raw is not None else None

    def edit(self,expected,operations,workspace=None,control=None):
        if control and control.cancelled.is_set(): raise Failure('CANCELLED','Stopped before native mutation')
        return self.request('edit',expectedRevision=expected,operations=operations)

    def saved(self):
        return base64.b64decode(self.request('saved')['svg'],validate=True)


def serve(session,req):
    if session.is_symlink() or session.stat().st_uid!=os.getuid() or session.stat().st_mode&0o077:
        raise Failure('WRONG_DOCUMENT','Remote session directory must be private')
    info=json.loads((session/'session.json').read_bytes())
    if info['originHash']!=req.get('originHash'):
        raise Failure('WRONG_DOCUMENT','Remote session was not started from this checkout')
    figure=Path(info['figure'])
    if req.get('mode')=='saved':
        if not (session/'closed.json').exists():
            raise Failure('DOCUMENT_CHECKED_OUT','Save and close remote Inkscape before returning the figure')
        raw=svg.read_file(figure)
        if svg.digest(raw)!=json.loads((session/'closed.json').read_bytes())['sha256']:
            raise Failure('EXTERNAL_MODIFICATION','Remote checkout changed after the desktop was closed')
        return {'svg':base64.b64encode(raw).decode()}
    desktop=Live(session,figure,None)
    if req.get('mode')=='capture':
        state,raw=desktop.capture()
        state['selection']=state['selection'][:100]
        if req.get('known')!=state['revision']: state['svg']=base64.b64encode(raw).decode()
        return state
    if req.get('mode')=='edit':
        args={k:v for k,v in req.items() if k not in ('mode','originHash')}
        if not EDIT.is_valid(args): raise Failure('INVALID_ARGUMENT','Invalid live edit batch')
        desktop.engine=Engine(info['inkscape'])
        answer=desktop.edit(args['expectedRevision'],args['operations'],figure.parent)
        answer['selection']=answer['selection'][:100]
        return answer
    raise Failure('INVALID_ARGUMENT','Unknown desktop request')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',type=Path,required=True)
    session=parser.parse_args().session
    try:
        raw=sys.stdin.buffer.read(256*1024+1)
        if len(raw)>256*1024: raise Failure('INVALID_ARGUMENT','Desktop request too large')
        answer={'ok':True,'result':serve(session,json.loads(raw))}
    except Failure as error:
        answer={'ok':False,'code':error.code,'message':str(error)[:400]}
    except (OSError,ValueError,TypeError,KeyError):
        answer={'ok':False,'code':'BRIDGE_UNAVAILABLE','message':'Remote session or request is invalid'}
    sys.stdout.write(json.dumps(answer,separators=(',',':')))


if __name__=='__main__': main()

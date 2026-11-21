import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import validate


PLUGIN = Path(__file__).resolve().parents[1]
GEWU = Path(os.environ.get('GEWU_ROOT','/data3/litian/projects/GeWu'))


class AdapterTest(unittest.TestCase):
    def test_real_sdk_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            package=root/'package'
            subprocess.run([sys.executable,str(PLUGIN/'package.py'),'--sdk-root',str(GEWU/'sdk/python'),'--out',str(package)],check=True)
            workspace=root/'workspace'; workspace.mkdir(); (workspace/'figure').mkdir()
            binding={'uri':'workspace:///figure','type':'inkscape.document'}
            instance={'instanceId':'test','workspaceId':'ws','generation':1}
            schema=json.loads((GEWU/'gewu-protocol/schema/plugin.v1.schema.json').read_text())
            validate(json.loads((package/'plugin.json').read_text()),schema)
            env={**os.environ,'GEWU_PLUGIN_PYTHON':sys.executable}
            with subprocess.Popen([str(package/'bin/inkscape-gewu')],cwd=workspace,env=env,
                                  stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True) as process:
                i=0
                def send(op,params):
                    nonlocal i
                    i+=1; rid=f'r{i}'
                    req={'id':rid,'op':op,'params':params}
                    validate(req,{**schema,'$ref':'#/$defs/HostFrame'})
                    process.stdin.write(json.dumps(req)+'\n'); process.stdin.flush()
                    while True:
                        answer=json.loads(process.stdout.readline())
                        validate(answer,{**schema,'$ref':'#/$defs/AdapterFrame'})
                        self.assertEqual(answer['id'],rid)
                        if answer['type']!='event': return answer['result']
                def call(tool,args):
                    return send('call',{'tool':tool,'args':args,'context':{**instance,'bindingResource':binding,'callId':f'c{i+1}'}})
                result=send('activate',{'apiVersion':'gewu.plugin/v1',**instance,'workspaceRoot':str(workspace),'bindingResource':binding,
                                        'settings':{'inkscapeExecutable':os.environ['INKSCAPE_BIN']}})
                specs={item['name']:item for item in result['tools']}
                self.assertIn('edit',specs)
                self.assertFalse(call('state',{})['data']['exists'])
                output=call('edit',{'expectedRevision':None,'operations':[{'op':'create_document','width':90,'height':70}]})
                self.assertTrue(output['ok'],output)
                validate(output['data'],specs['edit']['outputSchema'])
                revision=output['data']['revision']
                stale=call('undo',{'expectedRevision':'old'})
                self.assertEqual(stale['error']['code'],'STALE_REVISION')
                preview=call('render',{'revision':revision})
                resolved=send('resolve',preview['resources'][0])
                self.assertEqual(resolved['mimeType'],'image/png')
                self.assertTrue((workspace/resolved['path']).is_file())
                (workspace/'paper').mkdir()
                handoff=call('handoff',{'revision':revision,'consumer':'paper','project':'paper'})
                self.assertTrue(handoff['ok'],handoff)
                data=handoff['data']
                self.assertTrue((workspace/'paper'/data['projectRelativePath']).is_file())
                self.assertTrue((workspace/data['manifestPath']).is_file())
                self.assertEqual(call('handoff_status',{'path':data['manifestPath']})['data']['status'],'current')
                wrote=call('edit',{'expectedRevision':revision,'operations':[{'op':'add_text','text':'A','x':5,'y':8}]})
                self.assertTrue(wrote['ok'])
                self.assertEqual(call('handoff_status',{'path':data['manifestPath']})['data']['status'],'update_available')
                self.assertEqual(send('deactivate',{'reason':'test'}),{'stopped':True})
                self.assertEqual(process.wait(timeout=10),0)

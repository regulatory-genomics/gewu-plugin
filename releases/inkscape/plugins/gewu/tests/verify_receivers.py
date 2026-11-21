"""Build Paper and Manimble deliverables from a real GeWu Inkscape handoff."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event
from types import SimpleNamespace
from uuid import uuid4
from zipfile import ZipFile

from PIL import Image
from pypdf import PdfReader

from adapter import Adapter


def load(path, name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.Adapter()


def call(adapter, tool, args):
    control=SimpleNamespace(cancelled=Event(),progress=lambda _:None,register_process=lambda *_:None,process_exited=lambda *_:None)
    answer=adapter.call(tool,args,{'callId':uuid4().hex},control)
    if not answer['ok']:
        raise RuntimeError(f'{tool}: {answer}')
    return answer


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('out','paper-source','paper-bin','tectonic','manimble-source','manimble-python'):
        p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args(); root=a.out.resolve(); root.mkdir()
    figure=root/'figure'; figure.mkdir()
    chart=root/'panel.svg'; chart.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="#197a70"/></svg>')
    paper_dir=root/'paper'; deck=root/'slides'
    subprocess.run([str(a.paper_bin),'init',str(paper_dir)],check=True)
    shutil.copytree(a.manimble_source/'plugins/gewu/fixtures/deck',deck)
    figure_adapter=Adapter()
    figure_adapter.activate({'workspaceRoot':str(root),'bindingResource':{'uri':'workspace:///figure','type':'inkscape.document'},
                             'settings':{'inkscapeExecutable':os.environ['INKSCAPE_BIN']}})
    try:
        rev=call(figure_adapter,'edit',{'expectedRevision':None,'operations':[{'op':'create_document','width':80,'height':60}]})['data']['revision']
        rev=call(figure_adapter,'edit',{'expectedRevision':rev,'operations':[
            {'op':'import_panel','id':'color','path':'panel.svg','width':42,'x':8,'y':16},
            {'op':'add_text','id':'label','text':'GeWu handoff marker','x':8,'y':12}]})['data']['revision']
        paper=call(figure_adapter,'handoff',{'revision':rev,'consumer':'paper','project':'paper'})['data']
        slides=call(figure_adapter,'handoff',{'revision':rev,'consumer':'manimble','project':'slides'})['data']
    finally: figure_adapter.deactivate({'reason':'test'})

    # Real recipient adapter/engine exports, with only fixture source initialized here.
    (paper_dir/'results.md').write_text(f'# Results\n\n![GeWu delivered figure]({paper["projectRelativePath"]}){{#fig:gewu-layout}}\n')
    manuscript=load(a.paper_source/'plugins/gewu/adapter.py','receiver_paper')
    project={'uri':'workspace:///paper','type':'paper.project'}
    manuscript.activate({'workspaceRoot':str(root),'settings':{'executable':str(a.paper_bin),'tectonic':str(a.tectonic)}})
    try:
        paper_build=call(manuscript,'build',{'project':project,'formats':['pdf','html']})
        outputs=[Path(manuscript.resolve(ref)['path']) for ref in paper_build['resources'] if ref.get('mimeType') in ('application/pdf','text/html')]
        pdf=next(path for path in outputs if path.suffix=='.pdf'); html=next(path for path in outputs if path.suffix=='.html')
        reader=PdfReader(pdf)
        assert any('GeWu delivered figure' in (page.extract_text() or '') for page in reader.pages), 'Figure legend missing from Paper PDF'
        assert 'GeWu delivered figure' in html.read_text(), 'Missing figure in Paper HTML'
    finally:
        if close:=getattr(manuscript,'deactivate',None): close({'reason':'test'})

    manimble=load(a.manimble_source/'plugins/gewu/adapter.py','receiver_manimble')
    manimble.activate({'workspaceRoot':str(root),'bindingResource':{'uri':'workspace:///slides','type':'manimble.deck'},
                      'settings':{'python':str(a.manimble_python),'engineRoot':str(a.manimble_source/'engine')}})
    try:
        source=call(manimble,'source',{'scene':'Intro'})
        ref=call(manimble,'describe',{})['data']['scenes'][0]['source']
        original=source['data']['text']
        target=deck/slides['projectRelativePath']; code=f'        self.add(ImageMobject({str(target)!r}).scale_to_fit_height(3))\n'
        call(manimble,'edit_source',{'scene':'Intro','source':ref,'expectedText':original,'text':original.replace('        self.next_slide()',code+'        self.next_slide()')})
        preview=call(manimble,'render',{'scene':'Intro'})
        image=next(Path(manimble.resolve(ref)['path']) for ref in preview['resources'] if ref['type']=='manimble.preview')
        with Image.open(image) as im: assert (25,122,112) in set(im.convert('RGB').getdata()), 'Figure color not rendered in the slide'
        result=call(manimble,'export',{'format':'pptx'})
        pptx=Path(manimble.resolve(result['resources'][0])['path'])
        with ZipFile(pptx) as zip:
            assert b'<p:pic>' in zip.read('ppt/slides/slide1.xml'), 'Figure not retained as an image in PPTX'
            assert any(n.startswith('ppt/media/') for n in zip.namelist())
    finally: manimble.deactivate({'reason':'test'})
    report={'figureRevision':rev,'paperPdf':str(pdf),'paperHtml':str(html),'manimblePptx':str(pptx),'manimbleRender':str(image)}
    (root/'receivers.json').write_text(json.dumps(report,indent=2)+'\n'); print(json.dumps(report,indent=2))


if __name__=='__main__': main()

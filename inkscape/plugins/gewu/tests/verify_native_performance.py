"""Measure native edit latency with frozen small and 50k-node fixtures."""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import time

from document import Document
from native import Engine
from desktop.session import NativeSession
import svg


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False);report={'platform':platform.platform(),'samples':{}}
    engine=Engine(os.environ['INKSCAPE_BIN']);report['inkscape']=engine.version
    for count,limit in ((6,2),(50000,8)):
        folder=a.out/str(count);folder.mkdir();r=svg.new_page(180,120);g=svg.E.SubElement(r,svg.Q('g'),id='panel')
        for i in range(count):svg.E.SubElement(g,svg.Q('rect'),id=f'n{i}',x=str((i%250)*.5),y=str((i//250)*.4),width='.4',height='.3',fill='#147d92')
        figure=folder/'figure.svg';figure.write_bytes(svg.encode(r));d=Document(folder,figure,engine);native=None
        try:
            native=NativeSession(d,{});samples=[]
            for turn in range(30):
                revision=native.state()['revision'];start=time.perf_counter()
                result=native.edit(revision,[{'op':'move','ids':['panel'],'dx':.1 if turn%2==0 else -.1,'dy':0}],request_id=str(turn))
                elapsed=time.perf_counter()-start
                assert result['revision']!=revision;samples.append(elapsed)
            p95=sorted(samples)[int(.95*(len(samples)-1))]
            report['samples'][str(count)]={'seconds':samples,'p50':statistics.median(samples),'p95':p95,'targetSeconds':limit,'withinTarget':p95<=limit}
            (a.out/'performance.json').write_text(json.dumps(report,indent=2)+'\n')
        finally:
            if native:native.close()
            d.close()
    print(json.dumps(report,indent=2))
    if not all(v['withinTarget'] for v in report['samples'].values()):raise SystemExit('Native edit latency target was missed')


if __name__=='__main__':main()

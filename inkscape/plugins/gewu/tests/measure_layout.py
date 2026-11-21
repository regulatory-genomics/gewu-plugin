"""Generate bounded synthetic fixtures and record actual edit+preview timings."""
import argparse
import json
import os
from pathlib import Path
import platform
import statistics
import tempfile
import time

from document import Document
from native import Engine


def main():
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--out',type=Path,required=True); args=parser.parse_args()
    engine=Engine(os.environ['INKSCAPE_BIN']); results={}
    with tempfile.TemporaryDirectory() as temp:
        for name,n,repeats in (('six_panels',1,20),('dense_panel',50000,3)):
            root=Path(temp)/name; root.mkdir(); chart=root/'chart.svg'
            art=''.join(f'<rect x="{i%200}" y="{i//200}" width="1" height="1"/>' for i in range(n)) if n>1 else '<rect width="200" height="250"/>'
            chart.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="250">{art}</svg>')
            doc=Document(root,root,engine)
            try:
                doc.edit(None,[{'op':'create_document','width':180,'height':120}]); count=6 if n==1 else 1
                doc.edit(doc.revision,[{'op':'import_panel','id':f'panel{i}','path':'chart.svg','width':30,'x':5+(i%3)*45,'y':5+(i//3)*50} for i in range(count)])
                doc.state()
                samples=[]
                for _ in range(repeats):
                    start=time.perf_counter(); expected=doc.revision
                    doc.edit(expected,[{'op':'move','ids':['panel0'],'dx':.1,'dy':.1}]); state=doc.state(); path=doc.preview(doc.revision)
                    assert path.is_file() and state['revision']!=expected and len(state['objects'])==count
                    samples.append(time.perf_counter()-start)
                results[name]={'sourceNodes':n,'seconds':samples,'medianSeconds':statistics.median(samples),'p95Seconds':sorted(samples)[(95*len(samples)+99)//100-1]}
            finally: doc.close()
    args.out.write_text(json.dumps({'engine':engine.version,'host':platform.platform(),'cases':results},indent=2)+'\n')
    for name,result in results.items(): print(f"{name}: {result['p95Seconds']:.3f}s p95 over {len(result['seconds'])} synthetic runs")


if __name__=='__main__': main()

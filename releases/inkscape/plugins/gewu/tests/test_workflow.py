import os
from pathlib import Path
import tempfile
import unittest

from document import Document
from native import Engine, Failure
from pypdf import PdfReader
import workflow


class WorkflowTest(unittest.TestCase):
    def test_source_replacement_preset_and_desktop_return(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); src=home/'plot.svg'; checkout=home/'desktop.svg'
            src.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50"><rect id="rect" width="100" height="50" fill="blue"/><text id="axis" x="20" y="25">Old</text></svg>')
            d=Document(home,home,Engine(os.environ['INKSCAPE_BIN'])); self.addCleanup(d.close)
            def edit(*ops): return d.edit(d.revision,list(ops))['revision']
            edit({'op':'create_document','width':100,'height':85})
            edit({'op':'import_panel','id':'panel','path':'plot.svg','x':15,'y':15,'width':50})
            self.assertEqual(workflow.sources(home,d.state()['objects'])[0]['status'],'unchanged')
            before=d.state()['objects'][0]['bbox']
            src.write_text(src.read_text().replace('blue','green'))
            self.assertEqual(workflow.sources(home,d.state()['objects'])[0]['status'],'changed')
            edit({'op':'replace_panel','id':'panel'}, {'op':'set_inner_text','panel':'panel','id':'axis','text':'New'})
            current=d.state()['objects'][0]['bbox']
            self.assertAlmostEqual(before['x'],current['x'],places=2)
            self.assertAlmostEqual(before['width'],current['width'],places=2)
            edit({'op':'add_text','id':'label','text':'B','x':15,'y':10},
                 {'op':'set_preset','name':'journal','size':5,'color':'#123456'},
                 {'op':'apply_preset','name':'journal','ids':['label']})
            self.assertIn('#123456',d.path.read_text())
            start=d.revision; d.checkout(start,checkout)
            with self.assertRaises(Failure): edit({'op':'set_text','id':'label','text':'blocked'})
            checkout.write_text(checkout.read_text().replace('>B</text>','>C</text>'))
            d.checkin(start,True)
            self.assertIn('>C</text>',d.path.read_text())
            d.history(d.revision,-1)
            self.assertIn('>B</text>',d.path.read_text())

    def test_multipage_native_pdf(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); d=Document(home,home,Engine(os.environ['INKSCAPE_BIN'])); self.addCleanup(d.close)
            first=d.edit(None,[{'op':'create_document','width':50,'height':40}])['revision']
            second=d.edit(first,[{'op':'add_page','width':60,'height':80,'label':'Supplement'}])['revision']
            self.assertEqual([p['label'] for p in d.state()['pages']],['Page 1','Supplement'])
            out=home/'all.pdf'; d.export(second,out,'pdf')
            pdf=PdfReader(out)
            self.assertEqual(len(pdf.pages),2)
            # Inkscape 1.4.4 rounds native PDF page boxes to integral points.
            self.assertAlmostEqual(float(pdf.pages[1].mediabox.width)*25.4/72,60,delta=25.4/72)

import os
from pathlib import Path
import tempfile
import unittest

from lxml import etree as E
from PIL import Image
from pypdf import PdfReader

from document import Document
from native import Engine
import svg


class ExportTest(unittest.TestCase):
    def test_vector_text_page_dimensions_and_raster_dpi(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); d=Document(root,root,Engine(os.environ['INKSCAPE_BIN'])); self.addCleanup(d.close)
            d.edit(None,[{'op':'create_document','width':90,'height':60}])
            chart=root/'chart.svg'; chart.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><path d="M 0,0 L 100,50 L 45,100 Z" fill="#008080"/></svg>')
            updated=d.edit(d.revision,[{'op':'import_panel','id':'vector','path':'chart.svg','x':12,'y':12,'width':20},
                  {'op':'add_text','id':'label','text':'Editable label','x':35,'y':18}])['revision']
            for f in ('svg','pdf','png'): d.export(updated,root/('result.'+f),f,dpi=300)
            editable=E.parse(str(root/'result.svg'))
            self.assertEqual(editable.find(f'.//{svg.Q("text")}').text,'Editable label')
            self.assertTrue(editable.xpath('//*[@id="vector"]//*[local-name()="path"]'))
            with Image.open(root/'result.png') as image:
                for px,mm in zip(image.size,(90,60)): self.assertAlmostEqual(px,mm*300/25.4,delta=1)
            page=PdfReader(root/'result.pdf').pages[0]
            self.assertAlmostEqual(float(page.mediabox.width)*25.4/72,90,delta=25.4/72)
            self.assertIn('Editable label',page.extract_text())
            self.assertFalse(page.images,'Vector fixture must not be a raster image in the PDF')
            d.export(updated,root/'paths.pdf','pdf',text='paths')
            self.assertNotIn('Editable label',PdfReader(root/'paths.pdf').pages[0].extract_text())

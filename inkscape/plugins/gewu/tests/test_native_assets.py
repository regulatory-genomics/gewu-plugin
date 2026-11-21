"""PDF page/font selection and raster/vector import use native output, not screenshots."""
import os
from pathlib import Path
import tempfile
import unittest
from PIL import Image

from native import Engine
from native_document import NativeDocument
import svg


class NativeAssets(unittest.TestCase):
    def test_pdf_import_selects_second_page_and_keeps_text(self):
        engine=Engine(os.environ['INKSCAPE_BIN'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);r=svg.new_page(80,60)
            svg.apply(r,{'op':'add_text','text':'FIRST PAGE','x':10,'y':15},root,None)
            svg.apply(r,{'op':'add_page','width':80,'height':60,'label':'second'},root,None)
            page=svg.pages(r)[1]
            svg.apply(r,{'op':'add_text','text':'SECOND PAGE','x':page['x']+10,'y':page['y']+15},root,None)
            source=root/'two.svg';source.write_bytes(svg.encode(r));engine.export(source,root/'two.pdf','pdf')
            graph=NativeDocument(svg.encode(svg.new_page(180,120)),root,engine)
            graph.apply([{'op':'import_panel','path':'two.pdf','page':2,'text':'preserve','width':40}])
            content=''.join(graph.root.itertext())
            self.assertIn('SECOND PAGE',content)
            self.assertNotIn('FIRST PAGE',content)
            self.assertTrue(graph.root.findall('.//'+svg.Q('text')))

    def test_jpeg_import_is_an_embedded_image_and_svg_text_is_editable(self):
        engine=Engine(os.environ['INKSCAPE_BIN'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);Image.new('RGB',(100,60),'red').save(root/'panel.jpg')
            (root/'plot.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><text id="axis" x="5" y="20">Editable label</text></svg>')
            graph=NativeDocument(svg.encode(svg.new_page(180,120)),root,engine)
            graph.apply([{'op':'import_panel','path':'panel.jpg','width':40},{'op':'import_panel','path':'plot.svg','width':40}])
            self.assertTrue(graph.root.findall('.//'+svg.Q('image')))
            text=next(n for n in graph.root.iter(svg.Q('text')) if n.text=='Editable label')
            graph.apply([{'op':'set_text','id':text.get('id'),'text':'Edited axis'}])
            self.assertIn(b'Edited axis',graph.encode())

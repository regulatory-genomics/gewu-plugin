import os
from pathlib import Path
import tempfile
import unittest

from lxml import etree as ET
from PIL import Image

from document import Document
from native import Engine, Failure


class DocumentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.engine = Engine(os.environ['INKSCAPE_BIN'])
        self.plot = self.root / 'plot.svg'
        self.plot.write_text('''<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20" viewBox="0 0 40 20">
          <defs><clipPath id="c"><rect width="36" height="18"/></clipPath></defs>
          <style>.item { fill: #00aa00 }</style><g clip-path="url(#c)"><rect id="rect" class="item" width="40" height="20"/></g></svg>''')
        self.doc = Document(self.root, self.root, self.engine)
        self.addCleanup(self.doc.close)

    def apply(self, revision, *ops):
        return self.doc.edit(revision, list(ops))['revision']

    def create(self):
        return self.apply(None, {'op':'create_document', 'width':100, 'height':80})

    def test_editable_vector_import_isolated_and_rendered(self):
        initial = self.plot.read_bytes()
        start = self.create()
        revision = self.apply(start,
            {'op':'import_panel','path':'plot.svg','x':10,'y':15,'width':20},
            {'op':'import_panel','path':'plot.svg','x':45,'y':15,'width':20})
        doc = ET.parse(str(self.doc.path))
        ids = doc.xpath('//@id')
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(len(doc.xpath('//*[local-name()="clipPath"]')),2)
        self.assertEqual(len(doc.xpath('//*[contains(@style,"#00aa00")]')),2)
        self.assertEqual(self.plot.read_bytes(),initial)
        state = self.doc.state()
        self.assertEqual(state['revision'],revision)
        self.assertEqual(len(state['objects']),2)
        self.assertAlmostEqual(state['objects'][0]['bbox']['width'],18,delta=.04)
        with Image.open(self.doc.preview(revision)) as image:
            self.assertEqual(image.convert('RGBA').getpixel((45,65)),(0,170,0,255))

    def test_atomic_batch_conflict_and_shared_history(self):
        a = self.create()
        b = self.apply(a,{'op':'add_text','id':'label-a','text':'A','x':10,'y':10})
        with self.assertRaisesRegex(Failure,'refresh'):
            self.apply(a,{'op':'set_text','id':'label-a','text':'stale'})
        with self.assertRaises(Failure):
            self.apply(b,{'op':'set_text','id':'label-a','text':'bad'}, {'op':'delete','ids':['missing']})
        self.assertEqual(self.doc.state()['revision'],b)
        c = self.doc.history(b,-1)['revision']
        self.assertNotEqual(c,a)
        self.assertFalse(self.doc.state()['objects'])
        self.doc.history(c,1)
        self.assertEqual(self.doc.state()['objects'][0]['text'],'A')

    def test_layout_and_export(self):
        a = self.create()
        b = self.apply(a, {'op':'import_panel','id':'one','path':'plot.svg','x':4,'y':5,'width':20},
                       {'op':'import_panel','id':'two','path':'plot.svg','x':50,'y':30,'width':20},
                       {'op':'align','ids':['one','two'],'edge':'top','reference':'one'},
                       {'op':'distribute','ids':['one','two'],'axis':'x','gap':3})
        by_id = {x['id']:x['bbox'] for x in self.doc.state()['objects']}
        self.assertAlmostEqual(by_id['one']['y'],by_id['two']['y'],places=2)
        self.assertAlmostEqual(by_id['two']['x']-by_id['one']['x']-by_id['one']['width'],3,places=2)
        output = self.root / 'figure.pdf'
        self.doc.export(b,output,'pdf')
        self.assertTrue(output.read_bytes().startswith(b'%PDF-'))

    def test_external_conflict_is_not_overwritten(self):
        a = self.create()
        self.doc.path.write_bytes(self.doc.path.read_bytes().replace(b'100mm',b'105mm'))
        with self.assertRaises(Failure) as failure:
            self.apply(a, {'op':'add_text','text':'A','x':10,'y':10})
        self.assertEqual(failure.exception.code,'EXTERNAL_MODIFICATION')
        state = self.doc.state()
        self.doc.reload(a,state['observedHash'])
        self.assertEqual(self.doc.state()['page']['width'],105)


if __name__ == '__main__':
    unittest.main()

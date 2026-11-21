"""Nested objects stay editable without flattening native structure or references."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
import os

from lxml import etree as E
from native import Failure


class NativeDocumentTest(unittest.TestCase):
    def graph(self):
        self.assertIsNotNone(importlib.util.find_spec('native_document'),'Full native document indexing is missing')
        from native_document import NativeDocument
        return NativeDocument((Path(__file__).parent/'fixtures/native-rich.svg').read_bytes(),Path.cwd())

    def test_nested_inspection_is_readonly_and_world_move_preserves_parent(self):
        g=self.graph(); before=g.encode()
        found=g.inspect(ids=['curve-a','axis-unit'])
        self.assertEqual([n['parent'] for n in found['objects']],['nested','axis'])
        self.assertEqual(found['objects'][1]['text'],'(s)')
        self.assertEqual(g.encode(),before)
        g.apply([{'op':'move','ids':['curve-a'],'dx':2,'dy':3}])
        node=g.root.xpath('//*[@id="curve-a"]')[0]
        self.assertEqual(node.getparent().get('id'),'nested')
        self.assertEqual(node.get('transform'),'matrix(1,0,0,1,2,3)')
        self.assertEqual(g.root.xpath('string(//*[@id="axis-unit"])'),'(s)')

    def test_one_invalid_operation_leaves_the_entire_candidate_unchanged(self):
        g=self.graph(); before=g.encode()
        with self.assertRaises(Failure):
            g.apply([{'op':'set_text','id':'axis-unit','text':'ms'}, {'op':'move','ids':['missing'],'dx':1,'dy':1}])
        self.assertEqual(g.encode(),before)

    def test_path_node_style_clip_and_lpe_refusal(self):
        g=self.graph()
        g.apply([{'op':'set_style','ids':['curve-a','curve-b'],'style':{'stroke':'#0000ff','stroke-width':'0.5mm'}},
                 {'op':'set_path_node','id':'curve-a','index':1,'x':42,'y':21},
                 {'op':'set_clip','ids':['curve-b'],'source':'clip'}])
        self.assertIn('#0000ff',g.root.xpath('//*[@id="curve-a"]')[0].get('style'))
        self.assertEqual(g.root.xpath('//*[@id="curve-b"]')[0].get('clip-path'),'url(#clip)')
        self.assertIn('42',g.root.xpath('//*[@id="curve-a"]')[0].get('d'))
        with self.assertRaises(Failure) as lpe:
            g.apply([{'op':'set_path_node','id':'live-path','index':1,'x':2,'y':3}])
        self.assertEqual(lpe.exception.code,'UNSUPPORTED_OBJECT')

    def test_opaque_references_do_not_require_assigning_ids_during_inspection(self):
        g=self.graph(); raw=g.encode()
        page=g.inspect(limit=2)
        self.assertIsNotNone(page['nextOffset'])
        self.assertGreater(page['total'],2)
        self.assertEqual(g.encode(),raw)
        self.assertNotEqual(g.inspect(parent='plots')['total'],g.inspect()['total'])

    def test_created_shape_geometry_is_in_physical_mm(self):
        from native import Engine
        g=self.graph();g.engine=Engine(os.environ['INKSCAPE_BIN'])
        g.apply([{'op':'create_rect','id':'physical','x':10,'y':15,'width':20,'height':12}])
        b=g.measure()['physical']
        for key,want in {'x':10,'y':15,'width':20,'height':12}.items():self.assertAlmostEqual(b[key],want,places=4)

    def test_linked_image_is_rebased_without_reading_outside_workspace(self):
        g=self.graph()
        self.assertTrue(hasattr(type(g),'relocate'),'Working copies must rebase relative asset paths')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'fig').mkdir();(root/'cache').mkdir();(root/'fig/panel.png').write_bytes(b'asset')
            raw=b'<svg xmlns="http://www.w3.org/2000/svg" width="10mm" height="10mm"><image href="panel.png" width="10" height="10"/></svg>'
            rebased=type(g).relocate(raw,root/'fig',root/'cache',root)
            self.assertIn(b'../fig/panel.png',rebased)
            restored=type(g).relocate(rebased,root/'cache',root/'fig',root)
            self.assertIn(b'href="panel.png"',restored)
            bad=raw.replace(b'panel.png',b'../../outside.png')
            with self.assertRaises(Failure):type(g).relocate(bad,root/'fig',root/'cache',root)

    def test_native_transform_group_lock_and_layer_actions_keep_world_geometry(self):
        from native import Engine
        from native_document import NativeDocument
        import svg
        g=NativeDocument(svg.encode(svg.new_page(180,120)),Path.cwd(),Engine(os.environ['INKSCAPE_BIN']))
        g.apply([{'op':'create_layer','id':'layer','label':'Plots'},
                 {'op':'create_rect','id':'a','parent':'layer','x':10,'y':10,'width':20,'height':10},
                 {'op':'create_rect','id':'b','parent':'layer','x':50,'y':40,'width':10,'height':20},
                 {'op':'rotate','ids':['a'],'angle':90}])
        b=g.measure()['a']
        for key,want in {'x':15,'y':5,'width':10,'height':20}.items():self.assertAlmostEqual(b[key],want,places=3)
        g.apply([{'op':'resize','ids':['a'],'width':20}, {'op':'flip','ids':['b'],'axis':'x'},
                 {'op':'align','ids':['a'],'edge':'left','reference':'b'}, {'op':'distribute','ids':['a','b'],'axis':'x','gap':4},
                 {'op':'equalize','ids':['b'],'dimension':'height','reference':'a'}, {'op':'group','ids':['a','b'],'groupId':'together'},
                 {'op':'set_locked','ids':['together'],'locked':True}])
        with self.assertRaises(Failure):g.apply([{'op':'move','ids':['a'],'dx':1,'dy':1}])
        g.apply([{'op':'set_locked','ids':['together'],'locked':False},{'op':'ungroup','ids':['together']},
                 {'op':'create_layer','id':'other','label':'Other'},{'op':'move_to_layer','ids':['b'],'layer':'other'},
                 {'op':'rename_layer','id':'other','label':'Final'},{'op':'set_visible','ids':['a'],'visible':False}])
        self.assertEqual(g.pick('b').getparent().get('id'),'other')
        self.assertAlmostEqual(g.measure()['b']['height'],40,places=3)
        g.apply([{'op':'set_visible','ids':['a'],'visible':True},{'op':'reorder','ids':['a'],'position':'front'},{'op':'delete','ids':['a']}])
        with self.assertRaises(Failure):g.pick('a')

    def test_native_boolean_uses_one_candidate_without_destroying_defs(self):
        from native import Engine
        g=self.graph();g.engine=Engine(os.environ['INKSCAPE_BIN'])
        g.apply([{'op':'create_rect','id':'a','x':10,'y':10,'width':20,'height':10},
                 {'op':'create_rect','id':'b','x':20,'y':10,'width':20,'height':10},
                 {'op':'boolean','ids':['a','b'],'operation':'union'}])
        self.assertTrue(g.root.xpath('//*[@id="gradient"]'))
        matches=g.root.xpath('//*[@id="a" or @id="b"]')
        self.assertEqual(len(matches),1)
        self.assertEqual(E.QName(matches[0]).localname,'path')
        self.assertAlmostEqual(g.measure()[matches[0].get('id')]['width'],30,places=3)

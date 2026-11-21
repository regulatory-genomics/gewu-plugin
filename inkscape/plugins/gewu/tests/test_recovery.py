import os
from pathlib import Path
import tempfile
import unittest

from document import Document, atomic, write_json
from native import Engine, Failure
import svg


class RecoveryTest(unittest.TestCase):
    def test_durable_journal_settles_once_after_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); engine=Engine(os.environ['INKSCAPE_BIN'])
            for wrote in (False,True):
                path=root/f'{wrote}.svg'; d=Document(root,path,engine)
                original=d.edit(None,[{'op':'create_document','width':40,'height':50}])['revision']
                raw=d.path.read_bytes().replace(b'40mm',b'42mm'); before=d.record['hash']; after=d.store(raw)
                with self.assertRaises(Failure) as busy: Document(root,path,engine)
                self.assertEqual(busy.exception.code,'DOCUMENT_BUSY')
                history={**d.record,'sequence':2,'hash':after,'cursor':2,
                         'entries':[*d.record['entries'],{'before':before,'after':after,'label':'interrupted'}]}
                write_json(d.pending,{'before':before,'after':history,'call':'cut'})
                if wrote: atomic(d.path,raw)
                d.close()
                d=Document(root,path,engine)
                try:
                    self.assertFalse(d.pending.exists())
                    self.assertEqual(d.record['hash'],after if wrote else before)
                    self.assertEqual(d.state()['page']['width'],42 if wrote else 40)
                    if wrote:
                        d.history(d.revision,-1)
                        self.assertEqual(d.state()['page']['width'],40)
                    else: self.assertEqual(d.revision,original)
                finally: d.close()

    def test_snapshot_preserves_stable_ids_when_undo_appends_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); d=Document(root,root,Engine(os.environ['INKSCAPE_BIN']))
            try:
                first=d.edit(None,[{'op':'create_document','width':80,'height':90}])['revision']
                data=d.path.read_bytes()
                second=d.edit(first,[{'op':'add_text','id':'label','text':'A','x':4,'y':6}])['revision']
                third=d.history(second,-1)['revision']
                self.assertNotEqual(first,third)
                self.assertEqual(svg.digest(data),d.state()['observedHash'])
            finally: d.close()

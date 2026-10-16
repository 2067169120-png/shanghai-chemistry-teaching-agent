import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('textbook_tools', HERE / 'textbook_tools.py')
tools = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tools)

class TextbookResourceTests(unittest.TestCase):
    def test_manifest(self):
        books = tools.read_json('manifest.json')['books']
        self.assertEqual({b['id'] for b in books}, {'bx1','bx2','xb1','xb2','xb3'})
        self.assertEqual(sum(b['pdf_pages'] for b in books), 648)
        for b in books:
            self.assertEqual(len(b['sha256']), 64)
            self.assertEqual(b['repository_pdf_status'], 'pending_upload')

    def test_sections_and_page_ranges(self):
        books = tools.originals(HERE.parents[2])
        sections = tools.read_json('sections.json')
        self.assertEqual(len(sections), 60)
        self.assertEqual(len({s['id'] for s in sections}), 60)
        for s in sections:
            b = books[s['book_id']]
            self.assertGreaterEqual(s['pdf_start'], 1)
            self.assertLessEqual(s['pdf_end'], b['pdf_pages'])
            self.assertLessEqual(s['pdf_start'], s['pdf_end'])
            self.assertEqual(s['printed_start'] + b['printed_page_offset'], s['pdf_start'])

    def test_excerpt_coordinates(self):
        clips = tools.read_json('excerpts.json')
        books = tools.originals(HERE.parents[2])
        self.assertEqual(len(clips), 18)
        self.assertEqual(len({c['id'] for c in clips}), 18)
        for c in clips:
            b = books[c['book_id']]
            self.assertEqual(c['source_sha256'], b['sha256'])
            self.assertEqual(c['printed_page'] + b['printed_page_offset'], c['pdf_page'])
            x0,y0,x1,y1 = c['bbox_normalized']
            self.assertTrue(0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1)

    def test_search_precipitation(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output): tools.search('溶度积')
        found = json.loads(output.getvalue())
        self.assertIn('xb1-3.4', [x['id'] for x in found['sections']])
        self.assertIn('q37-ksp', [x['id'] for x in found['excerpts']])

    def test_search_empty_rejected(self):
        with self.assertRaises(ValueError): tools.search(' ')

    def test_empty_root_is_not_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output): code = tools.verify(Path(tmp))
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(output.getvalue())['all_five_verified'])

    def test_original_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'fake.pdf').write_bytes(b'not a textbook')
            with self.assertRaises(ValueError):
                tools.checked_path(root, {'path':'fake.pdf','bytes':14,'sha256':'0'*64})

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError): tools.inside(Path(tmp), '../escape.pdf')

    def test_missing_source_is_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'repo'; source=Path(tmp)/'source'; source.mkdir()
            with self.assertRaises(FileNotFoundError): tools.install(root, source)
            self.assertFalse(root.exists())

    def test_render_dpi_bounds(self):
        with self.assertRaises(ValueError):
            tools.render(HERE.parents[2], 'xb1', 82, Path('unused.png'), 10000)

    def test_render_requires_new_output(self):
        root = HERE.parents[2]
        first = tools.originals(root)['xb1']
        if not (root/first['path']).is_file(): self.skipTest('Local original not installed')
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)/'exists.png';out.write_bytes(b'existing')
            with self.assertRaises(FileExistsError): tools.render(root,'xb1',82,out,120)
            self.assertEqual(out.read_bytes(), b'existing')

    def test_all_local_originals(self):
        root = HERE.parents[2]
        if not all((root/b['path']).is_file() for b in tools.originals(root).values()):
            self.skipTest('Local originals not installed')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):code=tools.verify(root)
        self.assertEqual(code, 0)

if __name__ == '__main__': unittest.main()

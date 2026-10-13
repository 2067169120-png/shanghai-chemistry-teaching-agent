"""Same real PDF pixels and preview bindings, without a PIL native-buffer view."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from io import BytesIO
import importlib.util
from pathlib import Path
from threading import Lock, get_ident
from types import SimpleNamespace
import math
import io

import pytest
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, FloatObject, DecodedStreamObject
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import QCoreApplication, QTimer
from PySide6.QtGui import QPixmap

from test_textbook_bookshelf import colored_pdf
from test_section_material_sources import empty_registered, empty_supplement
from test_textbook_section_reader import section_fixture
from test_textbook_asset_catalog import asset_fixture
from test_textbook_bookshelf import shelf_fixture
from test_textbook_supplements import supplement_fixture
from test_section_page_crops import request as crop_request

from integrations.deeptutor_shchem_v1 import desktop_textbook_preparation_crops as after

def _before_render_page(data, page_number):
    """Frozen pre-change renderer, retained only as a pixel/PNG reference."""
    import pypdfium2 as pdfium
    from integrations.deeptutor_shchem_v1.desktop_local_pagination import _PDF_LOCK
    with _PDF_LOCK, pdfium.PdfDocument(data) as document:
        page = document[page_number - 1]
        try:
            width, height = page.get_size()
            scale = min(after.PAGE_DPI / 72, math.sqrt(after.MAX_PAGE_PIXELS / (width * height)),
                        after.MAX_PAGE_EDGE / max(width, height))
            while math.ceil(width * scale) * math.ceil(height * scale) > after.MAX_PAGE_PIXELS: scale *= .999
            bitmap = page.render(scale=scale)
            try:
                original = bitmap.to_pil()
                try:
                    image = original.convert('RGB')
                    try:
                        target = io.BytesIO(); image.save(target, format='PNG')
                        return {'page_data': target.getvalue(), 'width': image.width, 'height': image.height,
                                'scale': scale, 'effective_dpi': round(scale * 72, 2)}
                    finally: image.close()
                finally: original.close()
            finally: bitmap.close()
        finally: page.close()


before = SimpleNamespace(_render_page=_before_render_page)


def transparent_pdf():
    writer, stream = PdfWriter(), BytesIO()
    page = writer.add_blank_page(600, 800)
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/ExtGState'): DictionaryObject({
        NameObject('/GS'): DictionaryObject({NameObject('/Type'): NameObject('/ExtGState'),
            NameObject('/ca'): FloatObject(0.35), NameObject('/CA'): FloatObject(0.35)})})})
    content = DecodedStreamObject()
    content.set_data(b'q 0 0 1 rg 40 40 500 600 re f Q q /GS gs 1 0 0 rg 160 180 400 450 re f Q')
    page[NameObject('/Contents')] = writer._add_object(content)
    writer.write(stream)
    return stream.getvalue()


@pytest.mark.parametrize('data,page', [(colored_pdf(3), 1), (colored_pdf(3), 2), (transparent_pdf(), 1)])
def test_complete_before_after_rgb_png_recipe_is_exact(data, page):
    old, new = before._render_page(data, page), after._render_page(data, page)
    assert old == new
    with Image.open(BytesIO(old['page_data'])) as left, Image.open(BytesIO(new['page_data'])) as right:
        assert left.mode == right.mode == 'RGB' and left.size == right.size
        assert left.tobytes() == right.tobytes()


def test_foreign_bitmap_normal_finally_closes_handle_and_pil_never_gets_native_view(monkeypatch):
    import pypdfium2 as pdfium
    calls, native = [], pdfium.PdfBitmap.close
    def close(bitmap, *args, **kwargs):
        result = native(bitmap, *args, **kwargs)
        calls.append((result, bitmap.raw is None))
        return result
    monkeypatch.setattr(pdfium.PdfBitmap, 'close', close)
    monkeypatch.setattr(pdfium.PdfBitmap, 'to_pil', lambda _self: pytest.fail('No native-buffer PIL proxy'))
    result = after._render_page(colored_pdf(2), 2)
    assert result['page_data'].startswith(b'\x89PNG') and calls == [(True, True)]


def test_real_serial_and_worker_render_jobs_keep_exact_pixels_and_close_each_native_handle(monkeypatch):
    import pypdfium2 as pdfium
    data = colored_pdf(3)
    expected = [after._render_page(data, page) for page in (1, 2, 3)]
    closed, lock, native = [], Lock(), pdfium.PdfBitmap.close
    def close(bitmap, *args, **kwargs):
        result = native(bitmap, *args, **kwargs)
        with lock: closed.append((get_ident(), result, bitmap.raw is None))
        return result
    monkeypatch.setattr(pdfium.PdfBitmap, 'close', close)
    # No GUI object is created or accessed by these actual worker jobs.
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(after._render_page, data, page) for page in (1, 2, 3)]
        results = [future.result() for future in futures]
    assert results == expected and len(closed) == 3
    assert all(success and dead for _thread, success, dead in closed)


@pytest.mark.parametrize('fixture', ['empty_registered', 'empty_supplement'])
def test_real_qt_page_then_preview_callback_keeps_png_binding(request, fixture, monkeypatch):
    f = request.getfixturevalue(fixture)
    application = QApplication.instance() or QApplication([])
    from integrations.deeptutor_shchem_v1.desktop_section_material_sources import section_textbook_options
    row = next(row for row in section_textbook_options(f.facade, f.context)['rows'] if row['origin'] == 'section_textbook_page')
    page = f.facade.preparation_textbook_crop_page(row['selection'])
    old_raster = before._render_page(f.book.read_bytes(), page['pdf_page'])
    assert old_raster['page_data'] == page['page_data']
    label = QLabel(); pixmap = QPixmap()
    assert pixmap.loadFromData(page['page_data']); label.setPixmap(pixmap); label.show()
    results, failures = [], []
    bounds = {'left': 3, 'top': 3, 'right': page['width'] - 3, 'bottom': page['height'] - 3}
    def preview():
        try:
            results.append(f.facade.preparation_textbook_crop_preview(row['selection'], page['page_revision'],
                bounds, row['caption'], '保留完整可见条件'))
        except BaseException as exc: failures.append(exc)
    QTimer.singleShot(0, preview)
    for _ in range(5): application.processEvents()
    label.close(); label.deleteLater(); QCoreApplication.sendPostedEvents()
    assert not failures and len(results) == 1
    with Image.open(BytesIO(page['page_data'])) as image, image.crop(tuple(bounds.values())) as expected:
        with Image.open(BytesIO(results[0]['image_bytes'])) as actual:
            assert actual.mode == expected.mode and actual.size == expected.size and actual.tobytes() == expected.tobytes()
    # Confirm all original native provenance fields remain in the new preview.
    assert results[0]['provenance']['page_revision'] == page['page_revision']

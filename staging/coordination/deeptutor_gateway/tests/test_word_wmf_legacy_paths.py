from __future__ import annotations

import ctypes
import hashlib
import io
import struct

import pytest
from PIL import Image, ImageDraw
from test_word_wmf_fonts import (
    _create_font, _escape, _font, _record, _select, _textout, _wmf_records,
)

from integrations.deeptutor_shchem_v1 import desktop_word_metafile_preview as preview
from integrations.deeptutor_shchem_v1 import desktop_word_wmf_fonts as fonts


# Synthetic primitives exercise the Win16 structure, not a copied user file.
_PATH_INFO = struct.pack("<hBBHhhIHIHI", 0, 1, 1, 5, 0, 0, 0, 1, 0, 0, 0)
_POINTS = ((10, 10), (40, 10), (40, 40), (10, 10))


def _polygon(points=_POINTS):
    return _record(0x0324, struct.pack("<H", len(points)) +
                   b"".join(struct.pack("<hh", *point) for point in points))


def _clip(mode):
    return _escape(struct.pack("<h", mode), escape=4097)


def _begin():
    return _escape(escape=4096)


def _end(payload=_PATH_INFO):
    return _escape(payload, escape=4098)


def _ignore(value):
    return _escape(struct.pack("<H", value), escape=38)


def _path(*middle):
    return (_clip(0), _begin(), _polygon(), _end(), *middle, _clip(2), _clip(1))


def _dib():
    return (struct.pack("<I8h", 0x00660046, 5, 4, 0, 0, 9, 7, 35, 23) +
            struct.pack("<IiiHHIIiiII", 40, 4, 5, 1, 1, 0, 0, 0, 0, 0, 0) +
            bytes.fromhex("00000000ffffff00") + bytes(20))


@pytest.fixture(autouse=True)
def no_cache_across_cases():
    with preview._LOCK:
        preview._CACHE.clear()
        preview._cache_bytes = 0
    yield
    with preview._LOCK:
        preview._CACHE.clear()
        preview._cache_bytes = 0


def _reject_before_native(monkeypatch, source, *, code="metafile_wmf_text_unverified"):
    def native(*args):
        pytest.fail("malformed/unverified WMF reached native code")

    monkeypatch.setattr(preview, "_gdiplus_rasterize", native)
    monkeypatch.setattr(preview, "_native_rasterize", native)
    with pytest.raises(preview.WordMetafilePreviewError) as exc:
        preview.render_word_metafile(source, mime_type="image/wmf")
    assert exc.value.code == code
    assert not preview._CACHE


def test_closed_pending_path_and_separate_clip_save_preserve_explicit_font():
    # A drawing/BOOL pair between END and INCLUSIVE matches the source's
    # completed-path lifecycle; SaveDC is not a replacement for CLIP_SAVE.
    source = _wmf_records(
        _create_font(b"Arial", charset=0), _select(0), _textout(b"Fe"),
        _record(0x001E), *_path(_ignore(1), _record(0x0B41, _dib()), _ignore(0)),
        _record(0x0127, struct.pack("<h", -1)), _textout(b"3+"),
    )
    assert fonts.wmf_font_runs(source) == (
        (_font(b"Arial", charset=0), b"Fe"), (_font(b"Arial", charset=0), b"3+"),
    )


def test_recognized_controls_pass_original_bytes_and_verified_fonts_to_gdiplus(monkeypatch):
    source = _wmf_records(*_path(), _record(0x0B41, _dib()))
    calls = []

    def native(data, size):
        calls.append((data, size))
        image = Image.new("RGB", size, "white")
        ImageDraw.Draw(image).line((0, 0, size[0] - 1, size[1] - 1), fill="black")
        return image

    monkeypatch.setattr(preview, "_gdiplus_rasterize", native)
    monkeypatch.setattr(preview, "_native_rasterize", lambda *args: pytest.fail("fallback"))
    result = preview.render_word_metafile(source, mime_type="image/wmf", display_size=(100, 50))
    assert calls == [(source, (100, 50))]
    assert result["original_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["preview_sha256"] == hashlib.sha256(result["bytes"]).hexdigest()
    assert result["renderer_revision"] == preview.RENDERER_REVISION
    assert fonts.wmf_font_runs(source) == ()


@pytest.mark.parametrize("records", [
    (_escape(escape=9),), (_escape(b"postscript", escape=37),),
    (_escape(escape=38),), (_ignore(2),), (_ignore(0),), (_ignore(1),),
    (_ignore(1), _ignore(1), _ignore(0)),
    (_clip(0),), (_clip(1),), (_clip(2),), (_clip(3),),
    (_escape(struct.pack("<i", 0), escape=4097),),
    (_begin(),), (_clip(0), _escape(b"xx", escape=4096)),
    (_clip(0), _begin(), _polygon()), (_clip(0), _end()),
    (_clip(0), _begin(), _end()), (_clip(0), _begin(), _begin()),
    (_clip(0), _begin(), _polygon(), _polygon(), _end()),
    (_clip(0), _begin(), _polygon(_POINTS[:-1]), _end()),
    (_clip(0), _begin(), _polygon(((1, 1), (2, 2), (3, 3), (1, 1))), _end()),
    (_clip(0), _begin(), _record(0x0324, struct.pack("<H", 4) + bytes(4)), _end()),
    (_clip(0), _begin(), _textout(b"hidden"), _end()),
    (_clip(0), _begin(), _ignore(1), _polygon(), _ignore(0), _end()),
    (_clip(0), _begin(), _polygon(), _end(), _begin()),
    (_clip(0), _begin(), _polygon(), _end(), _clip(1)),
    (_clip(0), _begin(), _polygon(), _end(), _clip(2)),
    (_clip(0), _begin(), _polygon(), _end(), _clip(2), _clip(2), _clip(1)),
    (*_path(), _clip(1)), (_clip(0), _begin(), _polygon(), _end(b"")),
    (_ignore(1), _record(0x001E), _ignore(0)),
    (_clip(0), _begin(), _polygon(), _end(), _record(0x001E), _clip(2), _clip(1)),
    (_clip(0), _begin(), _record(0x0B41, _dib()), _end()),
])
def test_unknown_malformed_unbalanced_or_semantically_unverified_controls_stop_native(monkeypatch, records):
    code = "metafile_unsafe_record" if records == (_escape(escape=9),) else "metafile_wmf_text_unverified"
    _reject_before_native(monkeypatch, _wmf_records(*records), code=code)


@pytest.mark.parametrize("offset, fmt, value", [
    (0, "h", 1), (2, "B", 2), (3, "B", 2), (4, "H", 0),
    (6, "h", 1), (10, "I", 1), (14, "H", 0), (16, "I", 1),
    (20, "H", 1), (22, "I", 1),
])
def test_end_path_support_does_not_accept_other_rendering_or_object_styles(monkeypatch, offset, fmt, value):
    payload = bytearray(_PATH_INFO)
    struct.pack_into("<" + fmt, payload, offset, value)
    source = _wmf_records(_clip(0), _begin(), _polygon(), _end(bytes(payload)), _clip(2), _clip(1))
    _reject_before_native(monkeypatch, source)


@pytest.mark.parametrize("offset, fmt, value", [
    (0, "I", 0x00CC0020), (4, "h", 0), (6, "h", -1), (8, "h", 1),
    (10, "h", 1), (12, "h", 0), (14, "h", -1), (20, "I", 12),
    (24, "i", 0), (24, "i", 8193), (28, "i", -5), (32, "H", 2),
    (34, "H", 24), (36, "I", 1), (40, "I", 1), (52, "I", 3),
    (56, "I", 3), (63, "B", 1), (67, "B", 1),
])
def test_dib_stretch_requires_exact_packed_monochrome_structure(monkeypatch, offset, fmt, value):
    raw = bytearray(_dib())
    struct.pack_into("<" + fmt, raw, offset, value)
    _reject_before_native(monkeypatch, _wmf_records(_record(0x0B41, bytes(raw))))


@pytest.mark.parametrize("raw", [_dib()[:-2], _dib() + b"xx", _dib()[:20], _dib()[:66]])
def test_dib_rejects_missing_or_extra_header_palette_and_pixel_bytes(monkeypatch, raw):
    _reject_before_native(monkeypatch, _wmf_records(_record(0x0B41, raw)))


def test_path_control_and_clip_depth_budgets_stop_native(monkeypatch):
    monkeypatch.setattr(fonts, "_MAX_PATH_POINTS", 3)
    _reject_before_native(monkeypatch, _wmf_records(*_path()))
    monkeypatch.setattr(fonts, "_MAX_PATH_POINTS", 4096)
    monkeypatch.setattr(fonts, "_MAX_LEGACY_CONTROLS", 4)
    _reject_before_native(monkeypatch, _wmf_records(*_path()))
    monkeypatch.setattr(fonts, "_MAX_LEGACY_CONTROLS", 4096)
    monkeypatch.setattr(fonts, "_MAX_CLIP_SAVES", 2)
    _reject_before_native(monkeypatch, _wmf_records(_clip(0), _clip(0), _clip(0)))


def test_dib_pixel_budget_is_independent_of_packed_bit_buffer_size(monkeypatch):
    source = _wmf_records(_record(0x0B41, _dib()))
    monkeypatch.setattr(fonts, "_MAX_DIB_PIXELS", 20)
    assert fonts.wmf_font_runs(source) == ()
    monkeypatch.setattr(fonts, "_MAX_DIB_PIXELS", 19)
    _reject_before_native(monkeypatch, source)


def test_path_support_cannot_hide_missing_or_changed_font_identity(monkeypatch):
    # Drawing controls are not a way to accept text without an explicit font.
    _reject_before_native(monkeypatch, _wmf_records(*_path(), _textout(b"Fe")))
    _reject_before_native(monkeypatch, _wmf_records(_create_font(), _select(1), *_path()))
    _reject_before_native(monkeypatch, _wmf_records(_create_font(), _select(0),
                                                  _ignore(1), _textout(b"Fe"), _ignore(0)))


@pytest.mark.skipif(not hasattr(ctypes, "WinDLL"), reason="Windows GDI+ unavailable")
def test_valid_legacy_controls_never_bypass_windows_missing_font_check(monkeypatch):
    source = _wmf_records(_create_font(b"ShChemMissingFont762031", charset=0),
                          _select(0), *_path(), _textout(b"Fe3+"))
    _reject_before_native(monkeypatch, source, code="metafile_wmf_font_unavailable")


@pytest.mark.skipif(not hasattr(ctypes, "WinDLL"), reason="Windows GDI+ unavailable")
def test_real_windows_gdiplus_keeps_legacy_path_preview_and_source_bound_bytes():
    # Ordinary geometry survives the limited clip lifecycle. The actual user
    # WMF is verified separately with its immutable source SHA and own artifact.
    source = _wmf_records(*_path(), _record(0x0214, struct.pack("<hh", 10, 10)),
                          _record(0x0213, struct.pack("<hh", 40, 100)))
    result = preview.render_word_metafile(source, mime_type="image/wmf", display_size=(400, 200))
    with Image.open(io.BytesIO(result["bytes"])) as image:
        assert image.size == (400, 200)
        assert any(low != high for low, high in image.getextrema())
    assert result["original_sha256"] == hashlib.sha256(source).hexdigest()
    assert "GDI+" in result["conversion_note"]

from __future__ import annotations

import ctypes
import struct
from typing import ClassVar

import pytest
from test_word_metafile_preview import _wmf

from integrations.deeptutor_shchem_v1 import desktop_word_wmf_fonts as module


@pytest.mark.skipif(
    not hasattr(ctypes, "WinDLL"), reason="Windows font API unavailable"
)
def test_windows_real_font_and_glyph_checks_reject_substitution():
    module.verify_wmf_fonts(((_font(b"Arial", charset=0), b"Fe3+"),))
    with pytest.raises(module.WmfFontError) as exc:
        module.verify_wmf_fonts(
            ((_font(b"ShChemMissingFont762031", charset=0), b"Fe3+"),)
        )
    assert exc.value.code == "metafile_wmf_font_unavailable"


def _record(function, raw=b""):
    assert len(raw) % 2 == 0
    return struct.pack("<IH", 3 + len(raw) // 2, function) + raw


def _wmf_records(*records):
    body = b"".join((*records, _record(0)))
    placeable = _wmf()[:22]
    header = struct.pack(
        "<HHHIHIH",
        1,
        9,
        0x300,
        9 + len(body) // 2,
        sum(
            struct.unpack_from("<H", record, 4)[0] in {0x02FB, *module._CREATE_OBJECTS}
            for record in records
        ),
        max(len(record) for record in (*records, _record(0))) // 2,
        0,
    )
    return placeable + header + body


def _font(face=b"TestFont", *, charset=1, height=20):
    face = face.split(b"\0", 1)[0]
    assert 0 < len(face) <= 31
    face_field = face + b"\0" + b"\0" * (31 - len(face))
    return struct.pack(
        "<5h8B32s",
        height,
        0,
        0,
        0,
        400,
        0,
        0,
        0,
        charset,
        0,
        0,
        0,
        0,
        face_field,
    )


def _create_font(face=b"TestFont", *, charset=1):
    return _record(0x02FB, _font(face, charset=charset))


def _select(index):
    return _record(0x012D, struct.pack("<H", index))


def _delete(index):
    return _record(0x01F0, struct.pack("<H", index))


def _save():
    return _record(0x001E)


def _restore(level=1):
    return _record(0x0127, struct.pack("<h", level))


def _textout(text):
    text = bytes(text)
    raw = struct.pack("<h", len(text)) + text + b"\0" * (len(text) & 1)
    return _record(0x0521, raw + struct.pack("<hh", 0, 0))


def _exttextout(text, *, options=0, dx=None):
    text = bytes(text)
    assert len(text) <= 32767
    raw = struct.pack("<hh h H", 0, 0, len(text), options)
    if options & 6:
        raw += b"\0" * 8
    raw += text + b"\0" * (len(text) & 1)
    if dx is not None:
        assert len(dx) == len(text)
        raw += struct.pack("<" + "h" * len(dx), *dx)
    return _record(0x0A32, raw)


def _escape(payload=b"", *, escape=15):
    payload = bytes(payload)
    return _record(
        0x0626,
        struct.pack("<HH", escape, len(payload)) + payload + b"\0" * (len(payload) & 1),
    )


def _fonted(*records):
    return _wmf_records(_create_font(), _select(0), *records)


@pytest.mark.parametrize("capacity", [0, 4097])
def test_invalid_declared_object_capacity_never_reaches_font_api(capacity):
    source = bytearray(_fonted(_textout(b"Fe")))
    struct.pack_into("<H", source, 32, capacity)
    with pytest.raises(module.WmfFontError):
        module.wmf_font_runs(bytes(source))


def test_wmf_font_runs_extracts_exttextout_and_textout_with_explicit_font():
    raw_font = _font(b"Cambria", charset=134)
    source = _wmf_records(
        _record(0x02FB, raw_font),
        _select(0),
        _escape(b"inert"),
        _exttextout(b"CO", options=6, dx=[3, 4]),
        _textout(b"2"),
    )

    assert module.wmf_font_runs(source) == (
        (raw_font, b"CO"),
        (raw_font, b"2"),
    )


def test_save_restore_delete_and_object_slot_reuse_preserve_font_identity():
    first = _font(b"First")
    second = _font(b"Second")
    third = _font(b"Third")
    fourth = _font(b"Fourth")
    source = _wmf_records(
        _record(0x02FB, first),
        _record(0x02FA),
        _select(0),
        _textout(b"one"),
        _save(),
        _record(0x02FB, second),
        _select(2),
        _textout(b"two"),
        _restore(),
        _delete(2),
        _record(0x02FB, third),
        _select(2),
        _textout(b"three"),
        _select(0),
        _delete(2),
        _select(1),
        _textout(b"stock-keeps-first"),
        _record(0x02FB, fourth),
        _select(2),
        _textout(b"four"),
    )

    assert module.wmf_font_runs(source) == (
        (first, b"one"),
        (second, b"two"),
        (third, b"three"),
        (first, b"stock-keeps-first"),
        (fourth, b"four"),
    )


@pytest.mark.parametrize(
    "records",
    [
        (_select(0), _textout(b"default")),
        (_record(0x02FA), _select(0), _textout(b"stock")),
    ],
)
def test_stock_default_or_unresolved_font_never_reaches_text(records):
    with pytest.raises(module.WmfFontError) as exc:
        module.wmf_font_runs(_wmf_records(*records))
    assert exc.value.code == "metafile_wmf_text_unverified"


@pytest.mark.parametrize(
    "record",
    [
        _record(0x0777),
        _escape(escape=9),
    ],
)
def test_unknown_record_and_non_comment_escape_are_rejected(record):
    with pytest.raises(module.WmfFontError) as exc:
        module.wmf_font_runs(_fonted(record))
    assert exc.value.code == "metafile_wmf_text_unverified"


@pytest.mark.parametrize(
    "raw_record",
    [
        _record(0x0A32, b"\0" * 6),
        _record(0x0A32, struct.pack("<hh h H", 0, 0, -1, 0) + b"\0" * 2),
        _record(0x0A32, struct.pack("<hh h H", 0, 0, 1, 1) + b"A\0"),
        _record(0x0A32, struct.pack("<hh h H", 0, 0, 2, 0) + b"AB\0\0"),
        _record(0x0A32, struct.pack("<hh h H", 0, 0, 2, 0) + b"A\x1f"),
        _record(0x0521, struct.pack("<h", -1) + b"\0" * 4),
        _record(0x0521, struct.pack("<h", 2) + b"AB\0\0"),
        _record(0x0521, struct.pack("<h", 1) + b"A\0" + b"\0" * 6),
        _record(0x0521, struct.pack("<h", 2) + b"A\x1f" + b"\0\0"),
    ],
)
def test_text_records_reject_truncation_negative_counts_dx_options_and_controls(
    raw_record,
):
    with pytest.raises(module.WmfFontError) as exc:
        module.wmf_font_runs(_fonted(raw_record))
    assert exc.value.code == "metafile_wmf_text_unverified"


def test_text_budgets_are_fail_closed_and_exact_limits_are_allowed(monkeypatch):
    source = _fonted(_textout(b"A"), _textout(b"B"))
    monkeypatch.setattr(module, "_MAX_TEXT_BYTES", 2)
    assert len(module.wmf_font_runs(source)) == 2
    monkeypatch.setattr(module, "_MAX_TEXT_BYTES", 1)
    with pytest.raises(module.WmfFontError):
        module.wmf_font_runs(source)

    source = _fonted(_textout(b"A"), _textout(b"B"), _textout(b"C"))
    monkeypatch.setattr(module, "_MAX_RUNS", 2)
    with pytest.raises(module.WmfFontError):
        module.wmf_font_runs(source)


def test_verify_wmf_fonts_deduplicates_only_identical_font_text_runs(monkeypatch):
    calls = []

    class FakeApi:
        def verify(self, raw, text):
            calls.append((raw, text))

    monkeypatch.setattr(module, "_WindowsFontApi", lambda: FakeApi())
    raw_font = _font()
    module.verify_wmf_fonts(((raw_font, b"A"), (raw_font, b"A"), (raw_font, b"B")))

    assert calls == [(raw_font, b"A"), (raw_font, b"B")]


def test_empty_runs_do_not_require_windows_api(monkeypatch):
    monkeypatch.setattr(
        module, "_WindowsFontApi", lambda: pytest.fail("unexpected API")
    )
    module.verify_wmf_fonts(())


class _FakeFunction:
    def __init__(self, owner, name):
        self.owner = owner
        self.name = name
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.owner.calls.append((self.name, args))
        self.owner.call_counts[self.name] = self.owner.call_counts.get(self.name, 0) + 1
        if self.owner.fail_once == self.name and self.owner.call_counts[self.name] == 1:
            if self.name in {
                "CreateCompatibleDC",
                "CreateFontIndirectA",
                "SelectObject",
            }:
                return 0
            if self.name in {"GetTextFaceA", "GetGlyphIndicesA"}:
                return 0
            if self.name == "GetTextCharset":
                return 0
            raise OSError("synthetic GDI failure")
        if self.name == "CreateCompatibleDC":
            return 101
        if self.name == "DeleteDC":
            return 1
        if self.name == "CreateFontIndirectA":
            spec = ctypes.cast(args[0], ctypes.POINTER(module._Logfont)).contents
            self.owner.requested_face = bytes(spec.face).split(b"\0", 1)[0]
            self.owner.requested_charset = spec.charset
            return 202
        if self.name == "DeleteObject":
            return 1
        if self.name == "SelectObject":
            return 303
        if self.name == "GetTextFaceA":
            actual = self.owner.actual_face or self.owner.requested_face
            args[2].value = actual + b"\0"
            return len(actual)
        if self.name == "GetTextCharset":
            return self.owner.actual_charset
        if self.name == "GetGlyphIndicesA":
            indices = args[3]
            for index, value in enumerate(self.owner.glyph_values):
                if index < len(indices):
                    indices[index] = value
            return self.owner.glyph_count
        raise AssertionError(self.name)


class _FakeGdi:
    _NAMES: ClassVar[set[str]] = {
        "CreateCompatibleDC",
        "DeleteDC",
        "CreateFontIndirectA",
        "DeleteObject",
        "SelectObject",
        "GetTextFaceA",
        "GetTextCharset",
        "GetGlyphIndicesA",
    }

    def __init__(
        self,
        *,
        actual_face=None,
        actual_charset=None,
        glyph_count=1,
        glyph_values=(7,),
        fail_once=None,
    ):
        self.actual_face = actual_face
        self.actual_charset = actual_charset
        self.glyph_count = glyph_count
        self.glyph_values = glyph_values
        self.fail_once = fail_once
        self.calls = []
        self.call_counts = {}
        self.requested_face = None
        self.requested_charset = None
        self.functions = {}

    def __getattr__(self, name):
        if name not in self._NAMES:
            raise AttributeError(name)
        function = self.functions.setdefault(name, _FakeFunction(self, name))
        return function


def _fake_windows_api(monkeypatch, fake):
    requests = []

    def loader(name, **kwargs):
        requests.append((name, kwargs))
        return fake

    monkeypatch.setattr(module.ctypes, "WinDLL", loader, raising=False)
    return requests


def test_windows_font_api_uses_only_system32_loader_and_sets_signatures(monkeypatch):
    fake = _FakeGdi()
    requests = _fake_windows_api(monkeypatch, fake)

    module._WindowsFontApi()

    assert requests == [("gdi32.dll", {"winmode": 0x800})]
    for name in fake._NAMES:
        assert fake.functions[name].argtypes is not None
        assert fake.functions[name].restype is not None


def test_fake_gdi_validates_actual_face_charset_and_allows_short_dbcs_count(
    monkeypatch,
):
    fake = _FakeGdi(actual_charset=134, glyph_count=1, glyph_values=(1234,))
    _fake_windows_api(monkeypatch, fake)
    api = module._WindowsFontApi()

    api.verify(_font(b"SimSun", charset=134), b"\x81@")

    assert fake.requested_face == b"SimSun"
    assert fake.requested_charset == 134
    glyph_calls = [args for name, args in fake.calls if name == "GetGlyphIndicesA"]
    assert len(glyph_calls) == 1 and glyph_calls[0][2] == 2


@pytest.mark.parametrize(
    "fake_kwargs, code",
    [
        (
            {"actual_face": b"Arial", "actual_charset": 134},
            "metafile_wmf_font_unavailable",
        ),
        ({"actual_charset": 0}, "metafile_wmf_font_unavailable"),
        ({"actual_charset": 134, "glyph_count": 0}, "metafile_wmf_glyph_unavailable"),
        ({"actual_charset": 134, "glyph_count": 3}, "metafile_wmf_glyph_unavailable"),
        (
            {"actual_charset": 134, "glyph_values": (0,)},
            "metafile_wmf_glyph_unavailable",
        ),
        (
            {"actual_charset": 134, "glyph_values": (0xFFFF,)},
            "metafile_wmf_glyph_unavailable",
        ),
    ],
)
def test_fake_gdi_rejects_fallback_charset_and_missing_glyphs(
    monkeypatch, fake_kwargs, code
):
    fake = _FakeGdi(**fake_kwargs)
    _fake_windows_api(monkeypatch, fake)
    api = module._WindowsFontApi()

    with pytest.raises(module.WmfFontError) as exc:
        api.verify(_font(b"SimSun", charset=134), b"\x81@")
    assert exc.value.code == code


@pytest.mark.parametrize(
    "fail_once, cleanup",
    [
        ("CreateCompatibleDC", []),
        ("CreateFontIndirectA", ["DeleteDC"]),
        ("SelectObject", ["DeleteObject", "DeleteDC"]),
        ("GetTextFaceA", ["SelectObject", "DeleteObject", "DeleteDC"]),
        ("GetTextCharset", ["SelectObject", "DeleteObject", "DeleteDC"]),
        ("GetGlyphIndicesA", ["SelectObject", "DeleteObject", "DeleteDC"]),
    ],
)
def test_fake_gdi_step_failures_release_every_acquired_resource(
    monkeypatch, fail_once, cleanup
):
    fake = _FakeGdi(actual_charset=134, fail_once=fail_once)
    _fake_windows_api(monkeypatch, fake)
    api = module._WindowsFontApi()

    with pytest.raises((OSError, module.WmfFontError)):
        api.verify(_font(b"SimSun", charset=134), b"A")

    assert (
        [name for name, _ in fake.calls][-len(cleanup) :] == cleanup
        if cleanup
        else True
    )


def test_cleanup_error_does_not_prevent_later_gdi_cleanup(monkeypatch):
    fake = _FakeGdi(actual_charset=134)
    original = fake.__getattr__("DeleteObject")

    def failing_delete_object(*args):
        original(*args)
        raise OSError("synthetic cleanup failure")

    fake.functions["DeleteObject"] = failing_delete_object
    _fake_windows_api(monkeypatch, fake)
    api = module._WindowsFontApi()

    with pytest.raises(OSError, match="synthetic cleanup failure"):
        api.verify(_font(b"SimSun", charset=134), b"A")

    assert [name for name, _ in fake.calls][-3:] == [
        "SelectObject",
        "DeleteObject",
        "DeleteDC",
    ]

"""Fail-closed WMF font preflight before GDI+ draws source-bound previews.

Reads records, not equation semantics. Only explicit fonts with locally present
glyphs may reach the renderer; Windows' silent font substitution is not enough.
Reference: Microsoft MS-WMF Font Object, META_EXTTEXTOUT, GetGlyphIndicesA.
"""

from __future__ import annotations

import ctypes
import struct
from contextlib import ExitStack


class WmfFontError(ValueError):
    def __init__(self, code, message):
        self.code, self.message_zh = code, message
        super().__init__(message)


def _unsupported():
    raise WmfFontError(
        "metafile_wmf_text_unverified",
        "此WMF的文字、字体或控制记录暂不能完整核验，未生成预览。",
    )


# These records cannot introduce/select a font. Unknown operations are refused,
# rather than possibly missing a second text or font-selection mechanism.
_OTHER_RECORDS = {
    0x0000,
    0x0102,
    0x0201,
    0x0209,
    0x0103,
    0x0104,
    0x0105,
    0x0106,
    0x0107,
    0x0108,
    0x012E,
    0x020A,
    0x020B,
    0x020C,
    0x020D,
    0x020E,
    0x020F,
    0x0211,
    0x0410,
    0x0412,
    0x0213,
    0x0214,
    0x0228,
    0x0415,
    0x0416,
    0x0418,
    0x0419,
    0x041B,
    0x061C,
    0x0817,
    0x081A,
    0x0830,
    0x0324,
    0x0325,
    0x0538,
    0x041F,
    0x0548,
    0x061D,
}
_CREATE_OBJECTS = {0x02FA, 0x02FC, 0x00F7, 0x0142, 0x01F9, 0x06FF}
_MAX_RUNS = 4096
_MAX_TEXT_BYTES = 262_144


def wmf_font_runs(data):
    """Extract all drawn text and its selected LOGFONT from validated WMF.

    The caller must first validate the placeable header and every record length.
    This pass additionally validates text boundaries, object/state transitions,
    and permits only inert MFCOMMENT escapes. No native code runs here.
    """
    if len(data) < 40:
        _unsupported()
    capacity = struct.unpack_from("<H", data, 32)[0]
    if capacity > 4096:
        _unsupported()
    table, saved, runs = [], [], []
    active = None
    total_text, offset = 0, 40
    while offset + 6 <= len(data):
        words, function = struct.unpack_from("<IH", data, offset)
        if words < 3 or offset + words * 2 > len(data):
            _unsupported()
        raw = data[offset + 6 : offset + words * 2]
        offset += words * 2
        if function == 0:
            break
        if function == 0x02FB or function in _CREATE_OBJECTS:
            value = False
            if function == 0x02FB:
                if len(raw) != 50 or b"\0" not in raw[18:50] or not raw[18]:
                    _unsupported()
                value = raw
            if None in table:
                table[table.index(None)] = value
            else:
                if len(table) >= capacity:
                    _unsupported()
                table.append(value)
        elif function in {0x012D, 0x01F0}:
            if len(raw) != 2:
                _unsupported()
            index = struct.unpack("<H", raw)[0]
            # Stock fonts have no source-bound face; do not assume a system one.
            if index >= len(table) or table[index] is None:
                _unsupported()
            if function == 0x01F0:
                if table[index] is not False and (
                    table[index] == active or table[index] in saved
                ):
                    _unsupported()
                table[index] = None
            elif table[index] is not False:
                active = table[index]
        elif function == 0x001E:
            if raw or len(saved) >= 128:
                _unsupported()
            saved.append(active)
        elif function == 0x0127:
            if len(raw) != 2:
                _unsupported()
            level = struct.unpack("<h", raw)[0]
            index = len(saved) + level if level < 0 else level - 1
            if level == 0 or not 0 <= index < len(saved):
                _unsupported()
            active = saved[index]
            del saved[index:]
        elif function in {0x0A32, 0x0521}:
            if not active:
                _unsupported()
            if function == 0x0A32:
                if len(raw) < 8:
                    _unsupported()
                count, options = struct.unpack_from("<hH", raw, 4)
                if options & ~6:
                    _unsupported()
                start = 16 if options & 6 else 8
                end = start + count + (count & 1)
                if count < 0 or len(raw) not in {end, end + 2 * count}:
                    _unsupported()
            else:
                if len(raw) < 6:
                    _unsupported()
                count = struct.unpack_from("<h", raw)[0]
                start = 2
                if count < 0 or len(raw) != 6 + count + (count & 1):
                    _unsupported()
            text = raw[start : start + count]
            if any(value < 32 for value in text):
                _unsupported()
            total_text += count
            if len(runs) >= _MAX_RUNS or total_text > _MAX_TEXT_BYTES:
                _unsupported()
            if text:
                runs.append((active, text))
        elif function == 0x0626:
            if len(raw) < 4:
                _unsupported()
            escape, count = struct.unpack_from("<HH", raw)
            if escape != 15 or len(raw) != 4 + count + (count & 1):
                _unsupported()
        elif function not in _OTHER_RECORDS:
            _unsupported()
    return tuple(runs)


class _Logfont(ctypes.Structure):
    _fields_ = (
        [
            (name, ctypes.c_int32)
            for name in ("height", "width", "escapement", "orientation", "weight")
        ]
        + [
            (name, ctypes.c_ubyte)
            for name in (
                "italic",
                "underline",
                "strikeout",
                "charset",
                "outprecision",
                "clipprecision",
                "quality",
                "pitch",
            )
        ]
        + [("face", ctypes.c_char * 32)]
    )


class _WindowsFontApi:
    def __init__(self):
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            raise WmfFontError(
                "metafile_renderer_unavailable", "当前系统不能核验Windows旧公式字体。"
            )
        self.gdi = loader("gdi32.dll", winmode=0x00000800)
        ptr, integer = ctypes.c_void_p, ctypes.c_int32
        signatures = {
            "CreateCompatibleDC": ([ptr], ptr),
            "DeleteDC": ([ptr], integer),
            "CreateFontIndirectA": ([ctypes.POINTER(_Logfont)], ptr),
            "DeleteObject": ([ptr], integer),
            "SelectObject": ([ptr, ptr], ptr),
            "GetTextFaceA": ([ptr, integer, ptr], integer),
            "GetTextCharset": ([ptr], integer),
            "GetGlyphIndicesA": (
                [ptr, ptr, integer, ptr, ctypes.c_uint32],
                ctypes.c_uint32,
            ),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.gdi, name)
            function.argtypes, function.restype = args, result

    @staticmethod
    def _handle(value):
        if not value or value == ctypes.c_void_p(-1).value:
            raise OSError("Windows font check failed")
        return value

    def verify(self, raw, text):
        spec = _Logfont(*struct.unpack("<5h8B32s", raw))
        with ExitStack() as cleanup:
            dc = self._handle(self.gdi.CreateCompatibleDC(None))
            cleanup.callback(self.gdi.DeleteDC, dc)
            font = self._handle(self.gdi.CreateFontIndirectA(ctypes.byref(spec)))
            cleanup.callback(self.gdi.DeleteObject, font)
            previous = self._handle(self.gdi.SelectObject(dc, font))
            cleanup.callback(self.gdi.SelectObject, dc, previous)
            name = ctypes.create_string_buffer(256)
            if not self.gdi.GetTextFaceA(dc, len(name), name):
                raise OSError("Windows font name check failed")
            # Compare the actual selected face, not merely successful creation:
            # CreateFontIndirect silently falls back when a font is absent.
            if name.value.lower() != bytes(spec.face).lower() or (
                spec.charset != 1 and self.gdi.GetTextCharset(dc) != spec.charset
            ):
                raise WmfFontError(
                    "metafile_wmf_font_unavailable",
                    "此WMF所需的原字体或字符集在本机不可用，未用替代字体生成预览。",
                )
            indices = (ctypes.c_uint16 * len(text))()
            count = self.gdi.GetGlyphIndicesA(dc, text, len(text), indices, 1)
            # DBCS text can yield fewer glyphs than input bytes.
            if not 0 < count <= len(text) or any(
                index in {0, 0xFFFF} for index in indices[:count]
            ):
                raise WmfFontError(
                    "metafile_wmf_glyph_unavailable",
                    "此WMF含本机字体不能完整显示的符号，未生成可能缺字的预览。",
                )


def verify_wmf_fonts(runs):
    if not runs:
        return
    api = _WindowsFontApi()
    # Identical runs need only one check within this operation; no font state is
    # cached across operations or shared with Word, Pillow or another thread.
    for raw, text in dict.fromkeys(runs):
        api.verify(raw, text)

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
_MAX_PATH_POINTS = 4096
_MAX_LEGACY_CONTROLS = 4096
_MAX_CLIP_SAVES = 128
_MAX_DIB_PIXELS = 16_000_000


def _monochrome_dib_stretch(raw):
    """Validate the embedded BI_RGB/1bpp DIBSTRETCHBLT subset, never a handle.

    MS-WMF 2.3.1.3.1 / 2.2.2.3: DWORD ROP, eight signed WORD coordinates,
    BITMAPINFOHEADER, two RGBQUADs, DWORD-aligned bottom-up pixel rows.
    Other DIB headers, compression, palette indexes and source-DC forms stay
    unsupported. The original raster operation and pixels reach GDI+ unchanged.
    """
    if len(raw) < 68:
        _unsupported()
    rop, source_h, source_w, source_y, source_x, dest_h, dest_w, _, _ = (
        struct.unpack_from("<I8h", raw)
    )
    header, width, height, planes, bits, compression, image_size, _, _, used, important = (
        struct.unpack_from("<IiiHHIIiiII", raw, 20)
    )
    stride = ((width + 31) // 32) * 4
    pixel_bytes = stride * height
    if (
        rop != 0x00660046  # SRCINVERT; not an arbitrary ternary operation.
        or header != 40 or planes != 1 or bits != 1 or compression != 0
        or not 0 < width <= 8192 or not 0 < height <= 8192
        or width * height > _MAX_DIB_PIXELS or image_size not in (0, pixel_bytes)
        or used not in (0, 2) or important not in (0, 1, 2)
        or source_x != 0 or source_y != 0
        or (source_w, source_h) != (width, height)
        or dest_w <= 0 or dest_h <= 0
        or len(raw) != 68 + pixel_bytes
        or raw[63] != 0 or raw[67] != 0
    ):
        _unsupported()


class _LegacyPathControls:
    """Bounded Win16 drawing controls, not executable/printer escape access.

    Windows 3.0 SDK PATH_INFO is 26 bytes (short, BYTE, BYTE, LOGPEN,
    LOGBRUSH, DWORD); its CLIP_TO_PATH int and POSTSCRIPT_IGNORE BOOL are
    16 bits. Only the source's NO_DISPLAY closed-polygon clipping lifecycle
    is supported. Retaining a completed path for CLIP_INCLUSIVE is a bounded
    compatibility inference from the source stream and Win32 EndPath /
    SelectClipPath contracts, not an assertion of all old printer semantics.
    GDI's SaveDC/RestoreDC does not substitute for the separate clipping stack.
    All PostScript data, callbacks, channels and other escapes stay refused.
    """

    def __init__(self):
        self.ignored = False
        self.open_path = False
        self.polygon_seen = False
        self.completed_path = False
        self.clips = []
        self.count = 0

    def record(self, function, raw):
        if function == 0x0626:
            self._escape(raw)
        elif self.open_path:
            # No nested paths, text, state changes or native object operations
            # may hide inside a path whose semantics we have not validated.
            if function != 0x0324 or self.polygon_seen or len(raw) < 2:
                _unsupported()
            count = struct.unpack_from("<H", raw)[0]
            if not 4 <= count <= _MAX_PATH_POINTS or len(raw) != 2 + 4 * count:
                _unsupported()
            points = tuple(struct.iter_unpack("<hh", raw[2:]))
            area = sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2)
                       in zip(points, points[1:]))
            if points[0] != points[-1] or not area:
                _unsupported()
            self.polygon_seen = True
        elif function in (0x001E, 0x0127):
            if self.ignored or self.completed_path:
                _unsupported()
        elif function in (0x0A32, 0x0521) and self.ignored:
            # We still check every actual text run; never rely on an ignore
            # flag to bypass the explicit-font and glyph preflight.
            _unsupported()
        if function == 0x0B41:
            _monochrome_dib_stretch(raw)

    def _escape(self, raw):
        if len(raw) < 4:
            _unsupported()
        escape, count = struct.unpack_from("<HH", raw)
        if len(raw) != 4 + count + (count & 1):
            _unsupported()
        payload = raw[4:4 + count]
        if escape == 15 and not self.open_path:
            return  # Existing inert MFCOMMENT support.
        self.count += 1
        if self.count > _MAX_LEGACY_CONTROLS:
            _unsupported()
        if escape == 38:
            if count != 2 or self.open_path:
                _unsupported()
            value = struct.unpack("<H", payload)[0]
            if value not in (0, 1) or bool(value) == self.ignored:
                _unsupported()
            self.ignored = bool(value)
        elif escape == 4096:
            if (count or self.ignored or self.open_path or self.completed_path
                    or not self.clips or self.clips[-1]):
                _unsupported()
            self.open_path, self.polygon_seen = True, False
        elif escape == 4098:
            if count != 26 or self.ignored or not self.open_path or not self.polygon_seen:
                _unsupported()
            # NO_DISPLAY, ALTERNATE, TRANSPARENT, PS_NULL/BS_NULL, zero unused
            # widths/colors/hatch: this branch cannot draw an unverified path.
            if struct.unpack("<hBBHhhIHIHI", payload) != (
                0, 1, 1, 5, 0, 0, 0, 1, 0, 0, 0
            ):
                _unsupported()
            self.open_path, self.completed_path = False, True
        elif escape == 4097:
            if count != 2 or self.ignored or self.open_path:
                _unsupported()
            mode = struct.unpack("<h", payload)[0]
            if mode == 0:
                if self.completed_path or len(self.clips) >= _MAX_CLIP_SAVES:
                    _unsupported()
                self.clips.append(False)
            elif mode == 2:
                if not self.clips or self.clips[-1] or not self.completed_path:
                    _unsupported()
                self.clips[-1], self.completed_path = True, False
            elif mode == 1:
                if not self.clips or not self.clips[-1] or self.completed_path:
                    _unsupported()
                self.clips.pop()
            else:
                _unsupported()
        else:
            _unsupported()

    def finish(self):
        if self.ignored or self.open_path or self.completed_path or self.clips:
            _unsupported()


def _complete_records(data):
    """Prove the whole bounded stream before accepting a text-free font object."""
    if len(data) < 46:
        _unsupported()
    end = 22 + struct.unpack_from("<I", data, 28)[0] * 2
    if not 46 <= end <= len(data) or any(data[end:]):
        _unsupported()
    records, offset = [], 40
    while offset + 6 <= end:
        words, function = struct.unpack_from("<IH", data, offset)
        if words < 3 or offset + words * 2 > end or len(records) >= 200_000:
            _unsupported()
        raw = data[offset + 6:offset + words * 2]
        records.append((function, raw))
        offset += words * 2
        if function == 0:
            if raw or offset != end:
                _unsupported()
            return records
    _unsupported()


def wmf_font_runs(data):
    """Extract all drawn text and its selected LOGFONT from validated WMF.

    The caller must first validate the placeable header and every record length.
    This pass additionally validates text boundaries, object/state transitions,
    and permits inert MFCOMMENT plus a strict Win16 polygon-clip subset.
    No native code runs here.
    """
    if len(data) < 40:
        _unsupported()
    capacity = struct.unpack_from("<H", data, 32)[0]
    if capacity > 4096:
        _unsupported()
    records = _complete_records(data)
    controls = _LegacyPathControls()
    text_free = not any(function in {0x0A32, 0x0521} for function, _ in records)
    table, saved, runs = [], [], []
    active = None
    total_text = 0
    for function, raw in records:
        if function == 0:
            break
        controls.record(function, raw)
        if function == 0x02FB or function in _CREATE_OBJECTS:
            value = False
            if function == 0x02FB:
                # Some geometric WMFs carry a compact unused Font Object.
                # Accept its bounded, terminated face only when the complete
                # stream proves zero text-drawing records. Text retains the
                # existing full LOGFONT and actual-face/glyph checks.
                if ((len(raw) != 50 and not (text_free and 20 <= len(raw) < 50))
                        or b"\0" not in raw[18:50] or not raw[18]):
                    _unsupported()
                value = raw
            elif function == 0x00F7:
                if len(raw) < 4:
                    _unsupported()
                version, count = struct.unpack_from("<HH", raw)
                if (version != 0x0300 or not 1 <= count <= 256
                        or len(raw) != 4 + 4 * count
                        or any(raw[index] & ~7 for index in range(7, len(raw), 4))):
                    _unsupported()
                value = "palette"
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
            elif table[index] == "palette":
                _unsupported()
            elif table[index] is not False:
                active = table[index]
        elif function == 0x0234:
            # META_SELECTPALETTE has exactly one object-table WORD index.
            if len(raw) != 2:
                _unsupported()
            index = struct.unpack("<H", raw)[0]
            if index >= len(table) or table[index] != "palette":
                _unsupported()
        elif function == 0x0035:
            if raw:
                _unsupported()
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
            pass  # Fully checked by the independent path/clip lifecycle above.
        elif function == 0x0B41:
            pass  # Fully checked embedded monochrome DIB, not a native handle.
        elif function not in _OTHER_RECORDS:
            _unsupported()
    controls.finish()
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

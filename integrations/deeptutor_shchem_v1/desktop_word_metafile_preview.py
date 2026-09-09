"""Local, source-bound raster previews of embedded Windows metafiles.

The original bytes are never changed.  This is image rendering, not OCR or a
chemical-content check.  We call Pillow's Windows renderer directly because
WmfImagePlugin's process-global handler keeps a mutable bounding box.
"""

from __future__ import annotations

import ctypes
import hashlib
import io
import math
import struct
import threading
from collections import OrderedDict
from collections.abc import Mapping
from contextlib import ExitStack

from PIL import Image

RENDERER_REVISION = "20260910-word-metafile-preview-v4-gdiplus-emfplus"
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_DIMENSION = 8192
MAX_PIXELS = 16_000_000
MAX_RECORDS = 200_000
DEFAULT_DPI = 192
DEFAULT_MAX_DIMENSION = 2048
_CACHE_MAX_BYTES = 16 * 1024 * 1024
_CACHE_MAX_ENTRIES = 32
_MIME_FORMATS = {
    "image/wmf": "wmf",
    "image/x-wmf": "wmf",
    "image/emf": "emf",
    "image/x-emf": "emf",
}
_LOCK = threading.RLock()
_CACHE: OrderedDict[tuple, dict] = OrderedDict()
_cache_bytes = 0


class WordMetafilePreviewError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message_zh = message
        super().__init__(message)


def _fail(code: str, detail: str):
    raise WordMetafilePreviewError(code, detail + " 原图仍保留，请核对原Word。")


def can_attempt_metafile(asset: Mapping) -> bool:
    """Metadata-only eligibility; a True value does not promise conversion."""
    if not isinstance(asset, Mapping):
        return False
    mime_type = asset.get("mime_type")
    size = asset.get("bytes_count")
    return (
        isinstance(mime_type, str)
        and _MIME_FORMATS.get(mime_type.lower().strip()) == "emf"
        and (size is None or (type(size) is int and 0 < size <= MAX_SOURCE_BYTES))
    )


def _bbox_size(bbox):
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if not (0 < width <= 100_000_000 and 0 < height <= 100_000_000):
        _fail("metafile_invalid_dimensions", "旧式图片的边界尺寸无效，未生成预览。")
    return width, height


def _wmf_header(data):
    if len(data) < 46 or data[:6] != b"\xd7\xcd\xc6\x9a\x00\x00":
        _fail("metafile_invalid_header", "未检测到受支持的可定位WMF文件头，未猜测图片格式。")
    checksum = 0
    for word in struct.unpack_from("<11H", data):
        checksum ^= word
    kind, header_words, version, file_words = struct.unpack_from("<HHHI", data, 22)
    if checksum or kind != 1 or header_words != 9 or version not in (0x100, 0x300):
        _fail("metafile_invalid_header", "WMF文件头或校验值无效，未生成预览。")
    declared_end = 22 + file_words * 2
    if declared_end < 46 or declared_end > len(data) or any(data[declared_end:]):
        _fail("metafile_invalid_records", "WMF声明长度与原图记录不一致，未生成预览。")
    bbox = struct.unpack_from("<4h", data, 6)
    width, height = _bbox_size(bbox)
    inch = struct.unpack_from("<H", data, 14)[0]
    if inch == 0:
        _fail("metafile_invalid_dimensions", "WMF分辨率为零，未生成预览。")
    offset, count = 40, 0
    ended = False
    while offset < declared_end:
        if offset + 6 > declared_end:
            _fail("metafile_invalid_records", "WMF记录头不完整，未生成预览。")
        words, function = struct.unpack_from("<IH", data, offset)
        size = words * 2
        count += 1
        if size < 6 or offset + size > declared_end or count > MAX_RECORDS:
            _fail("metafile_invalid_records", "WMF记录长度或数量异常，未生成预览。")
        # Obsolete abort-procedure escapes must never reach the native renderer.
        if function == 0x0626 and size >= 8 and struct.unpack_from("<H", data, offset + 6)[0] == 9:
            _fail("metafile_unsafe_record", "WMF含不允许执行的旧式控制记录，未生成预览。")
        offset += size
        if function == 0:
            if size != 6 or offset != declared_end:
                _fail("metafile_invalid_records", "WMF结束记录位置异常，未生成预览。")
            ended = True
    if not ended:
        _fail("metafile_invalid_records", "WMF缺少结束记录，未生成预览。")
    return bbox, (width * DEFAULT_DPI / inch, height * DEFAULT_DPI / inch)


def _emf_header(data):
    if len(data) < 88 or data[:4] != b"\x01\x00\x00\x00" or data[40:44] != b" EMF":
        _fail("metafile_invalid_header", "未检测到受支持的EMF文件头，未猜测图片格式。")
    header_size = struct.unpack_from("<I", data, 4)[0]
    declared_end, record_count = struct.unpack_from("<II", data, 48)
    if (
        header_size < 88 or header_size % 4 or header_size > declared_end
        or declared_end > len(data) or record_count < 2 or record_count > MAX_RECORDS
        or any(data[declared_end:])
    ):
        _fail("metafile_invalid_records", "EMF声明长度或记录数量异常，未生成预览。")
    bbox = struct.unpack_from("<4i", data, 8)
    _bbox_size(bbox)
    frame = struct.unpack_from("<4i", data, 24)
    frame_width, frame_height = _bbox_size(frame)
    offset, count, plus_count, last_type, has_plus = 0, 0, 0, None, False
    while offset < declared_end:
        if offset + 8 > declared_end:
            _fail("metafile_invalid_records", "EMF记录头不完整，未生成预览。")
        record_type, size = struct.unpack_from("<II", data, offset)
        count += 1
        if size < 8 or size % 4 or offset + size > declared_end or count + plus_count > MAX_RECORDS:
            _fail("metafile_invalid_records", "EMF记录边界异常，未生成预览。")
        if (record_type == 1 and offset != 0) or (record_type == 14 and offset + size != declared_end):
            _fail("metafile_invalid_records", "EMF开始或结束记录位置异常，未生成预览。")
        if record_type == 70:
            if size < 12 or struct.unpack_from("<I", data, offset + 8)[0] > size - 12:
                _fail("metafile_invalid_records", "EMF扩展注释记录长度异常，未生成预览。")
            if size >= 16 and data[offset + 12:offset + 16] == b"EMF+":
                has_plus = True
                plus_offset = offset + 16
                plus_end = offset + 12 + struct.unpack_from("<I", data, offset + 8)[0]
                if plus_offset >= plus_end:
                    _fail("metafile_invalid_records", "EMF+扩展缺少完整绘图记录，未生成预览。")
                while plus_offset < plus_end:
                    if plus_offset + 12 > plus_end:
                        _fail("metafile_invalid_records", "EMF+扩展记录头不完整，未生成预览。")
                    _, _, plus_size, payload_size = struct.unpack_from("<HHII", data, plus_offset)
                    plus_count += 1
                    if (
                        plus_size < 12 or plus_size % 4 or plus_offset + plus_size > plus_end
                        or payload_size > plus_size - 12 or plus_count + count > MAX_RECORDS
                    ):
                        _fail("metafile_invalid_records", "EMF+扩展记录长度或数量异常，未生成预览。")
                    plus_offset += plus_size
        offset += size
        last_type = record_type
    if count != record_count or last_type != 14:
        _fail("metafile_invalid_records", "EMF记录数量或结束记录不匹配，未生成预览。")
    return bbox, (frame_width * DEFAULT_DPI / 2540, frame_height * DEFAULT_DPI / 2540), has_plus


def _render_size(natural_size, display_size):
    if display_size is not None:
        if (
            not isinstance(display_size, (tuple, list)) or len(display_size) != 2
            or any(type(value) is not int or value <= 0 for value in display_size)
        ):
            _fail("metafile_invalid_dimensions", "预览显示尺寸须为两个正整数像素值。")
        size = tuple(display_size)
    else:
        if any(not math.isfinite(value) or value <= 0 for value in natural_size):
            _fail("metafile_invalid_dimensions", "旧式图片的自然显示尺寸无效。")
        scale = min(1.0, DEFAULT_MAX_DIMENSION / max(natural_size))
        size = tuple(max(1, round(value * scale)) for value in natural_size)
    if max(size) > MAX_DIMENSION or size[0] * size[1] > MAX_PIXELS:
        _fail("metafile_preview_too_large", "旧式图片预览尺寸超过本地安全限制。")
    return size


def _native_rasterize(data, size, bbox):
    renderer = getattr(Image.core, "drawwmf", None)
    if renderer is None:
        _fail("metafile_renderer_unavailable", "当前系统缺少Windows旧式图片本地解码支持。")
    pixels = renderer(data, size, bbox)
    return Image.frombytes("RGB", size, pixels, "raw", "BGR", (size[0] * 3 + 3) & -4, -1)


class _StartupInput(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("callback", ctypes.c_void_p),
                ("suppress_background_thread", ctypes.c_int32), ("suppress_external_codecs", ctypes.c_int32)]


class _BitmapData(ctypes.Structure):
    _fields_ = [("width", ctypes.c_uint32), ("height", ctypes.c_uint32),
                ("stride", ctypes.c_int32), ("pixel_format", ctypes.c_int32),
                ("scan0", ctypes.c_void_p), ("reserved", ctypes.c_size_t)]


class _WindowsGdiPlusApi:
    """One call's handles only; no registered Pillow handler or shared token."""

    def __init__(self):
        loader = getattr(ctypes, "WinDLL", None)
        if loader is None:
            _fail("metafile_renderer_unavailable", "当前系统缺少Windows EMF+本地解码支持。")
        # Search only Windows' system directory, never the cwd or PATH.
        self.gdip = loader("gdiplus.dll", winmode=0x00000800)
        self.streams = loader("shlwapi.dll", winmode=0x00000800)
        pointer, integer, unsigned = ctypes.c_void_p, ctypes.c_int32, ctypes.c_uint32
        out_pointer = ctypes.POINTER(pointer)
        self._bind(self.gdip, "GdiplusStartup", [ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(_StartupInput), pointer])
        self._bind(self.gdip, "GdiplusShutdown", [ctypes.c_size_t], None)
        self._bind(self.streams, "SHCreateMemStream", [pointer, unsigned], pointer)
        self._bind(self.gdip, "GdipLoadImageFromStream", [pointer, out_pointer])
        self._bind(self.gdip, "GdipCreateBitmapFromScan0", [integer, integer, integer, integer, pointer, out_pointer])
        self._bind(self.gdip, "GdipGetImageGraphicsContext", [pointer, out_pointer])
        self._bind(self.gdip, "GdipGraphicsClear", [pointer, unsigned])
        self._bind(self.gdip, "GdipDrawImageRectI", [pointer, pointer, integer, integer, integer, integer])
        self._bind(self.gdip, "GdipBitmapLockBits", [pointer, pointer, unsigned, integer, ctypes.POINTER(_BitmapData)])
        self._bind(self.gdip, "GdipBitmapUnlockBits", [pointer, ctypes.POINTER(_BitmapData)])
        self._bind(self.gdip, "GdipDeleteGraphics", [pointer])
        self._bind(self.gdip, "GdipDisposeImage", [pointer])

    @staticmethod
    def _bind(library, name, arguments, result=ctypes.c_int32):
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result

    @staticmethod
    def _check(status):
        if status != 0:
            raise OSError("Windows image renderer failed")

    def startup(self):
        token = ctypes.c_size_t()
        options = _StartupInput(1, None, 0, 1)
        self._check(self.gdip.GdiplusStartup(ctypes.byref(token), ctypes.byref(options), None))
        return token.value

    def shutdown(self, token):
        self.gdip.GdiplusShutdown(token)

    def stream(self, data):
        buffer = ctypes.create_string_buffer(data)
        stream = self.streams.SHCreateMemStream(buffer, len(data))
        if not stream:
            raise OSError("Windows image stream allocation failed")
        # SHCreateMemStream copies the input; the Python buffer can now expire.
        return stream

    @staticmethod
    def release_stream(stream):
        vtable = ctypes.cast(stream, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
        release(stream)

    def load_image(self, stream):
        image = ctypes.c_void_p()
        self._check(self.gdip.GdipLoadImageFromStream(stream, ctypes.byref(image)))
        return image.value

    def bitmap(self, size):
        bitmap = ctypes.c_void_p()
        self._check(self.gdip.GdipCreateBitmapFromScan0(*size, 0, 0x26200A, None, ctypes.byref(bitmap)))
        return bitmap.value

    def graphics(self, bitmap):
        graphics = ctypes.c_void_p()
        self._check(self.gdip.GdipGetImageGraphicsContext(bitmap, ctypes.byref(graphics)))
        return graphics.value

    def draw(self, graphics, image, size):
        self._check(self.gdip.GdipGraphicsClear(graphics, 0xFFFFFFFF))
        self._check(self.gdip.GdipDrawImageRectI(graphics, image, 0, 0, *size))

    def read_bitmap(self, bitmap, size):
        rectangle = (ctypes.c_int32 * 4)(0, 0, *size)
        locked = _BitmapData()
        self._check(self.gdip.GdipBitmapLockBits(bitmap, rectangle, 1, 0x26200A, ctypes.byref(locked)))
        try:
            if (
                (locked.width, locked.height) != size or not locked.scan0
                or locked.pixel_format != 0x26200A or abs(locked.stride) != size[0] * 4
            ):
                raise OSError("Windows image pixel layout invalid")
            pixels = b"".join(ctypes.string_at(locked.scan0 + row * locked.stride, size[0] * 4)
                              for row in range(size[1]))
            with Image.frombytes("RGBA", size, pixels, "raw", "BGRA") as image:
                return image.convert("RGB")
        finally:
            self._check(self.gdip.GdipBitmapUnlockBits(bitmap, ctypes.byref(locked)))

    def dispose_image(self, image):
        self._check(self.gdip.GdipDisposeImage(image))

    def dispose_graphics(self, graphics):
        self._check(self.gdip.GdipDeleteGraphics(graphics))


def _gdiplus_rasterize(data, size):
    api = _WindowsGdiPlusApi()
    # ExitStack runs every earlier cleanup even if a later cleanup raises.
    with ExitStack() as cleanup:
        token = api.startup()
        cleanup.callback(api.shutdown, token)
        stream = api.stream(data)
        cleanup.callback(api.release_stream, stream)
        source = api.load_image(stream)
        cleanup.callback(api.dispose_image, source)
        bitmap = api.bitmap(size)
        cleanup.callback(api.dispose_image, bitmap)
        graphics = api.graphics(bitmap)
        cleanup.callback(api.dispose_graphics, graphics)
        api.draw(graphics, source, size)
        return api.read_bitmap(bitmap, size)


def render_word_metafile(data: bytes, *, mime_type: str, display_size=None) -> dict:
    """Return an immutable-byte PNG preview plus original/derived provenance.

    ``display_size`` is an optional exact output size in pixels, from known
    source display dimensions, not a guessed page size.  The default uses the
    format's physical frame at 192 dpi, proportionally capped at 2048 pixels.
    WMF is deliberately blocked: real-source QA found unreliable font placement
    and missing legacy symbols.  Unknown formats, invalid records and blank
    renders also fail explicitly.  EMF+ uses Windows GDI+ through an in-memory
    stream because classic GDI can silently omit its main drawing.  Neither
    strategy is chemical verification.
    Calls are serialized; the small process-local cache has no disk side effects.
    """
    global _cache_bytes
    if not isinstance(data, bytes) or not data or len(data) > MAX_SOURCE_BYTES:
        _fail("metafile_invalid_bytes", "旧式图片字节为空、类型不符或超过32MB限制。")
    if not isinstance(mime_type, str) or mime_type.lower().strip() not in _MIME_FORMATS:
        _fail("metafile_unsupported_format", "此图片未声明为受支持的WMF或EMF格式，未尝试猜测。")
    normalized_mime = mime_type.lower().strip()
    kind = _MIME_FORMATS[normalized_mime]
    if kind == "wmf":
        bbox, natural_size = _wmf_header(data)
        has_plus = False
    else:
        bbox, natural_size, has_plus = _emf_header(data)
    size = _render_size(natural_size, display_size)
    if kind == "wmf":
        _fail(
            "metafile_wmf_rendering_unverified",
            "此WMF的旧字体或坐标映射尚未可靠验证，可能出现缺字或错位；未生成预览或备课图片。",
        )
    original_sha = hashlib.sha256(data).hexdigest()
    key = (original_sha, mime_type, size, RENDERER_REVISION)
    with _LOCK:
        if key in _CACHE:
            _CACHE.move_to_end(key)
            return dict(_CACHE[key])
        try:
            with (_gdiplus_rasterize(data, size) if has_plus else _native_rasterize(data, size, bbox)) as raster:
                if raster.size != size or raster.mode != "RGB":
                    _fail("metafile_decode_failed", "旧式图片解码结果尺寸或颜色模式异常。")
                if all(low == high for low, high in raster.getextrema()):
                    _fail("metafile_blank_preview", "旧式图片解码得到空白或单色画面，未将其当作成功预览。")
                output = io.BytesIO()
                raster.save(output, format="PNG")
                png = output.getvalue()
        except WordMetafilePreviewError:
            raise
        except (OSError, ValueError, TypeError, OverflowError, MemoryError, RuntimeError, SystemError) as exc:
            raise WordMetafilePreviewError(
                "metafile_decode_failed", "旧式图片本地解码失败；原图仍保留，请核对原Word。"
            ) from exc
        if len(png) > MAX_PNG_BYTES:
            _fail("metafile_preview_too_large", "生成的旧式图片预览超过32MB限制。")
        result = {
            "bytes": png,
            "mime_type": "image/png",
            "original_mime_type": mime_type,
            "original_sha256": original_sha,
            "preview_sha256": hashlib.sha256(png).hexdigest(),
            "derived_preview": True,
            "renderer_revision": RENDERER_REVISION,
            "width": size[0],
            "height": size[1],
            "conversion_note": (
                f"由原{kind.upper()}字节经{'Windows GDI+' if has_plus else 'Windows GDI'}在本机转换为PNG预览；原图保留。"
                "仅作图像显示，未进行OCR、化学内容识别或完整性审定；细小符号请结合原教案核对。"
            ),
        }
        if len(png) <= _CACHE_MAX_BYTES:
            _CACHE[key] = dict(result)
            _cache_bytes += len(png)
            while len(_CACHE) > _CACHE_MAX_ENTRIES or _cache_bytes > _CACHE_MAX_BYTES:
                _, removed = _CACHE.popitem(last=False)
                _cache_bytes -= len(removed["bytes"])
        return result

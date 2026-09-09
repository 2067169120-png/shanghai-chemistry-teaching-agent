from __future__ import annotations

import hashlib
import io
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from integrations.deeptutor_shchem_v1 import desktop_word_metafile_preview as module


def _wmf(*, bbox=(0, 0, 200, 100), inch=1440, extra_record=b""):
    placeable = struct.pack("<IH4hHI", 0x9AC6CDD7, 0, *bbox, inch, 0)
    checksum = 0
    for word in struct.unpack("<10H", placeable):
        checksum ^= word
    placeable += struct.pack("<H", checksum)

    def record(function, *args):
        return struct.pack("<IH", 3 + len(args), function) + struct.pack("<" + "h" * len(args), *args)

    records = [record(0x020B, 0, 0), record(0x020C, 100, 200),
               record(0x0214, 10, 10), record(0x0213, 90, 190)]
    if extra_record:
        records.append(extra_record)
    records.append(record(0))
    body = b"".join(records)
    return placeable + struct.pack("<HHHIHIH", 1, 9, 0x300, 9 + len(body) // 2, 0,
                                   max(len(record) for record in records) // 2, 0) + body


def _emf(*, bbox=(0, 0, 200, 100), frame=(0, 0, 5292, 2646), padding=b""):
    records = struct.pack("<IIii", 27, 16, 10, 10) + struct.pack("<IIii", 54, 16, 190, 90)
    records += struct.pack("<IIIII", 14, 20, 0, 0, 20)
    header = struct.pack("<II4i4iIIIIHHIII4i", 1, 88, *bbox, *frame,
                         0x464D4520, 0x10000, 88 + len(records), 4, 1, 0,
                         0, 0, 0, 1920, 1080, 508, 286)
    return header + records + padding


@pytest.fixture(autouse=True)
def clear_cache():
    with module._LOCK:
        module._CACHE.clear()
        module._cache_bytes = 0
    yield
    with module._LOCK:
        module._CACHE.clear()
        module._cache_bytes = 0


def _fake_raster(data, size, bbox):
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).line((0, 0, size[0] - 1, size[1] - 1), fill="black")
    return image


@pytest.mark.parametrize("mime", ["image/emf", "image/x-emf"])
def test_can_attempt_is_metadata_only_not_success(mime):
    assert module.can_attempt_metafile({"mime_type": mime})
    assert module.can_attempt_metafile({"mime_type": mime, "bytes_count": 123})


@pytest.mark.parametrize("asset", [None, {}, {"mime_type": "image/png"},
    {"mime_type": "application/octet-stream"}, {"mime_type": "image/wmf", "bytes_count": True},
    {"mime_type": "image/wmf"}, {"mime_type": "image/x-wmf", "bytes_count": 123},
    {"mime_type": "image/wmf", "bytes_count": 0},
    {"mime_type": "image/emf", "bytes_count": module.MAX_SOURCE_BYTES + 1}])
def test_can_attempt_refuses_unsupported_metadata(asset):
    assert not module.can_attempt_metafile(asset)


@pytest.mark.parametrize("source,mime", [(_emf(), "image/x-emf")])
def test_source_bound_png_metadata_and_no_global_handler_change(monkeypatch, source, mime):
    from PIL import WmfImagePlugin
    handler_before = WmfImagePlugin._handler
    monkeypatch.setattr(module, "_native_rasterize", _fake_raster)
    original = bytes(source)
    result = module.render_word_metafile(source, mime_type=mime, display_size=(400, 200))
    assert result["original_sha256"] == hashlib.sha256(source).hexdigest()
    assert result["preview_sha256"] == hashlib.sha256(result["bytes"]).hexdigest()
    assert result["original_mime_type"] == mime
    assert result["mime_type"] == "image/png" and result["derived_preview"] is True
    assert result["renderer_revision"] == module.RENDERER_REVISION
    assert (result["width"], result["height"]) == (400, 200)
    with Image.open(io.BytesIO(result["bytes"])) as raster:
        assert raster.format == "PNG" and raster.size == (400, 200)
    assert "未进行OCR" in result["conversion_note"]
    assert source == original and WmfImagePlugin._handler is handler_before


@pytest.mark.parametrize("source,mime", [(_wmf(), "image/emf"), (_emf(), "image/wmf"),
                                       (b"not a metafile", "image/wmf")])
def test_mime_header_mismatch_never_guesses_or_decodes(monkeypatch, source, mime):
    monkeypatch.setattr(module, "_native_rasterize", lambda *args: pytest.fail("unexpected decode"))
    with pytest.raises(module.WordMetafilePreviewError, match="未猜测"):
        module.render_word_metafile(source, mime_type=mime)


@pytest.mark.parametrize("source", [b"", bytearray(_wmf()), None])
def test_invalid_bytes(source):
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(source, mime_type="image/wmf")
    assert exc.value.code == "metafile_invalid_bytes"


def test_source_size_and_unknown_mime(monkeypatch):
    monkeypatch.setattr(module, "MAX_SOURCE_BYTES", 5)
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(_wmf(), mime_type="image/wmf")
    assert exc.value.code == "metafile_invalid_bytes"
    monkeypatch.setattr(module, "MAX_SOURCE_BYTES", 1024)
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(_wmf(), mime_type="application/octet-stream")
    assert exc.value.code == "metafile_unsupported_format"


@pytest.mark.parametrize("size", [(0, 5), (-1, 5), (True, 4), (3.5, 2), (float("nan"), 2),
                                  (3,), "500x300", (9000, 2), (5000, 5000)])
def test_bad_sizes_do_not_reach_native(monkeypatch, size):
    monkeypatch.setattr(module, "_native_rasterize", lambda *args: pytest.fail("unexpected decode"))
    with pytest.raises(module.WordMetafilePreviewError):
        module.render_word_metafile(_wmf(), mime_type="image/wmf", display_size=size)


@pytest.mark.parametrize("source,mime", [(_wmf(bbox=(0, 0, 0, 100)), "image/wmf"),
    (_wmf(inch=0), "image/wmf"), (_emf(frame=(0, 0, 0, 5)), "image/emf"),
    (_emf(bbox=(20, 20, 0, 0)), "image/emf")])
def test_invalid_source_dimensions(source, mime):
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(source, mime_type=mime)
    assert exc.value.code == "metafile_invalid_dimensions"


@pytest.mark.parametrize("source,mime", [(_wmf()[:-2], "image/wmf"),
    (_wmf() + b"bad", "image/wmf"), (_emf()[:-4], "image/emf"), (_emf(padding=b"nonzero"), "image/emf")])
def test_invalid_declared_lengths_and_trailing_data(source, mime):
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(source, mime_type=mime)
    assert exc.value.code == "metafile_invalid_records"


def test_real_emf_style_zero_padding_is_hash_bound(monkeypatch):
    monkeypatch.setattr(module, "_native_rasterize", _fake_raster)
    source = _emf(padding=b"\0" * 500)
    result = module.render_word_metafile(source, mime_type="image/emf")
    assert result["original_sha256"] == hashlib.sha256(source).hexdigest()


@pytest.mark.parametrize("kind,offset,value", [("wmf", 40, 0), ("emf", 92, 0),
                                              ("emf", 52, 50), ("emf", 88, 14)])
def test_malformed_record_bounds_and_counts(kind, offset, value):
    source = bytearray(_wmf() if kind == "wmf" else _emf())
    struct.pack_into("<I", source, offset, value)
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(bytes(source), mime_type="image/" + kind)
    assert exc.value.code == "metafile_invalid_records"


def test_invalid_wmf_checksum():
    source = bytearray(_wmf())
    source[20] ^= 1
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(bytes(source), mime_type="image/wmf")
    assert exc.value.code == "metafile_invalid_header"


def test_obsolete_abort_escape_is_not_executed(monkeypatch):
    monkeypatch.setattr(module, "_native_rasterize", lambda *args: pytest.fail("unsafe decode"))
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(_wmf(extra_record=struct.pack("<IHH", 4, 0x0626, 9)), mime_type="image/wmf")
    assert exc.value.code == "metafile_unsafe_record"


def test_missing_native_renderer_has_explicit_error(monkeypatch):
    monkeypatch.delattr(Image.core, "drawwmf", raising=False)
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(_emf(), mime_type="image/emf")
    assert exc.value.code == "metafile_renderer_unavailable"


@pytest.mark.parametrize("color", ["white", "black", "red"])
def test_uniform_native_output_is_not_success(monkeypatch, color):
    monkeypatch.setattr(module, "_native_rasterize", lambda data, size, bbox: Image.new("RGB", size, color))
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(_emf(), mime_type="image/emf")
    assert exc.value.code == "metafile_blank_preview" and not module._CACHE


def test_native_failure_is_safe_and_not_cached(monkeypatch):
    def fail(*args):
        raise OSError("private-file-path")
    monkeypatch.setattr(module, "_native_rasterize", fail)
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(_emf(), mime_type="image/emf")
    assert exc.value.code == "metafile_decode_failed"
    assert "private-file-path" not in str(exc.value) and not module._CACHE


def test_cache_is_bounded_and_metadata_is_independent(monkeypatch):
    calls = []
    def render(*args):
        calls.append(1)
        return _fake_raster(*args)
    monkeypatch.setattr(module, "_native_rasterize", render)
    monkeypatch.setattr(module, "_CACHE_MAX_ENTRIES", 2)
    source = _emf()
    first = module.render_word_metafile(source, mime_type="image/emf", display_size=(100, 50))
    first["width"] = 999
    again = module.render_word_metafile(source, mime_type="image/emf", display_size=(100, 50))
    assert again["width"] == 100 and len(calls) == 1
    for size in [(102, 51), (104, 52)]:
        module.render_word_metafile(source, mime_type="image/emf", display_size=size)
    assert len(module._CACHE) == 2
    assert module._cache_bytes == sum(len(value["bytes"]) for value in module._CACHE.values())
    assert module._cache_bytes <= module._CACHE_MAX_BYTES


def test_native_calls_are_serialized(monkeypatch):
    running = []
    state_lock = threading.Lock()
    counts = {"active": 0}
    def render(*args):
        with state_lock:
            counts["active"] += 1
            running.append(counts["active"])
        time.sleep(0.01)
        image = _fake_raster(*args)
        with state_lock:
            counts["active"] -= 1
        return image
    monkeypatch.setattr(module, "_native_rasterize", render)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda n: module.render_word_metafile(
            _emf(), mime_type="image/emf", display_size=(100 + n, 50)), range(8)))
    assert len(results) == 8 and max(running) == 1


@pytest.mark.skipif(not hasattr(Image.core, "drawwmf"), reason="Windows Pillow native renderer unavailable")
@pytest.mark.parametrize("source,mime", [(_emf(), "image/emf")])
def test_windows_native_synthetic_metafiles_are_nonblank(source, mime):
    result = module.render_word_metafile(source, mime_type=mime, display_size=(400, 200))
    with Image.open(io.BytesIO(result["bytes"])) as image:
        assert any(low != high for low, high in image.getextrema())


@pytest.mark.parametrize("mime", ["image/wmf", "image/x-wmf"])
def test_valid_wmf_is_explicitly_blocked_after_real_visual_qa(monkeypatch, mime):
    monkeypatch.setattr(module, "_native_rasterize", lambda *args: pytest.fail("unsafe WMF preview"))
    source = _wmf()
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(source, mime_type=mime)
    assert exc.value.code == "metafile_wmf_rendering_unverified"
    assert "缺字或错位" in exc.value.message_zh and not module._CACHE
    assert source == _wmf()


def _emf_plus():
    source = bytearray(_emf())
    plus_record = struct.pack("<HHII", 0x4002, 0, 12, 0)
    record = struct.pack("<III4s", 70, 16 + len(plus_record), 4 + len(plus_record), b"EMF+") + plus_record
    source[88:88] = record
    struct.pack_into("<II", source, 48, len(source), 5)
    return bytes(source)


def test_emf_plus_never_reaches_classic_renderer(monkeypatch):
    monkeypatch.setattr(module, "_native_rasterize", lambda *args: pytest.fail("incomplete EMF+ preview"))
    calls = []
    def plus_renderer(data, size):
        calls.append(data)
        return _fake_raster(data, size, None)
    monkeypatch.setattr(module, "_gdiplus_rasterize", plus_renderer)
    result = module.render_word_metafile(_emf_plus(), mime_type="image/emf")
    assert calls == [_emf_plus()] and "GDI+" in result["conversion_note"]


def test_invalid_emf_comment_length_is_rejected():
    source = bytearray(_emf())
    source[88:88] = struct.pack("<III4s", 70, 16, 90, b"test")
    struct.pack_into("<II", source, 48, len(source), 5)
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(bytes(source), mime_type="image/emf")
    assert exc.value.code == "metafile_invalid_records"


@pytest.mark.parametrize("offset,value", [(108, 0), (108, 1000), (112, 1000)])
def test_invalid_nested_emf_plus_boundaries_never_reach_native(monkeypatch, offset, value):
    source = bytearray(_emf_plus())
    struct.pack_into("<I", source, offset, value)
    monkeypatch.setattr(module, "_gdiplus_rasterize", lambda *args: pytest.fail("invalid EMF+ decode"))
    with pytest.raises(module.WordMetafilePreviewError) as exc:
        module.render_word_metafile(bytes(source), mime_type="image/emf")
    assert exc.value.code == "metafile_invalid_records"


def test_gdiplus_dll_search_is_system32_only(monkeypatch):
    requests = []
    class Library:
        def __init__(self):
            self.functions = {}
        def __getattr__(self, name):
            return self.functions.setdefault(name, SimpleNamespace())
    def loader(name, **kwargs):
        requests.append((name, kwargs))
        return Library()
    monkeypatch.setattr(module.ctypes, "WinDLL", loader, raising=False)
    module._WindowsGdiPlusApi()
    assert requests == [("gdiplus.dll", {"winmode": 0x800}), ("shlwapi.dll", {"winmode": 0x800})]


@pytest.mark.parametrize("failure", [None, "startup", "stream", "load_image", "bitmap", "graphics", "draw", "read_bitmap"])
def test_gdiplus_resources_cleanup_on_each_failure(monkeypatch, failure):
    calls = []
    class FakeApi:
        def step(self, name, value=None):
            calls.append(name)
            if name == failure:
                raise OSError("fake native failure")
            return value
        def startup(self): return self.step("startup", "token")
        def stream(self, data): return self.step("stream", "stream-handle")
        def load_image(self, stream): return self.step("load_image", "source-handle")
        def bitmap(self, size): return self.step("bitmap", "bitmap-handle")
        def graphics(self, bitmap): return self.step("graphics", "graphics-handle")
        def draw(self, *args): self.step("draw")
        def read_bitmap(self, bitmap, size):
            self.step("read_bitmap")
            return _fake_raster(None, size, None)
        def dispose_graphics(self, graphics): self.step("dispose_graphics")
        def dispose_image(self, image): self.step("dispose_" + image)
        def release_stream(self, stream): self.step("release_stream")
        def shutdown(self, token): self.step("shutdown")
    monkeypatch.setattr(module, "_WindowsGdiPlusApi", FakeApi)
    if failure:
        with pytest.raises(OSError):
            module._gdiplus_rasterize(b"source", (20, 10))
    else:
        with module._gdiplus_rasterize(b"source", (20, 10)) as image:
            assert image.size == (20, 10)
    stages = ["startup", "stream", "load_image", "bitmap", "graphics", "draw", "read_bitmap"]
    finished = len(stages) if failure is None else stages.index(failure)
    expected_cleanup = []
    if finished >= 5: expected_cleanup.append("dispose_graphics")
    if finished >= 4: expected_cleanup.append("dispose_bitmap-handle")
    if finished >= 3: expected_cleanup.append("dispose_source-handle")
    if finished >= 2: expected_cleanup.append("release_stream")
    if finished >= 1: expected_cleanup.append("shutdown")
    assert calls == stages[:finished + (failure is not None)] + expected_cleanup


def test_cleanup_failure_still_releases_other_native_resources(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        startup=lambda: 1, stream=lambda data: 2, load_image=lambda stream: 3,
        bitmap=lambda size: 4, graphics=lambda bitmap: 5, draw=lambda *args: None,
        read_bitmap=lambda bitmap, size: _fake_raster(None, size, None),
        dispose_image=lambda image: calls.append(image),
        release_stream=lambda stream: calls.append(stream), shutdown=lambda token: calls.append(token),
    )
    def fail_graphics(graphics):
        calls.append(graphics)
        raise OSError("failed cleanup")
    fake.dispose_graphics = fail_graphics
    monkeypatch.setattr(module, "_WindowsGdiPlusApi", lambda: fake)
    with pytest.raises(OSError):
        module._gdiplus_rasterize(b"source", (20, 10))
    assert calls == [5, 4, 3, 2, 1]


def test_gdiplus_locked_bitmap_releases_lock_even_when_layout_invalid():
    calls = []
    api = object.__new__(module._WindowsGdiPlusApi)
    def lock(bitmap, rectangle, flags, pixel_format, output):
        calls.append("lock")
        output._obj.width = 1
        output._obj.height = 1
        output._obj.scan0 = None
        return 0
    def unlock(bitmap, output):
        calls.append("unlock")
        return 0
    api.gdip = SimpleNamespace(GdipBitmapLockBits=lock, GdipBitmapUnlockBits=unlock)
    with pytest.raises(OSError):
        api.read_bitmap(1, (1, 1))
    assert calls == ["lock", "unlock"]


@pytest.mark.skipif(not hasattr(module.ctypes, "WinDLL"), reason="Windows GDI+ unavailable")
def test_gdiplus_real_memory_stream_draws_synthetic_emf():
    with module._gdiplus_rasterize(_emf(), (200, 100)) as image:
        assert image.size == (200, 100) and any(low != high for low, high in image.getextrema())

"""Read a verified local PDF snapshot and render one bounded page at a time."""
from __future__ import annotations

import hashlib
from io import BytesIO
import math
from pathlib import Path

from .reader_cancellation import check_read_cancelled


class FrozenPdfPages:
    MAX_BYTES = 128 * 1024 * 1024

    def __init__(self, report):
        self.path = Path(report["path"])
        self.sha256 = report.get("pdf_sha256")
        if (not isinstance(self.sha256, str) or len(self.sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.sha256)):
            raise ValueError("预览缺少文件核对信息，请重新生成。")
        self._bytes = self._checked_bytes()
        import pypdfium2 as pdfium
        from .desktop_local_pagination import _PDF_LOCK
        with _PDF_LOCK, pdfium.PdfDocument(self._bytes) as document:
            self.count = len(document)
        if not 1 <= self.count <= 2000:
            raise ValueError("预览页数无效或超出本地阅读范围。")
        check_read_cancelled()

    def _checked_bytes(self):
        check_read_cancelled()
        with self.path.open("rb") as stream:
            raw = stream.read(self.MAX_BYTES + 1)
        if len(raw) > self.MAX_BYTES or hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("原预览文件已变化或超出阅读大小，请重新生成。")
        check_read_cancelled()
        return raw

    def verify_file(self):
        self._checked_bytes()

    def render(self, number, *, pixel_ratio=1.0):
        check_read_cancelled()
        if type(number) is not int or not 1 <= number <= self.count:
            raise ValueError("该页不在本次文档中。")
        if (isinstance(pixel_ratio, bool) or not isinstance(pixel_ratio, (int, float))
                or not math.isfinite(pixel_ratio) or not 1 <= pixel_ratio <= 4):
            raise ValueError("预览缩放倍率无效。")
        import pypdfium2 as pdfium
        from .desktop_local_pagination import _PDF_LOCK
        with _PDF_LOCK, pdfium.PdfDocument(self._bytes) as document:
            page = document[number - 1]
            try:
                width, height = page.get_size()
                if not math.isfinite(width * height) or min(width, height) <= 0:
                    raise ValueError("本页尺寸无法读取。")
                scale = min(1.5 * pixel_ratio, math.sqrt(4_000_000 / (width * height)), 4096 / max(width, height))
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil()
                    try:
                        target = BytesIO()
                        image.save(target, format="PNG")
                        result = {"data": target.getvalue(), "width": image.width, "height": image.height,
                                  "content_type": "image/png"}
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
        result["sha256"] = hashlib.sha256(result["data"]).hexdigest()
        check_read_cancelled()
        return result

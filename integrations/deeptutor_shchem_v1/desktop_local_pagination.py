"""Local DOCX pagination without a developer-specific runtime directory.

Use installed LibreOffice, or installed Microsoft Word on Windows. PDFium
rasterizes the emitted PDF; the caller retains existing frozen-page validation.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from threading import RLock

# PDFium itself is not thread-safe. Only rasterization needs this process lock.
_PDF_LOCK = RLock()


def find_libreoffice() -> Path | None:
    for name in ("soffice", "libreoffice"):
        candidate = shutil.which(name)
        if candidate:
            return Path(candidate)
    for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
        if base:
            candidate = Path(base) / "LibreOffice" / "program" / "soffice.exe"
            if candidate.is_file():
                return candidate
    return None


def render_docx(docx_path, output_dir, *, toolchain=None):
    from .desktop_mixed_paper_pagination import MixedPaperPaginationError
    from .reader_cancellation import check_read_cancelled

    check_read_cancelled()
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise MixedPaperPaginationError("local_pagination_dependency_missing",
            "请更新桌面依赖以安装 pypdfium2，然后重试分页预览。") from None
    docx = Path(docx_path).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    pdf = output / (docx.stem + ".pdf")
    office = find_libreoffice()
    if office is not None:
        with tempfile.TemporaryDirectory(prefix="shchem-office-") as profile:
            command = [str(office), "-env:UserInstallation=" + Path(profile).as_uri(),
                       "--headless", "--convert-to", "pdf:writer_pdf_Export",
                       "--outdir", str(output), str(docx)]
            try:
                subprocess.run(command, check=True, capture_output=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                raise MixedPaperPaginationError("local_pagination_conversion_failed",
                    "LibreOffice 未完成转换。请确认原文档可打开，关闭异常转换进程后重试；未生成可确认的分页。") from None
        backend = "libreoffice"
    elif os.name == "nt":
        from .paper_export_renderer import _word_com_pdf
        try:
            _word_com_pdf(docx, pdf)
        except Exception:
            raise MixedPaperPaginationError("local_pagination_office_missing",
                "试卷真实分页需要本机 Microsoft Word 或 LibreOffice。请安装其中一种后重新预览，题篮保持不变。") from None
        backend = "word_com"
    else:
        raise MixedPaperPaginationError("local_pagination_office_missing",
            "请安装 LibreOffice 后重试真实分页预览，题篮保持不变。")
    check_read_cancelled()
    if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
        raise MixedPaperPaginationError("local_pagination_pdf_missing", "转换未生成有效 PDF，请检查文档转换工具。")
    dpi = 180
    pages = []
    with _PDF_LOCK:
        document = pdfium.PdfDocument(str(pdf))
        try:
            if not 1 <= len(document) <= 500:
                raise MixedPaperPaginationError("pagination_page_count_invalid", "试卷页数为空或超出范围。")
            for index in range(len(document)):
                check_read_cancelled()
                page = document[index]
                bitmap = None
                try:
                    bitmap = page.render(scale=dpi / 72)
                    target = output / f"page-{index + 1}.png"
                    image = bitmap.to_pil()
                    try:
                        image.save(target)
                    finally:
                        image.close()
                    pages.append(target)
                finally:
                    if bitmap is not None:
                        bitmap.close()
                    page.close()
        finally:
            document.close()
    return pages, pdf, {"dpi": dpi, "tool": "local_office_pdfium",
                        "conversion_backend": backend, "invocation_mode": "local_native",
                        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}

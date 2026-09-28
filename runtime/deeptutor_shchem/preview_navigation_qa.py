"""Synthetic real Qt/PDF navigation, close/reopen memory and screenshot evidence."""
from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def rss_bytes():
    if sys.platform == "win32":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak_ws", "ws", "peak_paged", "paged",
                                                   "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.ws
    statm = Path("/proc/self/statm")
    return int(statm.read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE") if statm.exists() else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=6)
    args = parser.parse_args()
    if args.cycles < 3:
        raise ValueError("At least three close/reopen cycles are required")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    from PySide6.QtCore import QByteArray, QBuffer, QEvent, QIODevice, QRect, Qt
    from PySide6.QtGui import QFont, QImage, QPainter, QPdfWriter
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    from integrations.deeptutor_shchem_v1.desktop_workbench.actual_ppt_preview_dialog import ActualPptPreview
    from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPaginationDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
    from staging.coordination.deeptutor_gateway.tests.no_office_plugin import block_office_activation
    app = create_application([])
    app.setQuitOnLastWindowClosed(False)
    images, cycles = [], []

    def wait(predicate, timeout=20):
        start = time.monotonic()
        while not predicate():
            app.processEvents()
            QTest.qWait(10)
            if time.monotonic() - start > timeout:
                raise TimeoutError("The exact active page did not finish")
        app.processEvents()

    def capture(widget, name, controls):
        QTest.qWait(60)
        geometry = {}
        for key, control in controls.items():
            rect = QRect(control.mapTo(widget, control.rect().topLeft()), control.size())
            assert widget.rect().contains(rect), (name, key, rect, widget.size())
            geometry[key] = [rect.x(), rect.y(), rect.width(), rect.height()]
        shot = widget.grab()
        path = output / (name + ".png")
        assert shot.save(str(path))
        images.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                       "logical_size": [widget.width(), widget.height()],
                       "pixel_size": [shot.width(), shot.height()], "dpr": shot.devicePixelRatio(),
                       "controls": geometry})

    def cleanup(dialog):
        from shiboken6 import isValid
        if isValid(dialog):
            dialog.reject()
        app.processEvents()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(dialog), "Closed preview still retains native widgets"
        gc.collect()

    path = output / "authored-40-pages.pdf"
    writer = QPdfWriter(str(path))
    writer.setResolution(72)
    painter = QPainter(writer)
    for number in range(1, 41):
        if number > 1:
            assert writer.newPage()
        painter.setFont(QFont("Microsoft YaHei UI", 22))
        painter.drawText(35, 70, f"教学课件 · 合成第 {number} 页")
        painter.setFont(QFont("Microsoft YaHei UI", 13))
        painter.drawText(35, 115, "仅用于分页、缩放和导航验收")
        for y, title in ((180, "材料观察"), (330, "课堂讨论"), (480, "独立练习"), (630, "整理与反馈")):
            painter.drawRect(35, y, 505, 105)
            painter.drawText(55, y + 40, title)
    painter.end()
    del writer
    report = {"path": str(path), "pdf_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    with block_office_activation():
        initial_rss = rss_bytes()
        for cycle in range(args.cycles):
            start = time.monotonic()
            dialog = ActualPptPreview(report)
            dialog.show()
            wait(lambda: dialog._shown == 1)
            first_page_seconds = time.monotonic() - start
            assert dialog.count == 40 and len(dialog._cache) == 1
            peak_cache = 0
            for number in range(2, 41):
                dialog.page_selector.setValue(number)
                wait(lambda: dialog._shown == number)
                assert len(dialog._cache) <= 4 and dialog._cache.nbytes <= 64 * 1024 * 1024
                peak_cache = max(peak_cache, dialog._cache.nbytes)
            dialog.page_selector.setValue(7)
            wait(lambda: dialog._shown == 7)
            if cycle == 0:
                for width, height in ((360, 520), (420, 700), (1080, 760)):
                    dialog.resize(width, height)
                    capture(dialog, f"ppt-{width}", {"previous": dialog.prev, "next": dialog.next,
                        "page": dialog.page_selector, "zoom": dialog.zoom, "open": dialog.open_pdf,
                        "return": dialog.back, "status": dialog.caption})
            bridge = dialog.tasks
            dialog.reject()
            assert not dialog._cache and dialog._source is None and dialog._bitmap is None
            wait(lambda: not bridge._workers)
            cleanup(dialog)
            del dialog, bridge
            gc.collect()
            current = rss_bytes()
            cycles.append({"cycle": cycle + 1, "first_page_seconds": first_page_seconds,
                           "pages_visited": 41, "max_cache_bytes": peak_cache,
                           "released_cache_entries": 0, "post_close_rss": current})
            print(json.dumps(cycles[-1]), flush=True)

        documents, payloads = {}, {}
        for audience in ("student", "teacher"):
            pages = []
            for number in range(1, 41):
                image = QImage(1000, 1414, QImage.Format.Format_RGB32)
                image.fill(Qt.GlobalColor.white)
                painter = QPainter(image)
                painter.setFont(QFont("Microsoft YaHei UI", 36))
                painter.drawText(70, 110, f"{'学生版' if audience == 'student' else '教师版'} · 第 {number} 页")
                painter.setFont(QFont("Microsoft YaHei UI", 23))
                painter.drawText(70, 180, "合成预览材料 · 两版身份独立")
                for y in range(280, 1150, 220):
                    painter.drawRect(70, y, 860, 165)
                    painter.drawText(95, y + 70, "参考答案及评分说明" if audience == "teacher" else "题面、公共材料与作答区域")
                painter.end()
                data = QByteArray()
                buffer = QBuffer(data)
                buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                assert image.save(buffer, "PNG")
                raw = bytes(data)
                identity = f"{audience}-{number}"
                digest = hashlib.sha256(raw).hexdigest()
                payloads[identity] = {"data": raw, "content_type": "image/png", "sha256": digest}
                pages.append({"page_number": number, "image_id": identity,
                              "sha256": digest, "width": 1000, "height": 1414})
            documents[audience] = {"page_count": 40, "pages": pages}
        tasks = DesktopTaskBridge()
        data = {"title": "合成教学练习卷", "pagination": {"status": "rendered_pending_review",
            "manifest_sha256": "a" * 64, "documents": documents}}
        dialog = MixedPaperPaginationDialog(data, tasks, lambda key: payloads[key])
        dialog.show()
        wait(lambda: dialog._displayed_key == ("student", 1))
        for width, height in ((360, 520), (420, 700), (1000, 850)):
            dialog.resize(width, height)
            capture(dialog, f"paper-{width}", {"previous": dialog.previous_button, "next": dialog.next_button,
                "page": dialog.page_selector, "zoom": dialog.zoom, "review": dialog.review_page_button,
                "confirm": dialog.confirm_button, "return": dialog.back_button, "status": dialog.status})
        for audience in ("student", "teacher"):
            for number in range(1, 41):
                dialog._navigate((audience, number))
                wait(lambda: dialog._displayed_key == (audience, number))
                dialog.review_page_button.click()
                assert len(dialog._pixmaps) <= 4 and dialog._pixmaps.nbytes <= 64 * 1024 * 1024
        assert dialog.confirm_button.isEnabled() and len(dialog.review.reviewed) == 80
        dialog.resize(420, 700)
        capture(dialog, "paper-teacher-reviewed", {"confirm": dialog.confirm_button, "return": dialog.back_button,
                "review": dialog.review_page_button, "status": dialog.status})
        dialog._image_loader = lambda key: {**payloads[key], "sha256": "0" * 64}
        dialog._navigate(("student", 2))
        wait(lambda: bool(dialog._failures))
        assert not dialog.confirm_button.isEnabled()
        dialog.resize(360, 520)
        capture(dialog, "paper-failed", {"failure": dialog.failure_button, "return": dialog.back_button,
                                       "confirm": dialog.confirm_button, "status": dialog.status})
        cleanup(dialog)
        tasks.shutdown(1000)

    readings = [row["post_close_rss"] for row in cycles[1:] if row["post_close_rss"] is not None]
    growth = readings[-1] - min(readings) if readings else None
    memory_pass = growth is not None and growth <= 32 * 1024 * 1024
    manifest = {"synthetic_only": True, "provider_calls": 0, "office_calls": 0,
        "personal_state_accessed": False, "initial_rss": initial_rss, "cycles": cycles,
        "post_warmup_final_rss_growth": growth, "memory_tolerance_bytes": 32 * 1024 * 1024,
        "memory_pass": memory_pass, "images": images,
        "pdf_sha256": report["pdf_sha256"], "paper_pages_explicitly_reviewed": 80,
        "limitations": ["Authored PDF tests the actual PDF reader; no PPTX conversion fidelity claim.",
                        "RSS is a bounded observation across these runs, not an all-hardware memory guarantee.",
                        "Read failures are located; layout overflow still needs visual judgement."]}
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    assert memory_pass, manifest
    print(json.dumps({"manifest": str(output / "manifest.json"), "images": len(images), "memory_pass": memory_pass}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

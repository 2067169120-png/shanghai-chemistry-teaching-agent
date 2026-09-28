"""Actual raster/PDF reading, bounded caches and explicit review; no Office."""
import hashlib

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPdfWriter, QPixmap
from PySide6.QtTest import QTest

from test_desktop_mixed_paper_ui import Tasks, png_bytes
from test_word_question_dialog import _isolated_qt_app
from test_visual_mixed_paper_ui import pagination
from integrations.deeptutor_shchem_v1.desktop_preview_pdf import FrozenPdfPages
from integrations.deeptutor_shchem_v1.desktop_workbench.actual_ppt_preview_dialog import ActualPptPreview
from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import MixedPaperPaginationDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.preview_navigation import PageNavigator, PixmapCache, scaled_page


@pytest.fixture
def qt_app():
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import create_application
    with _isolated_qt_app() as app:
        create_application([])
        yield app


def paper_dialog(student=40, teacher=40):
    tasks, calls = Tasks(), []
    raw = png_bytes()
    def load(key):
        calls.append(key)
        return {"data": raw, "content_type": "image/png", "sha256": hashlib.sha256(raw).hexdigest()}
    dialog = MixedPaperPaginationDialog({"pagination": pagination(student, teacher)}, tasks, load)
    dialog.show()
    return dialog, tasks, calls


def pdf_report(tmp_path, count=40):
    path = tmp_path / "synthetic-slides.pdf"
    writer = QPdfWriter(str(path))
    writer.setResolution(72)
    painter = QPainter(writer)
    for number in range(1, count + 1):
        if number > 1:
            assert writer.newPage()
        painter.drawText(40, 100, f"SYNTHETIC PAGE {number}")
        painter.drawRect(40, 150, 400, 200)
    painter.end()
    del writer
    return {"path": str(path), "pdf_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_cache_evicts_by_memory_and_recency_and_does_not_retain_oversized(qt_app):
    cache = PixmapCache(80_000, 3)
    bitmap = QPixmap(100, 100)
    bitmap.fill(Qt.GlobalColor.white)
    cache.put(1, bitmap)
    cache.put(2, bitmap)
    assert cache.get(1) is bitmap
    assert cache.put(3, bitmap) == [2]
    assert len(cache) == 2 and cache.nbytes == 80_000
    assert cache.put(4, QPixmap(300, 300)) == [4]
    assert 4 not in cache and cache.nbytes == 80_000
    cache.clear()
    assert not cache and cache.nbytes == 0


def test_thumbnail_cache_releases_icons_without_losing_page_status(qt_app):
    navigation = PageNavigator()
    navigation.set_pages([(n, f"第{n}页") for n in range(80)])
    pixmap = QPixmap(240, 100)
    pixmap.fill(Qt.GlobalColor.green)
    for number in range(80):
        navigation.set_thumbnail(number, pixmap)
        navigation.set_status(number, "已核对")
    assert len(navigation._thumbnails) == 64
    assert navigation._items[0].icon().isNull()
    assert "已核对" in navigation._items[0].text()
    navigation.clear_pages()
    assert len(navigation._thumbnails) == 0 and navigation.count() == 0


def test_fast_jumps_read_only_active_then_latest_and_keep_roles_separate(qt_app):
    dialog, tasks, calls = paper_dialog()
    dialog.page_selector.setValue(10)
    dialog.page_selector.setValue(20)
    dialog._navigate(("teacher", 17))
    assert len(tasks.pending) == 1
    assert not dialog.review_page_button.isEnabled()
    tasks.finish()
    assert calls == ["student-page-1"] and len(tasks.pending) == 1
    assert dialog._displayed_key is None
    tasks.finish()
    assert calls == ["student-page-1", "teacher-page-17"]
    assert dialog._displayed_key == ("teacher", 17)
    assert dialog._labels["student"].pixmap().isNull()
    assert not dialog.review.reviewed
    dialog.reject()


def test_80_pages_keep_review_state_but_bound_images_and_release_on_close(qt_app):
    dialog, tasks, calls = paper_dialog()
    for audience in ("student", "teacher"):
        for number in range(1, 41):
            dialog._navigate((audience, number))
            tasks.flush()
            dialog.review_page_button.click()
            assert len(dialog._pixmaps) <= 4
            assert dialog._pixmaps.nbytes <= dialog._pixmaps.max_bytes
    assert len(calls) == 80 and len(dialog.review.reviewed) == 80
    assert dialog.confirm_button.isEnabled()
    dialog._navigate(("student", 1))
    assert not dialog.confirm_button.isEnabled()  # re-read must validate evicted page
    assert not dialog.review_page_button.isEnabled()
    tasks.flush()
    assert len(calls) == 81 and len(dialog.review.reviewed) == 80
    assert dialog.confirm_button.isEnabled()
    dialog.reject()
    assert len(dialog._pixmaps) == 0 and dialog._current_bitmap is None
    assert dialog.navigator.count() == 0
    assert all(label.pixmap().isNull() for label in dialog._labels.values())


def test_evicted_unreviewed_page_cannot_be_approved_while_reloading(qt_app):
    dialog, tasks, _ = paper_dialog(6, 1)
    for number in range(1, 7):
        dialog.page_selector.setValue(number)
        tasks.flush()
    dialog.page_selector.setValue(1)
    assert ("student", 1) in dialog.review.loaded
    assert not dialog.review_page_button.isEnabled()
    dialog._review_current()
    assert not dialog.review.reviewed
    tasks.flush()
    assert dialog.review_page_button.isEnabled()
    dialog.reject()


def test_failure_is_locatable_and_blocks_confirmation_but_not_other_pages(qt_app):
    dialog, tasks, _ = paper_dialog(3, 2)
    tasks.flush()
    dialog._image_loader = lambda _key: {"data": b"wrong"}
    dialog.page_selector.setValue(2)
    tasks.flush()
    assert dialog.review.failed and dialog.failure_button.isEnabled()
    assert "学生第2页" in dialog.status.text()
    dialog._navigate(("teacher", 1))
    tasks.flush()
    assert dialog.tabs.currentIndex() == 1
    dialog.failure_button.click()
    assert dialog._current() == ("student", 2)
    assert not dialog.confirm_button.isEnabled()
    dialog.reject()


def test_next_unreviewed_crosses_editions_without_marking_them(qt_app):
    dialog, tasks, _ = paper_dialog(1, 2)
    tasks.flush()
    dialog.review_page_button.click()
    dialog.unreviewed_button.click()
    tasks.flush()
    assert dialog._current() == ("teacher", 1)
    assert dialog.review.reviewed == {("student", 1)}
    dialog.reject()


def test_clicking_thumbnail_identity_selects_exact_edition_and_page(qt_app):
    dialog, tasks, calls = paper_dialog(3, 4)
    tasks.flush()
    dialog.navigator.setCurrentItem(dialog.navigator._items[("teacher", 3)])
    tasks.flush()
    assert dialog._current() == ("teacher", 3) and dialog._displayed_key == ("teacher", 3)
    assert calls[-1] == "teacher-page-3" and not dialog.review.reviewed
    dialog.reject()


def test_explicit_stop_does_not_leave_preview_stuck_loading(qt_app):
    dialog, tasks, _ = paper_dialog(3, 2)
    task_id = dialog._jobs[0]
    tasks.cancel(task_id)
    dialog._read_cancelled(task_id)
    assert not dialog._pending and dialog.review.failed
    dialog.page_selector.setValue(3)
    tasks.flush()
    assert dialog._displayed_key == ("student", 3) and not dialog.confirm_button.isEnabled()
    dialog.reject()


def test_late_page_completion_cannot_repopulate_closed_paper(qt_app):
    dialog, tasks, _ = paper_dialog()
    dialog.reject()
    tasks.finish(allow_cancelled=True)
    assert not dialog._pixmaps and not dialog._pending
    assert dialog.navigator.count() == 0 and not dialog.review.loaded


def test_closed_paper_releases_native_widgets_and_ignores_late_approval(qt_app):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid
    dialog, tasks, _ = paper_dialog(1, 1)
    tasks.flush()
    dialog.reject()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(dialog)
    dialog.reject()
    dialog.mark_confirmed()
    dialog.mark_confirmation_failed("Late failed approval")
    assert not dialog.confirmed


def test_owned_pdf_worker_finishes_before_native_dialog_is_deleted(qt_app, tmp_path, monkeypatch):
    from threading import Event
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from shiboken6 import isValid
    from integrations.deeptutor_shchem_v1.desktop_workbench import actual_ppt_preview_dialog as module
    entered, released = Event(), Event()
    original = module.FrozenPdfPages
    def delayed(report):
        entered.set()
        assert released.wait(10)
        return original(report)
    monkeypatch.setattr(module, "FrozenPdfPages", delayed)
    dialog = ActualPptPreview(pdf_report(tmp_path, 1))
    dialog.show()
    for _ in range(200):
        if entered.is_set():
            break
        QTest.qWait(10)
    assert entered.is_set()
    dialog.reject()
    assert isValid(dialog) and dialog._source is None
    released.set()
    for _ in range(200):
        QTest.qWait(10)
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if not isValid(dialog):
            break
    assert not isValid(dialog)


@pytest.mark.parametrize("width", [360, 420, 900])
def test_paper_narrow_controls_remain_inside_dialog(qt_app, width):
    dialog, tasks, _ = paper_dialog(2, 2)
    tasks.flush()
    dialog.resize(width, 520)
    QTest.qWait(30)
    assert dialog.width() == width
    for button in (dialog.back_button, dialog.confirm_button, dialog.review_page_button,
                   dialog.unreviewed_button, dialog.failure_button, dialog.next_button):
        point = button.mapTo(dialog, button.rect().center())
        assert dialog.rect().contains(point)
        assert button.height() > 15
    if width < 600:
        assert not dialog.navigator.isVisible()
        dialog.navigation_toggle.click()
        assert dialog.navigator.isVisible()
        dialog.navigator.setCurrentItem(dialog.navigator._items[("teacher", 2)])
        tasks.flush()
        assert dialog._displayed_key == ("teacher", 2)
    dialog.reject()


def test_real_pdf_first_page_is_lazy_and_back_navigation_uses_cache(qt_app, tmp_path, monkeypatch):
    report = pdf_report(tmp_path)
    calls = []
    original = FrozenPdfPages.render
    def render(self, number, **kwargs):
        calls.append(number)
        return original(self, number, **kwargs)
    monkeypatch.setattr(FrozenPdfPages, "render", render)
    tasks = Tasks()
    dialog = ActualPptPreview(report, tasks=tasks)
    dialog.show()
    assert calls == []
    tasks.finish()  # only discover page count
    assert dialog.count == 40 and calls == []
    tasks.finish()
    assert calls == [1] and dialog._shown == 1
    dialog.move(1)
    tasks.flush()
    dialog.move(-1)
    assert calls == [1, 2] and not tasks.pending
    dialog.page_selector.setValue(40)
    tasks.flush()
    assert calls == [1, 2, 40] and dialog._shown == 40
    assert not dialog.picture.pixmap().isNull()
    dialog.reject()
    assert dialog._source is None and not dialog._cache and dialog.picture.pixmap().isNull()


def test_pdf_snapshot_is_bound_and_external_open_rechecks_file(qt_app, tmp_path, monkeypatch):
    report = pdf_report(tmp_path, 2)
    from pathlib import Path
    source = FrozenPdfPages(report)
    Path(report["path"]).write_bytes(b"changed PDF")
    assert source.render(1)["data"].startswith(b"\x89PNG")
    with pytest.raises(ValueError):
        source.verify_file()
    with pytest.raises(ValueError):
        FrozenPdfPages(report)
    tasks = Tasks()
    dialog = ActualPptPreview(report, tasks=tasks)
    tasks.flush()
    assert not dialog.open_pdf.isEnabled() and dialog._source is None
    assert "已变化" in dialog.picture.text()
    dialog.reject()


def test_external_open_refuses_modified_file_without_discarding_snapshot(qt_app, tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    from integrations.deeptutor_shchem_v1.desktop_workbench import actual_ppt_preview_dialog as module
    report, tasks, opened = pdf_report(tmp_path, 2), Tasks(), []
    monkeypatch.setattr(module, "QDesktopServices", SimpleNamespace(openUrl=opened.append))
    dialog = ActualPptPreview(report, tasks=tasks)
    dialog.show()
    tasks.flush()
    dialog.open_pdf.click()
    assert len(opened) == 1
    Path(report["path"]).write_bytes(b"changed PDF")
    dialog.open_pdf.click()
    assert len(opened) == 1 and "原PDF已变化" in dialog.caption.text()
    assert dialog._shown == 1
    dialog.reject()


def test_pdf_size_limit_and_invalid_page_fail_before_render(qt_app, tmp_path, monkeypatch):
    report = pdf_report(tmp_path, 2)
    source = FrozenPdfPages(report)
    for page in (0, 3, True, "1"):
        with pytest.raises(ValueError):
            source.render(page)
    for ratio in (0, 5, True, float("nan")):
        with pytest.raises(ValueError):
            source.render(1, pixel_ratio=ratio)
    monkeypatch.setattr(FrozenPdfPages, "MAX_BYTES", 16)
    with pytest.raises(ValueError):
        FrozenPdfPages(report)


def test_high_dpi_keeps_logical_size_and_increases_real_render_resolution(qt_app, tmp_path):
    bitmap = QPixmap(1000, 1400)
    bitmap.fill(Qt.GlobalColor.white)
    normal = scaled_page(bitmap, 320, 1)
    high = scaled_page(bitmap, 320, 2)
    assert normal.deviceIndependentSize() == high.deviceIndependentSize()
    assert high.width() == 640 and high.devicePixelRatio() == 2
    source = FrozenPdfPages(pdf_report(tmp_path, 1))
    low, high = source.render(1), source.render(1, pixel_ratio=2)
    assert high["width"] > low["width"] and high["height"] > low["height"]
    assert high["width"] * high["height"] < 4_010_000


def test_pdf_stop_and_late_open_result_keep_closed_dialog_empty(qt_app, tmp_path):
    tasks = Tasks()
    dialog = ActualPptPreview(pdf_report(tmp_path, 2), tasks=tasks)
    identity = dialog._task
    tasks.cancel(identity)
    dialog._read_cancelled(identity)
    assert dialog._active is None and not dialog.open_pdf.isEnabled()
    dialog.reject()
    tasks.finish(allow_cancelled=True)
    assert dialog._source is None and not dialog._cache


@pytest.mark.parametrize("width", [360, 420, 1080])
def test_pdf_narrow_navigation_zoom_and_close(qt_app, tmp_path, width):
    tasks = Tasks()
    dialog = ActualPptPreview(pdf_report(tmp_path, 2), tasks=tasks)
    dialog.show()
    tasks.flush()
    dialog.resize(width, 520)
    QTest.qWait(30)
    assert dialog.width() == width
    for button in (dialog.prev, dialog.next, dialog.back, dialog.open_pdf, dialog.zoom):
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().center()))
    dialog.zoom.setCurrentIndex(1)
    assert dialog._shown == 1 and not tasks.pending
    dialog.page_selector.setValue(2)
    dialog.reject()
    tasks.finish(allow_cancelled=True)
    assert dialog._source is None and not dialog._cache

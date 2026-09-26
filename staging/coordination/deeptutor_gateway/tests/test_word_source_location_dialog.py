"""Purely synthetic Qt regression coverage for occurrence-specific source viewing."""

from __future__ import annotations

import os
import threading
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QCoreApplication, QEvent, QIODevice, QPoint, QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from shiboken6 import isValid

from integrations.deeptutor_shchem_v1.desktop_workbench.word_lesson_reader import (
    WordLessonReader,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_source_location_dialog import (
    WordSourceLocationDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


_UNSET = object()


class SyntheticTasks:
    """Deliberately deliver jobs in any order, including after cancellation."""

    def __init__(self):
        self.jobs = []
        self.cancelled = []

    def submit(self, _label, operation, *, on_success, on_failure):
        self.jobs.append((operation, on_success, on_failure))
        return len(self.jobs) - 1

    def cancel(self, task_id):
        self.cancelled.append(task_id)

    def succeed(self, task_id, value=_UNSET):
        operation, success, _failure = self.jobs[task_id]
        success(operation() if value is _UNSET else value)

    def fail(self, task_id, message="合成读取失败，请在原文件核对。"):
        self.jobs[task_id][2](message)


def synthetic_image(color="#628271", *, title="SYNTHETIC OBJECT", width=640, height=240):
    """Original geometric drawing; no source document or remote image input."""
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("#fffdf4"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(color), 4))
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(24, 68, 128, 104, 18, 18)
    painter.drawEllipse(width - 152, 68, 104, 104)
    painter.drawLine(182, 120, width - 188, 120)
    painter.drawLine(width - 212, 104, width - 188, 120)
    painter.drawLine(width - 212, 136, width - 188, 120)
    painter.setPen(QColor("#233d33"))
    font = painter.font()
    font.setPointSize(17)
    painter.setFont(font)
    painter.drawText(24, 40, title)
    painter.setFont(QApplication.font())
    painter.drawText(24, height - 18, "ORIGINAL QA DRAWING - NO DOCUMENT CONTENT")
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(data)


def synthetic_payload():
    common_asset = {
        "asset_id": "synthetic-shared-rId7",
        "label": "同一资源的合成原图",
        "relationship_id": "rId7",
        "sha256": "b" * 64,
        "mime_type": "image/png",
        "bytes_count": 512,
        "preview_supported": True,
    }
    locations = [
        {
            "location_id": "synthetic-paragraph-object-A",
            "kind": "ole_object",
            "label": "合成对象 A · 第一次出现",
            "position_text": "原文区块 3 · 第 1 段 · 对象 1",
            "context_text": "这是原创布局样例甲。方框与圆形之间有一条箭头，仅用于检查对象位置与图片归属。\n此段不包含教材或试题。",
            "xml_locator": "word/document.xml#/w:body/*[3]/*[2]/*[1]",
            "notices": ["合成旧式对象；图像只是布局测试，不代表公式内容已核验。"],
            "assets": [deepcopy(common_asset)],
            "relationship_ids": ["rId7"],
        },
        {
            "location_id": "synthetic-table-r2-c1-object-B",
            "kind": "ole_object",
            "label": "合成对象 B · 同资源再次出现",
            "position_text": "原文区块 8 · 表格第 2 行第 1 格 · 第 1 段 · 对象 1",
            "context_text": "原创表格样例乙：第二行第一格。\n虽然此处与对象 A 引用同一个 rId7，位置和上下文仍应分别保留。\n本行用于检查重复资源不会合并成一次出现。",
            "xml_locator": "word/document.xml#/w:body/*[8]/*[2]/*[1]/*[1]/*[2]/*[1]",
            "notices": ["同一图片资源在另一单元格再次出现；按本次位置读取。"],
            "assets": [deepcopy(common_asset)],
            "relationship_ids": ["rId7"],
        },
        {
            "location_id": "synthetic-native-formula-no-image",
            "kind": "native_omml",
            "label": "合成对象 C · 无独立原图",
            "position_text": "原文区块 9 · 第 2 段 · 原生公式 1",
            "context_text": "原创样例丙只有结构化对象，不提供独立图片。\n切到此处后，前一对象的图片必须清除。",
            "xml_locator": "word/document.xml#/w:body/*[9]/*[2]",
            "notices": ["保留无图缺口，不使用相邻图片代替。"],
            "assets": [],
            "relationship_ids": [],
        },
        {
            "location_id": "synthetic-unsupported-wmf",
            "kind": "ole_object",
            "label": "合成对象 D · WMF待核",
            "position_text": "原文区块 10 · 第 1 段 · 对象 1",
            "context_text": "原创样例丁：旧式矢量预览可能因字体缺失而不能显示。\n仍然保留位置及完整上下文供对照。",
            "xml_locator": "word/document.xml#/w:body/*[10]/*[1]/*[3]",
            "notices": ["无法预览不等于已恢复公式。"],
            "assets": [{**common_asset, "asset_id": "synthetic-wmf-rId9", "relationship_id": "rId9", "mime_type": "image/x-wmf", "preview_supported": False}],
            "relationship_ids": ["rId9"],
        },
    ]
    return {
        "source_name": "原创合成定位演示.docx",
        "source_sha256": "a" * 64,
        "source_revision": "synthetic-revision-only",
        "blocks": [{"index": n, "locations": [loc]} for n, loc in zip((3, 8, 9, 10), locations)],
    }


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    install_ui_font(app)
    return app


@pytest.fixture
def make_dialog(qt_app):
    dialogs = []

    def make(payload=None, *, show=True, select_native=None):
        source = synthetic_payload() if payload is None else deepcopy(payload)
        tasks = SyntheticTasks()
        calls = []
        colors = {
            "synthetic-paragraph-object-A": "#a34747",
            "synthetic-table-r2-c1-object-B": "#416cab",
        }

        def image(location_id, asset_id):
            calls.append((location_id, asset_id))
            return {"bytes": synthetic_image(colors.get(location_id, "#628271")), "derived_preview": False}

        dialog = WordSourceLocationDialog(
            lambda: deepcopy(source), image, tasks=tasks, select_native=select_native
        )
        dialogs.append(dialog)
        if show:
            dialog.show()
        qt_app.processEvents()
        assert len(tasks.jobs) == 1
        return dialog, tasks, calls

    yield make
    for dialog in dialogs:
        if isValid(dialog):
            dialog.close()
            dialog.deleteLater()
    qt_app.processEvents()


def _image_color(dialog):
    return dialog._pixmap.toImage().pixelColor(60, 100).name()


def _no_displayed_pixmap(dialog):
    pixmap = dialog.image_label.pixmap()
    return pixmap is None or pixmap.isNull()


def test_source_load_is_async_and_initial_controls_are_inert(make_dialog):
    dialog, tasks, calls = make_dialog()
    assert "正在" in dialog.source_name.text()
    assert not dialog.copy_button.isEnabled()
    assert not dialog.zoom_button.isEnabled()
    assert not dialog._payload and not calls
    tasks.succeed(0)
    assert dialog.source_name.text() == "原创合成定位演示.docx"
    assert dialog.location_list.count() == 4
    assert len(tasks.jobs) == 2 and not calls


def test_late_image_A_cannot_replace_B_even_for_same_resource_id(make_dialog):
    dialog, tasks, calls = make_dialog()
    tasks.succeed(0)
    dialog.location_list.setCurrentRow(1)
    assert len(tasks.jobs) == 3
    tasks.succeed(2)
    assert _image_color(dialog) == "#416cab"
    before = dialog.technical.toPlainText()
    tasks.succeed(1)
    assert _image_color(dialog) == "#416cab"
    assert dialog.technical.toPlainText() == before
    assert calls == [
        ("synthetic-table-r2-c1-object-B", "synthetic-shared-rId7"),
        ("synthetic-paragraph-object-A", "synthetic-shared-rId7"),
    ]


def test_late_image_failure_cannot_clear_newer_success(make_dialog):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    dialog.location_list.setCurrentRow(1)
    tasks.succeed(2)
    tasks.fail(1, "旧对象失败不应覆盖新对象")
    assert _image_color(dialog) == "#416cab"
    assert "旧对象" not in dialog.image_note.text()


@pytest.mark.parametrize("load_source_first", [False, True])
def test_close_cancels_pending_jobs_and_ignores_late_callbacks(make_dialog, load_source_first):
    dialog, tasks, _calls = make_dialog()
    if load_source_first:
        tasks.succeed(0)
    job_id = len(tasks.jobs) - 1
    previous = dialog.source_name.text()
    dialog.accept()
    assert job_id in tasks.cancelled and not dialog._jobs
    tasks.succeed(job_id)
    tasks.fail(job_id, "关闭后的失败")
    assert dialog.source_name.text() == previous
    assert dialog._pixmap is None
    assert "关闭后的" not in dialog.status.text()


@pytest.mark.parametrize("load_source_first", [False, True])
def test_destroyed_dialog_ignores_delayed_worker_success_and_failure(qt_app, load_source_first):
    tasks = SyntheticTasks()
    dialog = WordSourceLocationDialog(
        synthetic_payload, lambda *_args: {"bytes": synthetic_image()}, tasks=tasks
    )
    dialog.show()
    qt_app.processEvents()
    if load_source_first:
        tasks.succeed(0)
    pending = len(tasks.jobs) - 1
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(dialog)
    # Calling the queued Python callback must not touch the deleted QWidget.
    tasks.succeed(pending)
    tasks.fail(pending, "窗口销毁后返回的合成错误")
    assert pending in tasks.cancelled


def test_object_without_image_clears_previous_image_and_pending_image(make_dialog):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    tasks.succeed(1)
    assert dialog._pixmap is not None
    dialog.location_list.setCurrentRow(1)
    dialog.location_list.setCurrentRow(2)
    tasks.succeed(2)
    assert dialog._current["location_id"] == "synthetic-native-formula-no-image"
    assert dialog._pixmap is None and _no_displayed_pixmap(dialog)
    assert not dialog.zoom_button.isEnabled()
    assert "没有可独立读取的原图" in dialog.image_label.text()
    assert "样例丙" in dialog.context.toPlainText()
    assert dialog.copy_button.isEnabled()


@pytest.mark.parametrize("result", [None, {}, {"bytes": b"not-an-image"}, {"bytes": "not-bytes"}])
def test_bad_image_response_leaves_no_old_image(make_dialog, result):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    tasks.succeed(1)
    dialog.location_list.setCurrentRow(1)
    tasks.succeed(2, result)
    assert dialog._pixmap is None and _no_displayed_pixmap(dialog)
    assert not dialog.zoom_button.isEnabled()
    assert "原图暂时无法读取" in dialog.image_label.text()
    assert "无效" in dialog.image_note.text() or "无法显示" in dialog.image_note.text()
    assert "第二行第一格" in dialog.context.toPlainText()


def test_wmf_failure_preserves_location_not_another_picture(make_dialog):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    tasks.succeed(1)
    dialog.location_list.setCurrentRow(3)
    tasks.fail(2, "合成WMF字体不可用；未猜测公式内容。")
    assert dialog._pixmap is None and _no_displayed_pixmap(dialog)
    assert "合成WMF字体不可用" in dialog.image_note.text()
    assert "原文区块 10" in dialog.position.text()
    assert dialog.copy_button.isEnabled() and not dialog.zoom_button.isEnabled()


def test_unsupported_asset_does_not_submit_an_image_job(make_dialog):
    source = synthetic_payload()
    source["blocks"][0]["locations"][0]["assets"][0].update(
        mime_type="application/octet-stream", preview_supported=False
    )
    dialog, tasks, calls = make_dialog(source)
    tasks.succeed(0)
    assert len(tasks.jobs) == 1 and not calls
    assert "暂时无法预览" in dialog.image_label.text()
    assert dialog._pixmap is None


def test_empty_source_has_no_fabricated_locations_or_copy(make_dialog):
    source = synthetic_payload()
    source["blocks"] = []
    dialog, tasks, calls = make_dialog(source)
    tasks.succeed(0)
    assert "没有可定位" in dialog.position.text()
    assert dialog.location_list.count() == 0
    assert not dialog.copy_button.isEnabled()
    assert not dialog.zoom_button.isEnabled()
    assert not calls and len(tasks.jobs) == 1


@pytest.mark.parametrize("payload", [None, {}, {"blocks": None}, {"blocks": [{"locations": [{}]}]}])
def test_malformed_source_response_fails_closed(make_dialog, payload):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0, payload)
    assert "不完整" in dialog.status.text()
    assert dialog.count.text() == "未能定位"
    assert not dialog._locations and not dialog._payload
    assert not dialog.copy_button.isEnabled()


def test_source_failure_clears_existing_details_and_invalidates_pending_images(make_dialog):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    tasks.succeed(1)
    dialog.location_list.setCurrentRow(1)
    dialog._failed("来源修订已变化，请重新定位。")
    tasks.succeed(2)
    assert not dialog._payload and not dialog._locations and dialog._current is None
    assert dialog.location_list.count() == 0
    assert not dialog.context.toPlainText() and not dialog.technical.toPlainText()
    assert dialog._pixmap is None and _no_displayed_pixmap(dialog)
    assert not dialog.copy_button.isEnabled() and not dialog.zoom_button.isEnabled()


def test_strings_are_plain_text_and_copy_keeps_occurrence_and_fingerprint(make_dialog, monkeypatch):
    raw = '<a href="https://example.invalid">合成文本</a><img src="file:///synthetic.png">'
    source = synthetic_payload()
    source["source_name"] = raw
    loc = source["blocks"][0]["locations"][0]
    loc.update(label=raw, position_text=raw, context_text=raw, notices=[raw])
    opened = []
    monkeypatch.setattr(QDesktopServices, "openUrl", lambda url: opened.append(url))
    dialog, tasks, _calls = make_dialog(source)
    tasks.succeed(0)
    tasks.fail(1, raw)
    assert dialog.source_name.text() == raw
    assert dialog.context.toPlainText() == raw
    assert dialog.image_note.text() == raw
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in dialog.findChildren(QLabel))
    dialog.copy_button.click()
    copied = QApplication.clipboard().text()
    assert raw in copied and loc["xml_locator"] in copied
    assert "a" * 64 in copied
    assert not opened and _no_displayed_pixmap(dialog)


def test_search_no_match_then_restore_keeps_occurrence_identity(make_dialog):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    dialog.location_list.setCurrentRow(1)
    assert "第二行第一格" in dialog.context.toPlainText()
    dialog.search.setText("完全不存在的合成筛选")
    assert dialog.location_list.count() == 0 and dialog._current is None
    assert not dialog.copy_button.isEnabled()
    assert not dialog.context.toPlainText()
    assert dialog._pixmap is None
    dialog.search.clear()
    assert dialog.location_list.count() == 4
    ids = [dialog.location_list.item(i).data(Qt.ItemDataRole.UserRole)["location_id"] for i in range(4)]
    assert len(set(ids)) == 4
    dialog.search.setText("第二行第一格")
    assert dialog.location_list.count() == 1
    assert dialog._current["location_id"] == "synthetic-table-r2-c1-object-B"
    dialog.copy_button.click()
    assert "/*[8]/*[2]/*[1]" in QApplication.clipboard().text()


@pytest.mark.parametrize("width,height", [(1020, 790), (420, 790), (420, 580)])
def test_narrow_and_minimum_size_leave_actions_reachable(make_dialog, qt_app, width, height):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    dialog.status.setText("合成状态：图片未完成核对，仍可复制精确位置。" * 8)
    dialog.resize(width, height)
    qt_app.processEvents()
    assert dialog.width() == width
    assert dialog.height() == height
    assert dialog.splitter.orientation() == (
        Qt.Orientation.Vertical if width < 760 else Qt.Orientation.Horizontal
    )
    for button in dialog.findChildren(QPushButton):
        if not button.isVisible():
            continue
        if dialog.detail_scroll.isAncestorOf(button):
            dialog.detail_scroll.ensureWidgetVisible(button)
            qt_app.processEvents()
            viewport = dialog.detail_scroll.viewport()
            point = button.mapTo(viewport, QPoint(0, 0))
            assert viewport.rect().contains(point + QPoint(button.width() // 2, button.height() // 2)), button.text()
            assert not button.visibleRegion().isEmpty(), button.text()
            continue
        top_left = button.mapTo(dialog, QPoint(0, 0))
        assert dialog.rect().contains(top_left), button.text()
        assert dialog.rect().contains(top_left + QPoint(button.width() - 1, button.height() - 1)), button.text()
        assert not button.visibleRegion().isEmpty(), button.text()
    assert dialog.location_list.viewport().height() >= 30


def test_long_status_can_scroll_to_its_last_line_at_minimum_size(make_dialog, qt_app):
    dialog, tasks, _calls = make_dialog()
    tasks.succeed(0)
    end_marker = "合成状态末行：已到全文结尾。"
    message = "\n".join(
        [f"合成核对状态第 {i} 行：保留原文位置，图片结果尚待核对。" for i in range(1, 21)]
        + [end_marker]
    )
    dialog.resize(420, 580)
    dialog.status.setText(message)
    QTest.qWait(30)
    viewport = dialog.status_scroll.viewport()
    scrollbar = dialog.status_scroll.verticalScrollBar()
    last_point = QPoint(dialog.status.width() // 2, dialog.status.height() - 1)
    assert dialog.status.text() == message
    assert scrollbar.maximum() > 0
    assert not viewport.rect().contains(dialog.status.mapTo(viewport, last_point))
    assert dialog.status_scroll.horizontalScrollBar().maximum() == 0
    scrollbar.setFocus()
    QTest.keyClick(scrollbar, Qt.Key.Key_End)
    qt_app.processEvents()
    assert scrollbar.value() == scrollbar.maximum()
    assert viewport.rect().contains(dialog.status.mapTo(viewport, last_point))
    assert dialog.status.text().endswith(end_marker)
    assert dialog.location_list.viewport().height() >= 30
    for button in (dialog.copy_button, next(
        b for b in dialog.findChildren(QPushButton) if b.text() == "完成核对"
    )):
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().center()))
        assert not button.visibleRegion().isEmpty()


def native_payload():
    source = synthetic_payload()
    for block in source["blocks"][:2]:
        block["locations"][0].update(kind="image", source_states=[])
    source["blocks"][2]["locations"][0].update(kind="omml", source_states=[])
    return source


def native_success():
    return {"status": "selected", "selection_verified": True, "source_unchanged": True}


def test_native_entry_without_callback_or_selection_cannot_submit(make_dialog):
    dialog, tasks, _calls = make_dialog(native_payload())
    tasks.succeed(0)
    count = len(tasks.jobs)
    dialog.native_button.click()
    dialog._begin_native_selection()
    assert not dialog.native_button.isEnabled() and len(tasks.jobs) == count
    assert "当前入口" in dialog.native_status.text()

    calls = []
    dialog, tasks, _calls = make_dialog(
        native_payload(), select_native=lambda *args: calls.append(args)
    )
    tasks.succeed(0)
    dialog.location_list.setCurrentRow(-1)
    count = len(tasks.jobs)
    dialog._begin_native_selection()
    assert not dialog.native_button.isEnabled() and len(tasks.jobs) == count
    assert "先选择" in dialog.native_status.text() and not calls


@pytest.mark.parametrize("kind,states", [
    ("ole_object", []), ("field_code", []), ("symbol", []), ("alt_chunk", []),
    ("image", ["hidden"]), ("omml", ["deleted"]),
    ("image", ["compatibility_choice"]), ("omml", ["compatibility_fallback"]),
    ("image", None), ("image", ""), ("image", {}),
])
def test_native_unsupported_or_unconfirmed_state_is_inert(make_dialog, kind, states):
    source = native_payload()
    source["blocks"][0]["locations"][0].update(kind=kind, source_states=states)
    calls = []
    dialog, tasks, _calls = make_dialog(source, select_native=lambda *args: calls.append(args))
    tasks.succeed(0)
    count = len(tasks.jobs)
    dialog.native_button.click()
    dialog._begin_native_selection()
    assert len(tasks.jobs) == count and not calls
    assert not dialog.native_button.isEnabled()
    assert "不能" in dialog.native_status.text()


@pytest.mark.parametrize("row", [0, 1, 2])
def test_native_click_freezes_current_occurrence_and_only_worker_inputs(make_dialog, row):
    calls = []

    def select(location_id, cancelled):
        calls.append((location_id, cancelled, threading.get_ident()))
        return native_success()

    dialog, tasks, _calls = make_dialog(native_payload(), select_native=select)
    tasks.succeed(0)
    dialog.location_list.setCurrentRow(row)
    expected_id = dialog._current["location_id"]
    assert dialog.native_button.isEnabled()
    dialog.native_button.click()
    job = len(tasks.jobs) - 1
    assert not calls and not dialog.location_list.isEnabled()
    assert not dialog.search.isEnabled() and not dialog.native_button.isEnabled()
    assert dialog.cancel_native_button.isVisible()
    dialog._begin_native_selection()
    assert len(tasks.jobs) == job + 1
    values = []
    worker = threading.Thread(target=lambda: values.append(tasks.jobs[job][0]()))
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    assert calls[0][0] == expected_id
    assert isinstance(calls[0][1], threading.Event) and not calls[0][1].is_set()
    assert calls[0][2] != threading.get_ident()
    tasks.succeed(job, values[0])
    assert "原件已只读打开" in dialog.native_status.text()
    assert "已在 Word 中选中" in dialog.native_status.text()
    assert dialog.location_list.isEnabled() and dialog.search.isEnabled()
    assert dialog.native_button.isEnabled() and not dialog.cancel_native_button.isVisible()


@pytest.mark.parametrize("result", [
    None, {}, {"status": "selected"},
    {"status": "failed", "selection_verified": True, "source_unchanged": True},
    {"status": "selected", "selection_verified": False, "source_unchanged": True},
    {"status": "selected", "selection_verified": True, "source_unchanged": False},
    {"status": "selected", "selection_verified": 1, "source_unchanged": True},
    {"status": "selected", "selection_verified": True, "source_unchanged": "true"},
])
def test_native_success_requires_both_verified_flags(make_dialog, result):
    dialog, tasks, _calls = make_dialog(native_payload(), select_native=lambda *_args: result)
    tasks.succeed(0)
    dialog.native_button.click()
    tasks.succeed(len(tasks.jobs) - 1)
    assert "未能确认" in dialog.native_status.text()
    assert "原件已只读打开" not in dialog.native_status.text()
    assert dialog.search.isEnabled() and dialog.location_list.isEnabled()
    assert dialog.native_button.isEnabled() and not dialog.cancel_native_button.isVisible()


def test_native_cancel_signals_worker_and_late_results_cannot_replace_new_success(make_dialog):
    calls = []

    def select(location_id, cancelled):
        calls.append((location_id, cancelled))
        return native_success()

    dialog, tasks, _calls = make_dialog(native_payload(), select_native=select)
    tasks.succeed(0)
    dialog.native_button.click()
    old_job = len(tasks.jobs) - 1
    old_event = dialog._native_request["cancelled"]
    dialog.cancel_native_button.click()
    assert old_event.is_set() and old_job in tasks.cancelled
    assert "已请求取消" in dialog.native_status.text()
    assert dialog.search.isEnabled() and dialog.location_list.isEnabled()
    tasks.succeed(old_job)  # A queued cancelled operation must not call Word.
    assert not calls
    dialog.location_list.setCurrentRow(1)
    dialog.native_button.click()
    new_event = dialog._native_request["cancelled"]
    assert new_event is not old_event and not new_event.is_set()
    tasks.succeed(len(tasks.jobs) - 1)
    before = dialog.native_status.text()
    tasks.succeed(old_job, native_success())
    tasks.fail(old_job, "旧位置迟到的失败")
    assert dialog.native_status.text() == before
    assert calls[0][0] == "synthetic-table-r2-c1-object-B"


def test_native_cancel_reaches_an_already_running_operation(make_dialog):
    started, finish = threading.Event(), threading.Event()
    received = []

    def select(_location_id, cancelled):
        received.append(cancelled)
        started.set()
        assert finish.wait(2)
        return native_success()

    dialog, tasks, _calls = make_dialog(native_payload(), select_native=select)
    tasks.succeed(0)
    dialog.native_button.click()
    job = len(tasks.jobs) - 1
    values = []
    worker = threading.Thread(target=lambda: values.append(tasks.jobs[job][0]()))
    worker.start()
    try:
        assert started.wait(2)
        dialog.cancel_native_button.click()
        assert received[0].is_set()
    finally:
        finish.set()
        worker.join(2)
    tasks.succeed(job, values[0])
    assert "已请求取消" in dialog.native_status.text()
    assert dialog.search.isEnabled() and dialog.native_button.isEnabled()


@pytest.mark.parametrize("closing", ["accept", "reject", "close", "destroy"])
def test_native_close_or_direct_destruction_sets_cancellation_event(make_dialog, closing):
    calls = []
    dialog, tasks, _calls = make_dialog(native_payload(), select_native=lambda *args: calls.append(args))
    tasks.succeed(0)
    dialog.native_button.click()
    job = len(tasks.jobs) - 1
    event = dialog._native_request["cancelled"]
    if closing == "destroy":
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(dialog)
    else:
        getattr(dialog, closing)()
    assert event.is_set()
    tasks.succeed(job)
    tasks.succeed(job, native_success())
    tasks.fail(job, "窗口结束后的迟到失败")
    assert not calls


def test_native_programmatic_selection_change_cancels_old_occurrence(make_dialog):
    dialog, tasks, _calls = make_dialog(native_payload(), select_native=lambda *_args: native_success())
    tasks.succeed(0)
    dialog.native_button.click()
    job = len(tasks.jobs) - 1
    event = dialog._native_request["cancelled"]
    dialog.location_list.setCurrentRow(1)
    assert event.is_set()
    assert dialog.search.isEnabled() and dialog.location_list.isEnabled()
    assert dialog._current["location_id"] == "synthetic-table-r2-c1-object-B"
    tasks.succeed(job, native_success())
    assert "已在 Word 中选中" not in dialog.native_status.text()


def test_native_and_image_callbacks_keep_independent_state(make_dialog):
    dialog, tasks, _calls = make_dialog(native_payload(), select_native=lambda *_args: native_success())
    tasks.succeed(0)
    dialog.native_button.click()
    job = len(tasks.jobs) - 1
    tasks.succeed(1)
    assert _image_color(dialog) == "#a34747"
    assert "正在 Word" in dialog.native_status.text()
    assert not dialog.search.isEnabled()
    tasks.fail(job, "合成失败：原件修订已变化。")
    assert _image_color(dialog) == "#a34747"
    assert "修订已变化" in dialog.native_status.text()
    assert dialog.search.isEnabled() and dialog.native_button.isEnabled()
    dialog.native_button.click()
    tasks.succeed(len(tasks.jobs) - 1)
    before = dialog.native_status.text()
    tasks.fail(1, "合成图像读取失败")
    assert dialog.native_status.text() == before
    assert "合成图像读取失败" in dialog.image_note.text()


def test_native_task_start_failure_restores_controls(make_dialog, monkeypatch):
    dialog, tasks, _calls = make_dialog(native_payload(), select_native=lambda *_args: native_success())
    tasks.succeed(0)

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("synthetic bridge closed")

    monkeypatch.setattr(tasks, "submit", unavailable)
    dialog.native_button.click()
    assert "未能确认" in dialog.native_status.text()
    assert dialog._native_request is None
    assert dialog.search.isEnabled() and dialog.native_button.isEnabled()
    assert not dialog.cancel_native_button.isVisible()


@pytest.mark.parametrize("height", [790, 580])
def test_native_pending_narrow_window_keeps_cancel_and_footer_reachable(make_dialog, qt_app, height):
    dialog, tasks, _calls = make_dialog(native_payload(), select_native=lambda *_args: native_success())
    tasks.succeed(0)
    dialog.resize(420, height)
    dialog.native_button.click()
    qt_app.processEvents()
    assert dialog.size().width() == 420 and dialog.size().height() == height
    assert dialog.splitter.orientation() == Qt.Orientation.Vertical
    assert not dialog.search.isEnabled() and not dialog.location_list.isEnabled()
    viewport = dialog.detail_scroll.viewport()
    assert viewport.rect().contains(
        dialog.cancel_native_button.mapTo(viewport, dialog.cancel_native_button.rect().center())
    )
    for button in (dialog.native_button, dialog.cancel_native_button):
        assert dialog.detail_scroll.isAncestorOf(button)
        dialog.detail_scroll.ensureWidgetVisible(button)
        qt_app.processEvents()
        viewport = dialog.detail_scroll.viewport()
        assert viewport.rect().contains(button.mapTo(viewport, button.rect().center()))
        assert not button.visibleRegion().isEmpty()
    assert dialog.detail_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.location_list.viewport().height() >= 30
    for button in (dialog.copy_button, next(
        b for b in dialog.findChildren(QPushButton) if b.text() == "完成核对"
    )):
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().center()))
        assert not button.visibleRegion().isEmpty()
    dialog.cancel_native_button.click()
    assert dialog.search.isEnabled() and dialog.location_list.isEnabled()


def test_reader_location_action_is_explicit_and_old_generation_is_inert(qt_app):
    reader = WordLessonReader()
    selected, locations, images = [], [], []
    preparation_selection = {"start": 4, "end": 7}
    before = deepcopy(preparation_selection)
    reader.block_requested.connect(selected.append)
    reader.location_requested.connect(locations.append)
    reader.image_requested.connect(images.append)
    source = {
        "source_name": "原创合成教案.docx", "source_sha256": "a" * 64,
        "revision": "synthetic-reader-v1",
        "blocks": [{"index": 4, "text": "合成段落，不含原题。", "warnings": []}, {"index": 7, "text": "合成表格对象所在段。", "warnings": ["对象需定位"]}],
        "assets": [{"asset_id": "synthetic-image", "block_index": 7, "mime_type": "image/png", "preview_supported": True}],
    }
    try:
        reader.set_source(source)
        location_url = next(url for url, value in reader._actions.items() if value == ("location", 7))
        image_url = next(url for url, value in reader._actions.items() if value == ("image", "synthetic-image"))
        reader.browser.anchorClicked.emit(QUrl(location_url))
        assert locations == [7] and not selected and not images
        reader.search_edit.setText("合成")
        reader.scroll_to_block(4)
        assert preparation_selection == before and not selected
        reader.browser.anchorClicked.emit(QUrl(image_url))
        assert images == ["synthetic-image"]
        reader.set_source({**source, "revision": "synthetic-reader-v2"})
        reader.browser.anchorClicked.emit(QUrl(location_url))
        reader.browser.anchorClicked.emit(QUrl(image_url))
        reader.browser.anchorClicked.emit(QUrl("lesson-action:/forged/7"))
        assert locations == [7] and images == ["synthetic-image"] and not selected
        assert preparation_selection == before
        reader.browser.anchorClicked.emit(QUrl(next(url for url, value in reader._actions.items() if value == ("block", 4))))
        assert selected == [4]
    finally:
        reader.close()
        reader.deleteLater()
        qt_app.processEvents()


def test_import_reader_location_and_range_button_preserve_preparation_range(qt_app, monkeypatch):
    from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import ImportWordDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench import word_source_location_dialog as location_module

    calls, deleted = [], []

    class Facade:
        def imported_word_sources(self, *_args):
            return [{"source_id": name, "source_name": f"合成来源{name}.docx"} for name in ("A", "B")]

        def imported_word_preview(self, _batch, source_id):
            return {"source_name": f"合成来源{source_id}.docx", "source_sha256": source_id.lower() * 64,
                    "revision": "synthetic-" + source_id, "blocks": [
                        {"index": i, "text": f"原创段落{i}，没有真实题目。", "warnings": []}
                        for i in range(1, 4)], "assets": [], "warnings": []}

        def imported_word_source_locations(self, *args):
            calls.append(("locations", args))
            return synthetic_payload()

        def imported_word_location_image(self, *args):
            calls.append(("image", args))
            return {"bytes": synthetic_image()}

    class InlineDialog:
        def __init__(self, load, image, _parent, *, select_native=None):
            self.load, self.image = load, image

        def exec(self):
            self.load()
            self.image("synthetic-paragraph-object-A", "synthetic-shared-rId7")
            return 0

        def deleteLater(self):
            deleted.append(True)

    monkeypatch.setattr(location_module, "WordSourceLocationDialog", InlineDialog)
    dialog = ImportWordDialog(Facade(), "synthetic-batch")
    try:
        dialog.block_start.setValue(2)
        dialog.block_end.setValue(3)
        qt_app.processEvents()
        before = (dialog.block_start.value(), dialog.block_end.value())
        old_url = next(url for url, action in dialog.reader._actions.items() if action == ("location", 1))
        dialog.reader.browser.anchorClicked.emit(QUrl(old_url))
        assert (dialog.block_start.value(), dialog.block_end.value()) == before == (2, 3)
        assert calls[0] == ("locations", ("synthetic-batch", "A", "a" * 64, "synthetic-A", [1]))
        dialog.location_button.click()
        assert calls[2] == ("locations", ("synthetic-batch", "A", "a" * 64, "synthetic-A", [2, 3]))
        assert (dialog.block_start.value(), dialog.block_end.value()) == before
        dialog.source_combo.setCurrentIndex(1)
        after_change = (dialog.block_start.value(), dialog.block_end.value())
        dialog.reader.browser.anchorClicked.emit(QUrl(old_url))
        assert len(calls) == 4 and len(deleted) == 2
        assert (dialog.block_start.value(), dialog.block_end.value()) == after_change
    finally:
        dialog.close()
        dialog.deleteLater()
        qt_app.processEvents()

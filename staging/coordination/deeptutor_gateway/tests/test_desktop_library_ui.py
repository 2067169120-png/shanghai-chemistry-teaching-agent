from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _qt_available() -> bool:
    return importlib.util.find_spec("PySide6") is not None


@pytest.fixture
def qt_app():
    if not _qt_available():
        pytest.skip("PySide6 is available only in the desktop runtime")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@dataclass
class _Pending:
    task_id: str
    label: str
    operation: object
    on_success: object
    on_failure: object


class _ManualBridge:
    """Keeps callbacks controllable so tests can deliberately reorder them."""

    def __init__(self) -> None:
        self.pending: list[_Pending] = []
        self.cancelled: list[str] = []

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        task_id = f"task-{len(self.pending) + len(self.cancelled) + 1}"
        self.pending.append(_Pending(task_id, label, operation, on_success, on_failure))
        return task_id

    def cancel(self, task_id: str) -> None:
        self.cancelled.append(task_id)

    def take(self, label: str) -> _Pending:
        for index, task in enumerate(self.pending):
            if task.label == label:
                return self.pending.pop(index)
        raise AssertionError(f"No pending task named {label!r}")

    @staticmethod
    def succeed(task: _Pending, value=None, *, run: bool = False) -> None:
        result = task.operation() if run else value
        if task.on_success is not None:
            task.on_success(result)

    @staticmethod
    def fail(task: _Pending, message: str) -> None:
        if task.on_failure is not None:
            task.on_failure(message)


def _card(key: str, title: str, *, scope: str = "master"):
    from integrations.deeptutor_shchem_v1.desktop_facade import ThemeCard

    return ThemeCard(
        key=key,
        scope=scope,
        title_zh=title,
        paper_title_zh=f"{title}来源卷",
        source_zh="核心题库",
        atomic_total=2,
        atomic_matched=1,
        shared_context_zh=f"{title}共同材料摘要",
        page_zh="第 2–3 页",
    )


def _image(key: str, role: str = "question"):
    from integrations.deeptutor_shchem_v1.desktop_library import LibraryImage

    return LibraryImage(
        scope="master",
        node_id=key,
        crop_id=f"crop-{key}",
        sha256="a" * 64,
        role=role,
        caption_zh="共同材料 · 第 2 页"
        if role == "shared_material"
        else "题面 · 第 3 页",
        width=1200,
        height=800,
    )


def _detail(key: str, title: str, *, with_images: bool = True):
    from integrations.deeptutor_shchem_v1.desktop_library import (
        LibraryPartDetail,
        LibraryThemeDetail,
    )

    first = LibraryPartDetail(
        key=f"{key}-1",
        label_zh="18（1）",
        summary_zh="根据主题共同材料判断反应方向。",
        requirement_zh="写出判断并说明理由。",
        dependency_zh="使用本主题共同材料",
        question_images=(_image(f"{key}-1"),) if with_images else (),
        classification_zh=("作答形式：原因解释", "认知难度候选：中等（非学生实测）"),
        analysis_zh=("先识别氧化还原关系，再核对题图条件。",),
        reference_answer_zh="来源答案甲",
        answer_boundary_zh="来源参考答案（非官方）；系统未独立核验，不等于官方答案或评分细则。",
    )
    second = LibraryPartDetail(
        key=f"{key}-2",
        label_zh="18（2）①",
        summary_zh="承接前一小问进行计算。",
        requirement_zh="列式并保留单位。",
        dependency_zh="依赖前序作答单元：18（1）",
        question_images=(_image(f"{key}-2"),) if with_images else (),
        classification_zh=("作答形式：计算",),
        answer_boundary_zh="未找到可对齐的参考答案；不补写模型答案。",
    )
    return LibraryThemeDetail(
        key=key,
        scope="master",
        title_zh=title,
        paper_title_zh=f"{title}来源卷",
        source_zh="核心题库",
        page_zh="第 2–3 页",
        context_zh=f"{title}完整主题语境",
        shared_images=(_image(f"{key}-shared", "shared_material"),)
        if with_images
        else (),
        parts=(first, second),
        source_fields=(("来源卷", f"{title}来源卷"), ("年份", "2025")),
        notes_zh=("按主题保留原卷小题顺序。",),
    )


class _Facade:
    def __init__(self) -> None:
        self.details = {"A": _detail("A", "主题甲"), "B": _detail("B", "主题乙")}
        self.added: list[str] = []

    def basket(self):
        return []

    def add_theme_to_basket(self, card):
        self.added.append(card.key)
        return len(self.added)

    def library_theme_detail(self, card):
        return self.details[card.key]

    def library_image(self, _image):
        return b"not-used-in-selection-test"


def _search_result(*cards):
    from integrations.deeptutor_shchem_v1.desktop_facade import ThemeSearchResult

    return ThemeSearchResult(
        scope="master",
        query="",
        total_themes=len(cards),
        matched_atomic_parts=len(cards),
        cards=tuple(cards),
        has_more=False,
    )


def _visible_text(widget) -> str:
    from PySide6.QtWidgets import QLabel

    return "\n".join(label.text() for label in widget.findChildren(QLabel))


def test_pending_parentage_notice_does_not_create_broken_cards(qt_app):
    from dataclasses import replace

    from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import (
        LibraryPage,
    )

    bridge = _ManualBridge()
    page = LibraryPage(_Facade(), bridge)
    page._apply_results(replace(
        _search_result(), pending_atomic_parts=43, pending_matched_atomic_parts=2
    ))
    assert page.results.count() == 0
    assert not page.detail_view_button.isEnabled()
    assert not page.add_button.isEnabled()
    assert not bridge.pending
    assert "找到 0 道大题" in page.result_summary.text()
    assert "43 个作答单元" in page.result_summary.text()
    assert "本次匹配 2 个" in page.result_summary.text()
    assert "未计入" in page.result_summary.text()
    page._apply_results(_search_result(_card("A", "可读主题")))
    assert page.results.count() == 1
    assert "待补大题归属" not in page.result_summary.text()
    page.close()


def _png_bytes() -> bytes:
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(1200, 800, QImage.Format.Format_RGB32)
    image.fill(QColor("#f4efe4"))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")
    return bytes(buffer.data())


def test_library_selection_ignores_stale_detail_callback(qt_app) -> None:
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import (
        LibraryPage,
    )

    bridge = _ManualBridge()
    facade = _Facade()
    page = LibraryPage(facade, bridge)
    page._apply_results(_search_result(_card("A", "主题甲"), _card("B", "主题乙")))
    assert len(bridge.pending) == 1
    old = bridge.pending.pop(0)
    page.results.setCurrentRow(1)
    assert len(bridge.pending) == 1
    current = bridge.pending.pop(0)

    bridge.succeed(current, facade.details["B"])
    assert page.detail_view_button.isEnabled()
    assert page._selected_detail is facade.details["B"]
    assert "主题乙完整主题语境" in page.detail_context.text()

    bridge.succeed(old, facade.details["A"])
    assert page._selected_detail is facade.details["B"]
    assert "主题乙完整主题语境" in page.detail_context.text()
    assert "主题甲完整主题语境" not in page.detail_context.text()
    page.close()


def test_library_card_displays_expanded_answer_units_without_rewriting_inventory(
    qt_app,
) -> None:
    from dataclasses import replace

    from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import (
        LibraryPage,
    )

    bridge = _ManualBridge()
    page = LibraryPage(_Facade(), bridge)
    card = replace(_card("A", "分项主题"), atomic_total=10, display_atomic_units=13)
    page._apply_results(_search_result(card))
    assert "13 个作答单元" in page.results.item(0).text()
    assert card.atomic_total == 10
    page.close()


def test_legacy_handout_routes_to_per_question_entry_instead_of_metadata_basket(qt_app) -> None:
    from integrations.deeptutor_shchem_v1.desktop_facade import PERSONAL_HANDOUT_SCOPE
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_page import (
        LibraryPage,
    )

    bridge = _ManualBridge()
    facade = _Facade()
    page = LibraryPage(facade, bridge)
    handout = _card("H", "个人讲义题", scope=PERSONAL_HANDOUT_SCOPE)
    page._apply_results(_search_result(handout))
    assert not bridge.pending
    assert not page.detail_view_button.isEnabled()
    assert "讲义详情暂未接入" in page.detail_view_button.text()
    assert not page.add_button.isEnabled()
    assert "逐题预览" in page.add_button.text()
    assert page.word_questions_button.isEnabled()
    page.add_button.click()
    assert facade.added == []
    page.close()


def test_detail_preserves_full_chain_boundaries_and_missing_image_state(qt_app) -> None:
    from PySide6.QtWidgets import QLabel

    from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import (
        FitWidthImage,
        LibraryDetailDialog,
    )

    bridge = _ManualBridge()
    detail = _detail("A", "主题甲")
    dialog = LibraryDetailDialog(detail, bridge, lambda _image: b"")
    dialog.resize(420, 700)
    dialog.show()
    qt_app.processEvents()

    assert [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())] == [
        "题面",
        "答案与分析",
        "来源",
    ]
    text = _visible_text(dialog)
    assert "18（1）" in text and "18（2）①" in text
    assert "依赖前序作答单元：18（1）" in text
    assert "来源参考答案（非官方）" in text
    assert "未找到可对齐的参考答案；不补写模型答案。" in text
    answers = dialog.findChildren(QLabel, "LibraryReferenceAnswer")
    assert [answer.text() for answer in answers] == ["来源答案甲"]

    tasks = list(bridge.pending)
    bridge.pending.clear()
    bridge.succeed(tasks[0], _png_bytes())
    bridge.fail(tasks[1], "本地裁图缺失")
    bridge.succeed(tasks[2], _png_bytes())
    qt_app.processEvents()
    images = dialog.findChildren(FitWidthImage)
    assert len(images) == 3
    assert sum(image.has_image for image in images) == 2
    assert "本地裁图缺失" in _visible_text(dialog)
    assert all(image.minimumWidth() == 0 for image in images)

    no_image_dialog = LibraryDetailDialog(
        _detail("M", "缺图主题", with_images=False), _ManualBridge(), lambda _image: b""
    )
    missing_text = _visible_text(no_image_dialog)
    assert "未找到可核验的共同材料题图" in missing_text
    assert missing_text.count("摘要不是原题替代品") == 2
    dialog.close()
    no_image_dialog.close()


@pytest.mark.parametrize("width", [420, 900])
def test_detail_and_zoom_reader_do_not_force_horizontal_overflow(
    qt_app, width: int
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QScrollArea

    from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import (
        FitWidthImage,
        LibraryDetailDialog,
    )

    bridge = _ManualBridge()
    dialog = LibraryDetailDialog(_detail("W", "响应式主题"), bridge, lambda _image: b"")
    for task in tuple(bridge.pending):
        bridge.succeed(task, _png_bytes())
    bridge.pending.clear()
    dialog.resize(width, 700)
    dialog.show()
    for _ in range(4):
        qt_app.processEvents()

    question_scroll = dialog.tabs.widget(0)
    assert isinstance(question_scroll, QScrollArea)
    assert question_scroll.horizontalScrollBar().maximum() == 0
    image = dialog.findChildren(FitWidthImage)[0]
    assert image.width() <= question_scroll.viewport().width()
    assert image.focusPolicy() == Qt.FocusPolicy.StrongFocus

    image.setFocus()
    QTest.keyClick(image, Qt.Key.Key_Return)
    qt_app.processEvents()
    zoom = image._zoom_dialog
    assert zoom is not None and zoom.isVisible() and not zoom.isModal()
    zoom.resize(420, 620)
    zoom.zoom.setValue(150)
    qt_app.processEvents()
    assert zoom.zoom_value.text() == "150%"
    assert zoom.minimumSizeHint().width() <= 420
    assert zoom.zoom.geometry().right() < zoom.width()
    assert zoom.scroll.horizontalScrollBar().maximum() > 0
    assert zoom.scroll.horizontalScrollBar().height() > 0
    zoom.close()
    dialog.close()

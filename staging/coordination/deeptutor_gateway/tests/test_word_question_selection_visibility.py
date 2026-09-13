"""Teacher-facing selection requires a visible, readable question, not its title."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
from test_word_question_dialog import WORKBENCH_STYLE, _Facade, _isolated_qt_app, _Tasks

from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
    WordQuestionDialog,
)

STEM = (
    "【合成版式测试，不是真实试题】阅读材料并判断下列说法，选择一个选项。\n"
    "A．第一项完整选项，保留必要条件和计量单位。\n"
    "B．第二项完整选项，不应在导航或题面中被省略。\n"
    "C．第三项完整选项，含需要结合公共材料判断的条件。\n"
    "D．第四项完整选项，长题面末尾仍应能直接阅读。"
)
CONTEXT = "公共材料：本段为完成题目必须保留的共同条件，不能只显示题号。"


@pytest.fixture
def qt_app():
    with _isolated_qt_app(stylesheet=WORKBENCH_STYLE) as app:
        yield app


def _opening(*, images=True):
    facade, tasks = _Facade(), _Tasks()
    for row in facade.catalog["items"]:
        row["question_blocks"][0]["text"] = STEM + "\n来源题：" + row["key"]
        row["context_blocks"][0]["text"] = CONTEXT
        row["answer_blocks"][0]["text"] = "ANSWER-ONLY-SENTINEL"
        if not images:
            for block in row["question_blocks"] + row["context_blocks"]:
                block["assets"] = []
    dialog = WordQuestionDialog(facade, tasks)
    return dialog, facade, tasks


def _body_text(dialog):
    return "\n".join(label.text() for label in dialog._panels[0].findChildren(QLabel))


def test_full_question_and_material_visible_before_explicit_selection(qt_app):
    dialog, _facade, tasks = _opening(images=False)
    tasks.flush()
    assert STEM in _body_text(dialog)
    assert CONTEXT in _body_text(dialog)
    assert "ANSWER-ONLY-SENTINEL" not in _body_text(dialog)
    assert dialog.selections == []
    assert dialog.select_current_button.isEnabled()
    for index in range(dialog.question_list.count()):
        assert not dialog.question_list.item(index).flags() & Qt.ItemFlag.ItemIsUserCheckable
    dialog.select_current_button.click()
    assert len(dialog.selections) == 1
    assert dialog.selections[0]["key"] == dialog._current_key


def test_loading_required_image_does_not_enable_select(qt_app):
    dialog, _facade, tasks = _opening()
    tasks.finish("读取 Word 逐题目录")
    assert STEM in _body_text(dialog)
    assert tasks.pending
    assert dialog.current_question_readiness()[0] is False
    assert not dialog.select_current_button.isEnabled()
    dialog._select_current()
    assert dialog.selections == []
    tasks.flush()
    assert dialog.current_question_readiness()[0] is True
    assert dialog.select_current_button.isEnabled()


def test_failed_image_keeps_text_but_never_claims_ready(qt_app):
    dialog, facade, tasks = _opening()
    facade.word_question_image = lambda *_args: {"bytes": b"not-an-image", "mime_type": "image/png"}
    tasks.flush()
    assert STEM in _body_text(dialog)
    assert dialog.current_question_readiness()[0] is False
    assert not dialog.select_current_button.isEnabled()
    dialog._select_current()
    assert dialog.selections == []


def test_late_old_image_cannot_unlock_the_next_question(qt_app):
    dialog, _facade, tasks = _opening()
    tasks.finish("读取 Word 逐题目录")
    previous = dialog._current_key
    dialog.question_list.setCurrentRow(1)
    assert dialog._current_key != previous
    tasks.finish("读取 Word 题目来源图片", allow_cancelled=True)
    assert dialog.current_question_readiness()[0] is False
    assert not dialog.select_current_button.isEnabled()
    tasks.flush()
    assert dialog.current_question_readiness()[0] is True
    dialog.select_current_button.click()
    assert [row["key"] for row in dialog.selections] == [dialog._current_key]


def test_shared_material_image_is_required_too(qt_app):
    dialog, facade, tasks = _opening(images=False)
    first = facade.catalog["items"][0]
    first["context_blocks"][0]["assets"] = [{
        "asset_id": "shared-image", "preview_supported": True,
        "mime_type": "image/png", "label": "合成共同材料图",
    }]
    tasks.finish("读取 Word 逐题目录")
    assert CONTEXT in _body_text(dialog)
    assert not dialog.select_current_button.isEnabled()
    tasks.flush()
    assert dialog.select_current_button.isEnabled()


def test_unsupported_original_object_does_not_hide_question_text(qt_app):
    dialog, facade, tasks = _opening()
    first = facade.catalog["items"][0]
    first["question_blocks"][0]["assets"][0].update(
        preview_supported=False, mime_type="application/x-oleobject",
    )
    tasks.flush()
    assert STEM in _body_text(dialog)
    assert not dialog.current_question_readiness()[0]
    assert not dialog.select_current_button.isEnabled()


def test_visible_large_theme_is_not_blocked_by_image_lru_eviction(qt_app):
    dialog, facade, tasks = _opening(images=False)
    facade.catalog["items"][0]["question_blocks"][0]["assets"] = [
        {"asset_id": f"figure-{index}", "preview_supported": True,
         "mime_type": "image/png", "label": f"合成图 {index}"}
        for index in range(35)
    ]
    tasks.flush()
    assert len(dialog._image_cache) <= 32
    assert dialog.current_question_readiness()[0] is True
    assert dialog.select_current_button.isEnabled()
    dialog.select_current_button.click()
    assert len(dialog.selections) == 1


def test_pressing_enter_in_search_does_not_choose_or_import_a_question(qt_app):
    dialog, facade, tasks = _opening(images=False)
    tasks.flush()
    dialog.show()
    dialog.search.setFocus()
    dialog.search.setText("合成版式测试")
    QTest.keyClick(dialog.search, Qt.Key.Key_Return)
    qt_app.processEvents()
    tasks.flush()
    assert dialog.selections == []
    assert not [call for call in facade.calls if call[0] in {"reference", "export", "save"}]
    assert dialog.result() == 0


@pytest.mark.parametrize("width", [420, 900])
def test_collapsed_filters_leave_question_visible_in_initial_view(qt_app, width):
    dialog, _facade, tasks = _opening(images=False)
    tasks.flush()
    dialog.resize(width, 850)
    dialog.show()
    qt_app.processEvents()
    assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.multi_filter_panel.option_scroll.isHidden()
    assert dialog.tabs.mapTo(dialog, QPoint(0, 0)).y() < 630
    assert STEM in _body_text(dialog)

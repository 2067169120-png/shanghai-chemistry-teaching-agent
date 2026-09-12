"""Synthetic catalogue-ID filters; never open personal sources or providers."""

from __future__ import annotations

import json
import os
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from test_word_question_dialog import (
    WORKBENCH_STYLE,
    _attribute_options,
    _Facade,
    _isolated_qt_app,
    _question,
    _Tasks,
)

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    install_font_fallbacks,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
    WordQuestionDialog,
)


@pytest.fixture
def qt_app():
    with _isolated_qt_app(stylesheet=WORKBENCH_STYLE) as app:
        install_font_fallbacks()
        yield app


def _node(volume, chapter, section, *, title="物质分类"):
    return {
        "node_key": f"{volume}:{chapter}:{section}",
        "volume_id": volume,
        "volume_title": "必修第一册",
        "edition_title": "合成甲版" if volume == "BOOK-A" else "合成乙版",
        "chapter_id": chapter,
        "chapter_title": "第一章 化学研究" if chapter == "C1" else "第二章 物质变化",
        "section_title": title,
        "section_number": section,
    }


def _tag(item, node=None, *, exam="second_mock", grade="grade_12"):
    attributes = _attribute_options(item)["attributes"]
    attributes["primary_knowledge"].update(
        id="K01", label="物质分类", status="auto_suggested"
    )
    attributes["supporting_knowledge"] = [
        {"id": "K11", "label": "氧化还原", "status": "auto_suggested"}
    ]
    attributes["applicable_grades"]["values"] = [grade]
    attributes["original_source"]["exam_type"]["value"] = exam
    attributes["original_source"]["exam_type"]["status"] = "source_observed"
    attributes["curriculum_status"] = "auto_suggested" if node else "pending_mapping"
    attributes["original_source"]["display_label"] = "合成题 · 原考试类型：二模"
    attributes["curriculum_candidates"] = (
        []
        if node is None
        else [
            {
                "section_key": node["node_key"],
                "volume_id": node["volume_id"],
                "chapter_id": node["chapter_id"],
                "label": node["section_title"],
                "status": "auto_suggested",
                "evidence": [],
            }
        ]
    )
    item["attributes"] = attributes


def _loaded(*, unified=False):
    nodes = [
        _node("BOOK-A", "C1", "1.1"),
        _node("BOOK-B", "C1", "1.1"),
        _node("BOOK-A", "C2", "2.1"),
        _node("BOOK-A", "C1", "1.2", title="物质的量"),
        _node("BOOK-B", "C2", "2.1", title="目录中暂未收题的节"),
    ]
    facade, tasks = _Facade(), _Tasks()
    items = [_question(f"Q{i}", "A" if i != 2 else "B") for i in range(1, 8)]
    for item in items:
        for block in item["question_blocks"] + item["answer_blocks"]:
            block["assets"] = []
    for item, node in zip(items[:4], nodes[:4], strict=True):
        _tag(item, node)
    _tag(items[4])
    # Q6 has no attributes. Q7 has a section ID whose declared parent is wrong.
    _tag(items[6], nodes[0])
    items[6]["attributes"]["curriculum_candidates"][0]["volume_id"] = "BOOK-B"
    # A lecture heading must never become a textbook mapping.
    for item in items[4:]:
        item["chapter"] = "第一章 化学研究"
        item["source_name"] = "合成甲版必修第一册第一章.docx"
    facade.catalog["items"] = items
    facade.catalog["attribute_catalog"] = {"nodes": nodes, "knowledge_points": []}
    if unified:
        facade.add_word_questions_to_basket = lambda selections: len(selections)
    dialog = WordQuestionDialog(facade, tasks)
    tasks.flush()
    return dialog, facade, tasks


def _choose(combo, key):
    if isinstance(key, tuple):
        key = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    index = combo.findData(key)
    assert index >= 0, (key, [combo.itemData(i) for i in range(combo.count())])
    combo.setCurrentIndex(index)


def _visible(dialog):
    return [
        dialog.question_list.item(i).data(Qt.ItemDataRole.UserRole)
        for i in range(dialog.question_list.count())
    ]


def test_two_books_same_chapter_names_have_distinct_ids_and_parent_reset(qt_app):
    dialog, _facade, _tasks = _loaded()
    _choose(dialog.volume_filter, "BOOK-A")
    _choose(dialog.chapter_filter, ("BOOK-A", "C1"))
    _choose(dialog.section_filter, "BOOK-A:C1:1.1")
    assert _visible(dialog) == ["Q1"]
    assert "合成甲版" in dialog.volume_filter.currentText()
    _choose(dialog.volume_filter, "BOOK-B")
    assert dialog.chapter_filter.currentData() is None
    assert dialog.section_filter.currentData() is None
    assert not dialog.section_filter.isEnabled()
    _choose(dialog.chapter_filter, ("BOOK-B", "C1"))
    _choose(dialog.section_filter, "BOOK-B:C1:1.1")
    assert _visible(dialog) == ["Q2"]
    assert "合成乙版" in dialog.attribute_note.text()
    assert "自动建议，待教师确认" in dialog.attribute_note.text()


def test_curriculum_and_knowledge_grade_exam_filters_preserve_complete_selected_theme(
    qt_app,
):
    dialog, facade, tasks = _loaded()
    dialog.question_list.item(1).setCheckState(Qt.CheckState.Checked)
    before = deepcopy(dialog._items["Q1"])
    _choose(dialog.volume_filter, "BOOK-A")
    _choose(dialog.chapter_filter, ("BOOK-A", "C1"))
    _choose(dialog.knowledge_filter, "K11")  # supporting label also participates
    _choose(dialog.grade_filter, "grade_12")
    _choose(dialog.exam_filter, "second_mock")
    assert _visible(dialog) == ["Q1", "Q4"]
    _choose(dialog.section_filter, "BOOK-A:C1:1.1")
    dialog.question_list.item(0).setCheckState(Qt.CheckState.Checked)
    selected = deepcopy(dialog.selections)
    assert {item["key"] for item in selected} == {"Q1", "Q2"}
    _choose(dialog.exam_filter, "unknown")
    assert _visible(dialog) == []
    assert "没有符合条件" in dialog.detail_title.text()
    assert dialog.selections == selected
    assert dialog._items["Q1"] == before
    dialog.reset_filters_button.click()
    assert len(_visible(dialog)) == 7
    assert dialog.selections == selected
    tasks.flush()
    assert not any(
        call[0] in {"reference", "attribute_save", "export"} for call in facade.calls
    )


def test_unknown_and_missing_labels_do_not_infer_from_source_heading(qt_app):
    dialog, _facade, _tasks = _loaded()
    _choose(dialog.volume_filter, "unknown")
    assert _visible(dialog) == ["Q5", "Q6", "Q7"]
    assert not dialog.chapter_filter.isEnabled()
    assert not dialog.section_filter.isEnabled()
    _choose(dialog.knowledge_filter, "unknown")
    assert _visible(dialog) == ["Q6"]
    assert "尚未保存" in dialog.attributes_preview.toPlainText()


def test_catalog_refresh_preserves_valid_id_filters_and_selections_not_old_names(
    qt_app,
):
    dialog, facade, tasks = _loaded()
    dialog.question_list.item(0).setCheckState(Qt.CheckState.Checked)
    _choose(dialog.volume_filter, "BOOK-A")
    _choose(dialog.chapter_filter, ("BOOK-A", "C1"))
    _choose(dialog.section_filter, "BOOK-A:C1:1.1")
    for node in facade.catalog["attribute_catalog"]["nodes"]:
        if node["volume_id"] == "BOOK-A":
            node["volume_title"] = "已更正册名"
    dialog._load_catalog()
    tasks.flush()
    assert dialog.volume_filter.currentData() == "BOOK-A"
    assert "已更正册名" in dialog.volume_filter.currentText()
    assert dialog.chapter_filter.currentData() == '["BOOK-A","C1"]'
    assert dialog.section_filter.currentData() == "BOOK-A:C1:1.1"
    assert dialog.question_list.item(0).checkState() == Qt.CheckState.Checked
    facade.catalog["attribute_catalog"]["nodes"] = [
        node
        for node in facade.catalog["attribute_catalog"]["nodes"]
        if not (node["volume_id"] == "BOOK-A" and node["chapter_id"] == "C1")
    ]
    dialog._load_catalog()
    tasks.flush()
    assert dialog.volume_filter.currentData() == "BOOK-A"
    assert dialog.chapter_filter.currentData() is None
    assert dialog.section_filter.currentData() is None
    assert [item["key"] for item in dialog.selections] == ["Q1"]


def test_empty_catalog_chapter_result_and_reset_keep_unrelated_filters(qt_app):
    dialog, _facade, _tasks = _loaded()
    _choose(dialog.volume_filter, "BOOK-B")
    _choose(dialog.chapter_filter, ("BOOK-B", "C2"))
    assert _visible(dialog) == []
    _choose(dialog.section_filter, "BOOK-B:C2:2.1")
    _choose(dialog.source_combo, "A")
    dialog.search.setText("不会命中的合成字符串")
    dialog.reset_filters_button.click()
    assert dialog.search.text() == ""
    assert all(
        combo.currentData() is None
        for combo in (
            dialog.source_combo,
            dialog.volume_filter,
            dialog.chapter_filter,
            dialog.section_filter,
            dialog.knowledge_filter,
            dialog.grade_filter,
            dialog.exam_filter,
        )
    )
    assert len(_visible(dialog)) == 7


@pytest.mark.parametrize("catalog", [None, {}, {"nodes": []}])
def test_missing_catalog_is_explicit_and_does_not_hide_original_questions(
    qt_app, catalog
):
    dialog, facade, tasks = _loaded()
    facade.catalog["attribute_catalog"] = catalog
    dialog._load_catalog()
    tasks.flush()
    assert len(_visible(dialog)) == 7
    assert "目录暂不可用" in dialog.curriculum_filter_note.text()
    _choose(dialog.volume_filter, "unknown")
    assert len(_visible(dialog)) == 7
    assert dialog.volume_filter.findData("BOOK-A") < 0
    assert not dialog.chapter_filter.isEnabled()


def test_teacher_label_state_and_literal_catalog_text_remain_visible(qt_app):
    dialog, facade, tasks = _loaded()
    attributes = facade.catalog["items"][0]["attributes"]
    attributes["annotation_source"] = "teacher_modified"
    attributes["curriculum_candidates"][0]["status"] = "teacher_confirmed"
    facade.catalog["attribute_catalog"]["nodes"][0]["section_title"] = "<b>合成标签</b>"
    dialog._load_catalog()
    tasks.flush()
    assert "<b>合成标签</b>" in dialog.attribute_note.text()
    assert dialog.attribute_note.textFormat() == Qt.TextFormat.PlainText
    details = dialog.attributes_preview.toPlainText()
    assert "含教师修改" in details
    assert "教师确认" in details
    assert "<b>合成标签</b>" in details


@pytest.mark.parametrize("width", [400, 420, 900])
def test_curriculum_filter_controls_have_no_horizontal_overflow(qt_app, width):
    dialog, facade, tasks = _loaded()
    for node in facade.catalog["attribute_catalog"]["nodes"]:
        node["volume_title"] = "合成超长教材册名" * 12
        node["chapter_title"] = "合成超长章名" * 12
        node["section_title"] = "合成超长节名" * 12
    dialog._load_catalog()
    tasks.flush()
    _choose(dialog.volume_filter, "BOOK-A")
    _choose(dialog.chapter_filter, ("BOOK-A", "C1"))
    _choose(dialog.section_filter, "BOOK-A:C1:1.1")
    dialog.resize(width, 780)
    dialog.show()
    for _ in range(5):
        qt_app.processEvents()
    assert dialog.width() == width
    assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.question_list.horizontalScrollBar().maximum() == 0
    assert dialog.multi_filter_panel.width() <= width
    assert dialog.multi_filter_panel.chip_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.multi_filter_panel.chip_scroll.height() <= 70
    assert all(
        combo.isHidden()
        for combo in (
            dialog.volume_filter,
            dialog.chapter_filter,
            dialog.section_filter,
        )
    )


def test_visible_tag_checks_multiselect_or_and_remove_chips(qt_app):
    dialog, _facade, _tasks = _loaded()
    panel = dialog.multi_filter_panel
    assert panel.option_scroll.isHidden()
    panel.buttons["book"].click()
    panel.checks["BOOK-A"].click()
    panel.checks["BOOK-B"].click()
    assert panel.selection["book"] == {"BOOK-A", "BOOK-B"}
    assert _visible(dialog) == ["Q1", "Q2", "Q3", "Q4"]
    panel.buttons["exam"].click()
    panel.checks["second_mock"].click()
    assert _visible(dialog) == ["Q1", "Q2", "Q3", "Q4"]
    panel.chips[("book", "BOOK-A")].click()
    assert _visible(dialog) == ["Q2"]
    panel.chips[("exam", "second_mock")].click()
    assert panel.selection["exam"] == set()
    panel.clear_button.click()
    assert len(_visible(dialog)) == 7
    assert not panel.chips


def test_visible_tags_do_not_change_current_question_selection(qt_app):
    dialog, _facade, _tasks = _loaded()
    assert dialog.select_current_button.isEnabled()
    dialog.select_current_button.click()
    selected = deepcopy(dialog.selections)
    panel = dialog.multi_filter_panel
    panel.buttons["book"].click()
    panel.checks["BOOK-B"].click()
    assert _visible(dialog) == ["Q2"]
    assert dialog.selections == selected
    assert not (dialog.question_list.item(0).flags() & Qt.ItemFlag.ItemIsUserCheckable)
    dialog.select_current_button.click()
    assert {row["key"] for row in dialog.selections} == {"Q1", "Q2"}


def _demo_loaded():
    """Owned screenshot fixture: complete synthetic question and synthetic graph."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QImage, QPainter, QPen

    dialog, facade, tasks = _loaded(unified=True)
    question = facade.catalog["items"][0]
    question.update(
        title="合成演示题 · 溶液导电性", source_label="合成教学材料（非原题）"
    )
    question["context_blocks"][0]["text"] = (
        "研究小组向蒸馏水中加入少量氯化钠晶体，充分搅拌，"
        "用电导传感器记录变化。下图是为界面演示绘制的示意曲线。"
    )
    question["question_blocks"][0]["text"] = (
        "根据材料，关于氯化钠溶液导电原因的说明，合理的是（　）。\n"
        "A. 氯化钠晶体溶于水后产生可以自由移动的电子\n"
        "B. 氯化钠溶于水后形成可以自由移动的离子\n"
        "C. 只要物质能够溶于水，所得溶液就一定导电\n"
        "D. 溶液中水分子的数量增加是导电性增强的直接原因"
    )
    question["answer_blocks"][0]["text"] = (
        "合成示例参考答案：B。仅用于界面展示，不是真实原卷题。"
    )
    question["question_blocks"][0]["assets"] = [
        {
            "asset_id": "synthetic-conductivity",
            "label": "合成曲线 · 无真实实验数据",
            "preview_supported": True,
            "mime_type": "image/png",
        }
    ]
    picture = QImage(460, 150, QImage.Format.Format_RGB32)
    picture.fill(0xFFFAFCFA)
    painter = QPainter(picture)
    painter.setPen(QPen(Qt.GlobalColor.darkGray, 2))
    painter.drawLine(42, 115, 430, 115)
    painter.drawLine(42, 115, 42, 20)
    painter.drawText(52, 24, "电导读数（示意）")
    painter.drawText(376, 140, "时间")
    painter.setPen(QPen(Qt.GlobalColor.darkCyan, 3))
    for start, end in [
        ((45, 110), (125, 109)),
        ((125, 109), (210, 58)),
        ((210, 58), (300, 43)),
        ((300, 43), (422, 42)),
    ]:
        painter.drawLine(*start, *end)
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    picture.save(buffer, "PNG")
    facade.word_question_image = lambda *_args: {
        "bytes": bytes(data),
        "mime_type": "image/png",
    }
    dialog._load_catalog()
    tasks.flush()
    dialog.multi_filter_panel.set_selection(
        {"book": {"BOOK-A"}, "exam": {"second_mock"}}
    )
    tasks.flush()
    return dialog, facade, tasks


def test_knowledge_mode_all_requires_every_selected_primary_or_supporting_label(qt_app):
    dialog, facade, tasks = _loaded()
    facade.catalog["items"][1]["attributes"]["supporting_knowledge"] = []
    dialog._load_catalog()
    tasks.flush()
    panel = dialog.multi_filter_panel
    panel.buttons["knowledge"].click()
    panel.checks["K01"].click()
    panel.checks["K11"].click()
    assert "Q2" in _visible(dialog)
    panel.knowledge_all_check.click()
    assert panel.matching_selection()["knowledge_mode"] == "all"
    assert "Q2" not in _visible(dialog)
    assert "Q1" in _visible(dialog)
    panel.clear_button.click()
    assert not panel.knowledge_all_check.isChecked()
    assert len(_visible(dialog)) == 7


def test_more_than_lru_capacity_images_can_be_shown_then_selected(qt_app):
    from test_word_question_dialog import _asset

    facade, tasks = _Facade(), _Tasks()
    facade.catalog["items"][0]["question_blocks"][0]["assets"] = [
        _asset(f"synthetic-picture-{i}") for i in range(35)
    ]
    dialog = WordQuestionDialog(facade, tasks)
    tasks.finish("读取 Word 逐题目录")
    assert not dialog.select_current_button.isEnabled()
    tasks.flush()
    assert len(dialog._image_cache) == 32
    assert len(dialog._current_displayed_images) == 35
    assert dialog.current_question_readiness()[0]
    assert dialog.select_current_button.isEnabled()
    dialog.select_current_button.click()
    assert [row["key"] for row in dialog.selections] == ["Q1"]


def test_refresh_preserves_multisource_conditions_not_initial_hidden_combo(qt_app):
    facade, tasks = _Facade(), _Tasks()
    dialog = WordQuestionDialog(facade, tasks, initial_source_id="A")
    tasks.flush()
    assert dialog.multi_filter_panel.selection["source"] == {"A"}
    dialog.multi_filter_panel.set_selection({"source": {"A", "B"}})
    assert len(_visible(dialog)) == 3
    dialog._load_catalog()
    tasks.flush()
    assert dialog.multi_filter_panel.selection["source"] == {"A", "B"}
    assert len(_visible(dialog)) == 3


def test_preparation_secondary_controls_collapse_but_clear_selection_stays_available(
    qt_app,
):
    dialog, _facade, _tasks = _loaded()
    assert dialog.preparation_controls.isHidden()
    assert not dialog.clear_button.isHidden()
    dialog.preparation_toggle.click()
    assert not dialog.preparation_controls.isHidden()
    dialog.preparation_toggle.click()
    assert dialog.preparation_controls.isHidden()


def test_lesson_context_opens_preparation_controls(qt_app):
    facade, tasks = _Facade(), _Tasks()
    dialog = WordQuestionDialog(facade, tasks, lesson_topic="合成备课课题")
    tasks.flush()
    assert not dialog.preparation_controls.isHidden()

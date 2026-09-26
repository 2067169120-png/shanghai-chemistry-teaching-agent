"""Synthetic native range review, with no store, source-file or provider access."""

from __future__ import annotations

import os
from copy import deepcopy

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
    WordQuestionRangeDialog,
)

WARNINGS = {
    "unmarked_answer": "原文未使用答案标记；已按选项后的独立选项字母及同号说明列出答案候选，须核对后使用。",
    "nonstandard_label": "原文变式题标签缺少闭括号，已保留原文并识别为独立题目候选，请核对题目边界。",
    "self_contained_reference": "题目引用前文或前题，但未关联明确的共同材料；请核对材料范围。",
}


def source():
    return {
        "source_name": "合成教案 · 范围复核演示.docx",
        "blocks": [
            {
                "index": 10,
                "text": "【共同材料】实验中观察到金属置换现象。",
                "warnings": [],
            },
            {
                "index": 20,
                "text": "【变式训练3·变题型根据上述材料，判断氧化剂。",
                "warnings": [],
            },
            {
                "index": 40,
                "text": "A．失去电子的物质\nB．得到电子的物质",
                "warnings": [],
            },
            {"index": 60, "text": "(1)B", "warnings": []},
            {
                "index": 80,
                "text": "(1)氧化剂在反应中得到电子，发生还原反应。这是完整合成解析。",
                "warnings": [],
            },
            {
                "index": 100,
                "text": "未选择的后续教案区块，完整原文仍需保留。",
                "warnings": [],
            },
        ],
    }


def item(warnings=None):
    return {
        "block_start": 20,
        "question_end": 40,
        "answer_start": 60,
        "block_end": 80,
        "context_start": None,
        "context_end": None,
        "warnings": list(WARNINGS.values()) if warnings is None else warnings,
    }


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_dialog(qt_app):
    dialogs = []

    def make(given_item=None, given_source=None):
        dialog = WordQuestionRangeDialog(
            item() if given_item is None else given_item,
            source() if given_source is None else given_source,
        )
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.close()
        dialog.deleteLater()
    qt_app.processEvents()


def test_explicit_preview_is_required_and_review_boxes_start_unchecked(make_dialog):
    dialog = make_dialog()
    assert set(dialog.review_checks) == set(WARNINGS)
    assert not dialog.save_button.isEnabled()
    assert all(
        not box.isChecked() and not box.isEnabled()
        for box in dialog.review_checks.values()
    )
    assert "未选择的后续教案区块" in dialog.original.toPlainText()
    dialog._confirm()
    assert dialog.ranges is None
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "先点击" in dialog.status.text()


def test_preview_has_complete_separate_source_groups_and_legacy_ranges(make_dialog):
    dialog = make_dialog()
    dialog.fields["context_start"].setValue(10)
    dialog.fields["context_end"].setValue(10)
    original_text = dialog.original.toPlainText()
    dialog.preview_button.click()
    assert dialog.save_button.isEnabled()
    assert dialog.reading_tabs.currentIndex() == 1
    text = dialog.range_preview.toPlainText()
    assert "题面 · 学生作答所见" in text
    assert "答案与解析 · 教师参考，不进入学生题面" in text
    assert "共同材料 · 随题面保留" in text
    for block in source()["blocks"][:-1]:
        assert block["text"] in text
    assert "未选择的后续教案区块" not in text
    assert dialog.original.toPlainText() == original_text
    dialog.save_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.ranges == {
        "block_start": 20,
        "question_end": 40,
        "answer_start": 60,
        "block_end": 80,
        "context_start": 10,
        "context_end": 10,
    }


def test_only_explicitly_checked_applicable_issues_are_submitted(make_dialog):
    dialog = make_dialog()
    dialog.preview_button.click()
    dialog.review_checks["unmarked_answer"].setChecked(True)
    dialog.review_checks["nonstandard_label"].setChecked(True)
    dialog.save_button.click()
    assert dialog.ranges["reviewed_issues"] == ["unmarked_answer", "nonstandard_label"]
    assert dialog.ranges["context_start"] is None
    assert dialog.ranges["context_end"] is None


@pytest.mark.parametrize("key", tuple(WARNINGS))
def test_review_option_is_exposed_only_for_the_matching_original_warning(
    make_dialog, key
):
    dialog = make_dialog(item([WARNINGS[key]]))
    assert tuple(dialog.review_checks) == (key,)
    assert WARNINGS[key] in dialog.warning_label.text()
    dialog.preview_button.click()
    assert not dialog.review_checks[key].isChecked()
    dialog.save_button.click()
    assert "reviewed_issues" not in dialog.ranges


def test_unrelated_hard_boundary_warnings_never_offer_review_override(make_dialog):
    warnings = [
        "题目与答案处于同一来源区块，须核对范围或内容后才能导出学生版。",
        "所选范围包含多个明确题目标记，需核对是否应拆为多题。",
        "共同材料范围包含答案或解析标记，请核对。",
    ]
    dialog = make_dialog(item(warnings))
    assert not dialog.review_checks
    assert not dialog.review_note.isVisible()
    for warning in warnings:
        assert warning in dialog.warning_label.text()


@pytest.mark.parametrize(
    "field,new_value",
    [
        ("block_start", 10),
        ("question_end", 20),
        ("answer_start", 80),
        ("block_end", 100),
        ("context_start", 10),
        ("context_end", 10),
    ],
)
def test_every_range_edit_clears_preview_and_all_confirmations(
    make_dialog, field, new_value
):
    dialog = make_dialog()
    dialog.preview_button.click()
    for checkbox in dialog.review_checks.values():
        checkbox.setChecked(True)
    dialog.fields[field].setValue(new_value)
    assert dialog._preview_key is None
    assert not dialog.save_button.isEnabled()
    assert dialog.range_preview.toPlainText() == ""
    assert not dialog.reading_tabs.isTabEnabled(1)
    assert all(
        not box.isChecked() and not box.isEnabled()
        for box in dialog.review_checks.values()
    )
    assert "之前的勾选确认已清除" in dialog.status.text()
    dialog._confirm()
    assert dialog.ranges is None


def test_returning_to_original_numbers_still_requires_a_new_preview(make_dialog):
    dialog = make_dialog()
    dialog.preview_button.click()
    dialog.fields["block_end"].setValue(100)
    dialog.fields["block_end"].setValue(80)
    dialog._confirm()
    assert dialog.ranges is None
    dialog.preview_button.click()
    assert dialog.save_button.isEnabled()
    assert all(not box.isChecked() for box in dialog.review_checks.values())


def test_source_adjacency_not_arithmetic_adjacency_accepts_sparse_indices(make_dialog):
    dialog = make_dialog()
    assert dialog.fields["question_end"].value() == 40
    assert dialog.fields["answer_start"].value() == 60
    dialog.preview_button.click()
    assert dialog.save_button.isEnabled()
    assert dialog._preview_ranges["answer_start"] == 60


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"question_end": 20}, "紧邻"),
        ({"answer_start": 40}, "不能重叠"),
        ({"block_start": 30}, "实际存在"),
        ({"answer_start": 61}, "紧邻"),
        ({"block_start": 80}, "区块顺序"),
        ({"answer_start": 0}, "本题结束须等于题面结束"),
        ({"context_start": 10}, "同时填写"),
        ({"context_start": 10, "context_end": 40}, "不能与本题"),
        ({"context_start": 60, "context_end": 80}, "不能与本题"),
        ({"context_start": 90, "context_end": 100}, "实际存在"),
        ({"context_start": 100, "context_end": 10}, "区块顺序"),
    ],
)
def test_preview_rejects_invalid_source_ranges_before_save(
    make_dialog, changes, message
):
    dialog = make_dialog()
    for field, value in changes.items():
        dialog.fields[field].setValue(value)
    dialog.preview_button.click()
    assert not dialog.save_button.isEnabled()
    assert message in dialog.status.text()
    assert dialog._preview_ranges is None
    assert dialog.ranges is None


def test_same_source_block_cannot_be_both_question_and_answer(make_dialog):
    value = item()
    value.update(question_end=20, answer_start=20, block_end=20)
    dialog = make_dialog(value)
    dialog.preview_button.click()
    assert "同一段题答不能用整区块范围拆开" in dialog.status.text()
    assert not dialog.save_button.isEnabled()


def test_cancel_never_submits_preview_or_review_flags(make_dialog):
    dialog = make_dialog()
    dialog.preview_button.click()
    dialog.review_checks["unmarked_answer"].setChecked(True)
    dialog.cancel_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.ranges is None


def test_locate_and_switching_read_tabs_do_not_mutate_a_valid_preview(make_dialog):
    dialog = make_dialog()
    dialog.preview_button.click()
    before, key = dialog.original.toPlainText(), dialog._preview_key
    dialog.locate_button.click()
    assert dialog.reading_tabs.currentIndex() == 0
    assert dialog.original.toPlainText() == before
    assert dialog._preview_key == key
    dialog.reading_tabs.setCurrentIndex(1)
    assert dialog.save_button.isEnabled()


def test_source_text_is_frozen_and_html_is_literal_in_both_views(make_dialog):
    data = source()
    payload = '<img src="file:///C:/private.png"><b>不得执行或隐去的合成文字</b>'
    data["source_name"] = payload
    data["blocks"][1]["text"] = payload
    dialog = make_dialog(given_source=data)
    data["blocks"][1]["text"] = "外部对象后来修改"
    dialog.preview_button.click()
    assert payload in dialog.original.toPlainText()
    assert payload in dialog.range_preview.toPlainText()
    assert "外部对象后来修改" not in dialog.range_preview.toPlainText()
    source_labels = [
        label for label in dialog.findChildren(QLabel) if payload in label.text()
    ]
    assert source_labels
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in source_labels)
    assert dialog.warning_label.textFormat() == Qt.TextFormat.PlainText


@pytest.mark.parametrize(
    "blocks",
    [
        [],
        None,
        [{"index": True, "text": "a"}],
        [{"index": 1, "text": "a"}, {"index": 1, "text": "b"}],
        [{"index": 40, "text": "a"}, {"index": 20, "text": "b"}],
        [
            {"index": 10, "text": "a"},
            {"index": 40, "text": "b"},
            {"index": 20, "text": "c"},
        ],
    ],
)
def test_invalid_source_cannot_create_a_range_preview(make_dialog, blocks):
    data = source()
    data["blocks"] = deepcopy(blocks)
    dialog = make_dialog(given_source=data)
    assert not dialog.preview_button.isEnabled()
    assert not dialog.save_button.isEnabled()
    assert "无效或缺失" in dialog.status.text()


@pytest.mark.parametrize(
    "field, references",
    [
        ("asset_refs", ["private-image-a", "private-image-b"]),
        (
            "assets",
            [{"asset_id": "private-image-a"}, {"asset_id": "private-image-b"}],
        ),
    ],
)
def test_block_image_references_show_count_and_text_only_notice(
    make_dialog, field, references
):
    data = source()
    data["blocks"][1][field] = references
    dialog = make_dialog(given_source=data)
    dialog.preview_button.click()
    for editor in (dialog.original, dialog.range_preview):
        text = editor.toPlainText()
        assert "原图引用：2 项" in text
        assert "范围页为文字预览，原图请在逐题预览/原Word核对" in text
        assert data["blocks"][1]["text"] in text
        assert "private-image" not in text
    assert all(not box.isChecked() for box in dialog.review_checks.values())


def test_source_assets_bind_by_explicit_block_index_and_are_frozen(make_dialog):
    data = source()
    data["assets"] = [
        {"asset_id": "private-image-a", "block_index": 20},
        {"asset_id": "private-image-b", "block_index": 20},
        {"asset_id": "private-image-c", "block_index": 40},
        {"asset_id": "private-image-not-selected", "block_index": 100},
    ]
    dialog = make_dialog(given_source=data)
    data["assets"].append({"asset_id": "later-added-image", "block_index": 20})
    dialog.preview_button.click()
    text = dialog.range_preview.toPlainText()
    assert "原图引用：2 项" in text
    assert text.count("范围页为文字预览") == 2
    assert dialog.original.toPlainText().count("范围页为文字预览") == 3
    assert "private-image" not in text
    assert "原图引用：3 项" not in text


def test_image_references_are_not_double_counted_across_source_and_block(make_dialog):
    data = source()
    data["assets"] = [{"asset_id": "image-a", "block_index": 20}]
    data["blocks"][1]["assets"] = [
        {"asset_id": "image-a", "block_index": 20},
        {"asset_id": "image-b", "block_index": 20},
    ]
    data["blocks"][1]["asset_refs"] = ["image-a", "image-b", "image-c"]
    dialog = make_dialog(given_source=data)
    dialog.preview_button.click()
    assert "原图引用：3 项" in dialog.range_preview.toPlainText()
    assert dialog.range_preview.toPlainText().count("范围页为文字预览") == 1


def test_text_only_blocks_do_not_claim_source_images(make_dialog):
    dialog = make_dialog()
    dialog.preview_button.click()
    assert "原图引用" not in dialog.original.toPlainText()
    assert "原图引用" not in dialog.range_preview.toPlainText()


@pytest.mark.parametrize("width", [400, 420, 900])
def test_range_review_widgets_fit_narrow_and_wide_native_dialog(
    make_dialog, qt_app, width
):
    dialog = make_dialog()
    dialog.resize(width, 950)
    dialog.show()
    QTest.qWait(30)
    dialog.preview_button.click()
    QTest.qWait(30)
    assert dialog.width() == width
    assert dialog.scroll.widget().width() <= dialog.scroll.viewport().width()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    assert dialog.original.horizontalScrollBar().maximum() == 0
    assert dialog.range_preview.horizontalScrollBar().maximum() == 0
    assert dialog.reading_tabs.width() <= dialog.scroll.viewport().width()
    assert all(
        box.width() <= dialog.scroll.viewport().width()
        for box in dialog.review_checks.values()
    )
    qt_app.processEvents()

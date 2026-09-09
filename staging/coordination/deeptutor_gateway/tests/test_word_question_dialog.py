from __future__ import annotations

import os
from copy import deepcopy
from typing import ClassVar

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    WORKBENCH_STYLE,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
    WordQuestionDialog,
    WordQuestionRangeDialog,
)


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(WORKBENCH_STYLE)
    return app


class _Tasks(QObject):
    task_finished = Signal(str)

    def __init__(self):
        super().__init__()
        self.pending = []
        self.cancelled = set()
        self.serial = 0

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self.serial += 1
        task_id = str(self.serial)
        self.pending.append(
            {
                "task_id": task_id,
                "label": label,
                "operation": operation,
                "on_success": on_success,
                "on_failure": on_failure,
            }
        )
        return task_id

    def cancel(self, task_id):
        self.cancelled.add(task_id)

    def finish(self, label=None, *, allow_cancelled=False):
        index = next(
            (
                i
                for i, pending in enumerate(self.pending)
                if label is None or pending["label"] == label
            ),
            None,
        )
        assert index is not None, (label, [job["label"] for job in self.pending])
        pending = self.pending.pop(index)
        if allow_cancelled or pending["task_id"] not in self.cancelled:
            try:
                result = pending["operation"]()
            except (RuntimeError, TypeError, ValueError):
                pending["on_failure"]("测试来源暂时不可用")
            else:
                if pending["on_success"]:
                    pending["on_success"](result)
        self.task_finished.emit(pending["task_id"])

    def flush(self):
        count = 0
        while self.pending:
            self.finish()
            count += 1
            assert count < 80


def _asset(asset_id):
    return {
        "asset_id": asset_id,
        "label": "来源结构图",
        "preview_supported": True,
        "mime_type": "image/png",
    }


def _preparation_asset(number=1):
    sha = f"{number:064x}"
    return {
        "asset_id": "IMG-" + sha,
        "sha256": sha,
        "caption": "来源题图",
        "source": "演示讲义",
        "purpose": "课堂观察",
        "width": 100,
        "height": 80,
        "content_type": "image/png",
    }


def _question(key, source, *, ready=True, export_ready=True):
    return {
        "key": key,
        "revision": "revision-" + key,
        "batch_id": "batch-" + source,
        "source_id": source,
        "source_name": source + ".docx",
        "source_label": "来源 " + source,
        "chapter": "离子反应" if source == "A" else "有机结构",
        "title": "第 " + key[-1] + " 题",
        "question_blocks": [
            {
                "index": 2,
                "text": "题面 " + key + "：判断电解质"
                if source == "A"
                else "题面 " + key + "：分析葡萄糖结构",
                "warnings": [],
                "assets": [_asset(key + "-question")],
            }
        ],
        "answer_blocks": [
            {
                "index": 3,
                "text": "答案 " + key + "：选择 A；答案解析只在此处",
                "warnings": [],
                "assets": [_asset(key + "-answer")],
            }
        ],
        "context_blocks": [
            {"index": 1, "text": "本题共享材料", "warnings": [], "assets": []}
        ],
        "warnings": [],
        "boundary_status": "verified_candidate",
        "block_start": 2,
        "question_end": 2,
        "answer_start": 3,
        "block_end": 3,
        "context_start": 1,
        "context_end": 1,
        "source_block_count": 5,
        "selection_ready": ready,
        "export_ready": export_ready,
    }


class _Facade:
    def __init__(self, saved=None):
        self.catalog = {
            "revision": "catalog-r1",
            "sources": [
                {"source_id": "A", "source_name": "电离教师版.docx"},
                {"source_id": "B", "source_name": "有机解析版.docx"},
            ],
            "items": [
                _question("Q1", "A"),
                _question("Q2", "B"),
                _question("Q3", "A", ready=False, export_ready=False),
            ],
        }
        self.saved = deepcopy(saved or [])
        self.calls = []
        self.reference_changed = False
        self.reference_failed = False
        self.image_issues = []
        self.image_assets = []
        self.export_warnings = []

    def word_question_catalog(self):
        self.calls.append(("catalog",))
        return deepcopy(self.catalog)

    def word_question_saved_selection(self):
        self.calls.append(("saved",))
        return deepcopy(self.saved)

    def word_question_save_selection(self, selections):
        self.calls.append(("save", deepcopy(selections)))
        self.saved = deepcopy(selections)

    def word_question_image(self, key, revision, asset_id):
        self.calls.append(("image", key, revision, asset_id))
        image = QImage(20, 20, QImage.Format.Format_RGB32)
        image.fill(0xFFCCDDEE)
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        return {
            "bytes": bytes(data),
            "mime_type": "image/png",
            "label": "原图",
        }

    def word_question_reference(self, selections, *, include_images=True):
        self.calls.append(("reference", deepcopy(selections), include_images))
        if self.reference_failed:
            raise RuntimeError("test failure")
        return {
            "materials": "完整选题："
            + ";".join(
                f"{item['key']}（练习{item['points']}分）" for item in selections
            )
            + ("发生变化" if self.reference_changed else ""),
            "warnings": ["原图与题目范围仍需核对"],
            "selections": deepcopy(selections),
            "include_images": include_images,
            "image_assets": deepcopy(self.image_assets) if include_images else [],
            "image_issues": list(self.image_issues),
        }

    def word_question_export(self, title, selections, *, show_student_scores=False):
        self.calls.append(("export", title, deepcopy(selections), show_student_scores))
        return {
            "student_path": "C:/qa/student.docx",
            "teacher_path": "C:/qa/teacher.docx",
            "warnings": list(self.export_warnings),
        }

    def word_question_source(self, key, revision):
        self.calls.append(("source", key, revision))
        return {
            "source_name": "完整来源.docx",
            "source_sha256": "a" * 64,
            "revision": "source-r1",
            "blocks": [
                {"index": i, "text": f"完整来源区块{i}", "warnings": []}
                for i in range(1, 6)
            ],
            "assets": [],
        }

    def word_question_update_range(self, key, revision, **ranges):
        self.calls.append(("range", key, revision, deepcopy(ranges)))
        result = deepcopy(
            next(item for item in self.catalog["items"] if item["key"] == key)
        )
        result.update(ranges)
        result["revision"] += "-edited"
        return result


def _loaded(facade=None, **kwargs):
    facade = facade or _Facade()
    tasks = _Tasks()
    dialog = WordQuestionDialog(facade, tasks, **kwargs)
    tasks.finish("读取 Word 逐题目录")
    return dialog, facade, tasks


def _check(dialog, key, checked=True):
    for row in range(dialog.question_list.count()):
        item = dialog.question_list.item(row)
        if item.data(Qt.ItemDataRole.UserRole) == key:
            item.setCheckState(
                Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
            )
            return
    raise AssertionError(key)


def _cleanup(dialog, tasks):
    dialog.reject()
    tasks.flush()
    dialog.deleteLater()


def test_catalog_is_background_loaded_and_restores_selection_points(qt_app):
    facade = _Facade([{"key": "Q2", "revision": "revision-Q2", "points": 7}])
    tasks = _Tasks()
    dialog = WordQuestionDialog(facade, tasks, initial_source_id="B")
    assert facade.calls == []
    assert not dialog.preview_button.isEnabled()
    tasks.finish("读取 Word 逐题目录")
    assert dialog.question_list.count() == 1
    assert dialog._current_key == "Q2"
    assert dialog.points.value() == 7
    assert dialog.selections == facade.saved
    assert dialog.source_combo.currentData() == "B"
    _cleanup(dialog, tasks)


@pytest.mark.parametrize("empty", [True, False])
def test_catalog_warnings_are_visible_and_never_report_clean_success(qt_app, empty):
    facade = _Facade()
    if empty:
        facade.catalog["items"] = []
    facade.catalog["warnings"] = ["归档来源缺失，请重新导入。", "一项范围修订已失效。"]
    dialog, _facade, tasks = _loaded(facade)
    assert not dialog.catalog_note.isHidden()
    assert "归档来源缺失" in dialog.catalog_note.text()
    assert "范围修订已失效" in dialog.catalog_note.text()
    assert dialog.status.objectName() == "StatusAttention"
    if empty:
        assert "尚无可用题目" in dialog.status.text()
    _cleanup(dialog, tasks)


def test_question_card_does_not_repeat_a_title_already_in_the_stem(qt_app):
    facade = _Facade()
    question = facade.catalog["items"][0]
    question["title"] = "【即学即练1】下列属于电解质的是"
    question["question_blocks"][0]["text"] = question["title"] + " A.氯化钠 B.糖水"
    dialog, _facade, tasks = _loaded(facade)
    label = dialog.question_list.item(0).text()
    assert label.count(question["title"]) == 1
    assert "A.氯化钠 B.糖水" in label
    _cleanup(dialog, tasks)


def test_multi_source_selection_survives_filters_and_saves_updated_points(qt_app):
    dialog, facade, tasks = _loaded()
    _check(dialog, "Q1")
    dialog.points.setValue(6)
    dialog.source_combo.setCurrentIndex(dialog.source_combo.findData("B"))
    _check(dialog, "Q2")
    dialog.search.setText("葡萄糖")
    dialog._filter_items()
    assert {item["key"] for item in dialog.selections} == {"Q1", "Q2"}
    assert "已选 2 题" in dialog.selection_count.text()
    dialog.source_combo.setCurrentIndex(0)
    dialog.search.clear()
    dialog._filter_items()
    assert dialog.question_list.item(0).checkState() == Qt.CheckState.Checked
    assert dialog.selections[0]["points"] == 6
    dialog._persist_selection()
    tasks.flush()
    assert facade.saved == dialog.selections
    _cleanup(dialog, tasks)


def test_question_answer_and_context_are_separate_and_answer_images_are_lazy(qt_app):
    dialog, facade, tasks = _loaded()
    question_text = "\n".join(
        label.text() for label in dialog._panels[0].findChildren(QLabel)
    )
    assert "题面 Q1" in question_text and "本题共享材料" in question_text
    assert "答案 Q1" not in question_text
    assert not dialog._panels[1].findChildren(QLabel)
    tasks.finish("读取 Word 题目来源图片")
    assert len(dialog._image_targets) == 1
    assert all(call[-1] != "Q1-answer" for call in facade.calls if call[0] == "image")
    dialog.tabs.setCurrentIndex(1)
    answer_text = "\n".join(
        label.text() for label in dialog._panels[1].findChildren(QLabel)
    )
    assert "答案 Q1" in answer_text and "题面 Q1" not in answer_text
    tasks.finish("读取 Word 题目来源图片")
    assert any(call[-1] == "Q1-answer" for call in facade.calls)
    _cleanup(dialog, tasks)


def test_late_previous_question_image_never_updates_new_question(qt_app):
    dialog, _facade, tasks = _loaded()
    old_job = tasks.pending[0]["task_id"]
    dialog.next_button.click()
    assert dialog._current_key == "Q2"
    assert old_job in tasks.cancelled
    tasks.finish("读取 Word 题目来源图片", allow_cancelled=True)
    assert not dialog._image_targets
    assert "题面 Q2" in "\n".join(
        label.text() for label in dialog._panels[0].findChildren(QLabel)
    )
    _cleanup(dialog, tasks)


def test_preview_does_not_accept_and_points_change_invalidates_it(qt_app):
    dialog, _facade, tasks = _loaded()
    _check(dialog, "Q1")
    dialog.preview_button.click()
    tasks.finish("预览 Word 选题")
    assert dialog.preparation_reference is None
    assert dialog.import_button.isEnabled()
    assert "完整选题" in dialog.preview.toPlainText()
    assert "原图与题目范围仍需核对" in dialog.preview.toPlainText()
    dialog.points.setValue(9)
    assert dialog.preview.toPlainText() == ""
    assert not dialog.import_button.isEnabled()
    assert dialog.selections[0]["points"] == 9
    _cleanup(dialog, tasks)


@pytest.mark.parametrize(
    "changed,failed", [(False, False), (True, False), (False, True)]
)
def test_confirm_always_recompiles_and_checks_exact_reference(qt_app, changed, failed):
    dialog, facade, tasks = _loaded()
    _check(dialog, "Q1")
    dialog._persist_selection()
    tasks.flush()
    dialog.preview_button.click()
    tasks.finish("预览 Word 选题")
    facade.reference_changed, facade.reference_failed = changed, failed
    dialog.import_button.click()
    tasks.finish("确认 Word 选题参考")
    calls = [call for call in facade.calls if call[0] == "reference"]
    assert len(calls) == 2
    assert calls[0][1] == calls[1][1]
    if changed or failed:
        assert dialog.result() != QDialog.DialogCode.Accepted
        assert dialog.preparation_reference is None
        assert not dialog.import_button.isEnabled()
        _cleanup(dialog, tasks)
    else:
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.reference == dialog.preparation_reference
        assert "Q1" in dialog.preparation_reference["materials"]
        dialog.deleteLater()


def test_late_reference_does_not_restore_preview_after_selection_changes(qt_app):
    dialog, _facade, tasks = _loaded()
    _check(dialog, "Q1")
    dialog.preview_button.click()
    _check(dialog, "Q2")
    tasks.finish("预览 Word 选题")
    assert not dialog.import_button.isEnabled()
    assert dialog.preview.toPlainText() == ""
    _cleanup(dialog, tasks)


def test_image_mode_switch_invalidates_preview_and_explicit_text_only_is_confirmable(
    qt_app,
):
    dialog, facade, tasks = _loaded()
    facade.image_assets = [_preparation_asset()]
    facade.image_issues = ["另有一张 WMF 暂不支持"]
    _check(dialog, "Q1")
    assert dialog.include_images.isChecked()
    dialog.preview_button.click()
    tasks.finish("预览 Word 选题")
    assert "1 张原图" in dialog.preview.toPlainText()
    assert "WMF" in dialog.preview.toPlainText()
    assert not dialog.import_button.isEnabled()
    dialog.include_images.setChecked(False)
    assert not dialog.preview.toPlainText() and dialog._preview_reference is None
    dialog.preview_button.click()
    tasks.finish("预览 Word 选题")
    assert dialog.import_button.isEnabled()
    assert "仅文字（不带原图）" in dialog.preview.toPlainText()
    assert "WMF" in dialog.preview.toPlainText()
    assert facade.calls[-1][-1] is False
    _cleanup(dialog, tasks)


def test_mode_switch_during_background_preview_ignores_old_image_result(qt_app):
    dialog, _, tasks = _loaded()
    _check(dialog, "Q1")
    dialog.preview_button.click()
    dialog.include_images.setChecked(False)
    tasks.finish("预览 Word 选题")
    assert not dialog.preview.toPlainText()
    assert not dialog.import_button.isEnabled()
    _cleanup(dialog, tasks)


def test_changed_image_manifest_rejects_confirmation(qt_app):
    dialog, facade, tasks = _loaded()
    facade.image_assets = [_preparation_asset()]
    _check(dialog, "Q1")
    tasks.flush()
    dialog.preview_button.click()
    tasks.finish("预览 Word 选题")
    facade.image_assets = [_preparation_asset(2)]
    dialog.import_button.click()
    tasks.finish("确认 Word 选题参考")
    assert dialog.preparation_reference is None
    assert not dialog.import_button.isEnabled()
    _cleanup(dialog, tasks)


def test_over_limit_images_are_visible_and_never_silently_truncated(qt_app):
    dialog, facade, tasks = _loaded()
    facade.image_assets = [_preparation_asset(n) for n in range(13)]
    _check(dialog, "Q1")
    dialog.preview_button.click()
    tasks.finish("预览 Word 选题")
    assert "13 张原图" in dialog.preview.toPlainText()
    assert not dialog.import_button.isEnabled()
    _cleanup(dialog, tasks)


@pytest.mark.parametrize("show_scores", [False, True])
def test_export_uses_snapshot_title_and_exercise_points(qt_app, show_scores):
    dialog, facade, tasks = _loaded()
    _check(dialog, "Q1")
    dialog.points.setValue(5)
    dialog.export_title.setText("第一周离子复习")
    assert not dialog.show_student_scores.isChecked()
    dialog.show_student_scores.setChecked(show_scores)
    facade.export_warnings = ["保留静态图片，已移除内嵌编辑对象。"]
    dialog.export_button.click()
    assert not dialog.export_button.isEnabled()
    assert not dialog.import_button.isEnabled()
    tasks.finish("导出 Word 选题练习")
    call = next(call for call in facade.calls if call[0] == "export")
    assert call[1] == "第一周离子复习"
    assert call[2] == [{"key": "Q1", "revision": "revision-Q1", "points": 5}]
    assert call[3] is show_scores
    assert not dialog.student_button.isHidden()
    assert not dialog.teacher_button.isHidden()
    assert "C:/" not in dialog.status.text()
    assert "已移除内嵌编辑对象" in dialog.export_note.text()
    assert dialog.status.objectName() == "StatusAttention"
    _cleanup(dialog, tasks)


def test_unready_questions_cannot_be_selected_or_exported(qt_app):
    dialog, _facade, tasks = _loaded()
    _check(dialog, "Q3")
    assert dialog.selections == []
    assert not dialog.export_button.isEnabled()
    _check(dialog, "Q1")
    dialog._items["Q1"]["export_ready"] = False
    dialog._update_actions()
    assert not dialog.export_button.isEnabled()
    assert "暂不能导出" in dialog.export_button.toolTip()
    _cleanup(dialog, tasks)


def test_range_editor_shows_all_source_and_rejects_overlapping_answer(qt_app):
    facade = _Facade()
    item = facade.catalog["items"][0]
    source = facade.word_question_source("Q1", "revision-Q1")
    dialog = WordQuestionRangeDialog(item, source)
    assert "完整来源区块5" in dialog.original.toPlainText()
    dialog.fields["answer_start"].setValue(2)
    dialog.save_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.ranges is None
    dialog.fields["answer_start"].setValue(0)
    dialog.fields["block_end"].setValue(2)
    dialog.fields["context_start"].setValue(0)
    dialog.fields["context_end"].setValue(0)
    before = dialog.original.toPlainText()
    dialog.locate_button.click()
    assert dialog.original.toPlainText() == before
    dialog.save_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.ranges["answer_start"] is None
    assert dialog.ranges["context_start"] is None
    dialog.deleteLater()


def test_range_save_uses_old_revision_and_updates_selected_revision(
    qt_app, monkeypatch
):
    import integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog as module

    class RangeStub:
        DialogCode = QDialog.DialogCode
        ranges: ClassVar[dict] = {
            "block_start": 1,
            "question_end": 2,
            "answer_start": 3,
            "block_end": 3,
            "context_start": None,
            "context_end": None,
        }

        def __init__(self, *_args):
            pass

        def exec(self):
            return self.DialogCode.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, "WordQuestionRangeDialog", RangeStub)
    dialog, facade, tasks = _loaded()
    _check(dialog, "Q1")
    dialog.points.setValue(8)
    dialog.range_button.click()
    tasks.finish("读取 Word 完整来源")
    tasks.finish("保存 Word 题目范围")
    assert dialog.selections == [
        {"key": "Q1", "revision": "revision-Q1-edited", "points": 8}
    ]
    assert not dialog.import_button.isEnabled()
    call = next(call for call in facade.calls if call[0] == "range")
    assert call[1:3] == ("Q1", "revision-Q1")
    assert call[3] == RangeStub.ranges
    _cleanup(dialog, tasks)


def test_closing_flushes_latest_selection_before_releasing_dialog(qt_app):
    dialog, facade, tasks = _loaded()
    _check(dialog, "Q1")
    dialog.points.setValue(10)
    dialog.reject()
    assert not dialog._closed
    tasks.finish("保存 Word 勾选")
    assert dialog._closed
    assert facade.saved == [{"key": "Q1", "revision": "revision-Q1", "points": 10}]
    tasks.flush()
    dialog.deleteLater()


def test_closed_dialog_ignores_late_catalog_without_widget_updates(qt_app):
    facade, tasks = _Facade(), _Tasks()
    dialog = WordQuestionDialog(facade, tasks)
    dialog.reject()
    assert dialog._closed
    tasks.finish("读取 Word 逐题目录", allow_cancelled=True)
    assert not dialog._items
    dialog.deleteLater()


@pytest.mark.parametrize("width,height", [(1200, 820), (420, 780), (400, 680)])
def test_width_adaptation_has_no_horizontal_overflow(qt_app, width, height):
    dialog, _facade, tasks = _loaded()
    dialog.resize(width, height)
    dialog.show()
    for _ in range(5):
        qt_app.processEvents()
    assert dialog.width() == width
    assert dialog.height() == height
    assert dialog.body_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.question_list.horizontalScrollBar().maximum() == 0
    assert dialog.splitter.orientation() == (
        Qt.Orientation.Vertical if width < 760 else Qt.Orientation.Horizontal
    )
    _cleanup(dialog, tasks)

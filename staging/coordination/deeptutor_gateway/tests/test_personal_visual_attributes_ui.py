"""Synthetic native UI proposals and local fake-facade recovery; no network."""

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog
from test_personal_visual_questions_ui import _Facade, _ImmediateTasks, _item

from integrations.deeptutor_shchem_v1.desktop_personal_visual_attributes import (
    apply_teacher_edits,
    attribute_catalog,
    initial_attributes,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_attributes_dialog import (
    PersonalVisualAttributesDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import (
    PersonalVisualQuestionDialog,
)


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


CATALOG = attribute_catalog(
    {
        "knowledge_points": [
            {"id": "K11", "name": "氧化还原反应"},
            {"id": "K10", "name": "电离与离子反应"},
        ],
        "nodes": [
            {
                "node_key": "TB-1:1.1",
                "volume_id": "TB-1",
                "chapter_id": "C-1",
                "volume_title": "合成必修第一册",
                "chapter_title": "合成第一章",
                "section_title": "电离与离子反应",
            }
        ],
    }
)


def _attributes(row):
    # Pure synthetic inputs to the visual attribute initializer; these are not
    # a claimed import or CAS record and are never written to a store.
    bound = {
        **deepcopy(row),
        "candidate_revision": "synthetic-candidate-r1",
        "candidate_sha256": "c" * 64,
    }
    pages = {
        "page": {
            "source_file_id": "synthetic-page",
            "source_role": "question",
            "source_sha256": "a" * 64,
            "page_number": 1,
            "page_sha256": "b" * 64,
        }
    }
    printed = {
        "atomic_parts": [
            {
                "atomic_part_id": "synthetic-atomic",
                "classification": {
                    "primary_knowledge_K": ["K11"],
                    "supporting_knowledge_K": [],
                },
                "curriculum": {},
            }
        ]
    }
    return initial_attributes(
        bound, {"candidate": {"paper": {}}}, pages, printed, CATALOG
    )


class Facade(_Facade):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        for row in self.rows:
            row["batch_id"] = (
                "DESKTOPBATCH-" + ("a" if row["batch_id"] == "batch-a" else "b") * 32
            )
        self.attributes = {row["key"]: _attributes(row) for row in self.rows}
        self.option_calls, self.attribute_saves = [], []
        self.fail_options = self.fail_save = self.fail_detail = self.fail_refresh = (
            False
        )

    def personal_visual_questions(self, batch_id=None):
        if self.fail_refresh and self.attribute_saves:
            raise OSError("合成目录读取失败")
        result = super().personal_visual_questions(batch_id)
        result["filter_options"]["exam"] = [
            {"value": "second_mock", "label": "二模"},
            {"value": "unknown", "label": "待确认"},
        ]
        result["filter_options"]["teaching_use"] = [
            {"value": "review", "label": "复习"},
        ]
        return result

    def personal_visual_question_detail(self, *token):
        if self.fail_detail:
            raise OSError("合成详情错误")
        return super().personal_visual_question_detail(*token)

    def personal_visual_question_attribute_options(self, *token):
        self.option_calls.append(token)
        if self.fail_options:
            raise OSError("合成标签目录读取失败")
        self._find(*token)
        return {
            "attributes": deepcopy(self.attributes[token[1]]),
            "catalog": deepcopy(CATALOG),
            "history": [],
            "stored_revision": None,
        }

    def personal_visual_question_save_attributes(
        self,
        batch_id,
        key,
        revision,
        updates,
        *,
        expected_attribute_revision,
        teacher_confirmed=False,
    ):
        if self.fail_save:
            raise OSError("合成标签保存失败，可重试")
        row = self._find(batch_id, key, revision)
        assert self.attributes[key]["revision"] == expected_attribute_revision
        attributes = apply_teacher_edits(
            self.attributes[key],
            updates,
            curriculum_entries=CATALOG,
            teacher_confirmed=teacher_confirmed,
        )
        self.attributes[key] = attributes
        self.attribute_saves.append(
            (batch_id, key, revision, deepcopy(updates), teacher_confirmed)
        )
        row["facets"]["exam"] = [attributes["original_source"]["exam_type"]["value"]]
        row["attributes"] = deepcopy(attributes)
        row["facets"]["teaching_use"] = list(attributes["teaching_use_tags"])
        return {
            "detail": deepcopy(row),
            "attributes": deepcopy(attributes),
            "attribute_revision": attributes["revision"],
        }


def _editor(facade):
    row = facade.rows[0]
    return PersonalVisualAttributesDialog(
        row,
        facade.personal_visual_question_attribute_options(
            row["batch_id"], row["key"], row["revision"]
        ),
    )


def _propose(editor, *, confirmed=False):
    editor.primary_combo.setCurrentIndex(editor.primary_combo.findData("K10"))
    editor.grade_checks["grade_11"].setChecked(True)
    editor.exam_combo.setCurrentIndex(editor.exam_combo.findData("second_mock"))
    editor.source_fields["year"].setText("2026")
    editor.source_fields["region"].setText("上海")
    editor.source_fields["school"].setText("合成示例学校")
    editor.teacher_note.setPlainText("合成教师按原图人工核对，仅用于界面测试。")
    editor.curriculum_list.item(0).setCheckState(Qt.CheckState.Checked)
    editor.use_list.item(1).setCheckState(Qt.CheckState.Checked)
    editor.confirm_tags.setChecked(confirmed)
    editor._preview()
    assert editor.pages.currentIndex() == 1, editor.status.text()
    assert "修改前：" in editor.comparison.toPlainText()
    assert "修改后：" in editor.comparison.toPlainText()
    assert "原档案" in editor.comparison.toPlainText()


@pytest.mark.parametrize("confirmed", [False, True])
def test_real_editor_compares_every_field_before_returning_proposal(qt_app, confirmed):
    facade = Facade()
    before = deepcopy(facade.attributes)
    editor = _editor(facade)
    assert not editor.confirm_tags.isChecked()
    assert not editor.teacher_confirmed
    editor._confirm()
    assert editor.result() == QDialog.DialogCode.Rejected and editor.updates is None
    _propose(editor, confirmed=confirmed)
    assert "合成示例学校" in editor.comparison.toPlainText()
    assert "复习" in editor.comparison.toPlainText()
    assert "高二" in editor.comparison.toPlainText()
    assert editor.updates is None
    editor._confirm()
    assert editor.result() == QDialog.DialogCode.Accepted
    assert editor.teacher_confirmed is confirmed
    assert editor.updates and facade.attributes == before
    assert not facade.attribute_saves
    editor.close()


def test_editor_back_cancel_and_invalid_year_do_not_produce_savable_updates(qt_app):
    editor = _editor(Facade())
    _propose(editor)
    editor._back()
    assert editor.pages.currentIndex() == 0 and editor._proposal is None
    editor.source_fields["year"].setText("不是年份")
    editor._preview()
    assert editor.pages.currentIndex() == 0
    assert editor.status.objectName() == "StatusError"
    editor.reject()
    assert editor.updates is None and not editor.teacher_confirmed


def test_editor_rejects_attributes_from_another_batch(qt_app):
    facade = Facade()
    row = deepcopy(facade.rows[0])
    options = facade.personal_visual_question_attribute_options(
        row["batch_id"], row["key"], row["revision"]
    )
    row["batch_id"] = "DESKTOPBATCH-" + "f" * 32
    with pytest.raises(ValueError, match="版本不一致"):
        PersonalVisualAttributesDialog(row, options)


def _select_two(dialog):
    _item(dialog, "q-a").setCheckState(Qt.CheckState.Checked)
    dialog.question_list.setCurrentItem(_item(dialog, "q-b"))
    _item(dialog, "q-b").setCheckState(Qt.CheckState.Checked)
    dialog.question_list.setCurrentItem(_item(dialog, "q-a"))
    return deepcopy(dialog.selections)


def _accept_editor(monkeypatch, *, confirmed=False):
    def execute(editor):
        _propose(editor, confirmed=confirmed)
        editor._confirm()
        return editor.result()

    monkeypatch.setattr(PersonalVisualAttributesDialog, "exec", execute)


def test_saving_refreshes_filters_without_losing_unsaved_other_selection_or_roles(
    qt_app, monkeypatch
):
    facade = Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    original_rows = deepcopy(facade.rows)
    selections = _select_two(dialog)
    token = dialog._current_token
    assert dialog.attributes_button.isEnabled()
    _accept_editor(monkeypatch)
    dialog.attributes_button.click()
    assert len(facade.attribute_saves) == 1 and not facade.attribute_saves[0][-1]
    assert dialog.selections == selections and facade.saved == []
    assert dialog._current_token == token and dialog.attributes_button.isEnabled()
    assert dialog._image_previews[(token, "q-a-question", "question")].has_image
    assert not any(call[3].endswith("-answer") for call in facade.image_calls)
    for before, after in zip(original_rows, facade.rows, strict=True):
        for field in (
            "question_text",
            "shared_text",
            "answer_text",
            "images",
            "revision",
        ):
            assert before[field] == after[field]
    dialog.filter_panel.set_selection({"exam": {"second_mock"}})
    assert dialog.question_list.count() == 1
    assert dialog.question_list.item(0).data(256)[1] == "q-a"
    assert dialog.selections == selections
    dialog.filter_panel.set_selection({"teaching_use": {"review"}})
    assert dialog.question_list.count() == 1
    assert dialog.question_list.item(0).data(256)[1] == "q-a"
    assert dialog.selections == selections
    dialog.reject()


class DeferredTasks(_ImmediateTasks):
    def __init__(self):
        super().__init__()
        self.pending = []

    def submit(self, label, operation, *, on_success=None, on_failure=None):
        self.serial += 1
        self.pending.append((operation, on_success, on_failure))
        return f"deferred-{self.serial}"

    def flush(self, *, one=False):
        while self.pending:
            operation, success, failure = self.pending.pop(0)
            try:
                value = operation()
            except Exception as error:  # noqa: BLE001 - deliberately exercise UI recovery
                if failure:
                    failure(str(error))
            else:
                if success:
                    success(value)
            if one:
                break


def test_async_entry_waits_for_detail_and_pixels_and_protects_inflight_save(
    qt_app, monkeypatch
):
    facade, tasks = Facade(), DeferredTasks()
    dialog = PersonalVisualQuestionDialog(facade, tasks)
    assert not dialog.attributes_button.isEnabled()
    tasks.flush(one=True)
    assert dialog._detail_busy and not dialog.attributes_button.isEnabled()
    tasks.flush(one=True)
    assert (
        dialog._current_detail is not None and not dialog.attributes_button.isEnabled()
    )
    tasks.flush()
    assert dialog.attributes_button.isEnabled()
    _accept_editor(monkeypatch)
    dialog.attributes_button.click()
    assert dialog._attributes_busy and not dialog.attributes_button.isEnabled()
    tasks.flush(one=True)
    assert dialog._attributes_saving
    assert not dialog.cancel_button.isEnabled()
    dialog.reject()
    assert not dialog._closed
    assert not facade.attribute_saves
    tasks.flush()
    assert len(facade.attribute_saves) == 1
    assert dialog.attributes_button.isEnabled() and dialog.cancel_button.isEnabled()
    dialog.reject()


def test_cancel_in_editor_keeps_pixels_and_selection_without_write(qt_app, monkeypatch):
    facade = Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    selected = _select_two(dialog)
    monkeypatch.setattr(
        PersonalVisualAttributesDialog,
        "exec",
        lambda editor: QDialog.DialogCode.Rejected,
    )
    dialog.attributes_button.click()
    assert not facade.attribute_saves and not facade.saved
    assert dialog.selections == selected
    assert dialog.attributes_button.isEnabled()
    assert any(preview.has_image for preview in dialog._image_previews.values())
    assert "已取消" in dialog.status.text()
    dialog.reject()


@pytest.mark.parametrize("failure", ["options", "save", "refresh"])
def test_errors_restore_actions_and_preserve_basket_and_original_pixels(
    qt_app, monkeypatch, failure
):
    facade = Facade()
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    selected = _select_two(dialog)
    setattr(facade, "fail_" + failure, True)
    _accept_editor(monkeypatch, confirmed=True)
    dialog.attributes_button.click()
    assert not dialog._attributes_busy and not dialog._attributes_saving
    assert dialog.attributes_button.isEnabled()
    assert dialog.selections == selected and facade.saved == []
    assert any(preview.has_image for preview in dialog._image_previews.values())
    if failure == "refresh":
        assert len(facade.attribute_saves) == 1 and "标签已保存" in dialog.status.text()
        facade.fail_refresh = False
        dialog.reload_button.click()
        assert dialog.selections == selected
    else:
        assert not facade.attribute_saves
        setattr(facade, "fail_" + failure, False)
        dialog.attributes_button.click()
        assert len(facade.attribute_saves) == 1 and facade.attribute_saves[0][-1]
        assert dialog.selections == selected
    dialog.reject()


def test_detail_and_required_pixels_must_be_ready_before_tag_edit(qt_app):
    facade = Facade()
    facade.fail_detail = True
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    assert not dialog.attributes_button.isEnabled()
    dialog._edit_attributes()
    assert not facade.option_calls
    dialog.reject()
    broken = PersonalVisualQuestionDialog(Facade(image_failure=True), _ImmediateTasks())
    assert not broken.attributes_button.isEnabled()
    broken.reject()


@pytest.mark.parametrize("width,height", [(760, 780), (460, 600)])
def test_editor_keeps_comparison_and_footer_usable_at_supported_sizes(
    qt_app, width, height
):
    editor = _editor(Facade())
    editor.resize(width, height)
    editor.show()
    qt_app.processEvents()
    for button in (editor.cancel_button, editor.preview_button):
        assert button.isVisible()
        assert editor.rect().contains(button.mapTo(editor, button.rect().bottomRight()))
    _propose(editor)
    qt_app.processEvents()
    for button in (editor.cancel_button, editor.back_button, editor.save_button):
        assert button.isVisible()
        assert editor.rect().contains(button.mapTo(editor, button.rect().bottomRight()))
    assert "修改后" in editor.comparison.toPlainText()
    editor.reject()

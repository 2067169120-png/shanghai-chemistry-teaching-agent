from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from test_lecture_preparation_ui import ImageFacade

from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
    ImportWordDialog,
)


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class StudyFacade(ImageFacade):
    study = "对应知识方法的完整文字"
    calls = 0

    def imported_word_study_reference(self, *args, include_guidance=True, **kwargs):
        self.calls += 1
        result = self.imported_word_image_reference(*args, **kwargs)
        if include_guidance:
            result["include_guidance"] = True
            result["lecture_study"] = {
                "note_count": 2,
                "textbook_concept_count": 1,
                "materials": "【讲义研读参考】\n" + self.study,
            }
            result["materials"] += "\n\n" + result["lecture_study"]["materials"]
        return result


def test_explicit_preview_contains_original_and_study_and_navigation(qt_app):
    facade = StudyFacade()
    dialog = ImportWordDialog(facade, "b1")
    assert dialog.include_guidance.isChecked()
    assert not dialog.import_button.isEnabled() and facade.calls == 0
    dialog._compile_preview()
    assert facade.calls == 1
    assert "完整原文" in dialog.preview.toPlainText()
    assert facade.study in dialog.preview.toPlainText()
    assert dialog.preview_study_button.isEnabled()
    dialog._locate_study_reference()
    assert dialog.preview.textCursor().position() == dialog.preview.toPlainText().index(
        "【讲义研读参考】"
    )
    dialog._confirm()
    assert facade.calls == 2
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.reference["lecture_study"]["note_count"] == 2
    dialog.close()


def test_guidance_opt_out_invalidates_and_keeps_original_images(qt_app):
    dialog = ImportWordDialog(StudyFacade(), "b1")
    dialog._compile_preview()
    images = dialog._preview_reference["image_assets"]
    dialog.include_guidance.setChecked(False)
    assert dialog._preview_reference is None and not dialog.import_button.isEnabled()
    assert not dialog.preview_study_button.isEnabled()
    dialog._compile_preview()
    assert "lecture_study" not in dialog._preview_reference
    assert dialog._preview_reference["image_assets"] == images
    assert "完整原文" in dialog.preview.toPlainText()
    dialog.close()


@pytest.mark.parametrize("change", ["index", "range", "cancel"])
def test_changed_guidance_or_selection_cannot_accept_old_preview(qt_app, change):
    facade = StudyFacade()
    dialog = ImportWordDialog(facade, "b1")
    dialog._compile_preview()
    if change == "index":
        facade.study = "来源已更新"
        dialog._confirm()
    elif change == "range":
        dialog.block_end.setValue(2)
        dialog._confirm()
    else:
        dialog.reject()
    assert dialog.reference is None
    assert dialog.result() != QDialog.DialogCode.Accepted
    dialog.close()


def test_study_enters_complete_provider_prompt_without_authority_promotion():
    source = "【讲义研读参考】完整方法与条件；原Word区块12；本题教学标签：自动建议"
    prompt = _prompt(
        {"topic": "本次课题", "materials": source, "lesson_route": "two_periods"}
    )
    assert source in prompt
    assert "知识表达—主讲例题—独立练习—笔记归纳" in prompt
    assert "研读条目是AI改述，不能当作教材原句" in prompt
    assert "不可只凭标签生成一题并冒充所选原题" in prompt

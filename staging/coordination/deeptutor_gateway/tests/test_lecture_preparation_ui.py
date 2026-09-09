from __future__ import annotations

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from test_import_word_dialog import _Facade
from test_word_preparation_images_ui import asset, page_for, reference
from test_word_question_dialog import _Tasks

from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
    ImportWordDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.lecture_library_dialog import (
    LectureLibraryDialog,
)


@pytest.fixture
def qt_app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


class ImageFacade(_Facade):
    def imported_word_image_reference(
        self, batch, source, sha, start, end, expected_revision, *, include_images=True
    ):
        return {
            "source_selection": {
                "batch_id": batch,
                "source_id": source,
                "source_sha256": sha,
                "revision": expected_revision,
                "block_start": start,
                "block_end": end,
            },
            "materials": "完整原文 [Word区块1] 与原表格",
            "warnings": [],
            "include_images": include_images,
            "image_assets": [asset(1)] if include_images else [],
            "image_issues": ["旧图无法转换"] if self.fail and include_images else [],
        }


def test_original_dialog_prefers_requested_source_and_never_silently_falls_back(qt_app):
    facade = _Facade()
    dialog = ImportWordDialog(facade, "b1", initial_source_id="internal-answer")
    assert facade.preview_calls == [("b1", "internal-answer")]
    dialog.close()
    facade.preview_calls.clear()
    dialog = ImportWordDialog(facade, "b1", initial_source_id="missing")
    assert not facade.preview_calls and not dialog.preview_button.isEnabled()
    dialog.close()


def test_whole_source_image_reference_is_previewed_and_selection_change_invalidates(
    qt_app,
):
    dialog = ImportWordDialog(ImageFacade(), "b1")
    assert dialog.include_images.isChecked()
    dialog._compile_preview()
    assert dialog.import_button.isEnabled()
    assert dialog._preview_reference["image_assets"] == [asset(1)]
    dialog.include_images.setChecked(False)
    assert not dialog.import_button.isEnabled() and dialog._preview_reference is None
    dialog._compile_preview()
    dialog._confirm()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.reference["include_images"] is False
    assert dialog.reference["image_assets"] == []
    dialog.close()


def test_image_preview_instruction_matches_new_whole_source_handoff(qt_app):
    dialog = ImportWordDialog(ImageFacade(), "b1")
    dialog.asset_combo.setCurrentIndex(1)
    assert "带图选项" in dialog.image_note.text()
    assert "不会自动带入" not in dialog.image_note.text()
    dialog.close()


def test_bad_image_cannot_confirm_without_explicit_text_only_choice(qt_app):
    facade = ImageFacade()
    facade.fail = True
    dialog = ImportWordDialog(facade, "b1")
    dialog._compile_preview()
    assert not dialog.import_button.isEnabled()
    assert "旧图无法转换" in dialog.preview.toPlainText()
    dialog._confirm()
    assert dialog.reference is None
    dialog.include_images.setChecked(False)
    dialog._compile_preview()
    assert dialog.import_button.isEnabled()
    dialog.close()


def test_source_quality_hold_does_not_unlock_when_images_unchecked(qt_app):
    facade = ImageFacade()
    compile_reference = facade.imported_word_image_reference

    def held(*args, **kwargs):
        value = compile_reference(*args, **kwargs)
        value["reference_issues"] = ["原文已有待处理的问题"]
        return value

    facade.imported_word_image_reference = held
    dialog = ImportWordDialog(facade, "b1")
    for include in (True, False):
        dialog.include_images.setChecked(include)
        dialog._compile_preview()
        assert not dialog.import_button.isEnabled()
        assert "原文已有待处理的问题" in dialog.preview.toPlainText()
        dialog._confirm()
        assert dialog.reference is None
    dialog.close()


def source_reference(*images):
    value = reference(*images)
    del value["selections"]
    value["source_selection"] = {
        "batch_id": "b1",
        "source_id": "s1",
        "source_sha256": "a" * 64,
        "revision": "r1",
        "block_start": 1,
        "block_end": 3,
    }
    return value


def test_whole_source_handoff_uses_own_verifier_and_atomic_append(qt_app):
    page, facade, tasks = page_for([asset(1)])
    before = deepcopy(page._payload())
    called = []
    prepare = facade.import_word_question_reference
    facade.import_word_source_reference = lambda selected, existing: (
        called.append("source"),
        prepare(selected, existing),
    )[1]
    facade.import_word_question_reference = lambda *_: pytest.fail(
        "must not masquerade as question"
    )
    selected = source_reference(asset(2))
    assert page.import_word_reference(selected)
    assert page._payload() == before
    tasks.finish("导入 Word 选题图文")
    assert called == ["source"]
    assert page.image_assets.assets() == [asset(1), asset(2)]
    assert page.topic.text() == before["topic"]
    page.close()


def test_mixed_or_malformed_source_selection_rejected_without_dispatch(qt_app):
    page, _, _tasks = page_for()
    for bad in ("mixed", "bool_range", "empty_revision"):
        selected = source_reference(asset(2))
        if bad == "mixed":
            selected["selections"] = [{"key": "q", "revision": "r", "points": 1}]
        elif bad == "bool_range":
            selected["source_selection"]["block_start"] = True
        else:
            selected["source_selection"]["revision"] = ""
        before = deepcopy(page._payload())
        assert page.import_word_reference(selected) is False
        assert page._payload() == before
    page.close()


@pytest.mark.parametrize("always_fail", [False, True])
def test_render_failure_restores_original_image_model_and_no_success_signal(qt_app, monkeypatch, always_fail):
    page, facade, tasks = page_for([asset(1)])
    facade.import_word_source_reference = facade.import_word_question_reference
    before = deepcopy(page._payload())
    renders, signals = [], []
    original_render = page.image_assets._render_assets

    def broken_render():
        renders.append(True)
        if always_fail or len(renders) == 1:
            raise RuntimeError("synthetic view failure")
        original_render()

    monkeypatch.setattr(page.image_assets, "_render_assets", broken_render)
    page.image_assets.assets_changed.connect(lambda value: signals.append(value))
    assert page.import_word_reference(source_reference(asset(2)))
    tasks.finish("导入 Word 选题图文")
    assert page._payload() == before
    assert not signals and page.isEnabled()
    assert "未能完整导入" in page.status.text()
    if not always_fail:
        assert page.image_assets.asset_list.count() == 1
    page.close()


def test_lecture_picker_searches_locally_and_forwards_original_not_summary(
    qt_app, monkeypatch
):
    from integrations.deeptutor_shchem_v1.desktop_workbench import (
        lecture_library_dialog as module,
    )

    rows = [
        {
            "batch_id": "b1",
            "source_id": "s2",
            "source_name": "电离.docx",
            "title": "电离",
            "indexed": True,
            "preview": "这是索引而非原文",
            "search_text": "电离 常数",
        }
    ]
    monkeypatch.setattr(
        module, "lecture_catalog", lambda _: {"items": rows, "warnings": []}
    )
    forwarded = []

    class Original:
        DialogCode = QDialog.DialogCode

        def __init__(self, facade, batch, parent, *, initial_source_id):
            self.reference = {"materials": "真正原文"}
            forwarded.append((batch, initial_source_id))

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "ImportWordDialog", Original)
    tasks = _Tasks()
    dialog = LectureLibraryDialog(object(), tasks, lesson_topic="电离")
    tasks.finish("读取原教案知识索引")
    assert dialog.results.count() == 1
    dialog.search.setText("无结果")
    assert not dialog.open_button.isEnabled() and not dialog.preview.toPlainText()
    dialog.search.clear()
    dialog._open()
    assert forwarded == [("b1", "s2")]
    assert dialog.reference == {"materials": "真正原文"}
    dialog.close()


def test_late_catalog_after_cancel_does_not_repopulate(qt_app, monkeypatch):
    tasks = _Tasks()
    dialog = LectureLibraryDialog(object(), tasks)
    dialog.reject()
    dialog._ready({"items": [{"bad": True}]})
    assert dialog.results.count() == 0


def test_preparation_entry_passes_topic_and_preserves_other_form_fields(
    qt_app, monkeypatch
):
    from integrations.deeptutor_shchem_v1.desktop_workbench import (
        word_question_dialog as module,
    )

    page, _, _ = page_for()
    before = deepcopy(page._payload())
    topics = []

    class Picker:
        DialogCode = QDialog.DialogCode

        def __init__(self, facade, tasks, parent, *, lesson_topic):
            self.preparation_reference = {"materials": "所选原题", "warnings": []}
            topics.append(lesson_topic)

        def exec(self):
            return QDialog.DialogCode.Accepted

    monkeypatch.setattr(module, "WordQuestionDialog", Picker)
    page._import_word_questions()
    assert topics == [before["topic"]]
    assert "所选原题" in page.materials.toPlainText()
    assert all(
        page._payload()[key] == before[key] for key in before if key != "materials"
    )
    page.close()

"""Synthetic-only visual/core/Word composition and actual-page review contracts."""
from copy import deepcopy
from dataclasses import replace
import hashlib

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel
import pytest
from test_desktop_mixed_paper_ui import Facade, Tasks, png_bytes
from test_desktop_mixed_paper_ui import qt_app as qt_app

from integrations.deeptutor_shchem_v1.desktop_workbench.assembly_page import (
    MixedPaperPaginationDialog,
    MixedPaperPanel,
    PaperPage,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    MixedPaperComposerModel,
    MixedPaperPageReviewModel,
    PaperComposerModel,
)


def pagination(student=2, teacher=3):
    raw = png_bytes()
    return {
        "status": "rendered_pending_review", "manifest_sha256": "c" * 64,
        "documents": {
            audience: {"page_count": count, "pages": [
                {"page_number": n, "image_id": f"{audience}-page-{n}",
                 "sha256": hashlib.sha256(raw).hexdigest(), "width": 240, "height": 100}
                for n in range(1, count + 1)
            ]} for audience, count in (("student", student), ("teacher", teacher))
        },
    }


class VisualFacade(Facade):
    def __init__(self):
        super().__init__()
        self.rows.append({"key": "visual-a", "item_kind": "personal_visual_theme", "title_zh": "合成来源图片整主题"})
        self.pagination_failure = False
        self.page_failure = None
        self.page_counts = (2, 3)

    def paper_basket_projection(self):
        result = super().paper_basket_projection()
        for row in result["items"]:
            if row["key"] == "visual-a":
                row.update(kind="personal_visual_theme", settings={"use_source_scores": True},
                           content={"source_score_total": 7, "unknown_source_scores": 0})
        return result

    def create_paper_preview(self, payload):
        result = super().create_paper_preview(payload)
        for section in result.preview_model["sections"]:
            if section["key"] == "visual-a":
                section.update(kind="personal_visual_theme", points=7)
        self.created_preview = result
        return result

    def prepare_mixed_paper_pagination(self, preview_id, preview_hash):
        self.calls.append(("paginate", preview_id, preview_hash))
        if self.pagination_failure:
            raise RuntimeError("synthetic rendering failed")
        data = deepcopy(self.created_preview.preview_model)
        data["pagination"] = pagination(*self.page_counts)
        return replace(self.created_preview, preview_model=data, preview_hash="b" * 64)

    def paper_preview_image(self, preview_id, image_id):
        if self.page_failure == image_id:
            raise RuntimeError("synthetic missing page")
        return super().paper_preview_image(preview_id, image_id)


@pytest.fixture
def visual_panel(qt_app):
    facade, tasks = VisualFacade(), Tasks()
    widget = MixedPaperPanel(facade, tasks, PaperComposerModel())
    widget.load()
    tasks.flush()
    widget.show()
    yield widget, facade, tasks
    widget._closed = True
    if widget._preview_dialog:
        widget._preview_dialog.reject()
    widget.close()
    widget.deleteLater()
    qt_app.processEvents()


def open_pages(widget, tasks):
    widget.preview_button.click()
    tasks.flush()
    dialog = widget._preview_dialog
    assert isinstance(dialog, MixedPaperPaginationDialog)
    return dialog


def review_every_page(dialog, tasks):
    for index, audience in enumerate(("student", "teacher")):
        dialog.tabs.setCurrentIndex(index)
        for number in range(1, len(dialog.review.pages[audience]) + 1):
            dialog.page_selector.setValue(number)
            tasks.flush()
            dialog.review_page_button.click()


def test_visual_preserves_whole_theme_original_scores_and_no_extra_lines(visual_panel):
    widget, _, _ = visual_panel
    assert widget.model.order == ["core-a", "word-a", "visual-a"]
    widget.sections.setCurrentRow(2)
    assert "来源图片整主题" in widget.sections.currentItem().text()
    assert widget.points.isHidden() and widget.space.isHidden()
    assert not widget.points.isEnabled() and not widget.space.isEnabled()
    assert widget.request()["settings_by_key"]["visual-a"] == {"use_source_scores": True}
    widget.points.setValue(8)
    widget.space.setValue(5)
    assert widget.request()["settings_by_key"]["visual-a"] == {"use_source_scores": True}
    widget.up_button.click()
    assert widget.model.order == ["core-a", "visual-a", "word-a"]
    widget.sections.setCurrentRow(2)
    assert not widget.points.isHidden() and widget.points.isEnabled()
    widget.points.setValue(3.5)
    assert widget.model.settings["word-a"] == {"points": 3.5}


def test_visual_model_merge_restore_keeps_other_sources_and_fixed_settings():
    facade = VisualFacade()
    model = MixedPaperComposerModel()
    model.merge(facade.paper_basket_projection())
    model.settings["word-a"] = {"points": 4.5}
    model.move("visual-a", -1)
    draft = model.draft()
    other = MixedPaperComposerModel()
    other.merge(facade.paper_basket_projection())
    assert other.restore(draft)
    assert other.order == model.order and other.settings == model.settings
    draft["settings"]["visual-a"] = {"score_per_atomic": 2, "answer_space_lines": 3}
    assert not other.restore(draft)
    assert other.settings["visual-a"] == {"use_source_scores": True}


def test_visual_only_basket_uses_unified_composer_not_legacy_projection(qt_app):
    facade, tasks = VisualFacade(), Tasks()
    facade.rows = [facade.rows[-1]]
    widget = PaperPage(facade, tasks)
    tasks.flush()
    assert widget._mixed_panel.model.order == ["visual-a"]
    assert widget.model.themes == []
    widget.close()


def test_two_stage_preview_loads_only_current_page_and_tab_does_not_review(visual_panel):
    widget, facade, tasks = visual_panel
    dialog = open_pages(widget, tasks)
    assert [call[0] for call in facade.calls[:3]] == ["preview", "paginate", "image"]
    assert facade.calls[2][2] == "student-page-1"
    assert dialog.review.loaded == {("student", 1)}
    assert not dialog.review.reviewed
    dialog.tabs.setCurrentIndex(1)
    tasks.flush()
    assert dialog.review.loaded == {("student", 1), ("teacher", 1)}
    assert not dialog.review.reviewed and not dialog.confirm_button.isEnabled()
    assert not widget.export_button.isEnabled()


def test_all_loaded_pages_still_require_individual_explicit_review(visual_panel):
    widget, _, tasks = visual_panel
    dialog = open_pages(widget, tasks)
    for index, audience in enumerate(("student", "teacher")):
        dialog.tabs.setCurrentIndex(index)
        for page in range(1, len(dialog.review.pages[audience]) + 1):
            dialog.page_selector.setValue(page)
            tasks.flush()
    assert len(dialog.review.loaded) == 5
    assert not dialog.review.reviewed and not dialog.confirm_button.isEnabled()
    review_every_page(dialog, tasks)
    assert dialog.review.can_confirm and dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    tasks.flush()
    assert widget.export_button.isEnabled()


def test_exact_new_hash_approval_score_choice_and_export(visual_panel):
    widget, facade, tasks = visual_panel
    widget.show_scores.setChecked(True)
    dialog = open_pages(widget, tasks)
    review_every_page(dialog, tasks)
    dialog.confirm_button.click()
    tasks.flush()
    assert facade.calls[0][1]["show_question_scores"] is True
    assert any(call == ("approve", "synthetic-preview", "b" * 64) for call in facade.calls)
    widget.export_button.click()
    tasks.flush()
    assert any(call[0] == "export" and call[2] == "b" * 64 for call in facade.calls)


@pytest.mark.parametrize("failure", ["pagination_failure", "approval_failure", "middle_page"])
def test_pagination_read_or_approval_failure_never_unlocks(visual_panel, failure):
    widget, facade, tasks = visual_panel
    if failure == "middle_page":
        facade.page_failure = "teacher-page-2"
    else:
        setattr(facade, failure, True)
    widget.preview_button.click()
    tasks.flush()
    dialog = widget._preview_dialog
    if dialog:
        review_every_page(dialog, tasks)
        dialog.confirm_button.click()
        tasks.flush()
    assert not widget.export_button.isEnabled()
    if failure == "pagination_failure":
        assert dialog is None


def test_edits_invalidate_page_reviews_and_cancel_old_read_callbacks(visual_panel):
    widget, facade, tasks = visual_panel
    dialog = open_pages(widget, tasks)
    dialog.review_page_button.click()
    dialog.next_button.click()  # Queued old page read.
    loaded = set(dialog.review.loaded)
    widget.show_scores.toggle()
    assert dialog._closed and not widget._approved
    tasks.flush()
    assert dialog.review.loaded == loaded
    fresh = open_pages(widget, tasks)
    assert not fresh.review.reviewed
    assert len(fresh.review.loaded) == 1


def test_closed_dialog_cannot_approve_via_owner_callback(visual_panel):
    widget, facade, tasks = visual_panel
    dialog = open_pages(widget, tasks)
    review_every_page(dialog, tasks)
    dialog.close()
    widget._approve(dialog, widget._generation, widget._preview)
    tasks.flush()
    assert not any(call[0] == "approve" for call in facade.calls)


@pytest.mark.parametrize("mutation", [
    "missing_edition", "empty_pages", "count_mismatch", "missing_number", "duplicate_id",
    "wrong_status", "missing_hash", "wrong_hash", "invalid_width", "bool_height", "huge_image",
])
def test_pagination_manifest_fail_closed(mutation):
    data = pagination()
    student = data["documents"]["student"]
    page = student["pages"][0]
    if mutation == "missing_edition":
        del data["documents"]["teacher"]
    elif mutation == "empty_pages":
        student.update(page_count=0, pages=[])
    elif mutation == "count_mismatch":
        student["page_count"] += 1
    elif mutation == "missing_number":
        student["pages"][1]["page_number"] = 3
    elif mutation == "duplicate_id":
        student["pages"][1]["image_id"] = page["image_id"]
    elif mutation == "wrong_status":
        data["status"] = "pending"
    elif mutation == "missing_hash":
        del page["sha256"]
    elif mutation == "wrong_hash":
        data["manifest_sha256"] = "x" * 64
    elif mutation == "invalid_width":
        page["width"] = 0
    elif mutation == "bool_height":
        page["height"] = True
    elif mutation == "huge_image":
        page["width"] = 40_000_001
    with pytest.raises(ValueError):
        MixedPaperPageReviewModel(data)


@pytest.mark.parametrize("failure", ["hash", "raw", "mime", "dimensions", "missing_reply_hash"])
def test_actual_page_hash_decode_and_dimensions_required(qt_app, failure):
    tasks = Tasks()
    data = pagination(1, 1)
    raw = png_bytes()
    result = {"data": raw, "content_type": "image/png", "sha256": hashlib.sha256(raw).hexdigest()}
    if failure == "hash":
        result["sha256"] = "0" * 64
    elif failure == "raw":
        result["data"] = b"corrupted PNG"
    elif failure == "mime":
        result["content_type"] = "image/x-wmf"
    elif failure == "dimensions":
        data["documents"]["student"]["pages"][0]["height"] += 1
    else:
        del result["sha256"]
    dialog = MixedPaperPaginationDialog({"pagination": data}, tasks, lambda _id: result)
    dialog.show()
    tasks.flush()
    assert dialog.review.failed and not dialog.confirm_button.isEnabled()
    assert not dialog.review_page_button.isEnabled()
    dialog.reject()


def test_pure_review_never_marks_unloaded_page_and_new_manifest_clears_state():
    data = pagination(1, 1)
    model = MixedPaperPageReviewModel(data)
    assert not model.mark_reviewed("student", 1)
    for audience in ("student", "teacher"):
        page = model.page(audience, 1)
        assert model.mark_loaded(audience, 1, page["sha256"], page["width"], page["height"])
        assert not model.can_confirm
        assert model.mark_reviewed(audience, 1)
    assert model.can_confirm
    assert not MixedPaperPageReviewModel(data).can_confirm


@pytest.mark.parametrize("width", [420, 900])
def test_real_pages_narrow_layout_and_zoom_does_not_change_reviews(visual_panel, qt_app, width):
    widget, facade, tasks = visual_panel
    widget.resize(width, 900)
    dialog = open_pages(widget, tasks)
    dialog.resize(width, 850)
    QTest.qWait(30)
    assert dialog.width() == width
    assert dialog.tabs.widget(0).horizontalScrollBar().maximum() == 0
    calls = len(facade.calls)
    dialog.zoom.setCurrentIndex(dialog.zoom.findData(100))
    QTest.qWait(10)
    assert not dialog.review.reviewed and len(facade.calls) == calls
    assert all(label.textFormat() == Qt.TextFormat.PlainText for label in dialog.findChildren(QLabel))


def test_pagination_missing_cannot_fall_back_to_content_blocks(qt_app):
    dialog = MixedPaperPaginationDialog({"sections": [{"student_blocks": [{"kind": "text", "text": "Synthetic full body"}]}]}, Tasks(), lambda _key: None)
    assert dialog.review is None and not dialog.confirm_button.isEnabled()
    assert "Synthetic full body" not in "\n".join(label.text() for label in dialog.findChildren(QLabel))
    dialog.reject()


@pytest.mark.parametrize("failure", ["missing_pdf", "wrong_extension", "not_generated"])
def test_four_file_export_requires_complete_matching_docx_pdf(visual_panel, failure, monkeypatch):
    widget, facade, tasks = visual_panel
    dialog = open_pages(widget, tasks)
    review_every_page(dialog, tasks)
    dialog.confirm_button.click()
    tasks.flush()
    original = facade.export_paper_preview

    def incomplete(*args):
        result = original(*args)
        if failure == "missing_pdf":
            result["artifacts"].pop()
        elif failure == "wrong_extension":
            result["artifacts"][-1]["path"] = "synthetic-teacher.docx"
        else:
            result["pdf_status"] = "not_generated"
        return result

    monkeypatch.setattr(facade, "export_paper_preview", incomplete)
    widget.export_button.click()
    tasks.flush()
    assert not widget._artifact_paths
    assert all(button.isHidden() for button in widget.artifact_buttons.values())
    assert "不完整" in widget.status.text()

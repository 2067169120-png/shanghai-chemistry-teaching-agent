"""C05 real Qt navigation, pixel geometry, and temporary synthetic-store saves."""

from __future__ import annotations

import os
from copy import deepcopy
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QStyle, QStyleOptionFrame
from test_personal_visual_attributes import attribute_context, imported_visual_batch  # noqa: F401
from test_personal_visual_crops import BATCH_ID, _files, _target
from test_personal_visual_crops_ui import CropFacade, DeferredTasks, options
from test_personal_visual_questions_ui import _ImmediateTasks, _item

from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_crop_dialog import PersonalVisualCropDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.personal_visual_question_dialog import PersonalVisualQuestionDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from runtime.deeptutor_shchem.source_crop_review_qa import ImmediateTasks, SyntheticReviewFacade


@pytest.fixture
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def styled_qt_app(qt_app):
    original_style, original_sheet = qt_app.style().objectName(), qt_app.styleSheet()
    qt_app.setStyle("Fusion")
    qt_app.setStyleSheet(WORKBENCH_STYLE)
    yield qt_app
    qt_app.setStyleSheet(original_sheet)
    qt_app.setStyle(original_style)


def review(qt_app, tasks=None):
    facade = SyntheticReviewFacade()
    dialog = PersonalVisualCropDialog(facade, tasks or ImmediateTasks(), facade.plans["synthetic-shared_material"])
    dialog.resize(1160, 860)
    dialog.show()
    qt_app.processEvents()
    assert dialog._valid
    return facade, dialog


def switch(dialog, image_id):
    index = dialog.image_combo.findData(image_id)
    assert index >= 0 and dialog.image_combo.isEnabled()
    dialog.image_combo.setCurrentIndex(index)


def test_current_crop_visible_and_inline_zooms_do_not_modify_pixels(qt_app):
    facade, dialog = review(qt_app)
    before = dialog._pixel_bounds()
    assert dialog.previews.currentWidget() is dialog.current_image
    assert dialog.current_image.has_image and not dialog.new_image.has_image
    dialog.zoom.setValue(150)
    dialog.current_image.zoom.setValue(125)
    assert dialog.canvas.transform().m11() == 1.5
    assert dialog.current_image.image.transform().m11() == 1.25
    assert dialog.current_image._zoom_dialog is None
    assert dialog._pixel_bounds() == before
    QTest.mouseClick(dialog.fit_button, Qt.MouseButton.LeftButton)
    QTest.mouseClick(dialog.current_image.fit_button, Qt.MouseButton.LeftButton)
    assert dialog._pixel_bounds() == before and not facade.preview_calls
    assert int(dialog.zoom_value.text().rstrip("%")) == round(dialog.canvas.transform().m11() * 100)
    for parent, controls in ((dialog.canvas.parentWidget(), (dialog.fit_button, dialog.zoom, dialog.zoom_value)),
                             (dialog.current_image, (dialog.current_image.fit_button, dialog.current_image.zoom, dialog.current_image.zoom_value, dialog.current_image.zoom_button))):
        assert all(control.geometry().right() < parent.width() for control in controls)
        assert all(left.geometry().right() < right.geometry().left() for left, right in zip(controls, controls[1:]))
    dialog.reject()


def test_switch_page_restores_only_its_pixel_draft_and_save_targets_selected_image(qt_app):
    facade, dialog = review(qt_app)
    draft = (45, 135, 715, 620)
    dialog._set_bounds(draft)
    dialog.preview_button.click()
    expired = dialog._preview["preview_id"]
    switch(dialog, "synthetic-question")
    assert expired in facade.discarded and dialog._preview is None
    assert dialog._size == (920, 680) and dialog.options["role"] == "question"
    assert dialog._pixel_bounds() == (45, 140, 800, 520)
    dialog._set_bounds((20, 130, 820, 530))
    switch(dialog, "synthetic-shared_material")
    assert dialog._size == (760, 960) and dialog._pixel_bounds() == draft
    assert not dialog.save_button.isEnabled() and not dialog.impact_confirmed.isChecked()
    dialog.preview_button.click()
    dialog.impact_confirmed.setChecked(True)
    dialog.save_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert facade.save_calls[-1][-1] == "synthetic-shared_material"
    assert facade.preview_calls[-1][0] == "synthetic-shared_material"
    assert {row["key"] for row in dialog.saved_result["revision_changes"]} == {"synthetic-first", "synthetic-second"}


@pytest.mark.parametrize("change", [
    lambda value: value.update(key="different-question"),
    lambda value: value.update(revision="stale-revision"),
    lambda value: value.update(image_id="different-image"),
    lambda value: value.update(role="answer"),
    lambda value: value.update(page_number=9),
    lambda value: value.update(page_sha256="f" * 64),
    lambda value: value["current_image"].update(bytes=b"broken pixels"),
    lambda value: value.update(affected_question_keys=["wrong-question"]),
])
def test_failed_page_keeps_original_and_draft_never_falls_back(qt_app, change):
    facade, dialog = review(qt_app)
    old_options = deepcopy(dialog.options)
    draft = (45, 135, 715, 620)
    dialog._set_bounds(draft)
    facade.change_options = change
    switch(dialog, "synthetic-question")
    assert dialog.options == old_options and dialog._pixel_bounds() == draft
    assert dialog.image_combo.currentData() == "synthetic-shared_material"
    assert "无法核对" in dialog.status.text() and dialog.current_image.has_image
    assert dialog._valid and not dialog._busy
    assert not facade.save_calls and not dialog.save_button.isEnabled()
    dialog.reject()


def test_page_switch_after_close_and_old_preview_callbacks_cannot_change_current_job(qt_app):
    tasks = DeferredTasks()
    facade, dialog = review(qt_app, tasks)
    dialog._set_bounds((45, 135, 715, 620))
    dialog.preview_button.click()
    operation, success, failure = tasks.pending.pop(0)
    value = operation()
    success(value)
    switch(dialog, "synthetic-question")
    pending_id = dialog._task_id
    success(value)  # Duplicate late delivery from the preceding page.
    failure("late stale failure")
    assert dialog._busy and dialog._task_id == pending_id
    assert dialog.options["image_id"] == "synthetic-shared_material"
    dialog.reject()
    before = deepcopy(dialog.options)
    tasks.flush()
    assert dialog._closed and dialog.options == before and not facade.save_calls
    assert not facade.previews


def test_zoom_change_interrupts_old_drag_anchor(qt_app):
    _, dialog = review(qt_app)
    dialog.canvas.set_zoom(50)
    start = dialog.canvas.mapFromScene(QPointF(100, 240))
    QTest.mousePress(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=start)
    assert dialog.canvas._anchor is not None
    before = dialog._pixel_bounds()
    dialog.zoom.setValue(75)
    end = dialog.canvas.mapFromScene(QPointF(250, 300))
    QTest.mouseRelease(dialog.canvas.viewport(), Qt.MouseButton.LeftButton, pos=end)
    assert dialog._pixel_bounds() == before and dialog.canvas._anchor is None
    dialog.reject()


@pytest.mark.parametrize("width", [360, 420])
def test_narrow_scroll_keeps_crop_zoom_confirmation_and_actions_reachable(qt_app, width):
    facade, dialog = review(qt_app)
    dialog.resize(width, 800)
    qt_app.processEvents()
    assert dialog.width() == width and dialog.splitter.orientation() == Qt.Orientation.Vertical
    for button in (dialog.preview_button, dialog.save_button, dialog.cancel_button):
        point = button.mapTo(dialog, QPoint(0, 0))
        assert 0 <= point.x() and point.x() + button.width() <= width
        assert 0 <= point.y() and point.y() + button.height() <= dialog.height()
    dialog._set_bounds((45, 135, 715, 620))
    QTest.mouseClick(dialog.preview_button, Qt.MouseButton.LeftButton)
    qt_app.processEvents()
    assert dialog.previews.currentWidget() is dialog.new_image and dialog.new_image.has_image
    dialog.scroll.ensureWidgetVisible(dialog.new_image.zoom, 0, 0)
    qt_app.processEvents()
    assert not dialog.new_image.zoom.visibleRegion().isEmpty()
    dialog.new_image.zoom.setFocus()
    QTest.keyClick(dialog.new_image.zoom, Qt.Key.Key_Right)
    assert dialog.new_image.image.transform().m11() == dialog.new_image.zoom.value() / 100
    dialog.scroll.ensureWidgetVisible(dialog.impact_confirmed, 0, 0)
    qt_app.processEvents()
    QTest.mouseClick(dialog.impact_confirmed, Qt.MouseButton.LeftButton,
                     pos=QPoint(8, dialog.impact_confirmed.height() // 2))
    assert dialog.save_button.isEnabled()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    dialog.reject()
    assert not facade.save_calls and not facade.previews


@pytest.mark.parametrize("width", [360, 420, 1160])
def test_pixel_fields_show_four_digits_unit_and_separate_labels(styled_qt_app, width):
    _, dialog = review(styled_qt_app)
    dialog.resize(width, 800)
    dialog._set_bounds((45, 135, 715, 620))
    styled_qt_app.processEvents()
    for key, field in dialog.edges.items():
        dialog.scroll.ensureWidgetVisible(field, 0, 0)
        styled_qt_app.processEvents()
        label = dialog.edge_labels[key]
        assert label.geometry().right() < field.geometry().left()
        assert label.width() >= label.fontMetrics().horizontalAdvance(label.text())
        edit = field.lineEdit()
        style_option = QStyleOptionFrame()
        edit.initStyleOption(style_option)
        contents = edit.style().subElementRect(QStyle.SubElement.SE_LineEditContents, style_option, edit)
        margins = edit.textMargins()
        # QLineEdit reserves two more pixels at either end of its text area.
        visible_text_width = contents.width() - margins.left() - margins.right() - 4
        assert visible_text_width >= edit.fontMetrics().horizontalAdvance("9999 px")
        assert visible_text_width >= edit.fontMetrics().horizontalAdvance(field.text())
        assert edit.visibleRegion().boundingRect().width() == edit.width()
    assert dialog.scroll.horizontalScrollBar().maximum() == 0
    dialog.reject()


def _assert_fitted_crop(preview):
    view = preview.image
    visible_scene = view.mapToScene(view.viewport().rect()).boundingRect()
    assert visible_scene.contains(view.sceneRect())
    assert view.horizontalScrollBar().maximum() == 0
    assert view.verticalScrollBar().maximum() == 0


@pytest.mark.parametrize("width", [360, 420, 1160])
def test_first_preview_fits_visible_viewport_and_manual_zoom_survives_layout(styled_qt_app, width):
    _, dialog = review(styled_qt_app)
    dialog.resize(width, 800)
    styled_qt_app.processEvents()
    QTest.qWait(20)
    _assert_fitted_crop(dialog.current_image)
    assert not dialog.new_image.isVisible()
    dialog._set_bounds((45, 135, 715, 620))
    dialog.preview_button.click()
    styled_qt_app.processEvents()
    QTest.qWait(20)
    assert dialog.previews.currentWidget() is dialog.new_image
    _assert_fitted_crop(dialog.new_image)

    dialog.new_image.zoom.setValue(175)
    dialog.previews.setCurrentWidget(dialog.current_image)
    dialog.resize(width + 40, 860)
    dialog.previews.setCurrentWidget(dialog.new_image)
    styled_qt_app.processEvents()
    QTest.qWait(20)
    assert dialog.new_image.image.transform().m11() == 1.75
    assert dialog.new_image.zoom_value.text() == "175%"
    dialog.new_image.fit_button.click()
    styled_qt_app.processEvents()
    QTest.qWait(20)
    _assert_fitted_crop(dialog.new_image)
    dialog.reject()


def test_real_service_locator_scope_navigation_cancel_and_history(attribute_context, qt_app):
    context = attribute_context
    service = context["service"]
    _, row, shared, shared_options = _target(context, role="shared_material")
    other_image = next(image for image in row["images"] if image["image_id"] != shared["image_id"])
    initial = service.crop_options(BATCH_ID, row["key"], row["revision"], other_image["image_id"])
    assert [item["image_id"] for item in initial["review_images"]] == [item["image_id"] for item in row["images"]]
    for locator in initial["review_images"]:
        actual = service.crop_options(BATCH_ID, row["key"], row["revision"], locator["image_id"])
        assert all(locator[key] == actual[key] for key in ("role", "page_number", "page_sha256", "source_label"))
    facade = SimpleNamespace(
        personal_visual_question_crop_options=service.crop_options,
        personal_visual_question_preview_crop=service.preview_crop,
        personal_visual_question_save_crop=service.save_crop,
        personal_visual_question_discard_crop=service.discard_crop,
    )
    before_state = _files(context["paths"].state_root)
    provider_calls = len(context["provider"].requests)
    cancelled = PersonalVisualCropDialog(facade, _ImmediateTasks(), initial)
    switch(cancelled, shared["image_id"])
    bounds = (1, 2, shared_options["width"] - 1, shared_options["height"] - 2)
    cancelled._set_bounds(bounds)
    cancelled.preview_button.click()
    assert cancelled.new_image.has_image
    cancelled.reject()
    assert _files(context["paths"].state_root) == before_state
    assert not service.crop_previews.previews
    original_files = _files(service.root)
    saved = PersonalVisualCropDialog(facade, _ImmediateTasks(), initial)
    switch(saved, shared["image_id"])
    saved._set_bounds(bounds)
    saved.preview_button.click()
    saved.impact_confirmed.setChecked(True)
    saved.save_button.click()
    assert saved.result() == QDialog.DialogCode.Accepted
    assert {change["key"] for change in saved.saved_result["revision_changes"]} == set(shared_options["affected_question_keys"])
    updated = next(item for item in saved.saved_result["affected_rows"] if item["key"] == row["key"])
    updated_image = next(item for item in updated["images"] if item["evidence_id"] == shared["evidence_id"] and item["role"] == shared["role"])
    reopened = service.crop_options(BATCH_ID, row["key"], updated["revision"], updated_image["image_id"])
    assert tuple(reopened["pixel_bounds"].values()) == bounds
    assert len(reopened["history"]) == 1 and reopened["history"][0]["edit_origin"] == "teacher"
    assert _files(service.root) == original_files and len(context["provider"].requests) == provider_calls


def test_original_question_route_uses_final_switched_material_scope(qt_app, monkeypatch):
    facade = CropFacade()
    plans = {role: options(role) for role in ("question", "shared_material", "answer")}
    plans["question"]["affected_questions"] = plans["question"]["affected_questions"][:1]
    plans["question"]["affected_question_keys"] = ["q-a"]
    choices = [{key: plan[key] for key in ("image_id", "role", "page_number", "page_sha256", "source_label")} for plan in plans.values()]

    def crop_options(batch, key, revision, image_id):
        role = next(role for role, plan in plans.items() if plan["image_id"] == image_id)
        facade.plan = deepcopy(plans[role])
        facade.plan["review_images"] = deepcopy(choices)
        return deepcopy(facade.plan)

    monkeypatch.setattr(facade, "personal_visual_question_crop_options", crop_options)
    dialog = PersonalVisualQuestionDialog(facade, _ImmediateTasks())
    for key in ("q-a", "q-b", "q-c"):
        dialog.question_list.setCurrentItem(_item(dialog, key))
        _item(dialog, key).setCheckState(Qt.CheckState.Checked)
    dialog.question_list.setCurrentItem(_item(dialog, "q-a"))

    def save_switched(editor):
        assert editor.options["affected_question_keys"] == ["q-a"]
        switch(editor, "q-a-shared")
        editor._set_bounds((10, 12, 86, 110))
        editor.preview_button.click()
        editor.impact_confirmed.setChecked(True)
        editor.save_button.click()
        return editor.result()

    monkeypatch.setattr(PersonalVisualCropDialog, "exec", save_switched)
    entry = next(key for key in dialog._image_edit_crop_buttons if key[2] == "question")
    dialog._image_edit_crop_buttons[entry].click()
    assert [item["key"] for item in dialog.selections] == ["q-b"]
    assert dialog._current_token == ("batch-a", "q-a", "rev-a-new")
    assert "2 道题" in dialog.status.text()
    dialog.reject()

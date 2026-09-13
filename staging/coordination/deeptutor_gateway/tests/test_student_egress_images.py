"""Frozen student-page consent uses synthetic pixels, never private materials."""

from __future__ import annotations

import hashlib
import io
import os
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPlainTextEdit

from integrations.deeptutor_shchem_v1.desktop_workbench.student_egress_dialog import (
    AnalysisConfirmationDialog,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _png(color=(32, 90, 180), size=(96, 64)):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, "PNG")
    return output.getvalue()


def _confirmation():
    colors = ((25, 80, 200), (30, 170, 65), (210, 45, 75))
    roles = (
        ("question_pages", "题目页面"),
        ("reference_answer_pages", "参考答案页面"),
        ("student_work_pages", "学生作答页面"),
    )
    pages, contents = [], {}
    for index, ((role, label), color) in enumerate(zip(roles, colors, strict=True), 1):
        raw = _png(color)
        digest = hashlib.sha256(raw).hexdigest()
        pages.append(
            SimpleNamespace(
                file_id=f"file-opaque-synthetic-{index}",
                sha256=digest,
                role=role,
                role_zh=label,
                ordinal=1,
                page_number=1,
                label_zh=f"{label} · 第 1 页",
                mime_type="image/png",
                width=96,
                height=64,
            )
        )
        contents[digest] = (raw, "image/png")
    return (
        SimpleNamespace(
            student_id="student-opaque-synthetic",
            submission_id="submission-opaque-synthetic",
            expected_revision="revision-opaque-synthetic",
            provider_profile_id="profile-opaque-synthetic",
            provider_revision="provider-revision-opaque-synthetic",
            pages=tuple(pages),
            page_sha256=tuple(page.sha256 for page in pages),
            provider_label_zh="合成视觉模型 · 不调用网络",
            total_page_count=len(pages),
            page_counts_by_role={role: 1 for role, _label in roles},
            student_label_zh="匿名学生甲",
            retention_days=30,
            message_zh="确认后才发送本次冻结页面；测试没有模型连接。",
        ),
        contents,
        colors,
    )


def _dialog(confirmation, contents):
    reads = []

    def load(digest):
        assert isinstance(digest, str)
        reads.append(digest)
        return contents[digest]

    return AnalysisConfirmationDialog(confirmation, image_loader=load), reads


def _flush(app, dialog):
    for _ in range(120):
        app.processEvents()
        if not dialog._timer.isActive():
            break
    assert not dialog._timer.isActive()


def _consent(dialog):
    dialog.identifiers_clear.setChecked(True)
    dialog.egress_confirmed.setChecked(True)


def _pixel(dialog):
    return dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3]


def test_three_roles_show_distinct_actual_pixels_and_zoom(app):
    confirmation, contents, colors = _confirmation()
    dialog, reads = _dialog(confirmation, contents)
    try:
        dialog.show()
        assert dialog.tabs.currentIndex() == 0
        assert dialog.image_list.count() == 3
        assert dialog.start_button is dialog.confirm_button
        _flush(app, dialog)
        assert dialog._ready
        assert set(reads) == set(confirmation.page_sha256)
        for index, (page, color) in enumerate(
            zip(confirmation.pages, colors, strict=True)
        ):
            dialog.image_list.setCurrentRow(index)
            assert page.role_zh in dialog.image_list.item(index).text()
            assert not dialog.image_list.item(index).icon().isNull()
            assert dialog.image_preview.has_image
            assert _pixel(dialog) == color
            assert "96 × 64" in dialog.image_details.toPlainText()
        dialog.image_preview.zoom_button.click()
        assert dialog.image_preview._zoom_dialog is not None
        assert dialog.image_preview._zoom_dialog.isVisible()
        dialog.reject()
        assert dialog.image_preview._zoom_dialog is None
    finally:
        dialog.close()


@pytest.mark.parametrize(
    "privacy,egress", [(False, False), (True, False), (False, True), (True, True)]
)
def test_both_consents_and_complete_pixel_loading_are_required(app, privacy, egress):
    confirmation, contents, _colors = _confirmation()
    dialog, _reads = _dialog(confirmation, contents)
    try:
        dialog.identifiers_clear.setChecked(privacy)
        dialog.egress_confirmed.setChecked(egress)
        assert not dialog.start_button.isEnabled()
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Rejected
        _flush(app, dialog)
        assert dialog._ready
        assert dialog.start_button.isEnabled() == (privacy and egress)
        assert dialog.result() == QDialog.DialogCode.Rejected
        if not (privacy and egress):
            dialog.accept()
            _flush(app, dialog)
            assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()


def test_confirmation_rechecks_every_frozen_page_before_accepting(app):
    confirmation, contents, _colors = _confirmation()
    dialog, reads = _dialog(confirmation, contents)
    try:
        _flush(app, dialog)
        _consent(dialog)
        reads.clear()
        dialog.start_button.click()
        assert not dialog.start_button.isEnabled()
        assert dialog.result() == QDialog.DialogCode.Rejected
        _flush(app, dialog)
        assert reads == list(confirmation.page_sha256)
        assert dialog.result() == QDialog.DialogCode.Accepted
    finally:
        dialog.close()


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "invalid-pixels",
        "changed-sha",
        "wrong-mime",
        "wrong-width",
        "wrong-height",
    ],
)
def test_any_unreadable_or_mismatched_page_blocks_whole_request(app, failure):
    confirmation, contents, _colors = _confirmation()
    page = confirmation.pages[1]
    if failure == "missing":
        del contents[page.sha256]
    elif failure == "invalid-pixels":
        contents[page.sha256] = (b"not-an-image", "image/png")
    elif failure == "changed-sha":
        contents[page.sha256] = (_png((180, 180, 0)), "image/png")
    elif failure == "wrong-mime":
        contents[page.sha256] = (contents[page.sha256][0], "image/jpeg")
    elif failure == "wrong-width":
        page.width += 1
    else:
        page.height += 1
    dialog, _reads = _dialog(confirmation, contents)
    try:
        _consent(dialog)
        _flush(app, dialog)
        assert not dialog._ready
        assert not dialog.start_button.isEnabled()
        dialog.accept()
        _flush(app, dialog)
        assert dialog.result() == QDialog.DialogCode.Rejected
        if dialog.image_list.count() > 1:
            dialog.image_list.setCurrentRow(1)
            assert not dialog.image_preview.has_image
    finally:
        dialog.close()


@pytest.mark.parametrize(
    "failure",
    [
        "missing-pages",
        "empty-pages",
        "missing-hashes",
        "hash-order",
        "wrong-count",
        "wrong-role-count",
    ],
)
def test_incomplete_or_inconsistent_frozen_manifest_cannot_be_confirmed(app, failure):
    confirmation, contents, _colors = _confirmation()
    if failure == "missing-pages":
        del confirmation.pages
    elif failure == "empty-pages":
        confirmation.pages = ()
    elif failure == "missing-hashes":
        del confirmation.page_sha256
    elif failure == "hash-order":
        confirmation.page_sha256 = tuple(reversed(confirmation.page_sha256))
    elif failure == "wrong-count":
        confirmation.total_page_count += 1
    else:
        confirmation.page_counts_by_role["student_work_pages"] = 2
    dialog, _reads = _dialog(confirmation, contents)
    try:
        _consent(dialog)
        _flush(app, dialog)
        assert not dialog.start_button.isEnabled()
        dialog.accept()
        _flush(app, dialog)
        assert dialog.result() == QDialog.DialogCode.Rejected
    finally:
        dialog.close()


def test_bytes_changed_after_preview_block_confirmation_and_clear_stale_image(app):
    confirmation, contents, _colors = _confirmation()
    dialog, reads = _dialog(confirmation, contents)
    try:
        _flush(app, dialog)
        assert dialog.image_preview.has_image
        _consent(dialog)
        assert dialog.start_button.isEnabled()
        contents[confirmation.page_sha256[0]] = contents[confirmation.page_sha256[1]]
        reads.clear()
        dialog.start_button.click()
        _flush(app, dialog)
        assert confirmation.page_sha256[0] in reads
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert not dialog.start_button.isEnabled()
        assert not dialog.image_preview.has_image
    finally:
        dialog.close()


def test_cancel_during_loading_stops_local_reads_without_accepting(app):
    confirmation, contents, _colors = _confirmation()
    dialog, reads = _dialog(confirmation, contents)
    _consent(dialog)
    dialog.cancel_button.click()
    _flush(app, dialog)
    assert reads == []
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog._timer.isActive()
    dialog.close()


def test_gallery_does_not_render_internal_ids_or_hashes(app):
    confirmation, contents, _colors = _confirmation()
    dialog, _reads = _dialog(confirmation, contents)
    try:
        _flush(app, dialog)
        visible_text = [
            dialog.windowTitle(),
            *(widget.text() for widget in dialog.findChildren(QLabel)),
            *(widget.toPlainText() for widget in dialog.findChildren(QPlainTextEdit)),
        ]
        for index in range(dialog.image_list.count()):
            item = dialog.image_list.item(index)
            visible_text.extend((item.text(), item.toolTip()))
            dialog.image_list.setCurrentRow(index)
            visible_text.append(dialog.image_details.toPlainText())
        text = "\n".join(visible_text)
        hidden = (
            confirmation.student_id,
            confirmation.submission_id,
            confirmation.expected_revision,
            confirmation.provider_profile_id,
            confirmation.provider_revision,
            *confirmation.page_sha256,
            *(page.file_id for page in confirmation.pages),
        )
        assert "匿名学生甲" in text
        assert all(value not in text for value in hidden)
    finally:
        dialog.close()

"""Offscreen tests for the frozen visual-import consent gallery.

The dialog receives a page manifest and a page-id-only byte loader.  These
tests deliberately use synthetic PNG bytes so they exercise the actual pixel
path without reading source files, calling a provider, or touching personal
state.
"""

from __future__ import annotations

import hashlib
import io
import os
from collections.abc import Callable, Mapping
from copy import deepcopy

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from integrations.deeptutor_shchem_v1.desktop_visual_egress import (
    DesktopVisualEgressService,
)
from integrations.deeptutor_shchem_v1.desktop_visual_schema import (
    visual_import_request_policy,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget import (
    _LocalImagePreview,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.visual_import_egress_dialog import (
    VisualImportEgressDialog,
)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def _png(color: tuple[int, int, int] = (255, 0, 0), size: tuple[int, int] = (64, 40)) -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG", optimize=False)
    return stream.getvalue()


def _plan_for(
    specs: list[tuple[str, str, tuple[int, int, int], tuple[int, int]]],
) -> tuple[dict, dict[str, bytes]]:
    pages = []
    contents: dict[str, bytes] = {}
    for index, (source_name, source_role, color, size) in enumerate(specs, 1):
        page_id = f"PAGE-{index:03d}"
        raw = _png(color, size)
        contents[page_id] = raw
        pages.append(
            {
                "page_id": page_id,
                "source_name": source_name,
                "source_role": source_role,
                "page_number": index,
                "width": size[0],
                "height": size[1],
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mime_type": "image/png",
            }
        )
    model_label = "合成视觉模型 / 不调用网络"
    policy = visual_import_request_policy("https://example.invalid/v1", "synthetic-vision", "responses")
    return (
        {
            "preview_id": "PREVIEW-SYNTHETIC-1",
            "revision": "REV-SYNTHETIC-1",
            "batch_id": "BATCH-SYNTHETIC-1",
            "model_label": model_label,
            "pages": pages,
            "request_policy": policy,
            "confirmation_text": DesktopVisualEgressService._confirmation_text(model_label, pages, policy),
        },
        contents,
    )


def _dialog_for(
    plan: Mapping[str, object],
    contents: Mapping[str, bytes],
    *,
    reader: Callable[[str], bytes] | None = None,
):
    calls: list[str] = []

    def load(page_id: str) -> bytes:
        calls.append(page_id)
        if reader is not None:
            return reader(page_id)
        return contents[page_id]

    dialog = VisualImportEgressDialog(deepcopy(dict(plan)), load)
    return dialog, calls


def _flush(app: QApplication, dialog: VisualImportEgressDialog, limit: int = 2400) -> None:
    for _ in range(limit):
        app.processEvents()
        if not dialog._timer.isActive():
            break
    assert not dialog._timer.isActive()


def _close(dialog: VisualImportEgressDialog) -> None:
    if dialog.image_preview._zoom_dialog is not None:
        dialog.image_preview.close_zoom()
    dialog.close()


def test_gallery_is_open_by_default_and_displays_real_page_pixels(app):
    plan, contents = _plan_for(
        [("题目页.png", "question", (18, 52, 220), (64, 40))]
    )
    dialog, _calls = _dialog_for(plan, contents)

    assert dialog.tabs.currentIndex() == 0
    assert dialog.tabs.currentWidget() is not dialog.disclosure
    assert isinstance(dialog.image_preview, _LocalImagePreview)
    assert dialog.image_list.count() == 1
    _flush(app, dialog)

    assert not dialog.image_list.item(0).icon().isNull()
    assert dialog.image_preview.has_image
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (
        18,
        52,
        220,
    )
    details = dialog.image_details.toPlainText()
    assert "题目 / 共同材料" in details
    assert "题目页.png" in details
    assert "第 1 页" in details
    assert "64 × 40" in details
    assert dialog.disclosure.toPlainText() == plan["confirmation_text"]
    assert dialog.confirm_button.isEnabled()
    _close(dialog)


def test_question_answer_and_handout_pages_are_distinct_and_zoomable(app):
    plan, contents = _plan_for(
        [
            ("题面来源", "question", (220, 30, 30), (48, 32)),
            ("答案来源", "answer", (30, 180, 60), (48, 32)),
            ("讲义来源", "handout", (30, 80, 210), (48, 32)),
        ]
    )
    dialog, _calls = _dialog_for(plan, contents)
    _flush(app, dialog)

    assert dialog.image_list.count() == 3
    assert "题目 / 共同材料" in dialog.image_list.item(0).text()
    assert "参考答案" in dialog.image_list.item(1).text()
    assert "教师讲义" in dialog.image_list.item(2).text()

    dialog.image_list.setCurrentRow(1)
    app.processEvents()
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (
        30,
        180,
        60,
    )
    assert "参考答案" in dialog.image_details.toPlainText()
    assert "答案来源" in dialog.image_details.toPlainText()

    dialog.image_list.setCurrentRow(2)
    app.processEvents()
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (
        30,
        80,
        210,
    )
    assert "教师讲义" in dialog.image_details.toPlainText()
    dialog.image_preview.zoom_button.click()
    app.processEvents()
    assert dialog.image_preview._zoom_dialog is not None
    assert dialog.image_preview._zoom_dialog.isVisible()
    dialog.reject()
    assert dialog.image_preview._zoom_dialog is None


def test_request_budget_is_visible_alongside_image_not_only_in_fee_tab(app):
    plan, contents = _plan_for([("合成图片", "question", (200, 20, 20), (48, 32))])
    plan["request_policy"] = {"max_output_tokens": 32000, "timeout_seconds": 300,
                              "schema_dialect": "inline-v1"}
    dialog, _ = _dialog_for(plan, contents)
    _flush(app, dialog)
    assert "32000" in dialog.summary.text() and "300" in dialog.summary.text()
    assert dialog.tabs.currentIndex() == 0 and dialog.image_preview.has_image
    _close(dialog)


def test_default_gallery_discloses_additional_review_cost_and_uncreated_crops(app):
    plan, contents = _plan_for([
        ("冻结题目原页", "question", (200, 20, 20), (48, 32)),
        ("冻结答案原页", "answer", (20, 200, 20), (48, 32)),
    ])
    dialog, _ = _dialog_for(plan, contents)
    _flush(app, dialog)
    assert dialog.tabs.currentIndex() == 0
    assert dialog.image_list.count() == len(plan["pages"]) == 2
    assert "提取后追加原页与实际裁片的图像复核" in dialog.summary.text()
    assert "每组最多 8 条裁片" in dialog.summary.text()
    assert "按裁片数分组计费，请求次数在提取后确定" in dialog.summary.text()
    assert "复核失败不完成导入，不会自动重试" in dialog.summary.text()
    assert "当前预览为全部冻结源页；实际裁片提取后才能生成，仅来自这些源页" in dialog.summary.text()
    assert "尚未在本次发送前预览中展示" in dialog.disclosure.toPlainText()
    assert dialog.confirm_button.isEnabled() and dialog.image_preview.has_image
    _close(dialog)


def test_51_pages_are_all_kept_in_gallery_and_confirmation_rechecks_all(app):
    specs = [
        (
            f"来源-{index:02d}",
            "question" if index % 3 else "answer",
            (index * 17 % 256, index * 31 % 256, index * 47 % 256),
            (8, 6),
        )
        for index in range(1, 52)
    ]
    plan, contents = _plan_for(specs)
    dialog, calls = _dialog_for(plan, contents)
    _flush(app, dialog)

    assert dialog.image_list.count() == 51
    assert "51" in dialog.tabs.tabText(0)
    assert "来源-01" in dialog.image_list.item(0).text()
    assert "来源-51" in dialog.image_list.item(50).text()
    assert all(not dialog.image_list.item(i).icon().isNull() for i in range(51))
    assert dialog.confirm_button.isEnabled()

    before = len(calls)
    dialog.confirm_button.click()
    _flush(app, dialog)
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert set(calls[before:]) == {row["page_id"] for row in plan["pages"]}


def test_small_screen_keeps_footer_actions_visible(app):
    plan, contents = _plan_for(
        [
            ("窄窗题面", "question", (100, 100, 220), (120, 80)),
            ("窄窗答案", "answer", (220, 100, 100), (120, 80)),
        ]
    )
    dialog, _calls = _dialog_for(plan, contents)
    dialog.resize(420, 360)
    dialog.show()
    _flush(app, dialog)

    assert dialog.cancel_button.isVisible()
    assert dialog.confirm_button.isVisible()
    for button in (dialog.cancel_button, dialog.confirm_button):
        bottom_right = button.mapTo(dialog, button.rect().bottomRight())
        assert dialog.rect().contains(bottom_right)
    assert dialog.tabs.currentIndex() == 0
    _close(dialog)


def test_missing_page_blocks_confirmation_and_does_not_use_old_pixels(app):
    plan, contents = _plan_for(
        [
            ("缺失题面", "question", (230, 20, 20), (48, 32)),
            ("仍可查看", "question", (20, 20, 230), (48, 32)),
        ]
    )
    contents.pop(plan["pages"][0]["page_id"])
    dialog, _calls = _dialog_for(plan, contents)
    _flush(app, dialog)

    assert not dialog.confirm_button.isEnabled()
    assert "第 1 张" in dialog.validation_status.text()
    dialog.image_list.setCurrentRow(0)
    app.processEvents()
    assert not dialog.image_preview.has_image
    dialog.confirm_button.click()
    assert dialog.result() == QDialog.DialogCode.Rejected
    _close(dialog)


@pytest.mark.parametrize("tamper", ["digest", "dimensions"])
def test_tampered_page_is_not_accepted(app, tamper):
    plan, contents = _plan_for(
        [("篡改题面", "question", (220, 40, 40), (48, 32))]
    )
    page_id = plan["pages"][0]["page_id"]
    if tamper == "digest":
        contents[page_id] = _png((40, 40, 220), (48, 32))
    else:
        plan["pages"][0]["width"] += 1
    dialog, _calls = _dialog_for(plan, contents)
    _flush(app, dialog)

    assert not dialog.confirm_button.isEnabled()
    assert "第 1 张" in dialog.validation_status.text()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    _close(dialog)


def test_confirmation_rechecks_every_page_and_rejects_late_change(app):
    plan, contents = _plan_for(
        [
            ("稳定页", "question", (10, 130, 220), (48, 32)),
            ("确认时变化页", "answer", (220, 130, 10), (48, 32)),
            ("末页", "handout", (130, 10, 220), (48, 32)),
        ]
    )
    changed = False

    def reader(page_id: str) -> bytes:
        if changed and page_id == plan["pages"][1]["page_id"]:
            return _png((0, 0, 0), (48, 32))
        return contents[page_id]

    dialog, calls = _dialog_for(plan, contents, reader=reader)
    _flush(app, dialog)
    before = len(calls)
    changed = True
    dialog.confirm_button.click()
    _flush(app, dialog)

    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.confirm_button.isEnabled()
    assert "第 2 张" in dialog.validation_status.text()
    assert set(calls[before:]) == {row["page_id"] for row in plan["pages"]}
    _close(dialog)


def test_cancel_before_first_read_stops_local_loader_and_rejects(app):
    plan, contents = _plan_for(
        [("待读取题面", "question", (50, 100, 200), (48, 32)) for _ in range(8)]
    )
    dialog, calls = _dialog_for(plan, contents)
    assert dialog._timer.isActive()
    dialog.cancel_button.click()
    app.processEvents()

    assert calls == []
    assert not dialog._timer.isActive()
    assert dialog.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize("invalid", ["role", "duplicate", "mime"])
def test_invalid_page_role_identity_or_format_cannot_be_confirmed(app, invalid):
    specs = [
        ("第一页", "question", (60, 120, 220), (48, 32)),
        ("第二页", "answer", (220, 120, 60), (48, 32)),
    ]
    plan, contents = _plan_for(specs)
    if invalid == "role":
        plan["pages"][0]["source_role"] = "unknown-role"
    elif invalid == "duplicate":
        plan["pages"][1]["page_id"] = plan["pages"][0]["page_id"]
    else:
        plan["pages"][0]["mime_type"] = "image/tiff"

    dialog, calls = _dialog_for(plan, contents)
    assert dialog.image_list.count() == 0
    assert not dialog.confirm_button.isEnabled()
    assert "清单不完整" in dialog.validation_status.text()
    assert calls == []
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    _close(dialog)


def test_more_than_500_pages_is_rejected_without_truncating_or_reading(app):
    specs = [
        (f"超限页-{index:03d}", "question", (index % 256, 70, 180), (8, 6))
        for index in range(1, 502)
    ]
    plan, contents = _plan_for(specs)
    dialog, calls = _dialog_for(plan, contents)

    assert dialog.image_list.count() == 0
    assert not dialog.confirm_button.isEnabled()
    assert "清单不完整" in dialog.validation_status.text()
    assert calls == []
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    _close(dialog)


def test_500_pages_are_all_kept_and_loaded_without_truncation(app):
    specs = [
        (f"完整页-{index:03d}", "question", (index % 256, 100, 220), (8, 6))
        for index in range(1, 501)
    ]
    plan, contents = _plan_for(specs)
    dialog, _calls = _dialog_for(plan, contents)
    _flush(app, dialog, limit=5000)

    assert dialog.image_list.count() == 500
    assert "500" in dialog.tabs.tabText(0)
    assert "完整页-001" in dialog.image_list.item(0).text()
    assert "完整页-500" in dialog.image_list.item(499).text()
    assert not dialog.image_list.item(0).icon().isNull()
    assert not dialog.image_list.item(499).icon().isNull()
    assert dialog._cursor == 500
    assert dialog._ready
    assert dialog.confirm_button.isEnabled()
    _close(dialog)


@pytest.mark.parametrize(
    "plan",
    [
        {},
        {"pages": []},
        {"pages": None},
    ],
)
def test_empty_or_incomplete_plan_cannot_be_accepted(app, plan):
    dialog = VisualImportEgressDialog(plan, lambda _page_id: _png())

    assert dialog.image_list.count() == 0
    assert not dialog.confirm_button.isEnabled()
    assert "清单不完整" in dialog.validation_status.text()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    _close(dialog)

import copy
import hashlib
import io
import os

import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog
from test_desktop_ui import _Facade

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_egress_dialog import (
    PreparationEgressDialog,
    needs_scrollable_confirmation,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def _manifest(count=48):
    return "接收模型：合成测试模型。\n" + "\n".join(
        f"{n}. 教材图片 {n}：完整图注与来源。<b>不是HTML</b>" for n in range(1, count + 1)
    ) + "\n本次调用可能产生费用；重试可能再次计费。"


def _pictures(count=2):
    assets, contents = [], {}
    for index in range(count):
        stream = io.BytesIO()
        Image.new("RGB", (96, 64), (20 + index * 4, 50, 200 - index * 3)).save(stream, "PNG")
        raw = stream.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        row = {
            "asset_id": "IMG-" + digest, "sha256": digest,
            "caption": f"图片 {index + 1} <b>原文不是 HTML</b>",
            "source": f"合成测试来源 {index + 1}", "purpose": "验证实际像素预览",
            "width": 96, "height": 64, "content_type": "image/png",
        }
        assets.append(row)
        contents[row["asset_id"]] = raw
    return assets, contents


def _flush(app, dialog):
    for _ in range(120):
        app.processEvents()
        if not dialog._timer.isActive():
            break
    assert not dialog._timer.isActive()


def _dialog(count=2, **kwargs):
    assets, contents = _pictures(count)
    dialog = PreparationEgressDialog(
        "确认调用模型", _manifest(count), image_count=count, image_assets=assets,
        image_loader=lambda row: contents[row["asset_id"]], **kwargs,
    )
    return dialog, assets, contents


def test_compact_dialog_threshold_is_separate_from_asset_limit():
    assert not needs_scrollable_confirmation("短清单", 0)
    assert needs_scrollable_confirmation("短清单", 1)
    assert needs_scrollable_confirmation("短清单", 12)
    assert needs_scrollable_confirmation("短清单", 13)
    assert needs_scrollable_confirmation("长" * 1801, 1)
    assert not needs_scrollable_confirmation("短", "48")


def test_all_48_captions_readable_with_pinned_actions_and_default_cancel(app):
    text = _manifest()
    dialog, _, _ = _dialog(48)
    dialog.resize(560, 420)
    dialog.show()
    _flush(app, dialog)
    assert dialog.image_list.count() == 48
    assert all(not dialog.image_list.item(i).icon().isNull() for i in range(48))
    assert dialog.confirm_button.isEnabled()
    dialog.tabs.setCurrentWidget(dialog.disclosure)
    app.processEvents()
    assert dialog.disclosure.toPlainText() == text
    assert dialog.disclosure.verticalScrollBar().maximum() > 0
    assert dialog.cancel_button.isDefault() and not dialog.confirm_button.isDefault()
    for button in (dialog.cancel_button, dialog.confirm_button):
        assert button.isVisible()
        assert dialog.rect().contains(button.mapTo(dialog, button.rect().bottomRight()))
    dialog.disclosure.verticalScrollBar().setValue(dialog.disclosure.verticalScrollBar().maximum())
    assert dialog.disclosure.toPlainText().endswith("重试可能再次计费。")
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.close()


def test_confirm_requires_explicit_click_and_local_export_labels(app):
    dialog = PreparationEgressDialog("确认本地重新导出", _manifest(), local_only=True)
    dialog.show()
    app.processEvents()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.confirm_button.text() == "继续本地导出"
    dialog.confirm_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_selected_images_are_actual_distinct_pixels_and_zoomable(app):
    dialog, assets, _ = _dialog()
    dialog.show()
    assert not dialog.confirm_button.isEnabled()
    _flush(app, dialog)
    assert dialog.image_preview.has_image
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (20, 50, 200)
    dialog.image_list.setCurrentRow(1)
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (24, 50, 197)
    assert assets[1]["caption"] in dialog.image_details.toPlainText()
    assert assets[1]["source"] in dialog.image_details.toPlainText()
    assert "96 × 64" in dialog.image_details.toPlainText()
    dialog.image_preview.zoom_button.click()
    assert dialog.image_preview._zoom_dialog is not None
    assert dialog.image_preview._zoom_dialog.isVisible()
    dialog.reject()
    assert dialog.image_preview._zoom_dialog is None


def test_image_navigation_switches_actual_pixels_and_stays_in_sync_with_list(app):
    dialog, _, _ = _dialog(3)
    dialog.show()
    _flush(app, dialog)
    assert dialog.image_position.text() == "第 1 / 3 张"
    assert not dialog.previous_image_button.isEnabled()
    assert dialog.next_image_button.isEnabled()
    assert not dialog.next_image_button.autoDefault()
    dialog.next_image_button.click()
    assert dialog.image_list.currentRow() == 1
    assert dialog.image_position.text() == "第 2 / 3 张"
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (24, 50, 197)
    assert dialog.previous_image_button.isEnabled() and dialog.next_image_button.isEnabled()
    dialog.next_image_button.click()
    assert dialog.image_list.currentRow() == 2
    assert dialog.image_position.text() == "第 3 / 3 张"
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (28, 50, 194)
    assert dialog.previous_image_button.isEnabled()
    assert not dialog.next_image_button.isEnabled()
    dialog.next_image_button.click()
    assert dialog.image_list.currentRow() == 2
    dialog.previous_image_button.click()
    assert dialog.image_list.currentRow() == 1
    dialog.image_list.setCurrentRow(0)
    assert dialog.image_position.text() == "第 1 / 3 张"
    assert not dialog.previous_image_button.isEnabled()
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (20, 50, 200)
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.reject()


def test_double_click_thumbnail_opens_existing_zoom_without_accepting(app):
    dialog, _, _ = _dialog()
    dialog.show()
    _flush(app, dialog)
    item = dialog.image_list.item(1)
    position = dialog.image_list.visualItemRect(item).center()
    QTest.mouseClick(dialog.image_list.viewport(), Qt.MouseButton.LeftButton, pos=position)
    QTest.mouseDClick(dialog.image_list.viewport(), Qt.MouseButton.LeftButton, pos=position)
    app.processEvents()
    assert dialog.image_list.currentRow() == 1
    assert dialog.image_position.text() == "第 2 / 2 张"
    assert dialog.image_preview._source.toImage().pixelColor(0, 0).getRgb()[:3] == (24, 50, 197)
    assert dialog.image_preview._zoom_dialog is not None
    assert dialog.image_preview._zoom_dialog.isVisible()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog._rechecking is False
    dialog.reject()
    assert dialog.image_preview._zoom_dialog is None


def test_navigation_keeps_failed_images_blocking_confirmation(app):
    dialog, assets, contents = _dialog()
    contents.pop(assets[1]["asset_id"])
    dialog.show()
    _flush(app, dialog)
    assert not dialog.confirm_button.isEnabled()
    dialog.next_image_button.click()
    assert dialog.image_position.text() == "第 2 / 2 张"
    assert not dialog.image_preview.has_image
    assert not dialog.image_preview.zoom_button.isEnabled()
    dialog.image_list.itemDoubleClicked.emit(dialog.image_list.item(1))
    assert dialog.image_preview._zoom_dialog is None
    dialog.previous_image_button.click()
    assert dialog.image_preview.has_image
    assert not dialog.confirm_button.isEnabled()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.reject()


def test_navigation_is_hidden_without_images_and_disabled_for_one_image(app):
    empty = PreparationEgressDialog("发送前核对", "仅文字")
    empty.show()
    app.processEvents()
    assert not empty.image_navigation.isVisible()
    assert not empty.image_preview.zoom_button.isVisible()
    empty.reject()
    single, _, _ = _dialog(1)
    single.show()
    _flush(app, single)
    assert single.image_navigation.isVisible()
    assert single.image_position.text() == "第 1 / 1 张"
    assert not single.previous_image_button.isEnabled()
    assert not single.next_image_button.isEnabled()
    assert single.confirm_button.isEnabled()
    single.reject()


def test_small_gallery_keeps_navigation_raster_and_consent_actions_visible(app):
    dialog, _, _ = _dialog()
    dialog.resize(640, 480)
    dialog.show()
    _flush(app, dialog)
    assert dialog.width() <= 640 and dialog.height() <= 480
    assert dialog.image_preview.image.height() >= 120
    for widget in (
        dialog.previous_image_button, dialog.image_position, dialog.next_image_button,
        dialog.image_preview.zoom_button, dialog.cancel_button, dialog.confirm_button,
    ):
        assert widget.isVisible()
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().topLeft()))
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
    dialog.reject()


def test_confirm_revalidates_all_snapshot_bytes_then_accepts(app):
    dialog, assets, contents = _dialog()
    original = copy.deepcopy(assets)
    assets[0]["caption"] = "外部表单已修改"
    _flush(app, dialog)
    assert dialog._assets == original
    assert dialog.result() == QDialog.DialogCode.Rejected
    loaded = []

    def reader(row):
        loaded.append(row["asset_id"])
        result = contents[row["asset_id"]]
        row["caption"] = "读取器不能修改冻结清单"
        return result

    dialog._image_loader = reader
    dialog.confirm_button.click()
    assert not dialog.confirm_button.isEnabled()
    _flush(app, dialog)
    assert loaded == [row["asset_id"] for row in original]
    assert dialog._assets == original
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_changed_image_before_confirmation_blocks_send_and_clears_preview(app):
    dialog, assets, contents = _dialog()
    _flush(app, dialog)
    contents[assets[0]["asset_id"]] = contents[assets[1]["asset_id"]]
    dialog.confirm_button.click()
    _flush(app, dialog)
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert not dialog.confirm_button.isEnabled()
    assert "第 1 张" in dialog.validation_status.text()
    assert not dialog.image_preview.has_image
    dialog.close()


@pytest.mark.parametrize("failure", ["missing", "bad-pixels", "wrong-dimensions"])
def test_unreadable_image_blocks_whole_request_but_other_images_remain_viewable(app, failure):
    dialog, assets, contents = _dialog()
    if failure == "missing":
        contents.pop(assets[1]["asset_id"])
    elif failure == "bad-pixels":
        contents[assets[1]["asset_id"]] = b"broken"
    else:
        dialog._assets[1]["width"] = 97
    _flush(app, dialog)
    assert not dialog.confirm_button.isEnabled()
    assert "第 2 张" in dialog.validation_status.text()
    assert dialog.image_preview.has_image
    dialog.image_list.setCurrentRow(1)
    assert not dialog.image_preview.has_image
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.close()


@pytest.mark.parametrize("assets, count, reader", [([], 1, None), (None, 3, None), ([{}], 1, None)])
def test_missing_manifest_cannot_fall_back_to_caption_only_consent(app, assets, count, reader):
    dialog = PreparationEgressDialog(
        "确认", "含图片", image_count=count, image_assets=assets, image_loader=reader,
    )
    assert not dialog.confirm_button.isEnabled()
    assert "清单不完整" in dialog.validation_status.text()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Rejected
    dialog.close()


def test_cancel_stops_pending_local_reads_without_accepting(app):
    dialog, _, _ = _dialog(48)
    assert dialog._timer.isActive()
    dialog.reject()
    app.processEvents()
    assert dialog._cursor == 0
    assert not dialog._timer.isActive()
    assert dialog.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize("mode, local_only, expected", [
    ("vision", False, 2), ("vision", True, 0), ("local_only", False, 0),
])
def test_facade_discloses_only_sending_snapshot_assets(mode, local_only, expected):
    assets, _ = _pictures()
    preview = DesktopWorkbenchFacade._preparation_egress_disclosure(
        {"image_input_mode": mode, "image_assets": assets}, "测试模型",
        local_only_operation=local_only,
    )
    assert len(preview["image_assets"]) == expected
    assert preview["image_count"] == expected
    if expected:
        assert preview["image_assets"] == assets
        assets[0]["caption"] = "changed"
        assert preview["image_assets"][0]["caption"] != "changed"


@pytest.mark.parametrize("accepted", [False, True])
def test_page_uses_scrollable_dialog_result_without_api_call(app, monkeypatch, accepted):
    page = PreparationPage(_Facade(), DesktopTaskBridge())
    seen = []

    def execute(dialog):
        seen.append(dialog.disclosure.toPlainText())
        assert dialog.image_list.count() == 1
        _flush(app, dialog)
        assert dialog.image_preview.has_image
        return QDialog.DialogCode.Accepted if accepted else QDialog.DialogCode.Rejected

    monkeypatch.setattr(PreparationEgressDialog, "exec", execute)
    assets, contents = _pictures(1)
    page.facade.preparation_image_bytes = lambda row: contents[row["asset_id"]]
    text = _manifest(1)
    assert page._confirm_egress("确认调用模型", text, {
        "image_count": 1, "image_assets": assets,
    }) is accepted
    assert seen == [text]
    page.close()

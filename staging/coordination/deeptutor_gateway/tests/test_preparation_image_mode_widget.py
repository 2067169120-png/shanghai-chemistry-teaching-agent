from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget import (
    PreparationImagesWidget,
)


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(app):
    result = PreparationImagesWidget(SimpleNamespace())
    yield result
    result.close()


def _asset(number=1):
    digest = f"{number:064x}"
    return {
        "asset_id": "IMG-" + digest,
        "sha256": digest,
        "caption": "教材结构图",
        "source": "教师提供的课堂材料",
        "purpose": "比较结构特征",
        "width": 200,
        "height": 100,
        "content_type": "image/png",
    }


def test_default_and_old_draft_are_local_only(widget):
    assert widget.image_input_mode() == "local_only"
    assert widget.image_mode_combo.currentText() == "仅用于课件排版"
    widget.set_assets([_asset()])
    assert widget.image_input_mode() == "local_only"
    widget.set_image_input_mode("vision")
    widget.set_image_input_mode(None)
    assert widget.image_input_mode() == "local_only"


def test_mode_change_signal_supports_dirty_tracking_without_asset_change(widget):
    modes = QSignalSpy(widget.mode_changed)
    assets = QSignalSpy(widget.assets_changed)
    widget.set_image_input_mode("local_only")
    assert modes.count() == 0
    widget.set_image_input_mode("vision")
    assert modes.count() == 1
    assert modes.at(0) == ["vision"]
    widget.set_image_input_mode("vision")
    assert modes.count() == 1
    widget.image_mode_combo.setCurrentIndex(0)
    assert modes.count() == 2
    assert modes.at(1) == ["local_only"]
    assert assets.count() == 0


@pytest.mark.parametrize("value", ["auto", "", "VISION", 1, [], {}])
def test_unknown_mode_is_rejected_without_silent_downgrade(widget, value):
    widget.set_image_input_mode("vision")
    changes = QSignalSpy(widget.mode_changed)
    with pytest.raises(ValueError, match="local_only"):
        widget.set_image_input_mode(value)
    assert widget.image_input_mode() == "vision"
    assert changes.count() == 0


def test_editing_disabled_disables_mode_and_preserves_selection(widget):
    widget.set_image_input_mode("vision")
    changes = QSignalSpy(widget.mode_changed)
    widget.set_editing_enabled(False)
    assert not widget.image_mode_combo.isEnabled()
    assert not widget.add_button.isEnabled()
    assert not widget.asset_list.isEnabled()
    assert widget.image_input_mode() == "vision"
    widget.set_editing_enabled(True)
    assert widget.image_mode_combo.isEnabled()
    assert widget.image_input_mode() == "vision"
    assert changes.count() == 0


@pytest.mark.parametrize("count", [0, 1, 3])
def test_local_only_confirmation_always_sends_zero_images(widget, count):
    widget.set_assets([_asset(n) for n in range(1, count + 1)])
    text = widget.confirmation_text()
    assert "发送 0 张图片" in text
    assert "本次不发送图片像素" in text
    assert "图注、来源和用途" in text
    assert "不会直接看到图片" in text
    assert widget.privacy_label.text() == text


def test_vision_confirmation_tracks_exact_asset_count_without_changing_mode(widget):
    widget.set_image_input_mode("vision")
    widget.set_assets_strict([_asset(1), _asset(2)])
    changes = QSignalSpy(widget.mode_changed)
    assert "将发送 2 张图片像素给模型读取" in widget.confirmation_text()
    assert "图注、来源和用途也会随备课文字发送" in widget.confirmation_text()
    assert "不会直接看到图片" not in widget.confirmation_text()
    assert widget.privacy_label.text() == widget.confirmation_text()
    widget.asset_list.setCurrentRow(0)
    widget._remove_selected()
    assert "将发送 1 张图片像素" in widget.privacy_label.text()
    widget.clear_assets()
    assert widget.image_input_mode() == "vision"
    assert "发送 0 张图片" in widget.confirmation_text()
    assert "不发送图片像素" in widget.confirmation_text()
    assert "备课文字" in widget.confirmation_text()
    assert widget.privacy_label.text() == widget.confirmation_text()
    assert changes.count() == 0


def test_assets_remain_metadata_only_in_both_modes(widget):
    supplied = {**_asset(), "path": "C:/private/source.png", "base64": "SECRET"}
    widget.set_assets([supplied])
    for mode in ("local_only", "vision"):
        widget.set_image_input_mode(mode)
        assert widget.assets() == [_asset()]
        payload = {
            "image_assets": widget.assets(),
            "image_input_mode": widget.image_input_mode(),
        }
        assert "path" not in json.dumps(payload)
        assert "base64" not in json.dumps(payload)
        assert "SECRET" not in json.dumps(payload)
    returned = widget.assets()
    returned[0]["caption"] = "mutated copy"
    assert widget.assets()[0]["caption"] == "教材结构图"


def test_narrow_widget_keeps_mode_and_wrapping_privacy_notice_accessible(widget, app):
    widget.resize(320, 500)
    widget.set_image_input_mode("vision")
    widget.show()
    app.processEvents()
    assert widget.image_mode_combo.accessibleName() == "图片用法"
    assert widget.image_mode_combo.isVisible()
    assert widget.image_mode_combo.geometry().right() < widget.width()
    assert widget.privacy_label.wordWrap()
    assert widget.width() == 320

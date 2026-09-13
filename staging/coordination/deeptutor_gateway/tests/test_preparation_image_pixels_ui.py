"""Local pixels are visible without entering preparation payloads or imports."""

import hashlib
import io
import json
import os
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog

from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    MAX_IMAGE_BYTES,
    image_info,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget import (
    PreparationImageMetadataDialog,
    PreparationImagesWidget,
)


@pytest.fixture
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def png(color="red", size=(160, 80)):
    stream = io.BytesIO()
    Image.new("RGB", size, color).save(stream, format="PNG")
    return stream.getvalue()


def asset(data, caption="课堂图"):
    sha = hashlib.sha256(data).hexdigest()
    return {
        "asset_id": "IMG-" + sha,
        "sha256": sha,
        "caption": caption,
        "source": "课堂演示资料",
        "purpose": "观察颜色与结构",
        **image_info(data),
    }


def test_local_file_pixels_display_before_confirmation_and_zoom(app, tmp_path):
    path = tmp_path / "demo.png"
    path.write_bytes(png())
    dialog = PreparationImageMetadataDialog(
        str(path), caption="演示", source="教材" * 100, purpose="观察"
    )
    dialog.resize(400, 620)
    dialog.show()
    app.processEvents()
    assert dialog.preview.has_image
    assert dialog.preview._source.toImage().pixelColor(10, 10).name() == "#ff0000"
    assert dialog.source_input.lineWrapMode() == dialog.source_input.LineWrapMode.WidgetWidth
    assert dialog.width() == 400
    dialog.preview.zoom_button.click()
    zoom = dialog.preview._zoom_dialog
    assert zoom is not None and zoom.isVisible()
    zoom.zoom.setValue(150)
    assert zoom.image.pixmap().width() == 240
    dialog.reject()
    assert dialog.result() == QDialog.DialogCode.Rejected
    assert dialog.preview._zoom_dialog is None


def test_source_bytes_preview_needs_no_fabricated_file_or_reread(app, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget as module

    monkeypatch.setattr(module, "_preview_file_bytes", lambda _: pytest.fail("No file read"))
    dialog = PreparationImageMetadataDialog(
        "", preview_bytes=png("blue"), display_name="已加载原图",
        caption="原图", source="来源卷", purpose="观察",
    )
    assert dialog.preview._source.toImage().pixelColor(0, 0).name() == "#0000ff"
    dialog._accept_if_complete()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_zoom_does_not_allocate_raster_above_pixel_budget(app, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget as module

    monkeypatch.setattr(module, "MAX_IMAGE_PIXELS", 160 * 80)
    dialog = PreparationImageMetadataDialog("", preview_bytes=png())
    dialog.preview.zoom_button.click()
    assert dialog.preview._zoom_dialog.zoom.maximum() == 100
    dialog.close()


def test_oversize_file_is_rejected_without_reading_bytes(app, tmp_path, monkeypatch):
    from pathlib import Path

    path = tmp_path / "oversize.png"
    with path.open("wb") as stream:
        stream.truncate(MAX_IMAGE_BYTES + 1)
    monkeypatch.setattr(Path, "open", lambda *_a, **_k: pytest.fail("Must not read oversized file"))
    dialog = PreparationImageMetadataDialog(str(path))
    assert not dialog.preview.has_image
    assert "10MB" in dialog.preview.image.text()
    dialog.close()


def test_cancel_metadata_does_not_import_any_file(app, tmp_path, monkeypatch):
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget as module

    path = tmp_path / "demo.png"
    path.write_bytes(png())
    calls = []
    widget = PreparationImagesWidget(SimpleNamespace(import_preparation_image=lambda *a: calls.append(a)))
    monkeypatch.setattr(module.QFileDialog, "getOpenFileName", lambda *_a: (str(path), "PNG"))

    def reject(dialog):
        assert dialog.preview.has_image
        dialog.reject()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(PreparationImageMetadataDialog, "exec", reject)
    widget._choose_image()
    assert not calls and widget.assets() == []
    widget.close()


@pytest.mark.parametrize("fault", ["bad", "too_large", "pixels", "animated", "unsupported"])
def test_invalid_pixel_input_is_rejected_before_qt_decode(app, monkeypatch, fault):
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget as module

    data = b"bad"
    if fault == "too_large":
        data = b"x" * (MAX_IMAGE_BYTES + 1)
    elif fault == "pixels":
        data = png(size=(6000, 4001))
    elif fault in {"animated", "unsupported"}:
        stream = io.BytesIO()
        Image.new("RGB", (20, 20), "red").save(
            stream, format="GIF" if fault == "unsupported" else "PNG",
            save_all=True, append_images=[Image.new("RGB", (20, 20), "blue")],
        )
        data = stream.getvalue()
    monkeypatch.setattr(module._LocalImagePreview, "set_bytes", lambda *_a: pytest.fail("Unsafe Qt decode"))
    dialog = PreparationImageMetadataDialog(
        "", preview_bytes=data, caption="图", source="教材", purpose="观察"
    )
    assert not dialog.preview.has_image
    dialog._accept_if_complete()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "尚未导入" in dialog.status.text()
    dialog.close()


def test_only_selected_image_is_loaded_and_pixels_do_not_enter_metadata(app):
    raw = [png((index * 20, 0, 0)) for index in range(12)]
    assets = [asset(data, f"图{index}") for index, data in enumerate(raw)]
    by_id = {row["asset_id"]: data for row, data in zip(assets, raw, strict=True)}
    calls = []

    def load(row):
        calls.append(row["asset_id"])
        return by_id[row["asset_id"]]

    widget = PreparationImagesWidget(SimpleNamespace(preparation_image_bytes=load))
    widget.set_assets_strict(assets)
    assert calls == [assets[0]["asset_id"]]
    widget.asset_list.setCurrentRow(8)
    assert calls == [assets[0]["asset_id"], assets[8]["asset_id"]]
    assert widget.preview._caption == "图8"
    assert widget.preview._source.toImage().pixelColor(0, 0).red() == 160
    assert widget.assets() == assets
    encoded = json.dumps(widget.assets())
    assert "bytes" not in encoded and "path" not in encoded
    widget.clear_assets()
    assert widget.assets() == [] and not widget.preview.has_image
    assert not widget.preview.zoom_button.isEnabled()
    widget.close()


@pytest.mark.parametrize("fault", ["hash", "shape", "missing"])
def test_switching_to_invalid_image_clears_old_pixels_and_zoom(app, fault):
    red, blue = png(), png("blue")
    rows = [asset(red, "红图"), asset(blue, "蓝图")]
    if fault == "shape":
        rows[1]["width"] += 1

    def load(row):
        if row["caption"] == "红图":
            return red
        if fault == "missing":
            raise OSError("C:/private/raw-diagnostic")
        return red if fault == "hash" else blue

    widget = PreparationImagesWidget(SimpleNamespace(preparation_image_bytes=load))
    widget.set_assets_strict(rows)
    assert widget.preview.has_image
    widget.preview.zoom_button.click()
    assert widget.preview._zoom_dialog is not None
    widget.asset_list.setCurrentRow(1)
    assert not widget.preview.has_image
    assert widget.preview._zoom_dialog is None
    assert "无法显示" in widget.preview.image.text()
    assert "private" not in widget.preview.image.text()
    widget.close()


def test_close_during_read_does_not_restore_pixels(app):
    data = png()
    widget = None

    def load(_row):
        widget.close()
        return data

    widget = PreparationImagesWidget(SimpleNamespace(preparation_image_bytes=load))
    widget.set_assets_strict([asset(data)])
    assert not widget.preview.has_image


@pytest.mark.parametrize("width", [400, 700])
def test_long_sources_stay_inside_reading_column(app, width):
    data = png()
    row = {**asset(data), "source": "上海高中化学课堂演示来源" * 35}
    widget = PreparationImagesWidget(SimpleNamespace(preparation_image_bytes=lambda _row: data))
    widget.set_assets_strict([row])
    widget.resize(width, 580)
    widget.show()
    app.processEvents()
    assert widget.width() == width
    assert widget.preview.image.width() <= width
    assert widget.asset_list.horizontalScrollBar().maximum() == 0
    assert widget.preview.image.height() <= 140
    widget.close()

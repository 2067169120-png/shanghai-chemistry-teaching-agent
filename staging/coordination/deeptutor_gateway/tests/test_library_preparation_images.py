"""Original library pixels -> local lesson assets; no model calls or path handoff."""

import hashlib
import io
import json
from dataclasses import replace

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox
from test_desktop_library_ui import _detail, _ManualBridge, _png_bytes
from test_desktop_ui import _Facade, _fill_preparation_page

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
    PreparationImageStore,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.library_detail import (
    LibraryDetailDialog,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
    TeacherWorkbenchWindow,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget import (
    PreparationImageMetadataDialog,
)


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def source_facade(tmp_path, monkeypatch):
    import test_desktop_library as source

    stream = io.BytesIO()
    Image.new("RGB", (400, 200), "white").save(stream, format="PNG")
    raw = stream.getvalue()
    monkeypatch.setattr(source, "RAW", raw)
    monkeypatch.setattr(source, "DIGEST", hashlib.sha256(raw).hexdigest())
    facade, reader, _wave = source.fixture.__wrapped__(tmp_path)
    detail = facade.library_theme_detail(source.selected(facade))
    return facade, reader, detail.parts[0].question_images[0], raw


def test_verified_source_bytes_are_copied_without_temporary_export(source_facade):
    facade, _, image, raw = source_facade
    asset = facade.import_library_preparation_image(
        image, "原题图", "来源卷第3页", "观察条件"
    )
    store = PreparationImageStore(facade.paths.task_root / "preparation-v1/images")
    assert store.load(asset) == raw == facade.library_image(image)
    assert asset["sha256"] == image.sha256
    assert len(list(store.root.iterdir())) == 1
    assert store.import_bytes(raw, "原题图", "来源卷第3页", "观察条件") == asset
    serialized = json.dumps(asset)
    assert "view_id" not in serialized and "crop_id" not in serialized
    assert str(store.root) not in serialized


@pytest.mark.parametrize("fault", ["role", "digest", "expired", "changed_bytes"])
def test_invalid_library_origin_never_writes_image_asset(source_facade, fault):
    facade, reader, image, _ = source_facade
    if fault == "role":
        image = replace(image, role="answer")
    elif fault == "digest":
        image = replace(image, sha256="f" * 64)
    elif fault == "expired":
        facade._library_views.clear()
    else:
        reader.bad_bytes = True
    with pytest.raises(DesktopFacadeError):
        facade.import_library_preparation_image(image, "原题图", "来源卷", "观察条件")
    assert not list(facade.paths.task_root.glob("preparation-v1/images/*.image"))


@pytest.mark.parametrize("accept", [False, True])
def test_detail_image_requires_loaded_pixels_and_explicit_confirmation(
    app, monkeypatch, accept
):
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget as module

    received, metadata = [], []

    class Choice:
        def __init__(self, file_path, parent, **defaults):
            assert file_path == ""  # display label, not a fabricated file path
            metadata.append(defaults)
            self.caption = defaults["caption"]
            self.source = defaults["source"]
            self.purpose = "教师确认的观察用途"

        def exec(self):
            return (
                QDialog.DialogCode.Accepted if accept else QDialog.DialogCode.Rejected
            )

    monkeypatch.setattr(module, "PreparationImageMetadataDialog", Choice)
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    bridge = _ManualBridge()
    detail = _detail("A", "题目标题")
    dialog = LibraryDetailDialog(detail, bridge, lambda _image: _png_bytes())
    dialog.preparation_image_requested.connect(received.append)
    dialog.show()
    dialog._preparation_buttons[0].click()
    assert not metadata and not received
    first = bridge.pending.pop(0)
    bridge.succeed(first, run=True)
    dialog._preparation_buttons[0].click()
    assert metadata[0]["source"].startswith(detail.paper_title_zh)
    assert metadata[0]["preview_bytes"] == _png_bytes()
    if accept:
        assert received[0]["image"] is detail.shared_images[0]
        assert received[0]["purpose"] == "教师确认的观察用途"
        assert "来源卷" in received[0]["source"]
    else:
        assert not received and dialog.isVisible()
    dialog.close()


def test_window_handoff_preserves_form_and_assets_and_is_async(
    app, monkeypatch, source_facade
):
    real, _, image, raw = source_facade
    fixture = _Facade()
    fixture.import_library_preparation_image = real.import_library_preparation_image
    window = TeacherWorkbenchWindow(fixture)
    page = window.preparation_page
    page._availability_timer.stop()
    _fill_preparation_page(page)
    before = page._payload()
    tasks = []
    monkeypatch.setattr(
        window.tasks,
        "submit",
        lambda label, operation, **callbacks: (
            tasks.append((operation, callbacks)) or "read-image"
        ),
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    selection = {
        "image": image,
        "caption": "原题图",
        "source": "来源卷",
        "purpose": "观察已知条件",
    }
    window.library_page.preparation_image_requested.emit(selection)
    assert window.stack.currentWidget() is page
    assert not page.isEnabled()
    assert len(tasks) == 1 and not page._payload().get("image_assets")
    asset = tasks[0][0]()
    tasks[0][1]["on_success"](asset)
    assert page.isEnabled() and page._library_image_task_id is None
    after = page._payload()
    for key, value in before.items():
        assert after[key] == value
    assert after["image_assets"] == [asset]
    assert asset["sha256"] == hashlib.sha256(raw).hexdigest()
    window.library_page.preparation_image_requested.emit(selection)
    assert len(tasks) == 1  # duplicate rejected before another source read
    assert fixture.saved_preparation_payloads == []
    window.close()
    window.tasks.shutdown()


@pytest.mark.parametrize(
    "busy",
    [
        "_save_task_id",
        "_active_preparation_task_id",
        "_generation_qt_task_id",
        "_library_image_task_id",
    ],
)
def test_busy_preparation_rejects_handoff_without_mutation(app, monkeypatch, busy):
    window = TeacherWorkbenchWindow(_Facade())
    page = window.preparation_page
    page._availability_timer.stop()
    before = page._payload()
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    setattr(page, busy, "busy")
    assert not page.import_library_image({"image": _detail("A", "甲").shared_images[0]})
    assert page._payload() == before
    setattr(page, busy, None)
    window.close()
    window.tasks.shutdown()


def test_failed_image_read_restores_enabled_form_and_no_raw_error(app, monkeypatch):
    fixture = _Facade()
    window = TeacherWorkbenchWindow(fixture)
    page = window.preparation_page
    page._availability_timer.stop()
    tasks = []
    monkeypatch.setattr(
        window.tasks,
        "submit",
        lambda label, operation, **callbacks: tasks.append(callbacks) or "read-image",
    )
    before = page._payload()
    assert page.import_library_image({"image": _detail("A", "甲").shared_images[0]})
    tasks[0]["on_failure"]("raw-private-diagnostic")
    assert page.isEnabled() and page._payload() == before
    assert "raw-private" not in page.status.text()
    window.close()
    window.tasks.shutdown()


@pytest.mark.parametrize(
    "field,limit", [("caption", 160), ("source", 500), ("purpose", 500)]
)
def test_metadata_length_is_explicitly_rejected_without_truncation(app, field, limit):
    values = {"caption": "原图", "source": "教材", "purpose": "观察"}
    values[field] = "字" * (limit + 1)
    dialog = PreparationImageMetadataDialog("", preview_bytes=_png_bytes(), **values)
    assert getattr(dialog, field) == values[field]
    dialog._accept_if_complete()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "原文未被截断" in dialog.status.text()
    getattr(dialog, field + "_input").setText("字" * limit)
    dialog._accept_if_complete()
    assert dialog.result() == QDialog.DialogCode.Accepted


def test_full_asset_list_rejects_library_handoff_before_source_read(app, monkeypatch):
    window = TeacherWorkbenchWindow(_Facade())
    page = window.preparation_page
    page._availability_timer.stop()
    assets = [
        {
            "asset_id": "IMG-" + f"{index:064x}",
            "sha256": f"{index:064x}",
            "caption": "已有图片",
            "source": "教材",
            "purpose": "观察",
            "width": 400,
            "height": 200,
            "content_type": "image/png",
        }
        for index in range(page.image_assets_widget.MAX_ASSETS)
    ]
    page.image_assets_widget.set_assets(assets)
    before = page._payload()
    monkeypatch.setattr(QMessageBox, "information", lambda *_args: None)
    monkeypatch.setattr(
        window.tasks,
        "submit",
        lambda *_args, **_kwargs: pytest.fail("Must not read source when full"),
    )
    assert not page.import_library_image({"image": _detail("A", "甲").shared_images[0]})
    assert page._payload() == before
    window.close()
    window.tasks.shutdown()

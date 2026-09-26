from __future__ import annotations

import os
from types import SimpleNamespace
from typing import ClassVar

import pytest
from PySide6.QtWidgets import QApplication, QDialog
from test_desktop_ui import _Facade, _fill_preparation_page

from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget import (
    PreparationImageMetadataDialog,
    PreparationImagesWidget,
)
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


@pytest.fixture
def qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def image_asset() -> dict[str, object]:
    return {
        "asset_id": "IMG-" + "a" * 64,
        "sha256": "a" * 64,
        "caption": "教材图 2.14 氯化钠电离过程示意图",
        "source": "沪科技化学必修第一册，第 57 页",
        "purpose": "解释溶于水与熔融时的自由移动离子",
        "width": 1600,
        "height": 900,
        "content_type": "image/png",
    }


class _ImageFacade:
    def __init__(self, result: object | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, str, str]] = []

    def import_preparation_image(
        self, file_path: str, caption: str, source: str, purpose: str
    ) -> object:
        self.calls.append((file_path, caption, source, purpose))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_maximum_images_reload_and_limit_match_backend(qt_app, image_asset):
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import MAX_IMAGES

    widget = PreparationImagesWidget(_ImageFacade())
    assets = []
    for number in range(MAX_IMAGES + 1):
        sha = f"{number:064x}"
        assets.append({**image_asset, "asset_id": "IMG-" + sha, "sha256": sha})
    assert widget.MAX_ASSETS == MAX_IMAGES == 48
    for asset in assets[:MAX_IMAGES]:
        assert widget.append_asset(asset)
    assert not widget.add_button.isEnabled()
    assert not widget.append_asset(assets[MAX_IMAGES])
    assert f"{MAX_IMAGES}张" in widget.status.text()
    assert widget.assets() == assets[:MAX_IMAGES]
    reopened = PreparationImagesWidget(_ImageFacade())
    reopened.set_assets(widget.assets())
    assert reopened.assets() == assets[:MAX_IMAGES]
    assert reopened.asset_list.count() == MAX_IMAGES
    reopened.asset_list.setCurrentRow(MAX_IMAGES - 1)
    reopened._remove_selected()
    assert reopened.add_button.isEnabled()
    assert reopened.append_asset(assets[MAX_IMAGES])
    assert reopened.assets()[-1] == assets[MAX_IMAGES]
    widget.close()
    reopened.close()


@pytest.mark.parametrize("fault", ["too_many", "malformed", "duplicate"])
def test_strict_batch_replacement_is_atomic_without_truncation(
    qt_app, image_asset, fault
):
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import (
        MAX_IMAGES,
        PreparationImageError,
    )

    widget = PreparationImagesWidget(_ImageFacade())
    widget.set_assets_strict([image_asset])
    values = []
    for number in range(MAX_IMAGES + 1 if fault == "too_many" else 2):
        sha = f"{number:064x}"
        values.append({**image_asset, "asset_id": "IMG-" + sha, "sha256": sha})
    if fault == "malformed":
        values[-1]["path"] = "private-local-path"
    elif fault == "duplicate":
        values[-1] = dict(values[0])
    with pytest.raises(PreparationImageError):
        widget.set_assets_strict(values)
    assert widget.assets() == [image_asset]
    widget.close()


def test_set_assets_over_limit_keeps_existing_state(qt_app, image_asset):
    from integrations.deeptutor_shchem_v1.desktop_preparation_images import MAX_IMAGES

    widget = PreparationImagesWidget(SimpleNamespace())
    widget.set_assets([image_asset])
    before = widget.assets()
    oversized = []
    for number in range(MAX_IMAGES + 1):
        sha = f"{number + 1:064x}"
        oversized.append(
            {
                **image_asset,
                "asset_id": "IMG-" + sha,
                "sha256": sha,
            }
        )
    with pytest.raises(ValueError, match=rf"超过{MAX_IMAGES}张"):
        widget.set_assets(oversized)
    assert widget.assets() == before
    assert widget.asset_list.count() == 1
    widget.close()


class _AcceptedMetadataDialog:
    DialogCode = QDialog.DialogCode

    def __init__(self, _file_path: str, _parent=None):
        self.caption = "教材图 2.14 氯化钠电离过程示意图"
        self.source = "沪科技化学必修第一册，第 57 页"
        self.purpose = "解释自由移动离子"

    def exec(self):
        return self.DialogCode.Accepted


def test_metadata_dialog_requires_all_three_teacher_fields(qt_app, tmp_path) -> None:
    from PIL import Image

    path = tmp_path / "图2.14.png"
    Image.new("RGB", (80, 40), "green").save(path)
    dialog = PreparationImageMetadataDialog(str(path))
    dialog.show()
    qt_app.processEvents()
    dialog.confirm_button.click()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "图注" in dialog.status.text()
    dialog.caption_input.setText("图 2.14")
    dialog.source_input.setText("教材第 57 页")
    dialog.purpose_input.setText("解释电离")
    dialog.confirm_button.click()
    assert dialog.result() == QDialog.DialogCode.Accepted
    dialog.close()


def test_import_success_keeps_only_metadata_and_supports_removal(
    qt_app, monkeypatch, image_asset
) -> None:
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget as module

    facade = _ImageFacade(
        result={
            **image_asset,
            "local_path": "C:/private/teacher-book.png",
            "bytes": b"secret",
        }
    )
    widget = PreparationImagesWidget(facade)
    widget.show()
    monkeypatch.setattr(
        module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: ("C:/private/teacher-book.png", "PNG"),
    )
    monkeypatch.setattr(
        module, "PreparationImageMetadataDialog", _AcceptedMetadataDialog
    )

    widget.add_button.click()
    assert facade.calls == [
        (
            "C:/private/teacher-book.png",
            "教材图 2.14 氯化钠电离过程示意图",
            "沪科技化学必修第一册，第 57 页",
            "解释自由移动离子",
        )
    ]
    assert widget.assets() == [image_asset]
    assert widget.asset_list.count() == 1
    assert widget.asset_list.item(0).text().startswith(image_asset["caption"])
    assert image_asset["asset_id"] not in widget.asset_list.item(0).text()
    assert "private/teacher-book.png" not in widget.asset_list.item(0).text()

    widget.asset_list.setCurrentRow(0)
    widget.remove_button.click()
    assert widget.assets() == []
    widget.close()


def test_import_error_retains_existing_assets(qt_app, monkeypatch, image_asset) -> None:
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_images_widget as module

    facade = _ImageFacade(error=RuntimeError("private diagnostic"))
    widget = PreparationImagesWidget(facade)
    widget.set_assets([image_asset])
    before = widget.assets()
    monkeypatch.setattr(
        module.QFileDialog,
        "getOpenFileName",
        lambda *_args, **_kwargs: ("C:/private/fail.webp", "WebP"),
    )
    monkeypatch.setattr(
        module, "PreparationImageMetadataDialog", _AcceptedMetadataDialog
    )

    widget.add_button.click()
    assert widget.assets() == before
    assert "原有图片仍保留" in widget.status.text()
    widget.close()


def test_set_assets_sanitizes_private_values_and_empty_load_clears(
    qt_app, image_asset
) -> None:
    widget = PreparationImagesWidget(SimpleNamespace())
    widget.set_assets(
        [
            {
                **image_asset,
                "local_path": "C:/private/hidden.png",
                "image_bytes": b"hidden",
            }
        ]
    )
    assert widget.assets() == [image_asset]
    assert "local_path" not in widget.assets()[0]
    widget.set_assets(())
    assert widget.assets() == []
    assert widget.asset_list.count() == 0
    widget.close()


def test_open_draft_replaces_image_list_and_status_for_non_empty_and_empty_payload(
    qt_app, monkeypatch, image_asset
) -> None:
    import integrations.deeptutor_shchem_v1.desktop_workbench.preparation_draft_dialog as draft_module

    class Choice:
        DialogCode = QDialog.DialogCode
        payload: ClassVar[dict[str, object]] = {}

        def __init__(self, *_args, **_kwargs):
            self.selected = {"payload": dict(self.payload)}

        def exec(self):
            return self.DialogCode.Accepted

    monkeypatch.setattr(draft_module, "PreparationDraftDialog", Choice)
    facade = _Facade()
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    _fill_preparation_page(page)
    old_asset = {
        **image_asset,
        "asset_id": "IMG-" + "b" * 64,
        "sha256": "b" * 64,
        "caption": "旧草稿图片",
    }
    page.image_assets_widget.set_assets([old_asset])
    page._form_baseline = page._payload()

    Choice.payload = {**page._payload(), "image_assets": [image_asset]}
    page._open_draft()
    assert page.image_assets_widget.assets() == [image_asset]
    assert page.image_assets_widget.status.text() == "已添加 1 张教学图片。"

    page._form_baseline = page._payload()
    Choice.payload = {
        key: value for key, value in page._payload().items() if key != "image_assets"
    }
    page._open_draft()
    assert page.image_assets_widget.assets() == []
    assert page.image_assets_widget.status.text() == "尚未添加教学图片。"
    page.close()
    bridge.shutdown(1000)


def test_preparation_page_payload_is_optional_and_busy_state_locks_assets(
    qt_app, tmp_path, image_asset
) -> None:
    facade = _Facade()
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    _fill_preparation_page(page)
    assert "image_assets" not in page._payload()

    page.image_assets_widget.set_assets([image_asset])
    payload = page._payload()
    assert payload["image_assets"] == [image_asset]

    page._render_summary(
        SimpleNamespace(
            task_id="PREP-IMAGE-LOCK",
            status="running",
            progress_percent=20,
            message_zh="正在生成",
            retryable=False,
            artifact_ids=(),
            output_kind="joint",
            slide_count=0,
        )
    )
    assert not page.image_assets_widget.is_editing_enabled()
    assert not page.image_assets_widget.add_button.isEnabled()

    page._render_summary(
        SimpleNamespace(
            task_id="PREP-IMAGE-LOCK",
            status="completed",
            progress_percent=100,
            message_zh="已完成",
            retryable=False,
            artifact_ids=(),
            output_kind="joint",
            slide_count=0,
        )
    )
    assert page.image_assets_widget.is_editing_enabled()
    assert "本次不发送图片像素" in page.image_assets_widget.confirmation_text()
    assert "图注、来源和用途" in page.image_assets_widget.confirmation_text()
    page.close()
    bridge.shutdown(1000)

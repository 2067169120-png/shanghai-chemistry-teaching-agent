from __future__ import annotations

import os
from copy import deepcopy

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from test_desktop_ui import _fill_preparation_page, _PreparationFacade
from test_preparation_draft_ui import _chooser

from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import DesktopTaskBridge
from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
    PreparationPage,
)


def _preview(*, mode="vision", local=False):
    return {
        "image_input_mode": mode,
        "image_count": 2 if mode == "vision" and not local else 0,
        "images": [],
        "model_label": "已绑定模型 / frozen-vision-model",
        "confirmation_text": (
            "将使用冻结候选本地重新导出；不调用模型，不发送文字或图片。"
            if local
            else f"预检清单 {mode}：已绑定模型 / frozen-vision-model。"
            "将发送备课文字与图注、来源和用途；发送 2 张图片像素。服务可能产生费用。"
        ),
        "local_only_operation": local,
    }


class _EgressFacade(_PreparationFacade):
    def __init__(self, root):
        super().__init__(root)
        self.new_previews = []
        self.task_previews = []
        self.new_preview = _preview()
        self.task_preview = _preview()
        self.preview_error = None

    def preparation_egress_preview(self, payload, profile_id, revision):
        self.new_previews.append((deepcopy(payload), profile_id, revision))
        if self.preview_error:
            raise self.preview_error
        return deepcopy(self.new_preview)

    def preparation_task_egress_preview(self, task_id):
        self.task_previews.append(task_id)
        if self.preview_error:
            raise self.preview_error
        return deepcopy(self.task_preview)


@pytest.fixture
def page_bundle(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    facade = _EgressFacade(tmp_path)
    bridge = DesktopTaskBridge()
    page = PreparationPage(facade, bridge)
    page._availability_timer.stop()
    page._context_ready(
        (facade.preparation_availability(), facade.preparation_profiles(), ())
    )
    _fill_preparation_page(page)
    yield page, facade, app
    page.close()
    bridge.shutdown(1000)


def test_payload_omits_legacy_mode_and_mode_signal_marks_dirty(page_bundle):
    page, _, _ = page_bundle
    page._form_baseline = page._payload()
    assert "image_input_mode" not in page._payload()
    page.image_assets_widget.set_image_input_mode("vision")
    assert page._payload()["image_input_mode"] == "vision"
    assert page.isWindowModified()
    assert page._payload() != page._form_baseline
    page._saving_payload = page._payload()
    page._saved(None)
    assert not page.isWindowModified()
    page.image_assets_widget.set_image_input_mode("local_only")
    assert "image_input_mode" not in page._payload()
    assert page.isWindowModified()


@pytest.mark.parametrize("mode", [None, "vision"])
def test_draft_restores_mode_without_retaining_previous_selection(
    page_bundle, monkeypatch, mode
):
    page, facade, _ = page_bundle
    payload = page._payload()
    if mode:
        payload["image_input_mode"] = mode
    page.image_assets_widget.set_image_input_mode(
        "vision" if mode is None else "local_only"
    )
    _chooser(monkeypatch, payload)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes
    )
    page._open_draft()
    assert page.image_assets_widget.image_input_mode() == (mode or "local_only")
    assert page._payload() == page._form_baseline
    assert not page.isWindowModified()
    assert not facade.new_previews and not facade.prepare_calls


def test_new_generation_preflights_before_confirm_and_uses_preview_text(
    page_bundle, monkeypatch
):
    page, facade, _ = page_bundle
    page.image_assets_widget.set_image_input_mode("vision")
    started = []
    prompts = []
    monkeypatch.setattr(page, "_start_generation_task", started.append)

    def confirm(*args):
        assert len(facade.new_previews) == 1
        assert not facade.prepare_calls
        prompts.append(args[2])
        # The submitted snapshot cannot drift with the live form during consent.
        page.image_assets_widget.set_image_input_mode("local_only")
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    page._generate()
    assert prompts == [facade.new_preview["confirmation_text"] + "\n\n是否继续？"]
    assert facade.prepare_calls[0] == facade.new_previews[0]
    assert facade.prepare_calls[0][0]["image_input_mode"] == "vision"
    assert started == [facade.prepared]


def test_cancel_after_preflight_does_not_prepare(page_bundle, monkeypatch):
    page, facade, _ = page_bundle
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: QMessageBox.StandardButton.No
    )
    page._generate()
    assert len(facade.new_previews) == 1
    assert not facade.prepare_calls and not facade.generate_calls


@pytest.mark.parametrize(
    "error",
    [
        DesktopFacadeError("vision_required", "所选模型不支持读图。"),
        RuntimeError("private diagnostic"),
    ],
)
def test_new_preflight_failure_prevents_confirmation_and_prepare(
    page_bundle, monkeypatch, error
):
    page, facade, _ = page_bundle
    facade.preview_error = error
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: pytest.fail("must not confirm")
    )
    page._generate()
    assert not facade.prepare_calls and not facade.generate_calls
    assert "private diagnostic" not in page.status.text()
    assert (
        "不支持读图" in page.status.text()
        if isinstance(error, DesktopFacadeError)
        else "预检未能完成" in page.status.text()
    )


@pytest.mark.parametrize("preview", [None, {}, {"confirmation_text": " "}])
def test_malformed_preview_cannot_be_silently_confirmed(
    page_bundle, monkeypatch, preview
):
    page, facade, _ = page_bundle
    facade.new_preview = preview
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: pytest.fail("must not confirm")
    )
    page._generate()
    assert not facade.prepare_calls
    assert "预检未能完成" in page.status.text()


@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("local", [False, True])
def test_history_uses_frozen_preview_not_current_form_or_profile(
    page_bundle, monkeypatch, retry, local
):
    page, facade, _ = page_bundle
    summary = facade.cancelled if retry else facade.prepared
    facade.set_current(summary)
    facade.task_preview = _preview(mode="vision", local=local)
    page._restore_summary(summary)
    page.image_assets_widget.set_image_input_mode("local_only")
    page._profiles = ()
    page._availability = None
    monkeypatch.setattr(
        page,
        "_selected_profile",
        lambda: pytest.fail("history must not read current model"),
    )
    monkeypatch.setattr(
        page.image_assets_widget,
        "confirmation_text",
        lambda: "LIVE-FORM-MODE-MUST-NOT-ENTER-CONSENT",
    )
    started = []
    prompts = []
    monkeypatch.setattr(page, "_start_generation_task", started.append)

    def confirm(*args):
        assert facade.task_previews == [summary.task_id]
        prompts.append((args[1], args[2]))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", confirm)
    page._activate_current_task()
    assert not facade.new_previews and not facade.prepare_calls
    assert prompts[0][1] == facade.task_preview["confirmation_text"] + "\n\n是否继续？"
    assert "LIVE-FORM-MODE" not in prompts[0][1]
    assert prompts[0][0] == (
        "确认本地重新导出" if local else "确认重试备课候选" if retry else "确认继续生成"
    )
    assert facade.retry_calls == ([summary.task_id] if retry else [])
    assert started == [facade.prepared]
    assert started[0].task_id == summary.task_id


def test_history_preflight_failure_does_not_retry_or_confirm(page_bundle, monkeypatch):
    page, facade, _ = page_bundle
    facade.set_current(facade.cancelled)
    page._restore_summary(facade.cancelled)
    facade.preview_error = DesktopFacadeError(
        "image_changed", "冻结图片已变化，请重新添加。"
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: pytest.fail("must not confirm")
    )
    page._activate_current_task()
    assert facade.task_previews == [facade.cancelled.task_id]
    assert not facade.retry_calls and not facade.generate_calls
    assert "冻结图片已变化" in page.status.text()


def test_history_preview_requires_explicit_local_operation_flag(
    page_bundle, monkeypatch
):
    page, facade, _ = page_bundle
    facade.set_current(facade.prepared)
    page._restore_summary(facade.prepared)
    del facade.task_preview["local_only_operation"]
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_: pytest.fail("must not confirm")
    )
    page._activate_current_task()
    assert not facade.get_calls and not facade.generate_calls
    assert "历史任务发送预检未能完成" in page.status.text()

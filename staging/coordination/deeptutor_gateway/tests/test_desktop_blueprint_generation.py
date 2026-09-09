from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import desktop_prompt_blueprint
from integrations.deeptutor_shchem_v1.desktop_blueprint_generation import (
    BlueprintGenerationError,
    generate_blueprint,
    validate_blueprint,
)
from integrations.deeptutor_shchem_v1.desktop_chemistry_prompt_rules import (
    CHEMISTRY_CONSISTENCY_RULES,
)
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopFacadeError
from integrations.deeptutor_shchem_v1.intake_imports import PinnedVisualTransport
from integrations.deeptutor_shchem_v1.model_provider_probe import (
    ModelProviderProbeError,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_facade import (
    build_facade,
    desktop_paths,  # noqa: F401
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_preparation_facade import (
    _ProviderStore,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_preparation_provider import (
    _context,
    _Transport,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_prompt_blueprint import (
    PAYLOAD,
    SECTIONS,
    compiler,
)


def candidate() -> dict:
    return {
        "theme_center": "共同语境中的任务设计",
        "shared_material_plan": [
            {
                "material_id": "M1",
                "purpose": "规划供学生分析的共同材料",
                "evidence_refs": ["E1"],
            }
        ],
        "question_chain": [
            {
                "printed_question_id": "Q1",
                "atomic_part_id": "Q1-A1",
                "task_plan": "识别材料中的核心关系",
                "response_form": "简答",
                "material_refs": ["M1"],
                "depends_on": [],
                "answer_outline": "先确定条件再组织依据，具体数据待补齐",
                "knowledge_evidence": "依据 E1；编码 unknown",
                "difficulty_evidence": "一步信息提取；实测 unknown",
            }
        ],
        "unknowns": ["完整题面、数据与解答仍须补齐"],
    }


def preview() -> dict:
    return desktop_prompt_blueprint.compile_preview(
        Path.cwd(),
        PAYLOAD,
        SECTIONS,
        compiler=compiler,
    )


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
def test_exact_compiled_evidence_reaches_text_only_request(style):
    transport = _Transport(candidate())
    local = preview()
    result = generate_blueprint(_context(api_style=style), local, transport=transport)
    assert result["model_invoked"] and result["result_kind"] == "blueprint_only"
    assert result["publication_allowed"] is False
    assert result["usage"]["total_tokens"] == 30
    body = json.loads(transport.requests[0].body)
    text = json.dumps(body, ensure_ascii=False)
    assert "系统提示" in text and "电子转移" in text and "E1" in text
    assert CHEMISTRY_CONSISTENCY_RULES in text.replace("\\n", "\n")
    assert "fixture-secret" not in text and "source_path" not in text
    assert "image_url" not in text and "input_image" not in text


@pytest.mark.parametrize(
    "mutation",
    [
        "future",
        "missing_material",
        "duplicate_atomic",
        "split_printed",
        "forged_evidence",
        "missing_field",
    ],
)
def test_invalid_question_chain_is_not_accepted(mutation):
    value = candidate()
    first = value["question_chain"][0]
    if mutation == "future":
        first["depends_on"] = ["Q2-A1"]
    elif mutation == "missing_material":
        first["material_refs"] = ["not-in-theme"]
    elif mutation == "duplicate_atomic":
        value["question_chain"].append(deepcopy(first))
    elif mutation == "split_printed":
        value["question_chain"] += [
            dict(first, printed_question_id="Q2", atomic_part_id="Q2-A1"),
            dict(first, atomic_part_id="Q1-A2"),
        ]
    elif mutation == "forged_evidence":
        value["shared_material_plan"][0]["evidence_refs"] = ["E999"]
    else:
        first.pop("answer_outline")
    with pytest.raises(BlueprintGenerationError):
        validate_blueprint(value, 1)


@pytest.fixture
def configured(desktop_paths, monkeypatch):  # noqa: F811 - imported pytest fixture
    provider = _ProviderStore()
    facade = build_facade(desktop_paths, provider=provider)
    facade.prompt_curriculum_sections = lambda: tuple(
        SimpleNamespace(section_key=k, display_label_zh=v) for k, v in SECTIONS.items()
    )
    original = desktop_prompt_blueprint.compile_preview
    monkeypatch.setattr(
        desktop_prompt_blueprint,
        "compile_preview",
        lambda root, payload, labels, **kwargs: original(
            root, payload, labels, compiler=compiler, **kwargs
        ),
    )
    facade._blueprint_transport = _Transport(candidate())
    result = facade.compile_prompt_blueprint(PAYLOAD)
    return facade, provider, result


def test_confirmation_revision_and_precancel_precede_key_access(configured):
    facade, provider, local = configured
    for kwargs in (
        {"teacher_confirmed": False},
        {"teacher_confirmed": True, "should_cancel": lambda: True},
    ):
        with pytest.raises(DesktopFacadeError):
            facade.generate_prompt_blueprint(
                local["preview_id"], "teacher-text", provider.revision, **kwargs
            )
    with pytest.raises(DesktopFacadeError, match="变化"):
        facade.generate_prompt_blueprint(
            local["preview_id"], "teacher-text", "stale", teacher_confirmed=True
        )
    assert provider.borrow_calls == []
    assert facade._blueprint_transport.requests == []


def test_saved_result_reopens_and_repeat_does_not_call_or_read_key(configured):
    facade, provider, local = configured
    result = facade.generate_prompt_blueprint(
        local["preview_id"], "teacher-text", provider.revision, teacher_confirmed=True
    )
    repeated = facade.generate_prompt_blueprint(
        local["preview_id"], "teacher-text", provider.revision, teacher_confirmed=True
    )
    assert repeated == result and len(provider.borrow_calls) == 1
    assert not provider.borrow_active
    reopened = build_facade(facade.paths)
    assert reopened.prompt_blueprint_history()[0]["result"] == result
    saved = facade.state_store.path.read_text(encoding="utf-8")
    assert "fixture-secret" not in saved
    assert "Authorization" not in saved


def test_failed_model_is_not_saved_as_success_and_can_be_explicitly_retried(configured):
    facade, provider, local = configured
    facade._blueprint_transport = _Transport({})
    with pytest.raises(DesktopFacadeError):
        facade.generate_prompt_blueprint(
            local["preview_id"],
            "teacher-text",
            provider.revision,
            teacher_confirmed=True,
        )
    assert facade.prompt_blueprint_history() == ()
    assert (
        facade.state_store.snapshot()["drafts"][local["preview_id"]]["status"]
        == "failed"
    )
    facade._blueprint_transport = _Transport(candidate())
    facade.generate_prompt_blueprint(
        local["preview_id"], "teacher-text", provider.revision, teacher_confirmed=True
    )
    assert len(provider.borrow_calls) == 2


def test_interrupted_record_requires_fresh_preview_without_key_read(configured):
    facade, provider, local = configured
    record = facade.state_store.snapshot()["drafts"][local["preview_id"]]
    record["status"] = "running"
    facade.state_store.save_draft(local["preview_id"], record)
    with pytest.raises(DesktopFacadeError, match="重新编译"):
        facade.generate_prompt_blueprint(
            local["preview_id"],
            "teacher-text",
            provider.revision,
            teacher_confirmed=True,
        )
    assert provider.borrow_calls == []


def test_precancelled_provider_never_sends():
    transport = _Transport(candidate())
    with pytest.raises(BlueprintGenerationError, match="停止"):
        generate_blueprint(
            _context(), preview(), transport=transport, should_cancel=lambda: True
        )
    assert transport.requests == []


def test_transport_keeps_visual_default_and_accepts_explicit_text_budget():
    assert PinnedVisualTransport()._transport._total_timeout_seconds == 90
    assert (
        PinnedVisualTransport(
            total_timeout_seconds=300
        )._transport._total_timeout_seconds
        == 300
    )


def test_safe_failure_code_is_saved_without_exception_text(configured):
    facade, provider, local = configured

    class FailedTransport:
        def send(self, *args, **kwargs):
            raise ModelProviderProbeError("timeout", "private fixture diagnostics")

    facade._blueprint_transport = FailedTransport()
    with pytest.raises(DesktopFacadeError) as failure:
        facade.generate_prompt_blueprint(
            local["preview_id"],
            "teacher-text",
            provider.revision,
            teacher_confirmed=True,
        )
    assert failure.value.code == "timeout"
    assert "private fixture" not in str(failure.value)
    record = facade.state_store.snapshot()["drafts"][local["preview_id"]]
    assert record["error_code"] == "timeout" and record["status"] == "failed"
    assert "private fixture" not in facade.state_store.path.read_text(encoding="utf-8")


def test_incomplete_response_diagnostic_only_keeps_status_and_usage():
    class Incomplete(_Transport):
        def send(self, *args, **kwargs):
            response = super().send(*args, **kwargs)
            envelope = {
                "status": "incomplete",
                "error": None,
                "incomplete_details": {
                    "reason": "max_output_tokens",
                    "extra": "private-content",
                },
                "output_text": "private-content",
                "usage": {"input_tokens": 2, "output_tokens": 8},
            }
            return replace(response, body=json.dumps(envelope).encode())

    with pytest.raises(BlueprintGenerationError) as failure:
        generate_blueprint(_context(), preview(), transport=Incomplete(candidate()))
    summary = failure.value.response_summary
    assert summary["status"] == "incomplete"
    assert summary["incomplete_reason"] == "max_output_tokens"
    assert summary["usage"] == {"input_tokens": 2, "output_tokens": 8}
    assert "private-content" not in json.dumps(summary)
    assert "模型已连接" in failure.value.message_zh
    assert "输出额度" in failure.value.message_zh
    assert "DNS" not in failure.value.message_zh


def test_blueprint_budget_has_room_for_reasoning_and_is_not_a_retry(monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_blueprint_generation

    monkeypatch.setattr(desktop_blueprint_generation.time, "monotonic", lambda: 100.0)

    class TimedTransport(_Transport):
        def send(self, request, **kwargs):
            assert kwargs["deadline_monotonic"] == 400.0
            return super().send(request, **kwargs)

    transport = TimedTransport(candidate())
    generate_blueprint(_context(), preview(), transport=transport)
    assert len(transport.requests) == 1
    request = json.loads(transport.requests[0].body)
    assert request.get("max_output_tokens", request.get("max_tokens")) == 24000


def test_dialog_close_requests_stop_before_dismissing(configured, monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
        PromptBlueprintDialog,
    )

    facade, provider, local = configured
    app = QApplication.instance() or QApplication([])

    class PendingTasks:
        def submit(self, label, operation, *, on_success, on_failure):
            on_success(operation())

        def submit_progress(self, label, operation, *, on_success, on_failure):
            self.pending = (operation, on_failure)

    tasks = PendingTasks()
    dialog = PromptBlueprintDialog(facade, tasks)
    dialog._compiled(local)
    dialog.show()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    dialog.generate_button.click()
    assert dialog._generating
    dialog.close()
    assert not dialog._closed and dialog._stop.is_set()
    operation, failure_callback = tasks.pending
    with pytest.raises(DesktopFacadeError) as failure:
        operation(lambda _: None, lambda: False)
    failure_callback(failure.value.message_zh)
    assert provider.borrow_calls == []
    assert not dialog._generating
    dialog.close()
    app.processEvents()
    assert dialog._closed


def test_native_dialog_confirmation_save_history_and_input_invalidation(
    configured, monkeypatch
):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QMessageBox

    from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
        PromptBlueprintDialog,
    )

    facade, provider, _local = configured
    app = QApplication.instance() or QApplication([])

    class Tasks:
        def submit(self, label, operation, *, on_success, on_failure):
            try:
                on_success(operation())
            except Exception as exc:  # noqa: BLE001 - mirrors UI task failure boundary
                on_failure(str(exc))

        def submit_progress(self, label, operation, *, on_success, on_failure):
            self.submit(
                label,
                lambda: operation(lambda _: None, lambda: False),
                on_success=on_success,
                on_failure=on_failure,
            )

    dialog = PromptBlueprintDialog(facade, Tasks())
    dialog.title_input.setText(PAYLOAD["title"])
    dialog.goal.setPlainText(PAYLOAD["learning_goal"])
    dialog.sections.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.compile_button.click()
    assert dialog.generate_button.isEnabled()
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    dialog.generate_button.click()
    assert provider.borrow_calls == []
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    dialog.generate_button.click()
    assert len(provider.borrow_calls) == 1
    assert "已保存" in dialog.status.text()
    assert "共同语境" in dialog.generated_output.toPlainText()
    assert not dialog.generate_button.isEnabled()
    assert dialog.history.count() == 2
    dialog.title_input.setText("修改课题")
    assert not dialog.generated_output.toPlainText()
    assert not dialog.generate_button.isEnabled()
    dialog.history.setCurrentIndex(1)
    assert dialog.title_input.text() == PAYLOAD["title"]
    assert "共同语境" in dialog.generated_output.toPlainText()
    dialog.close()
    app.processEvents()

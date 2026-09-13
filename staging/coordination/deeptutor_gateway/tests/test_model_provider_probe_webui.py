from __future__ import annotations

import hashlib
import json
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
ROOT_MANIFEST = WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json"
INNER_MANIFEST = OVERLAY / "overlay.manifest.json"
APP = OVERLAY / "app.js"
INDEX = OVERLAY / "index.html"
STYLES = OVERLAY / "styles.css"
LAUNCHER = WORKSPACE / "integrations/deeptutor_shchem_v1/launcher.py"

PROMPT_SHA256 = "48543a5c638a299313060725e611d2d27dc163eb4044ac91e7b017453a591e3f"
PROMPT_BYTES = 101


def _slice(source: str, start: str, end: str) -> str:
    return source[source.index(start) : source.index(end, source.index(start))]


def test_chinese_ui_requires_preview_and_second_explicit_confirmation():
    html = INDEX.read_text(encoding="utf-8")
    assert ">测试连接<" in html
    assert "不可编辑的出站预览" in html
    assert "固定合成文本摘要" in html
    assert 'id="modelProviderProbeConfirm"' in html
    assert "我已核对上方完整出站预览，并明确同意" in html
    assert 'id="modelProviderProbeSubmit"' in html
    assert "确认并发送合成探测" in html
    assert 'id="modelProviderProbeCancel"' in html
    assert "可能调用所选模型并产生少量费用" in html
    assert "上游响应正文不会保存或显示" in html
    assert "store=false · background=false" in html
    assert "不等于云端零留存" in html
    assert "真实学生图片" in html
    assert "未授权商业题图" in html
    assert "production_model_invocation_enabled=false" in html


def test_browser_posts_only_revision_and_random_idempotency_key_then_polls_and_cancels():
    app = APP.read_text(encoding="utf-8")
    start = _slice(
        app,
        "async function startModelProviderProbe()",
        "async function cancelModelProviderProbe()",
    )
    cancel = _slice(
        app,
        "async function cancelModelProviderProbe()",
        "function candidateReviewPath",
    )
    poll = _slice(
        app,
        "async function pollModelProviderProbe",
        "async function startModelProviderProbe",
    )
    assert "window.crypto.getRandomValues" in app
    assert "probe_${" in app
    assert "JSON.stringify({ expected_revision: profile.revision, idempotency_key: idempotencyKey })" in start
    assert 'body: JSON.stringify({ expected_revision: revision })' in cancel
    assert "/test/${encodeURIComponent(runId)}" in poll
    assert "/test/${encodeURIComponent(probe.probe_run_id)}/cancel" in cancel
    for forbidden in ("api_key", "prompt:", "question", "student", "base_url", "provider_id", "model_id"):
        assert forbidden not in start
    for forbidden in ("api_key", "prompt:", "question", "student", "base_url", "provider_id", "model_id"):
        assert forbidden not in cancel


def test_probe_dto_is_closed_and_terminal_renderer_never_exposes_raw_content_or_full_ids():
    app = APP.read_text(encoding="utf-8")
    validator = _slice(
        app,
        "function validateModelProviderProbe",
        "function modelProviderProbeIsActive",
    )
    renderer = _slice(
        app,
        "function renderModelProviderProbe",
        "function openModelProviderProbePreview",
    )
    for status in (
        "queued",
        "running",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
        "stale",
    ):
        assert f'"{status}"' in validator
    for field in (
        "latency_ms",
        "usage",
        "cost",
        "egress",
        "model_invoked",
        "production_model_invocation_enabled",
        "receipt_id",
        "receipt_sha256",
    ):
        assert f'"{field}"' in validator
    assert "modelProviderExactKeys(value, keys)" in validator
    assert PROMPT_SHA256 in app
    assert "modelProviderSyntheticProbeContract.promptSha256" in validator
    assert "modelProviderSyntheticProbeContract.promptBytes" in validator
    assert "probe.receipt_id" not in renderer
    assert "probe.receipt_sha256" not in renderer
    assert "probe.error_code" not in renderer
    assert "response" not in renderer.lower()
    assert "原始错误" in renderer
    assert "响应正文不会保存或显示" in renderer


def test_probe_lifecycle_clears_key_and_blocks_duplicate_submission():
    app = APP.read_text(encoding="utf-8")
    lifecycle = _slice(
        app,
        "function openModelProviderProbePreview",
        "function candidateReviewPath",
    )
    assert lifecycle.count('$("modelProviderApiKey").value = ""') >= 6
    assert "state.modelProviderOperationPending || modelProviderProbeIsActive()" in lifecycle
    assert "探测中禁止重复提交" in INDEX.read_text(encoding="utf-8")
    assert "不会自动重试" in lifecycle
    assert "scheduleModelProviderProbePoll" in lifecycle
    assert "cancelModelProviderProbe" in lifecycle
    assert "modelProviderProbeIsActive" in lifecycle


def test_manifest_declares_separate_execute_capability_and_fixed_synthetic_boundary():
    root = json.loads(ROOT_MANIFEST.read_text(encoding="utf-8"))
    inner = json.loads(INNER_MANIFEST.read_text(encoding="utf-8"))
    expected = {
        "scope": "local_teacher_model_provider_settings_and_fixed_synthetic_probe",
        "settings_endpoint": "/api/v1/settings/model-providers",
        "profile_endpoint_template": "/api/v1/settings/model-providers/{profile_id}",
        "credential_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/credential",
        "synthetic_test_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/test",
        "synthetic_test_status_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}",
        "synthetic_test_cancel_endpoint_template": "/api/v1/settings/model-providers/{profile_id}/test/{probe_run_id}/cancel",
        "write_capability": "model_provider_settings_write",
        "synthetic_probe_execute_capability": "model_provider_synthetic_probe_execute",
        "credential_storage": "windows_credential_manager_current_user_non_roaming",
        "api_key_in_project_files": False,
        "api_key_in_browser_storage": False,
        "api_key_in_logs_or_responses": False,
        "arbitrary_base_url_allowed": False,
        "real_student_image_egress_default": False,
        "commercial_question_image_egress_default": False,
        "synthetic_probe_invocation_enabled": True,
        "production_model_invocation_enabled": False,
        "fixed_synthetic_only": True,
        "synthetic_prompt_sha256": PROMPT_SHA256,
        "synthetic_prompt_bytes": PROMPT_BYTES,
        "no_response_content_storage": True,
        "no_auto_retry": True,
        "no_redirect": True,
        "offline_workbench_without_key": True,
    }
    assert root["model_provider_settings"] == expected
    assert inner["model_provider_settings"] == expected
    launcher = LAUNCHER.read_text(encoding="utf-8")
    for marker in (
        '"synthetic_probe_execute_capability": "model_provider_synthetic_probe_execute"',
        '"synthetic_probe_invocation_enabled": True',
        '"production_model_invocation_enabled": False',
        '"fixed_synthetic_only": True',
        '"no_response_content_storage": True',
        '"no_auto_retry": True',
        '"no_redirect": True',
    ):
        assert marker in launcher


def test_both_manifests_bind_identical_current_static_bytes():
    root = json.loads(ROOT_MANIFEST.read_text(encoding="utf-8"))
    inner = json.loads(INNER_MANIFEST.read_text(encoding="utf-8"))
    assert root["files"] == inner["files"]
    assert set(root["files"]) == {"app.js", "index.html", "styles.css"}
    for name, descriptor in root["files"].items():
        raw = (OVERLAY / name).read_bytes()
        assert descriptor == {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }


def test_probe_layout_collapses_to_one_column_for_narrow_screens():
    css = STYLES.read_text(encoding="utf-8")
    assert ".model-provider-probe-preview" in css
    assert ".model-provider-probe-receipt" in css
    narrow = css[css.index("@media (max-width: 900px)") :]
    assert ".model-provider-probe-preview" in narrow
    assert ".model-provider-probe-receipt" in narrow
    assert "grid-template-columns: minmax(0, 1fr)" in narrow

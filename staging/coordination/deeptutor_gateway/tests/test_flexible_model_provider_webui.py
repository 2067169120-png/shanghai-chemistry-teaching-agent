from __future__ import annotations

import json
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
INDEX = OVERLAY / "index.html"
APP = OVERLAY / "app.js"
STYLES = OVERLAY / "styles.css"
ROOT_MANIFEST = WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json"
INNER_MANIFEST = OVERLAY / "overlay.manifest.json"


def _slice(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


def test_simple_provider_mode_accepts_manual_and_experimental_model_ids():
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert '<select id="modelProviderProvider"' in html
    assert '<input id="modelProviderModel" type="text"' in html
    assert 'list="modelProviderModelList"' in html
    assert '<datalist id="modelProviderModelList">' in html
    assert 'id="modelProviderRefreshModels"' in html
    assert "可手填尚未收录的新型号或实验型号" in html
    assert "modelProviderSafeModelId" in app
    assert "provider.modelsById.get(modelId) || null" in app
    assert "unverified_custom_model" in app


def test_model_list_refresh_only_updates_suggestions_and_never_infers_capabilities():
    app = APP.read_text(encoding="utf-8")
    refresh = _slice(
        app,
        "async function refreshModelProviderModels()",
        "function modelProviderAllowedDataClasses()",
    )
    assert "/models`" in refresh
    assert 'method: "POST"' in refresh
    assert "JSON.stringify({ expected_revision: profile.revision })" in refresh
    assert "result.capabilities_not_inferred !== true" in refresh
    assert "state.modelProviderSuggestedModels = [...result.model_ids]" in refresh
    assert "modelProviderRefreshModelDatalist()" in refresh
    assert "未推断任何能力" in refresh
    assert "仍可手填 model ID 并保存" in refresh
    assert '$("modelProviderModel").value =' not in refresh
    assert '$("modelProviderCustomModel").value =' not in refresh


def test_advanced_openai_compatible_mode_exposes_endpoint_style_model_and_capabilities():
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    for marker in (
        'id="modelProviderAdvanced"',
        'id="modelProviderUseCustom"',
        'id="modelProviderCustomDisplayName"',
        'id="modelProviderCustomBaseUrl"',
        'id="modelProviderCustomApiStyle"',
        'id="modelProviderCustomModel"',
        'id="modelProviderCustomText"',
        'id="modelProviderCustomVision"',
        'id="modelProviderCustomStructured"',
        'id="modelProviderAllowLoopbackHttp"',
    ):
        assert marker in html
    assert "公网或第三方地址必须使用 HTTPS" in html
    assert "仅 <code>localhost</code>、<code>127.0.0.1</code> 或 <code>::1</code>" in html
    save = _slice(app, "async function saveModelProviderConfig", "async function saveModelProviderCredential")
    for marker in (
        'provider_kind: "openai_compatible"',
        "display_name: customDisplayName",
        "model_id: model.model_id",
        "base_url: customBaseUrl",
        "api_style: customApiStyle",
        "local_endpoint_policy: localEndpointPolicy",
        "capabilities: model.capabilities",
        "expected_revision: existing?.revision || null",
    ):
        assert marker in save
    assert 'parsed.protocol === "https:"' in save
    assert '"allow_loopback_http"' in save


def test_connection_test_is_not_presented_as_vision_confirmation_or_recognition_fallback():
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    assert "连接测试只验证固定合成文本，不证明视觉能力" in html
    assert "资料导入与学生作业分析都要求 vision + structured_output" in html
    assert "文本连通性探测不算视觉证据" in html
    assert "文本探测不算视觉证据" in app
    assert "text_probe_confirms_vision" not in app
    assert "不启用 OCR 或文字识别回退" in html
    combined = f"{html}\n{app}".casefold()
    for forbidden in (
        'id="intakeimportocr',
        "/api/v1/ocr",
        "ocr_fallback",
        "ocrtext",
        "ocr_text",
        "tesseract",
    ):
        assert forbidden not in combined


def test_provider_ui_supports_source_page_policy_and_intake_execute_boundary():
    html = INDEX.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    css = STYLES.read_text(encoding="utf-8")
    assert 'id="modelProviderDataSourcePageImage"' in html
    assert 'id="modelProviderDataStudentAnswerImage"' in html
    assert 'value="source_page_image"' in html
    assert 'value="student_answer_image"' in html
    assert 'value="teacher_confirmed_source_pages"' in html
    assert 'value="teacher_confirmed_student_pages"' in html
    assert 'value="teacher_confirmed_visual_pages"' in html
    assert "intake_visual_execute" in html
    assert "每份资料或作业只确认一次" in html
    assert (
        'body[data-primary-route="ai"] #modelProviderSettingsForm .model-provider-data-policy'
        not in css
    )
    validator = _slice(
        app,
        "function validateModelProviderSettings",
        "function modelProviderProbeTimestamp",
    )
    assert '"source_page_image", "student_answer_image", "deidentified_student_text"' in validator
    assert '"teacher_confirmed_source_pages"' in validator
    assert '"teacher_confirmed_student_pages"' in validator
    assert '"teacher_confirmed_visual_pages"' in validator
    assert 'profile.allowed_data_classes.includes("source_page_image")' in validator
    assert 'profile.allowed_data_classes.includes("student_answer_image")' in validator
    save = _slice(
        app,
        "async function saveModelProviderConfig",
        "async function saveModelProviderCredential",
    )
    assert 'imageEgress === "teacher_confirmed_source_pages"' in save
    assert 'model.capabilities.includes("structured_output")' in save
    assert 'allowedDataClasses.includes("source_page_image")' in save


def test_profile_validator_accepts_catalog_suggestions_and_custom_profile_evidence():
    app = APP.read_text(encoding="utf-8")
    validator = _slice(
        app,
        "function validateModelProviderSettings",
        "function modelProviderProbeTimestamp",
    )
    assert 'provider.provider_kind !== undefined && provider.provider_kind !== "preset"' in validator
    assert 'provider.catalog_role !== undefined && provider.catalog_role !== "suggestions_only"' in validator
    assert "provider.custom_model_ids_allowed" in validator
    assert 'providerKind = profile?.provider_kind || "preset"' in validator
    assert 'profile.provider_id !== "openai_compatible"' in validator
    assert '"declared", "catalog", "probed", "unknown"' in validator
    assert "modelProviderExactKeys(profile" not in validator
    assert "modelProviderExactKeys(provider" not in validator


def test_feature_contract_declares_flexible_models_and_visual_only_boundary():
    root = json.loads(ROOT_MANIFEST.read_text(encoding="utf-8"))
    inner = json.loads(INNER_MANIFEST.read_text(encoding="utf-8"))
    assert root["flexible_model_provider_ui"] == inner["flexible_model_provider_ui"]
    contract = root["flexible_model_provider_ui"]
    assert contract["model_list_endpoint_template"].endswith("/{profile_id}/models")
    assert contract["provider_kinds"] == ["preset", "openai_compatible"]
    assert contract["preset_provider_ids"] == ["openai", "deepseek"]
    assert contract["openai_compatible_api_styles"] == ["responses", "chat_completions"]
    assert contract["manual_model_id_allowed"] is True
    assert contract["model_catalog_suggestions_only"] is True
    assert contract["model_list_capabilities_inferred"] is False
    assert contract["custom_public_https_base_url_allowed"] is True
    assert contract["custom_loopback_http_explicit_opt_in"] is True
    assert contract["custom_private_lan_http_allowed"] is False
    assert contract["capability_evidence_axes"] == [
        "declared",
        "catalog",
        "probed",
        "unknown",
    ]
    assert contract["text_probe_confirms_vision"] is False
    assert contract["vision_required_for_question_and_student_image_ingest"] is True
    assert contract["ocr_fallback_allowed"] is False
    assert contract["source_page_image_data_class"] is True
    assert contract["student_answer_image_data_class"] is True
    assert contract["teacher_confirmed_source_pages_policy"] is True
    assert contract["teacher_confirmed_student_pages_policy"] is True
    assert contract["teacher_confirmed_visual_pages_policy"] is True
    assert contract["intake_visual_execute_explained"] is True
    assert contract["production_model_invocation_enabled"] is False
    assert contract["offline_workbench_without_key"] is True


def test_provider_layout_remains_usable_on_narrow_screens():
    css = STYLES.read_text(encoding="utf-8")
    assert ".model-provider-custom-grid" in css
    assert ".model-provider-input-row" in css
    narrow = css[css.index("@media (max-width: 900px)") :]
    assert ".model-provider-custom-grid" in narrow
    phone = css[css.index("@media (max-width: 760px)") :]
    assert ".model-provider-input-row" in phone
    assert "grid-template-columns: minmax(0, 1fr)" in phone

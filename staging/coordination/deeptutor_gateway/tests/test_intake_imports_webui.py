from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
INDEX = OVERLAY / "index.html"
APP = OVERLAY / "app.js"
STYLES = OVERLAY / "styles.css"
INNER_MANIFEST = OVERLAY / "overlay.manifest.json"
OUTER_MANIFEST = WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slice(source: str, start: str, end: str) -> str:
    offset = source.index(start)
    return source[offset : source.index(end, offset)]


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        del tag
        value = dict(attrs).get("id")
        if value:
            self.ids.append(value)


def test_home_keeps_three_tasks_and_materials_route_opens_real_import() -> None:
    html = _source(INDEX)
    home = html.split('id="panel-home"', 1)[1].split(
        'id="panel-overview"', 1
    )[0]
    assert home.count('class="home-task-card"') == 3
    assert "导入通道正在接入" not in home
    assert "选择图片、PDF 或 DOCX" in home
    assert "整份来源页面" in home
    assert "待教师复核的候选" in home
    assert 'href="#/materials">开始导入</a>' in home


def test_materials_page_exposes_three_step_chinese_wizard_without_ids_or_json() -> None:
    html = _source(INDEX)
    wizard = html.split('id="intakeImportWizard"', 1)[1].split(
        'class="card inset full-bank-readiness-card', 1
    )[0]
    assert wizard.count('data-intake-step="') == 3
    for marker in (
        "这是什么资料？",
        "本机上传与页面准备",
        "选择视觉 Profile 并确认整份来源",
        "图片 / PDF / DOCX",
        "不确定填 unknown",
        'value="question_paper"',
        'value="answer"',
        'value="handout"',
        'value="textbook"',
        'value="syllabus"',
        'id="intakeImportFile"',
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        "一次性发送",
        "不会逐页弹窗",
        "candidate-only",
        "尚未正式入库",
        "不启用 OCR 或文字识别回退",
        'id="intakeImportReviewPage"',
        'id="intakeImportReviewAccept"',
        'id="intakeImportReviewReject"',
        'id="intakeImportReviewNote" rows="3" maxlength="2000"',
        'id="intakeImportPersonalLibraryList"',
        'id="intakeImportOpenLedger"',
    ):
        assert marker in wizard
    assert "填写 JSON" not in wizard
    assert "import_id" not in wizard
    assert "Profile ID" not in wizard
    assert 'href="#/review"' not in wizard
    parser = _Ids()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))


def test_upload_uses_raw_fetch_then_polls_real_job_and_supports_cancel_retry() -> None:
    app = _source(APP)
    raw_upload = _slice(
        app,
        "async function intakeImportRawUpload",
        "function changeIntakeImportFile",
    )
    assert 'method: "PUT"' in raw_upload
    assert '"Content-Type": mimeType' in raw_upload
    assert "body: file" in raw_upload
    assert "FileReader" not in raw_upload
    assert "content_base64" not in raw_upload

    submit = _slice(
        app,
        "async function submitIntakeImport",
        "function scheduleIntakeImportPolling",
    )
    assert 'api("/api/v1/intake/imports", {' in submit
    assert 'method: "POST"' in submit
    for field in (
        "source_role: role",
        "filename: file.name",
        "mime_type: mimeType",
        "size_bytes: file.size",
    ):
        assert field in submit
    assert "intakeImportRawUpload(job.import_id, file, mimeType, generation)" in submit

    poll = _slice(app, "async function pollIntakeImport", "async function analyzeIntakeImport")
    assert "/api/v1/intake/imports/${encodeURIComponent(importIdValue)}" in poll
    assert "intakeImportPollFailures < 3" in poll
    cancel = _slice(app, "async function cancelIntakeImport", "async function retryIntakeImport")
    assert "/cancel`" in cancel
    assert 'body: JSON.stringify({})' in cancel
    retry = _slice(app, "async function retryIntakeImport", "function startNewIntakeImport")
    for status in (
        'status === "awaiting_upload"',
        'status === "render_failed"',
        '"awaiting_visual_provider", "analysis_failed"',
    ):
        assert status in retry


def test_visual_preflight_requires_complete_profile_and_sends_one_confirmation() -> None:
    app = _source(APP)
    assessment = _slice(
        app,
        "function intakeImportProfileAssessment",
        "function intakeImportSelectedProfile",
    )
    for marker in (
        'profile.credential_state !== "configured"',
        '!effective.has("vision")',
        '!effective.has("structured_output")',
        'includes("source_page_image")',
        '["teacher_confirmed_source_pages", "teacher_confirmed_visual_pages"]',
        "evidence.catalog",
        "evidence.declared",
    ):
        assert marker in assessment
    assert "model_id" not in assessment

    analyze = _slice(app, "async function analyzeIntakeImport", "async function cancelIntakeImport")
    assert "/analyze`" in analyze
    assert "profile_id: profile.profile_id" in analyze
    assert "expected_revision: expectedRevision" in analyze
    assert "teacher_confirmed_egress: true" in analyze
    assert "currentProfile.revision !== expectedRevision" in analyze
    assert "assessment.eligible" in analyze
    assert "OCR" not in analyze


def test_reload_persists_only_import_pointer_and_never_key_or_file_bytes() -> None:
    app = _source(APP)
    persistence = _slice(
        app,
        "function persistIntakeImportPointer",
        "function stopIntakeImportPolling",
    )
    assert 'storageKey: "shchem.teacher.intake-import.v1"' in app
    assert "schema_version: intakeImportContract.storageSchemaVersion" in persistence
    assert "import_id: importIdValue" in persistence
    for forbidden in (
        "state.token",
        "api_key",
        "profile_id",
        "expected_revision",
        "file.name",
        "file.size",
        "source_sha256",
        "page_sha256",
        "FileReader",
        "ArrayBuffer",
    ):
        assert forbidden not in persistence
    restore = _slice(app, "async function restoreIntakeImport", "function openMaterialIntakeLedger")
    assert "readIntakeImportPointer()" in restore
    assert "/api/v1/intake/imports/${encodeURIComponent(importIdValue)}" in restore
    assert "recovered: true" in restore
    assert "Key、文件字节与页面内容" in app


def test_status_validator_rejects_stale_regression_and_fake_completion() -> None:
    app = _source(APP)
    validator = _slice(
        app,
        "function validateIntakeImportJob",
        "function intakeImportFileMimeType",
    )
    for status in (
        "awaiting_upload",
        "preparing_pages",
        "ready_for_analysis",
        "render_failed",
        "queued_for_analysis",
        "analyzing",
        "awaiting_visual_provider",
        "analysis_failed",
        "cancel_requested",
        "cancelled",
        "completed",
    ):
        assert f'"{status}"' in app
    assert "event.sequence !== index + 1" in validator
    assert "intakeImportLastEventSequence(value) < intakeImportLastEventSequence(previous)" in validator
    assert "Date.parse(value.updated_at) < Date.parse(previous.updated_at)" in validator
    assert "!intakeImportCanTransition(previous.status, value.status)" in validator
    assert 'value.status === "completed"' in validator
    assert "completedAttempt.model_invoked !== true" in validator
    assert "completedAttempt.visual_api_invocation_allowed !== true" in validator
    assert "completedAttempt.response?.candidate_schema_valid !== true" in validator
    assert 'value.candidate.candidate_status !== "candidate_only"' in validator
    assert "value.candidate.requires_teacher_review !== true" in validator
    assert "value.candidate.central_registry_write !== false" in validator


def test_completed_view_is_a_real_bound_teacher_review_workspace() -> None:
    html = _source(INDEX)
    wizard = html.split('id="intakeImportWizard"', 1)[1].split(
        'class="card inset full-bank-readiness-card', 1
    )[0]
    app = _source(APP)
    render = _slice(
        app,
        "function renderIntakeImportCandidate",
        "function intakeImportEffectiveStatus",
    )
    for array_name in (
        "printed_question_candidates",
        "answer_page_mappings",
        "textbook_mapping_candidates",
        "cognitive_difficulty_candidates",
        "review_blockers",
    ):
        assert array_name in render
    assert "paper_identity_candidates" in render
    assert "atomic_part_candidates" in render
    assert "candidate_sha256" in render
    assert "intakeImportReviewStatus" in render
    assert "intakeImportReviewAllBlockersAcknowledged" in render
    assert "textContent" in render
    assert "innerHTML" not in render
    assert "尚未正式入库" in html
    assert "待教师复核" in html
    assert 'href="#/review"' not in wizard
    assert "整卷 → 主题大题 → 印刷小题 → 最小作答单元" in html
    assert 'id="intakeImportOpenLedger"' in html


def test_review_loads_owned_page_bytes_and_recycles_every_blob_url() -> None:
    app = _source(APP)
    page = _slice(
        app,
        "async function loadIntakeImportReviewPage",
        "function renderIntakeImportReviewPages",
    )
    assert "/api/v1/intake/imports/${encodeURIComponent(job.import_id)}/pages/${page}/content" in page
    assert "Authorization: `Bearer ${tokenAtStart}`" in page
    assert 'responseType !== "image/png"' in page
    assert "descriptor.sha256" in page
    assert "response.blob()" in page
    assert "URL.createObjectURL(blob)" in page
    assert "URL.revokeObjectURL(objectUrl)" in page
    revoke = _slice(
        app,
        "function revokeIntakeImportReviewPageUrl",
        "function resetIntakeImportReviewPage",
    )
    assert "URL.revokeObjectURL(state.intakeImportReviewPageObjectUrl)" in revoke
    assert 'removeAttribute("src")' in revoke
    assert 'window.addEventListener("pagehide"' in app
    assert "revokeIntakeImportReviewPageUrl();" in app[app.index('window.addEventListener("pagehide"') :]


def test_teacher_review_posts_exact_hash_revision_decision_and_refreshes_409() -> None:
    app = _source(APP)
    review = _slice(
        app,
        "async function submitIntakeImportReview",
        "function validateIntakeImportPersonalLibrary",
    )
    assert "/api/v1/intake/imports/${encodeURIComponent(importIdValue)}/review" in review
    for field in (
        "expected_review_revision: job.review.revision",
        "candidate_sha256: job.candidate_sha256",
        "decision,",
        "acknowledged_blocker_codes: intakeImportReviewAcknowledgedCodes()",
        'teacher_note_zh: $("intakeImportReviewNote").value.trim()',
    ):
        assert field in review
    assert 'decision === "accept_personal_library"' in review
    assert "intakeImportReviewAllBlockersAcknowledged(job)" in review
    assert "error.status === 409" in review
    assert "refreshIntakeImportAfterReviewConflict(importIdValue)" in review
    assert "central" not in review.lower() or "未写入中央正式题库" in review


def test_personal_import_library_is_real_api_data_and_closed_candidate_layer() -> None:
    html = _source(INDEX)
    app = _source(APP)
    load = _slice(
        app,
        "async function loadIntakeImportPersonalLibrary",
        "async function openIntakeImportPersonalLibraryItem",
    )
    render = _slice(
        app,
        "function renderIntakeImportPersonalLibrary",
        "async function loadIntakeImportPersonalLibrary",
    )
    assert 'api("/api/v1/intake/personal-library")' in load
    assert "validateIntakeImportPersonalLibrary" in load
    assert 'value.schema_version !== "shchem.personal-import-library.v1"' in app
    assert "item.candidate_only !== true" in app
    assert "item.central_registry_write !== false" in app
    assert "item.theme_boundaries.length" in render
    assert "item.printed_question_candidates.length" in render
    assert "item.atomic_part_candidates.length" in render
    assert "textContent" in render
    assert "innerHTML" not in render
    assert "个人候选层" in html
    assert "不属于中央正式题库" in html


def test_visual_intake_never_introduces_text_recognition_fallback() -> None:
    app = _source(APP)
    intake = _slice(app, "function intakeImportStatusLabel", "function studentVisualSubmissionId")
    assert "直接分析页面图" in intake
    assert "不会改用文字识别回退" in intake
    for forbidden in (
        "Tesseract",
        "tesseract",
        "/ocr",
        "ocrText",
        "ocr_text",
        "FileReader",
        "content_base64",
    ):
        assert forbidden not in intake


def test_visual_import_manifest_binds_real_ui_bytes_and_closed_authority() -> None:
    inner = json.loads(_source(INNER_MANIFEST))
    outer = json.loads(_source(OUTER_MANIFEST))
    assert inner["visual_intake_import_ui"] == outer["visual_intake_import_ui"]
    contract = inner["visual_intake_import_ui"]
    assert contract["create_endpoint"] == "/api/v1/intake/imports"
    assert contract["raw_source_upload"] is True
    assert contract["accepted_mime_types"] == [
        "image/png",
        "image/jpeg",
        "image/webp",
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ]
    assert contract["profile_requirements"] == {
        "credential_state": "configured",
        "capabilities": ["vision", "structured_output"],
        "allowed_data_class": "source_page_image",
        "image_egress": "teacher_confirmed_source_pages",
    }
    assert contract["execute_capability"] == "intake_visual_execute"
    assert contract["confirmation_scope"] == "all_source_pages_this_import"
    assert contract["recognition_mode"] == "direct_page_vision"
    assert contract["browser_persistence"] == ["import_id"]
    assert contract["api_key_in_browser_storage"] is False
    assert contract["file_bytes_in_browser_storage"] is False
    assert contract["candidate_only"] is True
    assert contract["requires_teacher_review"] is True
    assert contract["central_registry_write"] is False
    assert contract["stale_response_rejected"] is True
    assert contract["status_regression_rejected"] is True
    for filename, descriptor in inner["files"].items():
        raw = (OVERLAY / filename).read_bytes()
        assert descriptor == {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }


def test_visual_import_layout_is_responsive_and_materials_specific() -> None:
    css = _source(STYLES)
    assert 'body[data-primary-route="materials"]' in css
    assert ":not(.intake-import-wizard)" in css
    assert 'body[data-primary-route="library"] #panel-candidate-review > .intake-import-wizard' in css
    for selector in (
        ".intake-import-steps",
        ".intake-import-metadata-grid",
        ".intake-import-source-summary",
        ".intake-import-provider-grid",
        ".intake-import-candidate-metrics",
        ".intake-import-review-layout",
        ".intake-import-page-toolbar",
        ".intake-import-hierarchy-summary",
        ".intake-import-personal-library-list",
    ):
        assert selector in css
    phone = css[css.index("@media (max-width: 760px)") :]
    assert re.search(
        r"\.intake-import-steps[^\n]+grid-template-columns:\s*minmax\(0,\s*1fr\)",
        phone,
    )
    assert re.search(
        r"\.intake-import-page-toolbar[^\n]+grid-template-columns:\s*minmax\(0,\s*1fr\)",
        phone,
    )

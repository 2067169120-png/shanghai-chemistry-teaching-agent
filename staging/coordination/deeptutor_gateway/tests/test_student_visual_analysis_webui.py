from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
INDEX = OVERLAY / "index.html"
APP = OVERLAY / "app.js"
STYLES = OVERLAY / "styles.css"
INNER_MANIFEST = OVERLAY / "overlay.manifest.json"
OUTER_MANIFEST = WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json"


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    offset = text.index(start)
    return text[offset : text.index(end, offset)]


class IdParser(HTMLParser):
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


def test_students_page_has_direct_visual_four_step_teacher_workflow() -> None:
    html = source(INDEX)
    panel = html.split('id="panel-students"', 1)[1].split(
        'id="panel-artifacts"', 1
    )[0]
    assert panel.count('data-student-visual-step="') == 4
    for marker in (
        'id="studentVisualWorkbench"',
        "快速分析一份作业 / 试卷",
        'id="studentVisualStudent"',
        'id="studentVisualCreateStudent"',
        'id="studentVisualQuestionFiles"',
        'id="studentVisualReferenceFiles"',
        'id="studentVisualWorkFiles"',
        "题目页面（必需）",
        "参考答案页面（可选）",
        "学生作答页面（必需）",
        "PNG、JPEG、WebP、PDF、DOCX",
        'id="studentVisualConfirmMatching"',
        'id="studentVisualProfile"',
        'id="studentVisualIdentifiersClear"',
        'id="studentVisualConfirmEgress"',
        "本次只确认一次，不逐页弹窗",
        'id="studentVisualAnalyze"',
        'id="studentVisualCancel"',
        'id="studentVisualRefresh"',
        'id="studentVisualCurriculumState"',
        'id="studentVisualRefreshDiagnosis"',
        'id="studentVisualScoringConfirmation"',
        'id="studentVisualEvidenceSufficiency"',
        'id="studentVisualDiagnosisStatus"',
        'id="studentVisualRecommendationGroups"',
        'id="studentVisualGoPrep"',
        "逐题评分、教材章节与错因复核",
        "单份作业最多显示“暂定薄弱”",
        "推荐完整主题",
        "candidate only",
        "尚未形成正式成绩",
    ):
        assert marker in panel
    assert "填写 JSON" not in panel
    assert "submission_id" not in panel
    assert "API Key" not in panel
    assert '<details class="student-legacy-workflow">' in panel
    parser = IdParser()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))


def test_student_files_use_registration_then_raw_put_without_text_or_base64_input() -> None:
    app = source(APP)
    upload = section(
        app,
        "async function studentVisualRawUpload",
        "async function uploadStudentVisualFiles",
    )
    assert 'method: "PUT"' in upload
    assert '"Content-Type": mimeType' in upload
    assert "body: file" in upload
    assert "FileReader" not in upload
    assert "readAsDataURL" not in upload
    assert "content_base64" not in upload

    flow = section(
        app,
        "async function uploadStudentVisualFiles",
        "async function confirmStudentVisualMatching",
    )
    assert 'api("/api/v1/submissions", {' in flow
    assert 'workflow: "quick_single_work"' in flow
    assert "/files`" in flow
    for field in (
        "role: queued.role",
        "filename: queued.file.name",
        "mime_type: queued.mimeType",
        "expected_size_bytes: null",
        "expected_sha256: null",
        "expected_revision: submission.revision",
    ):
        assert field in flow
    assert "studentVisualRawUpload(" in flow
    assert "state.studentVisualUploadQueue" in flow
    assert "重试未完成的本机保存" in app


def test_provider_preflight_requires_real_visual_evidence_and_student_policy() -> None:
    html = source(INDEX)
    app = source(APP)
    for marker in (
        'id="modelProviderDataStudentAnswerImage"',
        'value="student_answer_image"',
        'value="teacher_confirmed_student_pages"',
        'value="teacher_confirmed_visual_pages"',
        "文本连通性探测不算视觉证据",
    ):
        assert marker in html
    assessment = section(
        app,
        "function studentVisualProfileAssessment",
        "function studentVisualSelectedProfile",
    )
    for marker in (
        'profile.credential_state !== "configured"',
        '!effective.has("vision")',
        '!effective.has("structured_output")',
        'includes("student_answer_image")',
        '"teacher_confirmed_student_pages", "teacher_confirmed_visual_pages"',
        "evidence.catalog",
        "evidence.declared",
    ):
        assert marker in assessment
    assert "evidence.probed" not in assessment
    assert "model_id" not in assessment


def test_teacher_confirmation_binds_every_page_then_calls_direct_visual_api() -> None:
    app = source(APP)
    analyze = section(
        app,
        "async function analyzeStudentVisualSubmission",
        "async function cancelStudentVisualSubmission",
    )
    assert "/privacy-decisions`" in analyze
    assert 'decision: "approved"' in analyze
    assert "contains_direct_identifiers: false" in analyze
    assert "confirmed_page_sha256: studentVisualPageHashes(current)" in analyze
    assert "[...new Set((submission?.files || [])" in app
    assert "provider_profile_id: profile.profile_id" in analyze
    assert "provider_revision: providerRevision" in analyze
    assert "teacher_confirmed_student_page_egress: true" in analyze
    assert "/analyze`" in analyze
    assert "expected_revision: current.revision" in analyze
    assert "liveProfile.revision !== providerRevision" in analyze
    assert "stillCurrent.revision !== providerRevision" in analyze
    assert "FileReader" not in analyze
    assert "recognized_text" not in analyze


def test_awaiting_provider_never_auto_retries_analysis() -> None:
    app = source(APP)
    effective = section(
        app,
        "function studentVisualEffectiveStatus",
        "function studentVisualStatusLabel",
    )
    assert 'return "awaiting_visual_provider"' in effective
    controls = section(
        app,
        "function updateStudentVisualControls",
        "function resetStudentVisualRuntime",
    )
    assert "!assessment.eligible" in controls
    assert '"analysis_failed", "awaiting_visual_provider"' in controls
    polling = section(
        app,
        "function scheduleStudentVisualPolling",
        "async function analyzeStudentVisualSubmission",
    )
    assert "studentVisualContract.activeStatuses" in polling
    assert (
        'activeStatuses: Object.freeze(["queued_for_analysis", "analyzing", "cancel_requested"])'
        in app
    )
    assert "analyzeStudentVisualSubmission" not in polling
    assert "已停止自动查询" in polling


def test_reload_persists_only_submission_pointer() -> None:
    app = source(APP)
    persistence = section(
        app,
        "function persistStudentVisualPointer",
        "function stopStudentVisualPolling",
    )
    assert 'storageKey: "shchem.teacher.student-visual-submission.v1"' in app
    assert "schema_version: studentVisualContract.storageSchemaVersion" in persistence
    assert "submission_id: submissionIdValue" in persistence
    for forbidden in (
        "student_id:",
        "state.token",
        "api_key",
        "provider_profile_id",
        "file.name",
        "file.size",
        "page_sha256",
        "analysis",
        "candidate",
        "response",
        "FileReader",
    ):
        assert forbidden not in persistence
    restore = section(
        app,
        "async function restoreStudentVisualSubmission",
        "function startNewStudentVisualSubmission",
    )
    assert "readStudentVisualPointer()" in restore
    assert "/api/v1/submissions/${encodeURIComponent(submissionIdValue)}" in restore
    assert "recovered: true" in restore
    assert "未读取任何文件、Key 或模型响应" in restore
    controls = section(
        app,
        "function updateStudentVisualControls",
        "function resetStudentVisualRuntime",
    )
    assert "!state.studentVisualSubmissionId" in controls
    assert "!studentVisualSubmissionId(state.studentVisualSubmissionId)" in controls
    assert "(!submission || !studentVisualContract.activeStatuses.includes(status))" in controls


def test_validator_rejects_stale_state_and_fake_teacher_ready_result() -> None:
    app = source(APP)
    transitions = section(
        app,
        "function studentVisualCanTransition",
        "function validateStudentVisualSubmission",
    )
    assert 'awaiting_privacy_review: new Set(["awaiting_privacy_review", "ready_for_analysis", "awaiting_visual_provider", "upload_processing_failed", "cancelled"])' in transitions
    assert 'upload_processing_failed: new Set(["upload_processing_failed", "awaiting_upload", "awaiting_privacy_review", "cancelled"])' in transitions
    validator = section(
        app,
        "function validateStudentVisualSubmission",
        "function persistStudentVisualPointer",
    )
    assert "Date.parse(value.updated_at) < Date.parse(previous.updated_at)" in validator
    assert "!studentVisualCanTransition(previous.status, value.status)" in validator
    assert "value.events.length < previous.events.length" in validator
    assert 'value.status === "awaiting_teacher_review"' in validator
    assert "analysis.requires_teacher_review !== true" in validator
    assert "analysis.final_score !== null" in validator
    assert "analysis.long_term_update_allowed !== false" in validator
    assert "candidate.requires_teacher_review !== true" in validator
    assert "candidate.final_score !== null" in validator
    assert "candidate.long_term_update_allowed !== false" in validator
    assert "item.maximum_score > 100" in validator
    assert "hold.ocr_invoked !== false" in validator
    assert "hold.transport_attempt_count !== 0" in validator
    assert "private_relative_path" in validator


def test_review_adds_teacher_curriculum_and_error_decision_after_scoring() -> None:
    html = source(INDEX)
    app = source(APP)
    assert "识别只使用视觉 API 直接观察原始页面像素" in html
    assert "不启用文字识别回退" in html
    render = section(
        app,
        "function renderStudentVisualReview",
        "function renderStudentVisualSubmission",
    )
    for marker in (
        "candidate.page_quality",
        "candidate.matches",
        "match.visual_response_observation",
        "match.chemistry_observations",
        "match.scoring_points",
        "match.error_hypotheses",
        "match.blockers",
        "match.suggested_score",
        "match.maximum_score",
        "studentVisualCurriculumSelect",
        "studentVisualErrorTypeSelect",
        "studentVisualEvidenceDetails",
        "appendStudentVisualScoringDecision",
    ):
        assert marker in render
    append = section(
        app,
        "async function appendStudentVisualScoringDecision",
        "async function restoreStudentVisualSubmission",
    )
    assert "/scoring-decisions`" in append
    assert "expected_revision: submission.revision" in append
    assert "teacher_score: teacherScore" in append
    assert "reason" in append
    assert "result.decision?.append_only !== true" in append
    assert "result.final_score !== null" in append
    assert 'studentVisualDiagnosticEndpoint("decision"' in append
    for field in (
        "expected_revision: result.revision",
        "match_id: match.match_id",
        "scoring_decision_id: result.decision.decision_id",
        'decision: "accept"',
        "result: diagnosticResult",
        "primary_error_type: lostPoints ? primaryErrorType : null",
        "secondary_error_types: []",
        "curriculum_section_keys: lostPoints ? [curriculumSectionKey] : []",
        "teacher_note: reason",
    ):
        assert field in append
    assert 'diagnosticResult = lostPoints' in append
    assert 'teacherScore <= 1e-9 ? "incorrect" : "partial"' in append
    assert ': "correct"' in append
    assert "studentVisualCurriculumSection(curriculumSectionKey)" in append
    assert "studentVisualErrorTypes.some" in append
    assert "scoring_decisions" in app
    assert "教材章节（失分题必选）" in render
    assert "主要错因（失分题必选）" in render
    assert "粗心（仅由教师确认）" in app
    assert "5 册 / 19 章 / 60 节" in app
    assert "innerHTML" not in render
    assert "本题满分（0.1–100）" in app


def test_diagnostic_review_and_complete_theme_recommendation_are_read_only() -> None:
    app = source(APP)
    loader = section(
        app,
        "async function loadStudentVisualDiagnosticOutputs",
        "async function appendStudentVisualScoringDecision",
    )
    assert 'studentVisualDiagnosticEndpoint("review"' in loader
    assert 'studentVisualDiagnosticEndpoint("recommendationPreview"' in loader
    assert "studentVisualContract.recommendationPreviewMethod" in loader
    for marker in (
        'preview.candidate_only !== true',
        'preview.read_only !== true',
        'preview.long_term_update_allowed !== false',
        'preview.diagnosis_status === "stable_weakness"',
        "Number(preview.metrics.independent_source_count || 0) < 2",
    ):
        assert marker in loader

    render = section(
        app,
        "function studentVisualScoringConfirmationLabel",
        "function renderStudentVisualReview",
    )
    for marker in (
        "教师评分已确认",
        "证据不足",
        "证据不足，仅作暂定推荐",
        "暂定薄弱",
        "studentVisualRecommendationCard",
        "recommendation_reason_zh",
        "diagnosis_section_keys",
        "命中小题",
        "完整主题",
        "打开完整题链",
        "加入整主题",
        'window.location.hash = "#/library"',
        "openPersonalWorkbenchEntry(entry)",
        "addPersonalBasketEntry(entry)",
    ):
        assert marker in render


def test_student_visual_layout_fits_phone_and_manifest_is_exact() -> None:
    css = source(STYLES)
    for selector in (
        ".student-visual-workbench",
        ".student-visual-steps",
        ".student-visual-grid",
        ".student-visual-review-items",
        ".student-visual-decision-form",
        ".student-visual-diagnosis-status",
        ".student-visual-recommendation-actions",
    ):
        assert selector in css
    phone = css[css.rindex("@media (width <= 420px)") :].split("{", 1)[1]
    assert ".student-visual-create-row" in phone
    assert ".student-visual-match-row" in phone
    assert ".student-visual-diagnosis-head" in phone
    assert ".student-visual-recommendation-actions" in phone
    assert "grid-template-columns: minmax(0, 1fr)" in phone
    assert not re.search(r"width:\s*(?:[4-9]\d{2}|\d{4,})px", phone)

    inner = json.loads(source(INNER_MANIFEST))
    outer = json.loads(source(OUTER_MANIFEST))
    assert inner["student_visual_analysis_ui"] == outer["student_visual_analysis_ui"]
    contract = inner["student_visual_analysis_ui"]
    assert contract["create_student_endpoint"] == "/api/v1/students"
    assert contract["create_submission_endpoint"] == "/api/v1/submissions"
    assert contract["raw_file_upload"] is True
    assert contract["file_roles"] == [
        "question_pages",
        "reference_answer_pages",
        "student_work_pages",
    ]
    assert contract["profile_requirements"] == {
        "credential_state": "configured",
        "capabilities": ["vision", "structured_output"],
        "vision_evidence": ["catalog", "declared"],
        "allowed_data_class": "student_answer_image",
        "image_egress": [
            "teacher_confirmed_student_pages",
            "teacher_confirmed_visual_pages",
        ],
    }
    assert contract["confirmation_scope"] == "all_current_submission_pages_once"
    assert contract["browser_persistence"] == ["submission_id"]
    assert contract["no_automatic_retry"] is True
    assert contract["text_recognition_fallback_allowed"] is False
    assert contract["candidate_only"] is True
    assert contract["append_only_scoring_decisions"] is True
    assert contract["long_term_materialization"] is False
    for filename, descriptor in inner["files"].items():
        raw = (OVERLAY / filename).read_bytes()
        assert descriptor == {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": len(raw),
        }


def test_student_visual_app_javascript_parses() -> None:
    node = shutil.which("node")
    if node is None:
        return
    completed = subprocess.run(
        [node, "--check", str(APP)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr

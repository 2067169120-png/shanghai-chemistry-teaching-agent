from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


class _Markup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.attrs: dict[str, dict[str, str | None]] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        identity = values.get("id")
        if identity:
            self.ids.append(identity)
            self.attrs[identity] = {"tag": tag, **values}


def function_slice(app: str, start: str, end: str) -> str:
    return app[app.index(start) : app.index(end, app.index(start))]


def test_markup_exposes_two_real_modes_and_three_pane_composer() -> None:
    html = source("index.html")
    parser = _Markup()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    panel = html.split('id="personalWorkbench"', 1)[1].split(
        'class="card workbench-advanced-info"', 1
    )[0]

    for copy in (
        "模拟考试",
        "平时练习",
        "大题目录",
        "连续整卷",
        "只改当前项",
        "生成整卷预览",
        "进入整卷确认",
        "生成已确认整卷的 Word 与 PDF",
        "关键词",
        "教材册",
        "教材章",
        "教材节",
    ):
        assert copy in panel

    assert set(re.findall(r'data-prep-mode="([^"]+)"', panel)) == {
        "mock_exam",
        "daily_practice",
    }
    assert parser.attrs["prepPaperViewport"]["role"] == "region"
    assert parser.attrs["prepPreviewGate"]["role"] == "status"
    assert parser.attrs["prepPreviewError"]["role"] == "alert"
    assert parser.attrs["prepApprovalDialog"]["tag"] == "dialog"
    assert parser.attrs["prepApprovalDialog"]["aria-labelledby"] == (
        "prepApprovalDialogHeading"
    )


def test_preview_request_submits_real_basket_search_curriculum_and_exam_fields() -> None:
    app = source("app.js")
    request = function_slice(
        app, "function prepPreviewRequestPayload", "function prepNormalizePreviewCandidate"
    )
    for field in (
        "mode",
        "scope",
        "data_snapshot_id",
        "candidate_count",
        "search",
        "paper",
        "curriculum",
        "hard_constraints",
        "preferences",
        "ordering",
        "basket_selections",
    ):
        assert field in request
    for exam_field in (
        "exam_name_zh",
        "template_id",
        "template_version",
        "template_year",
        "duration_minutes",
        "total_score",
        "theme_count",
        "instructions_zh",
        "scoring_rules",
        "identity_fields_zh",
        "sealed_line",
        "answer_space_lines",
        "pagination_rules",
    ):
        assert f"{exam_field}:" in request
    assert 'if (keyword) search = { q: keyword };' in request
    assert "prepCurriculumSelector()" in request
    assert 'prepOptionalPositiveInteger("prepPracticeThemeCount"' in request
    assert "basketContract.selections" in request
    assert 'numbering_mode: "restart_within_each_theme"' in request
    assert "score_per_atomic" not in request
    assert 'id="prepPracticeThemeCount" type="number" min="1" step="1"' in source(
        "index.html"
    )


def test_preview_model_is_only_visible_paper_source_and_keeps_four_levels() -> None:
    app = source("app.js")
    normalizer = function_slice(
        app, "function prepNormalizePreviewCandidate", "function prepIssueMessages"
    )
    renderer = function_slice(
        app, "function prepSharedMaterialsElement", "function prepSelectedProjection"
    )
    assert 'model.schema_version !== prepPreviewContract.previewSchemaVersion' in normalizer
    assert "model.data_snapshot_id !== expectedRequest.data_snapshot_id" in normalizer
    assert "group.group_kind !== \"theme_big_question\"" in normalizer
    assert "group.shared_materials" in normalizer
    assert "group.printed_questions" in normalizer
    assert "printed.atomic_rows" in normalizer
    assert "group.display_number" in renderer
    assert "printed.display_number" in renderer
    assert "row.display_number" in renderer
    assert "group.shared_materials" in renderer
    assert "prepSharedMaterialsElement(group, themeIndex" in renderer
    assert "model.cover?.identity_fields_zh" in renderer
    assert "model.cover?.sealed_line?.enabled" in renderer
    assert "model.pagination" in renderer
    assert "source_printed_number" not in renderer
    assert "sampleThemes" not in app
    assert "extraThemeFactory" not in app


def test_compact_rows_expand_to_real_local_content_or_explicit_missing_state() -> None:
    app = source("app.js")
    renderer = function_slice(
        app, "function prepAtomicElement", "function prepPrintedElement"
    )
    loader = function_slice(
        app, "function prepDeriveQuestionCropEndpoint", "function prepApprovalWarningMessages"
    )
    assert 'collapsed' not in renderer or "expanded" in renderer
    assert "题面定位摘要" in renderer
    assert "完整题面引用待补，当前无法显示" in renderer
    assert "显示真实题面裁片" in renderer
    assert "prepDirectContentRef(row)" in renderer
    assert "Authorization: `Bearer ${state.token}`" in loader
    assert 'blob.type.toLocaleLowerCase().startsWith("image/png")' in loader
    assert "question-crops" in loader
    assert '"image_endpoint", "question_crop_endpoint"' in loader
    assert "master-visual-scan-aliases" in loader
    assert "answer|solution|rubric|marking|shared_material" in loader
    assert "没有用摘要或示例内容替代" in loader


def test_approval_is_server_bound_and_every_content_change_revokes_it() -> None:
    app = source("app.js")
    invalidation = function_slice(
        app, "function invalidatePrepPreview", "function prepRequiredInteger"
    )
    approval = function_slice(
        app, "async function approvePrepPreview", "async function startApprovedPrepExport"
    )
    export = function_slice(
        app, "async function startApprovedPrepExport", "async function reorderPrepThemes"
    )
    events = app[app.index('document.querySelector(".prep-mode-switch")') :]

    assert "composer.approval = null" in invalidation
    assert "composer.previewSnapshotSha256 = null" in invalidation
    assert "composer.previewLoadId += 1" in invalidation
    assert "composer.approvalLoadId += 1" in invalidation
    assert "题篮已改变，原整卷确认已失效" in app
    assert "prepSettingChanged" in events
    assert "prepPracticeVolume" in events
    assert "prepPracticeChapter" in events
    assert "prepPracticeSection" in events

    assert 'api(prepPreviewContract.approveEndpoint' in approval
    for field in (
        "candidate_id",
        "preview_snapshot_sha256",
        "data_snapshot_id",
    ):
        assert f"{field}:" in approval
    assert 'approval.status !== "approved"' in approval
    assert "loadId !== composer.approvalLoadId" in approval
    assert "approval.preview_snapshot_sha256 !== request.preview_snapshot_sha256" in approval

    assert "/api/v1/prep/blueprints/${encodeURIComponent(approval.preview_approval_id)}/export" in export
    assert "body: JSON.stringify({})" in export
    assert "basket_selections" not in export
    assert 'api("/api/v1/prep/exports"' not in export
    assert "returnedApproval !== approval.preview_approval_id" in export
    assert "if (prepExportJobRunning(job)) schedulePrepExportPoll" in export


def test_approved_export_reuses_existing_job_polling_and_four_downloads() -> None:
    html = source("index.html")
    app = source("app.js")
    expected = {"student_docx", "student_pdf", "teacher_docx", "teacher_pdf"}
    assert set(re.findall(r'data-prep-export-artifact="([^"]+)"', html)) == expected
    assert "schedulePrepExportPoll(job.job_id, loadId)" in app
    assert "async function pollPrepExport" in app
    assert (
        "/api/v1/prep/exports/${encodeURIComponent(state.prepExportJob.job_id)}"
        "/artifacts/${artifactId}"
    ) in app
    assert "state.prepExportJob?.status !== \"completed\"" in app
    new_export = function_slice(
        app, "async function startApprovedPrepExport", "async function reorderPrepThemes"
    )
    assert "/v1/responses" not in new_export
    assert "/chat/completions" not in new_export


def test_teacher_view_hides_internal_identity_and_hash_copy() -> None:
    html = source("index.html")
    panel = html.split('id="personalWorkbench"', 1)[1].split(
        'class="card workbench-advanced-info"', 1
    )[0]
    for forbidden in (
        "preview_snapshot_sha256",
        "preview_approval_id",
        "candidate_id",
        "theme_id",
        "atomic_part_id",
        "SHA-256",
    ):
        assert forbidden not in panel
    assert "来源题号只保留在后台追溯字段" in source("app.js")
    render_job = function_slice(
        source("app.js"), "function renderPrepExportJob", "function prepExportRequestPayload"
    )
    assert "summary.push(job.job_id)" not in render_job


def test_keyboard_focus_and_narrow_layout_contracts_are_present() -> None:
    html = source("index.html")
    app = source("app.js")
    css = source("styles.css")
    compact_css = " ".join(css.split())
    for key in ("ArrowUp", "ArrowDown", "Escape", "Enter"):
        assert key in function_slice(
            app, "function handlePrepDirectoryKeydown", "function startPrepDirectoryDrag"
        )
    assert "event.ctrlKey" in app
    keyboard = function_slice(
        app, "function handlePrepDirectoryKeydown", "function startPrepDirectoryDrag"
    )
    assert "originalBasket: [...state.personalWorkbench.basket]" in keyboard
    assert "state.personalWorkbench.basket = originalBasket" in keyboard
    assert "aria-pressed" in app
    assert "aria-expanded" in app
    assert "inspectorReturnFocus" in app
    assert "approvalReturnFocus" in app
    assert 'mobileInspectorOpen = window.matchMedia("(max-width: 760px)")' in app
    assert 'openPrepInspector($("prepPaperViewport").querySelector' in app
    assert 'data-prep-mobile-target="directory"' in html
    assert 'data-prep-mobile-target="paper"' in html
    assert 'data-prep-mobile-target="inspector"' in html
    assert "@media (width <= 760px)" in css
    assert '.prep-composer[data-mobile-view="paper"] .prep-paper-pane' in css
    assert "grid-template-columns: minmax(0, 1fr) 44px" in compact_css
    assert "max-width: 100%" in compact_css
    assert "min-height: 44px" in compact_css


def test_production_javascript_still_parses() -> None:
    subprocess.run(
        ["node", "--check", str(OVERLAY / "app.js")],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
        text=True,
    )

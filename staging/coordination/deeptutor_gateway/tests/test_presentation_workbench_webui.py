from __future__ import annotations

import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


def between(value: str, start: str, end: str) -> str:
    return value.split(start, 1)[1].split(end, 1)[0]


class _Ids(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        element_id = dict(attrs).get("id")
        if element_id:
            self.ids.append(element_id)


def test_presentations_reuses_roadmap_panel_without_adding_a_legacy_tab() -> None:
    html = source("index.html")
    app = source("app.js")
    legacy = between(html, '<nav class="tab-nav legacy-tab-nav"', "</nav>")
    panel = between(html, 'id="panel-feature-roadmap"', 'id="panel-generation"')

    assert legacy.count('role="tab"') == 10
    assert html.count('role="tab"') == 14
    assert 'id="presentationWorkbench"' in panel
    assert 'id="featureRoadmapCard"' in panel
    assert 'presentations: ["PPT 备课室"' in app
    assert '"feature-roadmap"' in app
    assert 'const presentationMode = primary === "presentations";' in app
    assert '$("presentationWorkbench").hidden = !presentationMode;' in app
    assert '$("featureRoadmapCard").hidden = presentationMode;' in app
    roadmap = between(app, "const featureRoadmapCopy", "function renderFeatureRoadmap")
    assert "hotspots:" in roadmap
    assert "templates:" in roadmap
    assert "presentations:" not in roadmap


def test_teacher_gets_a_chinese_three_step_ppt_workbench() -> None:
    html = source("index.html")
    workbench = between(
        html,
        '<section id="presentationWorkbench"',
        '<section id="featureRoadmapCard"',
    )
    for copy in (
        "选主题与课时",
        "校对每页页纲",
        "生成与下载",
        "匿名薄弱点",
        "教材落点",
        "教学目标",
        "冻结当前版本",
        "生成可编辑 PPTX",
        "取消生成",
        "重试生成",
        "预览蒙太奇",
        "页纲 JSON",
        "检查报告",
    ):
        assert copy in workbench
    assert workbench.count('data-presentation-step-target="1"') >= 2
    assert workbench.count('data-presentation-step-target="2"') >= 2
    assert workbench.count('data-presentation-step-target="3"') == 1

    visible_text = re.sub(r"<[^>]+>", " ", workbench)
    for forbidden in (
        "哈希",
        "本地路径",
        "API Key",
        "schema_version",
        "data_snapshot_id",
        "theme_id",
        "publication_allowed",
        "candidate_only",
    ):
        assert forbidden not in visible_text


def test_only_current_basket_themes_supply_internal_theme_bindings() -> None:
    html = source("index.html")
    app = source("app.js")
    candidates = between(
        app,
        "function presentationThemeCandidates()",
        "function presentationSelectedThemeCandidate()",
    )
    request = between(
        app,
        "function buildPresentationThemeRequest()",
        "async function createPresentationProject",
    )

    assert 'entry?.kind !== "theme" || entry.unit !== "theme"' in candidates
    assert "state.personalWorkbench.basket" in candidates
    assert "workbenchProduct(entry.scope)" in candidates
    assert "product?.data_snapshot_id" in candidates
    assert "themeId: entry.theme_id" in candidates
    assert "const selected = presentationSelectedThemeCandidate();" in request
    assert "scope: selected.scope" in request
    assert "data_snapshot_id: selected.dataSnapshotId" in request
    assert "theme_id: selected.themeId" in request
    for forbidden_control in (
        'id="presentationScope"',
        'id="presentationSnapshot"',
        'id="presentationThemeId"',
        'name="scope"',
        'name="data_snapshot_id"',
        'name="theme_id"',
    ):
        assert forbidden_control not in html
    assert "单题和“本题及依赖”不会被当成课件来源" in html
    assert 'href="#/library"' in html
    assert 'href="#/prep"' in html


def test_create_request_is_the_exact_theme_adapter_contract() -> None:
    app = source("app.js")
    request = between(
        app,
        "function buildPresentationThemeRequest()",
        "async function createPresentationProject",
    )
    expected = {
        "schema_version",
        "scope",
        "data_snapshot_id",
        "theme_id",
        "lesson_title",
        "grade",
        "duration_minutes",
        "textbook_selection",
        "lesson_goals",
        "diagnosis",
        "classroom_plan",
        "book_title",
        "volume",
        "chapter",
        "section",
        "publisher",
        "evidence_note",
        "learning_objectives",
        "key_points",
        "difficult_points",
        "prerequisites",
        "lesson_emphasis",
        "label",
        "summary",
        "common_error",
        "cause_hypothesis",
        "confidence",
        "counterevidence",
        "teacher_questions",
        "anticipated_responses",
        "homework",
    }
    observed = set(re.findall(r"^\s{6,8}([a-z_]+)(?::|,)", request, re.MULTILINE))
    assert expected <= observed
    assert 'schemaVersion: "shchem.presentation-theme-request.v1"' in app
    create = between(
        app,
        "async function createPresentationProject",
        "const presentationSlideTypeLabels",
    )
    assert "api(presentationContract.projectsEndpoint" in create
    assert 'method: "POST"' in create
    assert "body: JSON.stringify(request)" in create


def test_outline_editor_preserves_deck_and_saves_with_cas() -> None:
    app = source("app.js")
    editor = between(
        app,
        "function presentationDeckFromEditor()",
        "async function savePresentationOutline",
    )
    save = between(
        app,
        "async function savePresentationOutline",
        "async function freezePresentationVersion",
    )
    assert "const deck = presentationClone(outline.deck_json);" in editor
    for field in (
        "title: values.title",
        "learning_purpose: values.learning_purpose",
        "student_thinking_action: values.student_thinking_action",
        "estimated_minutes: minutes",
    ):
        assert field in editor
    assert "deck.lesson_flow = deck.lesson_flow.map" in editor
    assert "if (!timing.valid)" in editor
    assert "expected_revision: state.presentationOutlineRevision" in save
    assert "deck_json: deckJson" in save
    assert 'method: "PATCH"' in save
    assert 'data-presentation-slide-field="estimated_minutes"' not in source("index.html")
    assert "data.presentationSlideField = field" not in app
    assert "control.dataset.presentationSlideField = field" in app


def test_version_render_poll_cancel_retry_and_artifact_routes_are_wired() -> None:
    app = source("app.js")
    for marker in (
        "/api/v1/presentations/projects",
        "/versions",
        "/renders",
        "/api/v1/presentations/jobs/",
        "/cancel",
        "/retry",
        "/artifacts/preview_montage",
        "/artifacts/${artifactId}",
    ):
        assert marker in app
    assert "expected_outline_revision: state.presentationOutlineRevision" in app
    assert 'body: JSON.stringify({})' in app
    assert "schedulePresentationPoll" in app
    assert "presentationContract.activeStatuses.includes" in app
    assert "URL.createObjectURL(blob)" in app
    assert "URL.revokeObjectURL(state.presentationMontageObjectUrl)" in app
    assert "stopPresentationPolling();" in app
    pagehide = app.split('window.addEventListener("pagehide"', 1)[1]
    assert "stopPresentationPolling();" in pagehide
    assert "revokePresentationMontageUrl();" in pagehide


def test_route_loads_projects_on_demand_and_mobile_css_shrinks_to_one_column() -> None:
    app = source("app.js")
    css = source("styles.css")
    route = between(app, "function setRoute()", "async function api")
    assert 'if (primary === "presentations")' in route
    assert "!state.presentationProjectsLoaded && !state.presentationPending" in route
    assert "void loadPresentationProjects()" in route
    assert "else resumePresentationRoute();" in route

    compact = " ".join(css.split())
    assert ".feature-roadmap.presentation-mode" in compact
    assert "overflow-x: clip" in compact
    assert "@media (width <= 760px)" in compact
    assert "@media (width <= 420px)" in compact
    mobile = css.rsplit("@media (width <= 760px)", 1)[1]
    for selector in (
        ".presentation-hero",
        ".presentation-steps",
        ".presentation-field-grid-two",
        ".presentation-field-grid-three",
        ".presentation-outline-summary",
        ".presentation-slide-list",
        ".presentation-downloads > div",
    ):
        assert selector in mobile
    assert "grid-template-columns: minmax(0, 1fr)" in mobile
    narrow = css.rsplit("@media (width <= 420px)", 1)[1]
    assert ".presentation-workbench input" in narrow
    assert "max-width: 100%" in narrow
    assert not re.search(r"width:\s*(?:[4-9]\d{2}|\d{4,})px", mobile)


def test_presentation_markup_keeps_unique_ids_and_javascript_parses() -> None:
    html = source("index.html")
    parser = _Ids()
    parser.feed(html)
    assert len(parser.ids) == len(set(parser.ids))
    completed = subprocess.run(
        ["node", "--check", str(OVERLAY / "app.js")],
        cwd=WORKSPACE,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr

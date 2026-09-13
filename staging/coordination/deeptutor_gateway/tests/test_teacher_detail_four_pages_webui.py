from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


class _DetailMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tabs: dict[str, dict[str, str | None]] = {}
        self.panels: dict[str, dict[str, str | None]] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id and values.get("role") == "tab":
            self.tabs[element_id] = values
        if element_id and values.get("role") == "tabpanel":
            self.panels[element_id] = values


def test_detail_is_four_accessible_pages_while_legacy_eight_routes_remain() -> None:
    html = source("index.html")
    parser = _DetailMarkup()
    parser.feed(html)
    expected = {
        "candidateReviewDetailTabStem": "candidateReviewDetailPanelStem",
        "candidateReviewDetailTabClassification": "candidateReviewDetailPanelClassification",
        "candidateReviewDetailTabAnswer": "candidateReviewDetailPanelAnswer",
        "candidateReviewDetailTabSource": "candidateReviewDetailPanelSource",
    }
    for tab_id, panel_id in expected.items():
        assert parser.tabs[tab_id]["aria-controls"] == panel_id
        assert parser.panels[panel_id]["aria-labelledby"] == tab_id
    assert parser.tabs["candidateReviewDetailTabStem"]["aria-selected"] == "true"
    assert all(
        parser.tabs[tab_id]["aria-selected"] == "false"
        for tab_id in expected
        if tab_id != "candidateReviewDetailTabStem"
    )
    legacy = html.split('<nav class="tab-nav legacy-tab-nav"', 1)[1].split(
        "</nav>", 1
    )[0]
    assert legacy.count('role="tab"') == 10
    assert html.count('role="tab"') == 14
    assert html.count('role="tabpanel"') == 14


def test_detail_pages_reuse_existing_fields_and_keep_answer_pixels_out() -> None:
    html = source("index.html")
    stem = html.split('id="candidateReviewDetailPanelStem"', 1)[1].split(
        'id="candidateReviewDetailPanelClassification"', 1
    )[0]
    classification = html.split(
        'id="candidateReviewDetailPanelClassification"', 1
    )[1].split('id="candidateReviewDetailPanelAnswer"', 1)[0]
    answer = html.split('id="candidateReviewDetailPanelAnswer"', 1)[1].split(
        'id="candidateReviewDetailPanelSource"', 1
    )[0]
    source_panel = html.split('id="candidateReviewDetailPanelSource"', 1)[1].split(
        'id="fullBankReadinessTitle"', 1
    )[0]
    for marker in (
        "candidateReviewSharedContext",
        "candidateReviewStemPosition",
        "candidateReviewVisualDependency",
        "candidateReviewChain",
    ):
        assert marker in stem
    for marker in (
        "candidateReviewDetail",
        "candidateReviewVisualTextbook",
        "candidateReviewVisualClassification",
        "candidateReviewVisualDifficulty",
        "candidateReviewGapSummary",
        "candidateReviewTaggingOverlayCard",
    ):
        assert marker in classification
    assert "candidateReviewAnswer" in answer
    assert "candidateReviewAliasCard" in answer
    assert "<img" not in answer
    assert "答案页图片、答案裁片与答案页像素始终不展示" in answer
    for marker in (
        "candidateReviewSourceSummary",
        "candidateReviewVisualSource",
        "candidateReviewRefs",
        "candidateReviewVisualEvidence",
        "卷面身份与标题归因",
    ):
        assert marker in source_panel
    assert 'id="candidateReviewImage"' in html.split(
        'id="candidateReviewQuestionStage"', 1
    )[1].split('id="candidateReviewDetailPanelStem"', 1)[0]


def test_detail_keyboard_switching_and_optional_tagging_overlay_are_bounded() -> None:
    app = source("app.js")
    switching = app[
        app.index("const candidateReviewDetailPages") :
        app.index("function candidateReviewThemeEntryForValue")
    ]
    for marker in (
        'key: "stem"',
        'key: "classification"',
        'key: "answer"',
        'key: "source"',
        'event.key === "ArrowRight"',
        'event.key === "ArrowLeft"',
        '/^[1-4]$/.test(event.key)',
        'tab.setAttribute("aria-selected"',
        "panel.hidden = !active",
    ):
        assert marker in switching
    overlay = app[
        app.index("function renderCandidateReviewTaggingOverlay") :
        app.index("function candidateReviewThemeEntryForValue")
    ]
    for marker in (
        "candidate_only_read_only_supplemental_wechat_tagging_overlay",
        "textbook_directory_mapping",
        "source_identity",
        "cognitive_prelabel",
        "blocked_pending_review",
        "非学生实测难度",
        "非真人确认",
        "受保护技术内容已隐藏",
    ):
        assert marker in overlay
    assert "fetch(" not in overlay
    assert "api(" not in overlay
    assert "answer_alignment_boundary" not in overlay
    assert "reference_answer" not in overlay
    assert "renderCandidateReviewTaggingOverlay(value.tagging_overlay || null)" in app


def test_three_column_desktop_and_760_single_column_leave_bottom_nav_clear() -> None:
    css = " ".join(source("styles.css").split())
    assert (
        ".candidate-review-layout { display: grid; grid-template-columns: "
        "minmax(210px, .58fr) minmax(280px, .86fr) minmax(320px, 1.16fr)"
    ) in css
    assert "@media (max-width: 760px)" in css
    assert '.candidate-review-layout[data-detail-page="answer"] .candidate-question-stage' in css
    assert ".candidate-detail-tabs { position: static; grid-template-columns: repeat(4, minmax(0, 1fr)); }" in css
    assert "calc(112px + env(safe-area-inset-bottom))" in css
    assert "html, body { width: 100%; max-width: 100%; margin: 0; overflow-x: clip; }" in css
    assert ".candidate-detail-tabs button { min-height: 44px;" in css

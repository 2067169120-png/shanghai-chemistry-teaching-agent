from __future__ import annotations

from pathlib import Path


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


def test_progress_card_is_teacher_facing_and_theme_first() -> None:
    html = source("index.html")
    panel = html.split('id="questionProcessingProgress"', 1)[1].split(
        'id="personalWorkbench"', 1
    )[0]
    for copy in (
        "题库整理到哪一步了？",
        "四层切割",
        "逐图扫描",
        "教材目录",
        "难度证据",
        "年份来源",
        "参考答案",
        "打开完整题链",
    ):
        assert copy in panel or copy in source("app.js")
    assert "按完整主题" in panel
    assert "未独立核验" in panel


def test_progress_load_is_read_only_and_does_not_block_daily_tools() -> None:
    app = source("app.js")
    block = app[
        app.index("function questionProcessingProgressControls") : app.index(
            "function candidateReviewThemeCard"
        )
    ]
    assert "/api/v1/kb/question-processing-progress?scope=" in block
    assert "method: \"POST\"" not in block
    assert "state.candidateReviewScope" in block
    assert "complete_theme_big_question" in block
    assert "openCandidateReviewThemeChain(item.theme.id)" in block
    assert "快捷找题、主题目录和题篮仍可继续使用" in block
    assert "quickQuestionSearchControls(false)" not in block
    assert '["master", "wave1"].includes(scope)' in block
    assert "本范围待接入" in block


def test_progress_card_does_not_turn_missing_textbook_field_into_zero() -> None:
    app = source("app.js")
    block = app[
        app.index("function questionProcessingRatio") : app.index(
            "function candidateReviewThemeCard"
        )
    ]
    assert 'unavailableLabel = "待接入"' in block
    assert "教材目录“待接入”表示产品尚无题级目录字段，不等于 0 题" in block
    assert "questionProcessingIdentityValue" in block
    assert "paper.title_zh || paper.id" in block
    assert "summary.paper_identity?.all_identity_values_present" in block
    assert "summary.paper_identity?.complete_explicit_identity" not in block


def test_progress_card_keeps_candidate_completion_and_central_pending_together() -> None:
    app = source("app.js")
    block = app[
        app.index("function renderQuestionProcessingProgress") : app.index(
            "async function loadQuestionProcessingProgress"
        )
    ]
    for marker in (
        "summary.candidate_parent_chain_overlay",
        "候选整理43/43完成",
        "5 个完整主题、56 个 effective atomic、10 个 1→N 切割",
        "中央原记录43条待确认",
        "机器候选，不是正式人审",
        "中央 Master 未改写",
        "${value.total} 个中央主题 · 候选 5 主题",
    ):
        assert marker in block
    assert "summary.parent_chain?.complete_atomic" in block
    assert "summary.atomic_total" in block


def test_progress_card_has_filters_and_small_screen_layout() -> None:
    html = source("index.html")
    styles = source("styles.css")
    for element_id in (
        "questionProcessingRefresh",
        "questionProcessingPaperFilter",
        "questionProcessingGapFilter",
        "questionProcessingList",
    ):
        assert f'id="{element_id}"' in html
    assert ".question-processing-metrics" in styles
    assert ".question-processing-paper" in styles
    mobile = styles.split("@media (max-width: 420px)", 1)[1]
    assert ".question-processing-metrics" in mobile
    assert "minmax(0, 1fr)" in mobile

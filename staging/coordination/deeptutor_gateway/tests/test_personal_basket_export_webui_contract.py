from __future__ import annotations

import json
import re
import subprocess
from html.parser import HTMLParser
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
OUTER_MANIFEST = WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


class _ExportMarkup(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.attrs_by_id: dict[str, dict[str, str | None]] = {}

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.attrs_by_id[element_id] = {"tag": tag, **values}


def test_basket_export_is_compact_chinese_and_accessible() -> None:
    html = source("index.html")
    parser = _ExportMarkup()
    parser.feed(html)
    panel = html.split('id="personalExportPanel"', 1)[1].split("</section>", 1)[0]

    for copy in (
        "题篮导出",
        "试卷标题",
        "时长（分钟）",
        "编号方式",
        "每个作答单元分值",
        "每题答题行数",
        "生成 Word 与 PDF",
    ):
        assert copy in panel

    assert parser.attrs_by_id["personalExportPanel"]["aria-labelledby"] == (
        "personalExportHeading"
    )
    assert parser.attrs_by_id["personalExportProgress"]["tag"] == "progress"
    assert parser.attrs_by_id["personalExportStatus"]["role"] == "status"
    assert parser.attrs_by_id["personalExportError"]["role"] == "alert"
    assert parser.attrs_by_id["personalExportDownloadStatus"]["aria-live"] == "polite"


def test_basket_entries_become_exact_snapshot_bound_selections() -> None:
    app = source("app.js")
    contract = app[
        app.index("function prepExportBasketContract") : app.index(
            "function setPrepExportError"
        )
    ]
    assert "if (!basket.length)" in contract
    assert "if (scopes.length !== 1)" in contract
    assert (
        'const expectedDataSnapshotId = workbenchProduct(scope)?.data_snapshot_id;'
        in contract
    )
    assert 'entry.unit === "theme"\n        ? null' in contract
    assert ': (typeof entry.source_id === "string" ? entry.source_id.trim() : "")' in contract

    selection = contract.split("selections.push({", 1)[1].split("});", 1)[0]
    assert re.findall(r"^\s*([a-z_]+)\s*[:,]", selection, flags=re.MULTILINE) == [
        "scope",
        "selection_unit",
        "theme_id",
        "target_atomic_id",
        "expected_data_snapshot_id",
    ]


def test_export_uses_real_post_polling_failure_and_no_model_call() -> None:
    app = source("app.js")
    export_code = app[
        app.index("function prepExportJobRunning") : app.index(
            "function personalThemeTagSummary"
        )
    ]
    for field in (
        "title_zh",
        "duration_minutes",
        "numbering_mode",
        "score_per_atomic",
        "answer_space_lines",
        "selections",
    ):
        assert f"{field}:" in export_code
    assert 'api("/api/v1/prep/exports", {' in export_code
    assert "method: \"POST\"" in export_code
    assert "api(`/api/v1/prep/exports/${encodeURIComponent(jobId)}`)" in export_code
    assert '["queued", "running", "completed", "failed"]' in export_code
    for stage in (
        "loading_theme_catalog",
        "loading_question_assets",
        "rendering",
        "completed",
        "failed",
    ):
        assert stage in export_code
    assert "未将任务标记为完成" in export_code
    assert "modelProvider" not in export_code
    assert "/v1/responses" not in export_code
    assert "/chat/completions" not in export_code


def test_completed_job_exposes_only_the_four_authenticated_artifacts() -> None:
    html = source("index.html")
    app = source("app.js")
    css = source("styles.css")
    expected = {
        "student_docx": "下载学生版 Word",
        "student_pdf": "下载学生版 PDF",
        "teacher_docx": "下载教师版 Word",
        "teacher_pdf": "下载教师版 PDF",
    }
    assert set(re.findall(r'data-prep-export-artifact="([^"]+)"', html)) == set(
        expected
    )
    for artifact_id, label in expected.items():
        assert label in html
        assert f"{artifact_id}: \"personalExport" in app
    assert (
        "/api/v1/prep/exports/${encodeURIComponent(state.prepExportJob.job_id)}"
        "/artifacts/${artifactId}"
    ) in app
    assert "Authorization: `Bearer ${state.token}`" in app
    assert 'state.prepExportJob?.status !== "completed"' in app
    compact_css = " ".join(css.split())
    assert ".personal-export-downloads[hidden] { display: none; }" in compact_css
    assert ".personal-export-download-buttons { grid-template-columns: minmax(0, 1fr); }" in compact_css


def test_export_uses_integer_scores_and_recovers_the_last_local_job() -> None:
    html = source("index.html")
    app = source("app.js")
    score = re.search(r'<input id="personalExportScore"[^>]+>', html)
    assert score is not None
    assert 'min="1"' in score.group(0)
    assert 'step="1"' in score.group(0)
    assert "Number.isInteger(scorePerAtomic)" in app

    assert "last_export_job: null" in app
    assert "function rememberPrepExportJob(job)" in app
    assert "last_export_job: state.personalWorkbench.last_export_job" in app
    assert "async function resumeLastPrepExport()" in app
    assert "resumeLastPrepExport();" in app
    assert "上次导出任务与本机保存的题库身份不一致" in app


def test_personal_entries_add_optional_paper_id_without_breaking_old_storage() -> None:
    app = source("app.js")
    normalizer = app[
        app.index("function normalizePersonalWorkbenchEntry") : app.index(
            "function normalizePersonalWorkbenchStore"
        )
    ]
    assert 'paper_id: typeof entry.paper_id === "string"' in normalizer
    assert ': "",' in normalizer
    assert 'storageKey: "shchem.teacher.personal-workbench.v1"' in app
    assert 'schemaVersion: "shchem_teacher_personal_workbench_v1"' in app
    for builder in (
        "function personalThemeEntry",
        "function personalParentRepairThemeEntry",
        "function personalAtomicEntry",
        "function quickQuestionSearchThemeEntry",
    ):
        block = app[app.index(builder) :]
        block = block[: block.index("\n  function ", len(builder))]
        assert "paper_id:" in block


def test_same_paper_plan_uses_only_frozen_catalog_and_explicit_theme_order() -> None:
    app = source("app.js")
    code = app[
        app.index("function personalPaperCatalogEntry") : app.index(
            "function preparePersonalPaperExport"
        )
    ]
    assert "state.candidateReviewThemeCatalog?.value" in code
    assert "catalog.papers.filter" in code
    assert "[...paperEntry.theme_groups]" in code
    assert ".sort((left, right) => left.theme.sequence - right.theme.sequence)" in code
    assert ".map((group) => personalThemeEntry({ paper: paperEntry.paper, group }))" in code
    assert "quickQuestionSearchItems" not in code
    assert "matched_atomic_ids" not in code

    script = """
const state = { candidateReviewScope: "master", candidateReviewThemeCatalog: null };
function personalThemeEntry({ paper, group }) {
  return { unit: "theme", paper_id: paper.id, theme_id: group.theme.id };
}
""" + code + """
function group(sequence) {
  return {
    theme: {
      id: `T${sequence}`,
      sequence,
      sequence_status: "known_explicit",
      parent_chain_status: "complete",
    },
    atomic_chain: [{}],
  };
}
const paper = {
  id: "PAPER-1",
  title: "测试卷",
  status: "complete_parent_chain_inventory",
  missing_theme_note_zh: null,
  observed_theme_count: 3,
};
const paperEntry = { paper, theme_groups: [group(3), group(1), group(2)] };
state.candidateReviewThemeCatalog = { value: { scope: "master", papers: [paperEntry] } };
const plan = personalPaperExportPlan("PAPER-1");
if (!plan || !plan.complete) throw new Error("complete plan missing");
if (plan.entries.map((entry) => entry.theme_id).join("|") !== "T1|T2|T3") {
  throw new Error("theme order drifted");
}
paper.status = "observed_four_theme_recall_incomplete";
paper.missing_theme_note_zh = "主题缺失";
if (personalPaperExportLabel(paperEntry) !== "不完整卷：导出当前已整理部分") {
  throw new Error("incomplete label drifted");
}
paperEntry.theme_groups = [group(1), group(1)];
paper.observed_theme_count = 2;
if (personalPaperExportPlan("PAPER-1") !== null) throw new Error("duplicate order accepted");
"""
    subprocess.run(
        ["node", "-e", script],
        cwd=WORKSPACE,
        check=True,
        capture_output=True,
        text=True,
    )


def test_same_paper_action_confirms_replacement_prefills_and_jumps_to_export() -> None:
    app = source("app.js")
    action = app[
        app.index("function preparePersonalPaperExport") : app.index(
            "function personalPaperExportButton"
        )
    ]
    assert "state.personalWorkbench.basket.length" in action
    assert "window.confirm(" in action
    assert "替换现有题篮" in action
    assert "state.personalWorkbench.basket = [...plan.entries]" in action
    assert '$(("personalExportTitle"))' not in action
    assert '$("personalExportTitle").value' in action
    assert 'window.location.hash = "#/prep"' in action
    assert '$("personalExportPanel").scrollIntoView' in action
    assert "persistPersonalWorkbench" in action

    quick = app[
        app.index("function quickQuestionSearchCard") : app.index(
            "function renderQuickQuestionSearch"
        )
    ]
    theme = app[
        app.index("function candidateReviewThemeCard") : app.index(
            "function candidateReviewParentRepairSearchText"
        )
    ]
    overview = app[
        app.index("function renderCandidateReviewThemeOverview") : app.index(
            "function candidateReviewThemeAtomicRow"
        )
    ]
    assert "personalPaperExportButton(card.paper.id" in quick
    assert "personalPaperExportButton(paper.id" in theme
    assert "personalPaperExportButton(paper.id" in overview
    assert "不会从搜索结果补成整卷" in overview


def test_both_manifests_bind_same_catalog_paper_export_contract() -> None:
    inner = json.loads(source("overlay.manifest.json"))
    outer = json.loads(OUTER_MANIFEST.read_text(encoding="utf-8"))
    assert inner["files"] == outer["files"]
    assert inner["paper_catalog_export"] == outer["paper_catalog_export"]
    contract = inner["paper_catalog_export"]
    assert contract["theme_catalog_endpoint"] == (
        "/api/v1/kb/workbench/theme-groups?scope={wave1|master|supplemental}"
    )
    assert contract["export_start_endpoint"] == "/api/v1/prep/exports"
    assert contract["selection_unit"] == "theme"
    assert contract["source_is_current_frozen_theme_catalog_by_paper_id"] is True
    assert contract["search_results_used_to_assemble_paper"] is False
    assert contract["explicit_theme_sequence_preserved"] is True
    assert contract["existing_basket_replacement_confirmation"] is True
    assert contract["paper_id_optional_in_local_entries"] is True
    assert contract["old_local_entries_compatible"] is True
    assert contract["incomplete_paper_label"] == (
        "不完整卷：导出当前已整理部分"
    )

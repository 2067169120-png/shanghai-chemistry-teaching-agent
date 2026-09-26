from __future__ import annotations

import json
import re
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
ROOT_MANIFEST = WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json"


def source(name: str) -> str:
    return (OVERLAY / name).read_text(encoding="utf-8")


def test_curriculum_route_is_a_real_teacher_workbench_not_only_taxonomy() -> None:
    html = source("index.html")
    panel = html.split('<section id="panel-taxonomy"', 1)[1].split(
        '<section id="panel-students"', 1
    )[0]

    assert 'id="curriculumWorkbench"' in panel
    assert "按教材章节找完整主题题" in panel
    assert "上海科学技术出版社高中化学" in panel
    assert 'id="curriculumTree"' in panel
    assert 'role="tree"' in panel
    assert 'id="curriculumThemeResults"' in panel
    assert 'id="curriculumSearchForm"' in panel
    assert '<option value="master" selected>主索引题库</option>' in panel
    assert '<option value="supplemental">补充上海卷题</option>' in panel
    assert "命中的小题只在完整主题内高亮" in panel
    assert "不拆散共同材料和前序依赖" in panel

    advanced = panel.split(
        '<details class="workbench-advanced-info curriculum-advanced">', 1
    )[1]
    assert "标签词表与高级校对" in advanced
    assert 'id="taxonomyGrid"' in advanced
    assert 'id="tagPatchForm"' in advanced


def test_curriculum_controls_and_ids_are_unique() -> None:
    html = source("index.html")
    ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(ids) == len(set(ids))
    for expected in (
        "curriculumRefresh",
        "curriculumVolumeCount",
        "curriculumChapterCount",
        "curriculumSectionCount",
        "curriculumMappedAtomicCount",
        "curriculumTree",
        "curriculumBreadcrumb",
        "curriculumSelectionTitle",
        "curriculumScope",
        "curriculumMappingStatus",
        "curriculumSearchSubmit",
        "curriculumThemeResults",
        "curriculumResultStatus",
        "curriculumLoadMore",
    ):
        assert expected in ids


def test_curriculum_javascript_uses_dynamic_catalog_and_theme_first_search() -> None:
    app = source("app.js")

    assert 'api("/api/v1/textbooks")' in app
    assert 'api("/api/v1/questions/search"' in app
    assert "payload = { scope: $(\"curriculumScope\").value, filters: {}, curriculum, limit: 12 }" in app
    assert "curriculum.section = selection.section" in app
    assert "curriculum.chapter_id = selection.chapter_id" in app
    assert "curriculum.volume_id = selection.volume_id" in app
    assert "curriculum.mapping_status = mappingStatus" in app
    assert "quickQuestionSearchCard(card)" in app
    assert "complete_theme_chain_returned !== true" in app
    assert "共同材料、全部小题和前序依赖均保留" in app
    assert "不会用 K 标签猜题" in app
    assert "knowledge_tag_inference_used !== false" in app
    assert "state.curriculumItems" in app
    assert "state.quickQuestionSearchItems" in app


def test_curriculum_tree_counts_are_rendered_from_api_not_static_copy() -> None:
    app = source("app.js")
    html = source("index.html")

    for element_id, count_key in (
        ("curriculumVolumeCount", "counts.volumes"),
        ("curriculumChapterCount", "counts.chapters"),
        ("curriculumSectionCount", "counts.sections"),
        ("curriculumMappedAtomicCount", "counts.mapped_atomic_count"),
    ):
        assert f'$("{element_id}").textContent = String({count_key})' in app
        assert f'id="{element_id}">—<' in html

    assert "87" not in html.split('id="curriculumWorkbench"', 1)[1].split(
        '<details class="workbench-advanced-info curriculum-advanced">', 1
    )[0]


def test_curriculum_layout_collapses_without_horizontal_overflow_contract() -> None:
    css = " ".join(source("styles.css").split())

    assert ".curriculum-layout { display: grid;" in css
    assert "grid-template-columns: minmax(300px, .82fr) minmax(0, 1.58fr)" in css
    assert "@media (max-width: 900px)" in css
    assert ".curriculum-layout { grid-template-columns: minmax(0, 1fr); }" in css
    assert ".curriculum-search-controls { grid-template-columns: minmax(0, 1fr); }" in css
    assert ".curriculum-tree-panel, .curriculum-results-panel" in css
    assert "min-width: 0" in css


def test_both_manifests_bind_the_same_curriculum_workbench_contract() -> None:
    inner = json.loads(source("overlay.manifest.json"))
    outer = json.loads(ROOT_MANIFEST.read_text(encoding="utf-8"))

    assert inner["files"] == outer["files"]
    assert inner["curriculum_workbench"] == outer["curriculum_workbench"]
    contract = inner["curriculum_workbench"]
    assert contract["catalog_endpoint"] == "/api/v1/textbooks"
    assert contract["search_endpoint"] == "/api/v1/questions/search"
    assert contract["theme_first"] is True
    assert contract["knowledge_tag_inference_used"] is False
    assert contract["active_atomic_mappings"] == 87
    assert contract["candidate_only"] is True
    assert contract["human_reviewed"] is False
    assert contract["teaching_use_allowed"] is False
    assert contract["publication_allowed"] is False

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from integrations.deeptutor_shchem_v1.supplemental_visual_scan import (
    SupplementalVisualScanReader,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
MANIFESTS = (
    OVERLAY / "overlay.manifest.json",
    WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json",
)


def test_theme_is_the_recommended_default_browse_mode_without_a_new_top_tab():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    assert '<select id="candidateReviewBrowseMode" disabled>' in html
    assert '<option value="theme" selected>按主题大题（推荐）</option>' in html
    assert '<option value="atomic">逐小题</option>' in html
    assert 'id="candidateReviewThemeWorkbench"' in html
    assert 'id="candidateReviewThemeChainToolbar"' in html
    assert 'id="candidateReviewReturnTheme"' in html
    legacy = html.split('<nav class="tab-nav legacy-tab-nav"', 1)[1].split("</nav>", 1)[0]
    assert legacy.count('role="tab"') == 10
    assert html.count('role="tab"') == 14
    assert 'id="tab-theme' not in html
    assert 'candidateReviewBrowseMode: "theme"' in app
    assert "function applyCandidateReviewBrowseMode" in app
    assert "function changeCandidateReviewBrowseMode" in app


def test_theme_catalog_is_optional_and_failure_falls_back_only_to_atomic_view():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    loader = app[
        app.index("async function loadCandidateReviewThemeGroups") :
        app.index("async function loadAllCandidateReviewAtomicParts")
    ]
    assert "/api/v1/kb/workbench/theme-groups?scope=${encodeURIComponent(scope)}" in loader
    assert "return { value: null, error }" in loader
    mode = app[
        app.index("function applyCandidateReviewBrowseMode") :
        app.index("function resetCandidateReviewOverlay")
    ]
    assert "state.candidateReviewThemeLoadError" in mode
    assert "主题目录暂不可用，已降级到逐小题视图" in mode
    assert "candidateReviewMasterCoverageSummary().visibleCount" in mode
    assert "candidateReviewExpectedTotal()" in mode
    assert "${visible}/${Number.isInteger(denominator) ? denominator : visible} 扫描覆盖" in mode
    assert "与组合筛选继续可用" in mode
    assert "未伪造主题数" in mode
    load = app[
        app.index("async function loadCandidateReview(force = false)") :
        app.index("function parseCsv")
    ]
    assert "loadCandidateReviewThemeGroups(scope, scopeLoadId)" in load
    assert "state.candidateReviewBrowseMode = \"atomic\"" in load
    assert "applyCandidateReviewBrowseMode({ announceFallback: true })" in load


def test_theme_contract_excludes_answers_and_paths_and_keeps_alias_units_separate():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    validator = app[
        app.index("const candidateReviewThemeContract") :
        app.index("function candidateReviewThemeAnswerLabel")
    ]
    for marker in (
        'schema_version: "1.0.0-theme-workbench-candidate"',
        "papers: 5, theme_groups: 25, atomic_parts: 252, display_atomic_units: 252, unassigned_atomic_parts: 0",
        "papers: 20, theme_groups: 48, atomic_parts: 470, display_atomic_units: 477, unassigned_atomic_parts: 43",
        "function candidateReviewThemeForbiddenKey",
        '"reference_answer_text"',
        '"absolute_path"',
        '"source_url"',
        "answer_text_excluded !== true",
        "row.answer.availability !== \"per_alias_unit\"",
        "row.dependency.kind !== \"per_alias_unit\"",
        "printed_sequence_status",
        "atomic_sequence_status",
        'status === "unknown_pending_review" && value === null',
        "非 alias 题链行不得携带伪造分项",
        "seenMasterIds.size !== expected.atomic_parts",
        "visual_scanned: 397, unscanned: 73, label_complete: 379, label_pending: 91, answer_aligned: 358, answer_unaligned: 13, answer_absent: 106, quality_notes: 66",
    ):
        assert marker in validator
    assert "reference_answer_text" not in app[
        app.index("function candidateReviewThemeSearchText") :
        app.index("function candidateReviewThemeAtomicRow")
    ]
    assert "answer.reference_answer_text" not in validator


def test_theme_validator_accepts_the_live_wave_and_master_dto(tmp_path: Path):
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    validator = app[
        app.index("const candidateReviewThemeContract") :
        app.index("function candidateReviewThemeAnswerLabel")
    ]
    reader = ThemeWorkbenchReader(WORKSPACE / "sh-chem-db")
    payloads = [reader.groups("wave1"), reader.groups("master")]

    def atomic_items(payload: dict) -> list[dict[str, str]]:
        rows = [
            row
            for paper in payload["papers"]
            for group in paper["theme_groups"]
            for row in group["atomic_chain"]
        ] + payload["unassigned_pending_review"]["atomic_chain"]
        return [{"node_id": row["atomic_part_id"]} for row in rows]

    script = tmp_path / "validate-theme-dto.js"
    script.write_text(
        '"use strict";\n'
        + validator
        + "\nconst input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "const result = input.map(({payload, items}) => {\n"
        + "  const checked = validateCandidateReviewThemeCatalog(payload, payload.scope, items);\n"
        + "  return {scope: payload.scope, groups: checked.groups.length, unassigned: checked.unassigned.count, "
        + "repairThemes: checked.parentChainOverlay?.groups.length || 0, "
        + "repairSource: checked.parentChainOverlay?.sourceIds.size || 0, "
        + "repairEffective: checked.parentChainOverlay?.effectiveIds.size || 0};\n"
        + "});\nprocess.stdout.write(JSON.stringify(result));\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(script)],
        input=json.dumps(
            [
                {"payload": payload, "items": atomic_items(payload)}
                for payload in payloads
            ],
            ensure_ascii=False,
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == [
        {
            "scope": "wave1",
            "groups": 25,
            "unassigned": 0,
            "repairThemes": 0,
            "repairSource": 0,
            "repairEffective": 0,
        },
        {
            "scope": "master",
            "groups": 48,
            "unassigned": 43,
            "repairThemes": 5,
            "repairSource": 43,
            "repairEffective": 56,
        },
    ]


def test_parent_chain_candidate_overlay_is_browsable_without_changing_master_counts():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    validator = app[
        app.index("function validateCandidateReviewParentRepairOverlay") :
        app.index("function validateCandidateReviewThemeCatalog")
    ]
    for marker in (
        'source_master_atomics: 43',
        'effective_atomics: 56',
        'split_source_master_atomics: 10',
        'central.atomic_total !== 470',
        'central.original_pending_atomics !== 43',
        'central.applied !== false',
        'completion.human_reviewed !== false',
        'new Set(unassigned.atomic_chain.map((row) => row.atomic_part_id))',
        'row.candidate_effective_units.length > 1 ? "split_1_to_n"',
        'textbook_mapping_candidate',
        'difficulty.ten_factors.length !== 10',
    ):
        assert marker in validator + app

    browse = app[
        app.index("function candidateReviewParentRepairThemeCard") :
        app.index("function searchCandidateReviewThemes")
    ]
    for copy in (
        "候选整理43/43完成",
        "中央原记录43条待确认",
        "机器候选，不是正式人审",
        "中央 Master 未改写",
        "5个完整主题",
        "56个effective atomic",
        "10个1→N切割",
        "共享情境",
        "明确承接 effective",
        "十因素候选，未实测",
        "沪科技教材候选",
        "目录直映，最小教材单元待核验",
        "非官方 · 未独立核验",
        "不会把 Master 分母改成 483/486",
    ):
        assert copy in browse
    assert "candidateReviewParentRepairAtomicRow(row, atomicItems[index], entry)" in browse
    assert "candidateReviewThemeCountDisplayUnits(rows)" in browse


def test_theme_validator_uses_the_live_supplemental_snapshot_and_rejects_mixing(
    tmp_path: Path,
):
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    validator = app[
        app.index("const candidateReviewThemeContract") :
        app.index("function candidateReviewThemeAnswerLabel")
    ]
    reader = SupplementalVisualScanReader(WORKSPACE / "sh-chem-db")
    status = reader.status()
    payload = reader.theme_groups()
    rows = [
        row
        for paper in payload["papers"]
        for group in paper["theme_groups"]
        for row in group["atomic_chain"]
    ]
    items = [{"node_id": row["atomic_part_id"]} for row in rows]

    script = tmp_path / "validate-supplemental-theme-dto.js"
    script.write_text(
        '"use strict";\n'
        + "const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));\n"
        + "const state = {candidateReviewScopeStatus: input.status};\n"
        + validator
        + "\nconst checked = validateCandidateReviewThemeCatalog(input.payload, 'supplemental', input.items);\n"
        + "const mixed = JSON.parse(JSON.stringify(input.payload));\n"
        + "mixed.data_snapshot_id = '0'.repeat(64);\n"
        + "let mismatchRejected = false;\n"
        + "try { validateCandidateReviewThemeCatalog(mixed, 'supplemental', input.items); } catch (_) { mismatchRejected = true; }\n"
        + "process.stdout.write(JSON.stringify({groups: checked.groups.length, mismatchRejected}));\n",
        encoding="utf-8",
    )
    completed = subprocess.run(
        ["node", str(script)],
        input=json.dumps(
            {"status": status, "payload": payload, "items": items},
            ensure_ascii=False,
        ),
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "groups": status["counts"]["theme_big_questions"],
        "mismatchRejected": True,
    }


def test_theme_cards_are_lazy_and_atomic_navigation_stays_inside_selected_chain():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
    for marker in (
        "candidate-theme-paper",
        "candidate-theme-grid",
        "candidate-theme-card",
        "candidate-theme-atomic-row",
        "candidate-theme-chain-toolbar",
    ):
        assert marker in html + css + app
    overview = app[
        app.index("function renderCandidateReviewThemeOverview") :
        app.index("function candidateReviewThemeAtomicRow")
    ]
    assert 'document.createElement("details")' in overview
    assert "candidateReviewThemeCard(entry)" in overview
    assert "group.atomic_chain" not in overview.split("function candidateReviewThemeCard", 1)[-1]
    chain = app[
        app.index("function openCandidateReviewThemeChain") :
        app.index("function searchCandidateReviewThemes")
    ]
    assert "state.candidateReviewFilteredItems = atomicItems" in chain
    assert "candidateReviewThemeAtomicRow(row, atomicItems[index])" in chain
    assert "applyCandidateReviewBrowseMode()" in chain
    navigation = app[
        app.index("function updateCandidateReviewNavigation") :
        app.index("async function navigateCandidateReview")
    ]
    assert '"本主题"' in navigation
    assert "state.candidateReviewFilteredItems" in navigation


def test_shared_context_dependency_and_recall_missing_theme_are_visible_in_chinese():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    for marker in (
        "共享材料",
        "依赖共同材料",
        "依赖上一题或一个前序",
        "依赖多个前序",
        "明确承接：",
        "独立作答",
        "主题待补（${unassigned.count}）",
        "父链不完整题独立列出；未伪造成已知主题",
        "主题4题面缺失，不能称为五主题完整卷。",
        '!== "8|11|10|6"',
        "alias 分项",
        "卷内顺序待补；保持来源次序，不按题号猜测",
        "精标题图库与全库主索引各用自己的分母",
        "${counts.papers} 份卷、${counts.theme_groups} 个明确主题、${counts.atomic_parts} 个 atomic",
    ):
        assert marker in app + html


def test_jiading_theme_card_keeps_article_identity_answer_boundary_and_explicit_handoff():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    master = ThemeWorkbenchReader(WORKSPACE / "sh-chem-db").groups("master")
    jiading = next(
        (paper, group)
        for paper in master["papers"]
        for group in paper["theme_groups"]
        if group["theme"]["id"] == "THEME-516deca72e2d9aca307d"
    )
    paper, group = jiading
    assert paper["paper"] == {
        "id": "PAPER-2f34ac7602572e37c8be",
        "title": "2024学年高三年级第二次质量调研 化学试卷 / 公众号标题归类：【高考二模】2025届上海市嘉定区高三二模化学试卷",
        "order": None,
        "order_status": "unknown_source_order_preserved",
            "status": "observed_theme_one_article_classified_incomplete_paper",
            "observed_theme_count": 1,
            "missing_theme_note_zh": "当前只完成主题一“消毒剂”10个最小作答单元的逐题扫描；“嘉定区、2025届、二模”仅来自非官方公众号标题，卷面未署地区或“二模”，本记录也不代表整卷扫描完成。",
            "source_metadata": {
                "source_tier": "core_hierarchy_audit",
                "year": "2025",
                "region": "嘉定区（公众号标题；卷面未署地区）",
                "paper_type": "二模（公众号标题；卷面写第二次质量调研）",
                "attribution_status": "unknown",
            },
        }
    assert group["theme"]["title"] == "消毒剂"
    assert group["counts"] == {
        "printed": 8,
        "atomic": 10,
        "display_atomic_units": 10,
        "visual_scanned": 10,
        "unscanned": 0,
        "label_complete": 10,
        "label_pending": 0,
        "answer_aligned": 10,
        "answer_unaligned": 0,
        "answer_absent": 0,
        "quality_notes": 4,
    }
    assert group["dependencies"] == {
        "independent": 7,
        "shared_material_only": 2,
        "one_prior_part": 1,
        "multiple_prior_parts": 0,
        "per_alias_unit": 0,
        "explicit_prior_edge_count": 1,
        "blocked": 0,
    }
    q5_p2 = next(
        row for row in group["atomic_chain"] if row["atomic_part_id"] == "JD2025-EM-S1-Q5-P2"
    )
    assert q5_p2["dependency"] == {
        "kind": "one_prior_part",
        "prior_atomic_part_ids": ["JD2025-EM-S1-Q5-P1"],
        "explicit_prior_edge_count": 1,
        "status": "validated_explicit",
    }
    assert "明确承接：${row.dependency.prior_atomic_part_ids.join(\"、\")}" in app


def test_theme_manifest_binds_static_bytes_and_candidate_only_boundary():
    expected = {
        "endpoint": "/api/v1/kb/workbench/theme-groups?scope={wave1|master|supplemental}",
        "schema_version": "1.0.0-theme-workbench-candidate",
        "default_browse_mode": "theme",
        "fallback_browse_mode": "atomic",
        "wave1_atomic_inventory": 252,
        "master_atomic_inventory": 470,
        "master_unassigned_pending_review": 43,
        "master_display_atomic_units": 477,
        "supplemental_registry_id": "SHCHEM-SUPPLEMENTAL-VISUAL-SCAN-REGISTRY-2026-08-26-V1",
        "supplemental_counts_source": "runtime_verified_registry_snapshot",
        "supplemental_dynamic_counts": True,
        "supplemental_non_additive_to_master_or_wave1": True,
        "answer_text_in_catalog_search_list_or_logs": False,
        "paths_or_urls_exposed": False,
        "alias_units_rendered_separately": True,
        "theme_details_lazy": True,
        "failure_isolated_from_atomic_workbench": True,
    }
    for path in MANIFESTS:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["theme_workbench"] == expected
        assert manifest["claims"]["candidate_only"] is True
        assert manifest["claims"]["human_reviewed"] is False
        assert manifest["claims"]["teaching_use_allowed"] is False
        for filename in ("app.js", "index.html", "styles.css"):
            payload = (OVERLAY / filename).read_bytes()
            assert manifest["files"][filename] == {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }

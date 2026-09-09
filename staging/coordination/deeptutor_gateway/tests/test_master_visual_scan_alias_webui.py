from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from integrations.deeptutor_shchem_v1.master_visual_scan_alias import (
    MasterVisualScanAliasReader,
)


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
MANIFESTS = (
    OVERLAY / "overlay.manifest.json",
    WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json",
)
ONE_TO_ONE_ID = "PT2026-EM-S3-Q1-P1"
ONE_TO_MANY_ID = "MASTER-PART-e2f8e8e5a5539ea91234"
QUESTION_CROP_ID = "PT2026-T3-Q01-QUESTION-S1"


def _extract_js_function(app: str, name: str) -> str:
    start = app.index(f"  function {name}(") + 2
    end = app.index("\n  function ", start)
    return app[start:end].strip()


def _run_js(source: str, expression: str):
    script = source + f"\nprocess.stdout.write(JSON.stringify({expression}));"
    completed = subprocess.run(
        ["node"],
        input=script,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def _walk_keys(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _walk_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_keys(nested)


def test_master_coverage_ui_separates_exact_direct_alias_visible_and_remaining():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    for marker in (
        'id="candidateReviewCoverageExact"',
        'id="candidateReviewCoverageDirect"',
        'id="candidateReviewCoverageAlias"',
        'id="candidateReviewCoverageVisible"',
        'id="candidateReviewCoverageRemaining"',
        "精确映射",
        "新逐题扫描",
        "同题别名",
        "工作台可见",
        "新增扫描为 0",
        "function candidateReviewMasterCoverageSummary",
        "function candidateReviewMasterCoverageLabel",
        "alias_existing_visual_scanned",
        "alias_overlap_with_exact_or_direct",
    ):
        assert marker in app + html

    function = _extract_js_function(app, "candidateReviewMasterCoverageSummary")
    complete_state = """
const state = {
  candidateReviewMasterVisualScanProjectionByNode: new Map(Array.from({length:169}, (_, i) => [i, i])),
  candidateReviewMasterDirectCoverage: {direct_master_visual_scanned:195},
  candidateReviewMasterDirectScanByNode: new Map(Array.from({length:195}, (_, i) => [i, i])),
  candidateReviewMasterAliasCoverage: {alias_existing_visual_scanned:14},
  candidateReviewMasterAliasByNode: new Map(Array.from({length:14}, (_, i) => [i, i])),
  candidateReviewMasterVisualScannedNodeIds: new Set(Array.from({length:378}, (_, i) => i)),
};
"""
    assert _run_js(complete_state + function, "candidateReviewMasterCoverageSummary()") == {
        "exactCount": 169,
        "directCount": 195,
        "aliasCount": 14,
        "visibleCount": 378,
        "remainingCount": 92,
        "complete": True,
    }

    alias_failed_state = """
const state = {
  candidateReviewMasterVisualScanProjectionByNode: new Map(Array.from({length:169}, (_, i) => [i, i])),
  candidateReviewMasterDirectCoverage: {direct_master_visual_scanned:195},
  candidateReviewMasterDirectScanByNode: new Map(Array.from({length:195}, (_, i) => [i, i])),
  candidateReviewMasterAliasCoverage: null,
  candidateReviewMasterAliasByNode: new Map(),
  candidateReviewMasterVisualScannedNodeIds: new Set(Array.from({length:364}, (_, i) => i)),
};
"""
    failed = _run_js(alias_failed_state + function, "candidateReviewMasterCoverageSummary()")
    assert failed == {
        "exactCount": 169,
        "directCount": 195,
        "aliasCount": None,
        "visibleCount": 364,
        "remainingCount": None,
        "complete": False,
    }
    assert "同题别名覆盖接口暂不可用；不影响本题的 Master 原生详情、精确映射或新逐题扫描" in app
    assert "只有已成功核验的扫描层参与筛选，失败层不伪造产品数、覆盖量或剩余量" in app


def test_alias_catalog_filter_and_list_badge_are_metadata_only():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    reader = MasterVisualScanAliasReader(WORKSPACE / "sh-chem-db")
    catalog = reader.catalog()
    assert catalog["count"] == 14
    assert catalog["new_physical_scans"] == 0
    assert sum(item["relation_type"] == "alias_coverage_1_to_1" for item in catalog["items"]) == 8
    assert sum(item["relation_type"] == "alias_coverage_1_to_many" for item in catalog["items"]) == 6
    assert "reference_answer_text" not in set(_walk_keys(catalog))
    assert "merged_reference_answer_text" not in set(_walk_keys(catalog))

    quick = app[
        app.index("function candidateReviewQuickMatches") :
        app.index("function candidateReviewOrderLabel")
    ]
    assert 'filter === "visual_alias"' in quick
    assert "candidateReviewMasterAliasByNode.has(item.node_id)" in quick
    assert "同题别名覆盖（普陀）" in quick
    assert "reference_answer_text" not in quick

    result_block = app[
        app.index("function renderCandidateReviewResults") :
        app.index("async function searchCandidateReview")
    ]
    assert "题图已扫、Master身份映射" in result_block
    assert "aliasScan.cardinality" in result_block
    assert "reference_answer_text" not in result_block
    assert "model_candidate_analysis" not in result_block

    search_block = app[
        app.index("async function searchCandidateReview") :
        app.index("function resetCandidateReviewSelection")
    ]
    assert "reference_answer_text" not in search_block
    assert "target_atomic_parts" not in search_block


def test_alias_detail_keeps_one_to_one_and_one_to_many_units_separate():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")
    reader = MasterVisualScanAliasReader(WORKSPACE / "sh-chem-db")
    one = reader.detail(ONE_TO_ONE_ID)
    many = reader.detail(ONE_TO_MANY_ID)
    assert one["cardinality"] == "1:1"
    assert len(one["target_atomic_parts"]) == 1
    assert one["target_atomic_parts"][0]["reference_answer"]["reference_answer_text"]
    assert many["cardinality"] == "1:2"
    assert len(many["target_atomic_parts"]) == 2
    assert many["answer_projection"]["merge_prohibited_for_one_to_many"] is True
    assert many["answer_projection"]["merged_reference_answer_text"] is None
    assert all(
        unit["reference_answer"]["reference_answer_text"] is None
        for unit in many["target_atomic_parts"]
    )

    for marker in (
        'id="candidateReviewAliasCard"',
        'id="candidateReviewAliasUnits"',
        "对应最小作答单元分析",
        "分项 ${index + 1} / ${count} · 最小作答单元",
        "禁止拼接成一个答案或一组标签",
        "1→N 同题别名分项不得展示未逐项可靠对齐的答案正文",
        "本分项参考答案",
        "答案页图片",
        "candidate-alias-unit-list",
        "candidate-alias-unit",
    ):
        assert marker in app + html + css
    render_block = app[
        app.index("function renderCandidateReviewMasterAlias(value)") :
        app.index("function renderCandidateReviewMasterAliasPreview")
    ]
    assert "validation.units.forEach" in render_block
    assert "candidateReviewAliasAnswerRows(answer, expectedMeta)" in render_block
    assert "merged_reference_answer_text" not in render_block
    assert "log(" not in render_block

    validator_source = "\n".join(
        _extract_js_function(app, name)
        for name in (
            "validateCandidateReviewMasterAliasAuthority",
            "validateCandidateReviewMasterAliasIntegrity",
            "validateCandidateReviewMasterAliasReferenceAnswer",
            "validateCandidateReviewMasterAliasDetail",
        )
    )
    catalog = reader.catalog()
    catalog_by_node = {item["master_node_id"]: item for item in catalog["items"]}
    prefix = f"""
const candidateReviewMasterAliasContract = {{scope:"candidate_only_read_only_master_visual_scan_alias",coverage_kind:"alias_existing_visual_scan"}};
const candidateReviewVisualDimensionLabels = Object.fromEntries({json.dumps([
        "information_transformations",
        "reasoning_chain_steps",
        "knowledge_module_span",
        "representation_switches",
        "calculation_load",
        "experiment_load",
        "openness",
        "unfamiliarity",
        "language_load",
        "dependency_on_prior_parts",
    ])}.map((key) => [key, key]));
const state = {{
  candidateReviewMasterAliasByNode: new Map({json.dumps([
        [ONE_TO_ONE_ID, catalog_by_node[ONE_TO_ONE_ID]],
        [ONE_TO_MANY_ID, catalog_by_node[ONE_TO_MANY_ID]],
    ], ensure_ascii=False)}),
  candidateReviewMasterAliasCoverage: {{
    product_id: {json.dumps(catalog['product_id'])},
    integrity: {json.dumps(catalog['integrity'])},
  }},
}};
"""
    validated = _run_js(
        prefix + validator_source,
        f"[{json.dumps(one, ensure_ascii=False)}, {json.dumps(many, ensure_ascii=False)}].map((value) => {{ const result = validateCandidateReviewMasterAliasDetail(value, value.master_node_id); return {{count:result.units.length, oneToMany:result.oneToMany}}; }})",
    )
    assert validated == [
        {"count": 1, "oneToMany": False},
        {"count": 2, "oneToMany": True},
    ]


def test_alias_question_pixels_use_only_the_dedicated_safe_endpoint():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    reader = MasterVisualScanAliasReader(WORKSPACE / "sh-chem-db")
    payload = reader.question_crop(ONE_TO_ONE_ID, QUESTION_CROP_ID)
    assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(payload.data).hexdigest() == payload.sha256
    preview = app[
        app.index("function renderCandidateReviewMasterAliasPreview") :
        app.index("function renderMasterCandidateReviewOverlay")
    ]
    assert "/api/v1/kb/workbench/master-visual-scan-aliases/${encodeURIComponent(masterNodeId)}/question-crops/${encodeURIComponent(evidence.crop_id)}" in preview
    assert "answer" not in preview.lower()
    assert "本地" not in preview
    open_block = app[
        app.index("async function openCandidateReviewNode") :
        app.index("function renderCandidateReviewResults")
    ]
    assert "/api/v1/kb/workbench/master-visual-scan-aliases/${encodeURIComponent(item.node_id)}" in open_block
    assert "value.visual_scan_alias" in open_block
    assert "参考答案正文写入日志: false" in open_block


def test_manifests_bind_alias_layer_and_current_static_bytes():
    for path in MANIFESTS:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        alias = manifest["master_visual_scan_alias"]
        assert alias == {
            "scope": "candidate_only_read_only_master_visual_scan_alias",
            "coverage_kind": "alias_existing_visual_scan",
            "status_endpoint": "/api/v1/kb/workbench/master-visual-scan-aliases/status",
            "catalog_endpoint": "/api/v1/kb/workbench/master-visual-scan-aliases/catalog",
            "detail_endpoint_template": "/api/v1/kb/workbench/master-visual-scan-aliases/{master_node_id}",
            "question_crop_endpoint_template": "/api/v1/kb/workbench/master-visual-scan-aliases/{master_node_id}/question-crops/{crop_id}",
            "catalog_source": "master_visual_scan_alias_catalog",
            "master_atomic_covered": 14,
            "new_physical_scans": 0,
            "one_to_one_records": 8,
            "one_to_many_records": 6,
            "target_atomic_units": 19,
            "quick_filter": "visual_alias",
            "one_to_many_units_rendered_separately": True,
            "merged_answer_or_labels_allowed": False,
            "reference_answer_text_in_catalog_search_or_list": False,
            "question_pixels_dedicated_endpoint_only": True,
            "answer_pixels_exposed": False,
            "failure_isolated_from_exact_and_direct": True,
        }
        coverage = manifest["master_visual_scan_coverage"]
        assert coverage["master_atomic_inventory"] == 470
        assert coverage["wave1_exact_visual_scanned"] == 169
        assert coverage["direct_master_visual_scanned"] == 214
        assert coverage["alias_existing_visual_scanned"] == 14
        assert coverage["visual_scanned_master_atomic"] == 397
        assert coverage["remaining_unscanned"] == 73
        assert coverage["direct_exact_overlap_required"] == 0
        assert coverage["alias_overlap_with_exact_or_direct_required"] == 0
        for filename in ("app.js", "index.html", "styles.css"):
            payload = (OVERLAY / filename).read_bytes()
            assert manifest["files"][filename] == {
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
            }

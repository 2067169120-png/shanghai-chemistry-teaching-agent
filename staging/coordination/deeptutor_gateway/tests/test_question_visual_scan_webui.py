from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchReader,
)
from integrations.deeptutor_shchem_v1.question_visual_scan import (
    QuestionVisualScanReader,
)


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"


def test_question_visual_scan_workbench_has_chinese_candidate_only_surface():
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")

    for marker in (
        'id="candidateReviewVisualScanCard"',
        'id="candidateReviewVisualScanBadge"',
        'id="candidateReviewVisualClassification"',
        'id="candidateReviewVisualSolution"',
        'id="candidateReviewVisualDifficulty"',
        'id="candidateReviewVisualRisks"',
        'id="candidateReviewVisualComparison"',
        'id="candidateReviewVisualEvidence"',
        'id="candidateReviewVisualTechnical"',
        "模型逐图复核分析",
        "不是人工复核",
        "不是官方结论",
        "不可直接用于教学或发布",
        "已逐图复核",
        "252 / 252 已完成模型逐图复核",
        "徐汇46题、青浦45题、普陀52题、杨浦53题、大同高一校内期中56题",
        "已复核 · 徐汇（46 题）",
        "已复核 · 青浦（45 题）",
        "已复核 · 普陀（52 题）",
        "已复核 · 杨浦（53 题）",
        "已复核 · 大同高一校内期中（56 题）",
        "学校名来自来源标题、卷面未显示，答案资料缺失",
        "本卷不是区模或等级考真题",
    ):
        assert marker in html + app

    assert 'api("/api/v1/kb/workbench/question-visual-scans/catalog")' in app
    assert 'api("/api/v1/kb/workbench/question-visual-scans/status")' not in app
    assert (
        "`/api/v1/kb/workbench/question-visual-scans/"
        "${encodeURIComponent(item.node_id)}`"
    ) in app
    for marker in (
        "corrected_fields_by_node",
        "scan_classification",
        "cognitive_difficulty",
        "model_candidate_analysis",
        "comparison_with_wave1",
        "evidence_descriptors",
        "answer_boundary",
        "function validateCandidateReviewVisualScanCatalog",
        "function validateCandidateReviewVisualScanDetail",
        "function renderCandidateReviewVisualScan",
        "visual_scan_completed",
        "candidateReviewVisualScanBatchByNode",
        "XH2026-QUESTION-VISUAL-SCAN-V1-2026-08-24",
        "W1-XH2026-EM",
        "8e45890206f48ad6e737dd99acd29fa0ce1a70f391bf960f8c645e6792cb2ee7",
        "QP2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
        "W1-QP2026-EM",
        "2e328518e3ceffd0d07613d52e2b8a0c82b0e4a05181ad3bd03da6e73251ac22",
        "PT2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
        "W1-PT2026-EM",
        "f738a072ab08194e8513ca0237c0ab437a31662ca8fb7673c52587ab0feb25fd",
        "YP2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
        "W1-YP2026-EM",
        "c6900402187dca1660d27036827c99b6abde0eb83cd85461ef3899fbbd552a26",
        "DT2025-H1-MID-QUESTION-VISUAL-SCAN-V1-2026-08-25",
        "W1-DT2025-H1-MID",
        "ed7eed349e287098f8aa3be3cf97091f24daab6d5153e3c4f6a20d3fa9ce8d8d",
        "output_binding_count: 11",
        "source_binding_count: 149",
        "source_binding_count: 83",
        'unique_nodes: 252',
        'visual_scanned_yp',
        'visual_scanned_dt',
        'batchCorrectedTotal !== Number(counts.compare_corrected)',
        'catalogNodeIds.join("|") !== nodeIds.join("|")',
        'semi_open: "部分开放"',
        'single_step: "单步推理"',
        'multi_step: "多步推理"',
        'one_prior_part: "依赖一个前序小题"',
        'multiple_prior_parts: "依赖多个前序小题"',
    ):
        assert marker in app
    assert 'partly_open: "部分开放"' not in app
    assert "当前首批仅覆盖徐汇 46 题" not in app
    assert "逐图扫描题目清单不是 46 个唯一题目" not in app
    # The workbench now legitimately contains an independent Pudong theme-one
    # direct-scan product.  Keep this Wave1 assertion scoped to the historical
    # PT52 identity boundary instead of banning the district name globally.
    assert "已复核 · 浦东（52 题）" not in app + html
    assert "普陀 2026 二模（52 题）" in app + html

    assert "candidate-scan-badge" in css
    assert "candidate-visual-scan-card" in css
    assert "visual-scan-factor-grid" in css
    assert "visual-scan-comparison" in css
    assert "@media (max-width: 760px)" in css
    assert "@media (max-width: 420px)" in css


def test_question_visual_scan_ui_keeps_answer_pixels_and_internal_paths_closed():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    visual_block = app[
        app.index("function validateCandidateReviewVisualScanDetail") :
        app.index("function renderCandidateReviewDetail")
    ]

    assert "reference_summary_zh" not in visual_block
    assert "visual_alignment_evidence" not in visual_block
    assert "answer_crop" not in visual_block
    assert "answer_page" not in visual_block
    assert "参考答案正文在下方参考答案区按来源边界显示" in app
    assert "答案页图片、答案裁片和答案页像素不展示" in app
    assert "function validateCandidateReviewReferenceAnswer" in app
    assert "function renderCandidateReviewReferenceAnswer" in app
    assert 'Object.keys(referenceAnswer).sort().join("|") !== expectedKeys' in app
    assert 'answerBoundary.verified !== false' in app
    assert "SHA-256 ${item.sha256}" not in visual_block
    assert "candidateReviewFieldSummary(value.integrity)" not in visual_block
    assert "内部哈希不在页面展示" in visual_block
    assert 'const evidenceKeys = ["bytes", "crop_id", "evidence_role", "height", "sha256", "source_page", "visual_inspection_status", "width"]' in app
    assert 'descriptor.visual_inspection_status !== "actually_viewed_by_primary_model"' in app
    assert "const allowedAnswerAvailability = new Set(batch.allowed_answer_availability || [])" in app
    assert "!allowedAnswerAvailability.has(answer.availability)" in app
    assert "answer.authority !== batch.answer_authority" in app
    assert 'answer_authority: "none"' in app
    assert 'allowed_answer_availability: Object.freeze(["absent"])' in app
    assert "答案资料缺失" in app
    assert "state.candidateReviewScope === \"wave1\"" in app
    assert "state.candidateReviewVisualScanNodeIds.has(item.node_id)" in app


def test_question_visual_scan_ui_uses_safe_dom_only():
    combined = "\n".join(
        (OVERLAY / name).read_text(encoding="utf-8")
        for name in ("index.html", "app.js", "styles.css")
    )

    assert "innerHTML" not in combined
    assert "insertAdjacentHTML" not in combined
    assert "new Function" not in combined
    assert re.search(r"\beval\s*\(", combined) is None
    assert "?token=" not in combined
    assert "createElement" in combined
    assert "textContent" in combined
    assert "replaceChildren" in combined


def test_question_visual_scan_api_failure_keeps_base_252_workbench_usable():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    catalog_loader = app[
        app.index("async function loadCandidateReviewVisualScanCatalog") :
        app.index("async function loadAllCandidateReviewAtomicParts")
    ]

    assert 'api("/api/v1/kb/workbench/question-visual-scans/catalog")' in catalog_loader
    assert "catch (error)" in catalog_loader
    assert "return { value: null, error };" in catalog_loader
    assert "loadAllCandidateReviewAtomicParts(scope, scopeLoadId)" in app
    assert "逐图扫描接口暂不可用；基础 252 / 252 题仍已完整加载" in app
    assert "组合筛选、前后翻题与题图继续可用" in app
    assert "基础题目详情与题图仍可使用" in app
    assert "function navigateCandidateReview" in app
    assert "renderCandidateReviewPreview(value)" in app
    assert "动态扫描接口异常时，基础 252 题、组合筛选、前后翻题和题图仍可使用" in html


def test_master470_visual_scan_projection_is_exact_only_read_only_and_non_native():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    projection_validator = app[
        app.index("function validateMasterCandidateReviewVisualScanProjection") :
        app.index("async function loadCandidateReviewVisualScanCatalog")
    ]
    filter_block = app[
        app.index("async function searchCandidateReview") :
        app.index("function resetCandidateReviewSelection")
    ]

    assert "来自 Wave1 逐图扫描的候选投影｜模型分析｜待人工复核｜非主索引原生标签" in app
    for marker in (
        'summary.state !== "exact"',
        'summary.relation_type !== "exact_1_to_1"',
        "Number(summary.relation_count) !== 1",
        "waveNodeIds.length !== 1",
        "summary.pixel_reuse_allowed !== false",
        "projectedWaveNodeIds.has(waveNodeId)",
        "projectionByMaster.size !== 169",
        "projectedWaveNodeIds.size !== 169",
        "labelProjectionAllowed !== 165",
        "labelProjectionBlocked !== 4",
    ):
        assert marker in projection_validator
    for blocked_id in (
        "W1-XH2026-EM-AP-XH2026-EM-T5-Q03-A",
        "W1-XH2026-EM-AP-XH2026-EM-T5-Q03-B",
        "W1-XH2026-EM-AP-XH2026-EM-T5-Q05-A",
        "W1-XH2026-EM-AP-XH2026-EM-T5-Q05-B",
    ):
        assert blocked_id in app

    assert "function candidateReviewMasterVisualScanProjection" in app
    assert "split、anchor 与 unmapped 均不继承扫描分析、扫描标签或题图像素" in app
    assert "只显示扫描存在状态，不显示扫描标签、分析或题图像素" in app
    assert 'candidateReviewVisualScanBadge").textContent = "扫描存在 · 标签投影阻断"' in app
    assert "不覆盖主索引原生题型、标签或筛选条件，也不继承 Wave1 题图像素" in app
    assert "原 Wave1 候选（非主索引原生）" in app
    assert "candidateReviewMasterVisualScanProjectionByNode" not in filter_block
    assert "candidateReviewVisualScanCorrected" not in filter_block
    assert "candidateReviewVisualScanBatchByNode" not in filter_block
    assert 'scope === "wave1" ? atomicItems : null' in app
    assert 'if (scope !== "wave1") return { value: null, error: null };' not in app


def test_live_master_crosswalk_has_exactly_169_scanned_identities_and_four_fixed_blocks():
    master = MasterWave1WorkbenchReader(SHCHEM_ROOT)
    items = [
        item
        for offset in (0, 200, 400)
        for item in master.list_atomic(limit=200, offset=offset)["items"]
    ]
    scan_node_ids = set(QuestionVisualScanReader(SHCHEM_ROOT).catalog()["node_ids"])
    exact = [item for item in items if item["crosswalk_summary"]["state"] == "exact"]
    allowed = [item for item in exact if item["crosswalk_summary"]["overlay_allowed"]]
    blocked = [item for item in exact if not item["crosswalk_summary"]["overlay_allowed"]]
    projected_wave_ids = [
        item["crosswalk_summary"]["wave1_node_ids"][0]
        for item in exact
    ]

    assert len(items) == 470
    assert len(exact) == len(set(projected_wave_ids)) == 169
    assert len(allowed) == 165
    assert {item["node_id"] for item in blocked} == {
        "XH2026-EM-T5-Q03-A",
        "XH2026-EM-T5-Q03-B",
        "XH2026-EM-T5-Q05-A",
        "XH2026-EM-T5-Q05-B",
    }
    assert set(projected_wave_ids) <= scan_node_ids
    assert all(
        item["crosswalk_summary"]["relation_type"] == "exact_1_to_1"
        and item["crosswalk_summary"]["relation_count"] == 1
        and item["crosswalk_summary"]["pixel_reuse_allowed"] is False
        for item in exact
    )
    assert sum(item["crosswalk_summary"]["state"] == "split" for item in items) == 17
    assert sum(item["crosswalk_summary"]["state"] == "unmapped" for item in items) == 284


def test_master470_visual_scan_api_failure_keeps_base_workbench_usable():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    assert "逐图扫描接口暂不可用；基础 470 / 470 主索引题、原生标签与筛选仍可使用" in app
    assert "只有已成功核验的扫描层参与筛选，失败层不伪造产品数、覆盖量或剩余量" in app
    assert "本题的 Wave1 逐图扫描候选读取或校验失败；基础主索引详情继续可用" in app


def test_question_visual_scan_multibatch_manifests_bind_static_files_and_closed_claims():
    expected_claims = {
        "candidate_only": True,
        "human_reviewed": False,
        "official": False,
        "publication_allowed": False,
        "retrieval_ready": False,
        "generation_allowed": False,
        "teaching_use_allowed": False,
    }
    expected_batches = [
        {
            "product_id": "XH2026-QUESTION-VISUAL-SCAN-V1-2026-08-24",
            "paper_id": "W1-XH2026-EM",
            "atomic_parts": 46,
            "manifest_self_sha256": "8e45890206f48ad6e737dd99acd29fa0ce1a70f391bf960f8c645e6792cb2ee7",
        },
        {
            "product_id": "QP2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
            "paper_id": "W1-QP2026-EM",
            "atomic_parts": 45,
            "manifest_self_sha256": "2e328518e3ceffd0d07613d52e2b8a0c82b0e4a05181ad3bd03da6e73251ac22",
        },
        {
            "product_id": "PT2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
            "paper_id": "W1-PT2026-EM",
            "atomic_parts": 52,
            "manifest_self_sha256": "f738a072ab08194e8513ca0237c0ab437a31662ca8fb7673c52587ab0feb25fd",
        },
        {
            "product_id": "YP2026-QUESTION-VISUAL-SCAN-V1-2026-08-25",
            "paper_id": "W1-YP2026-EM",
            "atomic_parts": 53,
            "manifest_self_sha256": "c6900402187dca1660d27036827c99b6abde0eb83cd85461ef3899fbbd552a26",
        },
        {
            "product_id": "DT2025-H1-MID-QUESTION-VISUAL-SCAN-V1-2026-08-25",
            "paper_id": "W1-DT2025-H1-MID",
            "atomic_parts": 56,
            "manifest_self_sha256": "ed7eed349e287098f8aa3be3cf97091f24daab6d5153e3c4f6a20d3fa9ce8d8d",
        },
    ]
    manifests = (
        OVERLAY / "overlay.manifest.json",
        WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json",
    )
    for manifest_path in manifests:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["claims"] == expected_claims
        catalog = manifest["question_visual_scan_catalog"]
        assert catalog["scope"] == "candidate_only_read_only_question_visual_scan"
        assert catalog["endpoint"] == "/api/v1/kb/workbench/question-visual-scans/catalog"
        assert catalog["expected_batch_count"] == 5
        assert catalog["expected_unique_node_count"] == 252
        assert catalog["wave1_atomic_inventory_non_additive"] == 252
        assert catalog["batches"] == expected_batches
        assert catalog["candidate_only"] is True
        for key in (
            "human_reviewed",
            "official",
            "retrieval_ready",
            "generation_allowed",
            "teaching_use_allowed",
            "publication_allowed",
            "answer_content_exposed",
            "answer_pixels_exposed",
            "source_paths_exposed",
            "hashes_rendered_in_webui",
        ):
            assert catalog[key] is False
        projection = manifest["master_visual_scan_projection"]
        assert projection == {
            "scope": "candidate_only_read_only_master_exact_visual_scan_projection",
            "source_catalog_endpoint": "/api/v1/kb/workbench/question-visual-scans/catalog",
            "detail_endpoint_template": "/api/v1/kb/workbench/question-visual-scans/{wave1_node_id}",
            "master_atomic_inventory_non_additive": 470,
            "wave1_atomic_inventory_non_additive": 252,
            "exact_identity_associations": 169,
            "scan_label_projection_allowed": 165,
            "scan_label_projection_blocked": 4,
            "blocked_wave1_node_ids": [
                "W1-XH2026-EM-AP-XH2026-EM-T5-Q03-A",
                "W1-XH2026-EM-AP-XH2026-EM-T5-Q03-B",
                "W1-XH2026-EM-AP-XH2026-EM-T5-Q05-A",
                "W1-XH2026-EM-AP-XH2026-EM-T5-Q05-B",
            ],
            "exact_state_required": True,
            "relation_count_must_equal_one": True,
            "single_wave1_node_id_required": True,
            "split_anchor_unmapped_projection_allowed": False,
            "master_native_labels_modified": False,
            "master_native_filters_modified": False,
            "question_pixels_reused": False,
            "read_only": True,
            "candidate_only": True,
            "human_reviewed": False,
            "official": False,
            "retrieval_ready": False,
            "generation_allowed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }
        for filename, descriptor in manifest["files"].items():
            assert hashlib.sha256((OVERLAY / filename).read_bytes()).hexdigest() == descriptor["sha256"]

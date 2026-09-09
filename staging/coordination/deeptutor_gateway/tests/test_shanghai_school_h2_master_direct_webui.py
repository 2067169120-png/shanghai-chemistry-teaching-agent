from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.question_visual_scan import (
    QuestionVisualScanReader,
)


WORKSPACE = Path(__file__).resolve().parents[4]
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
MANIFESTS = (
    OVERLAY / "overlay.manifest.json",
    WORKSPACE / "runtime/deeptutor_shchem/overlay.manifest.json",
)

RESEARCH_PRODUCT = "RESEARCH2024-12-QUESTION-VISUAL-SCAN-V1-2026-08-25"
SHS_PRODUCT = "SHANGHAISCHOOL-H2-SPRING-MID-QUESTION-VISUAL-SCAN-V1-2026-08-25"
CAOYANG_PRODUCT = "CAOYANG2-H2-SPRING-FINAL-QUESTION-VISUAL-SCAN-V1-2026-08-25"
RECALL_PRODUCT = "SHCHEM-VSCAN-2026-LEVEL-RECALL-35-V1"
PUDONG_PRODUCT = "SHCHEM-VSCAN-PUDONG-2026-FIRST-MOCK-THEME1-V1"
JIADING_PRODUCT = "question_visual_scan_jiading_2025_theme1_disinfectants_v1_2026-08-25"
HUANGPU_PRODUCT = "QVS-HP2025-SECOND-MOCK-T4-CALCIUM-IODATE-V1"
QIBAO_PRODUCT = "QVS-QB2025-OPENING-T4-ELECTROLYTE-WASTEWATER-V1"
HONGKOU_PRODUCT = "QVS-HK2026-SECOND-MOCK-T4-NICKEL-RECOVERY-V1"
Q24 = "MASTER-PART-21cf713883c8f26af281"


def _extract_js_function(app: str, name: str) -> str:
    start = app.index(f"  function {name}(") + 2
    end = app.index("\n  function ", start)
    return app[start:end].strip()


def _run_js(functions: list[str], expression: str):
    script = "\n".join(functions) + f"\nprocess.stdout.write(JSON.stringify({expression}));"
    completed = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(completed.stdout)


def _direct_product_constants_js() -> str:
    return "\n".join(
        (
            f'const candidateReviewHuangpuDirectProductId = "{HUANGPU_PRODUCT}";',
            f'const candidateReviewQibaoDirectProductId = "{QIBAO_PRODUCT}";',
            f'const candidateReviewHongkouDirectProductId = "{HONGKOU_PRODUCT}";',
        )
    )


def test_webui_derives_direct_products_counts_and_shortcuts_from_catalog_without_native_overwrite():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")

    for marker in (
        "candidateReviewMasterDirectProductById",
        "MASTER-DIRECT-VISUAL-SCAN-AGGREGATE-V1",
        "candidateReviewMasterDirectProductUiDescriptor",
        "candidateReviewMasterDirectCoverageLabel",
        'filter.startsWith("direct_product:")',
        "state.candidateReviewMasterDirectProductById.forEach",
        "精确映射、新逐题扫描、同题别名和剩余题量分别核验",
    ):
        assert marker in app + html

    for forbidden in (
        RESEARCH_PRODUCT,
        SHS_PRODUCT,
        CAOYANG_PRODUCT,
        RECALL_PRODUCT,
        PUDONG_PRODUCT,
        JIADING_PRODUCT,
        "expected_direct_master_atomic",
        "expected_visual_scanned_master_atomic",
        "expected_remaining_unscanned",
        "direct_research",
        "direct_shs",
        "direct_caoyang",
        "已逐图扫描（297题）",
    ):
        assert forbidden not in app + html

    quick_matcher = app[
        app.index("function candidateReviewQuickMatches") :
        app.index("function populateCandidateReviewQuickOptions")
    ]
    assert "candidateReviewMasterDirectScanByNode.get(item.node_id)?.product_id" in quick_matcher
    assert 'filter.slice("direct_product:".length)' in quick_matcher

    quick_options = app[
        app.index("function populateCandidateReviewQuickOptions") :
        app.index("function candidateReviewOrderLabel")
    ]
    assert "product.option_label" in quick_options
    assert "product.option_value" in quick_options
    assert "candidateReviewMasterVisualScannedNodeIds.size" in quick_options
    assert "populateCandidateReviewQuickOptions(scope)" in app

    catalog_validator = app[
        app.index("function validateMasterCandidateReviewDirectScanCatalog") :
        app.index("async function loadCandidateReviewVisualScanCatalog")
    ]
    for marker in (
        'expectedTopKeys = "authority|count|coverage|integrity|items|master_node_ids|product_id|products|scope"',
        "direct_batch_disjoint_verified_on_read",
        "batch_manifest_file_sha256s",
        "productsById.has",
        "directIdsByProduct",
        "item.paper_id !== product.paper_id",
        "productRecordTotal !== directCount",
        "unionCount !== exactCount + directCount",
        "remainingCount !== masterCount - unionCount",
        "visualScannedIds.size !== unionCount",
    ):
        assert marker in catalog_validator

    catalog = MasterDirectVisualScanReader(WORKSPACE / "sh-chem-db").catalog()
    descriptor_source = _extract_js_function(app, "candidateReviewMasterDirectProductUiDescriptor")
    identity_source = _extract_js_function(app, "validateCandidateReviewMasterDirectCatalogIdentity")
    coverage_source = _extract_js_function(app, "candidateReviewMasterDirectCoverageLabel")
    descriptors = _run_js(
        [
            _direct_product_constants_js(),
            descriptor_source,
        ],
        f"{json.dumps(catalog['products'], ensure_ascii=False)}.map(candidateReviewMasterDirectProductUiDescriptor)",
    )
    assert [row["option_label"] for row in descriptors] == [
        "逐图扫描·调研卷（47题）",
        "逐图扫描·上海中学高二期中（43题）",
        "逐图扫描·曹杨二中高二期末（38题）",
        "逐图扫描·2026等级考流通回忆·4主题（35题）",
        "逐图扫描·浦东2026届一模·水合肼（11题）",
        "逐图扫描·嘉定标题归类·2025届二模·消毒剂（10题）",
        "逐图扫描·黄浦区 2025 二模 · 主题四（碘酸钙的制备）（11题）",
        "逐图扫描·七宝中学 2025 开学练习 · 主题四（电解质溶液及废水处理）（10题）",
        "逐图扫描·虹口标题归类 · 2026 二模 · 主题四（电镀污泥中镍的回收与测定）（9题）",
    ]
    jiading_descriptor = next(
        row for row in descriptors if row["product_id"] == JIADING_PRODUCT
    )
    for marker in (
        "当前只完成主题一“消毒剂”的逐题扫描",
        "“嘉定区、2025届、二模”仅来自非官方公众号标题",
        "答案直接采用来源参考答案",
        "系统未独立核验",
    ):
        assert marker in jiading_descriptor["source_boundary_zh"]
    qibao_descriptor = next(
        row for row in descriptors if row["product_id"] == QIBAO_PRODUCT
    )
    assert "七宝中学卷面直接标明" in qibao_descriptor["source_boundary_zh"]
    assert "主题四“电解质溶液及废水处理”" in qibao_descriptor["source_boundary_zh"]
    hongkou_descriptor = next(
        row for row in descriptors if row["product_id"] == HONGKOU_PRODUCT
    )
    assert "卷面只直接显示“高三 化学 2026.4”" in hongkou_descriptor[
        "source_boundary_zh"
    ]
    assert "虹口区和二模来自非官方公众号标题" in hongkou_descriptor[
        "source_boundary_zh"
    ]
    assert _run_js(
        [
            _direct_product_constants_js(),
            identity_source,
        ],
        f"{json.dumps(catalog['products'], ensure_ascii=False)}.map((product) => validateCandidateReviewMasterDirectCatalogIdentity(product.paper_identity_boundary, product.product_id).kind)",
    ) == [
        "title",
        "school",
        "school",
        "recall",
        "pudong_theme",
        "jiading_theme",
        "huangpu_theme",
        "qibao_theme",
        "hongkou_theme",
    ]
    assert _run_js(
        [coverage_source],
        f"candidateReviewMasterDirectCoverageLabel({json.dumps(catalog['coverage'], ensure_ascii=False)})",
    ) == "新逐题扫描 214 题；与精确映射合计 383 题；另有别名覆盖时在独立统计中相加"

    fourth_product = {
        "product_id": "FUTURE-FOURTH-DIRECT-V1",
        "paper_id": "MASTER-PAPER-future4",
        "count": 19,
        "paper_identity_boundary": {
            "paper_face_verified_claims": ["上海市第四中学", "高二年级", "期末考试", "化学"],
            "school": "上海市第四中学",
            "academic_year": "2025学年度",
            "semester": "第二学期",
            "grade": "高二年级",
            "subject": "化学",
            "paper_family": "校内期末",
            "district_mock_claim_allowed": False,
            "level_exam_original_claim_allowed": False,
            "official_status": "nonofficial",
        },
    }
    simulated = _run_js(
        [
            _direct_product_constants_js(),
            descriptor_source,
        ],
        f"{json.dumps(catalog['products'] + [fourth_product], ensure_ascii=False)}.map(candidateReviewMasterDirectProductUiDescriptor)",
    )
    assert len(simulated) == 10
    assert simulated[-1]["option_label"] == "逐图扫描·第四中学高二期末（19题）"
    assert _run_js(
        [coverage_source],
        'candidateReviewMasterDirectCoverageLabel({"direct_master_visual_scanned":214,"visual_scanned_master_atomic":383,"remaining_unscanned":87})',
    ) == "新逐题扫描 214 题；与精确映射合计 383 题；另有别名覆盖时在独立统计中相加"

    native_filter = app[
        app.index("async function searchCandidateReview") :
        app.index("function resetCandidateReviewSelection")
    ]
    for forbidden in (
        "candidateReviewMasterDirectScanByNode",
        "candidateReviewMasterVisualScannedNodeIds",
        "scan_classification",
        "candidate_analysis",
    ):
        assert forbidden not in native_filter


def test_shanghai_school_q24_uses_the_same_nonblocking_quality_note_ui_and_answer_pixels_stay_closed():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")
    css = (OVERLAY / "styles.css").read_text(encoding="utf-8")

    conflict_message = "模型独立推导AD，与非官方答案BD冲突；未核验、不可判分/教学"
    assert Q24 not in app
    assert conflict_message not in app + html
    assert 'id="candidateReviewAnswerConflict"' not in html
    assert "candidate-answer-conflict" not in css
    assert "candidateReviewMasterDirectConflictMessage" not in app
    assert "candidateReviewMasterDirectConflictNodeId" not in app

    direct_detail = app[
        app.index("function validateCandidateReviewMasterDirectScanDetail") :
        app.index("function appendCandidateReviewVisualList")
    ]
    for forbidden in (
        "reference_summary_zh",
        "visual_alignment_evidence",
        "answer_crop",
        "answer_page",
        "source_bindings",
        "output_bindings",
    ):
        assert forbidden not in direct_detail
    assert "const expectedAnswerKeys = structuredDirect" in direct_detail
    assert '"authority|availability|independently_verified|official_answer_claim_allowed|verified"' in direct_detail
    assert ': "authority|availability|verified"' in direct_detail
    assert 'Object.keys(answer).sort().join("|") !== expectedAnswerKeys' in direct_detail
    assert 'answer.authority !== (answer.availability === "absent" ? "none" : "nonofficial_reference")' in direct_detail
    assert "answer.verified !== false" in direct_detail
    assert "只允许当前题目的 question/shared PNG" in app

    reader = MasterDirectVisualScanReader(WORKSPACE / "sh-chem-db")
    catalog = reader.catalog()
    by_product: dict[str, set[str]] = {}
    for item in catalog["items"]:
        by_product.setdefault(item["product_id"], set()).add(item["master_node_id"])
    assert len(by_product[RESEARCH_PRODUCT]) == 47
    assert len(by_product[SHS_PRODUCT]) == 43
    assert Q24 in by_product[SHS_PRODUCT]
    assert Q24 not in by_product[RESEARCH_PRODUCT]
    q24_answer = reader.detail(Q24)["reference_answer"]
    assert q24_answer["reference_answer_text"]
    assert q24_answer["quality_note"]
    assert q24_answer["reference_answer_text"] != q24_answer["quality_note"]
    assert q24_answer["independently_verified"] is False

    recall_item = next(
        item
        for item in catalog["items"]
        if item["product_id"] == "SHCHEM-VSCAN-2026-LEVEL-RECALL-35-V1"
    )
    recall_statuses = {
        descriptor["visual_inspection_status"]
        for descriptor in reader.detail(recall_item["master_node_id"])[
            "evidence_descriptors"
        ]
    }
    assert recall_statuses == {
        "source_page_actually_viewed_and_crop_bounds_checked_by_primary_model"
    }
    assert all(f'"{status}"' in direct_detail for status in recall_statuses)


def test_reference_answer_ui_uses_catalog_metadata_for_filters_and_detail_text_only():
    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    html = (OVERLAY / "index.html").read_text(encoding="utf-8")

    for marker in (
        '>参考答案</button>',
        'new Option("有逐题参考答案", "answer_aligned")',
        'new Option("存在但未对齐", "answer_unaligned")',
        'new Option("暂无参考答案", "answer_absent")',
        'new Option("有质量提示", "answer_quality_note")',
        "function candidateReviewAnswerMeta",
        "function validateCandidateReviewReferenceAnswer",
        "function renderCandidateReviewReferenceAnswer",
        "答案存在，尚未逐题对齐；正文不展示",
        "暂无参考答案",
        "来源\", \"非官方参考答案",
        "非官方来源；系统未独立核验",
        "质量提示（非阻断）",
        "答案页图片",
    ):
        assert marker in app + html

    quick_matcher = app[
        app.index("function candidateReviewQuickMatches") :
        app.index("function populateCandidateReviewQuickOptions")
    ]
    assert "candidateReviewAnswerMeta(item)" in quick_matcher
    assert "answerMeta.has_quality_note === true" in quick_matcher
    assert "reference_answer_text" not in quick_matcher

    catalog_validator = app[
        app.index("function validateCandidateReviewVisualScanCatalog") :
        app.index("function validateMasterCandidateReviewVisualScanProjection")
    ]
    assert "reference_answer_by_node" in catalog_validator
    assert 'Object.keys(row).sort().join("|") !== "availability|has_quality_note|node_id|source_authority"' in catalog_validator
    assert "answerMeta.size !== 252" in catalog_validator

    search_block = app[
        app.index("async function searchCandidateReview") :
        app.index("function resetCandidateReviewSelection")
    ]
    result_block = app[
        app.index("function renderCandidateReviewResults") :
        app.index("async function searchCandidateReview")
    ]
    for block in (search_block, result_block):
        assert "reference_answer_text" not in block
        assert ".reference_answer" not in block
    assert "参考答案正文写入日志: false" in app
    assert "参考答案正文写入日志: true" not in app

    root = WORKSPACE / "sh-chem-db"
    wave_reader = QuestionVisualScanReader(root)
    wave_catalog = wave_reader.catalog()
    wave_meta = [
        row
        for batch in wave_catalog["batches"]
        for row in batch["reference_answer_by_node"]
    ]
    assert len(wave_meta) == 252
    assert sum(row["availability"] == "present_part_aligned" for row in wave_meta) == 181
    assert sum(row["availability"] == "present_unaligned" for row in wave_meta) == 15
    assert sum(row["availability"] == "absent" for row in wave_meta) == 56
    assert sum(row["has_quality_note"] for row in wave_meta) == 34

    unaligned_id = next(row["node_id"] for row in wave_meta if row["availability"] == "present_unaligned")
    absent_id = next(row["node_id"] for row in wave_meta if row["availability"] == "absent")
    assert wave_reader.detail(unaligned_id)["reference_answer"]["reference_answer_text"] is None
    assert wave_reader.detail(absent_id)["reference_answer"]["source_authority"] == "none"

    direct_reader = MasterDirectVisualScanReader(root)
    direct_catalog = direct_reader.catalog()
    assert len(direct_catalog["items"]) == 214
    assert all(row["availability"] == "present_part_aligned" for row in direct_catalog["items"])
    assert sum(row["has_quality_note"] for row in direct_catalog["items"]) == 29
    jiading_items = [
        row for row in direct_catalog["items"] if row["product_id"] == JIADING_PRODUCT
    ]
    assert len(jiading_items) == 10
    assert all(row["source_authority"] == "nonofficial_reference" for row in jiading_items)
    jiading_detail = direct_reader.detail(jiading_items[0]["master_node_id"])
    assert jiading_detail["answer_boundary"] == {
        "availability": "present_part_aligned",
        "authority": "nonofficial_reference",
        "verified": False,
    }
    assert jiading_detail["reference_answer"]["source_authority"] == "nonofficial_reference"
    assert jiading_detail["reference_answer"]["independently_verified"] is False
    assert jiading_detail["identity_boundary"] == {
        "article_title_literal": "【高考二模】2025届上海市嘉定区高三二模化学试卷",
        "complete_paper_claim_allowed": False,
        "coverage_scope": "theme_1_disinfectants_only_10_atomic_not_complete_paper",
        "covered_theme_title": "消毒剂",
        "district_and_second_mock_basis": "wechat_article_title_only_nonofficial_not_paper_face",
        "jiading_district_face_claim_allowed": False,
        "official_identity_claim_allowed": False,
        "official_status": "nonofficial",
        "paper_face_title_literal": "2024学年高三年级第二次质量调研 化学试卷",
        "second_mock_face_claim_allowed": False,
    }
    q24_answer = direct_reader.detail(Q24)["reference_answer"]
    assert q24_answer["reference_answer_text"]
    assert q24_answer["quality_note"]
    assert q24_answer["reference_answer_text"] != q24_answer["quality_note"]
    assert q24_answer["independently_verified"] is False
    caoyang_quality = [
        row["master_node_id"]
        for row in direct_catalog["items"]
        if row["product_id"] == CAOYANG_PRODUCT and row["has_quality_note"]
    ]
    assert caoyang_quality == [
        "MASTER-PART-7ce625068ac0ff891bda",
        "MASTER-PART-40c4bc407fb543618bff",
        "MASTER-PART-82c50246e53b8c4f1645",
    ]


def test_both_overlay_manifests_bind_catalog_driven_direct_products_and_static_bytes():
    expected_claims = {
        "candidate_only": True,
        "human_reviewed": False,
        "official": False,
        "publication_allowed": False,
        "retrieval_ready": False,
        "generation_allowed": False,
        "teaching_use_allowed": False,
    }
    for path in MANIFESTS:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        assert manifest["claims"] == expected_claims
        direct = manifest["master_direct_visual_scan"]
        assert direct["aggregate_product_id"] == "MASTER-DIRECT-VISUAL-SCAN-AGGREGATE-V1"
        assert direct["product_registry_source"] == "catalog.products"
        assert direct["product_count_source"] == "catalog.products.length"
        assert direct["direct_master_atomic_count_source"] == "catalog.coverage.direct_master_visual_scanned"
        assert direct["visual_scanned_union_source"] == "catalog.coverage.visual_scanned_master_atomic"
        assert direct["remaining_unscanned_source"] == "catalog.coverage.remaining_unscanned"
        assert direct["product_shortcuts_dynamic"] is True
        assert direct["future_product_frontend_code_change_required"] is False
        assert "products_by_id" not in direct
        assert "direct_product_count" not in direct
        assert "direct_master_atomic_count" not in direct
        assert direct["answer_content_exposed"] is True
        assert direct["answer_content_authority"] == "nonofficial_reference"
        assert direct["answer_content_independently_verified"] is False
        assert direct["answer_pixels_exposed"] is False
        assert manifest["master_visual_scan_coverage"] == {
            "master_atomic_inventory": 470,
            "wave1_exact_visual_scanned": 169,
            "direct_master_visual_scanned": 214,
            "alias_existing_visual_scanned": 14,
            "visual_scanned_master_atomic": 397,
            "remaining_unscanned": 73,
            "counts_source": "master_atomic.visual_scan_coverage",
            "direct_exact_overlap_required": 0,
            "alias_overlap_with_exact_or_direct_required": 0,
            "union_formula": "wave1_exact_visual_scanned + direct_master_visual_scanned + alias_existing_visual_scanned",
            "remaining_formula": "master_atomic_inventory - visual_scanned_master_atomic",
            "split_anchor_unmapped_inheritance_allowed": False,
            "master_native_labels_modified": False,
            "master_native_filters_modified": False,
        }
        assert manifest["reference_answer_display"] == {
            "scope": "candidate_visual_scan_reference_answer_teacher_read_only",
            "detail_fields": [
                "availability",
                "reference_answer_text",
                "source_authority",
                "independently_verified",
                "quality_note",
            ],
            "catalog_filter_fields_only": [
                "availability",
                "source_authority",
                "has_quality_note",
            ],
            "filters": [
                "answer_aligned",
                "answer_unaligned",
                "answer_absent",
                "answer_quality_note",
            ],
            "aligned_reference_answer_text_displayed": True,
            "unaligned_reference_answer_text_displayed": False,
            "absent_reference_answer_text_displayed": False,
            "quality_note_nonblocking_and_separate": True,
            "quality_notes_share_uniform_nonblocking_ui": True,
            "question_specific_blocking_alerts_present": False,
            "reference_answer_text_in_search": False,
            "reference_answer_text_in_logs": False,
            "reference_answer_text_in_list_dom": False,
            "answer_page_pixels_exposed": False,
            "answer_crop_pixels_exposed": False,
            "independently_verified": False,
            "human_reviewed": False,
            "teaching_use_allowed": False,
            "publication_allowed": False,
        }
        for filename in ("app.js", "index.html", "styles.css"):
            descriptor = manifest["files"][filename]
            payload = (OVERLAY / filename).read_bytes()
            assert descriptor["sha256"] == hashlib.sha256(payload).hexdigest()
            assert descriptor["bytes"] == len(payload)

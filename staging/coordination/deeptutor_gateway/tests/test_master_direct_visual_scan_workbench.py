from __future__ import annotations

import hashlib
import http.client
import json
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

from integrations.deeptutor_shchem_v1 import (
    huangpu2025_theme4_direct_visual_scan as huangpu_module,
)
from integrations.deeptutor_shchem_v1 import (
    jiading2025_theme1_direct_visual_scan as jiading_module,
)
from integrations.deeptutor_shchem_v1 import (
    pudong2026_first_mock_theme_direct_visual_scan as pudong_module,
)
from integrations.deeptutor_shchem_v1.caoyang2_h2_direct_visual_scan import (
    EXPECTED_MANIFEST_FILE_SHA256 as CAOYANG_EXPECTED_MANIFEST_FILE_SHA256,
)
from integrations.deeptutor_shchem_v1.caoyang2_h2_direct_visual_scan import (
    EXPECTED_MANIFEST_SELF_SHA256 as CAOYANG_EXPECTED_MANIFEST_SELF_SHA256,
)
from integrations.deeptutor_shchem_v1.caoyang2_h2_direct_visual_scan import (
    KNOWN_QUALITY_NOTES as CAOYANG_KNOWN_QUALITY_NOTES,
)
from integrations.deeptutor_shchem_v1.caoyang2_h2_direct_visual_scan import (
    PRODUCT_RELATIVE as CAOYANG_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.caoyang2_h2_direct_visual_scan import (
    Caoyang2H2DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.fengxian2025_theme2_direct_visual_scan import (
    PRODUCT_ID as FENGXIAN_PRODUCT_ID,
)
from integrations.deeptutor_shchem_v1.fengxian2025_theme2_direct_visual_scan import (
    PRODUCT_RELATIVE as FENGXIAN_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    HONGKOU2026_SECOND_MOCK_THEME4_CONFIG,
    QIBAO2025_OPENING_THEME4_CONFIG,
    Hongkou2026SecondMockTheme4DirectVisualScanReader,
    Huangpu2025Theme4DirectVisualScanReader,
    Qibao2025OpeningTheme4DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    PRODUCT_ID as HUANGPU_PRODUCT_ID,
)
from integrations.deeptutor_shchem_v1.huangpu2025_theme4_direct_visual_scan import (
    PRODUCT_RELATIVE as HUANGPU_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.jiading2025_theme1_direct_visual_scan import (
    EXPECTED_MANIFEST_FILE_SHA256 as JIADING_EXPECTED_MANIFEST_FILE_SHA256,
)
from integrations.deeptutor_shchem_v1.jiading2025_theme1_direct_visual_scan import (
    EXPECTED_MANIFEST_SELF_SHA256 as JIADING_EXPECTED_MANIFEST_SELF_SHA256,
)
from integrations.deeptutor_shchem_v1.jiading2025_theme1_direct_visual_scan import (
    PRODUCT_RELATIVE as JIADING_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.jiading2025_theme1_direct_visual_scan import (
    Jiading2025Theme1DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    EXPECTED_MANIFEST_FILE_SHA256,
    EXPECTED_MANIFEST_SELF_SHA256,
    PRODUCT_RELATIVE,
    MasterDirectVisualScanError,
    MasterDirectVisualScanReader,
    ResearchDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    PRODUCT_RELATIVE as MASTER_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchReader,
)
from integrations.deeptutor_shchem_v1.pudong2026_first_mock_theme_direct_visual_scan import (
    EXPECTED_MANIFEST_FILE_SHA256 as PUDONG_EXPECTED_MANIFEST_FILE_SHA256,
)
from integrations.deeptutor_shchem_v1.pudong2026_first_mock_theme_direct_visual_scan import (
    EXPECTED_MANIFEST_SELF_SHA256 as PUDONG_EXPECTED_MANIFEST_SELF_SHA256,
)
from integrations.deeptutor_shchem_v1.pudong2026_first_mock_theme_direct_visual_scan import (
    PRODUCT_RELATIVE as PUDONG_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.pudong2026_first_mock_theme_direct_visual_scan import (
    Pudong2026FirstMockThemeDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.reference_answer import (
    project_reference_answer,
)
from integrations.deeptutor_shchem_v1.shanghai_exam_2026_recall_direct_visual_scan import (
    PRODUCT_RELATIVE as RECALL_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.shanghai_exam_2026_recall_direct_visual_scan import (
    ShanghaiExam2026RecallDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.shanghai_school_h2_direct_visual_scan import (
    EXPECTED_MANIFEST_FILE_SHA256 as SHS_EXPECTED_MANIFEST_FILE_SHA256,
)
from integrations.deeptutor_shchem_v1.shanghai_school_h2_direct_visual_scan import (
    EXPECTED_MANIFEST_SELF_SHA256 as SHS_EXPECTED_MANIFEST_SELF_SHA256,
)
from integrations.deeptutor_shchem_v1.shanghai_school_h2_direct_visual_scan import (
    PRODUCT_RELATIVE as SHS_PRODUCT_RELATIVE,
)
from integrations.deeptutor_shchem_v1.shanghai_school_h2_direct_visual_scan import (
    Q24_MASTER_NODE_ID,
    ShanghaiSchoolH2DirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.shanghai_school_h2_direct_visual_scan import (
    _safe_relative as _safe_shs_relative,
)
from integrations.deeptutor_shchem_v1.theme_workbench import (
    ThemeWorkbenchError,
    ThemeWorkbenchReader,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
OVERLAY = WORKSPACE / "runtime/deeptutor_shchem/overlay"
OPENAPI = WORKSPACE / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"


def _browser_headers(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def _request(server, path: str, *, token: str | None = TOKEN_A):
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_address[1], timeout=30
    )
    headers = _browser_headers(server)
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = response.headers
    status = response.status
    content_type = response.getheader("Content-Type") or ""
    connection.close()
    if "application/json" in content_type:
        return status, json.loads(raw.decode("utf-8")), response_headers
    return status, raw, response_headers


def _tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_size,
            path.stat().st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _copy_bound_product(source_root: Path, target_root: Path, relative: Path) -> None:
    source_product = source_root / relative
    target_product = target_root / relative
    target_product.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_product, target_product, dirs_exist_ok=True)
    manifest = json.loads((source_product / "manifest.json").read_text(encoding="utf-8"))
    source_bindings = manifest.get("source_bindings")
    if source_bindings is None:
        source_manifest = json.loads(
            (source_product / "source_manifest.json").read_text(encoding="utf-8")
        )
        source_bindings = source_manifest.get(
            "source_bindings", source_manifest.get("sources")
        )
        if source_bindings is None:
            source_bindings = [
                *source_manifest.get("source_assets", []),
                *source_manifest.get("protected_inputs", []),
                *source_manifest.get("textbook_sources", []),
            ]
    assert isinstance(source_bindings, list)
    copied: set[str] = set()
    for binding in source_bindings:
        path_text = binding["path"]
        if path_text in copied:
            continue
        copied.add(path_text)
        relative_path = Path(path_text)
        if relative_path.parts and relative_path.parts[0] == "课本":
            source = source_root.parent / relative_path
            target = target_root.parent / relative_path
        else:
            source = source_root / relative_path
            target = target_root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _isolated_root(temp: Path) -> Path:
    root = temp / "sh-chem-db"
    _copy_bound_product(SHCHEM_ROOT, root, PRODUCT_RELATIVE)
    _copy_bound_product(SHCHEM_ROOT, root, SHS_PRODUCT_RELATIVE)
    _copy_bound_product(SHCHEM_ROOT, root, CAOYANG_PRODUCT_RELATIVE)
    _copy_bound_product(SHCHEM_ROOT, root, RECALL_PRODUCT_RELATIVE)
    _copy_bound_product(SHCHEM_ROOT, root, PUDONG_PRODUCT_RELATIVE)
    _copy_bound_product(SHCHEM_ROOT, root, JIADING_PRODUCT_RELATIVE)
    _copy_bound_product(SHCHEM_ROOT, root, HUANGPU_PRODUCT_RELATIVE)
    _copy_bound_product(
        SHCHEM_ROOT, root, QIBAO2025_OPENING_THEME4_CONFIG.product_relative
    )
    _copy_bound_product(
        SHCHEM_ROOT, root, HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.product_relative
    )
    _copy_bound_product(SHCHEM_ROOT, root, FENGXIAN_PRODUCT_RELATIVE)
    _copy_bound_product(SHCHEM_ROOT, root, MASTER_PRODUCT_RELATIVE)
    return root


def test_reader_projects_exact_224_safe_records_and_one_research_png():
    reader = MasterDirectVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    catalog = reader.catalog()

    assert status["coverage"] == {
        "master_atomic_inventory": 470,
        "wave1_exact_visual_scanned": 169,
        "direct_master_visual_scanned": 224,
        "visual_scanned_master_atomic": 393,
        "remaining_unscanned": 77,
        "direct_exact_overlap": 0,
    }
    assert catalog["count"] == len(catalog["master_node_ids"]) == 224
    assert len(set(catalog["master_node_ids"])) == 224
    assert all(
        item["availability"] == "present_part_aligned"
        and item["source_authority"] == "nonofficial_reference"
        and type(item["has_quality_note"]) is bool
        and "reference_answer_text" not in item
        for item in catalog["items"]
    )
    node_id = catalog["master_node_ids"][0]
    detail = reader.detail(node_id)
    question = next(
        item for item in detail["evidence_descriptors"]
        if item["evidence_role"] == "question"
    )
    crop = reader.question_crop(node_id, question["crop_id"])

    assert detail["master_node_id"] == detail["scan_hierarchy"]["atomic_part_id"] == node_id
    assert detail["scan_hierarchy"]["paper_id"] == "XJYF2024-12-RESEARCH-PAPER"
    assert crop.content_type == "image/png"
    assert crop.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert hashlib.sha256(crop.data).hexdigest() == question["sha256"] == crop.sha256
    assert len(crop.data) == question["bytes"] < 1048576


def test_detail_projection_excludes_answers_paths_bindings_urls_and_authority_upgrades():
    reader = MasterDirectVisualScanReader(SHCHEM_ROOT)
    node_id = reader.catalog()["master_node_ids"][0]
    detail = reader.detail(node_id)
    serialized = json.dumps(detail, ensure_ascii=False, sort_keys=True)

    assert detail["answer_boundary"] == {
        "availability": "present_part_aligned",
        "authority": "nonofficial_reference",
        "verified": False,
    }
    raw_record = json.loads(
        (SHCHEM_ROOT / PRODUCT_RELATIVE / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert detail["reference_answer"]["reference_answer_text"] == raw_record[
        "answer"
    ]["reference_summary_zh"]
    assert detail["reference_answer"]["independently_verified"] is False
    for forbidden in (
        "reference_summary_zh",
        "visual_alignment_evidence",
        "answer_crop",
        "answer_page",
        "source_manifest",
        "source_bindings",
        "output_bindings",
        "crop_asset",
        "source_asset",
        "absolute_path",
        "http://",
        "https://",
    ):
        assert forbidden not in serialized
    assert detail["authority"]["candidate_only"] is True
    assert detail["authority"]["read_only"] is True
    assert all(
        value is False
        for key, value in detail["authority"].items()
        if key not in {"candidate_only", "read_only"}
    )


def test_live_direct224_and_exact169_are_disjoint_and_union_to_393():
    master = MasterWave1WorkbenchReader(SHCHEM_ROOT).visual_scan_identity_index()
    direct = set(MasterDirectVisualScanReader(SHCHEM_ROOT).catalog()["master_node_ids"])
    exact = set(master["exact_master_ids"])

    assert len(master["master_atomic_ids"]) == 470
    assert len(direct) == 224
    assert len(exact) == 169
    assert direct <= set(master["master_atomic_ids"])
    assert direct.isdisjoint(exact)
    assert len(direct | exact) == 393
    assert len(set(master["master_atomic_ids"]) - direct - exact) == 77


def test_fengxian_grouped_projection_preserves_10_master_and_13_atomic_units():
    reader = MasterDirectVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    fengxian_ids = [
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"] == FENGXIAN_PRODUCT_ID
    ]
    assert len(fengxian_ids) == 10
    assert len(set(fengxian_ids)) == 10
    details = [reader.detail(node_id) for node_id in fengxian_ids]
    atomic_ids = [
        unit["atomic_part_id"]
        for detail in details
        for unit in detail["minimal_atomic_units"]
    ]
    assert len(atomic_ids) == len(set(atomic_ids)) == 13
    for question in (2, 3, 7):
        detail = reader.detail(f"FX2025-EM-S2-Q{question}-P1")
        assert [
            unit["atomic_part_id"] for unit in detail["minimal_atomic_units"]
        ] == [f"FX2025-EM-S2-Q{question}-P1", f"FX2025-EM-S2-Q{question}-P2"]
        with pytest.raises(MasterDirectVisualScanError) as missing:
            reader.detail(f"FX2025-EM-S2-Q{question}-P2")
        assert missing.value.status == 404


def test_all_registered_direct_products_reference_text_is_exact_and_never_cataloged():
    readers = (
        ResearchDirectVisualScanReader(SHCHEM_ROOT),
        ShanghaiSchoolH2DirectVisualScanReader(SHCHEM_ROOT),
        Caoyang2H2DirectVisualScanReader(SHCHEM_ROOT),
        ShanghaiExam2026RecallDirectVisualScanReader(SHCHEM_ROOT),
        Pudong2026FirstMockThemeDirectVisualScanReader(SHCHEM_ROOT),
        Jiading2025Theme1DirectVisualScanReader(SHCHEM_ROOT),
        Huangpu2025Theme4DirectVisualScanReader(SHCHEM_ROOT),
        Qibao2025OpeningTheme4DirectVisualScanReader(SHCHEM_ROOT),
        Hongkou2026SecondMockTheme4DirectVisualScanReader(SHCHEM_ROOT),
    )
    total = 0
    for reader in readers:
        snapshot = reader._snapshot()
        catalog = reader.catalog()
        metadata = {item["master_node_id"]: item for item in catalog["items"]}
        assert len(metadata) == len(snapshot.records)
        for record in snapshot.records:
            master_id = record["hierarchy"]["atomic_part_id"]
            candidate_analysis = record.get("candidate_analysis", {})
            has_quality_code_field = (
                "known_quality_note_code" in candidate_analysis
            )
            quality_code = candidate_analysis.get("known_quality_note_code")
            projection = project_reference_answer(
                record["answer"],
                None
                if has_quality_code_field
                else record.get("risks_and_limits"),
                quality_code,
            )
            assert projection["availability"] == "present_part_aligned"
            assert projection["reference_answer_text"] == record["answer"][
                "reference_summary_zh"
            ]
            assert projection["source_authority"] == "nonofficial_reference"
            assert projection["independently_verified"] is False
            if has_quality_code_field:
                expected_quality = bool(quality_code)
            elif "risks_and_limits" in record:
                expected_quality = bool(
                    record["risks_and_limits"][
                        "ambiguity_or_multiple_solutions_zh"
                    ]
                )
            else:
                expected_quality = bool(record["answer"].get("quality_note"))
            assert metadata[master_id]["has_quality_note"] is expected_quality
            assert "reference_answer_text" not in metadata[master_id]
            total += 1
    aggregate = MasterDirectVisualScanReader(SHCHEM_ROOT).catalog()
    assert len(readers) + 1 == len(aggregate["products"])
    assert total == 214
    assert aggregate["count"] == total + 10 == 224


def test_shs_reader_preserves_q24_conflict_without_exposing_answer_content_or_crop():
    reader = ShanghaiSchoolH2DirectVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    detail = reader.detail(Q24_MASTER_NODE_ID)
    serialized = json.dumps(detail, ensure_ascii=False, sort_keys=True)

    assert catalog["count"] == 43
    assert detail["answer_boundary"] == {
        "availability": "present_part_aligned",
        "authority": "nonofficial_reference",
        "verified": False,
    }
    assert detail["scan_classification"]["selection_rule"] == "indeterminate"
    assert detail["candidate_analysis"]["correctness_verified"] is False
    raw_record = next(
        json.loads(line)
        for line in (SHCHEM_ROOT / SHS_PRODUCT_RELATIVE / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if Q24_MASTER_NODE_ID in line
    )
    assert detail["reference_answer"] == {
        "availability": "present_part_aligned",
        "reference_answer_text": raw_record["answer"]["reference_summary_zh"],
        "source_authority": "nonofficial_reference",
        "independently_verified": False,
        "quality_note": "扫描记录含显式歧义或多解提示；该提示不阻断来源答案原文展示。",
    }
    assert "答案BD" in serialized and "计算AD" in serialized
    for forbidden in (
        "reference_summary_zh",
        "visual_alignment_evidence",
        "answer_crop",
        "answer_page",
        "crop_path",
        "source_path",
        "source_bindings",
        "output_bindings",
        "http://",
        "https://",
    ):
        assert forbidden not in serialized

    answer_crop_id = raw_record["answer"]["visual_alignment_evidence"][0]["crop_id"]
    with pytest.raises(MasterDirectVisualScanError) as exc_info:
        MasterDirectVisualScanReader(SHCHEM_ROOT).question_crop(
            Q24_MASTER_NODE_ID, answer_crop_id
        )
    assert exc_info.value.status == 403


def test_caoyang38_reader_projects_three_nonblocking_quality_notes_and_denies_answers():
    reader = Caoyang2H2DirectVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    assert catalog["count"] == 38
    assert sum(item["has_quality_note"] for item in catalog["items"]) == 3
    assert set(CAOYANG_KNOWN_QUALITY_NOTES.values()) == {
        "Q07_REFERENCE_ANSWER_ARITHMETIC_NOTE",
        "Q32_REFERENCE_ANSWER_ASYMMETRIC_CARBON_NOTE",
        "Q33_REFERENCE_ANSWER_SINGLE_CHOICE_NOTE",
    }
    rows = [
        json.loads(line)
        for line in (SHCHEM_ROOT / CAOYANG_PRODUCT_RELATIVE / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    q07 = next(
        row
        for row in rows
        if row["candidate_analysis"]["known_quality_note_code"]
        == "Q07_REFERENCE_ANSWER_ARITHMETIC_NOTE"
    )
    node_id = q07["hierarchy"]["atomic_part_id"]
    detail = reader.detail(node_id)
    assert detail["reference_answer"]["reference_answer_text"] == q07["answer"][
        "reference_summary_zh"
    ]
    assert detail["reference_answer"]["independently_verified"] is False
    assert "Q07_REFERENCE_ANSWER_ARITHMETIC_NOTE" in detail["reference_answer"][
        "quality_note"
    ]
    assert set(detail["candidate_analysis"]) == {
        "candidate_only",
        "correctness_verified",
        "solution_path_zh",
    }
    serialized = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "reference_summary_zh",
        "visual_alignment_evidence",
        "answer_crop",
        "answer_page",
        "crop_path",
        "source_path",
        "source_bindings",
        "output_bindings",
        "http://",
        "https://",
    ):
        assert forbidden not in serialized
    question = next(
        item for item in detail["evidence_descriptors"]
        if item["evidence_role"] == "question"
    )
    payload = reader.question_crop(node_id, question["crop_id"])
    assert hashlib.sha256(payload.data).hexdigest() == question["sha256"]
    answer_crop_id = q07["answer"]["visual_alignment_evidence"][0]["crop_id"]
    with pytest.raises(MasterDirectVisualScanError) as exc_info:
        MasterDirectVisualScanReader(SHCHEM_ROOT).question_crop(
            node_id, answer_crop_id
        )
    assert exc_info.value.status == 403


def test_pudong_theme11_reader_preserves_theme_boundary_answers_and_crop_roles():
    reader = Pudong2026FirstMockThemeDirectVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    assert catalog["count"] == 11
    assert sum(item["has_quality_note"] for item in catalog["items"]) == 3
    assert catalog["paper_identity_boundary"] == {
        "paper_face_title_literal": (
            "浦东新区2025学年度第一学期期末教学质量检测 高三化学试卷"
        ),
        "article_cohort_label": "2026届浦东新区高三一模",
        "region": "浦东新区",
        "paper_family": "first_mock",
        "covered_scope": "主题一（水合肼）",
        "complete_paper_local_pages_present": True,
        "complete_paper_visual_scan_claim_allowed": False,
        "pudong_second_mock_claim_allowed": False,
        "putuo_pt_identity_claim_allowed": False,
        "official_status": "nonofficial",
        "source_account": "学教有方",
        "answer_authority": "nonofficial_reference",
        "answer_independently_verified": False,
    }
    assert "reference_answer_text" not in json.dumps(catalog, ensure_ascii=False)
    rows = [
        json.loads(line)
        for line in (SHCHEM_ROOT / PUDONG_PRODUCT_RELATIVE / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    q3 = next(
        row
        for row in rows
        if row["hierarchy"]["atomic_part_id"] == "PD2026-YM-S1-Q3-P1"
    )
    detail = reader.detail("PD2026-YM-S1-Q3-P1")
    assert detail["scan_hierarchy"]["paper_id"] == "PAPER-22b0a396c9c1c0a37591"
    assert detail["scan_hierarchy"]["theme_id"] == "THEME-2c558871ad0b19268d6a"
    assert detail["scan_hierarchy"]["theme_title"] == "水合肼"
    assert detail["identity_boundary"]["complete_paper_claim_allowed"] is False
    assert detail["identity_boundary"]["pudong_second_mock_claim_allowed"] is False
    assert detail["identity_boundary"]["putuo_pt_identity_claim_allowed"] is False
    assert detail["reference_answer"]["reference_answer_text"] == q3["answer"][
        "reference_summary_zh"
    ]
    assert detail["reference_answer"]["quality_note"] == q3["answer"][
        "quality_note"
    ]
    assert detail["reference_answer"]["independently_verified"] is False
    assert set(detail["candidate_analysis"]) == {
        "candidate_only",
        "correctness_verified",
        "solution_path_zh",
    }
    question = next(
        item
        for item in detail["evidence_descriptors"]
        if item["evidence_role"] == "question"
    )
    shared = next(
        item
        for item in detail["evidence_descriptors"]
        if item["evidence_role"] == "shared_material"
    )
    for descriptor in (question, shared):
        payload = reader.question_crop(
            "PD2026-YM-S1-Q3-P1", descriptor["crop_id"]
        )
        assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(payload.data).hexdigest() == descriptor["sha256"]
    answer_crop_id = q3["answer"]["visual_alignment_evidence"][0]["crop_id"]
    for forbidden_crop_id in (answer_crop_id, "PD2026-T1-BOUNDARY-NEXT-THEME"):
        with pytest.raises(MasterDirectVisualScanError) as issue:
            reader.question_crop("PD2026-YM-S1-Q3-P1", forbidden_crop_id)
        assert issue.value.status == 403


def test_jiading_theme10_reader_preserves_identity_dependencies_answers_and_crop_roles():
    reader = Jiading2025Theme1DirectVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    assert catalog["count"] == 10
    assert sum(item["has_quality_note"] for item in catalog["items"]) == 4
    assert catalog["paper_identity_boundary"] == {
        "paper_face_title_literal": "2024学年高三年级第二次质量调研 化学试卷",
        "article_title_literal": "【高考二模】2025届上海市嘉定区高三二模化学试卷",
        "district_and_second_mock_basis": (
            "wechat_article_title_only_nonofficial_not_paper_face"
        ),
        "region_label": "嘉定区（仅公众号标题）",
        "paper_family": "second_mock_article_classified",
        "covered_scope": "主题一（消毒剂）",
        "complete_paper_visual_scan_claim_allowed": False,
        "jiading_district_face_claim_allowed": False,
        "second_mock_face_claim_allowed": False,
        "official_identity_claim_allowed": False,
        "official_status": "nonofficial",
        "source_account": "学教有方",
        "answer_authority": "nonofficial_reference",
        "answer_independently_verified": False,
    }
    assert "reference_answer_text" not in json.dumps(catalog, ensure_ascii=False)
    rows = [
        json.loads(line)
        for line in (SHCHEM_ROOT / JIADING_PRODUCT_RELATIVE / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    by_id = {row["hierarchy"]["atomic_part_id"]: row for row in rows}

    q5_id = "JD2025-EM-S1-Q5-P2"
    q5 = reader.detail(q5_id)
    assert q5["scan_hierarchy"]["paper_id"] == "PAPER-2f34ac7602572e37c8be"
    assert q5["scan_hierarchy"]["theme_id"] == "THEME-516deca72e2d9aca307d"
    assert q5["scan_hierarchy"]["theme_title"] == "消毒剂"
    assert q5["identity_boundary"]["complete_paper_claim_allowed"] is False
    assert q5["identity_boundary"]["jiading_district_face_claim_allowed"] is False
    assert q5["identity_boundary"]["second_mock_face_claim_allowed"] is False
    assert q5["dependency"]["prior_atomic_part_ids"] == [
        "JD2025-EM-S1-Q5-P1"
    ]
    assert q5["dependency"]["shared_material_crop_ids"] == []
    assert q5["reference_answer"]["reference_answer_text"] == by_id[q5_id][
        "answer"
    ]["reference_summary_zh"]
    assert q5["reference_answer"]["independently_verified"] is False

    q7_id = "JD2025-EM-S1-Q7-P2"
    q7 = reader.detail(q7_id)
    assert q7["dependency"]["prior_atomic_part_ids"] == []
    assert q7["dependency"]["shared_material_crop_ids"] == [
        "JD2025-T1-SHARED-GRAPH-INTRO",
        "JD2025-T1-SHARED-SPECIATION-GRAPH",
    ]
    roles = {item["evidence_role"] for item in q7["evidence_descriptors"]}
    assert roles == {"question", "shared_material"}
    for descriptor in q7["evidence_descriptors"]:
        payload = reader.question_crop(q7_id, descriptor["crop_id"])
        assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(payload.data).hexdigest() == descriptor["sha256"]

    answer_crop_id = by_id[q5_id]["answer"]["visual_alignment_evidence"][0][
        "crop_id"
    ]
    for forbidden_crop_id in (
        answer_crop_id,
        "JD2025-T1-BOUNDARY-NEXT-THEME-P2",
    ):
        with pytest.raises(MasterDirectVisualScanError) as issue:
            reader.question_crop(q5_id, forbidden_crop_id)
        assert issue.value.status == 403


def test_pudong_fifth_product_drift_blocks_aggregate_and_theme_only(monkeypatch):
    with tempfile.TemporaryDirectory(
        prefix="pudong-theme-reader-test-", dir=SHCHEM_ROOT
    ) as raw:
        copied = Path(raw) / "product"
        shutil.copytree(SHCHEM_ROOT / PUDONG_PRODUCT_RELATIVE, copied)
        crop = next((copied / "crops" / "question").glob("*.png"))
        crop.write_bytes(crop.read_bytes() + b"drift")
        monkeypatch.setattr(
            pudong_module, "PRODUCT_RELATIVE", copied.relative_to(SHCHEM_ROOT)
        )
        assert ResearchDirectVisualScanReader(SHCHEM_ROOT).status()["counts"][
            "scan_records"
        ] == 47
        assert MasterWave1WorkbenchReader(SHCHEM_ROOT).status()["counts"][
            "exact_identity"
        ] == 169
        with pytest.raises(MasterDirectVisualScanError):
            MasterDirectVisualScanReader(SHCHEM_ROOT).status()
        with pytest.raises(ThemeWorkbenchError):
            ThemeWorkbenchReader(SHCHEM_ROOT).groups("master")


def test_jiading_registered_product_drift_blocks_aggregate_and_theme_only(monkeypatch):
    with tempfile.TemporaryDirectory(
        prefix="jiading-theme-reader-test-", dir=SHCHEM_ROOT
    ) as raw:
        copied = Path(raw) / "product"
        shutil.copytree(SHCHEM_ROOT / JIADING_PRODUCT_RELATIVE, copied)
        crop = next((copied / "crops" / "question").glob("*.png"))
        crop.write_bytes(crop.read_bytes() + b"drift")
        monkeypatch.setattr(
            jiading_module, "PRODUCT_RELATIVE", copied.relative_to(SHCHEM_ROOT)
        )
        assert ResearchDirectVisualScanReader(SHCHEM_ROOT).status()["counts"][
            "scan_records"
        ] == 47
        assert MasterWave1WorkbenchReader(SHCHEM_ROOT).status()["counts"][
            "exact_identity"
        ] == 169
        with pytest.raises(MasterDirectVisualScanError):
            MasterDirectVisualScanReader(SHCHEM_ROOT).status()
        with pytest.raises(ThemeWorkbenchError):
            ThemeWorkbenchReader(SHCHEM_ROOT).groups("master")


def test_reader_fails_closed_on_output_and_source_byte_drift(tmp_path: Path):
    root = _isolated_root(tmp_path)
    product = root / PRODUCT_RELATIVE
    readme = product / "README.md"
    original_readme = readme.read_bytes()
    readme.write_bytes(original_readme + b"\n")
    with pytest.raises(MasterDirectVisualScanError):
        MasterDirectVisualScanReader(root).status()

    readme.write_bytes(original_readme)
    source_manifest = json.loads(
        (product / "source_manifest.json").read_text(encoding="utf-8")
    )
    crop_binding = next(
        item for item in source_manifest["source_bindings"]
        if "/evidence/questions/" in item["path"] and item["path"].endswith(".png")
    )
    source_crop = root / crop_binding["path"]
    source_crop.write_bytes(source_crop.read_bytes() + b"x")
    with pytest.raises(MasterDirectVisualScanError):
        MasterDirectVisualScanReader(root).status()


def test_shs_reader_fails_closed_on_nested_output_closure_and_unsafe_paths(
    tmp_path: Path,
):
    root = _isolated_root(tmp_path)
    product = root / SHS_PRODUCT_RELATIVE
    nested_crop = next((product / "crops" / "question").glob("*.png"))
    original = nested_crop.read_bytes()
    nested_crop.write_bytes(original + b"x")
    with pytest.raises(MasterDirectVisualScanError):
        MasterDirectVisualScanReader(root).status()

    nested_crop.write_bytes(original)
    rogue = product / "crops" / "question" / "rogue.png"
    rogue.write_bytes(b"not-bound")
    with pytest.raises(MasterDirectVisualScanError):
        MasterDirectVisualScanReader(root).status()

    for unsafe in ("../escape.png", "/absolute.png", "C:/drive.png", "a\\b.png"):
        with pytest.raises(MasterDirectVisualScanError):
            _safe_shs_relative(unsafe)


def test_huangpu_registered_product_drift_blocks_aggregate_and_theme_only(monkeypatch):
    with tempfile.TemporaryDirectory(
        prefix="huangpu-theme-reader-test-", dir=SHCHEM_ROOT
    ) as raw:
        copied = Path(raw) / "product"
        shutil.copytree(SHCHEM_ROOT / HUANGPU_PRODUCT_RELATIVE, copied)
        crop = next((copied / "crops").glob("*.png"))
        crop.write_bytes(crop.read_bytes() + b"drift")
        monkeypatch.setattr(
            huangpu_module, "PRODUCT_RELATIVE", copied.relative_to(SHCHEM_ROOT)
        )
        assert ResearchDirectVisualScanReader(SHCHEM_ROOT).status()["counts"][
            "scan_records"
        ] == 47
        assert MasterWave1WorkbenchReader(SHCHEM_ROOT).status()["counts"][
            "exact_identity"
        ] == 169
        with pytest.raises(MasterDirectVisualScanError):
            MasterDirectVisualScanReader(SHCHEM_ROOT).status()
        with pytest.raises(ThemeWorkbenchError):
            ThemeWorkbenchReader(SHCHEM_ROOT).groups("master")


def test_caoyang_product_drift_disables_the_registered_batch_aggregate(tmp_path: Path):
    root = _isolated_root(tmp_path)
    product = root / CAOYANG_PRODUCT_RELATIVE
    crop = next((product / "crops" / "question").glob("*.png"))
    crop.write_bytes(crop.read_bytes() + b"drift")
    with pytest.raises(MasterDirectVisualScanError):
        MasterDirectVisualScanReader(root).status()


def test_http_routes_are_teacher_origin_guarded_zero_write_and_crop_role_safe(tmp_path: Path):
    state_root = tmp_path / "state"
    with running_server(build_config(state_root)) as server:
        before = _tree_snapshot(state_root)
        status, catalog_envelope, _ = _request(
            server, "/api/v1/kb/workbench/master-direct-scans/catalog"
        )
        assert status == 200
        catalog = catalog_envelope["data"]
        node_id = catalog["master_node_ids"][0]
        status, detail_envelope, _ = _request(
            server, f"/api/v1/kb/workbench/master-direct-scans/{node_id}"
        )
        assert status == 200
        detail = detail_envelope["data"]
        descriptor = next(
            item for item in detail["evidence_descriptors"]
            if item["evidence_role"] == "question"
        )
        status, png, headers = _request(
            server,
            f"/api/v1/kb/workbench/master-direct-scans/{node_id}/question-crops/{descriptor['crop_id']}",
        )
        assert status == 200
        assert png.startswith(b"\x89PNG\r\n\x1a\n")
        assert headers.get("Cache-Control") == "no-store"
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("Content-Disposition") is None
        assert _tree_snapshot(state_root) == before

        status, _, _ = _request(
            server, "/api/v1/kb/workbench/master-direct-scans/catalog?x="
        )
        assert status == 400
        status, _, _ = _request(
            server, "/api/v1/kb/workbench/master-direct-scans/catalog", token=None
        )
        assert status == 401

        raw_record = json.loads(
            (SHCHEM_ROOT / PRODUCT_RELATIVE / "scan_records.jsonl")
            .read_text(encoding="utf-8").splitlines()[0]
        )
        answer_crop_id = raw_record["answer"]["visual_alignment_evidence"][0]["crop_id"]
        status, _, _ = _request(
            server,
            f"/api/v1/kb/workbench/master-direct-scans/{node_id}/question-crops/{answer_crop_id}",
        )
        assert status == 403


def test_http_routes_serve_shs_question_pixels_but_deny_q24_answer_pixels(
    tmp_path: Path,
):
    state_root = tmp_path / "state"
    with running_server(build_config(state_root)) as server:
        before = _tree_snapshot(state_root)
        status, detail_envelope, _ = _request(
            server, f"/api/v1/kb/workbench/master-direct-scans/{Q24_MASTER_NODE_ID}"
        )
        assert status == 200
        detail = detail_envelope["data"]
        assert detail["product_id"].startswith("SHANGHAISCHOOL-H2-SPRING-MID-")
        descriptor = next(
            item for item in detail["evidence_descriptors"]
            if item["evidence_role"] == "question"
        )
        status, png, headers = _request(
            server,
            f"/api/v1/kb/workbench/master-direct-scans/{Q24_MASTER_NODE_ID}/question-crops/{descriptor['crop_id']}",
        )
        assert status == 200
        assert hashlib.sha256(png).hexdigest() == descriptor["sha256"]
        assert headers.get("Cache-Control") == "no-store"
        assert headers.get("X-Content-Type-Options") == "nosniff"

        q24 = next(
            json.loads(line)
            for line in (SHCHEM_ROOT / SHS_PRODUCT_RELATIVE / "scan_records.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if Q24_MASTER_NODE_ID in line
        )
        answer_crop_id = q24["answer"]["visual_alignment_evidence"][0]["crop_id"]
        status, _, _ = _request(
            server,
            f"/api/v1/kb/workbench/master-direct-scans/{Q24_MASTER_NODE_ID}/question-crops/{answer_crop_id}",
        )
        assert status == 403
        assert _tree_snapshot(state_root) == before


def test_http_routes_preserve_fengxian_grouped_atomic_units_and_are_read_only(
    tmp_path: Path,
):
    state_root = tmp_path / "state"
    product_root = SHCHEM_ROOT / FENGXIAN_PRODUCT_RELATIVE
    product_before = _tree_snapshot(product_root)
    with running_server(build_config(state_root)) as server:
        status, detail_envelope, _ = _request(
            server,
            "/api/v1/kb/workbench/master-direct-scans/FX2025-EM-S2-Q2-P1",
        )
        assert status == 200
        detail = detail_envelope["data"]
        assert detail["product_id"] == FENGXIAN_PRODUCT_ID
        assert [
            unit["atomic_part_id"] for unit in detail["minimal_atomic_units"]
        ] == ["FX2025-EM-S2-Q2-P1", "FX2025-EM-S2-Q2-P2"]
        descriptor = next(
            item
            for item in detail["evidence_descriptors"]
            if item["evidence_role"] == "question"
        )
        status, png, _ = _request(
            server,
            "/api/v1/kb/workbench/master-direct-scans/"
            f"FX2025-EM-S2-Q2-P1/question-crops/{descriptor['crop_id']}",
        )
        assert status == 200
        assert hashlib.sha256(png).hexdigest() == descriptor["sha256"]
        status, _, _ = _request(
            server,
            "/api/v1/kb/workbench/master-direct-scans/"
            "FX2025-EM-S2-Q2-P1/question-crops/answer-p01-q2",
        )
        assert status == 403
        status, _, _ = _request(
            server, "/api/v1/kb/workbench/master-direct-scans/FX2025-EM-S2-Q2-P2"
        )
        assert status == 404
    assert _tree_snapshot(product_root) == product_before


def test_openapi_and_webui_bind_catalog_driven_direct_products_without_changing_native_filters():
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = contract["paths"]
    for path in (
        "/api/v1/kb/workbench/master-direct-scans/status",
        "/api/v1/kb/workbench/master-direct-scans/catalog",
        "/api/v1/kb/workbench/master-direct-scans/{master_node_id}",
        "/api/v1/kb/workbench/master-direct-scans/{master_node_id}/question-crops/{crop_id}",
    ):
        assert path in paths
    for schema_name in (
        "MasterDirectScanStatusEnvelope",
        "MasterDirectScanCatalogEnvelope",
        "MasterDirectScanDetailEnvelope",
        "MasterDirectScanEvidenceDescriptor",
        "MasterDirectScanAnswerBoundary",
    ):
        assert contract["components"]["schemas"][schema_name]["additionalProperties"] is False

    app = (OVERLAY / "app.js").read_text(encoding="utf-8")
    for marker in (
        "function validateMasterCandidateReviewDirectScanCatalog",
        "function validateCandidateReviewMasterDirectScanDetail",
        "function renderCandidateReviewMasterDirectPreview",
        "/api/v1/kb/workbench/master-direct-scans/catalog",
        "candidateReviewMasterDirectProductById",
        "candidateReviewMasterDirectProductUiDescriptor",
        'filter.startsWith("direct_product:")',
        "value.products",
        "direct_master_visual_scanned",
        "旧摄入候选（非主索引原生）",
        "答案页图片与答案裁片始终禁止",
    ):
        assert marker in app
    for product_identity in (
        EXPECTED_MANIFEST_SELF_SHA256,
        EXPECTED_MANIFEST_FILE_SHA256,
        SHS_EXPECTED_MANIFEST_SELF_SHA256,
        SHS_EXPECTED_MANIFEST_FILE_SHA256,
        CAOYANG_EXPECTED_MANIFEST_SELF_SHA256,
        CAOYANG_EXPECTED_MANIFEST_FILE_SHA256,
        PUDONG_EXPECTED_MANIFEST_SELF_SHA256,
        PUDONG_EXPECTED_MANIFEST_FILE_SHA256,
        JIADING_EXPECTED_MANIFEST_SELF_SHA256,
        JIADING_EXPECTED_MANIFEST_FILE_SHA256,
    ):
        assert product_identity not in app
    search_block = app[
        app.index("async function searchCandidateReview"):
        app.index("function resetCandidateReviewSelection")
    ]
    assert "candidateReviewMasterDirectScanByNode" not in search_block
    assert "candidateReviewMasterVisualScannedNodeIds" not in search_block
    assert "scan_classification" not in search_block


def test_openapi_strict_schemas_accept_aggregate_and_all_registered_detail_node_modes():
    contract = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    resolver = RefResolver.from_schema(contract)
    schemas = contract["components"]["schemas"]
    reader = MasterDirectVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    research_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"].startswith("RESEARCH2024-12-")
    )
    caoyang_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"].startswith("CAOYANG2-H2-")
    )
    recall_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"] == "SHCHEM-VSCAN-2026-LEVEL-RECALL-35-V1"
    )
    pudong_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"] == "SHCHEM-VSCAN-PUDONG-2026-FIRST-MOCK-THEME1-V1"
    )
    jiading_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"]
        == "question_visual_scan_jiading_2025_theme1_disinfectants_v1_2026-08-25"
    )
    huangpu_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"] == HUANGPU_PRODUCT_ID
    )
    qibao_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"] == QIBAO2025_OPENING_THEME4_CONFIG.product_id
    )
    hongkou_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"]
        == HONGKOU2026_SECOND_MOCK_THEME4_CONFIG.product_id
    )
    fengxian_node = next(
        item["master_node_id"]
        for item in catalog["items"]
        if item["product_id"] == FENGXIAN_PRODUCT_ID
        and item["minimal_atomic_unit_count"] == 2
    )
    payloads = (
        ("MasterDirectScanStatusEnvelope", reader.status()),
        ("MasterDirectScanCatalogEnvelope", catalog),
        ("MasterDirectScanDetailEnvelope", reader.detail(research_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(Q24_MASTER_NODE_ID)),
        ("MasterDirectScanDetailEnvelope", reader.detail(caoyang_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(recall_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(pudong_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(jiading_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(huangpu_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(qibao_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(hongkou_node)),
        ("MasterDirectScanDetailEnvelope", reader.detail(fengxian_node)),
    )
    for schema_name, data in payloads:
        envelope = {
            "contract_version": "shchem.gateway.v1",
            "request_id": "test-request",
            "data": data,
        }
        errors = sorted(
            Draft202012Validator(
                schemas[schema_name], resolver=resolver
            ).iter_errors(envelope),
            key=lambda issue: list(issue.absolute_path),
        )
        assert errors == []

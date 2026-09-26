from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import jsonschema
import pytest
import yaml

from integrations.deeptutor_shchem_v1 import question_visual_scan as visual_scan
from integrations.deeptutor_shchem_v1.config import Principal, token_digest
from integrations.deeptutor_shchem_v1.question_visual_scan import (
    EXPECTED_MANIFEST_SELF_SHA256,
    PRODUCT_RELATIVE,
    QuestionVisualScanError,
    QuestionVisualScanReader,
)
from staging.coordination.deeptutor_gateway.tests.test_gateway import (
    TOKEN_A,
    build_config,
    request,
    running_server,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"
PRODUCT = SHCHEM_ROOT / PRODUCT_RELATIVE
QP_SPEC = visual_scan.QP_BATCH_SPEC
QP_PRODUCT = SHCHEM_ROOT / QP_SPEC.product_relative
PT_SPEC = visual_scan.PT_BATCH_SPEC
PT_PRODUCT = SHCHEM_ROOT / PT_SPEC.product_relative
YP_SPEC = visual_scan.YP_BATCH_SPEC
YP_PRODUCT = SHCHEM_ROOT / YP_SPEC.product_relative
DT_SPEC = visual_scan.DT_BATCH_SPEC
DT_PRODUCT = SHCHEM_ROOT / DT_SPEC.product_relative
OPENAPI = (
    WORKSPACE
    / "staging/coordination/deeptutor_gateway/contracts/gateway_openapi_v1.yaml"
)
STUDENT_TOKEN = "visual-scan-student-token-0123456789"


def browser_headers(server) -> dict[str, str]:
    return {
        "Origin": f"http://127.0.0.1:{server.server_address[1]}",
        "Sec-Fetch-Site": "same-origin",
    }


def tree_snapshot(root: Path) -> dict[str, tuple[int, int, str]]:
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


def isolated_root(temp: Path) -> Path:
    root = temp / "sh-chem-db"
    target = root / PRODUCT_RELATIVE
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PRODUCT, target)
    source_manifest = json.loads(
        (PRODUCT / "source_manifest.json").read_text(encoding="utf-8")
    )
    for binding in source_manifest["source_bindings"]:
        relative = Path(binding["path"])
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SHCHEM_ROOT / relative, destination)
    return root


def isolated_catalog_root(temp: Path) -> Path:
    """Copy every frozen product and the exact union of their bound sources."""
    root = temp / "sh-chem-db"
    copied_sources: dict[Path, str] = {}
    for spec in visual_scan.BATCH_SPECS:
        product = SHCHEM_ROOT / spec.product_relative
        target = root / spec.product_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(product, target)
        source_manifest = json.loads(
            (product / "source_manifest.json").read_text(encoding="utf-8")
        )
        for binding in source_manifest["source_bindings"]:
            relative = Path(binding["path"])
            expected_sha256 = str(binding["sha256"])
            previous = copied_sources.get(relative)
            if previous is not None:
                assert previous == expected_sha256
                continue
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SHCHEM_ROOT / relative, destination)
            copied_sources[relative] = expected_sha256
    return root


def rebind_records(root: Path, mutator) -> str:
    product = root / PRODUCT_RELATIVE
    records_path = product / "scan_records.jsonl"
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mutator(records)
    raw = b"".join(
        json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
        for record in records
    )
    records_path.write_bytes(raw)
    manifest_path = product / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = next(
        item
        for item in manifest["output_bindings"]
        if item["path"] == "scan_records.jsonl"
    )
    descriptor["bytes"] = len(raw)
    descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["manifest_self_sha256"] = visual_scan._manifest_self_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(manifest["manifest_self_sha256"])


def rebind_batch_records(root: Path, spec, mutator) -> str:
    product = root / spec.product_relative
    records_path = product / "scan_records.jsonl"
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mutator(records)
    raw = b"".join(
        json.dumps(
            record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
        for record in records
    )
    records_path.write_bytes(raw)
    manifest_path = product / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = next(
        item
        for item in manifest["output_bindings"]
        if item["path"] == "scan_records.jsonl"
    )
    descriptor["bytes"] = len(raw)
    descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["manifest_self_sha256"] = visual_scan._manifest_self_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(manifest["manifest_self_sha256"])


def rebind_source_manifest(root: Path, mutator) -> str:
    product = root / PRODUCT_RELATIVE
    source_path = product / "source_manifest.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    mutator(source)
    raw = (json.dumps(source, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    source_path.write_bytes(raw)
    manifest_path = product / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = next(
        item
        for item in manifest["output_bindings"]
        if item["path"] == "source_manifest.json"
    )
    descriptor["bytes"] = len(raw)
    descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["source_manifest_binding"] = dict(descriptor)
    manifest["manifest_self_sha256"] = visual_scan._manifest_self_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(manifest["manifest_self_sha256"])


def rebind_batch_source_manifest(root: Path, spec, mutator) -> str:
    product = root / spec.product_relative
    source_path = product / "source_manifest.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    mutator(source)
    raw = (json.dumps(source, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    source_path.write_bytes(raw)
    manifest_path = product / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    descriptor = next(
        item
        for item in manifest["output_bindings"]
        if item["path"] == "source_manifest.json"
    )
    descriptor["bytes"] = len(raw)
    descriptor["sha256"] = hashlib.sha256(raw).hexdigest()
    manifest["source_manifest_binding"] = dict(descriptor)
    manifest["manifest_self_sha256"] = visual_scan._manifest_self_hash(manifest)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return str(manifest["manifest_self_sha256"])


def test_runtime_verified_status_has_exact_46_nodes_corrections_and_closed_gates():
    status = QuestionVisualScanReader(SHCHEM_ROOT).status()
    assert status["counts"] == {
        "expected_atomic_parts": 46,
        "scan_records": 46,
        "visual_scan_completed": 46,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 44,
        "unique_shared_crops_actually_viewed": 16,
        "answer_whole_pages_actually_viewed": 3,
        "compare_agree": 369,
        "compare_corrected": 45,
        "compare_blocked": 0,
        "corrected_nodes": 25,
    }
    assert len(status["node_ids"]) == len(set(status["node_ids"])) == 46
    assert [item["node_id"] for item in status["corrected_fields_by_node"]] == status[
        "node_ids"
    ]
    assert sum(bool(item["corrected_fields"]) for item in status["corrected_fields_by_node"]) == 25
    assert sum(len(item["corrected_fields"]) for item in status["corrected_fields_by_node"]) == 45
    assert status["integrity"] == {
        "manifest_self_sha256": EXPECTED_MANIFEST_SELF_SHA256,
        "hash_verified_on_read": True,
        "semantic_invariants_verified_on_read": True,
        "output_binding_count": 10,
        "source_binding_count": 122,
        "record_count": 46,
        "fail_closed": True,
    }
    assert status["authority"]["candidate_only"] is True
    assert status["authority"]["read_only"] is True
    assert all(
        value is False
        for key, value in status["authority"].items()
        if key not in {"candidate_only", "read_only"}
    )


def test_runtime_verified_catalog_has_five_hash_paired_batches_and_252_unique_nodes():
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    assert set(catalog) == {
        "scope",
        "counts",
        "batches",
        "node_ids",
        "authority",
        "integrity",
    }
    assert catalog["scope"] == "candidate_only_read_only_question_visual_scan"
    assert catalog["counts"] == {
        "batches": 5,
        "unique_nodes": 252,
        "visual_scan_completed": 252,
        "compare_agree": 1939,
        "compare_corrected": 329,
        "compare_blocked": 0,
        "corrected_nodes": 173,
    }
    assert len(catalog["node_ids"]) == len(set(catalog["node_ids"])) == 252
    assert catalog["node_ids"] == [
        node_id
        for batch in catalog["batches"]
        for node_id in batch["node_ids"]
    ]
    xh, qp, pt, yp, dt = catalog["batches"]
    assert (xh["product_id"], xh["paper_id"]) == (
        visual_scan.XH_BATCH_SPEC.product_id,
        visual_scan.XH_BATCH_SPEC.paper_id,
    )
    assert (qp["product_id"], qp["paper_id"]) == (
        QP_SPEC.product_id,
        QP_SPEC.paper_id,
    )
    assert (pt["product_id"], pt["paper_id"]) == (
        PT_SPEC.product_id,
        PT_SPEC.paper_id,
    )
    assert (yp["product_id"], yp["paper_id"]) == (
        YP_SPEC.product_id,
        YP_SPEC.paper_id,
    )
    assert (dt["product_id"], dt["paper_id"]) == (
        DT_SPEC.product_id,
        DT_SPEC.paper_id,
    )
    assert xh["integrity"] == {
        "manifest_self_sha256": EXPECTED_MANIFEST_SELF_SHA256,
        "hash_verified_on_read": True,
        "semantic_invariants_verified_on_read": True,
        "output_binding_count": 10,
        "source_binding_count": 122,
        "record_count": 46,
        "fail_closed": True,
    }
    assert qp["integrity"] == {
        "manifest_self_sha256": QP_SPEC.expected_manifest_self_sha256,
        "hash_verified_on_read": True,
        "semantic_invariants_verified_on_read": True,
        "output_binding_count": 10,
        "source_binding_count": 123,
        "record_count": 45,
        "fail_closed": True,
    }
    assert pt["integrity"] == {
        "manifest_self_sha256": PT_SPEC.expected_manifest_self_sha256,
        "hash_verified_on_read": True,
        "semantic_invariants_verified_on_read": True,
        "output_binding_count": 11,
        "source_binding_count": 142,
        "record_count": 52,
        "fail_closed": True,
    }
    assert yp["integrity"] == {
        "manifest_self_sha256": YP_SPEC.expected_manifest_self_sha256,
        "hash_verified_on_read": True,
        "semantic_invariants_verified_on_read": True,
        "output_binding_count": 11,
        "source_binding_count": 149,
        "record_count": 53,
        "fail_closed": True,
    }
    assert dt["integrity"] == {
        "manifest_self_sha256": DT_SPEC.expected_manifest_self_sha256,
        "hash_verified_on_read": True,
        "semantic_invariants_verified_on_read": True,
        "output_binding_count": 11,
        "source_binding_count": 83,
        "record_count": 56,
        "fail_closed": True,
    }
    assert xh["counts"]["compare_corrected"] == 45
    assert qp["counts"] == {
        "expected_atomic_parts": 45,
        "scan_records": 45,
        "visual_scan_completed": 45,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 46,
        "unique_shared_crops_actually_viewed": 16,
        "answer_whole_pages_actually_viewed": 2,
        "compare_agree": 363,
        "compare_corrected": 42,
        "compare_blocked": 0,
        "corrected_nodes": 24,
    }
    assert pt["counts"] == {
        "expected_atomic_parts": 52,
        "scan_records": 52,
        "visual_scan_completed": 52,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 52,
        "unique_shared_crops_actually_viewed": 26,
        "answer_whole_pages_actually_viewed": 2,
        "compare_agree": 371,
        "compare_corrected": 97,
        "compare_blocked": 0,
        "corrected_nodes": 43,
    }
    assert yp["counts"] == {
        "expected_themes": 5,
        "expected_printed_questions": 39,
        "expected_atomic_parts": 53,
        "scan_records": 53,
        "visual_scan_completed": 53,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 41,
        "unique_question_and_part_evidence_sha256_actually_viewed": 57,
        "unique_shared_crops_actually_viewed": 18,
        "answer_whole_pages_actually_viewed": 3,
        "compare_agree": 390,
        "compare_corrected": 87,
        "compare_blocked": 0,
        "corrected_nodes": 44,
    }
    assert dt["counts"] == {
        "expected_atomic_parts": 56,
        "scan_records": 56,
        "visual_scan_completed": 56,
        "blocked_pending_broader_crop": 0,
        "unique_question_crops_actually_viewed": 45,
        "unique_shared_crops_actually_viewed": 13,
        "answer_whole_pages_actually_viewed": 0,
        "compare_agree": 446,
        "compare_corrected": 58,
        "compare_blocked": 0,
        "corrected_nodes": 37,
    }
    assert len(qp["corrected_fields_by_node"]) == 45
    assert sum(
        bool(item["corrected_fields"])
        for item in qp["corrected_fields_by_node"]
    ) == 24
    assert sum(
        len(item["corrected_fields"])
        for item in qp["corrected_fields_by_node"]
    ) == 42
    assert len(pt["corrected_fields_by_node"]) == 52
    assert sum(
        bool(item["corrected_fields"])
        for item in pt["corrected_fields_by_node"]
    ) == 43
    assert sum(
        len(item["corrected_fields"])
        for item in pt["corrected_fields_by_node"]
    ) == 97
    assert sum(bool(item["corrected_fields"]) for item in yp["corrected_fields_by_node"]) == 44
    assert sum(len(item["corrected_fields"]) for item in yp["corrected_fields_by_node"]) == 87
    assert sum(bool(item["corrected_fields"]) for item in dt["corrected_fields_by_node"]) == 37
    assert sum(len(item["corrected_fields"]) for item in dt["corrected_fields_by_node"]) == 58
    reference_catalog = [
        item
        for batch in catalog["batches"]
        for item in batch["reference_answer_by_node"]
    ]
    assert len(reference_catalog) == 252
    assert Counter(item["availability"] for item in reference_catalog) == {
        "present_part_aligned": 181,
        "present_unaligned": 15,
        "absent": 56,
    }
    assert Counter(item["source_authority"] for item in reference_catalog) == {
        "nonofficial_reference": 196,
        "none": 56,
    }
    assert all(
        set(item)
        == {"node_id", "availability", "source_authority", "has_quality_note"}
        for item in reference_catalog
    )
    assert any(item["has_quality_note"] for item in reference_catalog)
    assert any(not item["has_quality_note"] for item in reference_catalog)
    assert catalog["authority"] == xh["authority"] == qp["authority"] == pt["authority"] == yp["authority"] == dt["authority"]
    assert catalog["integrity"] == {
        "batch_count": 5,
        "unique_node_count": 252,
        "all_batches_hash_verified_on_read": True,
        "all_batches_semantic_invariants_verified_on_read": True,
        "fail_closed": True,
    }
    serialized = json.dumps(catalog, ensure_ascii=False).casefold()
    for forbidden in (
        "source_bindings",
        "source_manifest",
        "rule_paths",
        "answer_evidence",
        "reference_summary",
        "visual_alignment",
        "article-image",
        "image_endpoint",
        "question-crops",
        "c:\\users",
        "file://",
        "kb/",
        "staging/",
        ".png",
        ".jpg",
    ):
        assert forbidden not in serialized


def test_detail_dispatches_exactly_across_five_batches_without_answer_or_path_leakage():
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    for batch in catalog["batches"]:
        node_id = batch["node_ids"][0]
        detail = reader.detail(node_id)
        assert detail["node_id"] == node_id
        assert detail["product_id"] == batch["product_id"]
        assert detail["paper_id"] == batch["paper_id"]
        assert detail["integrity"] == batch["integrity"]
        expected_availability, expected_authority = {
            PT_SPEC.paper_id: ("present_unaligned", "nonofficial_reference"),
            DT_SPEC.paper_id: ("absent", "none"),
        }.get(
            batch["paper_id"],
            ("present_part_aligned", "nonofficial_reference"),
        )
        assert detail["answer_boundary"] == {
            "availability": expected_availability,
            "authority": expected_authority,
            "verified": False,
        }
        reference_answer = detail["reference_answer"]
        assert reference_answer["availability"] == expected_availability
        assert reference_answer["source_authority"] == expected_authority
        assert reference_answer["independently_verified"] is False
        assert (reference_answer["reference_answer_text"] is not None) == (
            expected_availability == "present_part_aligned"
        )
        assert {item["evidence_role"] for item in detail["evidence_descriptors"]} <= {
            "question",
            "shared_material",
        }
        serialized = json.dumps(detail, ensure_ascii=False).casefold()
        for forbidden in (
            "answer_evidence",
            "visual_alignment",
            "reference_summary",
            "source_bindings",
            "source_manifest",
            "rule_paths",
            "whole_page",
            "article-image",
            "image_endpoint",
            "question-crops",
            "c:\\users",
            "file://",
            "kb/",
            "staging/",
            ".png",
            ".jpg",
        ):
            assert forbidden not in serialized


def test_reference_answer_projection_is_exact_for_all_252_and_never_catalogs_text():
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    metadata = {
        item["node_id"]: item
        for batch in catalog["batches"]
        for item in batch["reference_answer_by_node"]
    }
    assert len(metadata) == 252
    validated = reader._validated_catalog()
    aligned = unaligned = absent = 0
    for batch_reader, snapshot in validated:
        for record in snapshot.records:
            node_id = record["hierarchy"]["atomic_part_id"]
            source_answer = record["answer"]
            projection = batch_reader.detail(
                node_id, snapshot=snapshot
            )["reference_answer"]
            availability = source_answer["availability"]
            if availability == "present_part_aligned":
                aligned += 1
                assert projection["reference_answer_text"] == source_answer[
                    "reference_summary_zh"
                ]
            elif availability == "present_unaligned":
                unaligned += 1
                assert projection["reference_answer_text"] is None
            else:
                absent += 1
                assert availability == "absent"
                assert projection["reference_answer_text"] is None
                assert projection["source_authority"] == "none"
            assert projection["independently_verified"] is False
            explicit_doubt = bool(
                record["risks_and_limits"][
                    "ambiguity_or_multiple_solutions_zh"
                ]
            )
            assert metadata[node_id] == {
                "node_id": node_id,
                "availability": availability,
                "source_authority": projection["source_authority"],
                "has_quality_note": explicit_doubt,
            }
    assert (aligned, unaligned, absent) == (181, 15, 56)
    serialized_catalog = json.dumps(catalog, ensure_ascii=False)
    assert "reference_answer_text" not in serialized_catalog
    assert "reference_summary_zh" not in serialized_catalog


def test_pt_frozen_rules_pixel_counts_answer_split_and_manual_dependency_are_exact():
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    catalog = reader.catalog()
    pt = catalog["batches"][2]
    assert pt["product_id"] == PT_SPEC.product_id
    assert pt["paper_id"] == "W1-PT2026-EM"

    source_manifest = json.loads(
        (PT_PRODUCT / "source_manifest.json").read_text(encoding="utf-8")
    )
    rule_ids = [
        item["rule_id"] for item in source_manifest["rule_contract_bindings"]
    ]
    assert rule_ids == list(PT_SPEC.rule_paths)
    assert len(rule_ids) == 6
    assert all("/profiles/" not in rule_id.casefold() for rule_id in rule_ids)
    assert all("putuo-ermo" not in rule_id.casefold() for rule_id in rule_ids)

    rows = [
        json.loads(line)
        for line in (PT_PRODUCT / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    question_evidence = [
        evidence
        for row in rows
        for evidence in row["viewed_evidence"]
        if evidence["evidence_role"] == "question"
    ]
    assert len({item["crop_id"] for item in question_evidence}) == 53
    assert len({item["sha256"] for item in question_evidence}) == 52
    assert sum(
        row["answer"]["availability"] == "present_part_aligned"
        for row in rows
    ) == 41
    assert sum(
        row["answer"]["availability"] == "present_unaligned"
        for row in rows
    ) == 11

    node_id = "W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S03"
    detail = reader.detail(node_id)
    assert detail["hierarchy"]["paper_id"] == PT_SPEC.paper_id
    assert detail["dependency"]["prior_atomic_part_ids"] == [
        "W1-PT2026-EM-AP-PT2026-T4-Q08-P01-S01"
    ]


def test_yp_frozen_rules_pixel_counts_answer_split_and_dependencies_are_exact():
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    source_manifest = json.loads(
        (YP_PRODUCT / "source_manifest.json").read_text(encoding="utf-8")
    )
    assert source_manifest["paper_id"] == YP_SPEC.paper_id
    assert source_manifest["visual_inspection_declaration"] == {
        "whole_question_crops_actually_viewed": 41,
        "question_and_part_evidence_sha256_actually_viewed": 57,
        "shared_crops_actually_viewed": 18,
        "answer_whole_pages_actually_viewed": 3,
        "metadata_only_scan_forbidden": True,
        "ocr_is_authority": False,
    }
    rule_ids = [
        item["rule_id"] for item in source_manifest["rule_contract_bindings"]
    ]
    assert rule_ids == list(YP_SPEC.rule_paths)
    assert len(rule_ids) == 6
    assert all("/profiles/" not in rule_id.casefold() for rule_id in rule_ids)
    rows = [
        json.loads(line)
        for line in (YP_PRODUCT / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    question_evidence = [
        evidence
        for row in rows
        for evidence in row["viewed_evidence"]
        if evidence["evidence_role"] == "question"
    ]
    assert len({item["crop_id"] for item in question_evidence}) == 63
    assert len({item["sha256"] for item in question_evidence}) == 57
    assert sum(row["answer"]["availability"] == "present_part_aligned" for row in rows) == 49
    assert sum(row["answer"]["availability"] == "present_unaligned" for row in rows) == 4
    detail = reader.detail("W1-YP2026-EM-AP-YP2026-T4-Q05-P02-S02")
    assert detail["dependency"]["prior_atomic_part_ids"] == [
        "W1-YP2026-EM-AP-YP2026-T4-Q05-P02-S01"
    ]


def test_dt_school_title_only_identity_parent_context_and_absent_answers_are_exact():
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    source_manifest = json.loads(
        (DT_PRODUCT / "source_manifest.json").read_text(encoding="utf-8")
    )
    assert source_manifest["paper_id"] == DT_SPEC.paper_id
    assert source_manifest["source_identity"]["paper_family"] == "midterm"
    assert source_manifest["source_identity"]["structure_model"] == "other_observed"
    assert source_manifest["paper_identity_boundary"] == visual_scan.DT_PAPER_IDENTITY_BOUNDARY
    assert source_manifest["paper_identity_boundary"]["school_attribution_basis"] == (
        "wechat_article_title_only_not_visible_on_paper_face"
    )
    assert source_manifest["paper_identity_boundary"]["district"] == "unknown"
    assert source_manifest["answer_whole_pages"] == []
    assert len(source_manifest["parent_question_visual_evidence"]) == 1
    assert source_manifest["parent_question_visual_evidence"][0]["crop_id"] == (
        "DT2025-H1-Q33-E1"
    )
    rule_ids = [
        item["rule_id"] for item in source_manifest["rule_contract_bindings"]
    ]
    assert rule_ids == list(DT_SPEC.rule_paths)
    assert len(rule_ids) == 6
    assert all("/profiles/" not in rule_id.casefold() for rule_id in rule_ids)
    rows = [
        json.loads(line)
        for line in (DT_PRODUCT / "scan_records.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert len(rows) == 56
    assert all(row["answer"]["availability"] == "absent" for row in rows)
    assert all(row["answer"]["authority"] == "none" for row in rows)
    assert all(row["answer"]["visual_alignment_evidence"] == [] for row in rows)
    detail = reader.detail("W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S03")
    assert detail["answer_boundary"] == {
        "availability": "absent",
        "authority": "none",
        "verified": False,
    }
    assert detail["reference_answer"]["reference_answer_text"] is None
    assert detail["reference_answer"]["source_authority"] == "none"
    assert detail["reference_answer"]["independently_verified"] is False
    assert detail["dependency"]["prior_atomic_part_ids"] == [
        "W1-DT2025-H1-MID-AP-DT2025-H1-Q38-P01-S02"
    ]


def test_detail_is_chinese_candidate_analysis_with_safe_question_only_evidence():
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    node_id = reader.status()["node_ids"][0]
    detail = reader.detail(node_id)
    assert set(detail) == {
        "product_id",
        "scope",
        "paper_id",
        "node_id",
        "scan_status",
        "hierarchy",
        "visible_summary_zh",
        "response_requirement_zh",
        "dependency",
        "scan_classification",
        "cognitive_difficulty",
        "chemistry_observations",
        "model_candidate_analysis",
        "risks_and_limits",
        "comparison_with_wave1",
        "evidence_descriptors",
        "answer_boundary",
        "reference_answer",
        "authority",
        "integrity",
    }
    assert detail["model_candidate_analysis"]["review_state_zh"] == "待复核候选分析"
    assert detail["model_candidate_analysis"]["candidate_only"] is True
    assert detail["model_candidate_analysis"]["correctness_verified"] is False
    assert any(
        "③" in step
        for step in detail["model_candidate_analysis"]["solution_path_zh"]
    )
    assert detail["answer_boundary"] == {
        "availability": "present_part_aligned",
        "authority": "nonofficial_reference",
        "verified": False,
    }
    raw_record = json.loads(
        (PRODUCT / "scan_records.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert detail["reference_answer"] == {
        "availability": "present_part_aligned",
        "reference_answer_text": raw_record["answer"]["reference_summary_zh"],
        "source_authority": "nonofficial_reference",
        "independently_verified": False,
        "quality_note": None,
    }
    assert detail["cognitive_difficulty"]["is_measured"] is False
    assert detail["cognitive_difficulty"]["measured_difficulty"] is None
    assert len(detail["cognitive_difficulty"]["factors"]) == 10
    assert {item["evidence_role"] for item in detail["evidence_descriptors"]} <= {
        "question",
        "shared_material",
    }
    assert all(
        set(item)
        == {
            "crop_id",
            "evidence_role",
            "source_page",
            "sha256",
            "bytes",
            "width",
            "height",
            "visual_inspection_status",
        }
        for item in detail["evidence_descriptors"]
    )
    serialized = json.dumps(detail, ensure_ascii=False).casefold()
    for forbidden in (
        "crop_path",
        "original_source_path",
        "whole_page_path",
        "source_package_path",
        "reference_summary_zh",
        "visual_alignment_evidence",
        "image_endpoint",
        "question-crops",
        "c:\\users",
        "file://",
        "kb/",
        "staging/",
        ".png",
    ):
        assert forbidden not in serialized


def test_http_requires_bearer_teacher_and_trusted_loopback_origin(tmp_path):
    config = build_config(tmp_path / "state")
    config.principals.append(
        Principal("visual-scan-student", "student", token_digest(STUDENT_TOKEN))
    )
    config.validate()
    with running_server(config) as server:
        for route, count_key, expected in (
            (
                "/api/v1/kb/workbench/question-visual-scans/status",
                "scan_records",
                46,
            ),
            (
                "/api/v1/kb/workbench/question-visual-scans/catalog",
                "unique_nodes",
                252,
            ),
        ):
            status, _, _ = request(
                server, "GET", route, token=None, headers=browser_headers(server)
            )
            assert status == 401
            status, payload, _ = request(
                server,
                "GET",
                route,
                token=STUDENT_TOKEN,
                headers=browser_headers(server),
            )
            assert status == 403
            assert payload["error"]["code"] == "teacher_scope_required"
            status, payload, _ = request(
                server,
                "GET",
                route,
                token=TOKEN_A,
                headers={
                    "Origin": "https://evil.example",
                    "Sec-Fetch-Site": "cross-site",
                },
            )
            assert status == 403
            assert payload["error"]["code"] == "origin_denied"
            status, payload, _ = request(
                server,
                "GET",
                route,
                token=TOKEN_A,
                headers=browser_headers(server),
            )
            assert status == 200
            assert payload["data"]["counts"][count_key] == expected


def test_http_status_detail_unknown_invalid_query_and_zero_write(tmp_path):
    state_root = tmp_path / "state"
    config = build_config(state_root)
    with running_server(config) as server:
        headers = browser_headers(server)
        state_before = tree_snapshot(state_root)
        product_before = tree_snapshot(PRODUCT)
        qp_product_before = tree_snapshot(QP_PRODUCT)
        pt_product_before = tree_snapshot(PT_PRODUCT)
        yp_product_before = tree_snapshot(YP_PRODUCT)
        dt_product_before = tree_snapshot(DT_PRODUCT)
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/question-visual-scans/status",
            headers=headers,
        )
        assert status == 200
        xh_node_id = payload["data"]["node_ids"][0]
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/question-visual-scans/catalog",
            headers=headers,
        )
        assert status == 200
        assert payload["data"]["counts"]["unique_nodes"] == 252
        assert len(payload["data"]["batches"]) == 5
        qp_node_id = payload["data"]["batches"][1]["node_ids"][0]
        pt_node_id = payload["data"]["batches"][2]["node_ids"][0]
        yp_node_id = payload["data"]["batches"][3]["node_ids"][0]
        dt_node_id = payload["data"]["batches"][4]["node_ids"][0]
        for node_id, paper_id in (
            (xh_node_id, "W1-XH2026-EM"),
            (qp_node_id, "W1-QP2026-EM"),
            (pt_node_id, "W1-PT2026-EM"),
            (yp_node_id, "W1-YP2026-EM"),
            (dt_node_id, "W1-DT2025-H1-MID"),
        ):
            status, detail_payload, _ = request(
                server,
                "GET",
                f"/api/v1/kb/workbench/question-visual-scans/{node_id}",
                headers=headers,
            )
            assert status == 200
            assert detail_payload["data"]["node_id"] == node_id
            assert detail_payload["data"]["paper_id"] == paper_id
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/question-visual-scans/unknown-safe-node",
            headers=headers,
        )
        assert status == 404
        assert payload["error"]["code"] == "question_visual_scan_node_not_found"
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/question-visual-scans/..",
            headers=headers,
        )
        assert status == 400
        assert payload["error"]["code"] == "question_visual_scan_invalid_node_id"
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/question-visual-scans/status?x=1",
            headers=headers,
        )
        assert status == 400
        assert payload["error"]["code"] == "question_visual_scan_query_unsupported"
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/question-visual-scans/catalog?x=1",
            headers=headers,
        )
        assert status == 400
        assert payload["error"]["code"] == "question_visual_scan_query_unsupported"
        for route in (
            "/api/v1/kb/workbench/question-visual-scans/status",
            "/api/v1/kb/workbench/question-visual-scans/catalog",
            f"/api/v1/kb/workbench/question-visual-scans/{qp_node_id}",
            f"/api/v1/kb/workbench/question-visual-scans/{pt_node_id}",
            f"/api/v1/kb/workbench/question-visual-scans/{yp_node_id}",
            f"/api/v1/kb/workbench/question-visual-scans/{dt_node_id}",
        ):
            for suffix in ("?x=", "?x"):
                status, payload, _ = request(
                    server,
                    "GET",
                    route + suffix,
                    headers=headers,
                )
                assert status == 400
                assert (
                    payload["error"]["code"]
                    == "question_visual_scan_query_unsupported"
                )
        assert tree_snapshot(state_root) == state_before
        assert tree_snapshot(PRODUCT) == product_before
        assert tree_snapshot(QP_PRODUCT) == qp_product_before
        assert tree_snapshot(PT_PRODUCT) == pt_product_before
        assert tree_snapshot(YP_PRODUCT) == yp_product_before
        assert tree_snapshot(DT_PRODUCT) == dt_product_before


def test_bound_source_hash_mutation_fails_the_new_http_api_closed(tmp_path):
    root = isolated_root(tmp_path)
    source_manifest = json.loads(
        (root / PRODUCT_RELATIVE / "source_manifest.json").read_text(encoding="utf-8")
    )
    relative = source_manifest["source_bindings"][0]["path"]
    source = root / relative
    source.write_bytes(source.read_bytes() + b"\n")
    config = build_config(tmp_path / "state")
    config.shchem_root = root
    config.validate()
    with running_server(config) as server:
        status, payload, _ = request(
            server,
            "GET",
            "/api/v1/kb/workbench/question-visual-scans/status",
            headers=browser_headers(server),
        )
        assert status == 409
        assert payload["error"]["code"] == "question_visual_scan_hash_mismatch"


@pytest.mark.parametrize(
    "mutator, expected_code",
    [
        (
            lambda rows: rows[0]["authority_gates"].__setitem__(
                "human_reviewed", True
            ),
            "question_visual_scan_gate_elevated",
        ),
        (
            lambda rows: rows[0]["hierarchy"].__setitem__("theme_sequence", 99),
            "question_visual_scan_record_invalid",
        ),
        (
            lambda rows: rows[0]["comparison_with_wave1"]["item_type"].__setitem__(
                "old", ["forged"]
            ),
            "question_visual_scan_comparison_invalid",
        ),
        (
            lambda rows: rows[0]["answer"].__setitem__(
                "visual_alignment_evidence", []
            ),
            "question_visual_scan_answer_boundary_invalid",
        ),
    ],
)
def test_rehashed_semantic_mutations_still_fail_closed(
    tmp_path, monkeypatch, mutator, expected_code
):
    root = isolated_root(tmp_path)
    new_self_hash = rebind_records(root, mutator)
    monkeypatch.setattr(
        visual_scan, "EXPECTED_MANIFEST_SELF_SHA256", new_self_hash
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).status()
    assert error.value.code == expected_code


def test_rehashed_source_manifest_removal_still_fails_closed(
    tmp_path, monkeypatch
):
    root = isolated_root(tmp_path)

    def remove_answer_page(source):
        source["source_bindings"] = [
            item
            for item in source["source_bindings"]
            if not item["path"].endswith("article-image-10.png")
        ]

    new_self_hash = rebind_source_manifest(root, remove_answer_page)
    monkeypatch.setattr(
        visual_scan, "EXPECTED_MANIFEST_SELF_SHA256", new_self_hash
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).status()
    assert error.value.code == "question_visual_scan_binding_invalid"


def test_output_binding_drift_fails_before_projection(tmp_path):
    root = isolated_root(tmp_path)
    coverage = root / PRODUCT_RELATIVE / "coverage_report.json"
    coverage.write_bytes(coverage.read_bytes() + b"\n")
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).status()
    assert error.value.code == "question_visual_scan_hash_mismatch"


@pytest.mark.parametrize(
    "spec,node_id",
    [
        (QP_SPEC, "W1-QP2026-EM-AP-QP2026-EM-T1-P01"),
        (PT_SPEC, "W1-PT2026-EM-AP-PT2026-T1-Q01-P01-S01"),
    ],
)
def test_nonlegacy_batch_output_drift_disables_catalog_and_detail_http_closed(
    tmp_path, spec, node_id
):
    root = isolated_catalog_root(tmp_path)
    coverage = root / spec.product_relative / "coverage_report.json"
    coverage.write_bytes(coverage.read_bytes() + b"\n")
    config = build_config(tmp_path / "state")
    config.shchem_root = root
    config.validate()
    with running_server(config) as server:
        for route in (
            "/api/v1/kb/workbench/question-visual-scans/catalog",
            f"/api/v1/kb/workbench/question-visual-scans/{node_id}",
        ):
            status, payload, _ = request(
                server,
                "GET",
                route,
                headers=browser_headers(server),
                timeout=30,
            )
            assert status == 409
            assert payload["error"]["code"] == "question_visual_scan_hash_mismatch"


@pytest.mark.parametrize("spec", [YP_SPEC, DT_SPEC])
def test_late_batch_output_drift_disables_catalog_directly(tmp_path, spec):
    root = isolated_catalog_root(tmp_path)
    coverage = root / spec.product_relative / "coverage_report.json"
    coverage.write_bytes(coverage.read_bytes() + b"\n")
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).catalog()
    assert error.value.code == "question_visual_scan_hash_mismatch"


def test_rehashed_qp_gate_elevation_still_disables_entire_catalog(
    tmp_path, monkeypatch
):
    root = isolated_catalog_root(tmp_path)
    new_self_hash = rebind_batch_records(
        root,
        QP_SPEC,
        lambda rows: rows[0]["authority_gates"].__setitem__(
            "publication_allowed", True
        ),
    )
    monkeypatch.setattr(
        visual_scan,
        "BATCH_SPECS",
        (
            visual_scan.XH_BATCH_SPEC,
            replace(QP_SPEC, expected_manifest_self_sha256=new_self_hash),
            PT_SPEC,
            YP_SPEC,
            DT_SPEC,
        ),
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).catalog()
    assert error.value.code == "question_visual_scan_gate_elevated"


def test_rehashed_yp_wrong_answer_page_alignment_fails_closed(
    tmp_path, monkeypatch
):
    root = isolated_catalog_root(tmp_path)

    def forge_answer_page(rows):
        rows[0]["answer"]["visual_alignment_evidence"][0]["source_page"] = 999

    new_self_hash = rebind_batch_records(root, YP_SPEC, forge_answer_page)
    replacement = replace(YP_SPEC, expected_manifest_self_sha256=new_self_hash)
    monkeypatch.setattr(
        visual_scan,
        "BATCH_SPECS",
        tuple(
            replacement if spec.paper_id == YP_SPEC.paper_id else spec
            for spec in visual_scan.BATCH_SPECS
        ),
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).catalog()
    assert error.value.code == "question_visual_scan_answer_boundary_invalid"


def test_rehashed_yp_specific_profile_injection_fails_closed(
    tmp_path, monkeypatch
):
    root = isolated_catalog_root(tmp_path)

    def inject_profile(source):
        source["rule_contract_bindings"][0]["rule_id"] = (
            "kb/shanghai_observed_standard_v1/profiles/forged.json"
        )

    new_self_hash = rebind_batch_source_manifest(root, YP_SPEC, inject_profile)
    replacement = replace(YP_SPEC, expected_manifest_self_sha256=new_self_hash)
    monkeypatch.setattr(
        visual_scan,
        "BATCH_SPECS",
        tuple(
            replacement if spec.paper_id == YP_SPEC.paper_id else spec
            for spec in visual_scan.BATCH_SPECS
        ),
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).catalog()
    assert error.value.code == "question_visual_scan_rule_invalid"


def test_rehashed_dt_fake_answer_fails_closed(tmp_path, monkeypatch):
    root = isolated_catalog_root(tmp_path)

    def inject_answer(rows):
        rows[0]["answer"]["availability"] = "present_part_aligned"
        rows[0]["answer"]["authority"] = "nonofficial_reference"

    new_self_hash = rebind_batch_records(root, DT_SPEC, inject_answer)
    replacement = replace(DT_SPEC, expected_manifest_self_sha256=new_self_hash)
    monkeypatch.setattr(
        visual_scan,
        "BATCH_SPECS",
        tuple(
            replacement if spec.paper_id == DT_SPEC.paper_id else spec
            for spec in visual_scan.BATCH_SPECS
        ),
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).catalog()
    assert error.value.code == "question_visual_scan_answer_boundary_invalid"


def test_rehashed_dt_fake_paper_face_school_attribution_fails_closed(
    tmp_path, monkeypatch
):
    root = isolated_catalog_root(tmp_path)

    def promote_school_claim(source):
        source["paper_identity_boundary"]["school_attribution_basis"] = (
            "visible_on_paper_face"
        )

    new_self_hash = rebind_batch_source_manifest(root, DT_SPEC, promote_school_claim)
    replacement = replace(DT_SPEC, expected_manifest_self_sha256=new_self_hash)
    monkeypatch.setattr(
        visual_scan,
        "BATCH_SPECS",
        tuple(
            replacement if spec.paper_id == DT_SPEC.paper_id else spec
            for spec in visual_scan.BATCH_SPECS
        ),
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(root).catalog()
    assert error.value.code == "question_visual_scan_source_invalid"


def test_duplicate_node_ownership_across_frozen_batches_fails_closed(monkeypatch):
    monkeypatch.setattr(
        visual_scan,
        "BATCH_SPECS",
        (
            visual_scan.XH_BATCH_SPEC,
            visual_scan.XH_BATCH_SPEC,
            QP_SPEC,
            PT_SPEC,
            YP_SPEC,
            DT_SPEC,
        ),
    )
    with pytest.raises(QuestionVisualScanError) as error:
        QuestionVisualScanReader(SHCHEM_ROOT).catalog()
    assert error.value.code == "question_visual_scan_node_conflict"


@pytest.mark.parametrize(
    "value",
    [
        r"C:\Users\teacher\answer.png",
        r"\\server\share\answer.png",
        "file:///C:/Users/teacher/answer.png",
        "C%3A%5CUsers%5Cteacher%5Canswer.png",
        "C%253A%255CUsers%255Cteacher%255Canswer.png",
        quote(quote(quote(quote(r"C:\Users\teacher\answer.png", safe=""), safe=""), safe=""), safe=""),
        quote(quote(quote(quote(quote(quote(quote(quote(quote(r"C:\Users\teacher\answer.png", safe=""), safe=""), safe=""), safe=""), safe=""), safe=""), safe=""), safe=""), safe=""),
        "kb/formal/private.json",
        "staging/private.json",
        "见(kb/private.json)",
        "路径：staging/private.json",
        "配置=runtime/private.json",
        "工程(sh-chem-db/private.json)",
    ],
)
def test_projection_path_guard_rejects_literal_and_encoded_local_paths(value):
    with pytest.raises(QuestionVisualScanError) as error:
        visual_scan._reject_unsafe_projection({"visible_summary_zh": value})
    assert error.value.code == "question_visual_scan_projection_unsafe"


@pytest.mark.parametrize("value", ["Kb/Kw", "K=Ka₁·Kb/Kw"])
def test_projection_path_guard_does_not_confuse_equilibrium_constants_with_paths(
    value,
):
    visual_scan._reject_unsafe_projection({"visible_summary_zh": value})


@pytest.mark.parametrize(
    "value",
    [
        {"risks_and_limits": {"reference_summary_zh": "not safe"}},
        {
            "chemistry_observations": {
                "visual_alignment_evidence": [{"answer_page": "internal"}]
            }
        },
        {"source_manifest": {"source_bindings": []}},
        {"answer": {"exact_image_evidence": []}},
    ],
)
def test_projection_guard_rejects_source_and_answer_internal_fields(value):
    with pytest.raises(QuestionVisualScanError) as error:
        visual_scan._reject_unsafe_projection(value)
    assert error.value.code == "question_visual_scan_projection_unsafe"


def test_openapi_1_18_publishes_five_strict_hash_paired_read_only_routes():
    text = OPENAPI.read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    assert document["openapi"] == "3.1.0"
    assert document["info"]["version"] == "1.24.0"
    assert "/api/v1/kb/workbench/question-visual-scans/status:" in text
    assert "/api/v1/kb/workbench/question-visual-scans/catalog:" in text
    assert "/api/v1/kb/workbench/question-visual-scans/{node_id}:" in text
    assert "QuestionVisualScanStatusEnvelope" in text
    assert "QuestionVisualScanCatalogEnvelope" in text
    assert "QuestionVisualScanDetailEnvelope" in text
    assert "answer_boundary" in text
    assert "upstream nonofficial answer/reference summary" in text
    schemas = document["components"]["schemas"]
    catalog_data = schemas["QuestionVisualScanCatalogEnvelope"]["properties"][
        "data"
    ]
    assert catalog_data["properties"]["scope"]["const"] == (
        "candidate_only_read_only_question_visual_scan"
    )
    assert catalog_data["properties"]["batches"]["prefixItems"] == [
        {"$ref": "#/components/schemas/QuestionVisualScanXHBatchCatalog"},
        {"$ref": "#/components/schemas/QuestionVisualScanQPBatchCatalog"},
        {"$ref": "#/components/schemas/QuestionVisualScanPTBatchCatalog"},
        {"$ref": "#/components/schemas/QuestionVisualScanYPBatchCatalog"},
        {"$ref": "#/components/schemas/QuestionVisualScanDTBatchCatalog"},
    ]
    xh_batch = schemas["QuestionVisualScanXHBatchCatalog"]["properties"]
    qp_batch = schemas["QuestionVisualScanQPBatchCatalog"]["properties"]
    pt_batch = schemas["QuestionVisualScanPTBatchCatalog"]["properties"]
    yp_batch = schemas["QuestionVisualScanYPBatchCatalog"]["properties"]
    dt_batch = schemas["QuestionVisualScanDTBatchCatalog"]["properties"]
    assert (xh_batch["product_id"]["const"], xh_batch["paper_id"]["const"]) == (
        visual_scan.XH_BATCH_SPEC.product_id,
        visual_scan.XH_BATCH_SPEC.paper_id,
    )
    assert (qp_batch["product_id"]["const"], qp_batch["paper_id"]["const"]) == (
        QP_SPEC.product_id,
        QP_SPEC.paper_id,
    )
    assert (pt_batch["product_id"]["const"], pt_batch["paper_id"]["const"]) == (
        PT_SPEC.product_id,
        PT_SPEC.paper_id,
    )
    assert (yp_batch["product_id"]["const"], yp_batch["paper_id"]["const"]) == (
        YP_SPEC.product_id,
        YP_SPEC.paper_id,
    )
    assert (dt_batch["product_id"]["const"], dt_batch["paper_id"]["const"]) == (
        DT_SPEC.product_id,
        DT_SPEC.paper_id,
    )
    assert qp_batch["integrity"]["$ref"].endswith(
        "/QuestionVisualScanQPIntegrity"
    )
    assert schemas["QuestionVisualScanQPIntegrity"]["properties"][
        "manifest_self_sha256"
    ]["const"] == QP_SPEC.expected_manifest_self_sha256
    assert pt_batch["integrity"]["$ref"].endswith(
        "/QuestionVisualScanPTIntegrity"
    )
    assert schemas["QuestionVisualScanPTIntegrity"]["properties"][
        "manifest_self_sha256"
    ]["const"] == PT_SPEC.expected_manifest_self_sha256
    assert schemas["QuestionVisualScanYPIntegrity"]["properties"][
        "manifest_self_sha256"
    ]["const"] == YP_SPEC.expected_manifest_self_sha256
    assert schemas["QuestionVisualScanDTIntegrity"]["properties"][
        "manifest_self_sha256"
    ]["const"] == DT_SPEC.expected_manifest_self_sha256
    detail_data = schemas["QuestionVisualScanDetailEnvelope"]["properties"][
        "data"
    ]
    assert [
        (
            branch["properties"]["product_id"]["const"],
            branch["properties"]["paper_id"]["const"],
            branch["properties"]["integrity"]["$ref"],
        )
        for branch in detail_data["oneOf"]
    ] == [
        (
            visual_scan.XH_BATCH_SPEC.product_id,
            visual_scan.XH_BATCH_SPEC.paper_id,
            "#/components/schemas/QuestionVisualScanIntegrity",
        ),
        (
            QP_SPEC.product_id,
            QP_SPEC.paper_id,
            "#/components/schemas/QuestionVisualScanQPIntegrity",
        ),
        (
            PT_SPEC.product_id,
            PT_SPEC.paper_id,
            "#/components/schemas/QuestionVisualScanPTIntegrity",
        ),
        (
            YP_SPEC.product_id,
            YP_SPEC.paper_id,
            "#/components/schemas/QuestionVisualScanYPIntegrity",
        ),
        (
            DT_SPEC.product_id,
            DT_SPEC.paper_id,
            "#/components/schemas/QuestionVisualScanDTIntegrity",
        ),
    ]
    assert schemas["QuestionVisualScanHierarchy"]["properties"]["paper_id"][
        "enum"
    ] == ["W1-XH2026-EM", "W1-QP2026-EM"]
    assert schemas["QuestionVisualScanPTHierarchy"]["properties"]["paper_id"][
        "const"
    ] == "W1-PT2026-EM"
    assert schemas["QuestionVisualScanYPHierarchy"]["properties"]["paper_id"][
        "const"
    ] == "W1-YP2026-EM"
    assert schemas["QuestionVisualScanDTHierarchy"]["properties"]["paper_id"][
        "const"
    ] == "W1-DT2025-H1-MID"
    pattern = document["components"]["schemas"]["QuestionVisualScanNodeId"]["pattern"]
    assert pattern == r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"
    assert re.fullmatch(pattern, "W1-XH2026-EM-AP-XH2026-EM-T1-Q01")
    assert re.fullmatch(pattern, "safe:colon") is None
    assert re.fullmatch(pattern, "unsafe..node") is None
    assert "pixel_reuse_allowed: { const: false }" in text
    assert "question-visual-scans:" not in text


def test_openapi_detail_nested_objects_are_strict_and_reject_answer_internals():
    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    schemas = document["components"]["schemas"]
    detail_properties = schemas["QuestionVisualScanDetailEnvelope"]["properties"][
        "data"
    ]["properties"]
    expected_refs = {
        "chemistry_observations": "QuestionVisualScanChemistryObservations",
        "risks_and_limits": "QuestionVisualScanRisksAndLimits",
        "comparison_with_wave1": "QuestionVisualScanComparison",
    }
    for field, schema_name in expected_refs.items():
        assert detail_properties[field] == {
            "$ref": f"#/components/schemas/{schema_name}"
        }
        assert schemas[schema_name]["additionalProperties"] is False
        assert set(schemas[schema_name]["required"]) == set(
            schemas[schema_name]["properties"]
        )
    assert detail_properties["hierarchy"]["oneOf"] == [
        {"$ref": "#/components/schemas/QuestionVisualScanHierarchy"},
        {"$ref": "#/components/schemas/QuestionVisualScanPTHierarchy"},
        {"$ref": "#/components/schemas/QuestionVisualScanYPHierarchy"},
        {"$ref": "#/components/schemas/QuestionVisualScanDTHierarchy"},
    ]
    assert detail_properties["dependency"]["anyOf"] == [
        {"$ref": "#/components/schemas/QuestionVisualScanDependency"},
        {"$ref": "#/components/schemas/QuestionVisualScanPTDependency"},
        {"$ref": "#/components/schemas/QuestionVisualScanYPDependency"},
        {"$ref": "#/components/schemas/QuestionVisualScanDTDependency"},
    ]
    assert detail_properties["answer_boundary"]["anyOf"] == [
        {"$ref": "#/components/schemas/QuestionVisualScanAnswerBoundary"},
        {"$ref": "#/components/schemas/QuestionVisualScanPTAnswerBoundary"},
        {"$ref": "#/components/schemas/QuestionVisualScanYPAnswerBoundary"},
        {"$ref": "#/components/schemas/QuestionVisualScanDTAnswerBoundary"},
    ]
    for schema_name in (
        "QuestionVisualScanHierarchy",
        "QuestionVisualScanPTHierarchy",
        "QuestionVisualScanDependency",
        "QuestionVisualScanPTDependency",
        "QuestionVisualScanAnswerBoundary",
        "QuestionVisualScanPTAnswerBoundary",
        "QuestionVisualScanYPHierarchy",
        "QuestionVisualScanDTHierarchy",
        "QuestionVisualScanYPDependency",
        "QuestionVisualScanDTDependency",
        "QuestionVisualScanYPAnswerBoundary",
        "QuestionVisualScanDTAnswerBoundary",
    ):
        assert schemas[schema_name]["additionalProperties"] is False
        assert set(schemas[schema_name]["required"]) == set(
            schemas[schema_name]["properties"]
        )
    assert schemas["QuestionVisualScanComparisonEntry"][
        "additionalProperties"
    ] is False

    root_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$ref": "#/components/schemas/QuestionVisualScanDetailEnvelope",
        "components": document["components"],
    }
    validator = jsonschema.Draft202012Validator(root_schema)
    reader = QuestionVisualScanReader(SHCHEM_ROOT)
    validated = reader._validated_catalog()
    details = [
        batch_reader.detail(node_id, snapshot=snapshot)
        for batch_reader, snapshot in validated
        for node_id in snapshot.by_node_id
    ]
    assert len(details) == 252
    for detail in details:
        envelope = {
            "contract_version": "schema-test",
            "request_id": "schema-test-request",
            "data": detail,
        }
        assert list(validator.iter_errors(envelope)) == []
        chemistry_injection = deepcopy(envelope)
        chemistry_injection["data"]["chemistry_observations"][
            "reference_summary_zh"
        ] = "must be rejected"
        assert list(validator.iter_errors(chemistry_injection))
        risk_injection = deepcopy(envelope)
        risk_injection["data"]["risks_and_limits"][
            "visual_alignment_evidence"
        ] = [{"answer_page": "internal"}]
        assert list(validator.iter_errors(risk_injection))

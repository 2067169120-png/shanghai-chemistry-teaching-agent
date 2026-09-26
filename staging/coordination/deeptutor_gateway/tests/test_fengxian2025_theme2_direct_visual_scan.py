from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

import integrations.deeptutor_shchem_v1.fengxian2025_theme2_direct_visual_scan as fengxian_module
from integrations.deeptutor_shchem_v1.fengxian2025_theme2_direct_visual_scan import (
    EXPECTED_EFFECTIVE_ATOMIC_IDS,
    EXPECTED_GROUPED_ATOMIC_IDS,
    EXPECTED_MASTER_NODE_IDS,
    PAPER_ID,
    PRODUCT_ID,
    THEME_ID,
    Fengxian2025Theme2DirectVisualScanError,
    Fengxian2025Theme2DirectVisualScanReader,
)

WORKSPACE = Path(__file__).resolve().parents[4]
SHCHEM_ROOT = WORKSPACE / "sh-chem-db"


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(
            *(_all_keys(item) for item in value.values()), set()
        )
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value), set())
    return set()


def _rebind_manifest(
    product: Path, relative_paths: tuple[str, ...], monkeypatch
) -> None:
    path = product / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    bindings = {item["path"]: item for item in manifest["files"]}
    for relative in relative_paths:
        raw = (product / relative).read_bytes()
        bindings[relative]["bytes"] = len(raw)
        bindings[relative]["sha256"] = hashlib.sha256(raw).hexdigest()
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    raw = path.read_bytes()
    monkeypatch.setattr(fengxian_module, "EXPECTED_MANIFEST_BYTES", len(raw))
    monkeypatch.setattr(
        fengxian_module,
        "EXPECTED_MANIFEST_FILE_SHA256",
        hashlib.sha256(raw).hexdigest(),
    )


def test_fengxian_reader_exposes_exact_grouped_10_to_13_chain_and_boundaries():
    reader = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT)
    status = reader.status()
    catalog = reader.catalog()

    assert status["product_id"] == PRODUCT_ID
    assert status["paper_id"] == PAPER_ID
    assert status["theme_id"] == THEME_ID
    assert status["counts"]["master_projection_nodes"] == 10
    assert status["counts"]["minimal_atomic_units"] == 13
    assert catalog["count"] == 10
    assert catalog["minimal_atomic_unit_count"] == 13
    assert catalog["master_node_ids"] == list(EXPECTED_MASTER_NODE_IDS)
    assert {
        item["master_node_id"]: tuple(item["minimal_atomic_unit_ids"])
        for item in catalog["items"]
    } == EXPECTED_GROUPED_ATOMIC_IDS
    assert not {
        "reference_answer",
        "reference_answer_text",
        "local_path",
        "source_path",
        "asset",
        "stored_path",
        "output_path",
    } & _all_keys(catalog)
    assert all(
        value is False
        for key, value in status["authority"].items()
        if key not in {"candidate_only", "read_only"}
    )

    details = [reader.detail(master_id) for master_id in EXPECTED_MASTER_NODE_IDS]
    flattened = [
        unit_id
        for detail in details
        for unit_id in (
            unit.get("atomic_part_id")
            or unit.get("effective_atomic_part_id")
            or unit.get("atomic_id")
            for unit in detail["minimal_atomic_units"]
        )
    ]
    assert tuple(flattened) == EXPECTED_EFFECTIVE_ATOMIC_IDS
    for index, detail in enumerate(details, start=1):
        hierarchy = detail["scan_hierarchy"]
        assert hierarchy["paper_id"] == PAPER_ID
        assert (
            hierarchy.get("theme_id", hierarchy.get("theme_big_question_id"))
            == THEME_ID
        )
        assert hierarchy["printed_question_order"] == index
        assert hierarchy["independent_choice_section"] is False
        assert detail["theme_chain_role"]
        assert detail["dependencies"]
        assert detail["source_identity"]
        assert detail["answer_boundary"]["verified"] is False
        assert detail["answer_boundary"]["independently_verified"] is False
        assert detail["answer_boundary"]["official_answer_claim_allowed"] is False
        assert detail["answer_boundary"]["official_scoring_claim_allowed"] is False
        assert (
            detail["cognitive_difficulty"].get(
                "is_measured_difficulty",
                detail["cognitive_difficulty"].get("measured", False),
            )
            is False
        )
        assert (
            detail["integrity"]["grouped_master_10_to_atomic_13_verified_on_read"]
            is True
        )
        for unit in detail["minimal_atomic_units"]:
            classification = unit.get("classification", detail["classification"])
            assert all(axis in classification for axis in ("K", "A", "C", "R", "RP"))


def test_fengxian_reader_serves_only_question_or_shared_crops_bound_to_node():
    reader = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT)
    details = [reader.detail(master_id) for master_id in EXPECTED_MASTER_NODE_IDS]
    detail = next(item for item in details if item["evidence_descriptors"])
    descriptor = detail["evidence_descriptors"][0]
    payload = reader.question_crop(detail["master_node_id"], descriptor["crop_id"])
    assert payload.data.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload.sha256 == descriptor["sha256"]

    other = next(
        item
        for item in details
        if item["master_node_id"] != detail["master_node_id"]
        and any(
            evidence["crop_id"]
            not in {entry["crop_id"] for entry in detail["evidence_descriptors"]}
            for evidence in item["evidence_descriptors"]
        )
    )
    alien = next(
        evidence
        for evidence in other["evidence_descriptors"]
        if evidence["crop_id"]
        not in {entry["crop_id"] for entry in detail["evidence_descriptors"]}
    )
    with pytest.raises(Fengxian2025Theme2DirectVisualScanError) as unbound:
        reader.question_crop(detail["master_node_id"], alien["crop_id"])
    assert unbound.value.status == 404

    forbidden_id = next(iter(reader._snapshot().forbidden_crop_ids))
    with pytest.raises(Fengxian2025Theme2DirectVisualScanError) as denied:
        reader.question_crop(detail["master_node_id"], forbidden_id)
    assert denied.value.status == 403


def test_fengxian_reader_projects_only_explicit_upstream_question_relationships():
    reader = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT)
    expected = {
        "FX2025-EM-S2-Q1-P1": [
            ("paper-p03-q1", "question"),
            ("paper-p03-theme2-apparatus", "shared_material"),
        ],
        "FX2025-EM-S2-Q2-P1": [
            ("paper-p03-q2", "question"),
            ("paper-p03-theme2-apparatus", "shared_material"),
        ],
        "FX2025-EM-S2-Q3-P1": [
            ("paper-p03-q3", "question"),
            ("paper-p03-theme2-apparatus", "shared_material"),
        ],
        "FX2025-EM-S2-Q4-P1": [
            ("paper-p03-q4-graph", "question"),
            ("paper-p03-theme2-apparatus", "shared_material"),
        ],
        "FX2025-EM-S2-Q5-P1": [("paper-p03-q5", "question")],
        "FX2025-EM-S2-Q6-P1": [
            ("paper-p03-q6", "question"),
            ("paper-p03-q5", "shared_material"),
        ],
        "FX2025-EM-S2-Q7-P1": [
            ("paper-p04-q7", "question"),
            ("paper-p03-titration-stimulus", "shared_material"),
        ],
        "FX2025-EM-S2-Q8-P1": [
            ("paper-p04-q8-burette", "question"),
            ("paper-p03-titration-stimulus", "shared_material"),
        ],
        "FX2025-EM-S2-Q9-P1": [
            ("paper-p04-q9-table", "question"),
            ("paper-p03-titration-stimulus", "shared_material"),
        ],
        "FX2025-EM-S2-Q10-P1": [
            ("paper-p04-q10", "question"),
            ("paper-p03-titration-stimulus", "shared_material"),
        ],
    }

    actual = {
        master_id: [
            (item["crop_id"], item["evidence_role"])
            for item in reader.detail(master_id)["evidence_descriptors"]
        ]
        for master_id in EXPECTED_MASTER_NODE_IDS
    }
    assert actual == expected
    assert reader._crop_role({"path": "evidence/paper-p03-q4-graph.png"}) == "unknown"
    assert reader._crop_role({"path": "evidence/answer-p01-q4.png"}) == "unknown"


def test_fengxian_reader_fails_closed_on_output_drift_and_path_traversal(
    tmp_path: Path, monkeypatch
):
    reader = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT)
    copied = tmp_path / "product"
    shutil.copytree(reader.product_root, copied)
    reader.product_root = copied.resolve()
    records = copied / "scan_records.jsonl"
    records.write_bytes(records.read_bytes() + b"\n")
    with pytest.raises(Fengxian2025Theme2DirectVisualScanError) as drifted:
        reader.catalog()
    assert drifted.value.code == "fengxian_theme2_binding_mismatch"

    shutil.rmtree(copied)
    shutil.copytree(
        Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT).product_root, copied
    )
    manifest_path = copied / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../escape.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    raw = manifest_path.read_bytes()
    monkeypatch.setattr(fengxian_module, "EXPECTED_MANIFEST_BYTES", len(raw))
    monkeypatch.setattr(
        fengxian_module,
        "EXPECTED_MANIFEST_FILE_SHA256",
        hashlib.sha256(raw).hexdigest(),
    )
    reader.product_root = copied.resolve()
    with pytest.raises(Fengxian2025Theme2DirectVisualScanError) as escaped:
        reader.status()
    assert escaped.value.code in {
        "fengxian_theme2_manifest_drift",
        "fengxian_theme2_path_invalid",
    }


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("gate", "fengxian_theme2_gate_drift"),
        ("hierarchy", "fengxian_theme2_hierarchy_drift"),
        ("master_rebind", "fengxian_theme2_atomic_group_drift"),
        ("drop", "fengxian_theme2_count_or_order_drift"),
    ),
)
def test_fengxian_reader_rejects_resigned_structural_mutations(
    tmp_path: Path, monkeypatch, mutation: str, expected_code: str
):
    source = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT).product_root
    copied = tmp_path / mutation
    shutil.copytree(source, copied)
    records_path = copied / "scan_records.jsonl"
    records = [
        json.loads(line)
        for line in records_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if mutation == "gate":
        records[0]["authority_gates"]["publication_allowed"] = True
    elif mutation == "hierarchy":
        records[0]["hierarchy"]["printed_sequence"] = 99
    elif mutation == "master_rebind":
        records[1]["master_projection"]["keyed_reader_id"] = EXPECTED_MASTER_NODE_IDS[0]
        records[1]["master_projection"]["master_projection_id"] = (
            EXPECTED_MASTER_NODE_IDS[0]
        )
    else:
        records.pop()
    records_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
            for record in records
        ),
        encoding="utf-8",
    )
    _rebind_manifest(copied, ("scan_records.jsonl",), monkeypatch)
    reader = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT)
    reader.product_root = copied.resolve()
    with pytest.raises(Fengxian2025Theme2DirectVisualScanError) as captured:
        reader.status()
    assert captured.value.code == expected_code


def test_fengxian_reader_rejects_resigned_invalid_png(tmp_path: Path, monkeypatch):
    source = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT).product_root
    copied = tmp_path / "png"
    shutil.copytree(source, copied)
    crop_manifest_path = copied / "crop_manifest.json"
    crop_manifest = json.loads(crop_manifest_path.read_text(encoding="utf-8"))
    crop = next(
        item for item in crop_manifest["crops"] if "paper-p03-q1" in item["path"]
    )
    crop_path = copied / crop["path"]
    crop_path.write_bytes(b"not-a-png")
    crop["bytes"] = crop_path.stat().st_size
    crop["sha256"] = hashlib.sha256(crop_path.read_bytes()).hexdigest()
    crop_manifest_path.write_text(
        json.dumps(crop_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _rebind_manifest(copied, ("crop_manifest.json", crop["path"]), monkeypatch)
    reader = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT)
    reader.product_root = copied.resolve()
    with pytest.raises(Fengxian2025Theme2DirectVisualScanError) as captured:
        reader.catalog()
    assert captured.value.code == "fengxian_theme2_png_invalid"


def test_fengxian_reader_get_operations_leave_product_tree_byte_identical():
    reader = Fengxian2025Theme2DirectVisualScanReader(SHCHEM_ROOT)
    before = _tree_digest(reader.product_root)
    reader.status()
    reader.catalog()
    for master_id in EXPECTED_MASTER_NODE_IDS:
        detail = reader.detail(master_id)
        for descriptor in detail["evidence_descriptors"]:
            reader.question_crop(master_id, descriptor["crop_id"])
    assert _tree_digest(reader.product_root) == before

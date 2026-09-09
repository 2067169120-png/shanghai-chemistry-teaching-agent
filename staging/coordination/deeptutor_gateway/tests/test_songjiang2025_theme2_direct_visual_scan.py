from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image, ImageChops

from integrations.deeptutor_shchem_v1 import (
    songjiang2025_theme2_direct_visual_scan as module,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanError,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchReader,
)
from integrations.deeptutor_shchem_v1.pudong2026_first_mock_theme_direct_visual_scan import (
    _Snapshot,
)

DB = Path(__file__).resolve().parents[4] / "sh-chem-db"
PACKAGE = DB / module.PRODUCT_RELATIVE
MANIFEST = (module.PRODUCT_RELATIVE / "candidate_manifest.json").as_posix()
CANDIDATES = (module.PRODUCT_RELATIVE / "question_candidates.jsonl").as_posix()
EVIDENCE = (module.PRODUCT_RELATIVE / "evidence_map.json").as_posix()


@pytest.fixture(scope="module")
def master_evidence():
    reader = MasterWave1WorkbenchReader(DB)
    snapshot = reader._snapshot()
    return snapshot, reader._integrity(snapshot)


@pytest.fixture
def reader(monkeypatch, master_evidence):
    master_snapshot, integrity = master_evidence
    # A read-only verified Master fixture keeps mutation tests small. The live
    # test below instantiates the actual Master reader with no substitutions.
    master = SimpleNamespace(
        _snapshot=lambda: master_snapshot,
        _integrity=lambda snapshot: integrity,
    )
    value = module.Songjiang2025Theme2DirectVisualScanReader(DB, master)
    return value


def _patch_file(reader, monkeypatch, path, raw):
    original = reader._verified_file

    def changed(root, relative):
        resolved, data = original(root, relative)
        return resolved, raw if relative == path else data

    monkeypatch.setattr(reader, "_verified_file", changed)


def _rebind(reader, monkeypatch, path, payload, *, jsonl=False):
    raw = (
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in payload).encode()
        if jsonl
        else (json.dumps(payload, ensure_ascii=False) + "\n").encode()
    )
    manifest = json.loads((DB / MANIFEST).read_bytes())
    key = "candidate_file_sha256" if path == CANDIDATES else "evidence_map_sha256"
    manifest[key] = hashlib.sha256(raw).hexdigest()
    manifest_raw = (json.dumps(manifest, ensure_ascii=False) + "\n").encode()
    _patch_file(reader, monkeypatch, path, raw)
    _patch_file(reader, monkeypatch, MANIFEST, manifest_raw)
    monkeypatch.setattr(
        module,
        "EXPECTED_MANIFEST_FILE_SHA256",
        hashlib.sha256(manifest_raw).hexdigest(),
    )
    monkeypatch.setattr(module, "EXPECTED_MANIFEST_BYTES", len(manifest_raw))


def _rows():
    return [
        json.loads(line)
        for line in (DB / CANDIDATES).read_text(encoding="utf-8").splitlines()
    ]


def test_actual_package_validator_readonly_and_live_reader():
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in PACKAGE.rglob("*")
        if path.is_file()
    }
    validator_path = PACKAGE / "validate_candidate.py"
    validator = ModuleType("songjiang_existing_package_validator_readonly")
    validator.__file__ = str(validator_path)
    # Executing the source directly never asks an import loader to create a
    # __pycache__ entry in the protected source package, regardless of env flags.
    exec(  # noqa: S102 - trusted local validator; source-only load prevents pyc writes.
        compile(validator_path.read_bytes(), str(validator_path), "exec"),
        validator.__dict__,
    )
    report = validator.validate_package(write_report=False)
    assert report["errors"] == []
    assert report["summary"]["questions"] == report["summary"]["parts"] == 9
    assert report["summary"]["local_crops"] == 20
    live = module.Songjiang2025Theme2DirectVisualScanReader(DB)
    catalog = live.catalog()
    assert catalog["count"] == 9
    assert catalog["master_node_ids"] == list(module.EXPECTED_ATOMIC_IDS)
    assert catalog["integrity"]["output_binding_count"] == 22
    assert catalog["integrity"]["source_binding_count"] == 12
    assert catalog["integrity"]["manifest_self_sha256"] is None
    assert catalog["coverage"]["direct_exact_overlap"] == 0
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in PACKAGE.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_actual_all_24_crop_bytes_decode_and_match_explicit_source_boxes(reader):
    snapshot = reader._snapshot()
    assert isinstance(snapshot, _Snapshot)
    assert len(snapshot.records) == len(snapshot.by_master_id) == 9
    assert len(snapshot.crop_by_id) == 24
    roles = {
        role: sum(row["role"] == role for row in snapshot.crop_by_id.values())
        for role in ("question", "shared_material", "answer", "unknown")
    }
    assert roles == {"question": 9, "shared_material": 5, "answer": 9, "unknown": 1}
    for crop in snapshot.crop_by_id.values():
        data = snapshot.output_bytes[crop["output_path"]]
        assert hashlib.sha256(data).hexdigest() == crop["sha256"]
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            assert image.size == (crop["width"], crop["height"])
            with Image.open(
                io.BytesIO(snapshot.output_bytes[crop["source_asset"]])
            ) as source:
                x, y, w, h = crop["crop_box"]
                expected = source.crop((x, y, x + w, y + h)).convert("RGB")
                assert (
                    ImageChops.difference(expected, image.convert("RGB")).getbbox()
                    is None
                )


def test_9_details_keep_source_text_answer_links_unknowns_and_authority(reader):
    originals = _rows()
    for original in originals:
        part = original["parts"][0]
        detail = reader.detail(part["part_id"])
        assert detail["visible_summary_zh"] == part["prompt_raw"]
        assert (
            detail["reference_answer"]["reference_answer_text"]
            == part["answer_evidence"]["answer_text"]
        )
        assert (
            detail["reference_answer"]["reference_points"]
            == part["answer_evidence"]["reference_points"]
        )
        assert detail["reference_answer"]["points_authority"] == "nonofficial_reference"
        assert detail["reference_answer"]["independently_verified"] is False
        assert (
            detail["reference_answer"]["rubric_status"] == "absent_no_stepwise_rubric"
        )
        assert detail["scan_hierarchy"]["paper_id"] == module.PAPER_ID
        assert detail["scan_hierarchy"]["theme_id"] == module.THEME_ID
        assert (
            detail["scan_hierarchy"]["printed_question_id"] == original["question_id"]
        )
        assert detail["scan_classification"]["primary_K"] is None
        assert detail["scan_classification"]["supporting_K"] == []
        assert detail["scan_classification"]["knowledge_candidates_K"] == [
            k["id"] for k in part["classification"]["knowledge_K"]
        ]
        assert (
            detail["scan_classification"]["label_status"] == "partial_source_candidate"
        )
        assert detail["cognitive_difficulty"] == part["difficulty"]
        assert detail["cognitive_difficulty"]["cognitive_prelabel"] is None
        assert detail["dependency"]["status"] == "unknown_prior_dependency_not_recorded"
        assert detail["dependency"]["prior_atomic_part_ids"] == []
        assert (
            detail["source_identity"]["source_url"]
            == original["provenance"]["source_url"]
        )
        assert len(detail["answer_source_evidence"]) == 1
        assert (
            detail["answer_source_evidence"][0]["sha256"]
            == part["answer_evidence"]["source_page_refs"][0]["crop_sha256"]
        )
        assert not any(
            value
            for key, value in detail["authority"].items()
            if key not in {"candidate_only", "read_only"}
        )
        assert "C:\\" not in json.dumps(detail) and "output_path" not in json.dumps(
            detail
        )


def test_all_explicit_shared_context_edges_not_only_shorter_sidecar_are_retained(
    reader,
):
    for original in _rows():
        detail = reader.detail(original["parts"][0]["part_id"])
        expected = [
            row
            for row in original["source_locator"]["page_refs"]
            if row["role"] == "context"
        ]
        shared = [
            row
            for row in detail["evidence_descriptors"]
            if row["evidence_role"] == "shared_material"
        ]
        assert {row["sha256"] for row in shared} == {
            row["crop_sha256"] for row in expected
        }
        assert detail["dependency"]["shared_material_crop_ids"] == [
            row["crop_id"] for row in shared
        ]
        assert [row["raw_text"] for row in detail["shared_materials"]] == [
            row["raw_text"] for row in original["stimulus_blocks"]
        ]
    # Q8/9 each explicitly include the full continuation as well as green rust.
    assert (
        len(
            reader.detail(module.EXPECTED_ATOMIC_IDS[7])["dependency"][
                "shared_material_crop_ids"
            ]
        )
        == 2
    )


def test_q6_source_conflict_stays_visible_without_hiding_original_answer(reader):
    detail = reader.detail(module.EXPECTED_ATOMIC_IDS[5])
    assert detail["reference_answer"]["source_evidence_status"] == "source_conflict"
    assert "活塞" in detail["reference_answer"]["reference_answer_text"]
    assert "逻辑" in json.dumps(detail["quality_notes"], ensure_ascii=False)
    assert detail["reference_answer"]["independently_verified"] is False
    assert detail["authority"]["lineage_gate_complete"] is False


def test_question_and_shared_crop_routes_return_exact_bound_pixels(reader):
    detail = reader.detail(module.EXPECTED_ATOMIC_IDS[3])
    for descriptor in detail["evidence_descriptors"]:
        result = reader.question_crop(detail["master_node_id"], descriptor["crop_id"])
        assert result.sha256 == descriptor["sha256"]
        assert hashlib.sha256(result.data).hexdigest() == result.sha256
        assert len(result.data) == descriptor["bytes"]


def test_answer_unknown_and_cross_question_crops_are_not_routable(reader):
    snapshot = reader._snapshot()
    node = module.EXPECTED_ATOMIC_IDS[0]
    for crop_id in snapshot.forbidden_crop_ids:
        with pytest.raises(module.Songjiang2025Theme2DirectVisualScanError) as caught:
            reader.question_crop(node, crop_id)
        assert caught.value.status == 403
    other = next(
        row["crop_id"]
        for row in snapshot.records[1]["viewed_evidence"]
        if row["evidence_role"] == "question"
    )
    with pytest.raises(module.Songjiang2025Theme2DirectVisualScanError) as caught:
        reader.question_crop(node, other)
    assert caught.value.status == 404


@pytest.mark.parametrize("node", ["../outside", "a/b", "", "SJ2025-EM-S2-Q10-P1"])
def test_invalid_or_missing_master_identity_rejected(reader, node):
    with pytest.raises(MasterDirectVisualScanError):
        reader.detail(node)


@pytest.mark.parametrize(
    "kind",
    [
        "manifest",
        "candidates",
        "evidence_map",
        "schema",
        "source_page",
        "question_crop",
        "answer_crop",
        "shared_crop",
        "upstream_pilot",
        "upstream_manifest",
    ],
)
def test_each_frozen_binding_is_rechecked_after_a_successful_read(
    reader, monkeypatch, kind
):
    reader.catalog()
    manifest = json.loads((DB / MANIFEST).read_bytes())
    roles = reader._snapshot().crop_by_id.values()
    path = {
        "manifest": MANIFEST,
        "candidates": CANDIDATES,
        "evidence_map": EVIDENCE,
        "schema": manifest["shared_schema"],
        "source_page": manifest["source_assets"][0]["asset"],
        "upstream_pilot": manifest["upstream_theme_evidence"]["pilot_asset"],
        "upstream_manifest": manifest["upstream_theme_evidence"]["crop_manifest_asset"],
        **{
            label: next(row["output_path"] for row in roles if row["role"] == role)
            for label, role in (
                ("question_crop", "question"),
                ("answer_crop", "answer"),
                ("shared_crop", "shared_material"),
            )
        },
    }[kind]
    _patch_file(reader, monkeypatch, path, (DB / path).read_bytes() + b"changed")
    with pytest.raises(MasterDirectVisualScanError):
        reader.catalog()


@pytest.mark.parametrize(
    "mutation",
    [
        "role",
        "source_sha",
        "crop_box",
        "answer_edge",
        "gate",
        "source_identity",
        "printed_identity",
        "duplicate",
        "part_count",
    ],
)
def test_hash_rebound_candidate_still_requires_semantic_source_closure(
    reader, monkeypatch, mutation
):
    rows = _rows()
    row = rows[0]
    if mutation == "role":
        row["source_locator"]["page_refs"][0]["role"] = "context"
    elif mutation == "source_sha":
        row["source_locator"]["page_refs"][0]["source_sha256"] = "f" * 64
    elif mutation == "crop_box":
        row["source_locator"]["page_refs"][0]["crop_box"][0] += 1
    elif mutation == "answer_edge":
        row["parts"][0]["answer_evidence"]["source_page_refs"] = deepcopy(
            rows[1]["parts"][0]["answer_evidence"]["source_page_refs"]
        )
    elif mutation == "gate":
        row["gates"]["generation_allowed"] = True
    elif mutation == "source_identity":
        row["source_id"] = "file-another"
    elif mutation == "printed_identity":
        row["question_id"] = rows[1]["question_id"]
    elif mutation == "duplicate":
        rows[1] = deepcopy(rows[0])
    else:
        row["parts"].append(deepcopy(row["parts"][0]))
    _rebind(reader, monkeypatch, CANDIDATES, rows, jsonl=True)
    with pytest.raises(MasterDirectVisualScanError):
        reader.catalog()


@pytest.mark.parametrize(
    "mutation", ["missing_type", "duplicate_type", "cross_question", "answer_as_shared"]
)
def test_sidecar_cannot_remap_or_drop_explicit_evidence(reader, monkeypatch, mutation):
    value = json.loads((DB / EVIDENCE).read_bytes())
    if mutation == "missing_type":
        value["item_type_contract"]["entries"].pop()
    elif mutation == "duplicate_type":
        value["item_type_contract"]["entries"][1] = deepcopy(
            value["item_type_contract"]["entries"][0]
        )
    elif mutation == "cross_question":
        value["question_evidence"][0]["question_crop"] = value["question_evidence"][1][
            "question_crop"
        ]
    else:
        value["question_evidence"][0]["shared_stimulus_refs"] = [
            value["question_evidence"][0]["answer_crop"]
        ]
    _rebind(reader, monkeypatch, EVIDENCE, value)
    with pytest.raises(MasterDirectVisualScanError):
        reader.catalog()


@pytest.mark.parametrize(
    "mutation", ["parent", "source", "exact_overlap", "missing_master"]
)
def test_master_parent_and_exact_disjoint_boundaries(
    reader, monkeypatch, master_evidence, mutation
):
    snapshot, _ = master_evidence
    rows = list(snapshot.master_layers["atomic_part"])
    target_index = next(
        i
        for i, row in enumerate(rows)
        if row["atomic_part_id"] == module.EXPECTED_ATOMIC_IDS[0]
    )
    rows[target_index] = deepcopy(rows[target_index])
    relations = dict(snapshot.relations_by_master)
    if mutation == "parent":
        rows[target_index]["parent_theme_big_question_id"] = "THEME-other"
    elif mutation == "source":
        rows[target_index]["source_id"] = "file-other"
    elif mutation == "missing_master":
        rows.pop(target_index)
    else:
        relation = {"relation_type": "exact_1_to_1", "identity_mapping_allowed": True}
        relations[module.EXPECTED_ATOMIC_IDS[0]] = [relation]
    changed = SimpleNamespace(
        master_layers={"atomic_part": rows}, relations_by_master=relations
    )
    monkeypatch.setattr(reader.master_workbench, "_snapshot", lambda: changed)
    with pytest.raises(MasterDirectVisualScanError):
        reader.catalog()


def test_public_projection_copies_cannot_change_later_reads(reader):
    value = reader.detail(module.EXPECTED_ATOMIC_IDS[0])
    value["authority"]["generation_allowed"] = True
    value["scan_classification"]["knowledge_candidates_K"].append("K99")
    again = reader.detail(module.EXPECTED_ATOMIC_IDS[0])
    assert again["authority"]["generation_allowed"] is False
    assert "K99" not in again["scan_classification"]["knowledge_candidates_K"]

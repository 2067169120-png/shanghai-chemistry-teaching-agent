from __future__ import annotations

import hashlib
import io
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageChops

from integrations.deeptutor_shchem_v1 import (
    fudan2026_april_theme5_zns_direct_visual_scan as module,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanError,
)
from integrations.deeptutor_shchem_v1.master_wave1_workbench import (
    MasterWave1WorkbenchReader,
)

DB = Path(__file__).resolve().parents[4] / "sh-chem-db"
PACKAGE = DB / module.PRODUCT_RELATIVE
MANIFEST = (module.PRODUCT_RELATIVE / "candidate_manifest.json").as_posix()
pytestmark = pytest.mark.skipif(
    not (PACKAGE / "candidate_manifest.json").is_file(),
    reason="requires the private archived Fudan April ZnS package and Master",
)


@pytest.fixture(scope="module")
def master_snapshot():
    return MasterWave1WorkbenchReader(DB)._snapshot()


@pytest.fixture
def reader(master_snapshot):
    return module.Fudan2026AprilTheme5ZnsDirectVisualScanReader(
        DB, SimpleNamespace(_snapshot=lambda: master_snapshot)
    )


@pytest.fixture(scope="module")
def inputs(master_snapshot):
    return module.Fudan2026AprilTheme5ZnsDirectVisualScanReader(
        DB, SimpleNamespace(_snapshot=lambda: master_snapshot)
    )._inputs()


@pytest.fixture(scope="module")
def source_snapshot(master_snapshot):
    return module.Fudan2026AprilTheme5ZnsDirectVisualScanReader(
        DB, SimpleNamespace(_snapshot=lambda: master_snapshot)
    )._snapshot()


@pytest.fixture
def projected_reader(reader, source_snapshot, monkeypatch):
    monkeypatch.setattr(reader, "_snapshot", lambda: deepcopy(source_snapshot))
    return reader


def _bound_hashes():
    manifest = json.loads((DB / MANIFEST).read_bytes())
    paths = {path for path in PACKAGE.rglob("*") if path.is_file()}
    paths.update(DB / row["asset"] for row in manifest["source_assets"])
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def test_live_catalog_keeps_originals_and_master_identity_unchanged():
    before = _bound_hashes()
    catalog = module.Fudan2026AprilTheme5ZnsDirectVisualScanReader(DB).catalog()
    assert catalog["count"] == 9
    assert catalog["master_node_ids"] == list(module.EXPECTED_ATOMIC_IDS)
    assert catalog["paper_id"] == module.PAPER_ID
    assert catalog["integrity"]["source_binding_count"] == 13
    assert catalog["integrity"]["archived_crop_binding_count"] == 24
    assert catalog["integrity"]["display_crop_binding_count"] == 22
    assert catalog["integrity"]["output_binding_count"] == 46
    assert catalog["integrity"]["manifest_self_sha256"] is None
    assert catalog["coverage"]["direct_exact_overlap"] == 0
    assert all(
        item["availability"] == "present_part_aligned" for item in catalog["items"]
    )
    assert before == _bound_hashes()


def test_all_24_archived_crops_are_exact_source_pixels(inputs):
    _, _, sources, crops, outputs = inputs
    assert len(crops) == 24
    for asset, crop in crops.items():
        with (
            Image.open(io.BytesIO(outputs[asset])) as image,
            Image.open(io.BytesIO(sources[crop["source_asset"]])) as source,
        ):
            x, y, width, height = crop["crop_box_xywh"]
            expected = source.crop((x, y, x + width, y + height)).convert("RGB")
            assert image.size == expected.size
            assert (
                ImageChops.difference(expected, image.convert("RGB")).getbbox() is None
            )


def test_22_clean_display_windows_have_exact_original_page_bindings(
    source_snapshot, inputs
):
    _, _, sources, archive, _ = inputs
    assert Counter(crop["role"] for crop in source_snapshot.crop_by_id.values()) == {
        "question": 8,
        "shared_material": 6,
        "answer": 8,
    }
    assert {crop["display_key"] for crop in source_snapshot.crop_by_id.values()} == set(
        module._DISPLAY_WINDOWS
    )
    source_anchors = {
        (crop["sha256"], tuple(crop["crop_box_xywh"])) for crop in archive.values()
    }
    for crop in source_snapshot.crop_by_id.values():
        assert (
            crop["archived_crop_sha256"],
            tuple(crop["archived_crop_box"]),
        ) in source_anchors
        assert (
            hashlib.sha256(sources[crop["source_asset"]]).hexdigest()
            == crop["source_sha256"]
        )
        raw = source_snapshot.output_bytes[crop["output_path"]]
        assert hashlib.sha256(raw).hexdigest() == crop["sha256"]
        with (
            Image.open(io.BytesIO(raw)) as rendered,
            Image.open(io.BytesIO(sources[crop["source_asset"]])) as source,
        ):
            x, y, width, height = crop["source_crop_box"]
            expected = source.crop((x, y, x + width, y + height)).convert("RGB")
            assert rendered.size == expected.size
            assert (
                ImageChops.difference(expected, rendered.convert("RGB")).getbbox()
                is None
            )


def test_source_windows_are_frozen_reviewed_rectangles_not_archive_intersections(
    source_snapshot,
):
    by_key = {crop["display_key"]: crop for crop in source_snapshot.crop_by_id.values()}
    assert by_key["q42"]["source_crop_box"] == [190, 1518, 1160, 41]
    assert by_key["q42"]["source_crop_box"][1] < by_key["q42"]["archived_crop_box"][1]
    assert by_key["optical"]["source_crop_box"] == [190, 498, 1160, 432]
    assert (
        by_key["optical"]["source_crop_box"][1]
        < by_key["optical"]["archived_crop_box"][1]
    )
    assert by_key["preparation"]["source_crop_box"] == [190, 906, 1160, 493]
    assert by_key["q40"]["source_crop_box"][1] == 1395
    # All clean answer rectangles end before the following answer begins.
    for first, second in ((40, 41), (41, 42), (42, 43), (44, 45), (45, 46), (46, 47)):
        a, b = by_key[f"a{first}"], by_key[f"a{second}"]
        assert a["source_asset"] == b["source_asset"]
        assert a["source_crop_box"][1] + a["height"] <= b["source_crop_box"][1]


def test_all_parts_preserve_text_reference_points_and_candidate_unknowns(
    projected_reader, inputs
):
    scores = []
    for row in inputs[1]:
        for part in row["parts"]:
            detail = projected_reader.detail(part["part_id"])
            answer = detail["reference_answer"]
            scores.append(answer["reference_points"])
            assert detail["visible_summary_zh"] == part["prompt_raw"]
            assert (
                detail["source_identity"]["source_url"]
                == row["provenance"]["source_url"]
            )
            assert (
                detail["scan_hierarchy"]["printed_question_number"]
                == row["source_locator"]["printed_question_number"]
            )
            assert detail["scan_hierarchy"]["paper_id"] == module.PAPER_ID
            assert detail["scan_hierarchy"]["theme_id"] == module.THEME_ID
            assert (
                detail["scan_classification"]["knowledge_candidates"]
                == part["classification"]["knowledge_K"]
            )
            assert detail["scan_classification"]["primary_K"] is None
            assert detail["scan_classification"]["supporting_K"] == []
            assert (
                detail["scan_classification"]["selection_rule"]
                == part["selection_rule"]
            )
            assert detail["cognitive_difficulty"] == part["difficulty"]
            assert detail["cognitive_difficulty"]["cognitive_prelabel"] is None
            assert (
                answer["reference_answer_text"]
                == part["answer_evidence"]["answer_text"]
            )
            assert (
                answer["source_answer_text"] == part["answer_evidence"]["answer_text"]
            )
            assert (
                answer["source_authority"]
                == answer["points_authority"]
                == "nonofficial_reference"
            )
            assert answer["independently_verified"] is False
            assert not any(
                value
                for key, value in detail["authority"].items()
                if key not in {"candidate_only", "read_only"}
            )
            assert "C:\\" not in json.dumps(detail)
            assert "output_path" not in json.dumps(detail)
    assert scores == [2, 1, 2, 2, 2, 2, 2, 2, 2]
    assert sum(scores) == 17


def test_q41_two_parts_share_one_question_and_one_full_answer_image(projected_reader):
    first = projected_reader.detail("FD2026-APR-S5-Q41-P1")
    second = projected_reader.detail("FD2026-APR-S5-Q41-P2")
    assert first["evidence_descriptors"] == second["evidence_descriptors"]
    assert (
        first["reference_answer_images"][0]["crop_id"]
        == second["reference_answer_images"][0]["crop_id"]
    )
    assert (
        first["reference_answer_images"][0]["sha256"]
        == second["reference_answer_images"][0]["sha256"]
    )
    assert first["reference_answer_images"][0]["display_mode"] == "preview_only"
    assert second["reference_answer_images"][0]["display_mode"] == "inline_required"
    assert first["reference_answer_images"][0]["source_crop_box"] == [
        200,
        1528,
        1140,
        361,
    ]
    assert first["reference_answer"]["reference_points"] == 1
    assert second["reference_answer"]["reference_points"] == 2
    assert "8.0×10⁻⁵" in second["reference_answer"]["reference_answer_text"]
    assert (
        first["scan_hierarchy"]["printed_question_id"]
        == second["scan_hierarchy"]["printed_question_id"]
    )
    assert first["scan_hierarchy"]["atomic_sequence_in_printed"] == 1
    assert second["scan_hierarchy"]["atomic_sequence_in_printed"] == 2


def test_shared_materials_remain_complete_for_isolated_printed_questions(
    projected_reader,
):
    def shared(number):
        return [
            item
            for item in projected_reader.detail(f"FD2026-APR-S5-Q{number}-P1")[
                "evidence_descriptors"
            ]
            if item["evidence_role"] == "shared_material"
        ]

    assert shared(40) == shared(41) == shared(42)
    preparation = projected_reader.detail("FD2026-APR-S5-Q40-P1")
    assert {
        entry["source_stimulus_block_id"]
        for entry in preparation["dependency_evidence"]["theme_shared_block_bindings"]
    } == {
        "FD2026-APR-S5-Q40-THEME",
        "FD2026-APR-S5-Q40-PREP",
    }
    assert shared(43) == shared(44)
    assert [item["source_page"] for item in shared(43)] == [6, 7]
    assert shared(43)[1]["source_crop_box"] == [190, 180, 380, 275]
    assert shared(45)[0]["source_crop_box"] == [190, 498, 1160, 432]
    assert shared(46) == shared(47)
    assert [item["source_crop_box"] for item in shared(47)] == [
        [190, 1186, 1160, 40],
        [190, 1310, 810, 245],
    ]
    detail = projected_reader.detail("FD2026-APR-S5-Q47-P1")
    assert detail["dependency"]["prior_atomic_part_ids"] == []
    assert (
        detail["dependency_evidence"]["theme_shared_block_bindings"][0][
            "source_stimulus_block_id"
        ]
        == "FD2026-APR-S5-Q46-BATTERY"
    )


def test_question_and_teacher_answer_routes_are_separate_and_part_bound(
    projected_reader,
):
    for node in module.EXPECTED_ATOMIC_IDS:
        detail = projected_reader.detail(node)
        for descriptor in detail["evidence_descriptors"]:
            payload = projected_reader.question_crop(node, descriptor["crop_id"])
            assert payload.sha256 == descriptor["sha256"]
        for descriptor in detail["reference_answer_images"]:
            payload = projected_reader.teacher_answer_crop(node, descriptor["crop_id"])
            assert payload.sha256 == descriptor["sha256"]
            with pytest.raises(MasterDirectVisualScanError) as caught:
                projected_reader.question_crop(node, descriptor["crop_id"])
            assert caught.value.status == 403
    first, other = module.EXPECTED_ATOMIC_IDS[0], module.EXPECTED_ATOMIC_IDS[-1]
    answer = projected_reader.detail(other)["reference_answer_images"][0]
    with pytest.raises(MasterDirectVisualScanError) as caught:
        projected_reader.teacher_answer_crop(first, answer["crop_id"])
    assert caught.value.status == 404
    question = projected_reader.detail(first)["evidence_descriptors"][0]
    with pytest.raises(MasterDirectVisualScanError):
        projected_reader.teacher_answer_crop(first, question["crop_id"])


@pytest.mark.parametrize("value", ["../Q40", "C:\\question.png", "source A", ""])
def test_identifiers_cannot_be_paths(reader, value):
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.detail(value)
    assert caught.value.code == "master_direct_scan_invalid_identifier"
    assert caught.value.status == 400


@pytest.mark.parametrize("kind", ["manifest", "records", "source", "crop"])
def test_hash_drift_fails_closed(reader, inputs, monkeypatch, kind):
    manifest = inputs[0]
    asset = {
        "manifest": MANIFEST,
        "records": manifest["candidate_file"],
        "source": manifest["source_assets"][5]["asset"],
        "crop": manifest["crops"][0]["asset"],
    }[kind]
    original = reader._verified_file

    def changed(root, relative):
        path, raw = original(root, relative)
        return path, raw + b"changed" if relative == asset else raw

    monkeypatch.setattr(reader, "_verified_file", changed)
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.catalog()
    assert caught.value.code == "master_direct_scan_binding_mismatch"


@pytest.mark.parametrize(
    "mutation",
    ["question_role", "context_ref", "source_box", "answer_owner", "q41_points"],
)
def test_broken_explicit_relations_are_not_inferred(reader, inputs, mutation):
    _, rows, _, crops, _ = deepcopy(inputs)
    if mutation == "question_role":
        rows[0]["source_locator"]["page_refs"][1]["role"] = "answer"
    elif mutation == "context_ref":
        rows[5]["stimulus_blocks"][1]["image_refs"] = ["missing"]
    elif mutation == "source_box":
        rows[0]["source_locator"]["page_refs"][1]["crop_box"][1] += 1
    elif mutation == "answer_owner":
        rows[0]["parts"][0]["answer_evidence"]["source_page_refs"] = rows[-1]["parts"][
            0
        ]["answer_evidence"]["source_page_refs"]
    else:
        rows[1]["parts"][0]["answer_evidence"]["reference_points"] = 3
    with pytest.raises(MasterDirectVisualScanError):
        reader._relations(rows, crops)


def test_missing_display_window_fails_without_dirty_archive_fallback(
    reader, monkeypatch
):
    windows = dict(module._DISPLAY_WINDOWS)
    del windows["q42"]
    monkeypatch.setattr(module, "_DISPLAY_WINDOWS", windows)
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.catalog()
    assert caught.value.code == "fudan_zns_presentation_binding_invalid"


def test_master_parent_drift_fails(reader, master_snapshot, monkeypatch):
    changed = deepcopy(master_snapshot)
    changed.master_nodes[("atomic_part", module.EXPECTED_ATOMIC_IDS[0])][
        "parent_paper_id"
    ] = "another"
    monkeypatch.setattr(reader.master_workbench, "_snapshot", lambda: changed)
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.catalog()
    assert caught.value.code == "master_direct_scan_parent_chain_invalid"


def test_numeric_difficulty_is_rejected(
    reader, inputs, master_snapshot, source_snapshot
):
    _, rows, _, crops, _ = deepcopy(inputs)
    relation = reader._relations(rows, crops)[0]
    record = source_snapshot.records[0]
    rows[0]["parts"][0]["difficulty"]["factors"][0]["value"] = 2
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader._project(
            rows[0],
            0,
            relation,
            record["viewed_evidence"],
            record["answer"]["visual_alignment_evidence"][0],
            master_snapshot,
            1,
        )
    assert caught.value.code == "master_direct_scan_difficulty_invalid"


def test_gate_drift_is_not_accepted():
    with pytest.raises(MasterDirectVisualScanError) as caught:
        module._closed({"nested": [{"formal_promotion_allowed": True}]})
    assert caught.value.code == "master_direct_scan_gate_elevated"


def test_projection_is_detached(projected_reader):
    node = module.EXPECTED_ATOMIC_IDS[0]
    changed = projected_reader.detail(node)
    changed["scan_classification"]["knowledge_candidates"].clear()
    changed["reference_answer"]["reference_points"] = 99
    fresh = projected_reader.detail(node)
    assert fresh["scan_classification"]["knowledge_candidates"]
    assert fresh["reference_answer"]["reference_points"] == 2

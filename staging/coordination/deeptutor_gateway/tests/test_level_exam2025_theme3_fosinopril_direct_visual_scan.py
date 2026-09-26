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
    level_exam2025_theme3_fosinopril_direct_visual_scan as module,
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
    reason="requires the local frozen dual-source fosinopril package and Master",
)


@pytest.fixture(scope="module")
def master_snapshot():
    return MasterWave1WorkbenchReader(DB)._snapshot()


@pytest.fixture
def reader(master_snapshot):
    master = SimpleNamespace(_snapshot=lambda: master_snapshot)
    return module.LevelExam2025Theme3FosinoprilDirectVisualScanReader(DB, master)


@pytest.fixture(scope="module")
def inputs(master_snapshot):
    reader = module.LevelExam2025Theme3FosinoprilDirectVisualScanReader(
        DB, SimpleNamespace(_snapshot=lambda: master_snapshot)
    )
    return reader._inputs()


def _package_hashes():
    manifest = json.loads((DB / MANIFEST).read_bytes())
    paths = {path for path in PACKAGE.rglob("*") if path.is_file()}
    paths.update(DB / row["asset"] for row in manifest["source_assets"])
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def _patch_file(reader, monkeypatch, asset, raw):
    original = reader._verified_file

    def changed(root, relative):
        path, data = original(root, relative)
        return path, raw if relative == asset else data

    monkeypatch.setattr(reader, "_verified_file", changed)


def test_live_catalog_keeps_existing_identities_and_original_files_unchanged():
    before = _package_hashes()
    live = module.LevelExam2025Theme3FosinoprilDirectVisualScanReader(DB)
    catalog = live.catalog()
    assert catalog["count"] == 9
    assert catalog["master_node_ids"] == list(module.EXPECTED_ATOMIC_IDS)
    assert catalog["paper_id"] == module.PAPER_ID
    assert catalog["integrity"]["output_binding_count"] == 58
    assert catalog["integrity"]["source_binding_count"] == 14
    assert catalog["integrity"]["manifest_self_sha256"] is None
    assert catalog["integrity"]["new_visual_or_human_review_performed"] is False
    assert catalog["coverage"]["direct_exact_overlap"] == 0
    assert catalog["paper_identity_boundary"]["dual_sources_merged"] is False
    assert all(
        item["availability"] == "present_part_aligned" for item in catalog["items"]
    )
    assert live.status()["counts"]["question_crop_bindings"] == 10
    assert before == _package_hashes()


def test_all_49_frozen_crops_decode_and_match_explicit_source_rectangles(
    reader, inputs
):
    _, records, evidence, classification, outputs, crops = deepcopy(inputs)
    reader._relations(records, evidence, classification, crops)
    assert len(records) == 9
    assert len(crops) == 49
    assert Counter(crop["role"] for crop in crops.values()) == {
        "question": 10,
        "shared_material": 1,
        "answer": 9,
        "unknown": 29,
    }
    for crop in crops.values():
        raw = outputs[crop["output_path"]]
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            assert image.size == (crop["width"], crop["height"])
            with Image.open(DB / crop["source_asset"]) as source:
                x, y, width, height = crop["crop_box"]
                expected = source.crop((x, y, x + width, y + height)).convert("RGB")
                assert (
                    ImageChops.difference(expected, image.convert("RGB")).getbbox()
                    is None
                )


def test_nine_details_preserve_source_a_text_candidate_tags_and_unknowns(
    reader, inputs
):
    for original in inputs[1]:
        part = original["parts"][0]
        node = part["part_id"]
        source_a = part["source_variants"]["source_A"]
        answer_a = part["answer_evidence"]["source_variants"]["source_A"]
        detail = reader.detail(node)
        assert detail["visible_summary_zh"] == source_a["prompt_raw"]
        assert detail["response_requirement_zh"] == source_a["prompt_raw"]
        assert detail["source_identity"]["source_url"] == source_a["source_url"]
        assert detail["source_variant"] == "source_A"
        assert detail["scan_hierarchy"]["paper_id"] == module.PAPER_ID
        assert detail["scan_hierarchy"]["theme_id"] == module.THEME_ID
        assert (
            detail["scan_hierarchy"]["printed_question_id"] == original["question_id"]
        )
        assert (
            detail["scan_hierarchy"]["printed_question_number"]
            == source_a["printed_subquestion"]
        )
        classification = detail["scan_classification"]
        assert classification["item_type"] == source_a["item_type"]
        assert classification["selection_rule"] == source_a["selection_rule"]
        assert classification["primary_K"] is None
        assert classification["supporting_K"] == []
        assert (
            classification["knowledge_candidates"]
            == part["classification"]["knowledge_K"]
        )
        assert classification["label_status"] == "partial_source_candidate"
        assert detail["cognitive_difficulty"] == part["difficulty"]
        assert detail["cognitive_difficulty"]["cognitive_prelabel"] is None
        assert detail["dependency"]["dependency_kind"] == "shared_theme_context"
        assert detail["dependency"]["status"] == module.DEPENDENCY_STATUS
        assert detail["dependency"]["prior_atomic_part_ids"] == []
        assert detail["dependency_evidence"]["human_checked"] is False
        assert detail["dependency_evidence"]["candidate_only"] is True
        assert detail["dependency_evidence"]["source_variant"] == "source_A"
        assert detail["source_boundary_zh"] == module.SOURCE_BOUNDARY_ZH
        assert detail["quality_notes"][
            1 : 1 + len(original["source_conflicts_and_boundaries"])
        ] == [
            entry["resolution_note"]
            for entry in original["source_conflicts_and_boundaries"]
        ]
        answer = detail["reference_answer"]
        assert answer["source_answer_text"] == answer_a["value"]
        assert answer["reference_answer_text"] == (
            module.VISUAL_ANSWER_NOTICE
            if answer_a["visual_only"]
            else answer_a["value"]
        )
        assert (
            answer["answer_text_is_source_transcription"] is not answer_a["visual_only"]
        )
        assert answer["source_authority"] == "nonofficial_reference"
        assert answer["independently_verified"] is False
        assert answer["reference_points"] is None
        assert answer["rubric_status"] == "absent_no_stepwise_rubric"
        assert not any(
            v
            for k, v in detail["authority"].items()
            if k not in {"candidate_only", "read_only"}
        )
        assert "output_path" not in json.dumps(detail) and "C:\\" not in json.dumps(
            detail
        )


def test_source_b_is_explicit_comparison_not_a_prompt_or_answer_replacement(
    reader, inputs
):
    for index in (2, 5, 7):  # Q3 response form, Q6 selection wording, Q8 H2 wording.
        part = inputs[1][index]["parts"][0]
        detail = reader.detail(part["part_id"])
        comparison = detail["source_variant_comparison"]
        assert comparison["default_source_variant"] == "source_A"
        assert comparison["merged"] is False
        assert (
            comparison["differences"]
            == inputs[1][index]["source_conflicts_and_boundaries"]
        )
        for variant in ("source_A", "source_B"):
            assert (
                comparison["prompt_by_source"][variant]["prompt_raw"]
                == part["source_variants"][variant]["prompt_raw"]
            )
            assert (
                comparison["answer_by_source"][variant]["value"]
                == part["answer_evidence"]["source_variants"][variant]["value"]
            )
        assert (
            detail["visible_summary_zh"]
            == part["source_variants"]["source_A"]["prompt_raw"]
        )
    q3 = reader.detail(module.EXPECTED_ATOMIC_IDS[2])["source_variant_comparison"]
    assert (
        q3["prompt_by_source"]["source_A"]["item_type"]
        != q3["prompt_by_source"]["source_B"]["item_type"]
    )
    q3_detail = reader.detail(module.EXPECTED_ATOMIC_IDS[2])
    assert q3_detail["scan_classification"]["R"] == ["R01"]
    assert q3["prompt_by_source"]["source_B"]["item_type"] == "short_fill"
    assert (
        q3["original_dual_source_response_R"]
        == inputs[1][2]["parts"][0]["classification"]["response_R"]
    )
    assert {entry["id"] for entry in q3["original_dual_source_response_R"]} == {
        "R01",
        "R02",
    }
    projection = q3_detail["scan_classification"]["response_projection_evidence"]
    assert projection["source_variant"] == "source_A"
    assert projection["response_R_by_source"] == {
        "source_A": ["R01"],
        "source_B": ["R02"],
    }
    assert projection["source_sha256"] == module._DEPENDENCY_SOURCE_SHA256[4]
    assert projection["human_checked"] is False
    q6 = reader.detail(module.EXPECTED_ATOMIC_IDS[5])
    assert q6["scan_classification"]["selection_rule"] == "unknown"
    assert "来源A题面未注明计分及单选/多选规则" in q6["quality_notes"][-1]
    q8 = reader.detail(module.EXPECTED_ATOMIC_IDS[7])["source_variant_comparison"]
    assert (
        q8["answer_by_source"]["source_A"]["value"]
        != q8["answer_by_source"]["source_B"]["value"]
    )


def test_q7_retains_two_source_a_question_pages_and_shared_route(reader, inputs):
    node = module.EXPECTED_ATOMIC_IDS[6]
    detail = reader.detail(node)
    questions = [
        item
        for item in detail["evidence_descriptors"]
        if item["evidence_role"] == "question"
    ]
    assert [item["source_page"] for item in questions] == [4, 5]
    refs = inputs[2][6]["prompt_crop_refs"]
    assert [item.get("archived_crop_sha256", item["sha256"]) for item in questions] == [
        ref["crop_sha256"] for ref in refs if ref["source_variant"] == "source_A"
    ]
    shared = [
        item
        for item in detail["evidence_descriptors"]
        if item["evidence_role"] == "shared_material"
    ]
    assert len(shared) == 1
    assert detail["dependency"]["shared_material_crop_ids"] == [shared[0]["crop_id"]]
    assert detail["shared_materials"][0]["crop_ids"] == [shared[0]["crop_id"]]
    for descriptor in [*questions, *shared]:
        payload = reader.question_crop(node, descriptor["crop_id"])
        assert payload.sha256 == descriptor["sha256"]


def test_q5_q7_q9_require_teacher_structure_images_and_student_route_denies_all_answers(
    reader,
):
    for node in module.EXPECTED_ATOMIC_IDS:
        detail = reader.detail(node)
        assert len(detail["reference_answer_images"]) == 1
        image = detail["reference_answer_images"][0]
        assert image["access"] == "teacher_reference_answer_only"
        assert image["display_mode"] == (
            "inline_required"
            if node in module.VISUAL_ONLY_ATOMIC_IDS
            else "preview_only"
        )
        assert image["crop_id"] not in {
            item["crop_id"] for item in detail["evidence_descriptors"]
        }
        assert (
            reader.teacher_answer_crop(node, image["crop_id"]).sha256 == image["sha256"]
        )
        with pytest.raises(MasterDirectVisualScanError) as caught:
            reader.question_crop(node, image["crop_id"])
        assert caught.value.code == "master_direct_scan_crop_role_denied"
        assert caught.value.status == 403


def test_answer_route_rejects_other_nodes_and_comparison_or_boundary_images(reader):
    snapshot = reader._snapshot()
    node = module.EXPECTED_ATOMIC_IDS[0]
    other_answer = reader.detail(module.EXPECTED_ATOMIC_IDS[1])[
        "reference_answer_images"
    ][0]
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.teacher_answer_crop(node, other_answer["crop_id"])
    assert caught.value.code == "teacher_answer_crop_not_found"
    comparison = next(
        crop for crop in snapshot.crop_by_id.values() if crop["role"] == "unknown"
    )
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.teacher_answer_crop(node, comparison["crop_id"])
    assert caught.value.status == 404
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.question_crop(node, comparison["crop_id"])
    assert caught.value.status == 403
    question = reader.detail(node)["evidence_descriptors"][0]
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.teacher_answer_crop(node, question["crop_id"])
    assert caught.value.status == 404


@pytest.mark.parametrize("value", ["../Q01", "C:\\question.png", "source A", ""])
def test_identifiers_cannot_be_paths(reader, value):
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.detail(value)
    assert caught.value.code == "master_direct_scan_invalid_identifier"
    assert caught.value.status == 400


@pytest.mark.parametrize(
    "kind", ["manifest", "records", "classification", "source", "crop"]
)
def test_bound_inputs_fail_closed_on_changed_bytes(reader, inputs, monkeypatch, kind):
    manifest = inputs[0]
    paths = {
        "manifest": MANIFEST,
        "records": (module.PRODUCT_RELATIVE / "question_candidates.jsonl").as_posix(),
        "classification": (
            module.PRODUCT_RELATIVE / "classification_evidence.jsonl"
        ).as_posix(),
        "source": manifest["source_assets"][0]["asset"],
        "crop": manifest["crops"][0]["asset"],
    }
    path = paths[kind]
    _patch_file(reader, monkeypatch, path, (DB / path).read_bytes() + b"changed")
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.catalog()
    assert caught.value.code == "master_direct_scan_binding_mismatch"


@pytest.mark.parametrize(
    "mutation",
    ["q7_tail_removed", "source_b_question", "source_b_answer", "missing_shared"],
)
def test_explicit_evidence_relations_reject_missing_or_mixed_source_parts(
    reader, inputs, mutation
):
    _, rows, evidence, classification, _, crops = deepcopy(inputs)
    if mutation == "q7_tail_removed":
        rows[6]["parts"][0]["source_variants"]["source_A"]["question_crop_refs"].pop()
    elif mutation == "source_b_question":
        variants = rows[0]["parts"][0]["source_variants"]
        variants["source_A"]["question_crop_refs"] = variants["source_B"][
            "question_crop_refs"
        ]
    elif mutation == "source_b_answer":
        answers = rows[0]["parts"][0]["answer_evidence"]["source_variants"]
        answers["source_A"]["evidence_refs"] = answers["source_B"]["evidence_refs"]
    else:
        evidence[0]["context_crop_refs"] = []
    with pytest.raises(MasterDirectVisualScanError):
        reader._relations(rows, evidence, classification, crops)


def test_project_rejects_numeric_difficulty_instead_of_converting_unknown(
    reader, inputs, master_snapshot
):
    manifest, rows, evidence, classification, _, crops = deepcopy(inputs)
    relations = reader._relations(rows, evidence, classification, crops)
    row = rows[0]
    row["parts"][0]["difficulty"]["factors"][0]["value"] = 2
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader._project(
            row,
            relations[0],
            crops,
            master_snapshot,
            1,
            manifest["theme_title_by_source"]["source_A"],
        )
    assert caught.value.code == "master_direct_scan_difficulty_invalid"


def test_upstream_gate_change_is_not_accepted_as_authority():
    with pytest.raises(MasterDirectVisualScanError) as caught:
        module._closed({"nested": [{"formal_promotion_allowed": True}]})
    assert caught.value.code == "master_direct_scan_gate_elevated"


def test_dependency_declaration_rejects_other_source_page_or_missing_route(
    reader, inputs
):
    _, rows, evidence, classification, _, crops = deepcopy(inputs)
    relation = reader._relations(rows, evidence, classification, crops)[6]
    descriptors = [
        reader._descriptor(crops[asset])
        for role in ("question", "shared_material")
        for asset in relation[role]
    ]
    dependency, proof = module._source_dependency(
        module.EXPECTED_ATOMIC_IDS[6], descriptors
    )
    assert dependency["prior_atomic_part_ids"] == []
    assert {binding["page"] for binding in proof["source_page_bindings"]} == {4, 5}
    assert all(
        binding["sha256"] == module._DEPENDENCY_SOURCE_SHA256[binding["page"]]
        for binding in proof["source_page_bindings"]
    )
    with pytest.raises(MasterDirectVisualScanError):
        module._source_dependency(module.EXPECTED_ATOMIC_IDS[6], descriptors[:-1])
    descriptors[0]["source_sha256"] = "b" * 64
    with pytest.raises(MasterDirectVisualScanError):
        module._source_dependency(module.EXPECTED_ATOMIC_IDS[6], descriptors)


def test_projection_is_detached_from_original_records(reader):
    node = module.EXPECTED_ATOMIC_IDS[0]
    changed = reader.detail(node)
    changed["source_variant_comparison"]["merged"] = True
    changed["scan_classification"]["knowledge_candidates"].clear()
    fresh = reader.detail(node)
    assert fresh["source_variant_comparison"]["merged"] is False
    assert fresh["scan_classification"]["knowledge_candidates"]


def test_presentation_failure_is_a_reader_error_not_partial_archive_fallback(
    reader, monkeypatch
):
    from integrations.deeptutor_shchem_v1 import fosinopril_source_presentation

    def changed(*args):
        raise fosinopril_source_presentation.FosinoprilPresentationError(
            "changed source"
        )

    monkeypatch.setattr(
        fosinopril_source_presentation, "apply_source_a_presentation", changed
    )
    with pytest.raises(MasterDirectVisualScanError) as caught:
        reader.detail(module.EXPECTED_ATOMIC_IDS[6])
    assert caught.value.code == "fosinopril_presentation_binding_invalid"

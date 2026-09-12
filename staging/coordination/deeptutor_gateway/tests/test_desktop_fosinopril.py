"""Real source-A native browse/assembly integration; no renderer or provider.

Temporary state belongs to these tests. Frozen question/answer files are read
only, and evidence flags are never promoted to human or official approval.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import (
    level_exam2025_theme3_fosinopril_direct_visual_scan as fosinopril,
)
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopWorkbenchFacade,
    _canonical_digest,
)
from integrations.deeptutor_shchem_v1.desktop_library_session import (
    snapshot_reader_graph,
)
from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import (
    PaperComposerModel,
)
from integrations.deeptutor_shchem_v1.master_direct_visual_scan import (
    MasterDirectVisualScanReader,
)
from integrations.deeptutor_shchem_v1.paper_export_workbench import _prepare_catalog
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    build_assembly_blueprint,
    default_shanghai_theme_preset,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader

ROOT = Path(__file__).resolve().parents[4]
DB = ROOT / "sh-chem-db"
PACK = DB / fosinopril.PRODUCT_RELATIVE
pytestmark = pytest.mark.skipif(
    not (PACK / "candidate_manifest.json").is_file(),
    reason="requires the local pinned dual-source fosinopril candidate package",
)


def _hashes(paths):
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    source_files = tuple(
        PACK / name
        for name in (
            "candidate_manifest.json",
            "question_candidates.jsonl",
            "evidence_map.jsonl",
            "classification_evidence.jsonl",
        )
    )
    before = _hashes(source_files)
    source_title = json.loads(
        (PACK / "candidate_manifest.json").read_text(encoding="utf-8-sig")
    )["theme_title_by_source"]["source_A"]
    source_rows = {
        row["parts"][0]["part_id"]: row
        for line in (PACK / "question_candidates.jsonl").read_text(
            encoding="utf-8-sig"
        ).splitlines()
        if line.strip()
        for row in [json.loads(line)]
    }
    direct = MasterDirectVisualScanReader(DB, include_archived_candidates=True)
    themes = ThemeWorkbenchReader(
        DB, master_workbench=direct.master_workbench, direct_scans=direct
    )
    direct, themes, legacy = snapshot_reader_graph(
        (direct, themes, MasterDirectVisualScanReader(DB))
    )
    state = tmp_path_factory.mktemp("fosinopril-native") / "isolated-state"
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=state),
        theme_reader=themes,
        master_direct_reader=direct,
        master_workbench_reader=direct.master_workbench,
        provider_store=SimpleNamespace(list_metadata=list),
    )
    try:
        catalog = facade.paper_theme_catalog("master")
        identity = _canonical_digest(
            {"scope": "master", "paper": fosinopril.PAPER_ID, "theme": fosinopril.THEME_ID}
        )
        card = next(
            card
            for card in facade.search_themes(scope="master", limit=50).cards
            if card.source_identity_sha256 == identity
        )
        detail = facade.library_theme_detail(card)
        group = next(
            group
            for paper in catalog["papers"]
            for group in paper["theme_groups"]
            if group["theme"]["id"] == fosinopril.THEME_ID
        )
        scans = {node: direct.detail(node) for node in fosinopril.EXPECTED_ATOMIC_IDS}
        yield SimpleNamespace(
            facade=facade,
            direct=direct,
            legacy=legacy,
            catalog=catalog,
            card=card,
            detail=detail,
            group=group,
            scans=scans,
            sources=source_rows,
            source_title=source_title,
        )
    finally:
        facade.shutdown()
        assert _hashes(source_files) == before


def _selection(catalog, unit="theme", node=None):
    return {
        "scope": "master",
        "selection_unit": unit,
        "theme_id": fosinopril.THEME_ID,
        "target_atomic_id": node,
        "expected_data_snapshot_id": catalog["data_snapshot_id"],
    }


def _blueprint(catalog, selection):
    return build_assembly_blueprint(
        [selection],
        theme_loader=lambda scope, snapshot_id: _prepare_catalog(
            catalog, scope=scope, snapshot_id=snapshot_id
        ),
        expected_data_snapshot_id=catalog["data_snapshot_id"],
        preset=default_shanghai_theme_preset(),
    )


def test_native_262_fourteen_products_keep_legacy_224_ten_and_master_identity(native):
    catalog = native.direct.catalog()
    legacy = native.legacy.catalog()
    assert catalog["count"] == 262 and len(catalog["products"]) == 14
    assert legacy["count"] == 224 and len(legacy["products"]) == 10
    assert set(legacy["master_node_ids"]) < set(catalog["master_node_ids"])
    assert set(fosinopril.EXPECTED_ATOMIC_IDS) <= set(catalog["master_node_ids"])
    assert not set(fosinopril.EXPECTED_ATOMIC_IDS) & set(legacy["master_node_ids"])
    product = next(
        item for item in catalog["products"] if item["product_id"] == fosinopril.PRODUCT_ID
    )
    assert product["count"] == 9 and product["paper_id"] == fosinopril.PAPER_ID
    assert catalog["coverage"]["direct_master_visual_scanned"] == 262
    assert catalog["coverage"]["visual_scanned_master_atomic"] == 169 + 262
    assert catalog["coverage"]["remaining_unscanned"] == 470 - 169 - 262
    assert native.catalog["counts"]["atomic_parts"] == 470
    assert native.catalog["counts"]["display_atomic_units"] == 480
    assert native.facade._themes.direct_scans is native.direct
    assert native.facade._paper_export_readers()[3] is native.direct


def test_source_a_nine_questions_ten_images_one_shared_route_reach_native_preview(native):
    assert native.card.atomic_total == native.card.display_atomic_units == 9
    assert native.group["theme"]["title"] == native.source_title
    assert native.detail.title_zh == native.source_title
    assert [part.key for part in native.detail.parts] == list(fosinopril.EXPECTED_ATOMIC_IDS)
    assert len(native.detail.shared_images) == 1
    assert sum(len(part.question_images) for part in native.detail.parts) == 10
    assert [len(part.question_images) for part in native.detail.parts] == [1] * 6 + [2, 1, 1]
    for part in native.detail.parts:
        prompt = native.sources[part.key]["parts"][0]["source_variants"]["source_A"]
        assert part.summary_zh == prompt["prompt_raw"]
        assert part.requirement_zh == prompt["prompt_raw"]
        assert not part.availability_zh
        assert native.scans[part.key]["source_variant"] == "source_A"
        for image in part.question_images:
            assert image.role == "question"
            raw = native.facade.library_image(image)
            assert hashlib.sha256(raw).hexdigest() == image.sha256
            assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    shared = native.detail.shared_images[0]
    assert shared.role == "shared_material"
    assert hashlib.sha256(native.facade.library_image(shared)).hexdigest() == shared.sha256


def test_nine_teacher_answer_previews_three_required_images_never_enter_question_list(native):
    assert sum(len(part.answer_images) for part in native.detail.parts) == 9
    required = set()
    question_hashes = {
        image.sha256
        for part in native.detail.parts
        for image in part.question_images
    } | {image.sha256 for image in native.detail.shared_images}
    for part in native.detail.parts:
        scan = native.scans[part.key]
        answer = scan["reference_answer"]
        assert answer["availability"] == "present_part_aligned"
        assert answer["source_authority"] == "nonofficial_reference"
        descriptors = scan["reference_answer_images"]
        assert len(descriptors) == len(part.answer_images) == 1
        descriptor = descriptors[0]
        if descriptor["display_mode"] == "inline_required":
            required.add(part.key)
        else:
            assert descriptor["display_mode"] == "preview_only"
        assert descriptor["access"] == "teacher_reference_answer_only"
        assert descriptor["evidence_role"] == "answer"
        assert part.answer_images[0].sha256 not in question_hashes
        raw = native.facade.library_answer_image(part.answer_images[0])
        assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
        assert raw.startswith(b"\x89PNG\r\n\x1a\n")
    assert required == {f"LE2025-S3-Q{number:02}-P1" for number in (5, 7, 9)}


def test_source_b_remains_comparison_and_its_difference_notes_are_teacher_visible(native):
    for part in native.detail.parts:
        source = native.sources[part.key]
        scan = native.scans[part.key]
        comparison = scan["source_variant_comparison"]
        assert comparison["default_source_variant"] == "source_A"
        assert comparison["merged"] is False
        assert set(comparison["prompt_by_source"]) == {"source_A", "source_B"}
        notes = "\n".join(part.quality_notes_zh)
        for conflict in source["source_conflicts_and_boundaries"]:
            assert conflict["resolution_note"] in notes
        answer_a = source["parts"][0]["answer_evidence"]["source_variants"]["source_A"]
        if not answer_a["visual_only"]:
            assert part.reference_answer_zh == answer_a["value"]
    q3 = native.scans["LE2025-S3-Q03-P1"]
    assert q3["scan_classification"]["item_type"] == q3[
        "source_variant_comparison"
    ]["prompt_by_source"]["source_A"]["item_type"]


def test_source_reviewed_shared_route_dependencies_survive_native_catalog(native):
    route = native.detail.shared_images[0].crop_id
    assert [row["atomic_part_id"] for row in native.group["atomic_chain"]] == list(
        fosinopril.EXPECTED_ATOMIC_IDS
    )
    for row in native.group["atomic_chain"]:
        scan = native.scans[row["atomic_part_id"]]
        hierarchy = scan["scan_hierarchy"]
        assert row["printed_sequence"] == hierarchy["printed_sequence"]
        assert row["atomic_sequence_in_printed"] == hierarchy["atomic_sequence_in_printed"]
        assert str(row["printed_question_number"]) == str(hierarchy["printed_question_number"])
        assert row["dependency"]["kind"] == "shared_material_only"
        assert row["dependency"]["prior_atomic_part_ids"] == []
        assert scan["dependency"]["prior_atomic_part_ids"] == []
        assert scan["dependency"]["shared_material_crop_ids"] == [route]
        assert scan["dependency"]["status"] == "source_page_backed_candidate_dependency"
        evidence = scan["dependency_evidence"]
        assert evidence["source_variant"] == "source_A"
        assert evidence["candidate_only"] is True and evidence["human_checked"] is False
        assert evidence["source_page_bindings"]
        assert evidence["required_shared_material_crop_ids"] == [route]
        assert scan["cognitive_difficulty"]["cognitive_prelabel"] is None


@pytest.mark.parametrize("unit", ["atomic", "dependency"])
def test_q7_selection_keeps_route_without_inventing_prior_questions(native, unit):
    node = "LE2025-S3-Q07-P1"
    blueprint = _blueprint(native.catalog, _selection(native.catalog, unit, node))
    assert blueprint["status"] == "ready_for_content_resolution"
    assert blueprint["blockers"] == []
    theme = blueprint["theme_bundles"][0]
    assert theme["final_atomic_ids"] == [node]
    assert theme["auto_added_dependency_ids"] == []
    assert theme["integrity"]["dependency_closure_complete"] is True
    assert [item["material_id"] for item in theme["shared_materials"]] == [
        native.detail.shared_images[0].crop_id
    ]


def test_whole_theme_blueprint_preserves_nine_units_and_one_route(native):
    result = _blueprint(native.catalog, _selection(native.catalog))
    assert result["status"] == "ready_for_content_resolution"
    assert result["blockers"] == []
    assert result["counts"] == {
        "theme_count": 1,
        "printed_question_count": 9,
        "atomic_part_count": 9,
        "shared_material_count": 1,
    }
    assert result["theme_bundles"][0]["final_atomic_ids"] == list(fosinopril.EXPECTED_ATOMIC_IDS)


def test_native_basket_composer_and_export_request_keep_scores_hidden_and_zero_extra_lines(native):
    facade = native.facade
    facade.add_theme_to_basket(native.card)
    composer = PaperComposerModel.from_basket(
        facade.basket(),
        catalog=native.catalog,
        mode="daily_practice",
        title="福辛普利中间体专题练习",
        show_question_scores=False,
    )
    assert len(composer.themes) == 1
    questions = composer.themes[0].questions
    assert [question.key for question in questions] == list(fosinopril.EXPECTED_ATOMIC_IDS)
    for question in questions:
        question.score = 2
        question.answer_space = 0
    preview = composer.make_preview()
    assert preview["show_question_scores"] is False
    rows = preview["themes"][0]["questions"]
    assert len(rows) == 9
    assert all(row["score"] == 2 and row["answer_space"] == 0 for row in rows)
    assert [row["source_ref"]["atomic_part_id"] for row in rows] == list(
        fosinopril.EXPECTED_ATOMIC_IDS
    )
    request = facade._paper_export_request(
        {"payload": {}, "preview_model": preview}, [_selection(native.catalog)]
    )
    assert request["show_question_scores"] is False
    assert request["answer_space_lines"] == 0
    assert request["atomic_settings"] == {
        node: {"score": 2, "answer_space_lines": 0}
        for node in fosinopril.EXPECTED_ATOMIC_IDS
    }

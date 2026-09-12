"""Pinned-source native ZnS preview and content-plan regression, without a provider.

Only temporary test state/assets are written. These tests do not render Word,
approve classroom use, or modify the source/candidate/master records.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import (
    fudan2026_april_theme5_zns_direct_visual_scan as zns,
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
from integrations.deeptutor_shchem_v1.paper_export_renderer import (
    _printed_question_blocks,
)
from integrations.deeptutor_shchem_v1.paper_export_workbench import (
    _bind_blueprint_shared_materials_to_selected_details,
    _prepare_catalog,
    _preset_for_request,
    _theme_rows,
    _WorkbenchContentResolver,
)
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    build_assembly_blueprint,
    build_document_plans,
    default_shanghai_theme_preset,
)
from integrations.deeptutor_shchem_v1.theme_workbench import ThemeWorkbenchReader

ROOT = Path(__file__).resolve().parents[4]
DB = ROOT / "sh-chem-db"
PACK = DB / zns.PRODUCT_RELATIVE
pytestmark = pytest.mark.skipif(
    not (PACK / "candidate_manifest.json").is_file(),
    reason="requires the local hash-bound Fudan April ZnS source package",
)


def _hashes(paths):
    return {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


@pytest.fixture(scope="module")
def native(tmp_path_factory):
    manifest = json.loads(
        (PACK / "candidate_manifest.json").read_text(encoding="utf-8-sig")
    )
    source_files = {
        PACK / "candidate_manifest.json",
        PACK / "question_candidates.jsonl",
        *(DB / row["asset"] for row in manifest["source_assets"]),
        *(DB / row["asset"] for row in manifest["crops"]),
    }
    before = _hashes(source_files)
    direct = MasterDirectVisualScanReader(DB, include_archived_candidates=True)
    themes = ThemeWorkbenchReader(
        DB, master_workbench=direct.master_workbench, direct_scans=direct
    )
    direct, themes = snapshot_reader_graph((direct, themes))
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(
            ROOT, state_root=tmp_path_factory.mktemp("zns-native")
        ),
        theme_reader=themes,
        master_direct_reader=direct,
        master_workbench_reader=direct.master_workbench,
        provider_store=SimpleNamespace(list_metadata=list),
    )
    try:
        catalog = facade.paper_theme_catalog("master")
        key = _canonical_digest(
            {"scope": "master", "paper": zns.PAPER_ID, "theme": zns.THEME_ID}
        )
        card = next(
            card
            for card in facade.search_themes(scope="master", limit=50).cards
            if card.source_identity_sha256 == key
        )
        detail = facade.library_theme_detail(card)
        group = next(
            group
            for paper in catalog["papers"]
            for group in paper["theme_groups"]
            if group["theme"]["id"] == zns.THEME_ID
        )
        scans = {node: direct.detail(node) for node in zns.EXPECTED_ATOMIC_IDS}
        yield SimpleNamespace(
            facade=facade,
            direct=direct,
            catalog=catalog,
            card=card,
            detail=detail,
            group=group,
            scans=scans,
        )
    finally:
        facade.shutdown()
        assert _hashes(source_files) == before


def _selection(native, unit="theme", node=None):
    return {
        "scope": "master",
        "selection_unit": unit,
        "theme_id": zns.THEME_ID,
        "target_atomic_id": node,
        "expected_data_snapshot_id": native.catalog["data_snapshot_id"],
    }


def _blueprint(native, *, unit="theme", node=None, preset=None):
    return build_assembly_blueprint(
        [_selection(native, unit, node)],
        theme_loader=lambda scope, snapshot_id: _prepare_catalog(
            native.catalog, scope=scope, snapshot_id=snapshot_id
        ),
        expected_data_snapshot_id=native.catalog["data_snapshot_id"],
        preset=preset or default_shanghai_theme_preset(),
    )


def test_native_preview_contains_eight_printed_nine_atomic_and_six_shared_images(
    native,
):
    matches = native.facade.search_themes(scope="master", query="ZnS", limit=50)
    assert native.card.source_identity_sha256 in {
        card.source_identity_sha256 for card in matches.cards
    }
    assert tuple(part.key for part in native.detail.parts) == zns.EXPECTED_ATOMIC_IDS
    assert len(native.detail.shared_images) == 6
    assert native.card.atomic_total == native.card.display_atomic_units == 9
    assert (
        len({row["printed_question_id"] for row in native.group["atomic_chain"]}) == 8
    )
    assert native.catalog["counts"]["atomic_parts"] == 470
    assert native.catalog["counts"]["display_atomic_units"] == 480
    q41 = native.detail.parts[1:3]
    assert [(image.crop_id, image.sha256) for image in q41[0].question_images] == [
        (image.crop_id, image.sha256) for image in q41[1].question_images
    ]
    assert [image.sha256 for image in q41[0].answer_images] == [
        image.sha256 for image in q41[1].answer_images
    ]
    question_hashes = set()
    for descriptor in (
        *native.detail.shared_images,
        *(image for part in native.detail.parts for image in part.question_images),
    ):
        assert descriptor.role in {"question", "shared_material"}
        data = native.facade.library_image(descriptor)
        assert data.startswith(b"\x89PNG\r\n\x1a\n")
        assert hashlib.sha256(data).hexdigest() == descriptor.sha256
        question_hashes.add(descriptor.sha256)
    for part in native.detail.parts:
        assert len(part.question_images) == len(part.answer_images) == 1
        for descriptor in part.answer_images:
            data = native.facade.library_answer_image(descriptor)
            assert hashlib.sha256(data).hexdigest() == descriptor.sha256
            assert descriptor.sha256 not in question_hashes


def test_nonofficial_answers_and_points_survive_without_invented_difficulty(native):
    assert [
        native.scans[node]["reference_answer"]["reference_points"]
        for node in zns.EXPECTED_ATOMIC_IDS
    ] == [2, 1, 2, 2, 2, 2, 2, 2, 2]
    for scan in native.scans.values():
        assert scan["answer_boundary"]["authority"] == "nonofficial_reference"
        assert scan["answer_boundary"]["verified"] is False
        assert scan["cognitive_difficulty"]["cognitive_prelabel"] is None
        assert scan["authority"]["publication_allowed"] is False
        assert scan["authority"]["formal_promotion_allowed"] is False
        assert scan["dependency"]["prior_atomic_part_ids"] == []
        assert scan["dependency"]["shared_material_crop_ids"]
        assert (
            scan["reference_answer_images"][0]["access"]
            == "teacher_reference_answer_only"
        )


@pytest.mark.parametrize("number", list(range(40, 48)))
def test_single_question_keeps_its_source_material_without_unrelated_prior_questions(
    native, number
):
    node = f"FD2026-APR-S5-Q{number}-P1"
    blueprint = _blueprint(native, unit="atomic", node=node)
    assert blueprint["status"] == "ready_for_content_resolution"
    assert blueprint["blockers"] == []
    blueprint = _bind_blueprint_shared_materials_to_selected_details(
        blueprint, {node: native.scans[node]}
    )
    bundle = blueprint["theme_bundles"][0]
    assert bundle["final_atomic_ids"] == [node]
    assert bundle["auto_added_dependency_ids"] == []
    assert {material["material_id"] for material in bundle["shared_materials"]} == set(
        native.scans[node]["dependency"]["shared_material_crop_ids"]
    )


def test_whole_theme_content_plan_preserves_q41_once_and_teacher_scoring(
    native, tmp_path
):
    scores = dict(
        zip(zns.EXPECTED_ATOMIC_IDS, [2, 1, 2, 2, 2, 2, 2, 2, 2], strict=True)
    )
    request = {
        "show_question_scores": False,
        "numbering_mode": "continuous_across_paper",
        "duration_minutes": 40,
        "answer_space_lines": 0,
    }
    preset = _preset_for_request(request, theme_count=1, total_score=17)
    blueprint = _blueprint(native, preset=preset)
    assert blueprint["counts"] == {
        "theme_count": 1,
        "printed_question_count": 8,
        "atomic_part_count": 9,
        "shared_material_count": 6,
    }
    blueprint = _bind_blueprint_shared_materials_to_selected_details(
        blueprint, native.scans
    )
    wrapper = _prepare_catalog(
        native.catalog, scope="master", snapshot_id=native.catalog["data_snapshot_id"]
    )
    rows = _theme_rows(wrapper["catalog"], {zns.THEME_ID})
    resolver = _WorkbenchContentResolver(
        scope="master",
        rows=rows,
        details=native.scans,
        crop_loader=native.direct.question_crop,
        answer_crop_loader=native.direct.teacher_answer_crop,
        asset_root=tmp_path / "assets",
        score_per_atomic=2,
        default_answer_lines=0,
        atomic_settings={
            node: {"score": score, "answer_space_lines": 0}
            for node, score in scores.items()
        },
    )
    plans = build_document_plans(
        blueprint,
        preset=preset,
        content_resolver=resolver,
        paper_metadata={
            "title_zh": "ZnS专题练习",
            "subtitle_zh": None,
            "version_label_zh": "本地测试",
        },
    )
    for audience in ("student", "teacher"):
        section = plans[audience]["visible"]["theme_sections"][0]
        printed = section["printed_questions"]
        assert len(printed) == 8
        atoms = [atom for question in printed for atom in question["atomic_parts"]]
        assert sum(atom["score"] for atom in atoms) == 17
        assert all(atom["answer_space"]["lines"] == 0 for atom in atoms)
        q41 = printed[1]["atomic_parts"]
        assert [atom["score"] for atom in q41] == [1, 2]
        blocks, suppressed = _printed_question_blocks(q41)
        assert suppressed and len(blocks[0]) == 1 and blocks[1] == []
        if audience == "student":
            assert all("teacher_notes" not in atom for atom in atoms)
        else:
            assert all(
                "非官方" in atom["teacher_notes"]["answer_label_zh"] for atom in atoms
            )
            answer_blocks = [
                atom["teacher_notes"]["source_reference_answer"].get(
                    "content_blocks", []
                )
                for atom in atoms
            ]
            assert [len(blocks) for blocks in answer_blocks] == [
                0,
                0,
                1,
                0,
                0,
                0,
                0,
                0,
                0,
            ]
            assert (
                Path(answer_blocks[2][0]["asset_ref"]).stem
                == native.detail.parts[2].answer_images[0].sha256
            )
    assert preset["student_version"]["show_item_scores"] is False


def test_basket_and_preview_keep_explicit_scores_and_no_redundant_answer_lines(native):
    facade = native.facade
    facade.add_theme_to_basket(native.card)
    composer = PaperComposerModel.from_basket(
        facade.basket(),
        catalog=native.catalog,
        mode="daily_practice",
        title="ZnS专题练习",
        show_question_scores=False,
    )
    assert len(composer.themes) == 1 and len(composer.themes[0].questions) == 9
    for question, score in zip(
        composer.themes[0].questions, [2, 1, 2, 2, 2, 2, 2, 2, 2], strict=True
    ):
        question.score, question.answer_space = score, 0
    preview = composer.make_preview()
    request = facade._paper_export_request(
        {"payload": {}, "preview_model": preview}, [_selection(native)]
    )
    assert request["show_question_scores"] is False
    assert request["answer_space_lines"] == 0
    assert (
        sum(setting["score"] for setting in request["atomic_settings"].values()) == 17
    )
    assert request["atomic_settings"]["FD2026-APR-S5-Q41-P1"]["score"] == 1
    assert request["atomic_settings"]["FD2026-APR-S5-Q41-P2"]["score"] == 2

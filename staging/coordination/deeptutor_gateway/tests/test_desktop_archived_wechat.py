"""Opt-in, real local native integration; no model or real personal state."""

import os
from pathlib import Path

import pytest

from staging.coordination.deeptutor_gateway.scripts.verify_archived_wechat_native import (
    EXPECTED_ATOMS,
    EXPECTED_KNOWN_DEPENDENCIES,
    EXPECTED_PRIOR_IDS,
    EXPECTED_SHARED_IDS,
    audit_archived,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("SHCHEM_RUN_ARCHIVED_WECHAT_TESTS") != "1",
    reason="set SHCHEM_RUN_ARCHIVED_WECHAT_TESTS=1 for bounded pinned-source native integration",
)


@pytest.fixture(scope="module")
def audited(tmp_path_factory):
    workspace = Path(__file__).resolve().parents[4]
    isolated = tmp_path_factory.mktemp("archived-wechat-native")
    return audit_archived(workspace, isolated / "state")


def test_native_delta_20_and_legacy_224_contract_stay_separate(audited):
    assert audited["counts"]["native_direct"] == 244
    assert audited["counts"]["legacy_direct"] == 224
    assert audited["counts"]["legacy_products"] == 10
    assert audited["counts"]["native_products"] == 12
    assert {
        part["atomic_id"] for theme in audited["themes"] for part in theme["parts"]
    } == EXPECTED_ATOMS


def test_three_native_themes_open_with_original_text_answers_and_all_images(audited):
    assert [theme["atomic_parts"] for theme in audited["themes"]] == [9, 6, 5]
    assert audited["counts"]["question_image_occurrences"] == 20
    assert audited["counts"]["shared_image_occurrences"] == 5
    assert audited["counts"]["distinct_display_images"] == 25
    assert audited["checks"]["original_prompt_answer_equal"]
    assert audited["checks"]["pixels_match_bound_presentation"]
    assert audited["checks"]["presentation_repairs_verified"]
    assert audited["counts"]["presentation_repaired_images"] == 9
    assert audited["counts"]["question_presentation_repaired_images"] == 7
    assert audited["counts"]["question_margin_repaired_images"] == 2
    assert audited["counts"]["shared_presentation_repaired_images"] == 2
    assert [theme["shared_material_count"] for theme in audited["themes"]] == [3, 1, 1]
    for theme in audited["themes"]:
        assert {
            image["crop_id"]
            for image in theme["images"]
            if image["role"] == "shared_material"
        } == EXPECTED_SHARED_IDS[theme["theme_id"]]


def test_unknown_roles_difficulty_and_six_dependencies_are_not_filled(audited):
    unknown = []
    for theme in audited["themes"]:
        for part in theme["parts"]:
            assert part["primary_K"] is None and part["supporting_K"] == []
            assert part["cognitive_prelabel"] is None
            assert part["knowledge_candidates_K"]
            if part["atomic_id"] not in EXPECTED_KNOWN_DEPENDENCIES:
                unknown.append(part)
                assert part["dependency"] == "blocked_pending_review"
                assert (
                    part["dependency_status"] == "unknown_prior_dependency_not_recorded"
                )
                assert part["prior_atomic_part_ids"] == []
                assert part["dependency_evidence"] is None
    assert len(unknown) == audited["counts"]["unknown_dependencies"] == 6


def test_fourteen_page_backed_dependencies_preserve_prior_edges_and_candidate_flags(
    audited,
):
    known = [
        part
        for theme in audited["themes"]
        for part in theme["parts"]
        if part["atomic_id"] in EXPECTED_KNOWN_DEPENDENCIES
    ]
    assert len(known) == audited["counts"]["source_backed_candidate_dependencies"] == 14
    for part in known:
        prior = EXPECTED_PRIOR_IDS.get(part["atomic_id"], [])
        assert part["dependency"] == (
            "one_prior_part" if prior else "shared_material_only"
        )
        assert part["prior_atomic_part_ids"] == prior
        assert part["dependency_status"] == "source_page_backed_candidate_dependency"
        evidence = part["dependency_evidence"]
        assert evidence["status"] == "source_page_visual_inspection_candidate"
        assert evidence["candidate_only"] is True
        assert evidence["human_checked"] is False
        assert evidence["source_page_bindings"]
        assert (
            evidence["required_shared_material_crop_ids"]
            == part["required_shared_material_crop_ids"]
        )
        assert len(part["required_shared_material_crop_ids"]) == 1


def test_all_52_archives_remain_unchanged_and_two_full_pages_stay_archive_only(audited):
    assert (
        audited["counts"]["archived_crop_count"]
        == len(audited["archived_crop_sha256"])
        == 52
    )
    assert audited["counts"]["archive_only_shared_images"] == 2
    assert audited["checks"]["archive_only_shared_preserved_not_displayed"]
    assert audited["checks"]["unrevised_images_match_archived_sha256"]
    for theme in audited["themes"]:
        for image in theme["images"]:
            assert (
                image["archived_crop_sha256"]
                == audited["archived_crop_sha256"][image["crop_id"]]
            )
            if image["presentation_revision_id"] is None:
                assert image["sha256"] == image["archived_crop_sha256"]
            else:
                assert (
                    image["presentation_revision_id"]
                    == "archived-wechat-source-recrop-20260910-r3"
                )


def test_pending_cards_are_hidden_without_losing_wave1(audited):
    assert audited["checks"]["pending_search_zero_cards"]
    assert audited["checks"]["pending_parts"] == 43
    assert audited["checks"]["wave1_themes"] == 25


def test_shared_materials_and_source_references_survive_isolated_composer(audited):
    assert audited["checks"]["isolated_basket_composer_preserved"]
    assert audited["checks"]["composer_theme_count"] == 3
    assert audited["checks"]["composer_atomic_count"] == 20


def test_schema_and_readonly_boundaries_do_not_become_teaching_approval(audited):
    assert audited["checks"]["legacy_full_theme_schema"]
    assert audited["checks"]["native_theme_structure_schema"]
    assert audited["checks"]["read_phase_personal_unchanged"]
    assert audited["checks"]["bound_sources_unchanged"]
    assert audited["checks"]["source_backed_dependencies_remain_candidate"]
    assert audited["checks"]["unknown_dependencies_preserved"]
    assert audited["counts"]["bound_source_files"] > 20
    assert audited["model_calls"] == audited["source_writes"] == 0
    assert not audited["real_personal_state_accessed"]
    assert not audited["chemistry_approved"] and not audited["teacher_reviewed"]
    assert not audited["publication_allowed"]

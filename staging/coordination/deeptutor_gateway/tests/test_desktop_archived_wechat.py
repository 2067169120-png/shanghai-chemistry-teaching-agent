"""Opt-in, real local native integration; no model or real personal state."""

import os
from pathlib import Path

import pytest

from staging.coordination.deeptutor_gateway.scripts.verify_archived_wechat_native import (
    EXPECTED_ATOMS,
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
    assert audited["counts"]["shared_image_occurrences"] > 0
    assert audited["checks"]["original_prompt_answer_equal"]
    assert audited["checks"]["pixels_match_bound_presentation"]
    assert audited["checks"]["presentation_repairs_verified"]
    assert audited["counts"]["presentation_repaired_images"] == 5
    assert all(theme["shared_material_count"] for theme in audited["themes"])


def test_unknown_roles_difficulty_and_dependencies_are_not_filled(audited):
    for theme in audited["themes"]:
        for part in theme["parts"]:
            assert part["primary_K"] is None and part["supporting_K"] == []
            assert part["cognitive_prelabel"] is None
            assert part["dependency"] == "blocked_pending_review"
            assert part["knowledge_candidates_K"]


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
    assert audited["counts"]["bound_source_files"] > 20
    assert audited["model_calls"] == audited["source_writes"] == 0
    assert not audited["real_personal_state_accessed"]
    assert not audited["chemistry_approved"] and not audited["teacher_reviewed"]
    assert not audited["publication_allowed"]

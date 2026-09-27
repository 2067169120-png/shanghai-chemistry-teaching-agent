"""Draft details evidence rules; all source structures are synthetic."""
from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.desktop_workbench.paper_composer import MixedPaperComposerModel
from runtime.deeptutor_shchem.paper_details_fixture import rows
from test_desktop_mixed_paper_service import setup as setup, _add_word, _core_item, _request


def model_for(source=None):
    model = MixedPaperComposerModel()
    model.merge({"schema_version": "shchem.desktop-mixed-basket.v1", "basket_sha256": "a" * 64,
                 "items": deepcopy(rows() if source is None else source)})
    return model


def test_mixed_structure_is_explicit_and_missing_scores_do_not_become_zero():
    model = model_for()
    before = deepcopy(model.__dict__)
    result = model.details(duration_minutes=45)
    assert model.__dict__ == before
    assert result["counts"]["themes"] == {"known": 2, "unknown_sections": 1, "total": None}
    assert result["counts"]["printed"]["known"] == 4
    assert result["counts"]["atomic"]["known"] == 5
    assert result["current_score"] == {"known": 13.5, "known_count": 5, "missing_count": 1, "total": None}
    assert result["source_score"] == {"known": 3, "known_count": 1, "missing_count": 5, "total": None}
    assert result["duration_minutes"] == 45
    assert result["estimated_minutes"] is None and result["target_coverage"] is None


def test_word_title_or_number_cannot_become_theme_or_original_score():
    source = rows()[1]
    source["title_zh"] = "第五主题（满分100分）第7题含3小问"
    source["content"].update(score=100, atomic_total=3, theme_count=1)
    result = model_for([source]).details(duration_minutes=90)["rows"][0]
    assert result["counts"] == {"themes": None, "printed": None, "atomic": None}
    assert result["source_score"]["known"] is None
    assert result["current_score"]["total"] == 4.5
    assert result["selected"]["question_blocks"] == [2, 3]
    assert result["source_ref"] == source["source_ref"]


def test_excluded_and_reordered_rows_and_atomic_overrides_follow_draft():
    model = model_for()
    model.remove("word-synthetic")
    model.move("visual-synthetic", -1)
    model.settings["core-synthetic"]["atomic_settings"] = {
        "A-1": {"score": 3, "answer_space_lines": 0},
        "A-2": {"score": 5, "answer_space_lines": 1},
        "A-3": {"score": 6, "answer_space_lines": 2},
    }
    result = model.details(duration_minutes=30)
    assert [row["key"] for row in result["rows"]] == ["visual-synthetic", "core-synthetic"]
    assert [row["number"] for row in result["rows"]] == [1, 2]
    assert result["excluded_count"] == 1
    assert result["current_score"]["known"] == 17
    assert result["counts"]["atomic"]["total"] == 5
    model.restore_excluded()
    assert model.details(duration_minutes=30)["current_score"]["known"] == 21.5


def test_empty_is_no_paper_not_a_zero_point_exam():
    model = model_for()
    for key in list(model.order):
        model.remove(key)
    result = model.details(duration_minutes=30)
    assert result["rows"] == [] and result["excluded_count"] == 3
    assert result["current_score"]["total"] is None
    assert result["source_score"]["known"] is None


def test_all_known_source_scores_and_scope_do_not_leak_other_items():
    source = rows()[2]
    source["content"]["source_scores"][1].update(status="present", max_score=5)
    result = model_for([source]).details(duration_minutes=40)
    assert result["current_score"]["total"] == result["source_score"]["total"] == 8
    assert result["counts"]["themes"]["total"] == 1
    assert result["rows"][0]["selected"]["atomic_ids"] == ["V-A1", "V-A2"]
    assert "人工复核" in result["rows"][0]["structure_evidence"]


@pytest.mark.parametrize("value", [None, 0, -1, True, float("nan"), float("inf"), "3", {"value": 3}])
def test_invalid_or_zero_source_score_is_unknown(value):
    source = rows()[2]
    source["content"]["source_scores"][0]["max_score"] = value
    result = model_for([source]).details(duration_minutes=30)
    assert result["current_score"]["total"] is None
    assert result["current_score"]["known"] is None
    assert result["current_score"]["missing_count"] == 2


@pytest.mark.parametrize("mutation", ["duplicate_atomic", "duplicate_printed", "foreign_score", "duplicate_score"])
def test_ambiguous_visual_identity_is_rejected(mutation):
    source = rows()[2]
    content = source["content"]
    if mutation == "duplicate_atomic":
        content["theme"]["printed_questions"][1]["atomic_parts"][0]["atomic_part_id"] = "V-A1"
    elif mutation == "duplicate_printed":
        content["theme"]["printed_questions"][1]["printed_question_id"] = "V-Q1"
    elif mutation == "foreign_score":
        content["source_scores"][0]["printed_question_id"] = "unselected"
    else:
        content["source_scores"].append(deepcopy(content["source_scores"][0]))
    with pytest.raises(ValueError):
        model_for([source]).details(duration_minutes=30)


def test_missing_printed_identity_keeps_atomic_count_but_never_guesses_from_number():
    source = rows()[0]
    for atom in source["content"]["atomic_chain"]:
        atom.pop("printed_question_id")
        atom["printed_question_number"] = "7"
    row = model_for([source]).details(duration_minutes=40)["rows"][0]
    assert row["counts"] == {"themes": 1, "printed": None, "atomic": 3}


@pytest.mark.parametrize("settings", [{"A-1": {"score": 5}}, {"foreign": {"score": 5}}])
def test_partial_or_foreign_atomic_settings_do_not_silently_apply_defaults(settings):
    model = model_for()
    model.settings["core-synthetic"]["atomic_settings"] = settings
    with pytest.raises(ValueError):
        model.details(duration_minutes=45)


def test_explicit_alias_units_match_export_count_ids_and_per_unit_score():
    from test_paper_export_alias_projection import catalog, parent, selection, unit
    from integrations.deeptutor_shchem_v1.paper_export_alias_projection import project_explicit_alias_units
    source = catalog([parent(aliases=[unit("W-1", 1), unit("W-2", 2, "W-1")])])
    group = source["papers"][0]["theme_groups"][0]
    export = project_explicit_alias_units(source, [selection()], scope="master")
    exported = export.catalog["papers"][0]["theme_groups"][0]["atomic_chain"]
    row = rows()[0]
    row["content"] = group
    row["source_ref"]["theme_id"] = group["theme"]["id"]
    model = model_for([row])
    model.settings[row["key"]]["atomic_settings"] = {
        atom["atomic_part_id"]: {"score": 3, "answer_space_lines": 0} for atom in exported}
    result = model.details(duration_minutes=40)["rows"][0]
    assert result["counts"]["atomic"] == 2
    assert result["selected"]["atomic_ids"] == [atom["atomic_part_id"] for atom in exported]
    assert result["current_score"]["total"] == 6


def test_unrelated_source_content_cannot_supply_original_score_or_time():
    source = rows()[0]
    source["content"].update(time_minutes=20, score=99, knowledge_points=["某目标"])
    source["content"]["atomic_chain"][0]["score"] = 100
    result = model_for([source]).details(duration_minutes=45)
    assert result["source_score"]["known"] is None
    assert result["estimated_minutes"] is result["target_coverage"] is None


@pytest.mark.parametrize("index", [0, 2])
def test_theme_source_binding_mismatch_is_not_a_current_statistic(index):
    source = rows()[index]
    source["source_ref"]["theme_id"] = "other-theme"
    with pytest.raises(ValueError):
        model_for([source]).details(duration_minutes=40)


def test_real_service_projection_and_preview_use_same_current_known_score(setup):
    service, state, words, _, _ = setup
    state.add_many_to_basket([_core_item("master"), _core_item("supplemental")])
    _add_word(service, words)
    projection = service.projection()
    before = state.path.read_bytes()
    model = MixedPaperComposerModel()
    model.merge(projection)
    details = model.details(duration_minutes=40)
    assert state.path.read_bytes() == before
    preview = service.create_preview(_request(service)).preview_model
    assert details["current_score"]["total"] == preview["stats"]["total_score"] == 10.5
    assert details["counts"]["themes"]["known"] == 2
    assert details["counts"]["atomic"]["known"] == 4
    assert details["source_score"]["known"] is None


def test_word_source_block_one_based_contract_is_kept():
    source = rows()[1]
    source["content"]["question_blocks"] = [{"index": 1}]
    assert model_for([source]).details(duration_minutes=40)["rows"][0]["selected"]["question_blocks"] == [1]
    source["content"]["question_blocks"] = [{"index": 0}]
    assert model_for([source]).details(duration_minutes=40)["rows"][0]["selected"]["question_blocks"] is None

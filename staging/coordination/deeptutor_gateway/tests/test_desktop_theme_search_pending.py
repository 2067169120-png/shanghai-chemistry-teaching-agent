from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.question_search_workbench import (
    QuestionSearchError,
    QuestionSearchWorkbench,
)
from staging.coordination.deeptutor_gateway.tests.test_question_search_workbench import (
    atomic,
    curriculum_projection,
    theme_projection,
)


def projection():
    value = theme_projection()
    value["unassigned_pending_review"]["atomic_chain"] = [
        atomic("A4", summary="待归属检索词", knowledge="K04", ability="A01"),
        atomic("A5", summary="另一个待归属", knowledge="K05", ability="A02"),
    ]
    return value


def test_native_pagination_totals_and_facets_exclude_pending_without_editing_source():
    value = projection()
    before = deepcopy(value)
    search = QuestionSearchWorkbench()
    payload = {"scope": "master", "limit": 1}
    first = search.search(
        payload, theme_loader=lambda _: value, complete_themes_only=True
    )
    assert first["counts"] == {
        "theme_cards_scanned": 2,
        "theme_cards_matched": 2,
        "atomic_parts_scanned": 3,
        "atomic_parts_matched": 3,
        "returned_theme_cards": 1,
    }
    assert first["pending_parentage"] == {
        "atomic_parts_in_scope": 2,
        "atomic_parts_matched": 2,
        "included_in_theme_totals": False,
    }
    assert {row["value"] for row in first["facets"]["K"]["values"]} == {"K10", "K17"}
    assert first["page"]["has_more"] is True
    second = search.search(
        {**payload, "cursor": first["page"]["next_cursor"]},
        theme_loader=lambda _: value,
        complete_themes_only=True,
    )
    assert len(second["items"]) == 1
    assert second["items"][0]["theme"]["id"] != first["items"][0]["theme"]["id"]
    assert second["page"]["total_theme_cards"] == 2
    assert second["page"]["has_more"] is False
    assert value == before


def test_pending_only_query_has_no_openable_cards_but_retains_match_notice():
    result = QuestionSearchWorkbench().search(
        {"scope": "master", "q": "待归属检索词"},
        theme_loader=lambda _: projection(),
        complete_themes_only=True,
    )
    assert result["items"] == []
    assert result["page"]["total_theme_cards"] == 0
    assert result["counts"]["atomic_parts_matched"] == 0
    assert result["pending_parentage"]["atomic_parts_matched"] == 1


def test_default_diagnostic_projection_is_unchanged_and_cursors_are_mode_bound():
    search = QuestionSearchWorkbench()
    value = projection()
    payload = {"scope": "master", "limit": 1}
    old = search.search(payload, theme_loader=lambda _: value)
    explicit_old = search.search(
        payload, theme_loader=lambda _: value, complete_themes_only=False
    )
    assert old == explicit_old
    assert old["page"]["total_theme_cards"] == 4
    assert "pending_parentage" not in old
    native = search.search(
        payload, theme_loader=lambda _: value, complete_themes_only=True
    )
    for source, mode in ((old, True), (native, False)):
        with pytest.raises(QuestionSearchError) as exc:
            search.search(
                {**payload, "cursor": source["page"]["next_cursor"]},
                theme_loader=lambda _: value,
                complete_themes_only=mode,
            )
        assert exc.value.code == "question_search_cursor_stale"


def test_curriculum_join_keeps_pending_members_until_full_validation():
    search = QuestionSearchWorkbench()
    payload = {"scope": "master", "curriculum": {"volume_id": "TB-E1"}}
    result = search.search(
        payload,
        theme_loader=lambda _: projection(),
        curriculum_loader=lambda _: curriculum_projection(("A1", "A4")),
        complete_themes_only=True,
    )
    assert result["counts"]["atomic_parts_matched"] == 1
    assert result["pending_parentage"]["atomic_parts_matched"] == 1
    with pytest.raises(QuestionSearchError) as exc:
        search.search(
            payload,
            theme_loader=lambda _: projection(),
            curriculum_loader=lambda _: curriculum_projection(("A1", "A999")),
            complete_themes_only=True,
        )
    assert exc.value.code == "question_search_curriculum_join_invalid"


def test_wave_themes_are_not_filtered_by_title_or_pending_word():
    value = theme_projection("wave1")
    value["papers"][0]["theme_groups"][0]["theme"]["title"] = "父链与pending测试主题"
    result = QuestionSearchWorkbench().search(
        {"scope": "wave1", "q": "父链"},
        theme_loader=lambda _: value,
        complete_themes_only=True,
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["group_kind"] == "theme"
    assert result["pending_parentage"]["atomic_parts_in_scope"] == 0


def test_partial_source_labels_retain_candidates_without_guessing_primary_or_difficulty():
    from integrations.deeptutor_shchem_v1.theme_workbench import (
        ThemeWorkbenchError,
        _scan_dependency,
        _scan_labels,
    )

    record = {
        "classification": {
            "label_status": "partial_source_candidate", "item_type": "short_fill",
            "primary_K": None, "supporting_K": [], "knowledge_candidates_K": ["K04", "K05"],
            "A": [], "C": [], "R": [], "RP": [],
        },
        "difficulty": {"cognitive_prelabel": None},
        "dependency": {
            "status": "unknown_prior_dependency_not_recorded",
            "prior_atomic_part_ids": [], "shared_material_crop_ids": ["M1"],
        },
    }
    labels = _scan_labels(record)
    assert labels["status"] == "pending"
    assert labels["cognitive_prelabel"] is labels["primary_K"] is None
    assert labels["supporting_K"] == []
    value = theme_projection()
    value["papers"][0]["theme_groups"][0]["atomic_chain"][0]["label_summary"] = labels
    result = QuestionSearchWorkbench().search(
        {"scope": "master", "filters": {"K": ["K04"]}},
        theme_loader=lambda _: value, complete_themes_only=True,
    )
    assert result["items"][0]["matched_atomic_ids"] == ["A1"]
    dependency = _scan_dependency(record, current_id="A1", positions={"A1": ("T1", 0)})
    assert dependency["kind"] == "blocked_pending_review"
    assert dependency["status"] == "blocked_unknown_not_inferred"
    del record["classification"]["label_status"]
    with pytest.raises(ThemeWorkbenchError):
        _scan_labels(record)

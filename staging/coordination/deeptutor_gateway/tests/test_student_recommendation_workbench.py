from __future__ import annotations

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.student_recommendation_workbench import (
    CLAIM_SCOPE,
    CONTRACT_VERSION,
    StudentRecommendationWorkbench,
    StudentRecommendationWorkbenchError,
)

SNAPSHOT = "a" * 64
TARGET_SECTION = "V1-C1:1.1"
SECOND_SECTION = "V1-C1:1.2"


def _catalog() -> dict:
    volumes = []
    section_index = 0
    # 5 volumes, 19 chapters, 60 sections.  The first three chapters have
    # four sections and the remaining sixteen have three.
    chapter_distribution = (4, 4, 4, 4, 3)
    chapter_global = 0
    for volume_index, chapter_count in enumerate(chapter_distribution, 1):
        chapters = []
        for chapter_in_volume in range(1, chapter_count + 1):
            chapter_global += 1
            section_count = 4 if chapter_global <= 3 else 3
            chapter_id = f"V{volume_index}-C{chapter_in_volume}"
            sections = []
            for number in range(1, section_count + 1):
                section_index += 1
                section_number = f"{chapter_in_volume}.{number}"
                sections.append(
                    {
                        "section_key": f"{chapter_id}:{section_number}",
                        "section_id": None,
                        "section_number": section_number,
                        "section_title": f"第{section_index}节",
                        "display_label_zh": f"{section_number} 第{section_index}节",
                    }
                )
            chapters.append(
                {
                    "chapter_id": chapter_id,
                    "chapter_title": f"第{chapter_global}章",
                    "sections": sections,
                }
            )
        volumes.append(
            {
                "volume_id": f"V{volume_index}",
                "volume_title": f"第{volume_index}册",
                "chapters": chapters,
            }
        )
    assert section_index == 60
    return {
        "data_snapshot_id": "CURRICULUM-TEST-V1",
        "counts": {"volumes": 5, "chapters": 19, "sections": 60},
        "volumes": volumes,
    }


def _match(
    index: int,
    *,
    atomic: str | None = None,
    theme: str | None = None,
    paper: str | None = None,
) -> dict:
    return {
        "match_id": f"match-{index}",
        "atomic_part_id": atomic or f"current-atomic-{index}",
        "theme_id": theme,
        "paper_id": paper,
    }


def _decision(
    sequence: int,
    match_id: str,
    score: float,
    *,
    maximum: float = 1.0,
    section_keys: list[str] | None = None,
    action: str = "accept",
) -> dict:
    return {
        "sequence": sequence,
        "match_id": match_id,
        "teacher_score": score,
        "maximum_score": maximum,
        "decision": action,
        "curriculum_section_keys": (
            list(section_keys) if section_keys is not None else [TARGET_SECTION]
        ),
    }


def _card(
    scope: str,
    suffix: str,
    *,
    paper_id: str | None = None,
    theme_id: str | None = None,
    atomic_ids: tuple[str, ...] | None = None,
    matched_atomic_ids: tuple[str, ...] | None = None,
) -> dict:
    paper_id = paper_id or f"paper-{suffix}"
    theme_id = theme_id or f"theme-{suffix}"
    atomic_ids = atomic_ids or (f"rec-{suffix}-1", f"rec-{suffix}-2")
    matched_atomic_ids = matched_atomic_ids or (atomic_ids[0],)
    return {
        "scope": scope,
        "group_kind": "theme",
        "display_title_zh": f"推荐主题{suffix}",
        "paper": {
            "id": paper_id,
            "title": f"推荐卷{suffix}",
            "source_metadata": {"year": "2026"},
        },
        "theme": {"id": theme_id, "title": f"推荐主题{suffix}", "sequence": 1},
        "source_metadata": {"question_bank_layer": scope},
        "counts": {
            "atomic_total": len(atomic_ids),
            "atomic_matched": len(matched_atomic_ids),
            "display_atomic_units": len(atomic_ids),
        },
        "shared_context": {"material_count": 1, "context_summary_zh": "共同材料"},
        "dependencies": {"explicit_prior_edge_count": 1},
        "matched_atomic_ids": list(matched_atomic_ids),
        "match_details": [
            {
                "atomic_part_id": atomic_id,
                "reason_codes": ["curriculum_explicit_mapping_match"],
            }
            for atomic_id in matched_atomic_ids
        ],
        "atomic_chain": [
            {
                "atomic_part_id": atomic_id,
                "printed_question_number": str(index),
            }
            for index, atomic_id in enumerate(atomic_ids, 1)
        ],
    }


def _search_response(scope: str, items: list[dict]) -> dict:
    return {
        "scope": scope,
        "data_snapshot_id": SNAPSHOT,
        "items": deepcopy(items),
        "authority": {"candidate_only": True, "read_only": True},
        "integrity": {
            "complete_theme_chain_returned": True,
            "dependency_context_preserved": True,
        },
    }


def _payload(
    *,
    matches: list[dict] | None = None,
    decisions: list[dict] | None = None,
    scopes: list[str] | None = None,
    exclusions: dict | None = None,
) -> dict:
    matches = matches or [_match(1)]
    decisions = decisions if decisions is not None else [_decision(1, "match-1", 0)]
    return {
        "submission_id": "SUB-test-1",
        "data_snapshot_id": SNAPSHOT,
        "matches": matches,
        "scoring_decisions": decisions,
        "exclusions": exclusions
        or {"atomic_part_ids": [], "theme_ids": [], "paper_ids": []},
        "scopes": scopes or ["master"],
        "limit_per_section": 5,
    }


def _preview(payload: dict, search):
    return StudentRecommendationWorkbench().preview(
        payload,
        curriculum_catalog_loader=_catalog,
        question_search_loader=search,
    )


def _default_search(payload: dict) -> dict:
    return _search_response(payload["scope"], [_card(payload["scope"], "default")])


def test_latest_teacher_decision_for_same_match_replaces_earlier_loss() -> None:
    calls = []
    payload = _payload(
        decisions=[
            _decision(1, "match-1", 0),
            _decision(2, "match-1", 1),
        ]
    )

    result = _preview(payload, lambda request: calls.append(request))

    assert result["contract_version"] == CONTRACT_VERSION
    assert result["claim_scope"] == CLAIM_SCOPE
    assert result["scoring_confirmation"] == "teacher_confirmed"
    assert result["evidence_sufficiency"] == "insufficient_evidence"
    assert result["diagnosis_status"] == "no_weakness_evidence"
    assert result["diagnoses"][0]["supporting_evidence"] == []
    assert len(result["diagnoses"][0]["counterevidence"]) == 1
    assert result["scope_groups"] == [{"scope": "master", "items": []}]
    assert calls == []


def test_three_lost_parts_in_one_visual_submission_remain_provisional() -> None:
    matches = [_match(index) for index in range(1, 4)]
    decisions = [_decision(index, f"match-{index}", 0) for index in range(1, 4)]

    result = _preview(_payload(matches=matches, decisions=decisions), _default_search)

    diagnosis = result["diagnoses"][0]
    assert result["diagnosis_status"] == "provisional_weakness"
    assert result["evidence_sufficiency"] == "insufficient_evidence"
    assert diagnosis["metrics"]["valid_atomic_part_count"] == 3
    assert diagnosis["metrics"]["independent_source_count"] == 1
    assert diagnosis["metrics"]["stable_gate_satisfied"] is False
    assert result["metrics"]["stable_weakness_allowed"] is False
    assert result["scope_groups"][0]["items"]
    assert any(
        blocker["code"] == "stable_weakness_requires_independent_sources"
        for blocker in result["blockers"]
    )


def test_full_score_is_preserved_as_counterevidence_for_a_weak_section() -> None:
    matches = [_match(1), _match(2)]
    decisions = [
        _decision(1, "match-1", 0),
        _decision(2, "match-2", 1),
    ]

    result = _preview(_payload(matches=matches, decisions=decisions), _default_search)

    diagnosis = result["diagnoses"][0]
    assert diagnosis["diagnosis_status"] == "provisional_weakness"
    assert [row["match_id"] for row in diagnosis["supporting_evidence"]] == ["match-1"]
    assert [row["match_id"] for row in diagnosis["counterevidence"]] == ["match-2"]


def test_all_correct_does_not_invent_weakness_or_recommendations() -> None:
    calls = []
    matches = [_match(1), _match(2)]
    decisions = [
        _decision(1, "match-1", 1),
        _decision(2, "match-2", 2, maximum=2),
    ]

    result = _preview(
        _payload(matches=matches, decisions=decisions),
        lambda request: calls.append(request),
    )

    assert result["diagnosis_status"] == "no_weakness_evidence"
    assert result["metrics"]["supporting_evidence_count"] == 0
    assert result["metrics"]["counterevidence_count"] == 2
    assert result["scope_groups"][0]["items"] == []
    assert calls == []


def test_unknown_teacher_confirmed_section_is_rejected() -> None:
    payload = _payload(
        decisions=[_decision(1, "match-1", 0, section_keys=["V9-C9:9.9"])]
    )

    with pytest.raises(StudentRecommendationWorkbenchError) as captured:
        _preview(payload, _default_search)
    assert captured.value.code == "student_recommendation_section_not_allowed"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda response: response["integrity"].update(
                {"complete_theme_chain_returned": False}
            ),
            "student_recommendation_search_invalid",
        ),
        (
            lambda response: response["items"][0].update(
                {"group_kind": "unassigned_pending_review"}
            ),
            "student_recommendation_theme_card_invalid",
        ),
        (
            lambda response: response["items"][0].pop("shared_context"),
            "student_recommendation_theme_card_invalid",
        ),
    ],
)
def test_incomplete_or_parentless_theme_cards_fail_closed(
    mutation, expected_code
) -> None:
    def search(payload: dict) -> dict:
        response = _search_response(payload["scope"], [_card(payload["scope"], "x")])
        mutation(response)
        return response

    with pytest.raises(StudentRecommendationWorkbenchError) as captured:
        _preview(_payload(), search)
    assert captured.value.code == expected_code


def test_same_theme_from_two_sections_is_deduplicated_and_reasons_are_merged() -> None:
    decisions = [
        _decision(
            1,
            "match-1",
            0,
            section_keys=[SECOND_SECTION, TARGET_SECTION],
        )
    ]

    def search(payload: dict) -> dict:
        section = payload["curriculum"]["section"]
        matched = ("rec-shared-1",) if section == TARGET_SECTION else ("rec-shared-2",)
        card = _card(
            payload["scope"],
            "shared",
            matched_atomic_ids=matched,
        )
        return _search_response(payload["scope"], [card])

    result = _preview(_payload(decisions=decisions), search)

    items = result["scope_groups"][0]["items"]
    assert len(items) == 1
    assert items[0]["diagnosis_section_keys"] == [TARGET_SECTION, SECOND_SECTION]
    assert items[0]["matched_atomic_ids"] == ["rec-shared-1", "rec-shared-2"]
    assert items[0]["counts"]["atomic_matched"] == 2


def test_scope_groups_are_canonical_and_do_not_cross_deduplicate() -> None:
    def search(payload: dict) -> dict:
        return _search_response(
            payload["scope"],
            [
                _card(
                    payload["scope"],
                    "same",
                    paper_id="paper-same",
                    theme_id="theme-same",
                )
            ],
        )

    result = _preview(_payload(scopes=["supplemental", "master", "wave1"]), search)

    assert [group["scope"] for group in result["scope_groups"]] == [
        "master",
        "wave1",
        "supplemental",
    ]
    assert [len(group["items"]) for group in result["scope_groups"]] == [1, 1, 1]


def test_known_current_atomic_theme_and_paper_are_all_excluded() -> None:
    cards = [
        _card("master", "atomic", atomic_ids=("current-atomic-1", "other")),
        _card("master", "theme", theme_id="current-theme"),
        _card("master", "paper", paper_id="current-paper"),
        _card("master", "safe"),
    ]
    payload = _payload(
        matches=[_match(1, theme="current-theme", paper="current-paper")]
    )

    result = _preview(payload, lambda request: _search_response("master", cards))

    items = result["scope_groups"][0]["items"]
    assert [item["theme"]["id"] for item in items] == ["theme-safe"]
    assert result["metrics"]["excluded_theme_card_count"] == 3


def test_basket_selection_is_directly_usable_as_a_complete_theme_entry() -> None:
    result = _preview(_payload(), _default_search)
    item = result["scope_groups"][0]["items"][0]
    selection = item["basket_selection"]

    assert selection["kind"] == "theme"
    assert selection["unit"] == "theme"
    assert selection["scope"] == "master"
    assert selection["source_id"] == item["theme"]["id"]
    assert selection["theme_id"] == item["theme"]["id"]
    assert selection["paper_id"] == item["paper"]["id"]
    assert selection["atomic_ids"] == [
        row["atomic_part_id"] for row in item["atomic_chain"]
    ]
    assert selection["shared_material_count"] == 1
    assert selection["dependency_count"] == 1
    assert "共同材料" in item["recommendation_reason_zh"]


def test_pending_scoring_and_unconfirmed_sections_remain_insufficient() -> None:
    matches = [_match(1), _match(2)]
    decisions = [
        _decision(1, "match-1", 0, action="pending"),
    ]

    result = _preview(
        _payload(matches=matches, decisions=decisions),
        lambda _request: pytest.fail("search must not run"),
    )

    assert result["scoring_confirmation"] == "pending"
    assert result["evidence_sufficiency"] == "insufficient_evidence"
    assert result["diagnosis_status"] == "no_weakness_evidence"
    assert result["diagnoses"] == []
    assert {row["code"] for row in result["blockers"]} == {
        "teacher_scoring_pending",
        "teacher_curriculum_confirmation_missing",
    }


def test_input_array_order_does_not_change_output() -> None:
    matches = [_match(1), _match(2), _match(3)]
    decisions = [
        _decision(1, "match-1", 0),
        _decision(2, "match-2", 1),
        _decision(3, "match-3", 0, section_keys=[SECOND_SECTION]),
    ]
    exclusions = {
        "atomic_part_ids": ["exclude-b", "exclude-a"],
        "theme_ids": ["theme-z", "theme-y"],
        "paper_ids": ["paper-z", "paper-y"],
    }

    def search(payload: dict) -> dict:
        section = payload["curriculum"]["section"].replace(":", "-")
        items = [
            _card(payload["scope"], f"{section}-b"),
            _card(payload["scope"], f"{section}-a"),
        ]
        return _search_response(payload["scope"], list(reversed(items)))

    first = _preview(
        _payload(
            matches=matches,
            decisions=decisions,
            scopes=["supplemental", "master"],
            exclusions=exclusions,
        ),
        search,
    )
    second = _preview(
        _payload(
            matches=list(reversed(matches)),
            decisions=[decisions[2], decisions[0], decisions[1]],
            scopes=["master", "supplemental"],
            exclusions={
                key: list(reversed(value)) for key, value in exclusions.items()
            },
        ),
        search,
    )

    assert first == second

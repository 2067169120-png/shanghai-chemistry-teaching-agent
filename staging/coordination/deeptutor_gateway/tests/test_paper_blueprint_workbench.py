from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

WORKSPACE = Path(__file__).resolve().parents[4]
if str(WORKSPACE) not in sys.path:
    sys.path.insert(0, str(WORKSPACE))

from integrations.deeptutor_shchem_v1.paper_blueprint_workbench import (
    PaperBlueprintWorkbench,
    PaperBlueprintWorkbenchError,
    canonical_sha256,
)
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    default_shanghai_theme_preset,
)

FIXTURE_PATH = (
    WORKSPACE
    / "parallel_outputs"
    / "paper_blueprint_workbench"
    / "fixtures"
    / "synthetic_blueprint_inputs.json"
)
SNAPSHOT = "1" * 64
THEME_FOUNDATION = "SYN-T-FOUNDATION"
THEME_REDOX_A = "SYN-T-REDOX-A"
THEME_REDOX_B = "SYN-T-REDOX-B"
THEME_EQUIL_A = "SYN-T-EQUIL-A"
THEME_EQUIL_B = "SYN-T-EQUIL-B"
THEME_OLD = "SYN-T-OLD"
THEME_NOANSWER = "SYN-T-NOANSWER"


@pytest.fixture()
def synthetic_inputs() -> dict[str, Any]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert fixture["synthetic_fixture"] is True
    assert "合成测试数据" in fixture["notice_zh"]
    assert fixture["data_snapshot_id"] == SNAPSHOT
    return fixture


def _search_result(
    fixture: dict[str, Any],
    *,
    include_theme_ids: set[str] | None = None,
    reverse: bool = False,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for paper_entry in fixture["theme_catalog"]["papers"]:
        paper = paper_entry["paper"]
        for group in paper_entry["theme_groups"]:
            theme_id = group["theme"]["id"]
            if include_theme_ids is not None and theme_id not in include_theme_ids:
                continue
            matched = fixture["search_highlights"][theme_id]
            items.append(
                {
                    "scope": "master",
                    "group_kind": "theme",
                    "display_title_zh": group["theme"]["title"],
                    "paper": deepcopy(paper),
                    "theme": deepcopy(group["theme"]),
                    "source_metadata": deepcopy(paper["source_metadata"]),
                    "counts": {
                        "atomic_total": len(group["atomic_chain"]),
                        "atomic_matched": len(matched),
                        "display_atomic_units": len(group["atomic_chain"]),
                    },
                    "shared_context": deepcopy(group["shared_context"]),
                    "dependencies": deepcopy(group["dependencies"]),
                    "matched_atomic_ids": list(matched),
                    "match_details": [
                        {
                            "atomic_part_id": atomic_id,
                            "reason_codes": ["synthetic_test_match"],
                        }
                        for atomic_id in matched
                    ],
                    "atomic_chain": deepcopy(group["atomic_chain"]),
                }
            )
    if reverse:
        items.reverse()
    return {
        "schema_version": "1.0.0-question-search-theme-cards",
        "scope": "master",
        "q": "合成测试查询",
        "filters": {},
        "data_snapshot_id": SNAPSHOT,
        "counts": {
            "theme_cards_matched": len(items),
            "returned_theme_cards": len(items),
        },
        "page": {
            "limit": 50,
            "returned": len(items),
            "total_theme_cards": len(items),
            "has_more": False,
            "next_cursor": None,
        },
        "items": items,
        "curriculum": {
            "selector": {
                "volume_id": "SYN-V1",
                "mapping_status": "complete",
            },
            "explicit_mapping_only": True,
            "knowledge_tag_inference_used": False,
        },
        "integrity": {
            "theme_first": True,
            "atomic_matches_are_highlights_only": True,
            "complete_theme_chain_returned": True,
            "dependency_context_preserved": True,
        },
    }


def _hard_constraints(fixture: dict[str, Any]) -> dict[str, Any]:
    return {
        "coverage": {
            "K": ["K-SYN-FOUND", "K-SYN-REDOX", "K-SYN-EQUIL"],
            "A": ["A-SYN-READ", "A-SYN-APPLY", "A-SYN-EXPLAIN"],
            "C": ["C-SYN-EVIDENCE", "C-SYN-MODEL"],
            "R": ["R-SYN-FILL", "R-SYN-EQUATION", "R-SYN-CALC"],
            "RP": ["RP-SYN-TEXT", "RP-SYN-FLOW", "RP-SYN-TABLE"],
        },
        "difficulty_distribution": {"D1": 2, "D2": 3, "D3": 2},
        "source_years": [2025, 2026],
        "answer_eligibility": {
            "allowed_availability": ["present_part_aligned"],
            "allowed_authorities": ["nonofficial_reference"],
            "require_all_selected": True,
        },
        "required_theme_ids": [],
        "required_atomic_ids": [],
        "required_item_ids": [],
        "excluded_theme_ids": [],
        "excluded_atomic_ids": [],
        "excluded_item_ids": [],
        "no_duplicate_clusters": True,
        "dedup_clusters": deepcopy(fixture["dedup_clusters"]),
    }


def _mock_payload(fixture: dict[str, Any], *, candidate_count: int = 3) -> dict[str, Any]:
    preset = default_shanghai_theme_preset()
    return {
        "mode": "mock_exam",
        "scope": "master",
        "data_snapshot_id": SNAPSHOT,
        "candidate_count": candidate_count,
        "paper": {
            "exam_name_zh": "合成阶段测评（仅测试）",
            "subtitle_zh": "不对应任何真实考试",
            "template_id": preset["preset_id"],
            "template_version": preset["preset_version"],
            "template_year": 2031,
            "duration_minutes": 30,
            "duration_rule": "exact",
            "total_score": 30,
            "theme_count": 3,
            "instructions_zh": [
                "本卷全部题目均为合成测试数据。",
                "选择、填空、简答与计算任务嵌入完整主题中。",
            ],
            "scoring_rules": {
                "selection_rule_zh": "合成选择任务按题面要求计分。",
                "partial_credit_rule_zh": "合成主观任务按步骤建议计分。",
                "other_rule_zh": "本规则仅用于软件测试，不是官方评分细则。",
            },
            "identity_fields_zh": ["姓名", "班级"],
            "sealed_line": True,
            "numbering_mode": "restart_within_each_theme",
            "answer_space_lines": 3,
            "pagination_rules": {
                "cover_page": True,
                "keep_theme_together": True,
            },
        },
        "curriculum": {
            "volume_id": "SYN-V1",
            "mapping_status": "complete",
        },
        "hard_constraints": _hard_constraints(fixture),
        "preferences": {"selection_unit": "theme"},
        "ordering": {"teacher_theme_order": [], "prerequisite_edges": []},
    }


def _fixed_mock_payload(fixture: dict[str, Any]) -> dict[str, Any]:
    payload = _mock_payload(fixture, candidate_count=1)
    payload["hard_constraints"]["required_theme_ids"] = [
        THEME_FOUNDATION,
        THEME_REDOX_A,
        THEME_EQUIL_A,
    ]
    payload["hard_constraints"]["excluded_theme_ids"] = [
        THEME_REDOX_B,
        THEME_EQUIL_B,
        THEME_OLD,
        THEME_NOANSWER,
    ]
    return payload


def _daily_payload(fixture: dict[str, Any], *, selection_unit: str = "dependency") -> dict[str, Any]:
    hard = _hard_constraints(fixture)
    hard["coverage"] = {"K": ["K-SYN-REDOX"]}
    hard["difficulty_distribution"] = {"D2": {"min": 1}}
    hard["no_duplicate_clusters"] = False
    return {
        "mode": "daily_practice",
        "scope": "master",
        "data_snapshot_id": SNAPSHOT,
        "candidate_count": 3,
        "paper": {
            "title_zh": "合成氧化还原日常练习",
            "answer_space_lines": 2,
        },
        "curriculum": {
            "volume_id": "SYN-V1",
            "mapping_status": "complete",
        },
        "hard_constraints": hard,
        "preferences": {
            "selection_unit": selection_unit,
            "target_atomic_count": 3,
            "target_duration_minutes": 12,
        },
        "ordering": {"teacher_theme_order": [], "prerequisite_edges": []},
    }


def _optimize(
    fixture: dict[str, Any],
    payload: dict[str, Any],
    *,
    catalog: dict[str, Any] | None = None,
    search: dict[str, Any] | None = None,
    preset: dict[str, Any] | None = None,
    curriculum_catalog: dict[str, Any] | None = None,
    curriculum_mapping: dict[str, Any] | None = None,
    metadata_overlay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return PaperBlueprintWorkbench().optimize(
        payload,
        theme_catalog=deepcopy(catalog or fixture["theme_catalog"]),
        question_search_result=deepcopy(search or _search_result(fixture)),
        paper_format_preset=deepcopy(preset or default_shanghai_theme_preset()),
        curriculum_catalog=deepcopy(
            curriculum_catalog or fixture["curriculum_catalog"]
        ),
        curriculum_mapping=deepcopy(
            curriculum_mapping or fixture["curriculum_mapping"]
        ),
        metadata_overlay=deepcopy(metadata_overlay or fixture["metadata_overlay"]),
    )


def _selected_theme_ids(candidate: dict[str, Any]) -> list[str]:
    return [
        selection["theme_id"] for selection in candidate["basket_selections"]
    ]


def _theme_group(candidate: dict[str, Any], theme_id: str) -> dict[str, Any]:
    return next(
        group
        for group in candidate["preview_model"]["theme_groups"]
        if group["data_ref"]["theme_id"] == theme_id
    )


def _chinese_text(response: dict[str, Any]) -> str:
    records = [
        *response["conflicts"],
        *response["inventory_gaps"],
        *response["missing_questions"],
        *response["relaxation_options"],
    ]
    return "\n".join(str(record.get("message_zh", "")) for record in records)


def test_canonical_sha256_is_mapping_order_insensitive_but_list_order_sensitive() -> None:
    left = {"中文": "值", "nested": {"b": 2, "a": 1}, "items": [1, 2]}
    right = {"items": [1, 2], "nested": {"a": 1, "b": 2}, "中文": "值"}
    reordered_list = {"中文": "值", "nested": {"b": 2, "a": 1}, "items": [2, 1]}

    assert canonical_sha256(left) == canonical_sha256(right)
    assert canonical_sha256(left) != canonical_sha256(reordered_list)


def test_mock_exam_returns_three_deterministic_hard_constraint_candidates(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _mock_payload(synthetic_inputs)
    first = _optimize(synthetic_inputs, payload)
    second = _optimize(synthetic_inputs, deepcopy(payload))

    assert first == second
    assert {
        "schema_version",
        "mode",
        "data_snapshot_id",
        "request_sha256",
        "status",
        "candidates",
        "conflicts",
        "inventory_gaps",
        "relaxation_options",
    } <= set(first)
    assert first["mode"] == "mock_exam"
    assert first["status"] == "feasible"
    assert first["conflicts"] == []
    assert len(first["candidates"]) == 3
    assert [candidate["rank"] for candidate in first["candidates"]] == [1, 2, 3]
    assert len({candidate["candidate_id"] for candidate in first["candidates"]}) == 3

    for candidate in first["candidates"]:
        totals = candidate["coverage"]["totals"]
        assert totals == {
            "theme_count": 3,
            "atomic_count": 7,
            "total_score": 30,
            "score_complete": True,
            "estimated_time_minutes": 30,
            "time_complete": True,
        }
        assert candidate["coverage"]["difficulty_distribution"] == {
            "D1": 2,
            "D2": 3,
            "D3": 2,
        }
        required_axis_labels = {
            "K": {"K-SYN-FOUND", "K-SYN-REDOX", "K-SYN-EQUIL"},
            "A": {"A-SYN-READ", "A-SYN-APPLY", "A-SYN-EXPLAIN"},
            "C": {"C-SYN-EVIDENCE", "C-SYN-MODEL"},
            "R": {"R-SYN-FILL", "R-SYN-EQUATION", "R-SYN-CALC"},
            "RP": {"RP-SYN-TEXT", "RP-SYN-FLOW", "RP-SYN-TABLE"},
        }
        for axis, labels in required_axis_labels.items():
            assert labels <= {
                label
                for label, count in candidate["coverage"]["axis_coverage"][
                    axis
                ].items()
                if count >= 1
            }
        assert candidate["coverage"]["textbook_mapping"] == {
            "mapped_atomic_count": 7,
            "unknown_atomic_count": 0,
        }
        assert set(candidate["coverage"]["source_year_distribution"]) <= {
            "2025",
            "2026",
        }
        assert THEME_OLD not in _selected_theme_ids(candidate)
        assert THEME_NOANSWER not in _selected_theme_ids(candidate)
        assert candidate["integrity"]["hard_constraints_relaxed"] is False


def test_mock_exam_uses_complete_themes_and_atomic_matches_are_highlights_only(
    synthetic_inputs: dict[str, Any],
) -> None:
    response = _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs))
    candidate = response["candidates"][0]

    assert all(
        selection == {
            "scope": "master",
            "selection_unit": "theme",
            "theme_id": selection["theme_id"],
            "target_atomic_id": None,
            "expected_data_snapshot_id": SNAPSHOT,
        }
        for selection in candidate["basket_selections"]
    )
    assert candidate["integrity"]["atomic_matches_are_highlights_only"] is True
    redox = _theme_group(candidate, THEME_REDOX_A)
    assert redox["highlighted_atomic_ids"] == ["SYN-A-RA3"]
    assert [row["data_ref"]["atomic_part_id"] for row in redox["compact_rows"]] == [
        "SYN-A-RA1",
        "SYN-A-RA2",
        "SYN-A-RA3",
    ]
    bundle = next(
        item
        for item in candidate["assembly_blueprint"]["theme_bundles"]
        if item["source"]["theme_id"] == THEME_REDOX_A
    )
    assert bundle["final_atomic_ids"] == [
        "SYN-A-RA1",
        "SYN-A-RA2",
        "SYN-A-RA3",
    ]


def test_daily_dependency_mode_returns_explicit_transitive_closure_under_theme(
    synthetic_inputs: dict[str, Any],
) -> None:
    search = _search_result(
        synthetic_inputs,
        include_theme_ids={THEME_REDOX_A, THEME_REDOX_B},
    )
    search["q"] = "氧化还原"
    search["filters"] = {
        "K": ["K-SYN-REDOX"],
        "item_type": ["reasoned_explanation"],
        "D": ["D2"],
    }
    payload = _daily_payload(synthetic_inputs, selection_unit="dependency")
    payload["curriculum"] = {
        "volume_id": "SYN-V1",
        "chapter_id": "SYN-C2",
        "section": "SYN-C2:2.1",
        "mapping_status": "complete",
    }
    search["curriculum"]["selector"] = deepcopy(payload["curriculum"])
    response = _optimize(
        synthetic_inputs,
        payload,
        search=search,
    )

    assert response["status"] == "feasible"
    candidate = response["candidates"][0]
    assert candidate["preview_model"]["projection_unit"] == "theme_big_question"
    assert candidate["preview_model"]["layout_kind"] == "compact_practice"
    assert candidate["coverage"]["totals"]["atomic_count"] == 3
    assert candidate["coverage"]["totals"]["estimated_time_minutes"] == 12
    assert candidate["coverage"]["textbook_mapping"] == {
        "mapped_atomic_count": 3,
        "unknown_atomic_count": 0,
    }
    assert len(candidate["preview_model"]["theme_groups"]) == 1
    selection = candidate["basket_selections"][0]
    assert selection["selection_unit"] == "dependency"
    assert selection["target_atomic_id"] in {"SYN-A-RA3", "SYN-A-RB3"}
    final_ids = candidate["assembly_blueprint"]["theme_bundles"][0][
        "final_atomic_ids"
    ]
    assert final_ids in (
        ["SYN-A-RA1", "SYN-A-RA2", "SYN-A-RA3"],
        ["SYN-A-RB1", "SYN-A-RB2", "SYN-A-RB3"],
    )


def test_daily_practice_has_no_exam_theme_limit_and_targets_remain_soft(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _daily_payload(synthetic_inputs, selection_unit="theme")
    payload["paper"] = {"title_zh": "合成日常练习", "answer_space_lines": 2}
    payload["preferences"] = {
        "selection_unit": "theme",
        "target_atomic_count": 99,
        "target_duration_minutes": 99,
        "target_theme_count": 6,
    }
    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "feasible"
    assert response["normalized_request"]["paper"]["theme_count"] is None
    assert response["normalized_request"]["paper"]["total_score"] is None
    assert response["normalized_request"]["paper"]["duration_minutes"] is None
    candidate = response["candidates"][0]
    assert candidate["objective"]["daily_atomic_target_distance"] > 0
    assert candidate["objective"]["daily_time_target_distance_minutes"] > 0
    assert candidate["preview_model"]["cover"]["enabled"] is False
    assert candidate["integrity"]["hard_constraints_relaxed"] is False


def test_theme_first_preview_projection_is_collapsed_and_teacher_friendly(
    synthetic_inputs: dict[str, Any],
) -> None:
    response = _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs))
    preview = response["candidates"][0]["preview_model"]

    assert preview["projection_unit"] == "theme_big_question"
    assert [group["display_number"] for group in preview["theme_groups"]] == [
        "第一题",
        "第二题",
        "第三题",
    ]
    theme_keys = {
        "title",
        "source",
        "textbook",
        "score",
        "time_minutes",
        "difficulty",
        "shared_materials",
        "atomic_count",
    }
    compact_keys = {
        "display_number",
        "response_type",
        "score",
        "textbook_section",
        "difficulty",
        "answer_eligibility",
        "dependency",
        "expandable_content_ref",
    }
    for group in preview["theme_groups"]:
        assert group["group_kind"] == "theme_big_question"
        assert group["collapsed"] is True
        assert theme_keys <= set(group)
        assert "SYN-T-" not in group["heading_zh"]
        assert group["atomic_count"] == len(group["compact_rows"])
        assert group["compact_rows"]
        for row in group["compact_rows"]:
            assert row["collapsed"] is True
            assert compact_keys <= set(row)
            assert row["display_number"].startswith("第")
            assert "SYN-A-" not in row["display_number"]
            assert row["expandable_content_ref"].startswith("/api/")


def test_shared_material_is_rendered_once_and_never_copied_into_compact_rows(
    synthetic_inputs: dict[str, Any],
) -> None:
    response = _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs))
    candidate = response["candidates"][0]
    redox = _theme_group(candidate, THEME_REDOX_A)
    marker = "【合成共享材料：氧化还原路线甲，仅用于测试】"

    assert len(redox["shared_materials"]) == 1
    assert redox["shared_materials"][0]["render"] is True
    assert json.dumps(candidate["preview_model"], ensure_ascii=False).count(marker) == 1
    compact_json = json.dumps(redox["compact_rows"], ensure_ascii=False)
    assert marker not in compact_json
    assert '"shared_materials"' not in compact_json


def test_explicit_content_hash_deduplicates_shared_material_across_themes(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    groups = {
        group["theme"]["id"]: group
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
    }
    shared_hash = "a" * 64
    groups[THEME_FOUNDATION]["shared_context"]["materials"][0][
        "content_sha256"
    ] = shared_hash
    groups[THEME_REDOX_A]["shared_context"]["materials"][0][
        "content_sha256"
    ] = shared_hash

    response = _optimize(
        synthetic_inputs,
        _fixed_mock_payload(synthetic_inputs),
        catalog=catalog,
    )

    foundation = _theme_group(response["candidates"][0], THEME_FOUNDATION)
    redox = _theme_group(response["candidates"][0], THEME_REDOX_A)
    first = foundation["shared_materials"][0]
    reused = redox["shared_materials"][0]
    assert first["render"] is True
    assert first["content_sha256"] == shared_hash
    assert reused["render"] is False
    assert reused["reuse_of"] == first["material_anchor"]
    assert reused["content_sha256"] == shared_hash


def test_new_numbering_ignores_duplicate_source_numbers_and_preserves_trace(
    synthetic_inputs: dict[str, Any],
) -> None:
    response = _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs))
    redox = _theme_group(response["candidates"][0], THEME_REDOX_A)

    assert [item["display_number"] for item in redox["printed_questions"]] == [
        "1",
        "2",
        "3",
    ]
    assert [row["display_number"] for row in redox["compact_rows"]] == [
        "第1问",
        "第2问",
        "第3问",
    ]
    assert {
        item["data_ref"]["source_number"] for item in redox["printed_questions"]
    } == {"7"}
    assert {
        row["data_ref"]["source_printed_number"] for row in redox["compact_rows"]
    } == {"7"}
    assert response["candidates"][0]["preview_model"]["exam_information"][
        "source_numbers_are_trace_only"
    ] is True


def test_multiple_atomic_parts_in_one_printed_question_keep_continuous_numbering(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    redox = next(
        group
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
        if group["theme"]["id"] == THEME_REDOX_A
    )
    rows = redox["atomic_chain"]
    rows[1]["printed_question_id"] = rows[0]["printed_question_id"]
    rows[1]["printed_sequence"] = 1
    rows[1]["atomic_sequence_in_printed"] = 2
    rows[2]["printed_sequence"] = 2

    response = _optimize(
        synthetic_inputs,
        _fixed_mock_payload(synthetic_inputs),
        catalog=catalog,
    )

    group = _theme_group(response["candidates"][0], THEME_REDOX_A)
    assert [item["display_number"] for item in group["printed_questions"]] == [
        "1",
        "2",
    ]
    assert group["printed_questions"][0]["atomic_count"] == 2
    assert [row["display_number"] for row in group["compact_rows"]] == [
        "第1问",
        "第2问",
        "第3问",
    ]


def test_teacher_drag_order_overrides_default_progression_and_renumbers(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    payload["ordering"]["teacher_theme_order"] = [
        THEME_EQUIL_A,
        THEME_FOUNDATION,
        THEME_REDOX_A,
    ]
    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "feasible"
    candidate = response["candidates"][0]
    assert candidate["ordered_theme_ids"] == [
        THEME_EQUIL_A,
        THEME_FOUNDATION,
        THEME_REDOX_A,
    ]
    assert [
        (group["display_number"], group["data_ref"]["theme_id"])
        for group in candidate["preview_model"]["theme_groups"]
    ] == [
        ("第一题", THEME_EQUIL_A),
        ("第二题", THEME_FOUNDATION),
        ("第三题", THEME_REDOX_A),
    ]


def test_prerequisite_cycle_returns_actionable_chinese_conflict(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    payload["ordering"]["prerequisite_edges"] = [
        [THEME_FOUNDATION, THEME_REDOX_A],
        [THEME_REDOX_A, THEME_FOUNDATION],
    ]

    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "infeasible"
    assert any(
        conflict["code"] == "theme_order_cycle"
        and "形成循环" in conflict["message_zh"]
        for conflict in response["conflicts"]
    )
    assert any(
        option["code"] == "repair_theme_order_cycle"
        and "先修边" in option["message_zh"]
        for option in response["relaxation_options"]
    )


def test_theme_overlay_prerequisite_must_reference_current_snapshot(
    synthetic_inputs: dict[str, Any],
) -> None:
    overlay = deepcopy(synthetic_inputs["metadata_overlay"])
    overlay["themes"][THEME_REDOX_A]["prerequisite_theme_ids"] = [
        "TYPO-NOT-IN-SNAPSHOT"
    ]

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(
            synthetic_inputs,
            _fixed_mock_payload(synthetic_inputs),
            metadata_overlay=overlay,
        )

    assert captured.value.code == "theme_prerequisite_reference_missing"
    assert captured.value.details["missing_prerequisite_theme_ids"] == [
        "TYPO-NOT-IN-SNAPSHOT"
    ]


def test_mock_preview_contains_exam_contract_and_is_frozen_before_export(
    synthetic_inputs: dict[str, Any],
) -> None:
    response = _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs))
    candidate = response["candidates"][0]
    preview = candidate["preview_model"]

    assert preview["layout_kind"] == "exam"
    assert preview["cover"]["enabled"] is True
    assert preview["cover"]["title_zh"] == "合成阶段测评（仅测试）"
    assert preview["cover"]["identity_fields_zh"] == ["姓名", "班级"]
    assert preview["cover"]["sealed_line"]["enabled"] is True
    assert preview["exam_information"]["template_year"] == 2031
    assert preview["exam_information"]["scheduled_duration_minutes"] == 30
    assert preview["exam_information"]["total_score"] == 30
    assert preview["exam_information"]["theme_count"] == 3
    assert preview["pagination"]["status"] == "pending_renderer"
    assert preview["pagination"]["physical_page_count"] is None
    assert preview["answer_eligibility_summary"]["eligible"] is True
    assert preview["export_gate"]["preview_frozen_before_export"] is True
    assert preview["export_gate"]["export_allowed"] is False
    assert candidate["preview_snapshot_sha256"] == canonical_sha256(preview)
    assert candidate["preview_freeze"]["sha256"] == candidate[
        "preview_snapshot_sha256"
    ]
    assert candidate["export_precondition"][
        "required_preview_snapshot_sha256"
    ] == candidate["preview_snapshot_sha256"]


def test_preview_hash_changes_for_order_score_answer_space_and_preset(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    baseline = _optimize(synthetic_inputs, payload)["candidates"][0][
        "preview_snapshot_sha256"
    ]

    reordered_payload = deepcopy(payload)
    reordered_payload["ordering"]["teacher_theme_order"] = [
        THEME_EQUIL_A,
        THEME_FOUNDATION,
        THEME_REDOX_A,
    ]
    reordered = _optimize(synthetic_inputs, reordered_payload)["candidates"][0][
        "preview_snapshot_sha256"
    ]

    score_overlay = deepcopy(synthetic_inputs["metadata_overlay"])
    score_overlay["atomics"]["SYN-A-RA1"]["score"] = 5
    score_overlay["atomics"]["SYN-A-RA2"]["score"] = 3
    rescored = _optimize(
        synthetic_inputs,
        payload,
        metadata_overlay=score_overlay,
    )["candidates"][0]["preview_snapshot_sha256"]

    answer_space_overlay = deepcopy(synthetic_inputs["metadata_overlay"])
    answer_space_overlay["atomics"]["SYN-A-RA1"]["answer_space_lines"] = 1
    resized = _optimize(
        synthetic_inputs,
        payload,
        metadata_overlay=answer_space_overlay,
    )["candidates"][0]["preview_snapshot_sha256"]

    changed_preset = default_shanghai_theme_preset()
    changed_preset["page_layout"]["margins_mm"]["top"] = 19
    restyled = _optimize(
        synthetic_inputs,
        payload,
        preset=changed_preset,
    )["candidates"][0]["preview_snapshot_sha256"]

    assert len({baseline, reordered, rescored, resized, restyled}) == 5


def test_input_collection_order_does_not_change_deterministic_candidates(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _mock_payload(synthetic_inputs)
    baseline = _optimize(synthetic_inputs, payload)

    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    catalog["papers"].reverse()
    for paper in catalog["papers"]:
        paper["theme_groups"].reverse()
    mapping = deepcopy(synthetic_inputs["curriculum_mapping"])
    mapping["entries"].reverse()
    overlay = deepcopy(synthetic_inputs["metadata_overlay"])
    overlay["themes"] = dict(reversed(list(overlay["themes"].items())))
    overlay["atomics"] = dict(reversed(list(overlay["atomics"].items())))
    reordered = _optimize(
        synthetic_inputs,
        payload,
        catalog=catalog,
        search=_search_result(synthetic_inputs, reverse=True),
        curriculum_mapping=mapping,
        metadata_overlay=overlay,
    )

    assert reordered == baseline


def test_answer_eligibility_excludes_unqualified_required_theme_with_chinese_gap(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _mock_payload(synthetic_inputs, candidate_count=1)
    payload["paper"].update(
        {"theme_count": 1, "total_score": 10, "duration_minutes": 10}
    )
    payload["hard_constraints"]["coverage"] = {"K": ["K-SYN-EQUIL"]}
    payload["hard_constraints"]["difficulty_distribution"] = {"D3": 1}
    payload["hard_constraints"]["required_theme_ids"] = [THEME_NOANSWER]
    payload["hard_constraints"]["excluded_theme_ids"] = [
        THEME_EQUIL_A,
        THEME_EQUIL_B,
    ]
    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "infeasible"
    assert response["candidates"] == []
    assert any(
        gap["code"] == "answer_eligibility_missing"
        for gap in response["inventory_gaps"]
    )
    assert "答案资格" in _chinese_text(response)
    assert any(
        option["code"] == "review_answer_eligibility"
        for option in response["relaxation_options"]
    )


def test_duplicate_cluster_hard_constraint_fails_closed(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _mock_payload(synthetic_inputs, candidate_count=1)
    payload["paper"].update(
        {"theme_count": 2, "total_score": 24, "duration_minutes": 24}
    )
    payload["hard_constraints"]["coverage"] = {"K": ["K-SYN-REDOX"]}
    payload["hard_constraints"]["difficulty_distribution"] = {"D2": 6}
    payload["hard_constraints"]["required_theme_ids"] = [
        THEME_REDOX_A,
        THEME_REDOX_B,
    ]
    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "infeasible"
    assert response["candidates"] == []
    assert any(
        conflict["code"] == "duplicate_cluster_conflict"
        for conflict in response["conflicts"]
    )
    assert "重复簇" in _chinese_text(response)
    assert response["integrity"]["hard_constraints_relaxed"] is False


def test_required_excluded_conflict_suggests_reconciling_the_lists(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    payload["hard_constraints"]["excluded_theme_ids"].append(THEME_FOUNDATION)

    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "infeasible"
    assert any(
        conflict["code"] == "required_excluded_conflict"
        for conflict in response["conflicts"]
    )
    assert any(
        option["code"] == "reconcile_required_and_excluded_items"
        and "必选或排除列表" in option["message_zh"]
        for option in response["relaxation_options"]
    )


@pytest.mark.parametrize(
    ("field", "expected_zh"),
    [
        ("score", "分值"),
        ("time_minutes", "用时"),
        ("dedup_cluster_id", "去重簇"),
    ],
)
def test_missing_required_atomic_metadata_returns_chinese_inventory_gap(
    synthetic_inputs: dict[str, Any], field: str, expected_zh: str
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    overlay = deepcopy(synthetic_inputs["metadata_overlay"])
    overlay["atomics"]["SYN-A-RA2"].pop(field)
    response = _optimize(synthetic_inputs, payload, metadata_overlay=overlay)

    assert response["status"] == "infeasible"
    assert response["candidates"] == []
    assert response["inventory_gaps"]
    assert expected_zh in _chinese_text(response)
    assert response["relaxation_options"]
    assert response["integrity"]["hard_constraints_relaxed"] is False


def test_impossible_exact_total_never_returns_a_silently_relaxed_candidate(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    payload["paper"]["total_score"] = 31
    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "infeasible"
    assert response["candidates"] == []
    assert any(
        conflict["code"] == "total_score_mismatch"
        for conflict in response["conflicts"]
    )
    assert response["normalized_request"]["paper"]["total_score"] == 31
    assert response["integrity"]["hard_constraints_relaxed"] is False
    assert any(
        "不会自动修改" in option["message_zh"]
        for option in response["relaxation_options"]
    )


def test_daily_target_score_is_a_soft_ranking_preference(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _daily_payload(synthetic_inputs, selection_unit="theme")
    payload["preferences"]["target_total_score"] = 999

    response = _optimize(synthetic_inputs, payload)

    assert response["status"] == "feasible"
    assert response["normalized_request"]["preferences"][
        "target_total_score"
    ] == 999
    assert response["candidates"][0]["objective"][
        "daily_score_target_distance"
    ] > 0
    assert response["integrity"]["hard_constraints_relaxed"] is False


def test_daily_target_duration_does_not_turn_missing_time_into_a_hard_filter(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _daily_payload(synthetic_inputs, selection_unit="theme")
    payload["hard_constraints"]["coverage"] = {"K": ["K-SYN-FOUND"]}
    payload["hard_constraints"]["difficulty_distribution"] = {"D1": 2}
    payload["preferences"]["target_atomic_count"] = 2
    search = _search_result(
        synthetic_inputs, include_theme_ids={THEME_FOUNDATION}
    )
    overlay = deepcopy(synthetic_inputs["metadata_overlay"])
    overlay["atomics"]["SYN-A-F1"].pop("time_minutes")
    overlay["atomics"]["SYN-A-F2"].pop("time_minutes")

    response = _optimize(
        synthetic_inputs,
        payload,
        search=search,
        metadata_overlay=overlay,
    )

    assert response["status"] == "feasible"
    candidate = response["candidates"][0]
    assert candidate["coverage"]["totals"]["time_complete"] is False
    assert candidate["objective"]["daily_time_target_distance_minutes"] is None
    assert candidate["integrity"]["hard_constraints_relaxed"] is False


def test_missing_dependency_metadata_fails_closed_instead_of_guessing_independent(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    target = next(
        row
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
        for row in group["atomic_chain"]
        if row["atomic_part_id"] == "SYN-A-RA3"
    )
    target.pop("dependency")

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(
            synthetic_inputs,
            _daily_payload(synthetic_inputs, selection_unit="dependency"),
            catalog=catalog,
        )

    assert captured.value.code == "theme_catalog_dependency_missing"
    assert "不能默认猜成独立题" in captured.value.message_zh


def test_missing_explicit_source_order_fails_closed(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    first_atomic = catalog["papers"][0]["theme_groups"][0]["atomic_chain"][0]
    first_atomic.pop("printed_sequence")

    response = _optimize(
        synthetic_inputs,
        _fixed_mock_payload(synthetic_inputs),
        catalog=catalog,
    )

    assert response["status"] == "infeasible"
    assert response["candidates"] == []
    assert any(
        gap["code"] == "source_order_metadata_missing"
        for gap in response["inventory_gaps"]
    )
    assert any(
        option["code"] == "complete_source_order_metadata"
        and "不会按数组位置猜测" in option["message_zh"]
        for option in response["relaxation_options"]
    )


def test_singleton_source_order_can_be_derived_without_ambiguity(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    old_theme = next(
        group
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
        if group["theme"]["id"] == THEME_OLD
    )
    only_atomic = old_theme["atomic_chain"][0]
    only_atomic["printed_sequence"] = None
    only_atomic["printed_sequence_status"] = "unknown_pending_review"
    only_atomic["atomic_sequence_in_printed"] = None
    only_atomic["atomic_sequence_status"] = "unknown_pending_review"
    payload = {
        "mode": "daily_practice",
        "scope": "master",
        "data_snapshot_id": SNAPSHOT,
        "candidate_count": 1,
        "paper": {"title_zh": "合成单元素顺序测试"},
        "hard_constraints": {},
        "preferences": {"selection_unit": "theme"},
        "ordering": {"teacher_theme_order": [], "prerequisite_edges": []},
    }
    search = _search_result(synthetic_inputs, include_theme_ids={THEME_OLD})

    response = _optimize(
        synthetic_inputs,
        payload,
        catalog=catalog,
        search=search,
    )

    assert response["status"] == "feasible"
    bundle = response["candidates"][0]["assembly_blueprint"]["theme_bundles"][0]
    printed = bundle["printed_questions"][0]
    atomic = bundle["printed_questions"][0]["atomic_parts"][0]
    assert printed["source_sequence"] == 1
    assert atomic["atomic_sequence_in_printed"] == 1


def test_shared_material_dependency_requires_explicit_theme_context(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    redox = next(
        group
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
        if group["theme"]["id"] == THEME_REDOX_A
    )
    redox.pop("shared_context")

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(
            synthetic_inputs,
            _fixed_mock_payload(synthetic_inputs),
            catalog=catalog,
        )

    assert captured.value.code == "theme_shared_context_missing"
    assert "不能假定没有共同材料" in captured.value.message_zh


def test_shared_material_dependency_cannot_use_summary_as_the_material(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    redox = next(
        group
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
        if group["theme"]["id"] == THEME_REDOX_A
    )
    redox["shared_context"]["materials"] = []
    redox["shared_context"]["material_count"] = 0
    assert redox["shared_context"]["context_summary_zh"]

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(
            synthetic_inputs,
            _fixed_mock_payload(synthetic_inputs),
            catalog=catalog,
        )

    assert captured.value.code == "theme_shared_material_missing"
    assert "没有可用共同材料" in captured.value.message_zh


def test_multiple_curriculum_edges_are_canonicalized_before_hashing(
    synthetic_inputs: dict[str, Any],
) -> None:
    mapping = deepcopy(synthetic_inputs["curriculum_mapping"])
    mapping["entries"].append(
        {
            "atomic_id": "SYN-A-F1",
            "atomic_part_id": "SYN-A-F1",
            "volume_id": "SYN-V1",
            "chapter_id": "SYN-C2",
            "section_key": "SYN-C2:2.1",
            "mapping_status": "complete",
        }
    )
    reversed_mapping = deepcopy(mapping)
    reversed_mapping["entries"].reverse()

    first = _optimize(
        synthetic_inputs,
        _fixed_mock_payload(synthetic_inputs),
        curriculum_mapping=mapping,
    )
    second = _optimize(
        synthetic_inputs,
        _fixed_mock_payload(synthetic_inputs),
        curriculum_mapping=reversed_mapping,
    )

    assert first == second


def test_pagination_cannot_disable_theme_integrity_rules(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    payload["paper"]["pagination_rules"][
        "shared_material_at_theme_level_only"
    ] = False

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, payload)

    assert captured.value.code == "paper_pagination_rule_invalid"
    assert "共享材料" in captured.value.message_zh


def test_pagination_rejects_unknown_or_untyped_renderer_rules(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    payload["paper"]["pagination_rules"]["unknown_rule"] = 123

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, payload)

    assert captured.value.code == "paper_pagination_rule_invalid"
    assert "v1 尚未定义" in captured.value.message_zh


def test_mock_exam_preview_cannot_disable_cover(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _fixed_mock_payload(synthetic_inputs)
    payload["paper"]["pagination_rules"]["cover_page"] = False

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, payload)

    assert captured.value.code == "mock_exam_cover_required"
    assert "封面与考试信息" in captured.value.message_zh


def test_question_search_must_return_the_complete_ordered_theme_chain(
    synthetic_inputs: dict[str, Any],
) -> None:
    search = _search_result(synthetic_inputs)
    search["items"][0]["atomic_chain"].reverse()

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs), search=search)

    assert captured.value.code == "question_search_join_invalid"
    assert "完整主题题链" in captured.value.message_zh


def test_question_search_loader_collects_every_page_before_optimizing(
    synthetic_inputs: dict[str, Any],
) -> None:
    full = _search_result(synthetic_inputs)
    total = len(full["items"])
    first = deepcopy(full)
    first["items"] = full["items"][:2]
    first["counts"]["returned_theme_cards"] = 2
    first["page"] = {
        "limit": 2,
        "returned": 2,
        "total_theme_cards": total,
        "has_more": True,
        "next_cursor": "SYN-CURSOR-2",
    }
    second = deepcopy(full)
    second["items"] = full["items"][2:]
    second["counts"]["returned_theme_cards"] = total - 2
    second["page"] = {
        "limit": 50,
        "returned": total - 2,
        "total_theme_cards": total,
        "has_more": False,
        "next_cursor": None,
    }
    observed_payloads: list[dict[str, Any]] = []

    def loader(payload: dict[str, Any]) -> dict[str, Any]:
        observed_payloads.append(deepcopy(payload))
        assert payload["cursor"] == "SYN-CURSOR-2"
        return deepcopy(second)

    response = PaperBlueprintWorkbench().optimize(
        _fixed_mock_payload(synthetic_inputs),
        theme_catalog=deepcopy(synthetic_inputs["theme_catalog"]),
        question_search_result=first,
        question_search_loader=loader,
        paper_format_preset=default_shanghai_theme_preset(),
        curriculum_catalog=deepcopy(synthetic_inputs["curriculum_catalog"]),
        curriculum_mapping=deepcopy(synthetic_inputs["curriculum_mapping"]),
        metadata_overlay=deepcopy(synthetic_inputs["metadata_overlay"]),
    )

    assert response["status"] == "feasible"
    assert len(observed_payloads) == 1
    assert response["search_diagnostics"]["allowed_theme_count"] == total
    assert response["search_diagnostics"]["question_search_page"][
        "source_page_count"
    ] == 2


def test_incomplete_injected_question_search_page_fails_closed(
    synthetic_inputs: dict[str, Any],
) -> None:
    search = _search_result(synthetic_inputs)
    total = len(search["items"])
    search["items"] = search["items"][:1]
    search["counts"]["returned_theme_cards"] = 1
    search["page"] = {
        "limit": 1,
        "returned": 1,
        "total_theme_cards": total,
        "has_more": True,
        "next_cursor": "SYN-MISSING-NEXT-PAGE",
    }

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs), search=search)

    assert captured.value.code == "question_search_incomplete"
    assert "补齐全部主题卡" in captured.value.message_zh


def test_curriculum_search_cannot_claim_knowledge_tag_inference_as_mapping(
    synthetic_inputs: dict[str, Any],
) -> None:
    search = _search_result(synthetic_inputs)
    search["curriculum"]["explicit_mapping_only"] = False
    search["curriculum"]["knowledge_tag_inference_used"] = True

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, _fixed_mock_payload(synthetic_inputs), search=search)

    assert captured.value.code == "question_search_curriculum_evidence_blocked"
    assert "不能由 K 标签反推教材节" in captured.value.message_zh


def test_preview_cannot_freeze_without_question_content_or_expandable_reference(
    synthetic_inputs: dict[str, Any],
) -> None:
    catalog = deepcopy(synthetic_inputs["theme_catalog"])
    target = next(
        row
        for paper in catalog["papers"]
        for group in paper["theme_groups"]
        for row in group["atomic_chain"]
        if row["atomic_part_id"] == "SYN-A-RA1"
    )
    target.pop("detail_endpoint")
    assert target["visible_summary_zh"]
    overlay = deepcopy(synthetic_inputs["metadata_overlay"])
    overlay["atomics"]["SYN-A-RA1"].pop("expandable_content_ref")

    response = _optimize(
        synthetic_inputs,
        _fixed_mock_payload(synthetic_inputs),
        catalog=catalog,
        metadata_overlay=overlay,
    )

    assert response["status"] == "infeasible"
    assert response["candidates"] == []
    assert any(
        gap["code"] == "preview_content_missing"
        for gap in response["inventory_gaps"]
    )
    assert any(
        option["code"] == "complete_preview_content"
        for option in response["relaxation_options"]
    )


def test_coverage_max_conflict_has_chinese_field_level_guidance(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _daily_payload(synthetic_inputs, selection_unit="dependency")
    payload["hard_constraints"]["coverage"] = {
        "K": {"K-SYN-REDOX": {"min": 0, "max": 0}}
    }
    payload["hard_constraints"]["difficulty_distribution"] = {}
    search = _search_result(
        synthetic_inputs, include_theme_ids={THEME_REDOX_A, THEME_REDOX_B}
    )

    response = _optimize(synthetic_inputs, payload, search=search)

    assert response["status"] == "infeasible"
    assert any(
        conflict["code"] == "coverage_bound_unsatisfied"
        and conflict["details"]["label"] == "K-SYN-REDOX"
        and conflict["details"]["maximum"] == 0
        for conflict in response["conflicts"]
    )
    assert any(
        option["code"] == "relax_coverage_bounds_explicitly"
        and "提高/移除覆盖上限" in option["message_zh"]
        for option in response["relaxation_options"]
    )


def test_invalid_mode_raises_sanitized_chinese_error(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _mock_payload(synthetic_inputs)
    payload["mode"] = "standalone_choice_section"

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, payload)

    assert captured.value.code == "paper_blueprint_mode_invalid"
    assert "mock_exam" in captured.value.message_zh
    assert "daily_practice" in captured.value.message_zh


def test_mock_exam_missing_required_exam_metadata_raises_chinese_error(
    synthetic_inputs: dict[str, Any],
) -> None:
    payload = _mock_payload(synthetic_inputs)
    payload["paper"].pop("exam_name_zh")

    with pytest.raises(PaperBlueprintWorkbenchError) as captured:
        _optimize(synthetic_inputs, payload)

    assert captured.value.code == "paper_blueprint_request_invalid"
    assert "考试名称" in captured.value.message_zh


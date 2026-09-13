from __future__ import annotations

from copy import deepcopy

import pytest

from integrations.deeptutor_shchem_v1.paper_export_alias_projection import (
    PaperExportAliasProjectionError,
    export_display_title,
    project_direct_unit_scan,
    project_explicit_alias_units,
)
from integrations.deeptutor_shchem_v1.paper_export_workbench import _theme_rows


def dependency(*prior: str) -> dict[str, object]:
    return {
        "kind": "prior_part_only" if prior else "independent",
        "prior_atomic_part_ids": list(prior),
        "status": "validated_explicit",
    }


def unit(node_id: str, sequence: int, *prior: str) -> dict[str, object]:
    return {
        "atomic_part_id": node_id,
        "atomic_sequence_in_printed": sequence,
        "atomic_sequence_status": "known_explicit",
        "item_type": "short_fill",
        "label_summary": {"status": "complete", "primary_K": "K01"},
        "answer": {"availability": "present_part_aligned"},
        "dependency": dependency(*prior),
        "visible_summary_zh": f"{node_id}题意",
        "response_requirement_zh": f"{node_id}要求",
    }


def parent(
    parent_id: str = "MASTER-A",
    *,
    aliases: list[dict[str, object]] | None = None,
    sequence: int = 1,
    prior: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "atomic_part_id": parent_id,
        "printed_question_id": "MASTER-PQ-7",
        "printed_question_number": "7",
        "printed_sequence": 4,
        "printed_sequence_status": "known_explicit",
        "atomic_sequence_in_printed": sequence,
        "atomic_sequence_status": "known_explicit",
        "item_type": None,
        "label_summary": {"status": "blocked", "reason": "per_alias_unit"},
        "answer": {"availability": "per_alias_unit"},
        "dependency": dependency(*prior),
        "visible_summary_zh": None,
        "response_requirement_zh": None,
        "alias_units": aliases or [],
    }


def catalog(
    rows: list[dict[str, object]], title: str = "一、金属锡"
) -> dict[str, object]:
    paper = {
        "id": "MASTER-PAPER",
        "title": "合成测试来源卷",
        "source_metadata": {"year": "2026", "region": "上海"},
    }
    return {
        "scope": "master",
        "papers": [
            {
                "paper": paper,
                "theme_groups": [
                    {
                        "paper": paper,
                        "theme": {"id": "MASTER-THEME", "title": title, "sequence": 1},
                        "shared_context": {"materials": []},
                        "dependencies": {},
                        "counts": {
                            "atomic_total": len(rows),
                            "display_atomic_units": sum(
                                max(1, len(row.get("alias_units", []))) for row in rows
                            ),
                        },
                        "atomic_chain": rows,
                    }
                ],
            }
        ],
    }


def selection(unit_name: str = "theme", target: str | None = None) -> dict[str, object]:
    return {
        "scope": "master",
        "selection_unit": unit_name,
        "theme_id": "MASTER-THEME",
        "target_atomic_id": target,
        "expected_data_snapshot_id": "a" * 64,
    }


def test_whole_theme_expands_units_and_preserves_master_printed_parent() -> None:
    source = catalog(
        [parent(aliases=[unit("WAVE-A1", 1), unit("WAVE-A2", 2, "WAVE-A1")])]
    )
    result = project_explicit_alias_units(source, [selection()], scope="master")
    group = result.catalog["papers"][0]["theme_groups"][0]
    rows = group["atomic_chain"]
    by_source = {
        binding.source_node_id: output_id
        for output_id, binding in result.content_bindings.items()
    }

    assert [row["atomic_part_id"] for row in rows] == [
        by_source["WAVE-A1"],
        by_source["WAVE-A2"],
    ]
    assert [row["atomic_sequence_in_printed"] for row in rows] == [1, 2]
    assert all(row["printed_question_id"] == "MASTER-PQ-7" for row in rows)
    assert all(row["printed_question_number"] == "7" for row in rows)
    assert rows[1]["dependency"]["prior_atomic_part_ids"] == [by_source["WAVE-A1"]]
    assert rows[0]["answer"] != rows[1]["answer"] or rows[0] is not rows[1]
    assert result.expanded_parent_count == 1
    assert result.expanded_unit_count == 2
    assert (
        source["papers"][0]["theme_groups"][0]["atomic_chain"][0]["atomic_part_id"]
        == "MASTER-A"
    )


def test_content_binding_routes_child_to_wave1_without_losing_master_parent() -> None:
    result = project_explicit_alias_units(
        catalog([parent(aliases=[unit("WAVE-A1", 1)])]),
        [selection()],
        scope="master",
    )
    output_id, binding = next(iter(result.content_bindings.items()))
    assert (binding.source_scope, binding.source_node_id) == ("wave1", "WAVE-A1")
    assert binding.master_parent_atomic_id == "MASTER-A"
    rows = _theme_rows(
        result.catalog,
        {"MASTER-THEME"},
        content_bindings=result.content_bindings,
    )
    assert rows[0]["_export_content_scope"] == "wave1"
    assert rows[0]["atomic_part_id"] == output_id
    assert rows[0]["_export_content_node_id"] == "WAVE-A1"
    assert rows[0]["_export_alias_parent_atomic_id"] == "MASTER-A"


def test_direct_unit_binding_reads_parent_but_selects_exact_unit() -> None:
    direct_unit = unit("DIRECT-P2", 2)
    direct_unit["source_scope"] = "master"
    direct_unit["source_parent_atomic_id"] = "MASTER-A"
    result = project_explicit_alias_units(
        catalog([parent(aliases=[direct_unit])]), [selection()], scope="master"
    )
    output_id, binding = next(iter(result.content_bindings.items()))
    assert (binding.source_scope, binding.source_node_id) == ("master", "MASTER-A")
    assert binding.source_unit_node_id == "DIRECT-P2"
    rows = _theme_rows(
        result.catalog,
        {"MASTER-THEME"},
        content_bindings=result.content_bindings,
    )
    assert rows[0]["atomic_part_id"] == output_id
    assert rows[0]["_export_content_node_id"] == "MASTER-A"
    assert rows[0]["_export_content_unit_node_id"] == "DIRECT-P2"


def test_direct_unit_scan_never_inherits_parent_answer_or_analysis() -> None:
    scan = {
        "master_node_id": "MASTER-A",
        "candidate_analysis": {"solution_path_zh": ["PARENT-SOLUTION"]},
        "risks_and_limits": {"common_errors_zh": ["PARENT-RISK"]},
        "evidence_descriptors": [
            {"crop_id": "QUESTION-PARENT", "evidence_role": "question"}
        ],
        "minimal_atomic_units": [
            {
                "atomic_part_id": "DIRECT-P1",
                "hierarchy": {"atomic_part_id": "DIRECT-P1"},
                "visible_summary_zh": "第一问",
                "classification": {"item_type": "short_fill"},
                "cognitive_difficulty": {"cognitive_prelabel": "D1"},
                "dependency": {"prior_atomic_part_ids": []},
                "answer_boundary": {
                    "availability": "present_part_aligned",
                    "authority": "nonofficial_reference",
                    "independently_verified": False,
                },
                "reference_answer": {"reference_answer_text": "ANSWER-P1"},
            },
            {
                "atomic_part_id": "DIRECT-P2",
                "hierarchy": {"atomic_part_id": "DIRECT-P2"},
                "visible_summary_zh": "第二问",
                "classification": {"item_type": "short_fill"},
                "cognitive_difficulty": {"cognitive_prelabel": "D1"},
                "dependency": {"prior_atomic_part_ids": ["DIRECT-P1"]},
                "answer_boundary": {
                    "availability": "present_part_aligned",
                    "authority": "nonofficial_reference",
                    "independently_verified": False,
                },
                "reference_answer": {"reference_answer_text": "ANSWER-P2"},
            },
        ],
    }
    projected = project_direct_unit_scan(scan, "DIRECT-P2")
    assert projected["visible_summary_zh"] == "第二问"
    assert projected["reference_answer"]["reference_answer_text"] == "ANSWER-P2"
    assert projected["dependency"]["prior_atomic_part_ids"] == ["DIRECT-P1"]
    assert projected["evidence_descriptors"] == scan["evidence_descriptors"]
    assert "candidate_analysis" not in projected
    assert "risks_and_limits" not in projected


def test_shared_wave_source_across_master_themes_keeps_distinct_output_ids() -> None:
    source = catalog([parent(aliases=[unit("WAVE-SHARED", 1)])])
    second_group = deepcopy(source["papers"][0]["theme_groups"][0])
    second_group["theme"] = {"id": "MASTER-THEME-2", "title": "二、氯气", "sequence": 2}
    second_group["atomic_chain"] = [
        parent("MASTER-B", aliases=[unit("WAVE-SHARED", 1)])
    ]
    source["papers"][0]["theme_groups"].append(second_group)
    second_selection = selection()
    second_selection["theme_id"] = "MASTER-THEME-2"

    result = project_explicit_alias_units(
        source, [selection(), second_selection], scope="master"
    )
    bindings = list(result.content_bindings.values())

    assert len(bindings) == 2
    assert {binding.source_node_id for binding in bindings} == {"WAVE-SHARED"}
    assert len({binding.output_atomic_id for binding in bindings}) == 2
    assert {binding.master_parent_atomic_id for binding in bindings} == {
        "MASTER-A",
        "MASTER-B",
    }


def test_dependency_partial_parent_expands_to_every_explicit_child() -> None:
    result = project_explicit_alias_units(
        catalog([parent(aliases=[unit("WAVE-A1", 1), unit("WAVE-A2", 2)])]),
        [selection("dependency", "MASTER-A")],
        scope="master",
    )
    by_source = {
        binding.source_node_id: output_id
        for output_id, binding in result.content_bindings.items()
    }
    assert [item["target_atomic_id"] for item in result.selections] == [
        by_source["WAVE-A1"],
        by_source["WAVE-A2"],
    ]
    assert all(item["selection_unit"] == "dependency" for item in result.selections)


def test_atomic_partial_one_to_many_parent_is_rejected() -> None:
    with pytest.raises(
        PaperExportAliasProjectionError, match="不能作为单一小题加入"
    ) as caught:
        project_explicit_alias_units(
            catalog([parent(aliases=[unit("WAVE-A1", 1), unit("WAVE-A2", 2)])]),
            [selection("atomic", "MASTER-A")],
            scope="master",
        )
    assert caught.value.code == "paper_export_alias_partial_ambiguous"


def test_whole_theme_takes_precedence_over_redundant_ambiguous_partial() -> None:
    result = project_explicit_alias_units(
        catalog([parent(aliases=[unit("WAVE-A1", 1), unit("WAVE-A2", 2)])]),
        [selection(), selection("atomic", "MASTER-A")],
        scope="master",
    )
    assert result.selections[1]["target_atomic_id"] == "MASTER-A"


def test_unknown_or_ambiguous_dependency_remains_blocked() -> None:
    rows = [
        parent(aliases=[unit("WAVE-A1", 1), unit("WAVE-A2", 2)]),
        parent("MASTER-B", aliases=[], sequence=1, prior=("MASTER-A",)),
    ]
    with pytest.raises(PaperExportAliasProjectionError) as caught:
        project_explicit_alias_units(catalog(rows), [selection()], scope="master")
    assert caught.value.code == "paper_export_alias_dependency_ambiguous"

    broken = deepcopy(rows[:1])
    broken[0]["alias_units"][0]["dependency"] = dependency("NOT-IN-THEME")
    with pytest.raises(PaperExportAliasProjectionError) as caught:
        project_explicit_alias_units(catalog(broken), [selection()], scope="master")
    assert caught.value.code == "paper_export_alias_dependency_unresolved"


def test_one_to_one_parent_dependency_is_exactly_rewritten() -> None:
    rows = [
        parent(aliases=[unit("WAVE-A1", 1)]),
        parent("MASTER-B", aliases=[], sequence=2, prior=("MASTER-A",)),
    ]
    result = project_explicit_alias_units(catalog(rows), [selection()], scope="master")
    projected = result.catalog["papers"][0]["theme_groups"][0]["atomic_chain"]
    output_id = next(iter(result.content_bindings))
    assert projected[1]["dependency"]["prior_atomic_part_ids"] == [output_id]


@pytest.mark.parametrize(
    ("source", "display"),
    [
        ("一、金属锡", "金属锡"),
        ("十二．电化学", "电化学"),
        ("三. 有机合成", "有机合成"),
        ("1,2-二氯乙烷", "1,2-二氯乙烷"),
        ("2-甲基丙烷", "2-甲基丙烷"),
    ],
)
def test_title_projection_strips_only_explicit_chinese_ordinal(
    source: str, display: str
) -> None:
    assert export_display_title(source)[0] == display
    result = project_explicit_alias_units(
        catalog([parent()], title=source), [selection()], scope="master"
    )
    theme = result.catalog["papers"][0]["theme_groups"][0]["theme"]
    assert theme["title"] == display
    assert theme["source_title_zh"] == source


def test_title_projection_is_idempotent_and_keeps_original_source_title() -> None:
    first = project_explicit_alias_units(
        catalog([parent()], title="一、金属锡"), [selection()], scope="master"
    )
    second = project_explicit_alias_units(
        first.catalog, first.selections, scope="master"
    )
    theme = second.catalog["papers"][0]["theme_groups"][0]["theme"]
    assert theme["title"] == "金属锡"
    assert theme["source_title_zh"] == "一、金属锡"

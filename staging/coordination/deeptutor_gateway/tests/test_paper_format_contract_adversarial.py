from __future__ import annotations

from copy import deepcopy

import pytest
from jsonschema.exceptions import ValidationError

from integrations.deeptutor_shchem_v1.paper_format_presets import (
    PaperFormatContractError,
    build_assembly_blueprint,
    build_document_plans,
    build_render_request,
    run_export_preflight,
    validate_paper_format_preset,
    validate_render_receipts,
)
from staging.coordination.deeptutor_gateway.tests.test_paper_format_and_one_time_model_api import (
    PAPER_VALIDATOR,
    _BoundResolver,
    _blueprint_and_plans,
    _catalog_snapshot,
    _configured_preset,
    _dependency,
    _loader,
    _render_receipts,
    _Resolver,
    _selection,
)


def _assert_preflight_blocked(
    *, preset: dict, blueprint: dict, student_plan: dict, teacher_plan: dict
) -> dict:
    report = run_export_preflight(
        preset=preset,
        blueprint=blueprint,
        student_plan=student_plan,
        teacher_plan=teacher_plan,
    )
    assert report["status"] == "blocked"
    assert report["blocker_count"] > 0
    return report


def test_blocked_pending_review_preset_blocks_preflight():
    preset, blueprint, plans = _blueprint_and_plans()
    preset["template_status"] = "blocked_pending_review"

    report = _assert_preflight_blocked(
        preset=preset,
        blueprint=blueprint,
        student_plan=plans["student"],
        teacher_plan=plans["teacher"],
    )

    assert any(check["status"] == "blocked" for check in report["checks"])


def test_pseudo_independent_choice_theme_title_is_rejected():
    snapshot = _catalog_snapshot()
    snapshot["catalog"]["papers"][0]["theme_groups"][0]["theme"][
        "title"
    ] = "第一部分 选择题（独立板块）"

    with pytest.raises(PaperFormatContractError):
        build_assembly_blueprint(
            [_selection()],
            theme_loader=_loader(snapshot),
            expected_data_snapshot_id=snapshot["data_snapshot_id"],
            preset=_configured_preset(),
        )


def test_nonempty_alias_units_block_assembly_even_without_alias_dependency_kind():
    snapshot = _catalog_snapshot()
    row = snapshot["catalog"]["papers"][0]["theme_groups"][0]["atomic_chain"][0]
    row["alias_units"] = [
        {
            "atomic_part_id": "ALIAS-1",
            "dependency": _dependency("independent", []),
        }
    ]

    blueprint = build_assembly_blueprint(
        [_selection()],
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id=snapshot["data_snapshot_id"],
        preset=_configured_preset(),
    )

    assert blueprint["status"] == "blocked"
    assert any("alias" in blocker["code"] for blocker in blueprint["blockers"])


def test_disordered_explicit_source_rows_are_sorted_or_rejected_without_false_preserved_claim():
    snapshot = _catalog_snapshot()
    rows = snapshot["catalog"]["papers"][0]["theme_groups"][0]["atomic_chain"]
    rows[1], rows[2] = rows[2], rows[1]
    expected_order = [
        row["atomic_part_id"]
        for row in sorted(
            rows,
            key=lambda row: (
                row["printed_sequence"],
                row["atomic_sequence_in_printed"],
            ),
        )
    ]

    try:
        blueprint = build_assembly_blueprint(
            [_selection()],
            theme_loader=_loader(snapshot),
            expected_data_snapshot_id=snapshot["data_snapshot_id"],
            preset=_configured_preset(),
        )
    except PaperFormatContractError:
        return

    bundle = blueprint["theme_bundles"][0]
    assert bundle["final_atomic_ids"] == expected_order
    assert bundle["integrity"]["source_order_preserved"] is True


def test_multiple_scopes_are_blocked_before_cross_scope_assembly():
    master = _catalog_snapshot()
    other = deepcopy(master)
    other["scope"] = "wave"
    other["catalog"]["scope"] = "wave"
    other["catalog"]["papers"][0]["paper"]["id"] = "PAPER-WAVE-1"

    def load(scope: str, expected_data_snapshot_id: str) -> dict:
        assert expected_data_snapshot_id == master["data_snapshot_id"]
        if scope == "master":
            return deepcopy(master)
        if scope == "wave":
            return deepcopy(other)
        raise AssertionError(f"unexpected scope: {scope}")

    selections = [_selection(), {**_selection(), "scope": "wave"}]
    try:
        blueprint = build_assembly_blueprint(
            selections,
            theme_loader=load,
            expected_data_snapshot_id=master["data_snapshot_id"],
            preset=_configured_preset(theme_count=2),
        )
    except PaperFormatContractError as error:
        assert error.code == "multiple_scopes_require_dedup_review"
        return

    assert blueprint["status"] == "blocked"
    assert any(
        blocker["code"] == "multiple_scopes_require_dedup_review"
        for blocker in blueprint["blockers"]
    )


class _MaterialVariantResolver(_Resolver):
    def __init__(self, suffix: str) -> None:
        self.suffix = suffix

    def resolve_shared_material(self, reference: dict) -> dict:
        result = super().resolve_shared_material(reference)
        result["content_blocks"][0]["text_zh"] += self.suffix
        return result


class _PartialMaterialBindingResolver(_Resolver):
    def resolve_shared_material(self, reference: dict) -> dict:
        result = super().resolve_shared_material(reference)
        result["material_id"] = reference["material_id"]
        return result


class _MismatchedMaterialBindingResolver(_BoundResolver):
    def __init__(self, mutation: str) -> None:
        self.mutation = mutation

    def resolve_shared_material(self, reference: dict) -> dict:
        result = super().resolve_shared_material(reference)
        if self.mutation == "material_id":
            result["material_id"] = "OTHER-MATERIAL"
        elif self.mutation == "source_page":
            result["source_page"] = int(reference["page"]) + 1
        elif self.mutation == "source_sha256":
            result["source_sha256"] = "not-a-sha"
        return result


def _bound_blueprint_and_plans() -> tuple[dict, dict, dict]:
    snapshot = _catalog_snapshot()
    preset = _configured_preset()
    blueprint = build_assembly_blueprint(
        [_selection()],
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id=snapshot["data_snapshot_id"],
        preset=preset,
    )
    plans = build_document_plans(
        blueprint,
        preset=preset,
        paper_metadata={"title_zh": "共享材料对抗测试"},
        content_resolver=_BoundResolver(),
    )
    return preset, blueprint, plans


def test_partial_shared_material_source_binding_fails_closed():
    snapshot = _catalog_snapshot()
    preset = _configured_preset()
    blueprint = build_assembly_blueprint(
        [_selection()],
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id=snapshot["data_snapshot_id"],
        preset=preset,
    )

    with pytest.raises(PaperFormatContractError) as captured:
        build_document_plans(
            blueprint,
            preset=preset,
            paper_metadata={"title_zh": "共享材料不完整绑定测试"},
            content_resolver=_PartialMaterialBindingResolver(),
        )

    assert captured.value.code == "shared_material_source_binding_invalid"


@pytest.mark.parametrize("mutation", ("material_id", "source_page", "source_sha256"))
def test_mismatched_shared_material_source_binding_fails_closed(
    mutation: str,
) -> None:
    snapshot = _catalog_snapshot()
    preset = _configured_preset()
    blueprint = build_assembly_blueprint(
        [_selection()],
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id=snapshot["data_snapshot_id"],
        preset=preset,
    )

    with pytest.raises(PaperFormatContractError) as captured:
        build_document_plans(
            blueprint,
            preset=preset,
            paper_metadata={"title_zh": "共享材料错误绑定测试"},
            content_resolver=_MismatchedMaterialBindingResolver(mutation),
        )

    assert captured.value.code == "shared_material_source_binding_invalid"


def test_declared_source_page_order_rejects_reversed_bound_plan():
    preset, blueprint, plans = _bound_blueprint_and_plans()
    student = deepcopy(plans["student"])
    student["visible"]["theme_sections"][0]["shared_materials"].reverse()

    report = _assert_preflight_blocked(
        preset=preset,
        blueprint=blueprint,
        student_plan=student,
        teacher_plan=plans["teacher"],
    )

    assert any(
        check["check_id"] == "document_plan_integrity"
        and check["status"] == "blocked"
        for check in report["checks"]
    )


def test_shared_material_body_change_changes_content_version_id():
    preset, blueprint, _ = _blueprint_and_plans()
    metadata = {
        "title_zh": "上海高中化学主题式练习",
        "subtitle_zh": "合成测试",
        "version_label_zh": "v1",
    }
    first = build_document_plans(
        blueprint,
        preset=preset,
        paper_metadata=metadata,
        content_resolver=_MaterialVariantResolver("（版本甲）"),
    )
    second = build_document_plans(
        blueprint,
        preset=preset,
        paper_metadata=metadata,
        content_resolver=_MaterialVariantResolver("（版本乙）"),
    )

    assert (
        first["student"]["content_version_id"]
        != second["student"]["content_version_id"]
    )


def test_student_visible_chinese_answer_text_is_an_export_leak():
    preset, blueprint, plans = _blueprint_and_plans()
    student = deepcopy(plans["student"])
    student["visible"]["附录"] = {"答案": "A"}

    report = _assert_preflight_blocked(
        preset=preset,
        blueprint=blueprint,
        student_plan=student,
        teacher_plan=plans["teacher"],
    )

    assert any(
        check["check_id"] == "student_answer_leak" and check["status"] == "blocked"
        for check in report["checks"]
    )


@pytest.mark.parametrize("drift", ["question_number", "question_anchor_id"])
def test_teacher_visible_question_number_or_anchor_drift_blocks_preflight(drift: str):
    preset, blueprint, plans = _blueprint_and_plans()
    teacher = deepcopy(plans["teacher"])
    printed = teacher["visible"]["theme_sections"][0]["printed_questions"][0]
    if drift == "question_number":
        printed["question_number"] = 99
    else:
        printed["question_anchor_id"] = "Q99-99"

    _assert_preflight_blocked(
        preset=preset,
        blueprint=blueprint,
        student_plan=plans["student"],
        teacher_plan=teacher,
    )


def test_teacher_shared_material_duplicate_blocks_preflight():
    preset, blueprint, plans = _blueprint_and_plans()
    teacher = deepcopy(plans["teacher"])
    materials = teacher["visible"]["theme_sections"][0]["shared_materials"]
    materials.append(deepcopy(materials[0]))

    report = _assert_preflight_blocked(
        preset=preset,
        blueprint=blueprint,
        student_plan=plans["student"],
        teacher_plan=teacher,
    )

    assert any(
        "shared_material" in check["check_id"] and check["status"] == "blocked"
        for check in report["checks"]
    )


@pytest.mark.parametrize("drift", ["blueprint", "anchor", "bindings"])
def test_build_render_request_rejects_student_teacher_contract_drift(drift: str):
    preset, blueprint, plans = _blueprint_and_plans()
    preflight = run_export_preflight(
        preset=preset,
        blueprint=blueprint,
        student_plan=plans["student"],
        teacher_plan=plans["teacher"],
    )
    student = deepcopy(plans["student"])
    teacher = deepcopy(plans["teacher"])
    if drift == "blueprint":
        student["blueprint_digest"] = "drifted-blueprint-digest"
    elif drift == "anchor":
        teacher["question_anchor_digest"] = "0" * 64
    else:
        teacher["bindings"]["atomic_part_bindings"][0]["answer_anchor_id"] = "A99-99"

    with pytest.raises(PaperFormatContractError):
        build_render_request(
            preset=preset,
            student_plan=student,
            teacher_plan=teacher,
            preflight_report=preflight,
        )


def test_duplicate_sha256_across_four_render_receipts_blocks_delivery():
    preset, blueprint, plans = _blueprint_and_plans()
    preflight = run_export_preflight(
        preset=preset,
        blueprint=blueprint,
        student_plan=plans["student"],
        teacher_plan=plans["teacher"],
    )
    request = build_render_request(
        preset=preset,
        student_plan=plans["student"],
        teacher_plan=plans["teacher"],
        preflight_report=preflight,
    )
    receipts = _render_receipts(request)
    receipts[1]["sha256"] = receipts[0]["sha256"]

    report = validate_render_receipts(request, receipts)

    assert report["status"] == "blocked"
    assert any(
        check["status"] == "blocked"
        and ("sha" in check["check_id"] or "重复" in check["message_zh"])
        for check in report["checks"]
    )


@pytest.mark.parametrize("tamper", ["margin", "answer_space", "artifact", "teacher_labels"])
def test_nested_preset_tampering_is_rejected(tamper: str):
    preset = _configured_preset()
    if tamper == "margin":
        preset["page_layout"]["margins_mm"]["top"] = 0
    elif tamper == "answer_space":
        preset["student_version"]["answer_space"]["fallback_lines"] = -1
    elif tamper == "artifact":
        preset["rendering"]["artifacts"][0]["format"] = "txt"
    else:
        preset["teacher_version"]["allowed_answer_labels"] = ["nonofficial"]

    with pytest.raises(PaperFormatContractError):
        validate_paper_format_preset(preset)


@pytest.mark.parametrize("tamper", ["layout_type", "theme_count_type"])
def test_static_paper_schema_matches_runtime_for_nested_types(tamper: str):
    preset = _configured_preset()
    if tamper == "layout_type":
        preset["page_layout"] = []
    else:
        preset["per_paper"]["theme_count"]["value"] = "five"

    with pytest.raises(ValidationError):
        PAPER_VALIDATOR.validate(preset)
    with pytest.raises(PaperFormatContractError):
        validate_paper_format_preset(preset)

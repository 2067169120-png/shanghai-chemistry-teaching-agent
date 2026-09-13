from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import ClassVar

import pytest
from jsonschema import Draft202012Validator

from integrations.deeptutor_shchem_v1.one_time_model_api import (
    MODEL_CANDIDATE_SCHEMA_VERSION,
    QUESTION_CLASSIFICATION,
    STUDENT_ANALYSIS,
    THEME_QUESTION_GENERATION,
    DeepSeekChatAdapter,
    OneTimeModelApiError,
    OpenAICompatibleAdapter,
    OpenAIResponsesAdapter,
    build_usage_cost_hint,
    candidate_output_schema,
    create_one_time_api_configuration,
    prepare_connection_test_request,
    prepare_model_candidate_request,
    synthetic_probe_fingerprint,
    validate_model_candidate_output,
    validate_one_time_api_configuration,
)
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    PaperFormatContractError,
    build_assembly_blueprint,
    build_document_plans,
    build_render_request,
    default_shanghai_theme_preset,
    run_export_preflight,
    validate_paper_format_preset,
    validate_render_receipts,
)

ROOT = Path(__file__).resolve().parents[4]
PAPER_SCHEMA_PATH = (
    ROOT
    / "integrations"
    / "deeptutor_shchem_v1"
    / "schemas"
    / "paper_format_contract_v1.schema.json"
)
MODEL_SCHEMA_PATH = (
    ROOT
    / "integrations"
    / "deeptutor_shchem_v1"
    / "schemas"
    / "one_time_model_api_v1.schema.json"
)
PAPER_SCHEMA = json.loads(PAPER_SCHEMA_PATH.read_text(encoding="utf-8"))
MODEL_SCHEMA = json.loads(MODEL_SCHEMA_PATH.read_text(encoding="utf-8"))
PAPER_VALIDATOR = Draft202012Validator(PAPER_SCHEMA)
MODEL_VALIDATOR = Draft202012Validator(MODEL_SCHEMA)


def _dependency(kind: str, prior: list[str]) -> dict:
    return {
        "kind": kind,
        "prior_atomic_part_ids": prior,
        "explicit_prior_edge_count": len(prior),
        "status": "validated_explicit",
    }


def _atomic(
    atomic_id: str,
    printed_id: str,
    sequence: int,
    *,
    prior: list[str] | None = None,
    item_type: str = "short_answer",
) -> dict:
    prior = prior or []
    kind = (
        "independent"
        if not prior
        else ("one_prior_part" if len(prior) == 1 else "multiple_prior_parts")
    )
    return {
        "atomic_part_id": atomic_id,
        "printed_question_id": printed_id,
        "printed_question_number": str(sequence),
        "printed_sequence": sequence,
        "atomic_sequence_in_printed": 1,
        "item_type": item_type,
        "label_summary": {
            "primary_K": ["K-test"],
            "A": ["A-test"],
            "C": ["C-test"],
            "R": ["R-test"],
            "RP": ["RP-test"],
        },
        "answer": {
            "availability": "present_part_aligned",
            "source_authority": "nonofficial",
            "has_quality_note": False,
        },
        "dependency": _dependency(kind, prior),
        "detail_endpoint": f"/api/test/{atomic_id}",
        "alias_units": [],
    }


def _theme_group(suffix: str = "1") -> dict:
    a1, a2, a3, a4 = [f"A{suffix}-{index}" for index in range(1, 5)]
    return {
        "theme": {
            "id": f"T{suffix}",
            "title": f"主题{suffix}：共享情境",
            "sequence": int(suffix) if suffix.isdigit() else 1,
            "sequence_status": "known_explicit",
            "page_span": {
                "start_page": 1,
                "end_page": 2,
                "page_numbers": [1, 2],
                "status": "explicit_visual_evidence_union",
            },
            "parent_chain_status": "complete",
        },
        "counts": {"atomic": 4},
        "shared_context": {
            "context_summary_zh": f"主题{suffix}的共同材料。",
            "context_status": "candidate_context",
            "material_count": 2,
            "materials": [
                {
                    "material_id": f"M{suffix}-1",
                    "type": "text",
                    "page": 1,
                    "preview_allowed": True,
                    "used_by_atomic_count": 4,
                },
                {
                    "material_id": f"M{suffix}-2",
                    "type": "chart",
                    "page": 2,
                    "preview_allowed": True,
                    "used_by_atomic_count": 2,
                },
            ],
        },
        "dependencies": {
            "independent": 1,
            "shared_material_only": 0,
            "one_prior_part": 2,
            "multiple_prior_parts": 1,
            "per_alias_unit": 0,
            "explicit_prior_edge_count": 4,
            "blocked": 0,
        },
        "atomic_chain": [
            _atomic(a1, f"P{suffix}-1", 1, item_type="choice_single"),
            _atomic(a2, f"P{suffix}-2", 2, prior=[a1]),
            _atomic(a3, f"P{suffix}-3", 3, prior=[a1]),
            _atomic(a4, f"P{suffix}-4", 4, prior=[a2, a3], item_type="calculation"),
        ],
    }


def _catalog_snapshot(theme_count: int = 1, snapshot_id: str = "snapshot_demo_v1") -> dict:
    groups = [_theme_group(str(index)) for index in range(1, theme_count + 1)]
    return {
        "data_snapshot_id": snapshot_id,
        "scope": "master",
        "catalog": {
            "schema_version": "shchem.theme-workbench.v1",
            "scope": "master",
            "counts": {},
            "papers": [
                {
                    "paper": {
                        "id": "PAPER-1",
                        "title": "合成测试卷",
                        "source_metadata": {},
                    },
                    "theme_groups": groups,
                }
            ],
            "unassigned_pending_review": {
                "status": "none",
                "count": 0,
                "reason_zh": None,
                "atomic_chain": [],
            },
            "authority": {},
            "integrity": {
                "hash_verified_on_read": True,
                "semantic_invariants_verified_on_read": True,
                "complete_scope_coverage": True,
                "no_duplicate_atomic_parts": True,
                "explicit_order_only": True,
                "dependency_edges_validated": True,
                "answer_text_excluded": True,
                "pixel_reuse_allowed": False,
                "fail_closed": True,
            },
        },
    }


def _loader(snapshot: dict):
    def load(scope: str, expected_data_snapshot_id: str) -> dict:
        assert scope == "master"
        assert expected_data_snapshot_id == snapshot["data_snapshot_id"]
        return deepcopy(snapshot)

    return load


def _set_template_value(field: dict, value) -> None:
    field["value"] = value
    field["verification"] = {
        "status": "project_template_value",
        "evidence_refs": [],
        "verified_for_paper_id": None,
        "note_zh": "教师为本次项目模板填写，未作为永久官方规则。",
    }


def _configured_preset(theme_count: int = 1, total_score: int | None = None) -> dict:
    preset = default_shanghai_theme_preset()
    _set_template_value(preset["per_paper"]["theme_count"], theme_count)
    _set_template_value(preset["per_paper"]["total_score"], total_score or theme_count * 10)
    _set_template_value(preset["per_paper"]["duration_minutes"], 60)
    _set_template_value(
        preset["per_paper"]["scoring_rules"],
        {
            "selection_rule_zh": "选择小问嵌入主题，按本卷标注分值计分。",
            "partial_credit_rule_zh": "简答与计算按建议步骤给分。",
            "other_rule_zh": None,
        },
    )
    _set_template_value(
        preset["structure"]["subquestion_numbering"], "continuous_across_paper"
    )
    return preset


def _selection(
    unit: str = "theme",
    *,
    theme_id: str = "T1",
    atomic_id: str | None = None,
    snapshot_id: str = "snapshot_demo_v1",
) -> dict:
    return {
        "scope": "master",
        "selection_unit": unit,
        "theme_id": theme_id,
        "target_atomic_id": atomic_id,
        "expected_data_snapshot_id": snapshot_id,
    }


class _Resolver:
    scores: ClassVar[dict[str, int]] = {
        "A1-1": 1,
        "A1-2": 2,
        "A1-3": 3,
        "A1-4": 4,
    }

    def resolve_shared_material(self, reference: dict) -> dict:
        return {
            "content_blocks": [
                {
                    "block_type": "paragraph",
                    "text_zh": f"共享材料 {reference['material_id']}",
                    "asset_ref": None,
                    "alt_text_zh": None,
                }
            ],
            "source_label_zh": "来源卷共享材料（本地只读）",
        }

    def resolve_atomic_part(self, reference: dict) -> dict:
        atomic_id = reference["atomic_part_id"]
        suffix = int(atomic_id.rsplit("-", 1)[1])
        return {
            "question_blocks": [
                {
                    "block_type": "paragraph",
                    "text_zh": f"这是 {atomic_id} 的题面。",
                    "asset_ref": None,
                    "alt_text_zh": None,
                }
            ],
            "score": self.scores.get(atomic_id, suffix),
            "answer_space_lines": suffix,
            "reference_answer": {
                "status": "aligned",
                "text_zh": f"{atomic_id} 的来源参考答案",
                "authority_label": "nonofficial",
                "independently_verified": False,
                "source_label_zh": "机构参考答案页",
            },
            "explanation_zh": f"{atomic_id} 的教师解析",
            "explanation_label": "teacher_analysis",
            "pitfalls_zh": ["不要遗漏条件。"],
            "source_label_zh": "题面来自合成夹具；解析为教师候选。",
        }


class _BoundResolver(_Resolver):
    def resolve_shared_material(self, reference: dict) -> dict:
        result = super().resolve_shared_material(reference)
        material_id = reference["material_id"]
        result.update(
            {
                "material_id": material_id,
                "source_crop_id": material_id,
                "source_sha256": (
                    "a" * 64 if material_id.endswith("-1") else "b" * 64
                ),
                "source_page": reference["page"],
            }
        )
        return result


def _blueprint_and_plans():
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
        paper_metadata={
            "title_zh": "上海高中化学主题式练习",
            "subtitle_zh": "合成测试",
            "version_label_zh": "v1",
        },
        content_resolver=_Resolver(),
    )
    return preset, blueprint, plans


def _openai_profile(*, credential_state: str = "configured") -> dict:
    return {
        "profile_id": "teacher-primary",
        "provider_id": "openai",
        "model_id": "gpt-5",
        "base_url_policy": "openai_official_https_v1",
        "credential_ref": "ShanghaiChemWorkbench/model-provider/teacher-primary",
        "revision": "rev_" + "1" * 32,
        "capabilities": ["text", "vision", "structured_output"],
        "allowed_data_classes": [
            "synthetic_only",
            "question_text_redacted",
            "question_image_redacted",
            "deidentified_student_text",
        ],
        "image_egress": "redacted_question_only",
        "last_probe": {
            "status": "succeeded",
            "connection_state": "connected",
            "checked_at": "2026-08-27T10:00:00Z",
            "error_code": None,
            "model_invoked": True,
            "probe_run_id": "probe_" + "2" * 32,
            "receipt_id": "probe-receipt-" + "3" * 32,
            "receipt_sha256": "4" * 64,
            "latency_ms": 123,
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
        },
        "credential_state": credential_state,
        "model_configured": credential_state == "configured",
        "offline_workbench_available": True,
    }


def _openai_configuration() -> dict:
    return create_one_time_api_configuration(
        _openai_profile(),
        route_models={
            "student_analysis": "gpt-5",
            "question_classification": "gpt-5",
            "theme_question_generation": "gpt-5",
            "visual_understanding": "gpt-5",
        },
    )


def _context(data_classes: list[str]) -> dict:
    return {
        "source_snapshot_id": "snapshot_demo_v1",
        "data_classes": data_classes,
        "deidentified": False,
        "theme_context_complete": True,
        "dependency_closure_complete": True,
        "teacher_confirmed_egress": False,
    }


def test_schemas_are_valid_draft_2020_12():
    Draft202012Validator.check_schema(PAPER_SCHEMA)
    Draft202012Validator.check_schema(MODEL_SCHEMA)


def test_default_preset_has_no_permanent_theme_score_duration_or_rules():
    preset = default_shanghai_theme_preset()
    normalized = validate_paper_format_preset(preset)
    assert normalized["structure"]["top_level_unit"] == "theme_big_question"
    assert normalized["structure"]["standalone_choice_section_allowed"] is False
    assert normalized["structure"]["subquestion_numbering"]["value"] is None
    assert all(field["value"] is None for field in normalized["per_paper"].values())
    assert all(field["editable"] is True for field in normalized["per_paper"].values())
    PAPER_VALIDATOR.validate(normalized)


def test_preset_rejects_independent_choice_section():
    preset = default_shanghai_theme_preset()
    preset["structure"]["standalone_choice_section_allowed"] = True
    with pytest.raises(PaperFormatContractError) as caught:
        validate_paper_format_preset(preset)
    assert caught.value.code == "standalone_choice_section_forbidden"


def test_exact_paper_verification_requires_paper_and_evidence():
    preset = _configured_preset()
    field = preset["per_paper"]["theme_count"]
    field["verification"]["status"] = "verified_for_exact_paper"
    with pytest.raises(PaperFormatContractError) as caught:
        validate_paper_format_preset(preset)
    assert caught.value.code == "preset_exact_verification_incomplete"


def test_dependency_closure_preserves_source_order_and_material_once():
    snapshot = _catalog_snapshot()
    preset = _configured_preset()
    blueprint = build_assembly_blueprint(
        [_selection("dependency", atomic_id="A1-4")],
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id="snapshot_demo_v1",
        preset=preset,
    )
    bundle = blueprint["theme_bundles"][0]
    assert bundle["requested_atomic_ids"] == ["A1-4"]
    assert bundle["auto_added_dependency_ids"] == ["A1-1", "A1-2", "A1-3"]
    assert bundle["final_atomic_ids"] == ["A1-1", "A1-2", "A1-3", "A1-4"]
    assert len(bundle["shared_materials"]) == 2
    assert len({row["render_once_key"] for row in bundle["shared_materials"]}) == 2
    assert blueprint["top_level_unit"] == "theme_big_question"
    assert blueprint["standalone_choice_section_allowed"] is False
    first_atomic = bundle["printed_questions"][0]["atomic_parts"][0]
    assert first_atomic["item_type"] == "choice_single"
    PAPER_VALIDATOR.validate(blueprint)


def test_shared_materials_are_canonicalized_by_source_page_before_digest():
    snapshot = _catalog_snapshot()
    materials = snapshot["catalog"]["papers"][0]["theme_groups"][0][
        "shared_context"
    ]["materials"]
    materials.reverse()
    preset = _configured_preset()

    blueprint = build_assembly_blueprint(
        [_selection()],
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id=snapshot["data_snapshot_id"],
        preset=preset,
    )

    theme = blueprint["theme_bundles"][0]
    assert [row["page"] for row in theme["shared_materials"]] == [1, 2]
    assert theme["integrity"]["shared_material_order_basis"] == (
        "source_page_order"
    )
    plans = build_document_plans(
        blueprint,
        preset=preset,
        paper_metadata={"title_zh": "来源页顺序测试"},
        content_resolver=_Resolver(),
    )
    for audience in ("student", "teacher"):
        section = plans[audience]["visible"]["theme_sections"][0]
        assert section["shared_material_order_basis"] == "source_page_order"


def test_only_atomic_rejects_prior_dependency():
    snapshot = _catalog_snapshot()
    with pytest.raises(PaperFormatContractError) as caught:
        build_assembly_blueprint(
            [_selection("atomic", atomic_id="A1-4")],
            theme_loader=_loader(snapshot),
            expected_data_snapshot_id="snapshot_demo_v1",
            preset=_configured_preset(),
        )
    assert caught.value.code == "single_atomic_breaks_dependency"


@pytest.mark.parametrize(
    ("mutation", "expected_codes"),
    [
        ("missing", {"dependency_cross_theme_or_missing"}),
        ("future", {"dependency_not_prior"}),
        ("cycle", {"dependency_not_prior", "dependency_cycle"}),
    ],
)
def test_dependency_graph_fails_closed(mutation: str, expected_codes: set[str]):
    snapshot = _catalog_snapshot()
    rows = snapshot["catalog"]["papers"][0]["theme_groups"][0]["atomic_chain"]
    if mutation == "missing":
        rows[-1]["dependency"] = _dependency("one_prior_part", ["UNKNOWN"])
    elif mutation == "future":
        rows[0]["dependency"] = _dependency("one_prior_part", ["A1-4"])
    else:
        rows[0]["dependency"] = _dependency("one_prior_part", ["A1-4"])
        rows[-1]["dependency"] = _dependency("one_prior_part", ["A1-1"])
    with pytest.raises(PaperFormatContractError) as caught:
        build_assembly_blueprint(
            [_selection()],
            theme_loader=_loader(snapshot),
            expected_data_snapshot_id="snapshot_demo_v1",
            preset=_configured_preset(),
        )
    assert caught.value.code in expected_codes


def test_snapshot_drift_is_rejected():
    snapshot = _catalog_snapshot(snapshot_id="snapshot_new")
    with pytest.raises(PaperFormatContractError) as caught:
        build_assembly_blueprint(
            [_selection(snapshot_id="snapshot_old")],
            theme_loader=_loader(snapshot),
            expected_data_snapshot_id="snapshot_new",
            preset=_configured_preset(),
        )
    assert caught.value.code == "theme_snapshot_stale"


@pytest.mark.parametrize("theme_count", [4, 5])
def test_four_and_five_theme_templates_are_both_supported(theme_count: int):
    snapshot = _catalog_snapshot(theme_count=theme_count)
    selections = [_selection(theme_id=f"T{index}") for index in range(1, theme_count + 1)]
    blueprint = build_assembly_blueprint(
        selections,
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id="snapshot_demo_v1",
        preset=_configured_preset(theme_count=theme_count),
    )
    assert blueprint["counts"]["theme_count"] == theme_count
    assert blueprint["status"] == "ready_for_content_resolution"


def test_alias_units_are_preserved_and_block_exact_export_until_resolved():
    snapshot = _catalog_snapshot()
    row = snapshot["catalog"]["papers"][0]["theme_groups"][0]["atomic_chain"][0]
    row["dependency"] = _dependency("per_alias_unit", [])
    row["alias_units"] = [
        {"atomic_part_id": "ALIAS-1", "dependency": _dependency("independent", [])},
        {"atomic_part_id": "ALIAS-2", "dependency": _dependency("independent", [])},
    ]
    blueprint = build_assembly_blueprint(
        [_selection()],
        theme_loader=_loader(snapshot),
        expected_data_snapshot_id="snapshot_demo_v1",
        preset=_configured_preset(),
    )
    atomic = blueprint["theme_bundles"][0]["printed_questions"][0]["atomic_parts"][0]
    assert [item["atomic_part_id"] for item in atomic["alias_units"]] == ["ALIAS-1", "ALIAS-2"]
    assert blueprint["status"] == "blocked"
    assert blueprint["blockers"][0]["code"] == "alias_dependency_requires_exact_resolution"


def test_student_teacher_plans_and_export_preflight_are_safe_and_schema_valid():
    preset, blueprint, plans = _blueprint_and_plans()
    student = plans["student"]
    teacher = plans["teacher"]
    student_json = json.dumps(student["visible"], ensure_ascii=False)
    assert "teacher_notes" not in student_json
    assert "来源参考答案" not in student_json
    teacher_rows = [
        atomic
        for theme in teacher["visible"]["theme_sections"]
        for printed in theme["printed_questions"]
        for atomic in printed["atomic_parts"]
    ]
    assert all("teacher_notes" in row for row in teacher_rows)
    assert teacher_rows[0]["teacher_notes"]["answer_label_zh"] == "参考答案（非官方，未独立核验）"
    material_keys = [
        material["render_once_key"]
        for theme in student["visible"]["theme_sections"]
        for material in theme["shared_materials"]
    ]
    assert len(material_keys) == len(set(material_keys)) == 2
    report = run_export_preflight(
        preset=preset,
        blueprint=blueprint,
        student_plan=student,
        teacher_plan=teacher,
    )
    assert report["status"] == "ready_for_renderer"
    assert report["blocker_count"] == 0
    assert report["warning_count"] >= 3
    assert report["publication_allowed"] is False
    for artifact in (preset, blueprint, student, teacher, report):
        PAPER_VALIDATOR.validate(artifact)


def test_bound_shared_material_identity_is_frozen_in_both_document_plans():
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
        paper_metadata={"title_zh": "共享材料来源绑定测试"},
        content_resolver=_BoundResolver(),
    )

    for audience in ("student", "teacher"):
        section = plans[audience]["visible"]["theme_sections"][0]
        assert section["shared_material_order_basis"] == "source_page_order"
        assert [
            (
                material["material_id"],
                material["source_crop_id"],
                material["source_page"],
                material["source_sha256"],
            )
            for material in section["shared_materials"]
        ] == [
            ("M1-1", "M1-1", 1, "a" * 64),
            ("M1-2", "M1-2", 2, "b" * 64),
        ]
    preflight = run_export_preflight(
        preset=preset,
        blueprint=blueprint,
        student_plan=plans["student"],
        teacher_plan=plans["teacher"],
    )
    assert preflight["status"] == "ready_for_renderer"


def test_preflight_blocks_unconfigured_score_time_rules_and_theme_count():
    _, blueprint, plans = _blueprint_and_plans()
    report = run_export_preflight(
        preset=default_shanghai_theme_preset(),
        blueprint=blueprint,
        student_plan=plans["student"],
        teacher_plan=plans["teacher"],
    )
    assert report["status"] == "blocked"
    blocked = {row["check_id"] for row in report["checks"] if row["status"] == "blocked"}
    assert {
        "theme_count_configured",
        "total_score_configured",
        "duration_minutes_configured",
        "scoring_rules_configured",
        "numbering_configured",
    }.issubset(blocked)


def _render_receipts(request: dict) -> list[dict]:
    receipts = []
    for index, artifact in enumerate(request["artifacts"], start=1):
        audience = artifact["audience"]
        receipts.append(
            {
                "schema_version": request["schema_version"],
                "contract_kind": "render_receipt",
                "artifact_id": artifact["artifact_id"],
                "format": artifact["format"],
                "sha256": f"{index:x}" * 64,
                "render_job_id": request["render_job_id"],
                "render_request_digest": request["render_request_digest"],
                "plan_digest": request["plan_digests"][audience],
                "preflight_digest": request["preflight_digest"],
                "page_count": 4 if audience == "student" else 5,
                "rendered_page_count": 4 if audience == "student" else 5,
                "page_review_receipt_sha256": f"{index + 4:x}" * 64,
                "all_pages_visual_pass": True,
                "metadata_privacy_pass": True,
                "content_version_id": request["content_version_id"],
                "blueprint_digest": request["blueprint_digest"],
                "question_anchor_digest": request["question_anchor_digest"],
                "answer_anchor_digest": request["answer_anchor_digest"],
                "figure_count": 2,
            }
        )
    return receipts


def test_render_contract_requires_four_files_and_docx_pdf_parity():
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
    assert [item["artifact_id"] for item in request["artifacts"]] == [
        "student_docx",
        "student_pdf",
        "teacher_docx",
        "teacher_pdf",
    ]
    receipts = _render_receipts(request)
    report = validate_render_receipts(request, receipts)
    assert report["status"] == "local_delivery_candidate"
    assert report["publication_allowed"] is False
    PAPER_VALIDATOR.validate(request)
    for receipt in receipts:
        PAPER_VALIDATOR.validate(receipt)
    PAPER_VALIDATOR.validate(report)


def test_render_contract_blocks_pdf_page_drift():
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
    next(row for row in receipts if row["artifact_id"] == "student_pdf")["page_count"] = 3
    next(row for row in receipts if row["artifact_id"] == "student_pdf")[
        "rendered_page_count"
    ] = 3
    report = validate_render_receipts(request, receipts)
    assert report["status"] == "blocked"
    assert any(row["check_id"] == "student_docx_pdf_parity" for row in report["checks"])


def test_one_time_configuration_routes_four_uses_through_one_credential_ref():
    config = _openai_configuration()
    assert config["offline_workbench_available"] is True
    assert config["production_invocation_enabled"] is False
    assert {route["credential_ref"] for route in config["routes"].values()} == {
        "ShanghaiChemWorkbench/model-provider/teacher-primary"
    }
    assert config["capability_probe"]["observed"]["vision"] == "not_tested"
    serialized = json.dumps(config, ensure_ascii=False)
    assert "api_key" not in serialized.casefold()
    assert config["secret_storage"]["browser_storage"] is False
    assert validate_one_time_api_configuration(config) == config
    MODEL_VALIDATOR.validate(config)


def test_deepseek_configuration_allows_text_routes_and_honestly_disables_vision():
    profile = _openai_profile()
    profile.update(
        {
            "provider_id": "deepseek",
            "model_id": "deepseek-v4-flash",
            "base_url_policy": "deepseek_official_https_v1",
            "capabilities": ["text", "structured_output"],
            "allowed_data_classes": ["synthetic_only", "question_text_redacted"],
            "image_egress": "deny",
        }
    )
    config = create_one_time_api_configuration(
        profile,
        route_models={
            "student_analysis": "deepseek-v4-flash",
            "question_classification": "deepseek-v4-flash",
            "theme_question_generation": "deepseek-v4-pro",
            "visual_understanding": None,
        },
    )
    assert config["routes"]["visual_understanding"]["enabled"] is False
    request = prepare_connection_test_request(
        config, request_id="probe_" + "5" * 32
    )
    assert isinstance(DeepSeekChatAdapter(), DeepSeekChatAdapter)
    assert request.url == "https://api.deepseek.com/chat/completions"
    body = json.loads(request.body)
    assert body["response_format"] == {"type": "json_object"}
    MODEL_VALIDATOR.validate(request.public_preview())


@pytest.mark.parametrize(
    ("base_url", "api_style", "local_policy", "expected_endpoint"),
    [
        (
            "https://gateway.example.test/v1",
            "responses",
            "deny",
            "https://gateway.example.test/v1/responses",
        ),
        (
            "http://127.0.0.1:1234/v1",
            "chat_completions",
            "allow_loopback_http",
            "http://127.0.0.1:1234/v1/chat/completions",
        ),
    ],
)
def test_custom_openai_compatible_configuration_and_adapter_are_endpoint_bound(
    base_url: str,
    api_style: str,
    local_policy: str,
    expected_endpoint: str,
):
    profile = _openai_profile()
    profile.update(
        {
            "provider_kind": "openai_compatible",
            "provider_id": "openai_compatible",
            "display_name": "实验 OpenAI-compatible 服务",
            "model_id": "lab/model:preview",
            "model_status": "unverified_custom_model",
            "base_url_policy": (
                "openai_compatible_loopback_v1"
                if local_policy == "allow_loopback_http"
                else "openai_compatible_public_https_v1"
            ),
            "base_url": base_url,
            "api_style": api_style,
            "local_endpoint_policy": local_policy,
            "capabilities": ["text", "vision", "structured_output"],
        }
    )
    config = create_one_time_api_configuration(
        profile,
        route_models={route: "lab/model:preview" for route in (
            "student_analysis",
            "question_classification",
            "theme_question_generation",
            "visual_understanding",
        )},
    )
    assert config["provider_kind"] == "openai_compatible"
    assert config["provider_id"] == "openai_compatible"
    assert config["model_status"] == "unverified_custom_model"
    assert config["capability_probe"]["observed"]["vision"] == "not_tested"
    assert config["routes"]["visual_understanding"]["capability_evidence"][
        "probed"
    ] == ["text", "structured_output"]
    assert validate_one_time_api_configuration(config) == config
    MODEL_VALIDATOR.validate(config)

    request = prepare_connection_test_request(
        config, request_id="probe_" + "8" * 32
    )
    assert isinstance(
        OpenAICompatibleAdapter(
            base_url=base_url,
            base_url_policy=config["base_url_policy"],
            api_style=api_style,
        ),
        OpenAICompatibleAdapter,
    )
    assert request.url == expected_endpoint
    assert request.provider_id == "openai_compatible"
    assert request.execution_allowed is False
    MODEL_VALIDATOR.validate(request.public_preview())

    candidate_request = prepare_model_candidate_request(
        config,
        task_route=QUESTION_CLASSIFICATION,
        input_payload={
            "atomic_part": {"atomic_part_id": "A1-1", "text_zh": "选择正确说法。"},
            "theme_context": {"shared_material_zh": "共同材料", "prior_parts": []},
        },
        input_context=_context(["question_text_redacted"]),
        request_id="request_" + "8" * 32,
    )
    assert candidate_request.url == expected_endpoint
    assert candidate_request.execution_allowed is False
    MODEL_VALIDATOR.validate(candidate_request.public_preview())


def test_unprobed_custom_model_cannot_be_enabled_for_routes():
    profile = _openai_profile()
    profile.update(
        {
            "provider_kind": "openai_compatible",
            "provider_id": "openai_compatible",
            "display_name": "实验服务",
            "model_id": "model-preview",
            "base_url_policy": "openai_compatible_public_https_v1",
            "base_url": "https://gateway.example.test/v1",
            "api_style": "responses",
            "local_endpoint_policy": "deny",
            "model_status": "unverified_custom_model",
            "capabilities": ["text", "structured_output"],
            "last_probe": None,
            "allowed_data_classes": ["synthetic_only", "question_text_redacted"],
            "image_egress": "deny",
        }
    )
    with pytest.raises(OneTimeModelApiError) as caught:
        create_one_time_api_configuration(
            profile,
            route_models={
                "student_analysis": "model-preview",
                "question_classification": "model-preview",
                "theme_question_generation": "model-preview",
                "visual_understanding": None,
            },
        )
    assert caught.value.code == "route_model_unverified"


def test_probed_unknown_preset_model_can_route_structured_text_but_not_vision():
    profile = _openai_profile()
    profile.update(
        {
            "model_id": "gpt-6-experimental-2026-08",
            "model_status": "unverified_custom_model",
            "capabilities": ["text"],
            "allowed_data_classes": ["synthetic_only", "question_text_redacted"],
            "image_egress": "deny",
        }
    )
    config = create_one_time_api_configuration(
        profile,
        route_models={
            "student_analysis": "gpt-6-experimental-2026-08",
            "question_classification": "gpt-6-experimental-2026-08",
            "theme_question_generation": "gpt-6-experimental-2026-08",
            "visual_understanding": None,
        },
    )
    text_route = config["routes"]["question_classification"]
    assert text_route["enabled"] is True
    assert text_route["capability_evidence"] == {
        "declared": ["text"],
        "catalog": [],
        "probed": ["text", "structured_output"],
        "unknown": ["vision"],
    }
    assert config["routes"]["visual_understanding"]["enabled"] is False
    assert config["capability_probe"]["observed"]["vision"] == "not_tested"
    assert config["production_invocation_enabled"] is False
    assert validate_one_time_api_configuration(config) == config
    MODEL_VALIDATOR.validate(config)


def test_text_only_custom_profile_cannot_masquerade_as_visual_capable():
    profile = _openai_profile()
    profile.update(
        {
            "provider_kind": "openai_compatible",
            "provider_id": "openai_compatible",
            "display_name": "文本服务",
            "model_id": "text-only-preview",
            "model_status": "unverified_custom_model",
            "base_url_policy": "openai_compatible_public_https_v1",
            "base_url": "https://gateway.example.test/v1",
            "api_style": "chat_completions",
            "local_endpoint_policy": "deny",
            "capabilities": ["text", "structured_output"],
            "allowed_data_classes": ["synthetic_only", "question_text_redacted"],
            "image_egress": "deny",
        }
    )
    with pytest.raises(OneTimeModelApiError) as caught:
        create_one_time_api_configuration(
            profile,
            route_models={
                "student_analysis": "text-only-preview",
                "question_classification": "text-only-preview",
                "theme_question_generation": "text-only-preview",
                "visual_understanding": "text-only-preview",
            },
        )
    assert caught.value.code == "route_capability_missing"


def test_openai_synthetic_request_has_fixed_prompt_and_no_secret_material():
    config = _openai_configuration()
    request = prepare_connection_test_request(
        config, request_id="probe_" + "6" * 32
    )
    assert isinstance(OpenAIResponsesAdapter(), OpenAIResponsesAdapter)
    assert request.url == "https://api.openai.com/v1/responses"
    assert request.execution_allowed is False
    assert request.requires_teacher_confirmation is True
    body = json.loads(request.body)
    assert body["store"] is False
    assert body["background"] is False
    assert body["text"]["format"]["type"] == "json_schema"
    assert "Authorization" not in request.headers_without_authorization
    assert request.credential_ref not in request.body.decode("utf-8")
    assert "sk-" not in request.body.decode("utf-8")
    fingerprint = synthetic_probe_fingerprint()
    assert fingerprint["prompt_bytes"] == 101
    assert fingerprint["prompt_sha256"] == "48543a5c638a299313060725e611d2d27dc163eb4044ac91e7b017453a591e3f"
    MODEL_VALIDATOR.validate(request.public_preview())


class _RecordingOpenAIAdapter(OpenAIResponsesAdapter):
    def __init__(self) -> None:
        self.calls = []

    def build_request(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        return super().build_request(**kwargs)


def test_provider_adapter_is_injectable_and_route_selects_classification_model():
    config = _openai_configuration()
    adapter = _RecordingOpenAIAdapter()
    request = prepare_model_candidate_request(
        config,
        task_route=QUESTION_CLASSIFICATION,
        input_payload={
            "atomic_part": {"atomic_part_id": "A1-1", "text_zh": "选择正确说法。"},
            "theme_context": {"shared_material_zh": "共同材料", "prior_parts": []},
        },
        input_context=_context(["question_text_redacted"]),
        request_id="request_" + "7" * 32,
        adapters={"openai": adapter},
    )
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["task_route"] == QUESTION_CLASSIFICATION
    assert adapter.calls[0]["model_id"] == "gpt-5"
    assert request.execution_allowed is False
    assert request.public_preview()["candidate_only"] is True
    body = json.loads(request.body)
    assert body["text"]["format"]["schema"]["properties"]["task_route"][
        "const"
    ] == QUESTION_CLASSIFICATION


def test_theme_generation_request_requires_theme_first_blueprint():
    config = _openai_configuration()
    payload = {
        "task_card": {"purpose": "项目模板"},
        "evidence_pack": {"evidence_refs": ["E1", "E2", "E3", "E4"]},
        "theme_blueprint": {
            "top_level_unit": "selection_section",
            "standalone_choice_section_allowed": True,
        },
    }
    with pytest.raises(OneTimeModelApiError) as caught:
        prepare_model_candidate_request(
            config,
            task_route=THEME_QUESTION_GENERATION,
            input_payload=payload,
            input_context=_context(["question_text_redacted"]),
            request_id="request_" + "8" * 32,
        )
    assert caught.value.code == "generation_blueprint_invalid"


def test_student_identity_and_unredacted_student_input_are_rejected():
    config = _openai_configuration()
    context = _context(["deidentified_student_text"])
    context["deidentified"] = True
    with pytest.raises(OneTimeModelApiError) as caught:
        prepare_model_candidate_request(
            config,
            task_route=STUDENT_ANALYSIS,
            input_payload={
                "student_work": {"student_name": "真实姓名", "answer": "略"},
                "evidence": [],
            },
            input_context=context,
            request_id="request_" + "9" * 32,
        )
    assert caught.value.code == "student_identifier_forbidden"


def test_production_invocation_remains_disabled_until_shared_service_wiring():
    config = _openai_configuration()
    with pytest.raises(OneTimeModelApiError) as caught:
        prepare_model_candidate_request(
            config,
            task_route=QUESTION_CLASSIFICATION,
            input_payload={"atomic_part": {}, "theme_context": {}},
            input_context=_context(["question_text_redacted"]),
            request_id="request_" + "a" * 32,
            dry_run=False,
        )
    assert caught.value.code == "production_invocation_disabled"


def _classification_candidate() -> dict:
    return {
        "schema_version": MODEL_CANDIDATE_SCHEMA_VERSION,
        "candidate_status": "structured_candidate_only",
        "task_route": QUESTION_CLASSIFICATION,
        "source_snapshot_id": "snapshot_demo_v1",
        "candidate": {
            "atomic_part_id": "A1-1",
            "labels": {
                "item_type": "choice_single",
                "primary_knowledge_K": ["K-test"],
                "supporting_knowledge_K": [],
                "ability_A": ["A-test"],
                "context_C": ["C-test"],
                "response_R": ["R-test"],
                "representation_RP": ["RP-test"],
                "cognitive_prelabel": "D2",
            },
            "rationale_zh": ["依据题面与共同材料形成候选。"],
        },
        "evidence_refs": ["E1"],
        "uncertainties_zh": ["仍需教师确认。"],
        "requires_teacher_review": True,
        "authority_boundary": {
            "may_change_answer_authority": False,
            "may_change_source_provenance": False,
            "may_write_central_registry": False,
            "may_unlock_usage_eligibility": False,
            "may_claim_human_review": False,
        },
    }


def test_structured_candidate_schema_and_authority_boundary():
    candidate = _classification_candidate()
    Draft202012Validator(candidate_output_schema(QUESTION_CLASSIFICATION)).validate(
        candidate
    )
    MODEL_VALIDATOR.validate(candidate)
    assert (
        validate_model_candidate_output(
            candidate,
            expected_task_route=QUESTION_CLASSIFICATION,
            expected_source_snapshot_id="snapshot_demo_v1",
            allowed_evidence_refs=["E1"],
            expected_atomic_part_id="A1-1",
        )
        == candidate
    )


def test_candidate_cannot_mutate_answer_authority_or_source_provenance():
    candidate = _classification_candidate()
    candidate["candidate"]["source_authority"] = "official"
    with pytest.raises(OneTimeModelApiError) as caught:
        validate_model_candidate_output(
            candidate,
            expected_task_route=QUESTION_CLASSIFICATION,
            expected_source_snapshot_id="snapshot_demo_v1",
            allowed_evidence_refs=["E1"],
            expected_atomic_part_id="A1-1",
        )
    assert caught.value.code in {
        "candidate_authority_mutation_forbidden",
        "candidate_output_invalid",
    }


def test_usage_hint_reports_tokens_but_does_not_invent_price():
    hint = build_usage_cost_hint(
        {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    )
    assert hint["usage"]["total_tokens"] == 120
    assert hint["estimated_cost"]["amount"] is None
    MODEL_VALIDATOR.validate(hint)

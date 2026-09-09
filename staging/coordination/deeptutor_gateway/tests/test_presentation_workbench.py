from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
FIXTURE_ROOT = (
    ROOT
    / "staging"
    / "coordination"
    / "deeptutor_gateway"
    / "fixtures"
    / "presentation_workbench_v1"
)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1 import (
    presentation_workbench as workbench,
)


def _fixture(name: str) -> tuple[dict, Path]:
    root = FIXTURE_ROOT / name
    return json.loads((root / "input.json").read_text(encoding="utf-8")), root


def _deck(name: str) -> dict:
    value, root = _fixture(name)
    return workbench.compose_deck_json(value, asset_root=root)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _visible_text(deck: dict) -> str:
    return json.dumps(
        [
            {
                "title": slide["title"],
                "purpose": slide["learning_purpose"],
                "action": slide["student_thinking_action"],
                "blocks": slide["content_blocks"],
            }
            for slide in deck["slides"]
        ],
        ensure_ascii=False,
    )


def test_public_api_and_error_contract_are_stable() -> None:
    expected = {
        "PresentationToolchain",
        "PresentationWorkbenchError",
        "validate_presentation_input",
        "compose_deck_json",
        "validate_deck_json",
        "render_presentation_bundle",
        "finalize_slide_visual_review",
        "write_output_manifest",
    }
    assert expected.issubset(set(workbench.__all__))

    error = workbench.PresentationWorkbenchError(
        "presentation_example", "中文错误", 409, details={"stage": "test"}
    )
    assert error.code == "presentation_example"
    assert error.message_zh == "中文错误"
    assert error.status == 409
    assert error.details == {"stage": "test"}


def test_windows_render_commands_keep_the_no_window_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict = {}

    def fake_run(command: list[str], **kwargs: object):
        captured.update(kwargs)
        return workbench.subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(workbench.subprocess, "run", fake_run)
    workbench._run(
        ["fixture-render-command"],
        cwd=tmp_path,
        env={"PATH": ""},
        timeout=1,
    )
    expected = (
        getattr(workbench.subprocess, "CREATE_NO_WINDOW", 0)
        if os.name == "nt"
        else 0
    )
    assert captured["creationflags"] == expected
    if os.name == "nt":
        assert expected != 0
    assert "CREATE_NO_WINDOW" in inspect.getsource(
        workbench._ensure_node_modules_link
    )


@pytest.mark.parametrize(
    ("name", "deck_id", "slide_count", "atomic_count", "minutes"),
    [
        ("fixture_a", "PRES-f5be38bffd932a0528bfda4c", 14, 3, 42),
        ("fixture_b", "PRES-ae08286a8d069a07760887d0", 22, 6, 50),
    ],
)
def test_two_structurally_distinct_fixtures_build_deterministic_decks(
    name: str, deck_id: str, slide_count: int, atomic_count: int, minutes: int
) -> None:
    first = _deck(name)
    second = _deck(name)

    assert _canonical(first) == _canonical(second)
    assert first["deck_id"] == deck_id
    assert len(first["slides"]) == slide_count
    assert first["theme_binding"]["atomic_part_count"] == atomic_count
    assert sum(row["estimated_minutes"] for row in first["slides"]) == minutes
    assert workbench.validate_deck_json(first) == first


def test_second_fixture_proves_question_asset_and_atomic_structure_are_not_hardcoded() -> None:
    first = _deck("fixture_a")
    second = _deck("fixture_b")

    assert first["theme_binding"]["printed_question_count"] == 2
    assert second["theme_binding"]["printed_question_count"] == 3
    assert first["theme_binding"]["atomic_part_count"] == 3
    assert second["theme_binding"]["atomic_part_count"] == 6
    assert first["theme_binding"]["asset_ids_in_order"] == [
        "A-ASSET-APPARATUS-01",
        "A-ASSET-TREND-02",
    ]
    assert second["theme_binding"]["asset_ids_in_order"] == [
        "B-READING-RULER-77",
        "B-DATA-MATRIX-88",
        "B-TRANSFER-CROP-99",
    ]
    assert second["theme_binding"]["printed_question_ids_in_order"] == [
        "B-PRINT-MEASURE-X",
        "B-PRINT-DATA-BETA",
        "B-PRINT-TRANSFER-IV",
    ]
    prompt_titles = [
        row["title"] for row in second["slides"] if row["slide_type"] == "question_prompt"
    ]
    assert any("12（1）" in row for row in prompt_titles)
    assert any("数据任务 β" in row for row in prompt_titles)
    assert any("拓展-Ⅳ" in row for row in prompt_titles)
    assert "FX-T2" not in json.dumps(second, ensure_ascii=False)
    assert "Q10" not in json.dumps(second, ensure_ascii=False)
    source = Path(workbench.__file__).read_text(encoding="utf-8")
    assert "FX-T2" not in source
    assert "_question_by_number" not in source


@pytest.mark.parametrize("name", ["fixture_a", "fixture_b"])
def test_required_lesson_arc_theme_hierarchy_notes_and_candidate_boundary(name: str) -> None:
    deck = _deck(name)
    types = [row["slide_type"] for row in deck["slides"]]
    binding = deck["theme_binding"]

    assert set(workbench.MANDATORY_SLIDE_TYPES).issubset(types)
    assert types[:5] == [
        "cover",
        "objectives",
        "knowledge_thread",
        "textbook_evidence",
        "theme_shared_material",
    ] or types[:6] == [
        "cover",
        "objectives",
        "knowledge_thread",
        "textbook_evidence",
        "textbook_evidence",
        "theme_shared_material",
    ]
    assert types[-4:] == [
        "practice",
        "answer_review",
        "practice",
        "answer_review",
    ] or types[-3:] == ["practice", "answer_review", "homework"]
    assert types[-1] == "homework"
    assert [row["slide_number"] for row in deck["slides"]] == list(
        range(1, len(deck["slides"]) + 1)
    )
    assert len(deck["lesson_flow"]) == len(deck["slides"])
    assert binding["printed_question_count"] == len(binding["printed_question_ids_in_order"])
    assert binding["atomic_part_count"] == len(binding["atomic_part_ids_in_order"])
    assert all("[Sources]" in row["speaker_notes"] for row in deck["slides"])
    assert all("[/Sources]" in row["speaker_notes"] for row in deck["slides"])
    assert deck["candidate_boundary"]["publication_allowed"] is False
    assert deck["candidate_boundary"]["teacher_confirmation_required"] is True
    assert deck["candidate_boundary"]["chemistry_review"] == "pending_teacher_review"
    assert "publication_allowed" not in _visible_text(deck)
    assert "blocked_pending_review" not in _visible_text(deck)


def test_every_printed_question_advances_prompt_then_reveal_with_actual_atomic_ids() -> None:
    deck = _deck("fixture_b")
    question_slides = [
        row
        for row in deck["slides"]
        if row["slide_type"] in {"question_prompt", "question_progression"}
    ]
    by_printed: dict[str, list[dict]] = {}
    for slide in question_slides:
        by_printed.setdefault(slide["theme_context"]["printed_question_id"], []).append(slide)

    assert set(by_printed) == set(deck["theme_binding"]["printed_question_ids_in_order"])
    for rows in by_printed.values():
        assert [row["reveal_stage"] for row in rows] == ["prompt", "reveal"]
        assert rows[0]["theme_context"]["atomic_part_ids"] == rows[1]["theme_context"][
            "atomic_part_ids"
        ]
    assert by_printed["B-PRINT-MEASURE-X"][0]["theme_context"]["atomic_part_ids"] == [
        "B-UNIT-READ-VALUE",
        "B-UNIT-READ-RULE",
    ]
    assert by_printed["B-PRINT-TRANSFER-IV"][0]["theme_context"]["atomic_part_ids"] == [
        "B-UNIT-DIRECTION",
        "B-UNIT-REASON",
        "B-UNIT-IMPROVE",
    ]


def test_asset_hash_dimensions_path_and_anonymity_fail_closed(tmp_path: Path) -> None:
    value, root = _fixture("fixture_a")
    validated = workbench.validate_presentation_input(value, asset_root=root)
    assert validated["theme"]["assets"][0]["asset_id"] == "A-ASSET-APPARATUS-01"

    drift = deepcopy(value)
    drift["theme"]["assets"][0]["sha256"] = "0" * 64
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(drift, asset_root=root)
    assert captured.value.code == "presentation_asset_hash_drift"

    dimensions = deepcopy(value)
    dimensions["theme"]["assets"][0]["pixel_dimensions"] = [1, 1]
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(dimensions, asset_root=root)
    assert captured.value.code == "presentation_asset_dimensions_drift"

    escaped = deepcopy(value)
    escaped["theme"]["assets"][0]["path"] = "../outside.png"
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(escaped, asset_root=root)
    assert captured.value.code == "presentation_asset_path_escape"

    absolute = deepcopy(value)
    absolute["theme"]["assets"][0]["path"] = str((tmp_path / "outside.png").resolve())
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(absolute, asset_root=root)
    assert captured.value.code == "presentation_asset_path_absolute"

    identified = deepcopy(value)
    identified["diagnosis"]["student_name"] = "不应出现"
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(identified, asset_root=root)
    assert captured.value.code == "student_identifier_forbidden"


def test_unknown_fields_authority_upgrade_and_broken_references_are_rejected() -> None:
    value, root = _fixture("fixture_b")

    unknown = deepcopy(value)
    unknown["unexpected_outline"] = []
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(unknown, asset_root=root)
    assert captured.value.code == "presentation_input_invalid"

    publication = deepcopy(value)
    publication["candidate_use"]["publication_allowed"] = True
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(publication, asset_root=root)
    assert captured.value.code == "presentation_candidate_boundary_invalid"

    human = deepcopy(value)
    human["theme"]["human_reviewed"] = True
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(human, asset_root=root)
    assert captured.value.code == "presentation_authority_boundary_invalid"

    missing = deepcopy(value)
    missing["theme"]["printed_questions"][2]["atomic_parts"][1]["dependencies"] = [
        "NOT-AN-ATOMIC-ID"
    ]
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.compose_deck_json(missing, asset_root=root)
    assert captured.value.code == "presentation_dependency_reference_missing"


def test_deck_validation_rejects_timing_source_notes_and_governance_leak() -> None:
    timing = _deck("fixture_a")
    timing["slides"][0]["estimated_minutes"] += 1
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.validate_deck_json(timing)
    assert captured.value.code == "presentation_deck_timing_invalid"

    notes = _deck("fixture_a")
    notes["slides"][0]["speaker_notes"] = "没有来源区块"
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.validate_deck_json(notes)
    assert captured.value.code == "presentation_deck_source_notes_missing"

    leak = _deck("fixture_a")
    leak["slides"][0]["content_blocks"][0]["body"] = "publication_allowed=false"
    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.validate_deck_json(leak)
    assert captured.value.code == "presentation_visible_governance_leak"


def test_visual_review_requires_one_full_size_review_per_slide(tmp_path: Path) -> None:
    deck = _deck("fixture_a")
    qa = {
        "schema_version": workbench.QA_SCHEMA_VERSION,
        "deck_id": deck["deck_id"],
        "artifact_id": deck["deck_id"],
        "qa_status": "pending_full_page_visual_review",
        "machine_checks_passed": True,
        "checks": {},
        "visual_review": {
            "status": "pending",
            "review_kind": "model_full_page_visual_review",
            "human_review": False,
            "slides": [
                {
                    "slide_number": row["slide_number"],
                    "slide_id": row["slide_id"],
                    "status": "pending",
                }
                for row in deck["slides"]
            ],
        },
        "chemistry_review": {"status": "pending_teacher_review", "human_reviewed": False},
        "publication_allowed": False,
        "teacher_confirmation_required": True,
    }
    qa_path = tmp_path / "qa_report.json"
    qa_path.write_text(json.dumps(qa, ensure_ascii=False), encoding="utf-8")
    reviews = [
        {
            "slide_number": row["slide_number"],
            "status": "pass",
            "overflow": "pass",
            "overlap": "pass",
            "font_readability": "pass",
            "formula_fidelity": "pass" if row["chemical_expressions"] else "not_applicable",
            "question_image_clarity": "pass" if row["assets"] else "not_applicable",
            "notes": "逐页全尺寸检查通过。",
        }
        for row in deck["slides"]
    ]

    with pytest.raises(workbench.PresentationWorkbenchError) as captured:
        workbench.finalize_slide_visual_review(qa_path, slide_reviews=reviews[:-1])
    assert captured.value.code == "presentation_visual_review_incomplete"

    completed = workbench.finalize_slide_visual_review(qa_path, slide_reviews=reviews)
    assert completed["qa_status"] == "pass_layout_candidate_teacher_review_pending"
    assert completed["visual_review"]["human_review"] is False
    assert completed["chemistry_review"]["human_reviewed"] is False
    assert completed["publication_allowed"] is False


def test_output_manifest_hashes_files_and_keeps_candidate_boundary(tmp_path: Path) -> None:
    (tmp_path / "deck.json").write_text(
        json.dumps({"deck_id": "PRES-0123456789abcdef01234567"}), encoding="utf-8"
    )
    (tmp_path / "lesson_presentation.pptx").write_bytes(b"PK-fixture")

    manifest = workbench.write_output_manifest(tmp_path)
    rows = {row["path"]: row for row in manifest["files"]}

    assert manifest["artifact_id"] == "PRES-0123456789abcdef01234567"
    assert manifest["candidate_use"] == "local_personal_lesson_preparation_candidate"
    assert manifest["publication_allowed"] is False
    assert manifest["teacher_confirmation_required"] is True
    assert manifest["chemistry_review"] == "pending_teacher_review"
    assert rows["lesson_presentation.pptx"]["sha256"] == _sha256(
        tmp_path / "lesson_presentation.pptx"
    )


@pytest.mark.skipif(
    os.environ.get("RUN_PRESENTATION_RENDER_TESTS") != "1",
    reason="artifact-tool integration render is opt-in",
)
def test_artifact_tool_render_bundle_is_editable_complete_and_candidate_only(tmp_path: Path) -> None:
    value, root = _fixture("fixture_a")
    deck = workbench.compose_deck_json(value, asset_root=root)
    toolchain = workbench.PresentationToolchain.from_environment()

    result = workbench.render_presentation_bundle(
        deck,
        asset_root=root,
        output_dir=tmp_path,
        toolchain=toolchain,
    )

    assert result["artifact_id"] == deck["deck_id"]
    assert result["pptx"].is_file()
    assert result["pptx"].read_bytes()[:2] == b"PK"
    assert result["qa_status"] == "pending_full_page_visual_review"
    assert len(list(result["author_previews"].glob("slide-*.png"))) == len(deck["slides"])
    assert len(list(result["author_previews"].glob("slide-*.layout.json"))) == len(
        deck["slides"]
    )
    assert len(list(result["rendered_slides"].glob("slide-*.png"))) == len(deck["slides"])
    inventory = workbench._pptx_text_and_object_inventory(result["pptx"])
    assert len(inventory["slide_texts"]) == len(deck["slides"])
    assert inventory["shape_count"] > len(deck["slides"])
    assert inventory["picture_count"] >= 2
    assert inventory["notes_count"] == len(deck["slides"])
    assert inventory["notes_have_sources"] is True
    qa = json.loads(result["qa_report"].read_text(encoding="utf-8"))
    assert qa["machine_checks_passed"] is True
    assert qa["publication_allowed"] is False

"""Explicit per-question scope and complete-theme reference, synthetic temp state only."""

from __future__ import annotations

import hashlib
import io
from copy import deepcopy

import pytest
from PIL import Image
from test_personal_visual_attributes import _updates
from test_personal_visual_questions import BATCH_ID
from test_personal_visual_questions import (
    imported_visual_batch as imported_visual_batch,  # noqa: PLC0414
)

from integrations.deeptutor_shchem_v1 import desktop_personal_visual_questions as visual
from integrations.deeptutor_shchem_v1.desktop_personal_visual_questions import (
    PersonalVisualQuestionError,
)


@pytest.fixture
def scoped_context(imported_visual_batch, monkeypatch):
    context = imported_visual_batch
    service = context["service"]
    snapshot, pages = service._batch(BATCH_ID)
    snapshot = deepcopy(snapshot)
    theme = snapshot["candidate"]["paper"]["theme_big_questions"][0]
    originals = {item["evidence_id"]: item for item in snapshot["candidate"]["evidence"]}

    def evidence(identifier, source, box):
        result = deepcopy(originals[source])
        result.update(evidence_id=identifier, bbox=dict(zip(("x", "y", "width", "height"), box)))
        snapshot["candidate"]["evidence"].append(result)

    evidence("SCOPE-Q1", "EV-SRC-Q-A", (0, 0, 0.5, 0.5))
    evidence("SCOPE-Q2", "EV-SRC-Q-C", (0, 0, 0.5, 0.5))
    evidence("SCOPE-SA", "EV-SRC-Q-A", (0.5, 0, 0.5, 0.5))
    evidence("SCOPE-SB", "EV-SRC-Q-C", (0.5, 0, 0.5, 0.5))
    evidence("SCOPE-VA", "EV-SRC-Q-B", (0, 0, 0.5, 0.5))
    evidence("SCOPE-ORPHAN", "EV-SRC-Q-B", (0.5, 0.5, 0.5, 0.5))
    evidence("SCOPE-A1", "EV-SRC-A-A", (0, 0, 0.5, 0.5))
    evidence("SCOPE-A2", "EV-SRC-A-A", (0.5, 0.5, 0.25, 0.5))
    template = theme["shared_materials"][0]
    theme["shared_materials"] = [
        dict(deepcopy(template), shared_material_id="SCOPE-SM-A", content="只关联第一题的共同材料甲",
             evidence_refs=["SCOPE-SA"], chemical_expressions=[], visual_object_refs=["SCOPE-V-A"]),
        dict(deepcopy(template), shared_material_id="SCOPE-SM-B", content="只关联第二题的共同材料乙",
             evidence_refs=["SCOPE-SB"], chemical_expressions=[], visual_object_refs=[]),
        dict(deepcopy(template), shared_material_id="SCOPE-SM-ORPHAN", content="没有小题引用的完整主题共同材料",
             evidence_refs=["SCOPE-ORPHAN"], chemical_expressions=[], visual_object_refs=[]),
    ]
    theme["visual_objects"] = [dict(deepcopy(theme["visual_objects"][0]),
                                     visual_object_id="SCOPE-V-A", evidence_refs=["SCOPE-VA"])]
    theme["context"] = "仅属于完整主题的总背景"
    for index, printed in enumerate(theme["printed_questions"], 1):
        for node in [printed, *printed["atomic_parts"]]:
            node.update(evidence_refs=[f"SCOPE-Q{index}"], visual_object_refs=[],
                        chemical_expressions=[], options=[])
        printed["shared_material_refs"] = [f"SCOPE-SM-{'A' if index == 1 else 'B'}"]
        for atomic in printed["atomic_parts"]:
            answer = atomic["answer"]
            answer["evidence_refs"] = [f"SCOPE-A{index}"]
            answer["alignment"]["evidence_refs"] = [f"SCOPE-A{index}"]
            for expression in answer["chemical_expressions"]:
                expression["evidence_refs"] = [f"SCOPE-A{index}"]
    monkeypatch.setattr(service, "_batch", lambda batch: (snapshot, pages))
    return dict(context, snapshot=snapshot, pages=pages, theme=theme)


def _rows(context):
    catalog = context["service"].catalog(BATCH_ID)
    assert catalog["warnings"] == []
    return catalog["items"]


def _roles(row):
    return {role: {image["evidence_id"] for image in row["images"] if image["role"] == role}
            for role in ("shared_material", "question", "answer")}


def test_each_question_displays_only_explicit_materials_and_visual_edges(scoped_context):
    left, right = _rows(scoped_context)
    assert left["shared_text"] == "只关联第一题的共同材料甲"
    assert right["shared_text"] == "只关联第二题的共同材料乙"
    assert _roles(left)["shared_material"] == {"SCOPE-SA", "SCOPE-VA"}
    assert _roles(right)["shared_material"] == {"SCOPE-SB"}
    assert _roles(left)["question"] == {"SCOPE-Q1"}
    assert _roles(right)["question"] == {"SCOPE-Q2"}
    assert _roles(left)["answer"] and _roles(right)["answer"]
    assert _roles(left)["answer"].isdisjoint(_roles(right)["answer"])
    assert all(not row["teacher_reviewed"] for row in (left, right))


def test_atomic_shared_refs_and_option_visual_refs_are_followed_without_duplicate_images(scoped_context):
    theme = scoped_context["theme"]
    left, right = theme["printed_questions"]
    left["atomic_parts"][0]["shared_material_refs"] = ["SCOPE-SM-B"]
    left["evidence_refs"].extend(["SCOPE-SA", "SCOPE-VA"])
    left["visual_object_refs"] = ["SCOPE-V-A"]
    right["options"] = [{"label": "A", "content": "合成独立选项图", "evidence_refs": [],
                         "chemical_expressions": [], "visual_object_refs": ["SCOPE-V-A"]}]
    left_row, right_row = _rows(scoped_context)
    assert _roles(left_row)["shared_material"] == {"SCOPE-SA", "SCOPE-SB", "SCOPE-VA"}
    assert _roles(left_row)["question"] == {"SCOPE-Q1"}
    assert _roles(right_row)["question"] == {"SCOPE-Q2", "SCOPE-VA"}
    assert len(left_row["images"]) == len({image["evidence_id"] for image in left_row["images"]})


def test_same_pixels_with_distinct_evidence_ids_are_not_assumed_to_be_the_same_relation(scoped_context):
    snapshot = scoped_context["snapshot"]
    alias = deepcopy(next(e for e in snapshot["candidate"]["evidence"] if e["evidence_id"] == "SCOPE-SA"))
    alias["evidence_id"] = "SCOPE-ALIAS"
    snapshot["candidate"]["evidence"].append(alias)
    scoped_context["theme"]["printed_questions"][0]["evidence_refs"].append("SCOPE-ALIAS")
    row = _rows(scoped_context)[0]
    assert "SCOPE-SA" in _roles(row)["shared_material"]
    assert "SCOPE-ALIAS" in _roles(row)["question"]


@pytest.mark.parametrize("refs", [[], ["unknown"], ["MISSING-SHARED"]])
def test_unknown_material_links_warn_without_borrowing_any_shared_text_or_image(scoped_context, refs):
    scoped_context["theme"]["printed_questions"][0]["shared_material_refs"] = refs
    row = _rows(scoped_context)[0]
    assert row["shared_text"] == ""
    assert _roles(row)["shared_material"] == set()
    assert any("共同材料" in warning and ("未明确" in warning or "未对应" in warning) for warning in row["warnings"])


def test_missing_visual_ref_warns_and_does_not_select_a_theme_figure(scoped_context):
    printed = scoped_context["theme"]["printed_questions"][1]
    printed["visual_object_refs"] = ["MISSING-VISUAL"]
    row = _rows(scoped_context)[1]
    assert _roles(row)["question"] == {"SCOPE-Q2"}
    assert any("图形引用尚未对应" in warning for warning in row["warnings"])


def test_unlinked_theme_context_is_warned_in_detail_and_preserved_in_reference(scoped_context):
    theme = scoped_context["theme"]
    theme["shared_materials"] = []
    for printed in theme["printed_questions"]:
        printed["shared_material_refs"] = []
    row = _rows(scoped_context)[0]
    assert row["shared_text"] == ""
    assert any("关联尚未明确" in warning for warning in row["warnings"])
    assert theme["context"] in scoped_context["service"].reference([row])["materials"]


def test_question_or_shared_reference_cannot_relabel_answer_evidence(scoped_context):
    answer_id = next(e["evidence_id"] for e in scoped_context["snapshot"]["candidate"]["evidence"]
                     if e["source_role"] == "answer")
    scoped_context["theme"]["shared_materials"][0]["evidence_refs"].append(answer_id)
    with pytest.raises(PersonalVisualQuestionError, match="角色冲突"):
        scoped_context["service"]._rows(BATCH_ID, scoped_context["snapshot"], scoped_context["pages"])


def test_full_theme_reference_keeps_orphan_material_pixels_context_all_questions_and_answers(scoped_context):
    service = scoped_context["service"]
    rows = _rows(scoped_context)
    assert all("没有小题引用" not in row["shared_text"] for row in rows)
    reference, raw_images = service._compile_reference([rows[0]])
    assert reference["question_count"] == 2 and reference["theme_count"] == 1
    for text in ("仅属于完整主题的总背景", "只关联第一题的共同材料甲", "只关联第二题的共同材料乙",
                 "没有小题引用的完整主题共同材料", "第1题", "第2题", rows[0]["answer_text"], rows[1]["answer_text"]):
        assert text in reference["materials"]
    orphan = next(e for e in scoped_context["snapshot"]["candidate"]["evidence"] if e["evidence_id"] == "SCOPE-ORPHAN")
    page = scoped_context["pages"][(orphan["source_file_id"], orphan["page_number"], orphan["page_sha256"])]
    with Image.open(io.BytesIO(service._file(page["relative_path"]))) as source:
        output = io.BytesIO()
        source.crop((source.width // 2, source.height // 2, source.width, source.height)).save(output, "PNG")
    assert hashlib.sha256(output.getvalue()).hexdigest() in raw_images
    assert sum(asset["purpose"] == "教师参考答案，不用于学生题面" for asset in reference["image_assets"]) == 2


def test_scope_change_keeps_unaffected_revision_selection_and_old_label_history(scoped_context, monkeypatch):
    context = scoped_context
    service, theme = context["service"], context["theme"]
    theme["context"] = ""
    # Second question already has the complete explicit scope, so its visible
    # content is unchanged when moving from the old broad view to the new view.
    theme["printed_questions"][1]["shared_material_refs"] = [m["shared_material_id"] for m in theme["shared_materials"]]
    project = visual._shared_projection
    monkeypatch.setattr(visual, "_shared_projection", lambda theme, nodes=None: project(theme))
    before = _rows(context)
    for row in before:
        options = service.attribute_options(BATCH_ID, row["key"], row["revision"])
        service.save_attributes(BATCH_ID, row["key"], row["revision"],
                                _updates(options, teacher_note="只属于合成旧版本的个人标签"),
                                expected_attribute_revision=options["attributes"]["revision"])
    service.save_selection(before)
    previous_history = service.attribute_store.history(before[0]["key"])
    bytes_before = service.attribute_store.path.read_bytes()
    monkeypatch.setattr(visual, "_shared_projection", project)
    after = _rows(context)
    assert after[0]["revision"] != before[0]["revision"]
    assert after[0]["attribute_warning"] and after[0]["attributes"]["teacher_note"] == ""
    assert after[1]["revision"] == before[1]["revision"]
    assert after[1]["attributes"]["teacher_note"] == "只属于合成旧版本的个人标签"
    assert [value["key"] for value in service.catalog()["selection"]] == [after[1]["key"]]
    assert service.attribute_store.history(before[0]["key"]) == previous_history
    assert service.attribute_store.path.read_bytes() == bytes_before


def test_shared_only_question_evidence_remains_selectable_after_id_deduplication(scoped_context):
    printed = scoped_context["theme"]["printed_questions"][0]
    for node in [printed, *printed["atomic_parts"]]:
        node["evidence_refs"] = ["SCOPE-SA"]
    row = _rows(scoped_context)[0]
    assert _roles(row)["question"] == set()
    assert "SCOPE-SA" in _roles(row)["shared_material"]
    assert row["selection_ready"]
    assert row["attributes"]["material_status"]["missing_visual"] is False
    assert row["attributes"]["material_status"]["question_image_count"] == 2
    assert scoped_context["service"].reference([row])["question_count"] == 2


def test_answer_pixels_alone_do_not_satisfy_question_visual_status(scoped_context):
    printed = scoped_context["theme"]["printed_questions"][0]
    printed["shared_material_refs"] = []
    for node in [printed, *printed["atomic_parts"]]:
        node["evidence_refs"] = []
    row = _rows(scoped_context)[0]
    assert {image["role"] for image in row["images"]} == {"answer"}
    assert row["attributes"]["material_status"]["missing_visual"] is True
    assert row["attributes"]["material_status"]["question_image_count"] == 0
    assert row["selection_ready"] is False

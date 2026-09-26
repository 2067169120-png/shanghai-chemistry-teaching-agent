from copy import deepcopy

import pytest
from docx import Document
from test_paper_export_openapi import _contract, _request, _validator
from test_paper_export_renderer import _all_docx_text

from integrations.deeptutor_shchem_v1 import paper_export_renderer as renderer
from integrations.deeptutor_shchem_v1.paper_export_workbench import (
    PaperExportWorkbenchError,
    _preset_for_request,
    _validated_request,
)
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    validate_paper_format_preset,
)


@pytest.mark.parametrize("choice", [None, False, True])
def test_score_visibility_is_optional_default_off_and_bound_into_preset(choice):
    request = _request()
    if choice is not None:
        request["show_question_scores"] = choice
    original = deepcopy(request)
    normalized = _validated_request(request)
    assert normalized["show_question_scores"] is (choice is True)
    preset = _preset_for_request(normalized, theme_count=1, total_score=6)
    assert preset["student_version"]["show_item_scores"] is (choice is True)
    assert preset["student_version"]["show_total_score"] is (choice is True)
    validate_paper_format_preset(preset)
    assert not list(
        _validator(_contract(), "PaperExportStartRequest").iter_errors(request)
    )
    assert request == original


@pytest.mark.parametrize("bad", [0, 1, "false", "true", None, [], {}])
def test_score_visibility_rejects_nonboolean_request(bad):
    request = {**_request(), "show_question_scores": bad}
    with pytest.raises(PaperExportWorkbenchError):
        _validated_request(request)
    assert list(_validator(_contract(), "PaperExportStartRequest").iter_errors(request))


@pytest.mark.parametrize("show_scores", [False, True])
def test_question_scores_follow_choice_but_teacher_scoring_is_complete(
    tmp_path, monkeypatch, show_scores
):
    bundle = renderer.build_synthetic_demo_bundle()
    preset = deepcopy(bundle["preset"])
    preset["student_version"]["show_item_scores"] = show_scores
    preset["student_version"]["show_total_score"] = show_scores
    for audience in ("student", "teacher"):
        plan = bundle[f"{audience}_plan"]
        target = tmp_path / f"{audience}.docx"
        renderer.build_docx_from_plan(plan, preset=preset, output_path=target)
        doc = Document(target)
        question_paragraphs = "\n".join(
            p.text
            for p in doc.paragraphs
            if p.style.name in {"ShChemQuestion", "ShChemThemeHeading"}
        )
        assert (" 分）" in question_paragraphs) is show_scores
        text = _all_docx_text(target)
        assert renderer.audit_docx(target, plan=plan, preset=preset)["status"] == "pass"
        monkeypatch.setattr(renderer, "_pdf_text", lambda _path, value=text: value)
        assert (
            renderer._pdf_text_audit(target, plan=plan, preset=preset)["status"]
            == "pass"
        )
        if audience == "student":
            assert "评分（" not in text
            assert ("建议满分" in text) is show_scores
        else:
            for number, score in ((1, 2), (2, 3), (3, 5)):
                assert f"第{number}题评分（{score} 分）" in text
            incomplete = text.replace("第2题评分（3 分）", "第2题评分")
            assert not renderer._score_labels_present(incomplete, plan, show_scores)


def test_multi_part_scoring_names_parent_total_and_each_part():
    printed = {
        "question_number": 33,
        "atomic_parts": [
            {"part_label_zh": "（3）第一空", "score": 2},
            {"part_label_zh": "（3）第二空", "score": 3},
        ],
    }
    assert (
        renderer._teacher_scoring_label(printed, 0)
        == "第33题（本题共 5 分）（3）第一空评分（2 分）"
    )
    assert (
        renderer._teacher_scoring_label(printed, 1) == "第33题（3）第二空评分（3 分）"
    )

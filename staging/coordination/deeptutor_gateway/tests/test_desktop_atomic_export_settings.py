from copy import deepcopy

import pytest
from docx import Document

from integrations.deeptutor_shchem_v1 import paper_export_renderer as renderer
from integrations.deeptutor_shchem_v1.desktop_facade import (
    DesktopFacadeError,
    DesktopWorkbenchFacade,
)
from integrations.deeptutor_shchem_v1.paper_format_presets import (
    PaperFormatContractError,
    _normalize_atomic_content,
)


def frozen(questions):
    return {
        "payload": {"duration_minutes": 20},
        "preview_model": {"title": "逐项设置测试", "themes": [{"questions": questions}]},
    }


def test_frozen_preview_emits_identity_bound_settings_without_mutation():
    source = frozen([
        {"key": "A1", "source_ref": {"atomic_part_id": "A1"}, "score": 2, "answer_space": 0},
        {"key": "EXPALIAS-A2", "score": 30, "answer_space": 8},
    ])
    original = deepcopy(source)
    request = DesktopWorkbenchFacade._paper_export_request(source, [])
    assert request["atomic_settings"] == {
        "A1": {"score": 2, "answer_space_lines": 0},
        "EXPALIAS-A2": {"score": 30, "answer_space_lines": 8},
    }
    assert request["score_per_atomic"] == 1  # Legacy fallback is not used for these IDs.
    assert source == original


@pytest.mark.parametrize("questions", [
    [{"key": "A1", "score": 2, "answer_space": 2}, {"score": 3, "answer_space": 2}],
    [{"key": "A1", "score": 2, "answer_space": 2}, {"key": "A1", "score": 3, "answer_space": 2}],
    [{"key": "A1", "source_ref": {"atomic_part_id": "OTHER"}, "score": 2, "answer_space": 2}],
])
def test_incomplete_or_conflicting_bindings_are_not_guessed(questions):
    with pytest.raises(DesktopFacadeError) as error:
        DesktopWorkbenchFacade._paper_export_request(frozen(questions), [])
    assert error.value.code == "paper_export_atomic_binding_invalid"


def test_unbound_legacy_uniform_preview_remains_compatible():
    request = DesktopWorkbenchFacade._paper_export_request(
        frozen([{"score": 2, "answer_space": 3}] * 2), []
    )
    assert request["score_per_atomic"] == 2
    assert "atomic_settings" not in request
    with pytest.raises(DesktopFacadeError) as error:
        DesktopWorkbenchFacade._paper_export_request(
            frozen([{"score": 2, "answer_space": 1}, {"score": 3, "answer_space": 4}]), []
        )
    assert error.value.code == "paper_export_atomic_binding_required"


def test_explicit_answer_lines_survive_plan_and_choice_rendering(monkeypatch):
    original = renderer._SyntheticDemoResolver.resolve_atomic_part

    def resolve(self, reference):
        content = original(self, reference)
        content.update(answer_space_explicit=True, answer_space_lines=7)
        return content

    monkeypatch.setattr(renderer._SyntheticDemoResolver, "resolve_atomic_part", resolve)
    bundle = renderer.build_synthetic_demo_bundle()
    plan = bundle["student_plan"]
    spaces = [a["answer_space"] for s in plan["visible"]["theme_sections"]
              for p in s["printed_questions"] for a in p["atomic_parts"]]
    assert spaces == [{"mode": "ruled_lines_exact", "lines": 7}] * 3
    doc = Document()
    renderer._style_document(doc, bundle["preset"])
    observed = []
    monkeypatch.setattr(renderer, "_add_answer_space", lambda doc, lines, **kw: observed.append(lines))
    renderer._add_theme_sections(
        doc, plan["visible"], audience="student", asset_root=None,
        content_width_dxa=9000, body_size_pt=11, blueprint=bundle["blueprint"],
    )
    assert observed == [7, 7, 7]  # Includes the choice question; no type-based truncation.


def test_explicit_answer_marker_requires_a_boolean():
    content = renderer._SyntheticDemoResolver().resolve_atomic_part({"atomic_part_id": "SYNTH-CO2-A1"})
    content["answer_space_explicit"] = "true"
    with pytest.raises(PaperFormatContractError) as error:
        _normalize_atomic_content(content, 3)
    assert error.value.code == "answer_space_invalid"

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1.desktop_blueprint_generation import (
    generate_blueprint,
)
from integrations.deeptutor_shchem_v1.desktop_prompt_blueprint import (
    PromptPreviewError,
    build_request,
    compile_preview,
)
from integrations.deeptutor_shchem_v1.desktop_theme_structure_reference import (
    REFERENCE_KEY,
    StructureReferenceError,
    compile_structure_reference,
    load_reference,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_blueprint_generation import (
    candidate,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_preparation_provider import (
    _context,
    _Transport,
)
from staging.coordination.deeptutor_gateway.tests.test_desktop_prompt_blueprint import (
    PAYLOAD,
    SECTIONS,
    compiler,
)

WORKSPACE = Path(__file__).resolve().parents[4]
FIXTURE = WORKSPACE / (
    "sh-chem-db/05_命题热点素材/hotspot_theme_pipeline_v1_2026-08-28/"
    "prompt_distillation_workbench_v1/fixtures/read_theme_structure.fengxian_2025_theme2.json"
)


def fixture_loader(_workspace):
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_real_verified_reader_becomes_a_compact_structure_reference():
    digest = load_reference(WORKSPACE)
    before = deepcopy(digest)
    result = compile_structure_reference(WORKSPACE, loader=lambda _: digest)
    assert result["counts"] == {
        "printed_questions": 10,
        "atomic_parts": 13,
        "shared_materials": 3,
        "prior_dependencies": 1,
    }
    assert digest == before
    evidence = "\n".join(result["evidence"]["supports"])
    assert "卷内主题序号：unknown" in evidence
    assert "P10 / A13" in evidence
    assert "不机械复制" in evidence
    assert "比较与开放简答" in evidence and "未识别作答形态" not in evidence
    assert len(evidence) < 6000
    assert "K02" not in evidence and "FX2025" not in evidence
    assert "sha256" not in evidence and "relative_path" not in evidence
    assert "question_visual_scan" not in evidence
    provenance = result["local_provenance"]
    assert len(provenance["source"]["source_refs"]) == 4
    assert len(provenance["adapter"]["sha256"]) == 64
    assert len(provenance["alias_mapping"]["atomic"]) == 13


def test_reference_is_opt_in_and_never_reaches_legacy_compiler_contract(tmp_path):
    calls = []

    def loader(root):
        calls.append(root)
        return fixture_loader(root)

    def checked_compiler(request, **kwargs):
        assert "theme_reference" not in request
        return compiler(request, **kwargs)

    old = compile_preview(
        tmp_path, PAYLOAD, SECTIONS, compiler=checked_compiler, structure_loader=loader
    )
    assert calls == [] and "structure_reference" not in old
    selected = {**PAYLOAD, "theme_reference": REFERENCE_KEY}
    result = compile_preview(
        tmp_path, selected, SECTIONS, compiler=checked_compiler, structure_loader=loader
    )
    assert calls == [tmp_path]
    assert result["evidence"][-1]["source_type"] == "question_structure_candidate"
    assert "S3" in result["task_prompt"] and "P10" in result["task_prompt"]
    assert result["external_call_performed"] is False
    assert build_request(selected, SECTIONS)["section_keys"] == PAYLOAD["section_keys"]


@pytest.mark.parametrize("value", ["arbitrary/path", None, True, [], {}])
def test_unknown_reference_is_rejected(value):
    with pytest.raises(PromptPreviewError, match="试题结构参考"):
        build_request({**PAYLOAD, "theme_reference": value}, SECTIONS)


def test_selected_missing_reference_fails_without_silent_fallback(tmp_path):
    with pytest.raises(PromptPreviewError) as exc:
        compile_preview(
            tmp_path,
            {**PAYLOAD, "theme_reference": REFERENCE_KEY},
            SECTIONS,
            compiler=compiler,
        )
    assert exc.value.code == "prompt_structure_unavailable"
    assert str(tmp_path) not in exc.value.message_zh


@pytest.mark.parametrize(
    "mutation", ["future", "material", "parent", "duplicate", "authority"]
)
def test_broken_reference_cannot_become_prompt_evidence(mutation):
    digest = fixture_loader(WORKSPACE)
    atomic = digest["hierarchy"]["theme"]["printed_questions"][0]["atomic_parts"][0]
    if mutation == "future":
        atomic["prior_atomic_part_ids"] = ["FX2025-EM-S2-Q10-P1"]
    elif mutation == "material":
        atomic["shared_material_id"] = "missing-material"
    elif mutation == "parent":
        atomic["parent_printed_question_id"] = "other-parent"
    elif mutation == "duplicate":
        digest["hierarchy"]["theme"]["printed_questions"].append(
            deepcopy(digest["hierarchy"]["theme"]["printed_questions"][0])
        )
    else:
        digest["authority_gates"]["official_claim_allowed"] = True
    with pytest.raises(StructureReferenceError):
        compile_structure_reference(WORKSPACE, loader=lambda _: digest)


@pytest.mark.parametrize("style", ["responses", "chat_completions"])
def test_reference_reaches_exact_outbound_request_but_provenance_does_not(style):
    local = compile_preview(
        WORKSPACE,
        {**PAYLOAD, "theme_reference": REFERENCE_KEY},
        SECTIONS,
        compiler=compiler,
        structure_loader=fixture_loader,
    )
    model_result = candidate()
    model_result["shared_material_plan"][0]["evidence_refs"] = ["E1", "E2"]
    transport = _Transport(model_result)
    generate_blueprint(_context(api_style=style), local, transport=transport)
    body = transport.requests[0].body.decode("utf-8")
    assert "试题结构参考" in body and "P10 / A13" in body
    assert "E2" in body
    for excluded in (
        "local_provenance",
        "source_refs",
        "FX2025",
        "scan_records.jsonl",
        "K02",
        "fixture-secret",
    ):
        assert excluded not in body


def test_native_reference_choice_compiles_invalidates_and_restores_history(tmp_path):
    from types import SimpleNamespace

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
        PromptBlueprintDialog,
    )

    app = QApplication.instance() or QApplication([])
    saved = []

    class Tasks:
        def submit(self, label, operation, *, on_success, on_failure):
            on_success(operation())

    def compile_local(payload):
        saved.append(deepcopy(payload))
        return compile_preview(
            tmp_path,
            payload,
            SECTIONS,
            compiler=compiler,
            structure_loader=fixture_loader,
        )

    facade = SimpleNamespace(
        prompt_curriculum_sections=lambda: tuple(
            SimpleNamespace(section_key=k, display_label_zh=v)
            for k, v in SECTIONS.items()
        ),
        compile_prompt_blueprint=compile_local,
    )
    dialog = PromptBlueprintDialog(facade, Tasks())
    dialog.title_input.setText(PAYLOAD["title"])
    dialog.goal.setPlainText(PAYLOAD["learning_goal"])
    dialog.sections.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.theme_reference.setCurrentIndex(1)
    dialog.resize(420, 900)
    dialog.show()
    app.processEvents()
    assert dialog.width() == 420
    dialog.compile_button.click()
    assert saved[-1]["theme_reference"] == REFERENCE_KEY
    assert "P10 / A13" in dialog.evidence_output.toPlainText()
    preview = dialog._preview
    dialog.theme_reference.setCurrentIndex(0)
    assert dialog._preview is None and not dialog.task_output.toPlainText()
    result = {"candidate": candidate(), "model_id": "fixture", "latency_ms": 10}
    dialog._history = [{"input": saved[-1], "preview": preview, "result": result}]
    dialog._load_history(1)
    assert dialog.theme_reference.currentData() == REFERENCE_KEY
    assert dialog._preview is preview
    assert "P10 / A13" in dialog.evidence_output.toPlainText()
    dialog.close()

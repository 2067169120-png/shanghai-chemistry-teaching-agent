import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1.desktop_prompt_blueprint import (
    PromptPreviewError,
    build_request,
    compile_preview,
)

SECTIONS = {"TB-M1-C1:1.2": "必修第一册 氧化还原反应"}
PAYLOAD = {
    "title": "氧化还原主题练习",
    "learning_goal": "依据电子转移关系说明氧化还原过程。",
    "section_keys": list(SECTIONS),
    "grade": 10,
}
GATES = dict.fromkeys(
    (
        "human_reviewed",
        "teaching_use_allowed",
        "bank_ingest_allowed",
        "official_status_allowed",
        "publication_allowed",
    ),
    False,
)


def compiler(request, **kwargs):
    assert kwargs["max_digest_chars"] >= 12000
    assert request["desired_output"]["artifact"] == "prompt_bundle"
    assert "central_preflight_receipt" not in request
    return {
        "eligibility": "blueprint_only",
        "gates": GATES.copy(),
        "system_prompt": "系统提示",
        "task_prompt": request["theme"]["title"],
        "evidence_digest": [
            {
                "scope": "氧化还原反应",
                "supports": ["电子转移"],
                "source_path": "local/source.pdf",
                "source_type": "textbook",
            }
        ],
        "bundle_id": "TEST-BUNDLE",
    }


def test_adapter_keeps_teacher_goal_unknown_taxonomy_and_does_not_mutate():
    before = deepcopy(PAYLOAD)
    request = build_request(PAYLOAD, SECTIONS)
    assert request["learning_targets"][0]["primary_K"] == "unknown"
    assert request["learning_targets"][0]["statement"] == PAYLOAD["learning_goal"]
    assert PAYLOAD == before


def test_native_request_matches_real_compiler_contract():
    from jsonschema import Draft202012Validator

    contract = Path(__file__).resolve().parents[4] / (
        "sh-chem-db/05_命题热点素材/hotspot_theme_pipeline_v1_2026-08-28/"
        "prompt_distillation_workbench_v1/contracts/prompt_build_request.schema.json"
    )
    schema = json.loads(contract.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(build_request(PAYLOAD, SECTIONS))


def test_facade_section_labels_include_book_and_chapter():
    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade

    facade = object.__new__(DesktopWorkbenchFacade)
    facade.curriculum_catalog = lambda: {
        "volumes": [
            {
                "volume_title": "必修第一册",
                "chapters": [
                    {
                        "chapter_title": "化学研究的天地",
                        "sections": [
                            {
                                "section_key": "TB-M1-C1:1.2",
                                "display_label_zh": "1.2 物质的量",
                            }
                        ],
                    }
                ],
            }
        ]
    }
    result = facade.prompt_curriculum_sections()
    assert result[0].display_label_zh == "必修第一册 / 化学研究的天地 / 1.2 物质的量"


@pytest.mark.parametrize(
    "change",
    [
        {"section_keys": []},
        {"section_keys": ["unknown"]},
        {"section_keys": list(SECTIONS) * 2},
        {"grade": True},
        {"title": ""},
        {"learning_goal": "字" * 501},
        {"central_preflight_receipt": {}},
    ],
)
def test_bad_teacher_input_rejected(change):
    with pytest.raises(PromptPreviewError):
        build_request({**PAYLOAD, **change}, SECTIONS)


def test_preview_is_local_and_does_not_expose_provenance_paths(tmp_path):
    result = compile_preview(tmp_path, PAYLOAD, SECTIONS, compiler=compiler)
    assert result["external_call_performed"] is False
    assert result["section_labels"] == list(SECTIONS.values())
    assert "source_path" not in result["evidence"][0]


@pytest.mark.parametrize(
    "change",
    [
        {"eligibility": "candidate_generation"},
        {"gates": {}},
        {"gates": {**GATES, "teaching_use_allowed": True}},
        {"evidence_digest": []},
    ],
)
def test_unsafe_compiler_result_is_not_presented(tmp_path, change):
    def invalid(request, **kwargs):
        return {**compiler(request, **kwargs), **change}

    with pytest.raises(PromptPreviewError):
        compile_preview(tmp_path, PAYLOAD, SECTIONS, compiler=invalid)


def test_native_dialog_selects_chapter_and_compiles_offline(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
        PromptBlueprintDialog,
    )

    app = QApplication.instance() or QApplication([])

    class Tasks:
        def submit(self, label, operation, *, on_success, on_failure):
            try:
                result = operation()
            except PromptPreviewError as exc:
                on_failure(exc.message_zh)
            else:
                on_success(result)

    facade = SimpleNamespace(
        prompt_curriculum_sections=lambda: tuple(
            SimpleNamespace(section_key=key, display_label_zh=label)
            for key, label in SECTIONS.items()
        ),
        compile_prompt_blueprint=lambda payload: compile_preview(
            tmp_path, payload, SECTIONS, compiler=compiler
        ),
    )
    dialog = PromptBlueprintDialog(facade, Tasks())
    dialog.resize(420, 760)
    dialog.show()
    app.processEvents()
    assert dialog.width() == 420
    dialog.title_input.setText(PAYLOAD["title"])
    dialog.goal.setPlainText(PAYLOAD["learning_goal"])
    dialog.sections.item(0).setCheckState(Qt.CheckState.Checked)
    dialog.compile_button.click()
    assert dialog.system_output.toPlainText() == "系统提示"
    assert "电子转移" in dialog.evidence_output.toPlainText()
    assert "未调用模型" in dialog.status.text()
    assert dialog.compile_button.isEnabled()
    assert dialog.goal.isEnabled()
    dialog.title_input.setText("修改后的主题")
    assert not dialog.task_output.toPlainText()
    assert "重新编译" in dialog.status.text()
    dialog.close()
    dialog._compiled({})  # Late worker completion after close is ignored.

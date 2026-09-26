"""Real local source QA; --execute makes exactly one saved-profile model call.

Uses isolated personal state. No source edits, OCR, actual-user draft replacement,
or credentials in arguments/output. Run --ui with the desktop Python runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from integrations.deeptutor_shchem_v1.desktop_blueprint_generation import (
    blueprint_prompt,
)
from integrations.deeptutor_shchem_v1.desktop_handout_candidates import (
    HandoutCandidateService,
)
from integrations.deeptutor_shchem_v1.desktop_handout_practice import (
    HandoutPracticeService,
    NativeParagraphs,
)
from integrations.deeptutor_shchem_v1.desktop_handout_prompt_reference import (
    compile_handout_reference,
    native_text,
    reference_options,
)
from integrations.deeptutor_shchem_v1.desktop_prompt_blueprint import compile_preview
from integrations.deeptutor_shchem_v1.desktop_state import DesktopStateStore


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    output = (
        ROOT
        / "runtime/deeptutor_shchem/qa"
        / time.strftime("handout-prompt-%Y%m%d-%H%M%S")
    )
    output.mkdir(parents=True, exist_ok=False)
    state = DesktopStateStore(
        Path(tempfile.mkdtemp(prefix="shchem-handout-prompt-qa-"))
    )
    service = HandoutCandidateService(ROOT, state)
    catalog = service.catalog()["items"]
    native = NativeParagraphs(ROOT)
    validated = 0
    for item in catalog:
        if item["practice_eligible"]:
            for role in ("question", "answer"):
                assert native_text(native.read(item, role)).strip()
            validated += 1
    selected = [
        item
        for item in catalog
        if item["package_id"] == "PKG-033" and item["practice_eligible"]
    ][:3]
    selected += [
        item
        for item in catalog
        if item["package_id"] == "PKG-051"
        and item["printed_number"] == "5"
        and item["practice_eligible"]
    ]
    assert len(selected) == 4 and all(item["practice_eligible"] for item in selected)
    HandoutPracticeService(service).save_draft(
        "电解质与物质检验参考选题",
        [{"key": item["key"], "revision": item["revision"]} for item in selected],
        2,
    )
    option = reference_options(state)[0]
    payload = {
        "title": "离子反应与物质检验主题设计",
        "grade": 10,
        "learning_goal": "结合参考讲义提炼电解质概念、离子方程式正误判断和离子检验的任务设计。围绕共同水样情境设计主题内任务，说明借鉴的考查关系及改写方式，不照搬原题。",
        "section_keys": ["TB-M1-C2:2.2"],
        "handout_reference": {
            "reference_id": option["reference_id"],
            "revision": option["revision"],
            "include_answers": True,
        },
    }
    preview = compile_preview(
        ROOT,
        payload,
        {"TB-M1-C2:2.2": "必修第一册 / 海洋中的卤素资源 / 氧化还原反应和离子反应"},
        handout_loader=lambda choice: compile_handout_reference(service, choice),
    )
    text = blueprint_prompt(preview)
    assert "_{" in text and "^{" in text and "原题表格" in text
    assert "expanded/" not in text and "local_provenance" not in text
    report = {
        "native_questions_verified": validated,
        "selected_count": len(selected),
        "selected": [
            {"package": item["package_id"], "printed_number": item["printed_number"]}
            for item in selected
        ],
        "evidence_count": len(preview["evidence"]),
        "prompt_chars": len(text),
        "state_root": str(state.root),
        "model_invoked": False,
    }
    (output / "preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"stage": "local_verified", "output": str(output), **report},
            ensure_ascii=False,
        ),
        flush=True,
    )
    if args.ui or args.execute:
        from integrations.deeptutor_shchem_v1.desktop_facade import (
            DesktopWorkbenchFacade,
        )
        from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
        from integrations.deeptutor_shchem_v1.model_provider_settings import (
            ModelProviderSettingsStore,
        )

        paths = DesktopPaths.from_workspace(ROOT, state_root=state.root)
        provider = (
            ModelProviderSettingsStore(
                DesktopPaths.from_workspace(ROOT).settings_root, project_root=ROOT
            )
            if args.execute
            else None
        )
        facade = DesktopWorkbenchFacade(
            paths, state_store=state, provider_store=provider
        )
        try:
            if args.ui:
                from PySide6.QtCore import Qt
                from PySide6.QtWidgets import QApplication

                from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
                    WORKBENCH_STYLE,
                    install_font_fallbacks,
                )
                from integrations.deeptutor_shchem_v1.desktop_workbench.prompt_blueprint_dialog import (
                    PromptBlueprintDialog,
                )

                class Tasks:
                    def submit(self, label, operation, *, on_success, on_failure):
                        on_success(operation())

                app = QApplication.instance() or QApplication([])
                install_font_fallbacks()
                app.setStyleSheet(WORKBENCH_STYLE)
                dialog = PromptBlueprintDialog(facade, Tasks())
                dialog.title_input.setText(payload["title"])
                dialog.grade.setCurrentIndex(dialog.grade.findData(payload["grade"]))
                dialog.goal.setPlainText(payload["learning_goal"])
                for index in range(dialog.sections.count()):
                    item = dialog.sections.item(index)
                    if item.data(Qt.ItemDataRole.UserRole) in payload["section_keys"]:
                        item.setCheckState(Qt.CheckState.Checked)
                dialog.handout_reference.setCurrentIndex(1)
                dialog.include_handout_answers.setChecked(True)
                dialog.compile_button.click()
                assert dialog._preview["handout_reference"]["question_count"] == 4
                dialog.show()
                captures = []
                for width in (800, 420):
                    dialog.resize(width, 800)
                    for _ in range(4):
                        app.processEvents()
                    assert dialog.width() == width and dialog.height() == 800
                    assert dialog.inputs_scroll.horizontalScrollBar().maximum() == 0
                    for label, position in (
                        ("top", 0),
                        ("bottom", dialog.inputs_scroll.verticalScrollBar().maximum()),
                    ):
                        dialog.inputs_scroll.verticalScrollBar().setValue(position)
                        app.processEvents()
                        path = output / f"dialog-{width}-{label}.png"
                        assert dialog.grab().save(str(path))
                        captures.append(str(path))
                dialog._show_inputs(False)
                path = output / "evidence-expanded.png"
                app.processEvents()
                assert dialog.grab().save(str(path))
                captures.append(str(path))
                report["captures"] = captures
                dialog.close()
            if args.execute:
                report["model_call_attempted"] = True
                profile = next(
                    p
                    for p in facade.preparation_profiles()
                    if p.profile_id == "desktop-default"
                )
                saved = facade.compile_prompt_blueprint(payload)
                generated = facade.generate_prompt_blueprint(
                    saved["preview_id"],
                    profile.profile_id,
                    profile.revision,
                    teacher_confirmed=True,
                )
                report.update(
                    model_invoked=True,
                    preview_id=saved["preview_id"],
                    latency_ms=generated["latency_ms"],
                    usage=generated["usage"],
                    model_id=generated["model_id"],
                )
                (output / "blueprint.json").write_text(
                    json.dumps(generated, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:
            report.update(status="failed", error_code=getattr(exc, "code", "qa_failed"))
            if "saved" in locals():
                record = state.snapshot()["drafts"].get(saved["preview_id"], {})
                report["preview_id"] = saved["preview_id"]
                report["response_summary"] = record.get("response_summary", {})
                report["model_invoked"] = report["response_summary"].get(
                    "model_invoked"
                )
        finally:
            facade.shutdown()
    (output / "verification.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {"stage": "completed", "output": str(output), **report}, ensure_ascii=False
        ),
        flush=True,
    )
    return 1 if report.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

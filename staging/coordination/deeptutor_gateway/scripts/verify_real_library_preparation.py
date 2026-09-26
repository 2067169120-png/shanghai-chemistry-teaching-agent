"""Exercise real library selection -> modal preview -> lesson form, offline.

Only QA outputs and isolated personal state are written. No provider settings,
model requests, central library writes, or generated lesson files are involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


class NoProviderAccess:
    def __getattr__(self, name):
        raise AssertionError("Provider access is forbidden in this local QA")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.relative_to(ROOT / "runtime/deeptutor_shchem/qa")
    output.mkdir(parents=True, exist_ok=False)

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_library_preparation import (
        library_preparation_reference,
    )
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_preparation import (
        normalize_preparation_payload,
    )
    from integrations.deeptutor_shchem_v1.desktop_preparation_provider import _prompt
    from integrations.deeptutor_shchem_v1.desktop_workbench.library_preparation_dialog import (
        LibraryPreparationDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.main_window import (
        TeacherWorkbenchWindow,
    )

    app = QApplication.instance() or QApplication([])
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
    paths = DesktopPaths.from_workspace(
        ROOT, state_root=output / "isolated-personal-state"
    )
    facade = DesktopWorkbenchFacade(paths, provider_store=NoProviderAccess())
    protected = [
        ROOT / "sh-chem-db/catalog.csv",
        ROOT / "sh-chem-db/kb/textbook_knowledge_map_v1_2026-08-28/concepts.jsonl",
    ]
    digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
    before_files = {str(p.relative_to(ROOT)): digest(p) for p in protected}
    window = TeacherWorkbenchWindow(facade)
    page = window.preparation_page
    page._availability_timer.stop()
    before_form = page._payload()
    before_basket = facade.basket()
    before_paper = window.paper_page._composer.model.draft_payload()
    reports, screenshots, failures = [], [], []
    original_exec = LibraryPreparationDialog.exec

    def run_dialog(dialog):
        def operate():
            try:
                for width in (420, 900):
                    dialog.resize(width, 800)
                    app.processEvents()
                    assert dialog.width() == width
                    assert dialog.preview.horizontalScrollBar().maximum() == 0
                    for position in ("top", "bottom"):
                        bar = dialog.preview.verticalScrollBar()
                        bar.setValue(0 if position == "top" else bar.maximum())
                        app.processEvents()
                        path = output / f"preview-{width}-{position}.png"
                        assert dialog.grab().save(str(path))
                        screenshots.append(str(path))
                # Select only the last unit to exercise the dependency warning.
                for index in range(dialog.units.count() - 1):
                    dialog.units.item(index).setCheckState(Qt.CheckState.Unchecked)
                dialog.include_answers.setChecked(False)
                selected = dialog.reference
                assert selected and selected["question_count"] == 1
                dialog.import_button.click()
            except Exception as exc:  # noqa: BLE001 - close the modal and fail outside Qt
                failures.append(type(exc).__name__ + ": " + str(exc))
                dialog.reject()

        QTimer.singleShot(0, operate)
        return original_exec(dialog)

    try:
        for scope in ("master", "wave1", "supplemental"):
            result = facade.search_themes(scope=scope, limit=1)
            assert result.cards, scope
            detail = facade.library_theme_detail(result.cards[0])
            keys = [part.key for part in detail.parts]
            reference = library_preparation_reference(detail, keys)
            assert reference["question_count"] == len(keys)
            (output / f"reference-{scope}.txt").write_text(
                reference["materials"], encoding="utf-8"
            )
            reports.append(
                {
                    "scope": scope,
                    "theme": detail.title_zh,
                    "paper": detail.paper_title_zh,
                    "units": len(keys),
                    "answer_text_units": sum(
                        bool(p.reference_answer_zh) for p in detail.parts
                    ),
                    "characters": len(reference["materials"]),
                    "source_revision": reference["source_revision"],
                }
            )
            print(f"Verified local {scope}: {len(keys)} units", flush=True)

        # The actual library page loads the selected current card asynchronously.
        result = facade.search_themes(scope="master", query="银镜", limit=1)
        assert result.cards, "Expected real silver-mirror theme is absent"
        window.library_page._apply_results(result)
        deadline = time.monotonic() + 45
        while (
            window.library_page._selected_detail is None and time.monotonic() < deadline
        ):
            app.processEvents()
            time.sleep(0.01)
        assert window.library_page.preparation_button.isEnabled()
        LibraryPreparationDialog.exec = run_dialog
        window.library_page.preparation_button.click()
        if failures:
            raise AssertionError(failures)
        after = page._payload()
        assert window.stack.currentWidget() is page
        for key in before_form:
            if key != "materials":
                assert before_form[key] == after[key], key
        assert after["materials"].startswith(before_form["materials"])
        assert "本次未选入本单元的参考答案" in after["materials"]
        assert "未自动带入未选小问" in after["materials"]
        # The form is intentionally unfilled. Fill only the isolated QA form
        # after checking preservation, then compile the normal provider prompt.
        page.topic.setText("电解质与电离方程式")
        page.audience.setText("高二")
        page.objective.setPlainText("区分电离与导电，记录定义并完成知识表。")
        payload = normalize_preparation_payload(page._payload())
        prompt = _prompt(payload)
        assert json.dumps(after["materials"], ensure_ascii=False)[1:-1] in prompt
        assert facade.basket() == before_basket
        assert window.paper_page._composer.model.draft_payload() == before_paper
        after_files = {str(p.relative_to(ROOT)): digest(p) for p in protected}
        assert before_files == after_files
        report = {
            "scopes": reports,
            "source_files_sha256": after_files,
            "selected_reference_characters": len(after["materials"]),
            "real_qt_library_button_to_modal_to_form": True,
            "compiled_prompt_contains_entire_reference": True,
            "existing_form_preserved_except_materials": True,
            "basket_and_composer_unchanged": True,
            "provider_access_forbidden": True,
            "model_calls": 0,
            "screenshots": screenshots,
            "visual_review": "pending",
        }
        (output / "verification.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False))
    finally:
        LibraryPreparationDialog.exec = original_exec
        window.close()
        window.tasks.shutdown()
        facade.shutdown()


if __name__ == "__main__":
    main()

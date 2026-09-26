"""Synthetic-only Word attribute UI captures, never personal provider state."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from capture_word_questions_demo import build_demo

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def capture(folder, output):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.tasks import (
        DesktopTaskBridge,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_attributes_dialog import (
        WordQuestionAttributesDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.word_question_dialog import (
        WordQuestionDialog,
    )

    state = folder / "isolated-state"
    if state.exists():
        raise RuntimeError("Use a fresh capture state directory")

    class DemoProviders:
        def list_metadata(self):
            return ()

        def __getattr__(self, name):
            raise RuntimeError("Demo must not access provider configuration: " + name)

    app = create_application([])
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "arial.ttf", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)
    app.setFont(QFont("Microsoft YaHei", 10))
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=state),
        provider_store=DemoProviders(),
    )
    source = folder / "演示讲义 电解质与电离.docx"
    receipt = facade.save_visual_import_batch(
        handout_files=(source,), source_type="教师讲义"
    )
    output.mkdir(parents=True, exist_ok=True)
    tasks = DesktopTaskBridge()

    def settle(condition, seconds=20):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                QTest.qWait(100)
                return
            time.sleep(0.015)
        raise RuntimeError("Owned demo UI did not settle")

    browser = WordQuestionDialog(facade, tasks, batch_id=receipt.batch_id)
    browser.resize(1200, 880)
    browser.show()
    settle(lambda: not browser._catalog_busy and browser.question_list.count() == 3)
    browser.question_list.item(0).setCheckState(Qt.CheckState.Checked)
    settle(
        lambda: not browser._selection_save_busy and not browser._save_timer.isActive()
    )
    item = browser._items[browser._current_key]
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    editor = WordQuestionAttributesDialog(item, options)
    editor.resize(720, 850)
    editor.show()
    editor.primary_combo.setCurrentIndex(editor.primary_combo.findData("K01"))
    editor.grade_checks["grade_10"].setChecked(True)
    editor.grade_checks["grade_12"].setChecked(True)
    editor.teacher_note.setPlainText(
        "演示标注：用于高一巩固与高三复习；原考试类型保留待确认。"
    )
    QTest.qWait(200)
    editor.grab().save(str(output / "word-attributes.png"))
    editor.resize(400, 700)
    QTest.qWait(200)
    assert editor.body_scroll.horizontalScrollBar().maximum() == 0
    editor.grab().save(str(folder / "attribute-editor-400.png"))
    editor.body_scroll.ensureWidgetVisible(editor.teacher_note)
    QTest.qWait(100)
    editor.grab().save(str(folder / "attribute-editor-400-lower.png"))
    editor.resize(700, 850)
    editor.body_scroll.verticalScrollBar().setValue(0)
    QTest.qWait(100)
    editor.grab().save(str(folder / "attribute-editor-700.png"))
    editor._preview()
    assert editor.pages.currentIndex() == 1 and editor.updates is None
    QTest.qWait(200)
    editor.grab().save(str(output / "word-attribute-preview.png"))
    editor._confirm()
    saved = facade.word_question_save_attributes(
        item["key"],
        item["revision"],
        editor.updates,
        expected_attribute_revision=editor.expected_attribute_revision,
        expected_stored_revision=editor.expected_stored_revision,
    )
    assert saved["teacher_note"].startswith("演示标注")
    browser._load_catalog()
    settle(lambda: not browser._catalog_busy)
    browser.knowledge_filter.setCurrentIndex(browser.knowledge_filter.findData("K01"))
    browser.grade_filter.setCurrentIndex(browser.grade_filter.findData("grade_12"))
    assert browser.question_list.count() == 1 and len(browser.selections) == 1
    browser.tabs.setCurrentIndex(3)
    QTest.qWait(200)
    browser.grab().save(str(folder / "word-attribute-filter.png"))
    browser.resize(400, 900)
    QTest.qWait(200)
    assert browser.body_scroll.horizontalScrollBar().maximum() == 0
    browser.grab().save(str(folder / "word-attribute-filter-400.png"))
    browser.reject()
    settle(lambda: not browser.isVisible())
    tasks.shutdown()
    report = {
        "synthetic_only": True,
        "provider_called": False,
        "question_count": 3,
        "filtered_count": 1,
        "selected_count": 1,
        "explicit_comparison_before_save": True,
        "teacher_edit_version": saved["edit_version"],
        "public_screenshots": ["word-attributes.png", "word-attribute-preview.png"],
        "source_docx_deliverable": False,
    }
    (folder / "attribute-ui-check.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--build-demo", action="store_true")
    args = parser.parse_args()
    if args.build_demo:
        print(build_demo(args.folder))
    else:
        capture(args.folder, args.output or args.folder)

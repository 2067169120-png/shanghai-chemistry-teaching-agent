"""Capture product widgets using an existing synthetic DOCX and isolated state."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--folder", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.folder.exists():
        raise RuntimeError("Use a fresh isolated capture directory")
    if not args.source.name.startswith("演示讲义 "):
        raise RuntimeError("Only the existing synthetic demonstration is allowed")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtTest import QTest

    from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
    from integrations.deeptutor_shchem_v1.desktop_paths import DesktopPaths
    from integrations.deeptutor_shchem_v1.desktop_workbench.app import (
        create_application,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.import_word_dialog import (
        ImportWordDialog,
    )
    from integrations.deeptutor_shchem_v1.desktop_workbench.lecture_library_dialog import (
        LectureLibraryDialog,
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
    from integrations.deeptutor_shchem_v1.desktop_workbench.workflow_pages import (
        PreparationPage,
    )

    class NoProviders:
        def list_metadata(self):
            return ()

        def __getattr__(self, name):
            raise RuntimeError("Synthetic navigation capture cannot access providers")

    app = create_application([])
    for name in ("msyh.ttc", "msyhbd.ttc", "msyhl.ttc", "arial.ttf", "segoeui.ttf"):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + name)
    app.setFont(QFont("Microsoft YaHei", 10))
    facade = DesktopWorkbenchFacade(
        DesktopPaths.from_workspace(ROOT, state_root=args.folder / "isolated-state"),
        provider_store=NoProviders(),
    )
    receipt = facade.save_visual_import_batch(
        handout_files=(args.source,), source_type="教师讲义"
    )
    tasks = DesktopTaskBridge()
    args.output.mkdir(parents=True, exist_ok=True)

    def settle(condition):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            app.processEvents()
            if condition():
                QTest.qWait(100)
                return
            QTest.qWait(15)
        raise RuntimeError("Owned synthetic UI did not settle")

    library = LectureLibraryDialog(facade, tasks, lesson_topic="电离")
    library.show()
    settle(lambda: library.results.count() == 1)
    library.grab().save(str(args.output / "lesson-source-library.png"))
    library.resize(400, 700)
    QTest.qWait(100)
    library.grab().save(str(args.folder / "library-400.png"))
    assert library.width() == 400
    library.reject()
    original = ImportWordDialog(facade, receipt.batch_id)
    original.show()
    original._view_whole_document()
    original._compile_preview()
    assert original.import_button.isEnabled() and original.include_images.isChecked()
    original._confirm()
    original_reference = original.reference
    assert (
        original_reference["source_selection"] and original_reference["include_images"]
    )
    page = PreparationPage(facade, tasks)
    page._availability_timer.stop()
    page.topic.setText("电解质与电离")
    page.audience.setText("高一 · 40人")
    page.lesson_count.setValue(2)
    page.objective.setPlainText("判断常见物质的类别，写出电离方程式，并解释判断依据。")
    page.materials.setPlainText("教师原有备课要求：每课时保留笔记表与当堂练习。")
    assert page.import_word_reference(original_reference)
    settle(lambda: not page._word_import_in_flight)
    assert original_reference["materials"] in page.materials.toPlainText()
    page.resize(1000, 950)
    page.show()
    QTest.qWait(150)
    page.grab().save(str(args.folder / "preparation-after-import.png"))
    page.close()

    item = facade.word_question_catalog()["items"][0]
    options = facade.word_question_attribute_options(item["key"], item["revision"])
    editor = WordQuestionAttributesDialog(item, options)
    editor.primary_combo.setCurrentIndex(editor.primary_combo.findData("K01"))
    editor.teacher_note.setPlainText("演示资料标签，用于界面检查，不是实际教师审定。")
    editor._preview()
    editor._confirm()
    saved = facade.word_question_save_attributes(
        item["key"],
        item["revision"],
        editor.updates,
        expected_attribute_revision=editor.expected_attribute_revision,
        expected_stored_revision=editor.expected_stored_revision,
    )
    topic = saved["primary_knowledge"]["label"]
    questions = WordQuestionDialog(facade, tasks, lesson_topic=topic)
    questions.resize(1200, 880)
    questions.show()
    settle(lambda: not questions._catalog_busy and questions.question_list.count() == 3)
    questions.grab().save(str(args.output / "lesson-topic-questions.png"))
    questions.resize(400, 850)
    QTest.qWait(150)
    assert questions.body_scroll.horizontalScrollBar().maximum() == 0
    questions.grab().save(str(args.folder / "questions-400.png"))
    questions.reject()
    settle(lambda: not questions.isVisible())
    tasks.shutdown()
    report = {
        "synthetic_only": True,
        "source_count": 1,
        "question_count": 3,
        "original_reference_appended": True,
        "original_words_not_replaced_by_summary": True,
        "source_images_in_fixture": 0,
        "provider_accesses": 0,
        "public_screenshots": [
            "lesson-source-library.png",
            "lesson-topic-questions.png",
        ],
    }
    (args.folder / "ui-check.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print(json.dumps(report))


if __name__ == "__main__":
    main()

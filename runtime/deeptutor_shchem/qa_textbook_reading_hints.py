"""Offscreen screenshots of real textbook reader components; no STATE/facade."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QPushButton

from integrations.deeptutor_shchem_v1.desktop_preparation_sources import CONCEPTS, PreparationSourcesService
from integrations.deeptutor_shchem_v1.desktop_workbench.studio_style import WORKBENCH_STYLE
from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog
from integrations.deeptutor_shchem_v1.desktop_workbench.typography import install_ui_font


class ImmediateReadOnlyTasks:
    def submit(self, _label, operation, *, on_success, on_failure):
        try:
            result = operation()
        except Exception as exc:
            on_failure(str(exc))
        else:
            on_success(result)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--source-data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    install_ui_font(app)
    app.setStyleSheet(WORKBENCH_STYLE)
    original_catalog = PreparationSourcesService(args.workspace)._concepts()
    original_catalog_bytes = (args.workspace / CONCEPTS).read_bytes()
    fixture = args.output / "verified-reader-fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    rows = [dict(original_catalog[key]) for key in
            ("TB-E2-C1-S11-C03", "TB-E2-C1-S13-C02", "TB-E2-C1-S13-C03")]
    book = (args.source_data / rows[0]["source_path"]).resolve(strict=True)
    assert book.is_relative_to(args.source_data.resolve()) and book.suffix.lower() == ".pdf"
    with book.open("rb") as stream:
        book_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    assert all(row["source_sha256"] == book_sha for row in rows)
    copied_book = fixture / "books" / book.name
    copied_book.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(book, copied_book)
    for row in rows:
        row["source_path"] = "books/" + book.name
    catalog = fixture / CONCEPTS
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    for name in ("reading-notes-20260928-atomic-structure.json", "reading-notes-20260928-periodic-table.json"):
        target = fixture / "knowledge/textbook" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.workspace / "knowledge/textbook" / name, target)
    service = PreparationSourcesService(fixture)
    options = {row["concept_id"]: row for row in service.concept_options()}
    facade = SimpleNamespace(preparation_textbook_source=service.textbook_source)
    shots = []

    def settle():
        for _ in range(12):
            app.processEvents()
            time.sleep(0.02)

    def open_reader(concept, width, height):
        dialog = TextbookSourceDialog(facade, options[concept], tasks=ImmediateReadOnlyTasks())
        dialog.resize(width, height)
        dialog.show()
        deadline = time.monotonic() + 15
        while not dialog._loaded_pages and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert dialog._loaded_pages, dialog.status.text()
        settle()
        dialog.summary_tabs.setCurrentIndex(1)
        settle()
        return dialog

    def capture(dialog, name, expected_count, required=(), absent=()):
        settle()
        text = dialog.reading_hints.toPlainText()
        assert dialog._reading_hint_count == expected_count
        assert all(value in text for value in required)
        assert all(value not in text for value in absent)
        controls = {}
        for button in dialog.findChildren(QPushButton):
            if button.isVisible():
                bottom = button.mapTo(dialog, button.rect().bottomRight())
                controls[button.text()] = [bottom.x(), bottom.y()]
                assert bottom.x() < dialog.width() and bottom.y() < dialog.height(), button.text()
        path = args.output / (name + ".png")
        assert dialog.grab().save(str(path))
        shots.append({"name": name, "path": str(path.resolve()),
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "size": [dialog.width(), dialog.height()], "hint_count": expected_count,
                      "current_pdf_page": dialog.view.pageNavigator().currentPage() + 1,
                      "page_range": dialog._source["pdf_pages"], "text": text,
                      "visible_button_bottom_right": controls,
                      "hint_scroll_maximum": dialog.reading_hints.verticalScrollBar().maximum()})

    atomic = open_reader("TB-E2-C1-S11-C03", 1000, 850)
    capture(atomic, "atomic-1000", 1, ("光子能量、端点与波长", "印刷页码：5、6"))
    atomic.resize(420, 720)
    capture(atomic, "atomic-420", 1, ("适用范围与待核对处",))
    atomic.reading_hints.verticalScrollBar().setValue(atomic.reading_hints.verticalScrollBar().maximum())
    capture(atomic, "atomic-420-scrolled", 1, ("课堂核对建议",))
    atomic._jump(10)  # PDF 11 lies outside the selected concept's 9--10 pages.
    capture(atomic, "atomic-page-11-no-hints", 0, ("当前页面暂无",), ("光子能量、端点与波长",))
    atomic.reject()

    periodic = open_reader("TB-E2-C1-S13-C02", 1000, 850)
    capture(periodic, "periodic-1000", 1, ("四分区、五分区与氦的画法", "印刷页码：18、19"))
    periodic.resize(420, 620)
    capture(periodic, "periodic-420-short", 1, ("候选内容，待教师核对",))
    periodic.reading_hints.verticalScrollBar().setValue(3)
    capture(periodic, "periodic-420-short-scrolled", 1, ("适用范围与待核对处",))
    periodic._jump(23)
    capture(periodic, "periodic-page-24-no-hints", 0, ("当前页面暂无",), ("四分区、五分区与氦的画法",))
    periodic.reject()

    partial = open_reader("TB-E2-C1-S13-C03", 760, 740)
    capture(partial, "periodic-range-23-only", 0, ("当前页面暂无",), ("四分区、五分区与氦的画法",))
    partial.reject()
    report = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
              "actual_qt_components": True, "offscreen": True, "candidate_only": True,
              "human_visual_reviewed": False, "provider_calls": 0, "state_access": False,
              "original_concept_count": len(original_catalog),
              "fixture_concept_count": len(options),
              "concept_count_unchanged": len(original_catalog) == 383
              and original_catalog_bytes == (args.workspace / CONCEPTS).read_bytes(),
              "verified_pdf_sha256": book_sha,
              "source_note_files_copied_without_edits": True,
              "screenshots": shots}
    (args.output / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"screenshots": len(shots), "original_concept_count": len(original_catalog), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

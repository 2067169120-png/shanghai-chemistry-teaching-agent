"""Repeatable real-catalog, offscreen section-reader evidence; no STATE/provider."""

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
from pypdf import PdfReader

from integrations.deeptutor_shchem_v1.curriculum_workbench import DIRECTORY_RELATIVE
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import CONCEPTS, PreparationSourcesService
from integrations.deeptutor_shchem_v1.desktop_textbook_reading_hints import load_reading_hints
from integrations.deeptutor_shchem_v1.desktop_textbook_section_reader import TextbookSectionError, textbook_section_scope
from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import PreparationSourcesDialog
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


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--source-data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--include-periodic-law", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.source_data = args.source_data.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    fixture = args.output / ("verified-reader-fixture-21-notes" if args.include_periodic_law
                             else "verified-reader-fixture-15-notes")
    fixture.mkdir(parents=True, exist_ok=True)
    topics = ["atomic-structure", "periodic-table"]
    if args.include_periodic_law:
        topics.append("periodic-law")
    inputs = [args.workspace / CONCEPTS,
              args.workspace / "sh-chem-db" / DIRECTORY_RELATIVE,
              *(args.workspace / "knowledge/textbook" / ("reading-notes-20260928-" + topic + ".json")
                for topic in topics)]
    input_hashes = {str(path): sha(path) for path in inputs}
    for original in inputs:
        target = fixture / original.relative_to(args.workspace)
        assert target.resolve().is_relative_to(fixture.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        assert sha(original) == sha(target)
    catalog = PreparationSourcesService(fixture)._concepts()
    assert len(catalog) == 383
    scopes, blocked, source_files = {}, {}, {}
    for key, row in catalog.items():
        try:
            scopes[key] = textbook_section_scope(fixture, row)
        except TextbookSectionError as exc:
            blocked[key] = {"code": exc.code, "reason": exc.message_zh, "pdf_pages": row["pdf_pages"]}
        if row["source_path"] not in source_files:
            original = (args.source_data / row["source_path"]).resolve(strict=True)
            assert original.is_relative_to(args.source_data) and original.suffix.casefold() == ".pdf"
            digest = sha(original)
            assert digest == row["source_sha256"]
            source_files[row["source_path"]] = {"source_sha256": digest, "bytes": original.stat().st_size,
                                                "pdf_page_count": len(PdfReader(original).pages)}
            # Only the two books used in screenshots are copied into the private
            # fixture. All five originals above are checked read-only.
            if row["volume_id"] in {"TB-M1", "TB-E2"}:
                target = fixture / row["source_path"]
                assert target.resolve().is_relative_to(fixture.resolve())
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(original, target)
                assert sha(target) == digest
        assert row["source_sha256"] == source_files[row["source_path"]]["source_sha256"]
        if key in scopes:
            assert max(scopes[key]["pdf_pages"]) <= source_files[row["source_path"]]["pdf_page_count"]
    assert len(scopes) == 360 and len(blocked) == 23
    hints_by_section = {}
    note_coverage = []
    for original in inputs[2:]:
        for note in json.loads(original.read_text(encoding="utf-8"))["notes"]:
            exact = [row for row in catalog.values()
                     if row.get("volume_id") == note["volume_id"] and row.get("section_key") == note["section_key"]
                     and row["source_sha256"] == note["textbook_evidence"]["source_sha256"]]
            concept_matches = [row["concept_id"] for row in exact if set(note["pdf_pages"]).issubset(row["pdf_pages"])]
            section_matches = [row["concept_id"] for row in exact if row["concept_id"] in scopes
                               and set(note["pdf_pages"]).issubset(scopes[row["concept_id"]]["pdf_pages"])]
            assert section_matches
            scope = scopes[section_matches[0]]
            identity = (note["volume_id"], note["section_key"])
            if identity not in hints_by_section:
                hints_by_section[identity] = load_reading_hints(
                    fixture, volume_id=note["volume_id"], section_key=note["section_key"],
                    source_sha256=note["textbook_evidence"]["source_sha256"], pdf_pages=scope["pdf_pages"])
            assert note["id"] in {n["id"] for n in hints_by_section[identity]["notes"]}
            note_coverage.append({"id": note["id"], "file": original.name, "title": note["title"], "pdf_pages": note["pdf_pages"],
                                  "printed_pages": note["printed_pages"], "section_key": note["section_key"],
                                  "concept_mode_count": len(concept_matches), "concept_mode_ids": concept_matches,
                                  "section_mode_count": len(section_matches), "section_mode_ids": section_matches,
                                  "section_pdf_pages": scope["pdf_pages"]})
    assert len(note_coverage) == (21 if args.include_periodic_law else 15)
    formerly_unreachable = [n["id"] for n in note_coverage if not n["concept_mode_count"]]
    assert {"READ-TB-M1-20260928-PERIODIC-01", "READ-TB-M1-20260928-PERIODIC-03",
            "READ-TB-E2-20260928-PERIODIC-08", "READ-TB-E2-20260928-ATOMIC-05"}.issubset(formerly_unreachable)
    save_json(args.output / "real-concept-coverage.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "catalog_count": len(catalog),
        "readable_section_scope_count": len(scopes), "blocked_count": len(blocked),
        "scope_basis": "verified directory content_pdf_pages; notes never define scope",
        "notes_checked": note_coverage, "formerly_unreachable_now_reachable": formerly_unreachable,
        "matched": {key: {"original_concept_pdf_pages": catalog[key]["pdf_pages"], **scope} for key, scope in scopes.items()},
        "blocked": blocked, "original_pdf_files_verified": source_files, "original_input_hashes": input_hashes,
        "provider_calls": 0, "state_access": False, "candidate_only": True, "human_visual_reviewed": False})

    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    install_ui_font(app)
    app.setStyleSheet(WORKBENCH_STYLE)
    service = PreparationSourcesService(fixture)
    options = {row["concept_id"]: row for row in service.concept_options()}
    facade = SimpleNamespace(preparation_textbook_source=service.textbook_source,
                             preparation_textbook_section_source=service.textbook_section_source,
                             preparation_concept_options=service.concept_options)
    shots = []

    def settle():
        for _ in range(12):
            app.processEvents()
            time.sleep(0.02)

    def screenshot(dialog, name, **evidence):
        settle()
        path = args.output / (name + ".png")
        assert dialog.grab().save(str(path))
        shots.append({"name": name, "path": str(path), "sha256": sha(path),
                      "size": [dialog.width(), dialog.height()], **evidence})

    def reader(key, width=1000, height=850, mode="section", ready=True):
        dialog = TextbookSourceDialog(facade, options[key], tasks=ImmediateReadOnlyTasks(), reading_mode=mode)
        dialog.resize(width, height)
        dialog.show()
        settle()
        deadline = time.monotonic() + 15
        while ready and not dialog._loaded_pages and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.02)
        assert dialog._loaded_pages == ready, dialog.status.text()
        if ready:
            assert dialog._source["concept_id"] == key
            if mode == "section":
                assert not dialog.excerpt_button.isVisible() and not dialog.excerpt_button.isEnabled()
                assert dialog.excerpt is None
            dialog.summary_tabs.setCurrentIndex(1)
        return dialog

    def capture(dialog, name, *, required=(), absent=(), count=None):
        settle()
        text = dialog.reading_hints.toPlainText()
        assert all(value in text for value in required), text
        assert all(value not in text for value in absent), text
        page = dialog.view.pageNavigator().currentPage() + 1
        expected_count = sum(page in note["pdf_pages"] for note in dialog._source["reading_hints"]["notes"])
        assert dialog._reading_hint_count == expected_count, text
        if count is not None and not args.include_periodic_law:
            assert dialog._reading_hint_count == count, text
        buttons = {}
        for button in dialog.findChildren(QPushButton):
            if button.isVisible():
                point = button.mapTo(dialog, button.rect().bottomRight())
                buttons[button.text()] = [point.x(), point.y()]
                assert point.x() < dialog.width() and point.y() < dialog.height(), button.text()
        screenshot(dialog, name, reading_mode=dialog.reading_mode, heading=dialog.heading.text(),
                   pdf_pages=dialog._source["pdf_pages"], current_pdf_page=dialog.view.pageNavigator().currentPage() + 1,
                   printed_position=dialog.position.text(), reading_hint_count=dialog._reading_hint_count,
                   reading_hint_text=text, visible_button_bottom_right=buttons,
                   hint_scroll_maximum=dialog.reading_hints.verticalScrollBar().maximum(),
                   hint_scroll_value=dialog.reading_hints.verticalScrollBar().value(),
                   excerpt_visible=dialog.excerpt_button.isVisible(), excerpt_enabled=dialog.excerpt_button.isEnabled())

    picker = PreparationSourcesDialog(facade)
    picker.resize(1000, 680)
    picker.show()
    picker.concept_search.setText("TB-E2-C1-S13-C03")
    picker.concept_list.setCurrentRow(0)
    settle()
    assert picker.section_read_button.isEnabled() and picker.original_button.isEnabled()
    assert picker.selected_concepts == []
    screenshot(picker, "entry-separate-reading-actions", selected_concepts=picker.selected_concepts)
    picker.resize(420, 680)
    settle()
    picker.body_scroll.ensureWidgetVisible(picker.section_read_button)
    screenshot(picker, "entry-separate-reading-actions-420", selected_concepts=picker.selected_concepts)
    picker.reject()

    concept = reader("TB-E2-C1-S13-C03", mode="concept")
    assert concept._source["pdf_pages"] == [23]
    capture(concept, "concept-scope-stays-page-23", absent=("四分区、五分区与氦的画法",), count=0)
    concept.reject()

    periodic = reader("TB-E2-C1-S13-C03")
    assert periodic._source["pdf_pages"] == list(range(21, 30))
    periodic._jump(22)
    capture(periodic, "section-periodic-full-scope", required=("四分区、五分区与氦的画法",), count=1)
    periodic.resize(420, 620)
    capture(periodic, "section-periodic-420", required=("候选内容，待教师核对",), count=1)
    periodic.reading_hints.verticalScrollBar().setValue(periodic.reading_hints.verticalScrollBar().maximum())
    capture(periodic, "section-periodic-420-scrolled", required=("适用范围与待核对处",), count=1)
    periodic.resize(1000, 850)
    periodic._jump(28)
    capture(periodic, "section-periodic-note08-page29", required=("第一电离能和电负性的趋势都有条件",), count=1)
    before = periodic.view.pageNavigator().currentPage()
    periodic._jump(29)
    assert periodic.view.pageNavigator().currentPage() == before and not periodic.next.isEnabled()
    periodic.reject()

    mandatory = reader("TB-M1-C4-4.1-C01")
    capture(mandatory, "section-periodic-note01-page112", required=("按原子序数定位，并注明族编号约定",), count=1)
    mandatory._jump(112)
    capture(mandatory, "section-page113-no-stale-hints", absent=("按原子序数定位",), count=0)
    mandatory._jump(119)
    capture(mandatory, "section-periodic-note03-page120", required=("分清氢化物热稳定性和溶液酸性",), count=1)
    mandatory.reject()

    atomic = reader("TB-E2-C1-S11-C01")
    atomic._jump(14)
    capture(atomic, "section-atomic-note05-page15", required=("发射、吸收与元素识别",), count=1)
    atomic.reject()

    no_notes = reader("TB-M1-C2-S21-C01")
    capture(no_notes, "section-without-reading-hints", required=("当前页面暂无",), count=0)
    no_notes.reject()

    for index, note in enumerate(n for n in note_coverage if n["file"] == "reading-notes-20260928-periodic-law.json"):
        extra = reader(note["section_mode_ids"][0])
        extra._jump(note["pdf_pages"][0] - 1)
        # Several notes can legitimately support the same page. Show the
        # current evidence target's title instead of repeating the first note.
        cursor = extra.reading_hints.document().find(note["title"])
        assert not cursor.isNull()
        cursor.clearSelection()
        extra.reading_hints.setTextCursor(cursor)
        extra.reading_hints.centerCursor()
        capture(extra, f"section-periodic-law-note{index + 1:02d}", required=(note["title"], "候选内容，待教师核对"))
        extra.reject()

    missing = reader("TB-M1-C1-U01", 760, 700, ready=False)
    assert "尚无明确的册与节" in missing.status.text()
    screenshot(missing, "section-unavailable-explicit-reason", message=missing.status.text())
    missing.reject()
    original = reader("TB-M1-C1-U01", 760, 700, mode="concept")
    capture(original, "concept-readable-after-section-block", count=0)
    original.reject()

    assert input_hashes == {str(path): sha(path) for path in inputs}
    assert len(PreparationSourcesService(args.workspace)._concepts()) == 383
    save_json(args.output / "manifest.json", {
        "created_at_utc": datetime.now(timezone.utc).isoformat(), "actual_qt_components": True,
        "offscreen": True, "candidate_only": True, "human_visual_reviewed": False,
        "provider_calls": 0, "state_access": False, "concept_count_unchanged": True,
        "original_catalog_count": 383, "fixture_catalog_count": len(options),
        "reading_note_count": len(note_coverage), "included_note_topics": topics,
        "input_hashes_unchanged": input_hashes, "source_note_files_copied_without_edits": True,
        "registry_copied_without_edits": True, "private_original_page_images": True,
        "coverage_report": str(args.output / "real-concept-coverage.json"), "screenshots": shots})
    print(json.dumps({"screenshots": len(shots), "section_scope_count": len(scopes), "blocked_count": len(blocked),
                      "notes_reachable": len(note_coverage), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

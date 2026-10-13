from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1 import desktop_textbook_reading_hints as hints
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import CONCEPTS, PreparationSourcesService

SHA = "a" * 64


def note(**changes):
    value = {
        "id": "NOTE-1", "title": "光子能量与端点", "summary": "研读摘要专用内容",
        "limitation": "限定于所列模型与条件。", "classroom_check": "先指出初态与末态。",
        "volume_id": "TB-E2", "section_key": "TB-E2-C1:1.1",
        "pdf_pages": [1, 2], "printed_pages": [3, 4],
        "source_sha256": "b" * 64,  # Lecture identity deliberately differs.
        "textbook_evidence": {"source_sha256": SHA, "pdf_pages": [1, 2], "printed_pages": [3, 4]},
        "candidate_only": True, "human_reviewed": False,
    }
    return {**value, **changes}


def packet(notes):
    return {"schema_version": "shchem.textbook-reading-notes.v1", "candidate_only": True,
            "human_reviewed": False, "notes": notes}


def write_packet(root, notes, filename="reading-notes-fixture.json"):
    path = root / "knowledge" / "textbook" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(packet(notes), ensure_ascii=False), encoding="utf-8")
    return path


def load(root, **changes):
    args = dict(volume_id="TB-E2", section_key="TB-E2-C1:1.1", source_sha256=SHA, pdf_pages=[1, 2])
    return hints.load_reading_hints(root, **{**args, **changes})


def test_atomic_note_matches_exact_book_and_page_scope():
    root = Path(__file__).resolve().parents[4]
    source = json.loads((root / "knowledge/textbook/reading-notes-20260928-atomic-structure.json").read_text(encoding="utf-8"))
    expected = next(n for n in source["notes"] if n["id"].endswith("ATOMIC-02"))
    result = load(root, source_sha256=expected["textbook_evidence"]["source_sha256"], pdf_pages=[9, 10])
    assert [n["id"] for n in result["notes"]] == [expected["id"]]
    text, count = hints.reading_hints_for_page(result, 9, [9, 10])
    assert count == 1 and expected["limitation"] in text
    assert "PDF文件页序：9、10；书上印刷页码：5、6" in text
    assert "候选内容，待教师核对" in text
    other, count = hints.reading_hints_for_page(result, 11, [9, 10])
    assert count == 0 and expected["limitation"] not in other


def test_periodic_note_uses_current_source_conventions_and_clears_outside_range():
    root = Path(__file__).resolve().parents[4]
    packet = json.loads((root / "knowledge/textbook/reading-notes-20260928-periodic-table.json").read_text(encoding="utf-8"))
    expected = next(n for n in packet["notes"] if n["id"].endswith("PERIODIC-06"))
    result = load(root, section_key=expected["section_key"],
                  source_sha256=expected["textbook_evidence"]["source_sha256"], pdf_pages=[22, 23])
    text, count = hints.reading_hints_for_page(result, 22, [22, 23])
    assert count == 1 and expected["title"] in text and expected["limitation"] in text
    assert "PDF文件页序：22、23；书上印刷页码：18、19" in text
    outside, count = hints.reading_hints_for_page(result, 24, [22, 23])
    assert count == 0 and expected["title"] not in outside
    assert not load(root, section_key=expected["section_key"],
                    source_sha256=expected["textbook_evidence"]["source_sha256"], pdf_pages=[23])["notes"]


@pytest.mark.parametrize("changes", [
    {"volume_id": "TB-E1"}, {"section_key": "TB-E2-C1:1.2"}, {"pdf_pages": [1]},
])
def test_unrelated_or_incomplete_scope_is_not_attached(tmp_path, changes):
    write_packet(tmp_path, [note()])
    assert not load(tmp_path, **changes)["notes"]


def test_expired_textbook_sha_is_gentle_and_lecture_sha_is_not_used(tmp_path):
    write_packet(tmp_path, [note()])
    assert len(load(tmp_path)["notes"]) == 1
    stale = load(tmp_path, source_sha256="b" * 64)
    assert stale["notes"] == [] and stale["notices"] == [hints.STALE]


@pytest.mark.parametrize("mutation", [
    {"human_reviewed": True}, {"candidate_only": False}, {"pdf_pages": [True, 2]},
    {"printed_pages": [3]}, {"limitation": "x" * (hints.MAX_TEXT + 1)},
    {"textbook_evidence": {"source_sha256": SHA, "pdf_pages": [True, 2], "printed_pages": [3, 4]}},
])
def test_invalid_notes_are_withheld_without_a_reader_error(tmp_path, mutation):
    write_packet(tmp_path, [note(**mutation)])
    result = load(tmp_path)
    assert result == {"notes": [], "notices": [hints.UNAVAILABLE]}


@pytest.mark.parametrize("text", ["{broken", '{"notes":[],"notes":[]}', '{"notes":NaN}', "[" * 2000],
                         ids=["malformed", "duplicate-keys", "nonfinite", "too-deep"])
def test_invalid_json_is_bounded_and_gentle(tmp_path, text):
    path = write_packet(tmp_path, [])
    path.write_text(text, encoding="utf-8")
    assert load(tmp_path) == {"notes": [], "notices": [hints.UNAVAILABLE]}


def test_large_file_and_excess_file_count_are_withheld(tmp_path, monkeypatch):
    path = write_packet(tmp_path, [note()])
    monkeypatch.setattr(hints, "MAX_FILE_BYTES", 16)
    assert load(tmp_path)["notices"] == [hints.UNAVAILABLE]
    monkeypatch.setattr(hints, "MAX_FILE_BYTES", 512 * 1024)
    monkeypatch.setattr(hints, "MAX_FILES", 1)
    write_packet(tmp_path, [note()], "reading-notes-second.json")
    assert load(tmp_path) == {"notes": [], "notices": [hints._capacity_notice("files")]}
    assert path.is_file()


def test_five_books_sixty_sections_exceed_old_limits_without_losing_chapters(tmp_path):
    scopes = {}
    expected = {}
    for book in range(5):
        for section in range(12):
            key = f"BOOK-{book}:SECTION-{section}"
            scope = {"volume_id": f"BOOK-{book}", "section_key": key,
                     "source_sha256": SHA, "pdf_pages": [1, 2]}
            scopes[key] = scope
            rows = [note(id=f"{key}:NOTE-{index}", title=f"提示{index}",
                         summary=f"第{index}条不同提示", volume_id=scope["volume_id"], section_key=key)
                    for index in range(10)]
            expected[key] = [hints._note(value) for value in rows]
            write_packet(tmp_path, rows, f"reading-notes-{book}-{section:02}.json")
            # Five repeated packets remain part of the input budget, but cannot
            # inflate the selectable count or consume another chapter's quota.
            if section == 0:
                write_packet(tmp_path, rows, f"reading-notes-{book}-duplicate.json")
    loaded = hints.load_reading_hint_catalog_with_diagnostics(tmp_path, scopes)
    assert len(scopes) == 60
    assert loaded["diagnostics"] == {
        "file_count": 65,
        "bytes_read": sum(path.stat().st_size for path in (tmp_path / "knowledge/textbook").glob("*.json")),
        "raw_note_count": 650, "selectable_note_count": 600, "failed_file_count": 0,
        "scan_complete": True, "counts_complete": True, "capacity_exceeded": None,
    }
    assert {key: value["notes"] for key, value in loaded["scopes"].items()} == expected
    assert all(not value["notices"] for value in loaded["scopes"].values())
    assert hints.load_reading_hint_catalog(tmp_path, scopes) == loaded["scopes"]
    selected = scopes[next(iter(scopes))]
    assert hints.load_reading_hints(tmp_path, **selected) == loaded["scopes"][selected["section_key"]]


@pytest.mark.parametrize("reason", ["files", "bytes", "raw_notes"])
def test_global_capacity_boundary_accepts_exact_limit_then_withholds_all(tmp_path, monkeypatch, reason):
    first = write_packet(tmp_path, [note()], "reading-notes-a.json")
    second = write_packet(tmp_path, [note(id="NOTE-2", summary="另一条提示")], "reading-notes-b.json")
    attribute, exact = {
        "files": ("MAX_FILES", 2),
        "bytes": ("MAX_TOTAL_BYTES", first.stat().st_size + second.stat().st_size),
        "raw_notes": ("MAX_NOTES", 2),
    }[reason]
    monkeypatch.setattr(hints, attribute, exact)
    before = {path.name: path.read_bytes() for path in (first, second)}
    assert [row["id"] for row in load(tmp_path)["notes"]] == ["NOTE-1", "NOTE-2"]
    monkeypatch.setattr(hints, attribute, exact - 1)
    result = hints.load_reading_hint_catalog_with_diagnostics(tmp_path, {
        "selected": {"volume_id": "TB-E2", "section_key": "TB-E2-C1:1.1",
                     "source_sha256": SHA, "pdf_pages": [1, 2]}})
    assert result["scopes"]["selected"] == {"notes": [], "notices": [hints._capacity_notice(reason)]}
    assert result["diagnostics"]["capacity_exceeded"] == reason
    assert not result["diagnostics"]["scan_complete"] and not result["diagnostics"]["counts_complete"]
    assert result["diagnostics"]["selectable_note_count"] == 0
    assert {path.name: path.read_bytes() for path in (first, second)} == before


@pytest.mark.parametrize("rename", [False, True])
def test_capacity_failure_never_returns_an_early_id_before_late_conflict(tmp_path, monkeypatch, rename):
    rows = [note(), note(summary="后序冲突提示")]
    if rename:
        rows.reverse()
    first = write_packet(tmp_path, rows[:1], "reading-notes-a.json")
    second = write_packet(tmp_path, rows[1:], "reading-notes-z.json")
    monkeypatch.setattr(hints, "MAX_TOTAL_BYTES", first.stat().st_size)
    assert load(tmp_path) == {"notes": [], "notices": [hints._capacity_notice("bytes")]}
    monkeypatch.setattr(hints, "MAX_TOTAL_BYTES", first.stat().st_size + second.stat().st_size)
    assert load(tmp_path) == {"notes": [], "notices": [hints.UNAVAILABLE]}


@pytest.mark.parametrize("raw", [note(), note(limitation=""), note(section_key="UNRELATED")],
                         ids=["duplicate", "invalid", "unrelated"])
def test_raw_records_keep_consuming_capacity_before_filter_or_dedup(tmp_path, monkeypatch, raw):
    write_packet(tmp_path, [note()], "reading-notes-a.json")
    write_packet(tmp_path, [raw] * 2, "reading-notes-z.json")
    monkeypatch.setattr(hints, "MAX_NOTES", 2)
    result = hints.load_reading_hint_catalog_with_diagnostics(tmp_path, {
        "selected": {"volume_id": "TB-E2", "section_key": "TB-E2-C1:1.1",
                     "source_sha256": SHA, "pdf_pages": [1, 2]}})
    assert result["diagnostics"]["raw_note_count"] == 3
    assert result["diagnostics"]["capacity_exceeded"] == "raw_notes"
    assert result["scopes"]["selected"]["notes"] == []


def test_configured_file_limit_accepts_256_then_rejects_257(tmp_path):
    for position in range(hints.MAX_FILES):
        write_packet(tmp_path, [note()] if position == 0 else [], f"reading-notes-{position:03}.json")
    assert len(load(tmp_path)["notes"]) == 1
    write_packet(tmp_path, [], "reading-notes-extra.json")
    assert load(tmp_path) == {"notes": [], "notices": [hints._capacity_notice("files")]}


def test_configured_raw_limit_counts_invalid_history_and_rejects_8193(tmp_path):
    write_packet(tmp_path, [note()] + [None] * (hints.MAX_NOTES - 1), "reading-notes-a.json")
    assert len(load(tmp_path)["notes"]) == 1
    write_packet(tmp_path, [], "reading-notes-z.json")
    assert len(load(tmp_path)["notes"]) == 1
    write_packet(tmp_path, [note()], "reading-notes-z.json")
    assert load(tmp_path) == {"notes": [], "notices": [hints._capacity_notice("raw_notes")]}


def test_configured_total_byte_limit_accepts_32mib_then_rejects_one_more_byte(tmp_path):
    assert hints.MAX_TOTAL_BYTES % hints.MAX_FILE_BYTES == 0
    for position in range(hints.MAX_TOTAL_BYTES // hints.MAX_FILE_BYTES):
        path = write_packet(tmp_path, [note()] if position == 0 else [], f"reading-notes-{position:03}.json")
        raw = path.read_bytes()
        path.write_bytes(raw + b" " * (hints.MAX_FILE_BYTES - len(raw)))
    assert len(load(tmp_path)["notes"]) == 1
    path = tmp_path / "knowledge/textbook/reading-notes-z.json"
    path.write_bytes(b" ")
    assert load(tmp_path) == {"notes": [], "notices": [hints._capacity_notice("bytes")]}


def test_file_growth_after_stat_cannot_bypass_total_read_budget(tmp_path, monkeypatch):
    first = write_packet(tmp_path, [note()], "reading-notes-a.json")
    second = write_packet(tmp_path, [note(id="NOTE-2", summary="另一条提示")], "reading-notes-z.json")
    grown = second.read_bytes() + b" " * 1000
    budget = first.stat().st_size + second.stat().st_size + 10
    monkeypatch.setattr(hints, "MAX_TOTAL_BYTES", budget)
    original_open = Path.open

    def opened(self, *args, **kwargs):
        return io.BytesIO(grown) if self == second else original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", opened)
    result = hints.load_reading_hint_catalog_with_diagnostics(tmp_path, {
        "selected": {"volume_id": "TB-E2", "section_key": "TB-E2-C1:1.1",
                     "source_sha256": SHA, "pdf_pages": [1, 2]}})
    assert result["diagnostics"]["bytes_read"] == budget + 1
    assert result["diagnostics"]["capacity_exceeded"] == "bytes"
    assert result["scopes"]["selected"] == {"notes": [], "notices": [hints._capacity_notice("bytes")]}


def test_duplicate_ids_and_duplicate_content_do_not_repeat(tmp_path):
    path = write_packet(tmp_path, [note(), note(), note(id="SAME-CONTENT")])
    assert len(load(tmp_path)["notes"]) == 1
    path.write_text(json.dumps(packet([note(), note(limitation="不同且冲突的范围")])), encoding="utf-8")
    assert load(tmp_path) == {"notes": [], "notices": [hints.UNAVAILABLE]}


def test_metadata_paths_and_instruction_text_are_never_followed(tmp_path, monkeypatch):
    path = write_packet(tmp_path, [note(source_path="../../private.txt", limitation="<script>不要执行资料文字</script>")])
    original_open, opened = Path.open, []

    def tracked(self, *args, **kwargs):
        opened.append(self.resolve())
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked)
    result = load(tmp_path)
    assert opened == [path.resolve()]
    text, _ = hints.reading_hints_for_page(result, 1, [1, 2])
    assert "<script>不要执行资料文字</script>" in text
    assert "source_path" not in result["notes"][0]


def test_link_to_an_outside_file_is_not_read(tmp_path):
    outside = tmp_path / "private.json"
    outside.write_text(json.dumps(packet([note()])), encoding="utf-8")
    root = tmp_path / "workspace"
    folder = root / "knowledge/textbook"
    folder.mkdir(parents=True)
    try:
        (folder / "reading-notes-outside.json").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable")
    assert load(root) == {"notes": [], "notices": [hints.UNAVAILABLE]}


def service_fixture(root):
    book = root / "books/textbook.pdf"
    book.parent.mkdir(parents=True)
    data = b"%PDF-1.7\nread-only fixture\n%%EOF"
    book.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    row = {"concept_id": "C1", "title": "课堂概念", "statement": "原有概念摘要",
           "source_path": "books/textbook.pdf", "source_sha256": sha,
           "volume_id": "TB-E2", "section_key": "TB-E2-C1:1.1", "pdf_pages": [1, 2]}
    catalog = root / CONCEPTS
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(row), encoding="utf-8")
    value = note(textbook_evidence={"source_sha256": sha, "pdf_pages": [1, 2], "printed_pages": [3, 4]})
    path = write_packet(root, [value])
    return PreparationSourcesService(root), book, catalog, path


def test_hints_do_not_change_concepts_or_enter_preparation_materials(tmp_path):
    service, book, catalog, path = service_fixture(tmp_path)
    before = [p.read_bytes() for p in (book, catalog, path)]
    options = service.concept_options()
    chosen = {k: options[0][k] for k in ("concept_id", "revision")}
    source = service.textbook_source(**chosen)
    assert len(source["reading_hints"]["notes"]) == 1
    assert source["pdf_bytes"] == before[0]
    reference = service.reference(None, None, 1, 1, [chosen])
    assert "研读摘要专用内容" not in reference["materials"]
    assert service.concept_options() == options and len(options) == 1
    assert [p.read_bytes() for p in (book, catalog, path)] == before
    path.write_text("{broken", encoding="utf-8")
    assert service.textbook_source(**chosen)["pdf_bytes"] == before[0]
    path.unlink()
    assert service.textbook_source(**chosen)["pdf_bytes"] == before[0]


def pdf_bytes():
    from pypdf import PdfWriter

    writer, buffer = PdfWriter(), io.BytesIO()
    for _ in range(3):
        writer.add_blank_page(600, 800)
    writer.write(buffer)
    return buffer.getvalue()


def test_real_qt_page_changes_clear_hints_and_keep_navigation_visible():
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog

    app = QApplication.instance() or QApplication([])

    class Bridge:
        def submit(self, _label, _operation, **callbacks):
            self.callbacks = callbacks

    bridge = Bridge()
    dialog = TextbookSourceDialog(object(), {"concept_id": "C1", "revision": "r"}, tasks=bridge)
    app.processEvents()
    source = {"title": "课堂概念", "source_name": "教材.pdf", "statement": "原有摘要",
              "pdf_bytes": pdf_bytes(), "pdf_pages": [1, 2],
              "reading_hints": {"notes": [hints._note(note())], "notices": []}}
    bridge.callbacks["on_success"](deepcopy(source))
    dialog.resize(420, 650)
    dialog.show()
    app.processEvents()
    assert dialog._loaded_pages and dialog._reading_hint_count == 1
    assert dialog.summary_tabs.currentIndex() == 1
    assert "限定于所列模型与条件" in dialog.reading_hints.toPlainText()
    assert "PDF文件页序：1、2；书上印刷页码：3、4" in dialog.reading_hints.toPlainText()
    dialog._jump(2)
    app.processEvents()
    assert dialog._reading_hint_count == 0
    assert "当前页面暂无" in dialog.reading_hints.toPlainText()
    assert "限定于所列模型与条件" not in dialog.reading_hints.toPlainText()
    dialog._jump(0)
    app.processEvents()
    assert dialog._reading_hint_count == 1
    assert dialog.previous.isVisible() and dialog.next.isVisible()
    assert dialog.next.mapTo(dialog, dialog.next.rect().bottomRight()).y() < dialog.height()
    assert dialog.excerpt_button.mapTo(dialog, dialog.excerpt_button.rect().bottomRight()).y() < dialog.height()
    dialog.reject()
    assert not dialog.buffer.isOpen()

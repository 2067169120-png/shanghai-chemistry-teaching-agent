"""Candidate reading notes become references only after an explicit, current selection."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from integrations.deeptutor_shchem_v1 import desktop_textbook_asset_catalog as catalog
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError, PreparationSourcesService
from test_textbook_bookshelf import (shelf_fixture as shelf_fixture, disk_bytes,
                                    asset_fixture as asset_fixture, section_fixture as section_fixture)
from test_textbook_reading_hints import note, write_packet


def write_notes(f, **changes):
    defaults = {"pdf_pages": [10], "printed_pages": [6],
        "textbook_evidence": {"source_sha256": f.row["source_sha256"], "pdf_pages": [10], "printed_pages": [6]}}
    return write_packet(f.root, [note(**{**defaults, **changes})])


def choose(f):
    row = f.shelf.snapshot()["reading_notes"][0]
    return {key: row[key] for key in ("note_id", "section_key", "revision", "directory_revision")}


def test_snapshot_loads_note_archive_once_without_pdf_and_keeps_review_status(shelf_fixture, monkeypatch):
    f = shelf_fixture
    path = write_notes(f)
    before = disk_bytes(f.root)
    original, reads = Path.open, []
    def opened(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(catalog, "_read_pdf", lambda *_: pytest.fail("listing opened a PDF"))
    result = f.shelf.snapshot()
    assert reads == [path]
    row = result["reading_notes"][0]
    assert row["note_id"] == "NOTE-1" and row["candidate_only"] and not row["human_reviewed"]
    section = next(row for row in result["sections"] if row["section_key"] == f.row["section_key"])
    assert section["reading_note_count"] == 1
    assert sum(row["reading_note_count"] for row in result["volumes"]) == 1
    assert disk_bytes(f.root) == before
    assert str(f.root) not in str(result) and "source_path" not in str(result)


def test_note_only_reference_does_not_require_or_automatically_select_a_concept(shelf_fixture, monkeypatch):
    f = shelf_fixture
    write_notes(f)
    selected = choose(f)
    before = disk_bytes(f.root)
    monkeypatch.setattr(PreparationSourcesService, "_concepts", lambda *_: pytest.fail("note selected concept"))
    value = f.shelf.reading_reference([selected])
    assert "研读摘要专用内容" in value["materials"] and "限定于所列模型与条件" in value["materials"]
    assert "PDF文件页序：[10]；书上印刷页码：[6]" in value["materials"]
    assert '"human_reviewed": false' in value["materials"] and '"generation_allowed": false' in value["materials"]
    assert "研读记录SHA-256：" + selected["revision"] in value["materials"]
    assert "蒸馏候选原文" not in value["materials"] and disk_bytes(f.root) == before


@pytest.mark.parametrize("kind", ["note-change", "source-change", "directory-change", "duplicate", "unknown-field"])
def test_selection_rejects_stale_or_ambiguous_reference(shelf_fixture, kind):
    f = shelf_fixture
    write_notes(f)
    selected = choose(f)
    choices = [selected]
    if kind == "note-change":
        write_notes(f, summary="另一版摘要")
    elif kind == "source-change":
        f.book.write_bytes(f.book.read_bytes() + b"\nchanged")
    elif kind == "directory-change":
        selected["directory_revision"] = "f" * 64
    elif kind == "duplicate":
        choices.append(deepcopy(selected))
    else:
        selected["source_path"] = "../../private.txt"
    with pytest.raises(PreparationSourceError):
        f.shelf.reading_reference(choices)


@pytest.mark.parametrize("change", ["printed-mapping", "conflict", "review-flag"])
def test_unusable_notes_are_withheld_but_original_chapter_remains_readable(shelf_fixture, change):
    f = shelf_fixture
    path = write_notes(f)
    if change == "printed-mapping":
        write_notes(f, printed_pages=[9], textbook_evidence={
            "source_sha256": f.row["source_sha256"], "pdf_pages": [10], "printed_pages": [9]})
    elif change == "review-flag":
        write_notes(f, human_reviewed=True)
    else:
        packet = json.loads(path.read_text(encoding="utf-8"))
        write_packet(f.root, packet["notes"] + [{**packet["notes"][0], "summary": "冲突"}])
    result = f.shelf.snapshot()
    assert result["reading_notes"] == [] and result["notices"]
    section = next(row for row in result["sections"] if row["section_key"] == f.row["section_key"])
    source = f.shelf.section_source(section["section_key"], section["revision"])
    assert source["pdf_bytes"] and source["pdf_pages"] == list(range(7, 16))


@pytest.mark.parametrize("change", ["note", "directory"])
def test_slow_pdf_verification_rechecks_selected_note_and_directory(shelf_fixture, monkeypatch, change):
    f = shelf_fixture
    write_notes(f)
    selected = choose(f)
    original = catalog._read_pdf
    def changed(*args):
        value = original(*args)
        if change == "note":
            write_notes(f, summary="读取原书期间更新")
        else:
            f.registry["nodes"][0]["section_title"] += "更新"
            f.activate()
        return value
    monkeypatch.setattr(catalog, "_read_pdf", changed)
    with pytest.raises(PreparationSourceError):
        f.shelf.reading_reference([selected])


def test_combined_reference_keeps_concept_and_teacher_excerpt(shelf_fixture):
    f = shelf_fixture
    write_notes(f)
    selected = choose(f)
    concept = f.service.concept_options()[0]
    chosen = {key: concept[key] for key in ("concept_id", "revision")}
    excerpt = {**chosen, "source_sha256": f.row["source_sha256"], "pdf_page": 10,
               "text": "教师手工摘录，仍需核对", "confirmed": True}
    result = f.shelf.reading_reference([selected], concepts=[chosen], textbook_excerpts=[excerpt])
    assert "蒸馏候选原文" in result["materials"] and "研读摘要专用内容" in result["materials"]
    assert "教师手工摘录，仍需核对" in result["materials"] and '"human_reviewed": false' in result["materials"]


def test_limits_refuse_large_reference_without_truncation(shelf_fixture):
    f = shelf_fixture
    path = write_notes(f)
    packet = json.loads(path.read_text(encoding="utf-8"))
    template = packet["notes"][0]
    write_packet(f.root, [{**template, "id": f"NOTE-{i}", "summary": str(i) + "甲" * 3999,
                          "limitation": "乙" * 4000, "classroom_check": "丙" * 4000}
                         for i in range(6)])
    rows = f.shelf.snapshot()["reading_notes"]
    choices = [{key: row[key] for key in ("note_id", "section_key", "revision", "directory_revision")} for row in rows]
    with pytest.raises(PreparationSourceError, match="未截断"):
        f.shelf.reading_reference(choices)
    with pytest.raises(PreparationSourceError, match="30条"):
        f.shelf.reading_reference(choices * 6)


def test_legacy_restriction_list_is_preserved_without_archive_rewrite(shelf_fixture):
    f = shelf_fixture
    path = write_notes(f)
    packet = json.loads(path.read_text(encoding="utf-8"))
    packet["notes"][0].pop("limitation")
    packet["notes"][0]["limitations"] = ["第一项适用条件", "第二项待核对处"]
    path.write_text(json.dumps(packet), encoding="utf-8")
    before = path.read_bytes()
    row = f.shelf.snapshot()["reading_notes"][0]
    assert row["limitation"] == "第一项适用条件\n第二项待核对处"
    value = f.shelf.reading_reference([choose(f)])
    assert "第一项适用条件\n第二项待核对处" in value["materials"]
    assert path.read_bytes() == before


@pytest.mark.parametrize("limits", [[], "", ["有效", None], ["x" * 4001], ["x" * 2500, "y" * 2500]])
def test_legacy_restriction_fallback_is_bounded_and_fail_closed(shelf_fixture, limits):
    f = shelf_fixture
    path = write_notes(f)
    packet = json.loads(path.read_text(encoding="utf-8"))
    packet["notes"][0].pop("limitation")
    packet["notes"][0]["limitations"] = limits
    path.write_text(json.dumps(packet), encoding="utf-8")
    assert not f.shelf.snapshot()["reading_notes"]


def test_legacy_plain_text_restriction_keeps_original_words(shelf_fixture):
    f = shelf_fixture
    path = write_notes(f)
    packet = json.loads(path.read_text(encoding="utf-8"))
    packet["notes"][0].pop("limitation")
    packet["notes"][0]["limitations"] = "原笔记已记录的限定条件"
    path.write_text(json.dumps(packet), encoding="utf-8")
    assert "原笔记已记录的限定条件" in f.shelf.reading_reference([choose(f)])["materials"]

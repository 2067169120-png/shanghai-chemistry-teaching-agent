from __future__ import annotations

import hashlib
import io
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import desktop_textbook_bookshelf as bookshelf
from integrations.deeptutor_shchem_v1 import desktop_textbook_asset_catalog as catalog
from integrations.deeptutor_shchem_v1 import desktop_textbook_assets as metadata
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import PreparationSourceError
from test_textbook_section_reader import section_fixture as section_fixture
from test_textbook_asset_catalog import asset_fixture as asset_fixture, candidate, correction_packet, write_corrections


def colored_pdf(page_count=128):
    """Original colored rectangles only; no private textbook fixture bytes."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer, output = PdfWriter(), io.BytesIO()
    for index in range(page_count):
        page = writer.add_blank_page(600, 800)
        stream = DecodedStreamObject()
        red, green, blue = ((index % 3) / 2, ((index + 1) % 3) / 2, ((index + 2) % 3) / 2)
        stream.set_data(f"q {red} {green} {blue} rg 40 40 520 720 re f Q\n".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(output)
    return output.getvalue()


def replace_book(f, data):
    f.book.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    f.row["source_sha256"] = f.asset["source_sha256"] = sha
    for row in f.registry["volumes"] + f.registry["nodes"]:
        if row["volume_id"] == f.row["volume_id"]:
            row["source_sha256"] = sha
    f.catalog.write_text(json.dumps(f.row), encoding="utf-8")
    f.write_assets([f.asset])
    f.activate()


@pytest.fixture
def shelf_fixture(asset_fixture):
    f = asset_fixture
    replace_book(f, colored_pdf())
    f.shelf = bookshelf.TextbookBookshelfService(f.root)
    return f


def selected(f, section_key=None):
    row = next(row for row in f.shelf.snapshot()["sections"]
               if row["section_key"] == (section_key or f.row["section_key"]))
    return {key: row[key] for key in ("section_key", "revision")}


def disk_bytes(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def assert_no_private_paths(value, root):
    text = json.dumps(value, ensure_ascii=False)
    assert str(root) not in text and root.as_posix() not in text
    assert "source_path" not in text and "page_image_path" not in text
    assert "synthetic-evidence" not in text and "never-opened" not in text


def test_snapshot_is_read_only_metadata_and_lists_missing_books(shelf_fixture, monkeypatch):
    f = shelf_fixture
    before = disk_bytes(f.root)
    original_open = Path.open
    allowed = {f.catalog, f.asset_path, f.registry_path}
    opened = []

    def metadata_only(self, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        assert mode in {"r", "rb"} and self in allowed
        opened.append(self)
        return original_open(self, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", metadata_only)
        scoped.setattr(catalog, "_read_pdf", lambda *_: pytest.fail("listing must not read a PDF"))
        result = f.shelf.snapshot()
    assert opened and set(opened) == allowed
    assert len(result["volumes"]) == 5 and len(result["sections"]) == 60
    assert len({row["chapter_id"] for row in result["sections"]}) == 19
    found = [row for row in result["volumes"] if row["local_file_status"] == "found"]
    assert len(found) == 1 and found[0]["volume_id"] == f.row["volume_id"]
    assert found[0]["knowledge_count"] == found[0]["asset_count"] == 1
    assert found[0]["source_name"] == f.book.name
    assert sum(row["local_file_status"] == "missing_or_unavailable" for row in result["volumes"]) == 4
    assert result["unmapped_concept_count"] == 0
    assert result["notices"] and disk_bytes(f.root) == before
    section = next(row for row in result["sections"] if row["section_key"] == f.row["section_key"])
    assert section["pdf_pages"] == list(range(7, 16))
    assert section["printed_pages"] == list(range(3, 12)) and section["section_number"] == "1.1"
    assert section["knowledge_count"] == section["asset_count"] == 1
    option = f.service.concept_options()[0]
    assert {key: result["concepts"][0][key] for key in ("concept_id", "revision", "title", "statement")} == {
        key: option[key] for key in ("concept_id", "revision", "title", "statement")}
    assert result["concepts"][0]["chapter_id"] == f.row["chapter_id"]
    assert_no_private_paths(result, f.root)


def test_scaled_reading_archive_covers_all_five_books_and_sixty_sections_once(shelf_fixture, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_textbook_reading_hints as hints
    from integrations.deeptutor_shchem_v1.desktop_preparation_sources import _digest
    from test_textbook_reading_hints import note, write_packet

    f = shelf_fixture
    expected = {}
    packet_paths = []
    duplicated_books = set()
    for position, node in enumerate(f.registry["nodes"]):
        page, printed = node["content_pdf_pages"][0], node["printed_pages"][0]
        rows = [note(id=f"{node['node_key']}:NOTE-{index}", title=f"提示{index}",
                     summary=f"第{index}条不同提示", volume_id=node["volume_id"], section_key=node["node_key"],
                     pdf_pages=[page], printed_pages=[printed], textbook_evidence={
                         "source_sha256": node["source_sha256"], "pdf_pages": [page], "printed_pages": [printed]})
                for index in range(9)]
        expected[node["node_key"]] = [hints._note(value) for value in rows]
        packet_paths.append(write_packet(f.root, rows, f"reading-notes-{position:03}.json"))
        if node["volume_id"] not in duplicated_books:
            duplicated_books.add(node["volume_id"])
            packet_paths.append(write_packet(f.root, rows, f"reading-notes-duplicate-{node['volume_id']}.json"))
    before = disk_bytes(f.root)
    original_open, opened = Path.open, []

    def tracked(self, *args, **kwargs):
        if self in packet_paths:
            opened.append(self)
        return original_open(self, *args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "open", tracked)
        scoped.setattr(catalog, "_read_pdf", lambda *_: pytest.fail("listing opened a PDF"))
        result = f.shelf.snapshot()
    assert len(result["volumes"]) == 5 and len(result["sections"]) == 60
    assert len(packet_paths) == len(opened) == len(set(opened)) == 65
    assert set(opened) == set(packet_paths)
    assert len(result["reading_notes"]) == 540
    assert {row["section_key"] for row in result["reading_notes"]} == set(expected)
    assert all(row["reading_note_count"] == 9 for row in result["sections"])
    assert sum(row["reading_note_count"] for row in result["volumes"]) == 540
    diagnostics = result["reading_note_diagnostics"]
    assert diagnostics["file_count"] == 65 and diagnostics["raw_note_count"] == 585
    assert diagnostics["selectable_note_count"] == 540
    assert diagnostics["scan_complete"] and diagnostics["counts_complete"]
    assert diagnostics["capacity_exceeded"] is None
    for row in result["reading_notes"]:
        original = next(value for value in expected[row["section_key"]] if value["id"] == row["note_id"])
        assert {key: row[key] for key in original} == original
        assert row["revision"] == _digest(original)
        assert row["candidate_only"] is True and row["human_reviewed"] is False
    assert disk_bytes(f.root) == before
    assert_no_private_paths(result, f.root)


def test_capacity_diagnostics_keep_original_pages_and_metadata_independent(shelf_fixture, monkeypatch):
    from integrations.deeptutor_shchem_v1 import desktop_textbook_reading_hints as hints
    from test_textbook_reading_hints import note, write_packet

    f = shelf_fixture
    selected_section = selected(f)
    original_source = f.shelf.section_source(**selected_section)
    value = note(pdf_pages=[10], printed_pages=[6], textbook_evidence={
        "source_sha256": f.row["source_sha256"], "pdf_pages": [10], "printed_pages": [6]})
    write_packet(f.root, [value, value, {**value, "limitation": ""},
                          {**value, "section_key": "UNRELATED"}], "reading-notes-a.json")
    valid = f.shelf.snapshot()
    assert valid["reading_note_diagnostics"]["raw_note_count"] == 4
    assert valid["reading_note_diagnostics"]["selectable_note_count"] == 1
    before = disk_bytes(f.root)
    monkeypatch.setattr(hints, "MAX_NOTES", 3)
    blocked = f.shelf.snapshot()
    assert blocked["reading_notes"] == []
    assert blocked["reading_note_diagnostics"]["capacity_exceeded"] == "raw_notes"
    assert blocked["reading_note_diagnostics"]["selectable_note_count"] == 0
    assert hints._capacity_notice("raw_notes") in blocked["notices"]
    assert blocked["concepts"] == valid["concepts"] and blocked["assets"] == valid["assets"]
    assert [{key: value for key, value in row.items() if key != "reading_note_count"} for row in blocked["sections"]] == [
        {key: value for key, value in row.items() if key != "reading_note_count"} for row in valid["sections"]]
    source = f.shelf.section_source(**selected_section)
    assert {key: value for key, value in source.items() if key != "reading_hints"} == {
        key: value for key, value in original_source.items() if key != "reading_hints"}
    assert source["reading_hints"] == {"notes": [], "notices": [hints._capacity_notice("raw_notes")]}
    assert disk_bytes(f.root) == before


def test_direct_section_reads_exact_declared_range_even_without_a_concept(shelf_fixture, monkeypatch):
    f = shelf_fixture
    section_key = f.row["chapter_id"] + ":1.2"
    choice = selected(f, section_key)
    before = disk_bytes(f.root)
    monkeypatch.setattr(bookshelf.PreparationSourcesService, "_concepts",
                        lambda *_: pytest.fail("direct section must not depend on a concept"))
    result = f.shelf.section_source(**choice)
    assert result["pdf_pages"] == list(range(16, 25))
    assert result["printed_pages"] == list(range(12, 21))
    assert result["concept_pdf_pages"] == []
    assert result["reading_mode"] == "section" and result["entry_origin"] == "textbook_bookshelf"
    assert result["section_key"] == section_key and result["section_number"] == "1.2"
    assert result["source_sha256"] == hashlib.sha256(result["pdf_bytes"]).hexdigest()
    assert result["pdf_bytes"] == before[f.book.relative_to(f.root)]
    from pypdf import PdfReader
    assert b" rg " in PdfReader(io.BytesIO(result["pdf_bytes"])).pages[15].get_contents().get_data()
    assert result["visual_assets"]["assets"] == []
    assert all(result[key] is False for key in metadata._CLOSED_FLAGS)
    assert "concept_id" not in result and "materials" not in result
    assert_no_private_paths({k: v for k, v in result.items() if k != "pdf_bytes"}, f.root)
    assert disk_bytes(f.root) == before


def test_direct_section_does_not_expand_preparation_concept_material(shelf_fixture):
    f = shelf_fixture
    option = f.service.concept_options()[0]
    choice = {key: option[key] for key in ("concept_id", "revision")}
    result = f.shelf.section_source(**selected(f))
    reference = f.service.reference(None, None, 1, 1, [choice])
    assert "PDF文件页序：[7, 8, 10]" in reference["materials"]
    with pytest.raises(PreparationSourceError):
        f.service.reference(None, None, 1, 1, [result])
    with pytest.raises(PreparationSourceError, match="不在当前知识点来源范围"):
        f.service.reference(None, None, 1, 1, [choice], textbook_excerpts=[{
            **choice, "source_sha256": result["source_sha256"], "pdf_page": 15,
            "text": "整节阅读不扩大知识点摘录范围", "confirmed": True,
        }])


def test_unknown_and_supplementary_concepts_are_not_forced_into_numbered_sections(shelf_fixture):
    f = shelf_fixture
    variants = [
        {"concept_id": "MISLEADING-TB-M1-C99", "section_key": None,
         "supplement_node_key": "declared-introduction"},
        {"concept_id": "unknown-chapter", "chapter_id": None},
        {"concept_id": "wrong-sha", "source_sha256": "f" * 64},
        {"concept_id": "wrong-parent", "chapter_id": "TB-E2-C2"},
        {"concept_id": "outside-section", "pdf_pages": [20]},
        {"concept_id": "untrusted-source", "source_path": "../private.pdf"},
        {"concept_id": "unknown-volume", "volume_id": None},
        {"concept_id": "supplement-with-formal-key", "supplement_node_key": "declared-review"},
    ]
    rows = [f.row, *[{**f.row, **changes} for changes in variants]]
    f.catalog.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    result = f.shelf.snapshot()
    assert len(result["concepts"]) == 9 and result["unmapped_concept_count"] == 8
    by_id = {row["concept_id"]: row for row in result["concepts"]}
    for changes in variants:
        assert by_id[changes["concept_id"]]["section_key"] is None
    assert by_id["MISLEADING-TB-M1-C99"]["volume_id"] == "TB-E2"
    assert by_id["MISLEADING-TB-M1-C99"]["chapter_id"] == f.row["chapter_id"]
    assert by_id["unknown-chapter"]["chapter_id"] is None
    assert by_id["wrong-parent"]["chapter_id"] == "TB-E2-C2"
    for name in ("wrong-sha", "untrusted-source", "unknown-volume"):
        assert by_id[name]["volume_id"] is None and by_id[name]["chapter_id"] is None
    assert by_id["outside-section"]["pdf_pages"] == [20]
    assert_no_private_paths(result, f.root)


def test_caption_overlay_and_closed_gate_assets_are_shared_by_snapshot_and_section(shelf_fixture):
    f = shelf_fixture
    f.asset["printed_page"] = None
    supplement = candidate(f, visual_asset_id="declared-review", section_key=None,
                           supplement_node_key="review-declared-only", printed_page=None)
    f.write_assets([f.asset, supplement])
    packet = correction_packet(f)
    write_corrections(f, packet)
    before = disk_bytes(f.root)
    result = f.shelf.snapshot()
    assert len(result["assets"]) == 2
    corrected = next(row for row in result["assets"] if row["visual_asset_id"] == f.asset["visual_asset_id"])
    assert corrected["label"] == packet["corrections"][0]["label"]
    assert corrected["printed_page"] is None
    section = f.shelf.section_source(**selected(f))
    assert section["visual_assets"]["assets"] == [corrected]
    assert all(corrected[key] is False for key in metadata._CLOSED_FLAGS)
    assert section["visual_assets"]["assets"][0]["candidate_only"] is True
    assert disk_bytes(f.root) == before


def test_invalid_optional_caption_metadata_does_not_hide_verified_section(shelf_fixture):
    f = shelf_fixture
    packet = correction_packet(f)
    packet["corrections"][0]["pdf_page"] += 1
    write_corrections(f, packet)
    result = f.shelf.snapshot()
    assert result["assets"] == [] and any("更正" in text for text in result["notices"])
    source = f.shelf.section_source(**selected(f))
    assert source["pdf_bytes"] and source["visual_assets"]["assets"] == []
    assert any("更正" in text for text in source["visual_assets"]["notices"])


def test_only_current_section_and_version_reading_hints_are_returned(shelf_fixture):
    f = shelf_fixture
    note = {
        "id": "synthetic-note", "title": "原创研读提示", "summary": "原页核对提示",
        "limitation": "候选", "classroom_check": "待教师核对", "volume_id": f.row["volume_id"],
        "section_key": f.row["section_key"], "candidate_only": True, "human_reviewed": False,
        "pdf_pages": [8], "printed_pages": [4],
        "textbook_evidence": {"source_sha256": f.row["source_sha256"], "pdf_pages": [8], "printed_pages": [4]},
    }
    folder = f.root / "knowledge/textbook"
    folder.mkdir(parents=True)
    (folder / "reading-notes-synthetic.json").write_text(json.dumps({
        "schema_version": "shchem.textbook-reading-notes.v1", "candidate_only": True,
        "human_reviewed": False, "notes": [note, {**note, "id": "other", "section_key": "TB-E2-C1:1.2"}],
    }), encoding="utf-8")
    result = f.shelf.section_source(**selected(f))
    assert [row["id"] for row in result["reading_hints"]["notes"]] == [note["id"]]


@pytest.mark.parametrize("change", ["unactivated", "activated", "removed", "source-version"])
def test_stale_directory_or_source_version_is_refused_before_pdf_read(shelf_fixture, monkeypatch, change):
    f = shelf_fixture
    choice = selected(f)
    if change == "unactivated":
        f.registry_path.write_bytes(f.registry_path.read_bytes() + b" ")
    elif change == "removed":
        f.registry_path.unlink()
    elif change == "source-version":
        replace_book(f, colored_pdf(127))
    else:
        f.registry["title"] = "合成新版本目录"
        f.activate()
    monkeypatch.setattr(catalog, "_read_pdf", lambda *_: pytest.fail("stale selection read PDF"))
    with pytest.raises(bookshelf.TextbookBookshelfError) as raised:
        f.shelf.section_source(**choice)
    assert raised.value.message_zh and str(f.root) not in str(raised.value)


@pytest.mark.parametrize("when", ["snapshot", "pdf-read", "hints-read"])
def test_directory_change_during_operation_is_refused(shelf_fixture, monkeypatch, when):
    f = shelf_fixture
    choice = selected(f)

    def change():
        f.registry["title"] = "在读取期间激活的新目录"
        f.activate()

    if when == "snapshot":
        original = catalog.TextbookAssetCatalogService.options

        def changed_assets(self, *args, **kwargs):
            value = original(self, *args, **kwargs)
            change()
            return value

        monkeypatch.setattr(catalog.TextbookAssetCatalogService, "options", changed_assets)
        operation = f.shelf.snapshot
    elif when == "pdf-read":
        original = catalog._read_pdf

        def changed_pdf(*args, **kwargs):
            value = original(*args, **kwargs)
            change()
            return value

        monkeypatch.setattr(catalog, "_read_pdf", changed_pdf)
        operation = lambda: f.shelf.section_source(**choice)
    else:
        def changed_hints(*args, **kwargs):
            change()
            return {"notes": [], "notices": []}

        monkeypatch.setattr(bookshelf, "load_reading_hints", changed_hints)
        operation = lambda: f.shelf.section_source(**choice)
    with pytest.raises(bookshelf.TextbookBookshelfError, match="变化"):
        operation()


@pytest.mark.parametrize("failure", ["missing", "sha", "header", "invalid-pdf", "too-short", "too-large", "encrypted", "page-tree"])
def test_pdf_failure_never_returns_unverified_bytes(shelf_fixture, monkeypatch, failure):
    f = shelf_fixture
    if failure == "header":
        replace_book(f, b"not a PDF with registered SHA")
    elif failure == "invalid-pdf":
        replace_book(f, b"%PDF-1.7\ninvalid page tree\n%%EOF")
    elif failure == "too-short":
        replace_book(f, colored_pdf(14))
    elif failure in {"encrypted", "page-tree"}:
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, NumberObject
        writer, output = PdfWriter(), io.BytesIO()
        for _ in range(16):
            writer.add_blank_page(600, 800)
        if failure == "encrypted":
            writer.encrypt("synthetic-only")
        else:
            writer._root_object["/Pages"][NameObject("/Count")] = NumberObject(15)
        writer.write(output)
        replace_book(f, output.getvalue())
    choice = selected(f)
    if failure == "missing":
        f.book.unlink()
        assert next(row for row in f.shelf.snapshot()["volumes"]
                    if row["volume_id"] == f.row["volume_id"])["local_file_status"] == "missing_or_unavailable"
    elif failure == "sha":
        f.book.write_bytes(colored_pdf(127))
    elif failure == "too-large":
        monkeypatch.setattr(catalog, "MAX_PDF_BYTES", f.book.stat().st_size - 1)
    with pytest.raises(bookshelf.TextbookBookshelfError) as raised:
        f.shelf.section_source(**choice)
    assert raised.value.code == "textbook_bookshelf_unavailable"
    assert "PDF" in raised.value.message_zh and str(f.root) not in raised.value.message_zh


@pytest.mark.parametrize("unsafe", ["../private.pdf", "C:/private.pdf", "books\\private.pdf", "books/../private.pdf"])
def test_unsafe_directory_path_is_never_followed(shelf_fixture, monkeypatch, unsafe):
    f = shelf_fixture
    choice = selected(f)
    for row in f.registry["volumes"] + f.registry["nodes"]:
        if row["volume_id"] == f.row["volume_id"]:
            row["source_path"] = unsafe
    f.activate()
    monkeypatch.setattr(catalog, "_read_pdf", lambda *_: pytest.fail("unsafe location read PDF"))
    with pytest.raises(bookshelf.TextbookBookshelfError, match="位置"):
        f.shelf.snapshot()
    with pytest.raises(bookshelf.TextbookBookshelfError):
        f.shelf.section_source(**choice)


@pytest.mark.parametrize("field,value", [
    ("content_pdf_pages", [7, True]), ("content_pdf_pages", [7, 9999]),
    ("printed_pages", [3, 10]), ("chapter_id", "TB-M1-C1"),
    ("source_sha256", "f" * 64), ("source_root", "other-root"),
])
def test_invalid_directory_parent_or_range_fails_closed(shelf_fixture, field, value):
    f = shelf_fixture
    node = next(row for row in f.registry["nodes"] if row["node_key"] == f.row["section_key"])
    node[field] = value
    f.activate()
    with pytest.raises(bookshelf.TextbookBookshelfError):
        f.shelf.snapshot()


@pytest.mark.parametrize("kind", ["pdf", "concepts", "directory", "workspace"])
def test_symlink_is_not_followed(shelf_fixture, tmp_path, kind):
    f = shelf_fixture
    choice = selected(f)
    if kind == "workspace":
        path, target = tmp_path / "linked-workspace", f.root
    else:
        path = {"pdf": f.book, "concepts": f.catalog, "directory": f.registry_path}[kind]
        target = path.with_suffix(path.suffix + ".original")
        path.rename(target)
    try:
        path.symlink_to(target, target_is_directory=kind == "workspace")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    service = bookshelf.TextbookBookshelfService(path if kind == "workspace" else f.root)
    if kind == "pdf":
        result = service.snapshot()
        assert next(row for row in result["volumes"] if row["volume_id"] == f.row["volume_id"])["local_file_status"] == "missing_or_unavailable"
    else:
        with pytest.raises(bookshelf.TextbookBookshelfError):
            service.snapshot()
    if kind != "concepts":
        with pytest.raises(bookshelf.TextbookBookshelfError):
            service.section_source(**choice)


def test_reparse_point_source_is_unavailable_without_following_it(shelf_fixture, monkeypatch):
    f = shelf_fixture
    choice = selected(f)
    original_lstat, original_open = Path.lstat, Path.open

    def reparse(self, *args, **kwargs):
        info = original_lstat(self, *args, **kwargs)
        if self == f.book.parent:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
        return info

    def no_pdf(self, *args, **kwargs):
        assert self != f.book
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse)
    monkeypatch.setattr(Path, "open", no_pdf)
    snapshot = f.shelf.snapshot()
    assert all(row["local_file_status"] == "missing_or_unavailable" for row in snapshot["volumes"])
    with pytest.raises(bookshelf.TextbookBookshelfError):
        f.shelf.section_source(**choice)


@pytest.mark.parametrize("choice", [
    {"section_key": None, "revision": "a" * 64},
    {"section_key": "TB-E2-C1:1.1", "revision": "stale"},
    {"section_key": "x" * 181, "revision": "a" * 64},
])
def test_invalid_selection_rejected_before_metadata_read(shelf_fixture, monkeypatch, choice):
    f = shelf_fixture
    monkeypatch.setattr(f.shelf, "_directory", lambda: pytest.fail("invalid selection should not load directory"))
    with pytest.raises(bookshelf.TextbookBookshelfError, match="重新选择"):
        f.shelf.section_source(**choice)


@pytest.mark.parametrize("failure", ["missing", "duplicate-id", "nonfinite", "bad-pages", "size", "count"])
def test_concept_metadata_failure_is_safe_and_does_not_change_native_rows(shelf_fixture, monkeypatch, failure):
    f = shelf_fixture
    if failure == "missing":
        f.catalog.unlink()
    elif failure == "duplicate-id":
        f.catalog.write_text(json.dumps(f.row) + "\n" + json.dumps(f.row), encoding="utf-8")
    elif failure == "nonfinite":
        f.catalog.write_text(json.dumps({**f.row, "untrusted": float("inf")}), encoding="utf-8")
    elif failure == "bad-pages":
        f.catalog.write_text(json.dumps({**f.row, "pdf_pages": [True]}), encoding="utf-8")
    elif failure == "size":
        monkeypatch.setattr(bookshelf, "MAX_CONCEPT_BYTES", f.catalog.stat().st_size - 1)
    else:
        monkeypatch.setattr(bookshelf, "MAX_CONCEPTS", 0)
    before = disk_bytes(f.root)
    with pytest.raises(bookshelf.TextbookBookshelfError, match="知识目录") as raised:
        f.shelf.snapshot()
    assert str(f.root) not in raised.value.message_zh and disk_bytes(f.root) == before

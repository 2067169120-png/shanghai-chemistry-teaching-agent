from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from integrations.deeptutor_shchem_v1 import curriculum_workbench as registry_module
from integrations.deeptutor_shchem_v1.desktop_facade import DesktopWorkbenchFacade
from integrations.deeptutor_shchem_v1.desktop_preparation_sources import CONCEPTS, PreparationSourceError, PreparationSourcesService
from integrations.deeptutor_shchem_v1.desktop_textbook_section_reader import TextbookSectionError, textbook_section_scope

def blank_pdf(page_count):
    """Generated blank pages only; no local textbook or private fixture input."""
    from pypdf import PdfWriter

    writer, buffer = PdfWriter(), io.BytesIO()
    for _ in range(page_count):
        writer.add_blank_page(600, 800)
    writer.write(buffer)
    return buffer.getvalue()


def synthetic_directory(source_sha256):
    """Exercise the real 5/19/60 protocol with wholly invented metadata.

    The status/role strings below are protocol fixture values, not evidence of
    any real textbook or visual review. Only the fixture's hash is activated;
    no directory validation function or count/identity rule is bypassed.
    """
    volumes, nodes = [], []
    for volume_index, (volume_id, chapter_count) in enumerate(zip(
            ("TB-M1", "TB-M2", "TB-E1", "TB-E2", "TB-E3"), (4, 4, 4, 4, 3))):
        source = {
            "volume_id": volume_id,
            "volume_title": f"合成教材册 {volume_index + 1}",
            "source_root": "workspace_root",
            "source_path": f"books/synthetic-{volume_id}.pdf",
            "source_sha256": source_sha256,
            "toc_pdf_pages": [1],
            "toc_visual_evidence": [{
                "bytes": 1, "path": f"synthetic-evidence/{volume_id}.png",
                "role": "textbook_toc_visual_evidence", "sha256": "a" * 64,
            }],
        }
        volumes.append({**deepcopy(source), "textbook_family": "合成测试系列", "publisher": "合成测试出版者",
                        "edition_or_printing": None, "edition_status": registry_module.EDITION_STATUS_UNKNOWN,
                        "evidence_level": "L1_LOCAL_TEXTBOOK"})
        section_index = 0
        for chapter in range(1, chapter_count + 1):
            # 19 * 3 sections plus one in each of the first three volumes.
            section_count = 4 if chapter == 1 and volume_index < 3 else 3
            for section in range(1, section_count + 1):
                chapter_id, number = f"{volume_id}-C{chapter}", f"{chapter}.{section}"
                first = 7 + section_index * 9
                nodes.append({
                    **deepcopy(source), "chapter_id": chapter_id, "chapter_title": f"合成第{chapter}章",
                    "node_key": f"{chapter_id}:{number}", "section_number": number,
                    "section_title": f"合成第{number}节",
                    "section_id": f"SYNTHETIC-SECTION-{len(nodes) + 1}" if len(nodes) < 3 else None,
                    "unit_id": None, "unit_title": None, "unit_status": registry_module.UNIT_STATUS_UNKNOWN,
                    "status": "toc_visual_verified_directory_node", "content_pdf_pages": [first, first + 8],
                    "printed_pages": [first - 4, first + 4],
                })
                section_index += 1
    return {
        "schema_version": "1.0.0-textbook-directory-nodes", "title": "合成测试目录",
        "volume_count": 5, "chapter_count": 19, "numbered_section_count": 60,
        "section_id_policy": "only_reuse_existing_verified_ids_otherwise_null",
        "unit_policy": registry_module.UNIT_STATUS_UNKNOWN, "volumes": volumes, "nodes": nodes,
    }


@pytest.fixture
def section_fixture(tmp_path, monkeypatch):
    row = {"concept_id": "SYNTHETIC-CONCEPT-1", "title": "合成概念", "statement": "合成知识摘要",
           "volume_id": "TB-E2", "chapter_id": "TB-E2-C1", "section_key": "TB-E2-C1:1.1",
           "source_path": "books/synthetic-TB-E2.pdf", "pdf_pages": [7, 8, 10],
           "candidate_only": True, "human_reviewed": False}
    book = tmp_path / row["source_path"]
    book.parent.mkdir(parents=True)
    book.write_bytes(blank_pdf(128))
    sha = hashlib.sha256(book.read_bytes()).hexdigest()
    row["source_sha256"] = sha
    registry = synthetic_directory(sha)
    catalog = tmp_path / CONCEPTS
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps(row), encoding="utf-8")
    registry_path = tmp_path / "sh-chem-db" / registry_module.DIRECTORY_RELATIVE
    registry_path.parent.mkdir(parents=True)

    def activate():
        data = json.dumps(registry, ensure_ascii=False).encode("utf-8")
        registry_path.write_bytes(data)
        monkeypatch.setattr(registry_module, "EXPECTED_DIRECTORY_FILE_SHA256", hashlib.sha256(data).hexdigest())

    activate()
    return SimpleNamespace(root=tmp_path, row=row, registry=registry, registry_path=registry_path,
                           book=book, catalog=catalog, activate=activate, service=PreparationSourcesService(tmp_path))


def chosen(fixture):
    option = fixture.service.concept_options()[0]
    return {key: option[key] for key in ("concept_id", "revision")}


def test_section_reads_registry_range_without_changing_concept_or_reference(section_fixture):
    f = section_fixture
    before = [path.read_bytes() for path in (f.book, f.catalog, f.registry_path)]
    selection = chosen(f)
    concept = f.service.textbook_source(**selection)
    section = f.service.textbook_section_source(**selection)
    assert concept["pdf_pages"] == [7, 8, 10]
    assert section["pdf_pages"] == list(range(7, 16))
    assert section["printed_pages"] == list(range(3, 12))
    assert section["concept_pdf_pages"] == [7, 8, 10]
    assert section["reading_mode"] == "section" and section["section_key"] == "TB-E2-C1:1.1"
    assert section["pdf_bytes"] == concept["pdf_bytes"] == before[0]
    assert section["registry_sha256"] == hashlib.sha256(before[2]).hexdigest()
    assert f.service.concept_options()[0]["revision"] == selection["revision"]
    materials = f.service.reference(None, None, 1, 1, [selection])["materials"]
    assert "PDF文件页序：[7, 8, 10]" in materials
    with pytest.raises(PreparationSourceError, match="选择记录不正确"):
        f.service.reference(None, None, 1, 1, [section])
    with pytest.raises(PreparationSourceError, match="不在当前知识点来源范围"):
        f.service.reference(None, None, 1, 1, [selection], textbook_excerpts=[{
            **selection, "source_sha256": section["source_sha256"], "pdf_page": 15,
            "text": "整节阅读不能授权超出知识点的摘录", "confirmed": True,
        }])
    assert [path.read_bytes() for path in (f.book, f.catalog, f.registry_path)] == before


def test_section_rechecks_registry_each_request_and_concept_view_remains_available(section_fixture):
    f = section_fixture
    selection = chosen(f)
    f.service.textbook_section_source(**selection)
    f.registry_path.write_bytes(f.registry_path.read_bytes() + b" ")
    with pytest.raises(TextbookSectionError, match="目录未能核对"):
        f.service.textbook_section_source(**selection)
    assert f.service.textbook_source(**selection)["pdf_pages"] == [7, 8, 10]
    node = next(n for n in f.registry["nodes"] if n["node_key"] == f.row["section_key"])
    node["content_pdf_pages"], node["printed_pages"] = [7, 14], [3, 10]
    f.activate()  # An explicitly activated valid registry is re-read, not cached.
    assert f.service.textbook_section_source(**selection)["pdf_pages"] == list(range(7, 15))


@pytest.mark.parametrize("key,value", [
    ("section_key", "unknown"), ("volume_id", "TB-E1"), ("source_sha256", "0" * 64),
    ("chapter_id", "TB-E2-C9"), ("pdf_pages", [7, 20]),
])
def test_mismatched_concept_cannot_expand_to_a_section(section_fixture, key, value):
    f = section_fixture
    f.row[key] = value
    f.catalog.write_text(json.dumps(f.row), encoding="utf-8")
    with pytest.raises(TextbookSectionError):
        f.service.textbook_section_source(**chosen(f))


def test_stale_concept_and_changed_pdf_fail_before_returning_section(section_fixture):
    f = section_fixture
    selection = chosen(f)
    with pytest.raises(PreparationSourceError, match="已变化"):
        f.service.textbook_section_source(selection["concept_id"], "stale")
    f.book.write_bytes(b"%PDF-1.7\nchanged\n%%EOF")
    with pytest.raises(PreparationSourceError, match="内容已变化"):
        f.service.textbook_section_source(**selection)


@pytest.mark.parametrize("key,value", [
    ("content_pdf_pages", [7, True]), ("printed_pages", [3, 10]),
    ("source_root", "other_root"), ("status", "unverified"),
])
def test_registry_protocol_or_page_mapping_is_not_guessed(section_fixture, key, value):
    f = section_fixture
    node = next(n for n in f.registry["nodes"] if n["node_key"] == f.row["section_key"])
    node[key] = value
    f.activate()
    with pytest.raises(TextbookSectionError):
        f.service.textbook_section_source(**chosen(f))


@pytest.mark.parametrize("unsafe", ["../private.pdf", "C:/private.pdf", "books\\private.pdf"])
def test_unsafe_registry_source_paths_are_blocked(section_fixture, unsafe):
    f = section_fixture
    for item in f.registry["volumes"] + f.registry["nodes"]:
        if item["volume_id"] == f.row["volume_id"]:
            item["source_path"] = unsafe
    f.row["source_path"] = unsafe
    f.catalog.write_text(json.dumps(f.row), encoding="utf-8")
    f.activate()
    with pytest.raises(TextbookSectionError, match="位置"):
        f.service.textbook_section_source(**chosen(f))


def test_section_notes_do_not_define_or_extend_the_registry_pages(section_fixture):
    f = section_fixture
    folder = f.root / "knowledge/textbook"
    folder.mkdir(parents=True)
    (folder / "reading-notes-broken.json").write_text('{"pdf_pages":[1,9999]}', encoding="utf-8")
    assert f.service.textbook_section_source(**chosen(f))["pdf_pages"] == list(range(7, 16))


@pytest.mark.parametrize("raw", [b"{", b'{"nodes":[],"nodes":[]}', b"x" * (2 * 1024 * 1024 + 1)],
                         ids=["invalid-json", "duplicate-key", "oversized"])
def test_unreadable_or_invalid_directory_only_blocks_section(section_fixture, monkeypatch, raw):
    f = section_fixture
    f.registry_path.write_bytes(raw)
    monkeypatch.setattr(registry_module, "EXPECTED_DIRECTORY_FILE_SHA256", hashlib.sha256(raw).hexdigest())
    with pytest.raises(TextbookSectionError):
        f.service.textbook_section_source(**chosen(f))
    assert f.service.textbook_source(**chosen(f))["pdf_pages"] == [7, 8, 10]


def test_facade_section_reader_has_no_state_or_provider_dependency(section_fixture):
    f = section_fixture
    facade = SimpleNamespace(paths=SimpleNamespace(workspace_root=f.root))
    source = DesktopWorkbenchFacade.preparation_textbook_section_source(facade, **chosen(f))
    assert source["pdf_pages"] == list(range(7, 16))


def test_known_and_unknown_synthetic_concepts_preserve_their_scope_boundary(section_fixture):
    f = section_fixture
    known = deepcopy(f.row)
    unknown = {**deepcopy(f.row), "concept_id": "SYNTHETIC-NO-SECTION", "section_key": None}
    f.catalog.write_text("\n".join(json.dumps(row) for row in (known, unknown)), encoding="utf-8")
    before = f.catalog.read_bytes()
    options = {row["concept_id"]: row for row in f.service.concept_options()}
    scope = textbook_section_scope(f.root, known)
    assert scope["pdf_pages"] == list(range(7, 16))
    assert scope["concept_pdf_pages"] == known["pdf_pages"] == [7, 8, 10]
    with pytest.raises(TextbookSectionError, match="尚无明确的册与节"):
        f.service.textbook_section_source(unknown["concept_id"], options[unknown["concept_id"]]["revision"])
    assert f.service.textbook_source(unknown["concept_id"], options[unknown["concept_id"]]["revision"])["pdf_pages"] == [7, 8, 10]
    assert f.catalog.read_bytes() == before and len(f.service.concept_options()) == 2


def ui_source():
    return {"concept_id": "C1", "title": "所选概念", "source_name": "教材.pdf", "statement": "概念摘要",
            "pdf_bytes": blank_pdf(5), "reading_mode": "section", "volume_title": "测试教材册",
            "section_number": "1.1", "section_title": "测试节", "pdf_pages": [2, 3, 4],
            "printed_pages": [10, 11, 12], "concept_pdf_pages": [3], "reading_hints": {"notes": [], "notices": []}}


def test_real_qt_section_navigation_and_excerpt_handlers_are_read_only():
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog

    app = QApplication.instance() or QApplication([])

    class Bridge:
        def submit(self, _label, operation, **callbacks):
            self.operation, self.callbacks = operation, callbacks

    bridge = Bridge()
    dialog = TextbookSourceDialog(object(), {"concept_id": "C1", "revision": "r"},
                                  tasks=bridge, reading_mode="section")
    dialog.resize(420, 620)
    dialog.show()
    app.processEvents()
    bridge.callbacks["on_success"](ui_source())
    app.processEvents()
    assert dialog._loaded_pages and not dialog.excerpt_button.isVisible()
    assert not dialog.excerpt_button.isEnabled()
    assert "2—4" in dialog.heading.text() and "10—12" in dialog.heading.text()
    assert "测试教材册" in dialog.heading.text() and "测试节" in dialog.heading.text()
    assert dialog.pages.itemText(1) == "PDF第3页 · 印刷第11页"
    assert not dialog.previous.isEnabled() and dialog.next.isEnabled()
    dialog._jump(3)
    assert dialog.view.pageNavigator().currentPage() == 3
    assert not dialog.next.isEnabled() and dialog.previous.isEnabled()
    dialog._jump(4)
    assert dialog.view.pageNavigator().currentPage() == 3
    from PySide6.QtCore import QPointF
    dialog.view.pageNavigator().jump(0, QPointF())
    assert dialog.view.pageNavigator().currentPage() == 1
    dialog._edit_excerpt()
    dialog._excerpt_applied({"text": "must not become a selection"})
    assert dialog.excerpt_panel is None and dialog.excerpt is None and not dialog._closed
    assert dialog.next.mapTo(dialog, dialog.next.rect().bottomRight()).y() < dialog.height()
    dialog.reject()


def test_section_out_of_document_pages_fail_without_enabling_reader():
    from PySide6.QtWidgets import QApplication
    from integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog import TextbookSourceDialog

    app = QApplication.instance() or QApplication([])

    class Bridge:
        def submit(self, _label, _operation, **callbacks):
            self.callbacks = callbacks

    bridge = Bridge()
    dialog = TextbookSourceDialog(object(), {"concept_id": "C1", "revision": "r"},
                                  tasks=bridge, reading_mode="section")
    app.processEvents()
    source = ui_source()
    source["pdf_pages"] = [2, 3, 9]
    bridge.callbacks["on_success"](source)
    app.processEvents()
    assert not dialog._loaded_pages and not dialog.next.isEnabled()
    assert "本节目录页序超出教材范围" in dialog.status.text()
    assert dialog.excerpt is None
    dialog.reject()


def test_section_button_never_changes_selection_even_if_dialog_returns_accepted(monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog
    from integrations.deeptutor_shchem_v1.desktop_workbench.preparation_sources_dialog import PreparationSourcesDialog
    import integrations.deeptutor_shchem_v1.desktop_workbench.textbook_source_dialog as module

    app = QApplication.instance() or QApplication([])
    seen = []

    class Facade:
        def preparation_concept_options(self, _query):
            return [{"concept_id": "C1", "revision": "r", "title": "概念", "statement": "摘要"}]

        def preparation_textbook_source(self, *_args):
            raise AssertionError("not called by fixture")

        preparation_textbook_section_source = preparation_textbook_source

    class Preview:
        excerpt = {"text": "not authorized as a section action"}

        def __init__(self, _facade, concept, _parent, **kwargs):
            seen.append((concept, kwargs))

        def exec(self):
            return QDialog.DialogCode.Accepted

        def deleteLater(self):
            pass

    monkeypatch.setattr(module, "TextbookSourceDialog", Preview)
    dialog = PreparationSourcesDialog(Facade())
    app.processEvents()
    dialog.concept_list.setCurrentRow(0)
    assert dialog.section_read_button.isEnabled()
    before = deepcopy(dialog.selected_concepts), deepcopy(dialog._textbook_excerpts)
    dialog.section_read_button.click()
    assert seen[0][1] == {"reading_mode": "section"}
    assert (dialog.selected_concepts, dialog._textbook_excerpts) == before == ([], {})
    dialog.reject()
